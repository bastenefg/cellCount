"""Interactive single-field segmentation review with an isolated preview worker."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import shutil
import uuid
import time
from tempfile import TemporaryDirectory

import numpy as np
from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)
from pipeline.io import validate_config
from .services import ROOT
from .segmentation import read_preview
from .segmentation_view import SegmentationCanvas
from .preview_server import publish_json


def note(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


def action(text, slot, primary=False):
    widget = QPushButton(text)
    widget.clicked.connect(slot)
    if primary:
        widget.setObjectName("primary")
    return widget


class CompactNumber(QDoubleSpinBox):
    def textFromValue(self, value):
        text = super().textFromValue(value)
        separator = self.locale().decimalPoint()
        return text.rstrip("0").rstrip(separator) if separator in text else text


class SegmentationDialog(QDialog):
    """Temporary preview edits become analysis settings only on explicit Apply."""

    def __init__(self, rows, config, parent=None, selected=0, from_results=False):
        super().__init__(parent)
        if not rows:
            raise ValueError("Add a field or import samples.csv before previewing segmentation.")
        self.rows = deepcopy(rows)
        self.original_config = deepcopy(config)
        self.config = deepcopy(config)
        self.preview = None
        self.preview_field = None
        self.worker = None
        self._server = None
        self.worker_request = None
        self.worker_output = None
        self._generation = 0
        self._worker_generation = None
        self._last_channel = "red"
        self._loading = False
        self._threshold_dragging = False
        self._threshold_ranges = {}
        self._closing = False
        self._retry = False
        self._temporary = TemporaryDirectory(prefix="cell_segmentation_")
        self._cache = {}
        self._baseline = {}
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(180)
        self.debounce.timeout.connect(self.refresh_preview)
        self.response_timer = QTimer(self)
        self.response_timer.setInterval(30)
        self.response_timer.timeout.connect(self._poll_response)
        self.setWindowTitle("Inspect & tune segmentation · Live/Dead Cell Counter")
        self.resize(1460, 900)
        self.setMinimumSize(1020, 660)
        self._build(from_results)
        self.field_choice.setCurrentIndex(max(0, min(selected, len(rows) - 1)))
        self._load_controls()
        self._invalidate()
        QTimer.singleShot(0, self.refresh_preview)

    def _build(self, from_results):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(note("See what is being counted.", "title"), 1)
        header.addWidget(note("2D FIELD PREVIEW · LIVE / DEAD", "badge"))
        layout.addLayout(header)
        layout.addWidget(note("Tune the image or maximum projection here. Preview counts are 2D; accepted settings apply to your next 2D or 3D run. The 3D run uses the original stack and may resolve different objects. EBFP is evaluated in full 2D runs only.", "muted"))
        top = QHBoxLayout()
        top.addWidget(note("Field"))
        self.field_choice = QComboBox()
        for row in self.rows:
            self.field_choice.addItem(row["image_id"])
        self.field_choice.currentIndexChanged.connect(self._field_changed)
        top.addWidget(self.field_choice, 2)
        top.addWidget(note("Channel"))
        self.channel_choice = QComboBox()
        self.channel_choice.addItem("DEAD · red", "red")
        self.channel_choice.addItem("LIVE · green", "green")
        self.channel_choice.currentIndexChanged.connect(self._channel_changed)
        top.addWidget(self.channel_choice, 1)
        self.auto = QCheckBox("Auto-update")
        self.auto.setChecked(True)
        self.auto.toggled.connect(self._auto_changed)
        top.addWidget(self.auto)
        self.update_button = action("Update preview", self.refresh_preview, True)
        top.addWidget(self.update_button)
        layout.addLayout(top)
        split = QSplitter(Qt.Orientation.Horizontal)
        settings = QWidget()
        settings.setMinimumWidth(275)
        settings.setMaximumWidth(350)
        controls = QVBoxLayout(settings)
        controls.setContentsMargins(0, 0, 10, 0)
        controls.setSpacing(12)
        group = QGroupBox("Detection settings · selected channel")
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.inputs = {}
        self.threshold_sliders = {}
        definitions = [
            ("high", "Peak threshold", .5, .000001, 1e6, "Minimum peak contrast above the estimated background. Raising this rejects weaker detections."),
            ("low", "Region threshold", .5, .000001, 1e6, "Contrast required to grow the mask around a peak. Must be at most the peak threshold."),
            ("min_area_px", "Minimum area (px²)", 1, 1, 100000000, "Reject smaller segmented objects. This refers to the final mask area, not its bounding box."),
            ("sigma_px", "Smoothing (px)", .1, .000001, 10000, "Gaussian smoothing before background subtraction. Higher values suppress fine detail."),
        ]
        for key, title, step, low, high, help_text in definitions:
            widget = self._spin(key == "min_area_px", low, high, step)
            widget.setToolTip(help_text)
            widget.setAccessibleName(title)
            widget.valueChanged.connect(self._settings_changed)
            self.inputs[key] = widget
            form.addRow(title, widget)
            if key in ("high", "low"):
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setAccessibleName(title + " slider")
                slider.setToolTip(help_text + " Release to update the preview. Use the number above for an exact value.")
                slider.setSingleStep(10)
                slider.sliderPressed.connect(self._threshold_drag_started)
                slider.valueChanged.connect(lambda value, name=key: self._threshold_slid(name, value))
                slider.sliderReleased.connect(self._threshold_drag_finished)
                self.threshold_sliders[key] = slider
                form.addRow(slider)
        self.threshold_range = QComboBox()
        self.threshold_range.setAccessibleName("Threshold slider range")
        self.threshold_range.setToolTip("Use a smaller range for finer mouse adjustments. This changes the sliders only, not the thresholds.")
        for maximum in (25, 100, 255, 1024, 4095, 16384, 65535):
            self.threshold_range.addItem(f"0–{maximum:,}", maximum)
        self.threshold_range.currentIndexChanged.connect(self._threshold_range_changed)
        form.addRow("Slider range", self.threshold_range)
        form.addRow(note("Thresholds act on the smoothed, background-subtracted signal, not on raw brightness.", "muted"))
        form.addRow(note("Drag a slider, then release to update. Region cannot exceed peak; exact values can also be typed above.", "muted"))
        controls.addWidget(group)
        shared = QGroupBox("Background, splitting & matching")
        form = QFormLayout(shared)
        self.shared = {}
        definitions = [
            ("background_sigma_px", "Background (px)", False, .000001, 10000, 1),
            ("min_peak_distance_px", "Peak spacing (px)", False, 1, 10000, 1),
            ("peak_window_px", "Peak window (odd)", True, 3, 9999, 2),
            ("max_distance_px", "Match distance (px)", False, .000001, 10000, 1),
        ]
        for key, title, integer, low, high, step in definitions:
            widget = self._spin(integer, low, high, step)
            widget.setAccessibleName(title)
            widget.valueChanged.connect(self._settings_changed)
            self.shared[key] = widget
            form.addRow(title, widget)
        self.exclude_border = QCheckBox("Exclude border objects")
        self.exclude_border.toggled.connect(self._settings_changed)
        form.addRow(self.exclude_border)
        form.addRow(note("These settings affect both channels. Greater peak spacing can reduce splitting of neighboring peaks.", "muted"))
        controls.addWidget(shared)
        controls.addWidget(action("Reset to opening settings", self.reset_settings))
        controls.addWidget(action("Save reviewed preset…", self.save_preset))
        controls.addWidget(note("Judge settings using object boundaries and appropriate controls. Freeze one reviewed preset for the complete comparison.", "muted"))
        controls.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(290)
        scroll.setMaximumWidth(370)
        scroll.setWidget(settings)
        split.addWidget(scroll)
        visual = QWidget()
        visual_layout = QVBoxLayout(visual)
        visual_layout.setContentsMargins(8, 0, 0, 0)
        visual_layout.setSpacing(9)
        display = QHBoxLayout()
        self.overlay_mode = QComboBox()
        for name, value in [("Outlines", "outlines"), ("Filled masks", "filled"), ("Labels only", "labels"), ("No overlay", "none")]:
            self.overlay_mode.addItem(name, value)
        self.overlay_mode.currentIndexChanged.connect(self.render)
        display.addWidget(self.overlay_mode)
        self.show_ids = QCheckBox("Object IDs")
        self.show_ids.toggled.connect(self.render)
        display.addWidget(self.show_ids)
        self.show_contrast = QCheckBox("Working signal")
        self.show_contrast.toggled.connect(self.render)
        display.addWidget(self.show_contrast)
        display.addStretch()
        display.addWidget(action("−", lambda: self.overlay_view.zoom(1 / 1.3)))
        display.addWidget(action("+", lambda: self.overlay_view.zoom(1.3)))
        display.addWidget(action("1:1", lambda: self.overlay_view.reset_full_size()))
        display.addWidget(action("Fit", self.fit_views))
        visual_layout.addLayout(display)
        brightness = QHBoxLayout()
        brightness.addWidget(note("Display black"))
        self.black = self._spin(False, -65535, 65535, 1)
        self.black.setMaximumWidth(95)
        brightness.addWidget(self.black)
        brightness.addWidget(note("White"))
        self.white = self._spin(False, -65535, 65535, 5)
        self.white.setMaximumWidth(95)
        brightness.addWidget(self.white)
        self.white.setValue(255 if self.config["input"]["dtype"] == "uint8" else 65535)
        self.black.valueChanged.connect(self.render)
        self.white.valueChanged.connect(self.render)
        brightness.addWidget(action("Auto display", self.auto_display))
        brightness.addWidget(action("Full range", self.full_display))
        brightness.addStretch()
        visual_layout.addLayout(brightness)
        visual_layout.addWidget(note("Brightness changes the display only. Wheel to zoom, drag to pan; both views stay aligned.", "muted"))
        images = QHBoxLayout()
        self.raw_view = SegmentationCanvas()
        self.overlay_view = SegmentationCanvas()
        self.raw_view.viewChanged.connect(self.overlay_view.set_view)
        self.overlay_view.viewChanged.connect(self.raw_view.set_view)
        self.raw_view.pixelClicked.connect(self.inspect_pixel)
        self.overlay_view.pixelClicked.connect(self.inspect_pixel)
        for title, viewer in [("INPUT TIFF", self.raw_view), ("SEGMENTED OBJECTS", self.overlay_view)]:
            column = QVBoxLayout()
            column.addWidget(note(title, "eyebrow"))
            column.addWidget(viewer, 1)
            images.addLayout(column, 1)
        visual_layout.addLayout(images, 1)
        self.pixel_info = note("Click an object to inspect its area, stored intensity and peak contrast.", "muted")
        self.pixel_info.setMinimumHeight(46)
        visual_layout.addWidget(self.pixel_info)
        self.counts = note("Loading full-resolution segmentation…", "section")
        self.counts.setMinimumHeight(40)
        visual_layout.addWidget(self.counts)
        self.baseline = note("", "muted")
        visual_layout.addWidget(self.baseline)
        split.addWidget(visual)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        footer = QHBoxLayout()
        self.status = note("Preparing preview…", "muted")
        footer.addWidget(self.status, 1)
        footer.addWidget(action("Close", self.reject))
        self.apply_button = action("Use for new run" if from_results else "Use settings for next run", self.accept, True)
        self.apply_button.setEnabled(False)
        footer.addWidget(self.apply_button)
        layout.addLayout(footer)

    @staticmethod
    def _spin(integer, low, high, step):
        widget = QSpinBox() if integer else CompactNumber()
        if not integer:
            widget.setDecimals(6)
        widget.setRange(low, high)
        widget.setSingleStep(step)
        widget.setKeyboardTracking(False)
        widget.setMinimumWidth(85)
        return widget

    def _load_controls(self):
        self._loading = True
        channel = self.channel_choice.currentData()
        self._last_channel = channel
        params = self.config["segmentation"][channel]
        self._control_originals = {}
        for key, widget in self.inputs.items():
            widget.setValue(params[key])
            self._control_originals[key] = (widget.value(), params[key])
        for key, widget in self.shared.items():
            value = self.config["matching"][key] if key == "max_distance_px" else self.config["segmentation"][key]
            widget.setValue(value)
            self._control_originals[key] = (widget.value(), value)
        self.exclude_border.setChecked(self.config["segmentation"]["exclude_border"])
        self._sync_threshold_sliders(preferred=self._threshold_ranges.get(channel, max(25, params["high"] * 2)))
        self._loading = False

    def _sync_threshold_sliders(self, preferred=None):
        """Move slider thumbs without rounding the exact numeric settings."""
        largest = max(self.inputs[key].value() for key in self.threshold_sliders)
        target = max(largest, preferred or self.threshold_range.currentData())
        choices = [self.threshold_range.itemData(i) for i in range(self.threshold_range.count())]
        if largest > choices[-1]:
            # Preserve unusual imported presets and the existing numeric range.
            maximum = min(1000000, 10 ** int(np.ceil(np.log10(largest))))
            self.threshold_range.addItem(f"0–{maximum:,}", maximum)
            choices.append(maximum)
        maximum = next((value for value in choices if value >= target), choices[-1])
        self.threshold_range.blockSignals(True)
        self.threshold_range.setCurrentIndex(choices.index(maximum))
        for index, value in enumerate(choices):
            self.threshold_range.model().item(index).setEnabled(value >= largest)
        self.threshold_range.blockSignals(False)
        self._threshold_ranges[self.channel_choice.currentData()] = maximum
        for key, slider in self.threshold_sliders.items():
            slider.blockSignals(True)
            # Hundredths allow keyboard refinement even on a broad 16-bit range.
            slider.setRange(0, int(maximum * 100))
            slider.setPageStep(max(100, int(maximum * 10)))
            slider.setValue(round(self.inputs[key].value() * 100))
            slider.blockSignals(False)

    def _threshold_range_changed(self, *_):
        if not self._loading:
            self._sync_threshold_sliders()

    def _threshold_drag_started(self):
        self._threshold_dragging = True
        self.debounce.stop()

    def _threshold_slid(self, key, position):
        if self._loading:
            return
        value = max(self.inputs[key].minimum(), position / 100)
        # Moving a slider never creates a high/low combination the pipeline
        # rejects. Exact typed values retain the existing validation behavior.
        other = self.inputs["low" if key == "high" else "high"].value()
        value = max(value, other) if key == "high" else min(value, other)
        if value == self.inputs[key].value():
            self._sync_threshold_sliders()
            return
        self.inputs[key].setValue(value)

    def _threshold_drag_finished(self):
        self._threshold_dragging = False
        if self.auto.isChecked() and not self._is_current():
            self.debounce.start()

    def _collect_controls(self):
        for key, widget in self.inputs.items():
            displayed, original = self._control_originals[key]
            self.config["segmentation"][self._last_channel][key] = original if widget.value() == displayed else widget.value()
        for key, widget in self.shared.items():
            displayed, original = self._control_originals[key]
            value = original if widget.value() == displayed else widget.value()
            target = self.config["matching"] if key == "max_distance_px" else self.config["segmentation"]
            target[key] = value
        self.config["segmentation"]["exclude_border"] = self.exclude_border.isChecked()

    def _settings_changed(self, *_):
        if self._loading:
            return
        self._sync_threshold_sliders()
        self._collect_controls()
        self._invalidate()

    def _field_changed(self, *_):
        if not hasattr(self, "apply_button"):
            return
        self.preview = None
        self.raw_view.clear_image()
        self.overlay_view.clear_image()
        self.baseline.setText("")
        self.counts.setText("Loading selected field…")
        self.pixel_info.setText("Loading selected field; previous measurements are hidden.")
        self._invalidate()

    def _channel_changed(self, *_):
        if not hasattr(self, "apply_button"):
            return
        self._collect_controls()
        self._load_controls()
        self.pixel_info.setText("Click an object to inspect its measurements.")
        self.render()

    def _auto_changed(self, enabled):
        if enabled and not self._is_current() and not self._threshold_dragging:
            self.debounce.start()
        elif not enabled:
            self.debounce.stop()

    def _key(self):
        row = self.rows[self.field_choice.currentIndex()]
        stamps = []
        for channel in ("green", "red"):
            path = Path(row[channel])
            try:
                stat = path.stat()
                stamps.append((str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            except OSError:
                stamps.append((str(path), None))
        return (self.field_choice.currentIndex(), json.dumps([self.config, stamps], sort_keys=True))

    def _is_current(self):
        return self.preview is not None and self.preview_field == self._key()

    def _invalidate(self):
        self._generation += 1
        self.apply_button.setEnabled(False)
        self.status.setText("Settings changed — preview updating…" if self.auto.isChecked() else "Settings changed — click Update preview.")
        if self.preview:
            self.counts.setText("Previous preview — update required for current settings")
            self.pixel_info.setText("Settings changed. The images show the previous preview until the update finishes.")
        if self._threshold_dragging:
            self.status.setText("Release the threshold slider to update the preview." if self.auto.isChecked()
                                else "Settings changed — click Update preview.")
        elif self.auto.isChecked():
            self.debounce.start()

    def refresh_preview(self):
        if self._closing:
            return
        self.debounce.stop()
        if self._threshold_dragging:
            return
        try:
            self._collect_controls()
            validate_config(self.config)
        except Exception as exc:
            self.apply_button.setEnabled(False)
            self.status.setText("Review settings: " + str(exc))
            return
        key = self._key()
        if key in self._cache:
            self._retry = False
            self.last_update_seconds = 0.0
            self._set_preview(self._cache[key], key)
            return
        if self.worker:
            # Finish the current computation, then take only the latest edits.
            # Restarting Python on every change used to dominate update latency.
            self._retry = True
            self.status.setText("Finishing the current preview; your latest settings are queued…")
            return
        self._worker_generation = self._generation
        root = Path(self._temporary.name)
        identity = f"preview_{self._generation}_{uuid.uuid4().hex[:8]}"
        request = root / "request.json"
        self._worker_request_path = request
        output = root / identity
        self.worker_request = {"row": deepcopy(self.rows[key[0]]), "config": deepcopy(self.config)}
        self._request_id = identity
        self._request_started = time.perf_counter()
        self.worker_output = output
        self._worker_key = key
        publish_json(request, {"id": identity, **self.worker_request})
        if self._server is None:
            self._start_server()
        self.worker = self._server
        self.response_timer.start()
        self.status.setText("Updating full-resolution segmentation…")
        self.apply_button.setEnabled(False)

    def _start_server(self):
        worker = QProcess(self)
        self._server = worker
        env = QProcessEnvironment.systemEnvironment()
        env.insert("MPLBACKEND", "Agg")
        env.insert("PYTHONUTF8", "1")
        worker.setProcessEnvironment(env)
        worker.setWorkingDirectory(str(ROOT))
        worker.finished.connect(self._finished)
        worker.errorOccurred.connect(self._process_error)
        prefix = [] if getattr(sys, "frozen", False) else [str(ROOT / "run_app.py")]
        worker.start(sys.executable, [*prefix, "--preview-server", self._temporary.name,
                                      "--parent-pid", str(os.getpid())])

    def _process_error(self, error):
        if self._server and error == QProcess.ProcessError.FailedToStart:
            message = self._server.errorString()
            worker, self._server, self.worker = self._server, None, None
            self._retry = False
            self.response_timer.stop()
            worker.deleteLater()
            self.status.setText("Preview could not start: " + message)
            self._discard_worker_files()

    def _finished(self, code, exit_status):
        if not self._server:
            return
        worker, self._server, self.worker = self._server, None, None
        self._retry = False
        self.response_timer.stop()
        stderr = bytes(worker.readAllStandardError()).decode("utf-8", errors="replace")
        worker.deleteLater()
        if self._closing:
            return
        self.apply_button.setEnabled(False)
        self.status.setText("Preview worker stopped: " + (stderr[-1500:] or "Click Update preview to restart."))
        self._discard_worker_files()

    def _poll_response(self):
        if self._closing or self.worker is None:
            return
        path = Path(self._temporary.name) / "response.json"
        if not path.exists():
            return
        try:
            response = json.loads(path.read_text(encoding="utf-8"))
            if response.get("id") != self._request_id:
                return
        except (OSError, ValueError):
            return
        self.worker = None
        self.response_timer.stop()
        self.last_update_seconds = time.perf_counter() - self._request_started
        if self._retry or self._worker_generation != self._generation:
            explicit_retry = self._retry
            self._retry = False
            self._discard_worker_files()
            if explicit_retry or self.auto.isChecked():
                QTimer.singleShot(0, self.refresh_preview)
            else:
                self.status.setText("Settings changed — click Update preview.")
            return
        try:
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Preview worker did not complete."))
            data = read_preview(self.worker_output)
            # Keep at most three full-resolution previews in memory.
            if len(self._cache) >= 3:
                self._cache.pop(next(iter(self._cache)))
            self._cache[self._worker_key] = data
            self._set_preview(data, self._worker_key)
            self.last_update_seconds = time.perf_counter() - self._request_started
            if self.apply_button.isEnabled():
                self.status.setText(self._ready_status())
        except Exception as exc:
            self.apply_button.setEnabled(False)
            self.status.setText("Preview failed: " + str(exc))
        finally:
            self._discard_worker_files()

    def _discard_worker_files(self):
        # Only this dialog's direct-child temporary artifacts may be deleted.
        # Arrays are loaded in memory; completed preview files are not run records.
        root = Path(self._temporary.name).resolve()
        if self.worker_output is not None:
            output = self.worker_output.resolve()
            if output.parent == root and output.is_dir():
                shutil.rmtree(output, ignore_errors=True)
        request = getattr(self, "_worker_request_path", None)
        if request is not None and request.resolve().parent == root:
            try:
                request.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            (root / "response.json").unlink(missing_ok=True)
        except OSError:
            pass

    def _set_preview(self, preview, key):
        changed_field = self.preview_field is None or self.preview_field[0] != key[0]
        self.preview, self.preview_field = preview, key
        counts = preview["counts"]
        if preview["config"] == self.original_config:
            self._baseline[key[0]] = deepcopy(counts)
        if not self.render():
            return
        if changed_field:
            self.fit_views()
        self.apply_button.setEnabled(self._is_current())
        self.status.setText(self._ready_status())

    def _ready_status(self):
        duration = getattr(self, "last_update_seconds", None)
        timing = f" · updated in {duration:.2f} s" if duration is not None else ""
        return "Preview current · full image" + timing + " · display and zoom do not affect segmentation"

    def render(self, *_):
        if not self.preview:
            return False
        low, high = self.black.value(), self.white.value()
        if high <= low:
            self.raw_view.clear_image()
            self.overlay_view.clear_image()
            self.apply_button.setEnabled(False)
            self.counts.setText("Choose a valid display window to review the current segmentation.")
            self.status.setText("Display white must be greater than black; segmentation is unchanged.")
            return False
        channel = self.channel_choice.currentData()
        image = self.preview["raw_" + channel]
        masks = self.preview["labels_" + channel]
        rows = self.preview["detections_" + channel]
        self.raw_view.set_image(image, masks, rows, low, high, mode="none", channel=channel, show_ids=False)
        working = self.preview["contrast_" + channel] if self.show_contrast.isChecked() else image
        self.overlay_view.set_image(working, masks, rows, low, high,
                                    mode=self.overlay_mode.currentData(), channel=channel,
                                    show_ids=self.show_ids.isChecked())
        if self._is_current():
            count = self.preview["counts"]
            self.counts.setText(f"{len(rows):,} {'DEAD' if channel == 'red' else 'LIVE'} detections in this channel\nGreen only {count['live_only']:,}  ·  Red only {count['dead_only']:,}  ·  Both {count['double_positive']:,}  ·  Total {count['total']:,}")
            old = self._baseline.get(self.field_choice.currentIndex())
            if old:
                self.baseline.setText(f"Opening preset: {old['green_detections']:,} green / {old['red_detections']:,} red detections. Preview applies one shared preset across the future run.")
            if not self.worker:
                self.apply_button.setEnabled(True)
                self.status.setText(self._ready_status())
        return True

    def inspect_pixel(self, x, y, ident):
        if not self.preview:
            return
        channel = self.channel_choice.currentData()
        array = self.preview["raw_" + channel]
        if not (0 <= y < array.shape[0] and 0 <= x < array.shape[1]):
            return
        ident = int(self.preview["labels_" + channel][y, x])
        contrast = self.preview["contrast_" + channel][y, x]
        prefix = "CURRENT" if self._is_current() else "PREVIOUS PREVIEW"
        text = f"{prefix} · pixel ({x}, {y}) · TIFF intensity {array[y,x]:g} · working contrast {contrast:.2f}"
        if ident:
            record = next((r for r in self.preview["detections_" + channel] if int(r["id"]) == ident), None)
            if record:
                text += f"\nObject {ident}: area {record['area']} px² · peak contrast {record['peak_contrast']:.2f} · mean intensity {record['mean_raw']:.2f} · parent peaks {record['parent_count']}"
        else:
            text += "\nNo retained detection at this pixel."
        self.pixel_info.setText(text)

    def fit_views(self):
        self.raw_view.fit()
        self.overlay_view.fit()

    def full_display(self):
        self.black.blockSignals(True)
        self.black.setValue(0)
        self.black.blockSignals(False)
        self.white.setValue(255 if self.config["input"]["dtype"] == "uint8" else 65535)
        self.render()

    def auto_display(self):
        if self.preview:
            array = self.preview["raw_" + self.channel_choice.currentData()]
            low, high = np.percentile(array, [0.1, 99.9])
            if high <= low:
                low, high = float(array.min()), float(array.max())
            self.black.blockSignals(True)
            self.black.setValue(low)
            self.black.blockSignals(False)
            self.white.setValue(max(high, low + 1))
            self.render()

    def reset_settings(self):
        self.config = deepcopy(self.original_config)
        self._threshold_ranges.clear()
        self._load_controls()
        self._invalidate()

    def result_config(self):
        self._collect_controls()
        validate_config(self.config)
        return deepcopy(self.config)

    def save_preset(self):
        if not self._is_current() or not self.apply_button.isEnabled():
            self.status.setText("Update the preview before saving a reviewed preset.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save reviewed segmentation preset", "reviewed_segmentation.json", "JSON settings (*.json)")
        if not path:
            return
        try:
            destination = Path(path).resolve()
            if destination.suffix.lower() != ".json":
                raise ValueError("Choose a .json filename.")
            if destination == (ROOT / "configs/reference_48h.json").resolve() or destination.is_relative_to(ROOT / "reference"):
                raise ValueError("Save a separate preset; keep the bundled reference intact.")
            if any((parent / "run_manifest.json").exists() for parent in destination.parents):
                raise ValueError("Save the preset outside completed run folders.")
            destination.write_text(json.dumps(self.result_config(), indent=2, allow_nan=False) + "\n", encoding="utf-8")
            self.status.setText("Reviewed preset saved: " + destination.name)
        except Exception as exc:
            QMessageBox.warning(self, "Could not save preset", str(exc))

    def accept(self):
        if not self._is_current() or not self.apply_button.isEnabled():
            self.status.setText("Update the preview before applying these settings.")
            return
        super().accept()

    def done(self, result):
        self._closing = True
        self.debounce.stop()
        self.response_timer.stop()
        if self._server:
            worker = self._server
            worker.kill()
            worker.waitForFinished(5000)
            if self._server:
                self._server.deleteLater()
                self._server = None
        self.worker = None
        self._temporary.cleanup()
        super().done(result)
