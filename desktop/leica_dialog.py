"""Leica series/channel selection with cancellable, lazy background reads."""
from copy import deepcopy
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QProgressBar,
    QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)


def caption(text="", muted=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if muted:
        widget.setObjectName("muted")
    return widget


class LeicaJob(QThread):
    result = Signal(int, str, object)
    failed = Signal(int, str)
    progress = Signal(int, int)

    def __init__(self, token, kind, payload, output_base, parent):
        super().__init__(parent)
        self.token, self.kind = token, kind
        self.payload, self.output_base = payload, output_base

    def run(self):
        try:
            from .leica import inspect_leica, preview_selection, import_selection
            if self.kind == "catalog":
                result = inspect_leica(self.payload)
            elif self.kind == "preview":
                result = preview_selection(self.payload, cancelled=self.isInterruptionRequested)
            else:
                result = import_selection(self.payload, self.output_base,
                    progress=self.progress.emit, cancelled=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.result.emit(self.token, self.kind, result)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(self.token, str(exc))


class LeicaImportDialog(QDialog):
    """Import one chosen acquisition into persistent scalar TIFFs for analysis."""
    _last_directory = ""

    def __init__(self, config, output_base, parent=None):
        super().__init__(parent)
        self.config = deepcopy(config)
        self.output_base = Path(output_base)
        self.result_bundle = None
        self.catalog = None
        self.worker = None
        self._token = 0
        self._pending = None
        self._loading = False
        self._closing = False
        self._importing = False
        self._preview_current = False
        self._pixmaps = {}
        self.setWindowTitle("Import Leica images · LIF / LOF")
        self.resize(1040, 820)
        self.setMinimumSize(880, 720)
        self.setAcceptDrops(True)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(180)
        self.debounce.timeout.connect(self._preview)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        title = caption("Choose what to count.")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(caption("Open or drop a Leica file. Select the acquisition and assign its fluorescence channels.", True))
        files = QHBoxLayout()
        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        self.file_path.setPlaceholderText(".lif or .lof — the original file stays unchanged")
        files.addWidget(self.file_path, 1)
        self.open_button = QPushButton("Open Leica file…")
        self.open_button.clicked.connect(self.choose_file)
        files.addWidget(self.open_button)
        layout.addLayout(files)
        outer = layout
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.controls = QWidget()
        controls = QVBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        series_row = QHBoxLayout()
        series_row.addWidget(caption("Image series"))
        self.series = QComboBox()
        self.series.setMinimumContentsLength(30)
        self.series.currentIndexChanged.connect(self._series_changed)
        series_row.addWidget(self.series, 1)
        controls.addLayout(series_row)
        self.details = caption("", True)
        controls.addWidget(self.details)
        grid = QGridLayout()
        self.channels = {}
        for column, (key, name) in enumerate((("green", "LIVE"), ("red", "DEAD"), ("ebfp", "EBFP (optional)"))):
            grid.addWidget(caption(name), 0, column)
            choice = QComboBox()
            choice.setAccessibleName(name + " Leica channel")
            choice.currentIndexChanged.connect(self._channels_changed)
            self.channels[key] = choice
            grid.addWidget(choice, 1, column)
        controls.addLayout(grid)
        self.channel_confirmation = QCheckBox("I checked the LIVE / DEAD channel assignments above")
        self.channel_confirmation.toggled.connect(self._update_import_button)
        controls.addWidget(self.channel_confirmation)
        controls.addWidget(caption("Leica channel names may be generic. Use your staining/acquisition settings to identify LIVE and DEAD.", True))
        zrow = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("Single Z plane", "slice")
        self.mode.addItem("Maximum intensity projection", "max")
        self.mode.setMinimumContentsLength(30)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        zrow.addWidget(self.mode)
        zrow.addWidget(caption("Time point"))
        self.time = QSpinBox()
        self.time.setRange(1, 1)
        self.time.valueChanged.connect(self._selection_changed)
        zrow.addWidget(self.time)
        zrow.addStretch()
        controls.addLayout(zrow)
        self.slice_controls = QWidget()
        zrow = QHBoxLayout(self.slice_controls)
        zrow.setContentsMargins(0, 0, 0, 0)
        zrow.addWidget(caption("Z plane"))
        self.z_slider = QSlider(Qt.Orientation.Horizontal)
        self.z_slider.setTracking(False)
        self.z_slider.valueChanged.connect(lambda value: self.z_index.setValue(value))
        zrow.addWidget(self.z_slider, 1)
        self.z_index = QSpinBox()
        self.z_index.valueChanged.connect(self._z_changed)
        zrow.addWidget(self.z_index)
        self.z_count = caption("/ 1", True)
        zrow.addWidget(self.z_count)
        controls.addWidget(self.slice_controls)
        self.projection_controls = QWidget()
        zrow = QHBoxLayout(self.projection_controls)
        zrow.setContentsMargins(0, 0, 0, 0)
        zrow.addWidget(caption("Project Z planes"))
        self.z_start, self.z_end = QSpinBox(), QSpinBox()
        for item in (self.z_start, self.z_end):
            item.valueChanged.connect(self._selection_changed)
        zrow.addWidget(self.z_start)
        zrow.addWidget(caption("through"))
        zrow.addWidget(self.z_end)
        zrow.addStretch()
        controls.addWidget(self.projection_controls)
        self.stack_note = caption("", True)
        controls.addWidget(self.stack_note)
        calibration = QHBoxLayout()
        calibration.addWidget(caption("Pixel size (µm)   Y"))
        self.pixel_y, self.pixel_x = QDoubleSpinBox(), QDoubleSpinBox()
        for widget in (self.pixel_y, self.pixel_x):
            widget.setDecimals(6)
            widget.setRange(.000001, 100000)
            widget.setKeyboardTracking(False)
            widget.valueChanged.connect(self._update_import_button)
        calibration.addWidget(self.pixel_y)
        calibration.addWidget(caption("X"))
        calibration.addWidget(self.pixel_x)
        self.calibration_note = caption("", True)
        calibration.addWidget(self.calibration_note, 1)
        controls.addLayout(calibration)
        layout.addWidget(self.controls)
        previews = QHBoxLayout()
        self.previews = {}
        for role, title in (("green", "LIVE channel"), ("red", "DEAD channel"), ("merged", "Merged preview")):
            column = QVBoxLayout()
            column.addWidget(caption(title))
            pane = QLabel("Choose a file")
            pane.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pane.setMinimumSize(180, 155)
            pane.setStyleSheet("background: #102f34; color: #cfdfdb; border-radius: 6px;")
            column.addWidget(pane, 1)
            previews.addLayout(column, 1)
            self.previews[role] = pane
        layout.addLayout(previews, 1)
        layout.addWidget(caption("Preview brightness is scaled for visibility. Imported intensities and bit depth are preserved.", True))
        layout = outer
        self.status = caption("Only selected channels and planes are read; large files are not loaded into memory as a whole.", True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.import_button = QPushButton("Use these images →")
        self.import_button.setObjectName("primary")
        self.import_button.clicked.connect(self._import)
        footer.addWidget(self.import_button)
        layout.addLayout(footer)
        self.controls.setEnabled(False)
        self._mode_changed()

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Leica acquisition", self._last_directory,
                                             "Leica images (*.lif *.lof)")
        if path:
            self.open_file(path)

    def open_file(self, path):
        if self._importing:
            return
        path = str(Path(path).resolve())
        self.__class__._last_directory = str(Path(path).parent)
        self.file_path.setText(path)
        self.file_path.setToolTip(path)
        self.catalog = None
        self._preview_current = False
        self.controls.setEnabled(False)
        self.import_button.setEnabled(False)
        self._clear_previews("Reading image list…")
        self.status.setText("Reading image names, dimensions and channel metadata…")
        self._schedule("catalog", path)

    def _schedule(self, kind, payload):
        self.debounce.stop()
        self._token += 1
        self._pending = (self._token, kind, payload)
        if self.worker is not None:
            self.worker.requestInterruption()
        else:
            self._start_pending()

    def _start_pending(self):
        if not self._pending or self._closing:
            return
        token, kind, payload = self._pending
        self._pending = None
        job = LeicaJob(token, kind, payload, self.output_base, self)
        self.worker = job
        job.result.connect(self._received)
        job.failed.connect(self._failed)
        job.progress.connect(self._progress)
        job.finished.connect(self._finished)
        job.start()

    def _finished(self):
        job, self.worker = self.worker, None
        if job is not None:
            job.deleteLater()
        if self._closing:
            super().reject()
        elif self.result_bundle is not None:
            super().accept()
        else:
            self._start_pending()

    def _received(self, token, kind, result):
        if token != self._token or self._closing:
            return
        if kind == "catalog":
            self.catalog = result
            self._loading = True
            self.series.clear()
            for series in result["series"]:
                self.series.addItem(series["name"], series["index"])
            self._loading = False
            self.controls.setEnabled(True)
            self._series_changed()
        elif kind == "preview":
            self._render(result["channels"])
            self._preview_current = True
            self.status.setText("Preview ready. Check channels and Z selection, then use these images.")
            self._update_import_button()
        else:
            self.result_bundle = result

    def _failed(self, token, message):
        if token != self._token:
            return
        self._importing = False
        self.controls.setEnabled(self.catalog is not None)
        self.open_button.setEnabled(True)
        self.progress.hide()
        self.status.setText(message)
        self._preview_current = False
        self._clear_previews("Preview unavailable")
        self._update_import_button()

    def _current_series(self):
        if self.catalog:
            return next((s for s in self.catalog["series"] if s["index"] == self.series.currentData()), None)
        return None

    def _series_changed(self, *_):
        if self._loading:
            return
        series = self._current_series()
        if series is None:
            self.status.setText("No image series found in this file.")
            return
        self._loading = True
        sizes = series["sizes"]
        self.details.setText(f"{sizes.get('X', '?')} × {sizes.get('Y', '?')} pixels · {series['dtype']} · "
                             f"{len(series['channels'])} channels · {sizes.get('Z', 1)} Z planes · {sizes.get('T', 1)} time points")
        self.details.setToolTip(series["path"])
        for key, choice in self.channels.items():
            choice.clear()
            if key == "ebfp":
                choice.addItem("Not acquired / not used", None)
            for channel in series["channels"]:
                choice.addItem(f"{channel['index'] + 1}: {channel['name']} ({channel['bit_depth']} bit)", channel["index"])
            choice.setCurrentIndex(1 if key == "red" and choice.count() > 1 else 0)
        self.channel_confirmation.setChecked(False)
        z_count = sizes.get("Z", 1)
        for widget in (self.z_index, self.z_start, self.z_end, self.z_slider):
            widget.setRange(1, z_count)
        self.z_index.setValue((z_count + 1) // 2)
        self.z_slider.setValue(self.z_index.value())
        self.z_start.setValue(1)
        self.z_end.setValue(z_count)
        self.z_count.setText(f"/ {z_count}")
        self.mode.setCurrentIndex(0)
        self.mode.setEnabled(z_count > 1)
        self.time.setRange(1, sizes.get("T", 1))
        self.time.setValue(1)
        self.time.setEnabled(sizes.get("T", 1) > 1)
        calibration = series.get("pixel_size_um")
        self.pixel_y.setValue((calibration or self.config["input"]["pixel_size_um"])[0])
        self.pixel_x.setValue((calibration or self.config["input"]["pixel_size_um"])[1])
        self.calibration_note.setText("From Leica metadata; editable." if calibration else "Not in metadata — check the preset value.")
        self._loading = False
        self._mode_changed()

    def _channels_changed(self, *_):
        if not self._loading:
            self.channel_confirmation.setChecked(False)
            self._selection_changed()

    def _z_changed(self, value):
        self.z_slider.blockSignals(True)
        self.z_slider.setValue(value)
        self.z_slider.blockSignals(False)
        self._selection_changed()

    def _mode_changed(self, *_):
        projecting = self.mode.currentData() == "max"
        self.slice_controls.setVisible(not projecting)
        self.projection_controls.setVisible(projecting)
        self.stack_note.setText("Counts are 2D projected objects. Cells above one another can merge; this is not a 3D cell count."
                                if projecting else "Only this focal plane is counted. Other Z planes are not pooled as separate cells.")
        self._selection_changed()

    def _selection_changed(self, *_):
        if self._loading:
            return
        self._preview_current = False
        self._update_import_button()
        self._clear_previews("Updating…")
        if self.worker is not None and not self._importing:
            self.worker.requestInterruption()
        self._token += 1
        self._pending = None
        if self.catalog:
            self.debounce.start()

    def _request(self):
        series = self._current_series()
        if not series or not series["supported"]:
            raise ValueError(series.get("reason", "Select an image series.") if series else "Select an image series.")
        channels = {key: value.currentData() for key, value in self.channels.items()}
        chosen = [value for value in channels.values() if value is not None]
        if channels["green"] is None or channels["red"] is None or len(chosen) != len(set(chosen)):
            raise ValueError("Assign different acquired channels to LIVE, DEAD and optional EBFP.")
        if self.mode.currentData() == "max" and self.z_start.value() > self.z_end.value():
            raise ValueError("The first Z plane must be at or before the last plane.")
        return {"source": self.file_path.text(), "source_identity": self.catalog["source"],
                "series_index": self.series.currentData(), "channels": channels,
                "mode": self.mode.currentData(), "z_index": self.z_index.value() - 1,
                "z_start": self.z_start.value() - 1, "z_stop": self.z_end.value(),
                "time_index": self.time.value() - 1,
                "pixel_size_um": [self.pixel_y.value(), self.pixel_x.value()]}

    def _preview(self):
        try:
            request = self._request()
        except ValueError as exc:
            self.status.setText(str(exc))
            self._clear_previews("Check selection")
            return
        request["preview_max_size"] = 600
        self.status.setText("Reading selected channels and Z planes…")
        self._schedule("preview", request)

    def _update_import_button(self, *_):
        self.import_button.setEnabled(self._preview_current and self.channel_confirmation.isChecked() and not self._importing)

    def _import(self):
        try:
            request = self._request()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        if not self.channel_confirmation.isChecked():
            return
        self._importing = True
        self.controls.setEnabled(False)
        self.open_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText("Preparing the selected images for analysis…")
        self._schedule("import", request)

    def _progress(self, done, total):
        if self._importing:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _clear_previews(self, message):
        self._pixmaps.clear()
        for pane in self.previews.values():
            pane.clear()
            pane.setText(message)

    def _render(self, arrays):
        mapped = {}
        for key, array in arrays.items():
            step = max(1, int(np.ceil(max(array.shape) / 600)))
            values = array[::step, ::step]
            upper = float(np.percentile(values, 99.8)) or float(values.max()) or 1
            mapped[key] = np.asarray(np.clip(values.astype(np.float32) / upper, 0, 1) * 255, dtype=np.uint8)
        zeros = np.zeros_like(mapped["green"])
        frames = {"green": np.stack([zeros, mapped["green"], zeros], axis=-1),
                  "red": np.stack([mapped["red"], zeros, zeros], axis=-1),
                  "merged": np.stack([mapped["red"], mapped["green"], mapped.get("ebfp", zeros)], axis=-1)}
        for key, frame in frames.items():
            image = QImage(frame.data, frame.shape[1], frame.shape[0], frame.strides[0], QImage.Format.Format_RGB888).copy()
            self._pixmaps[key] = QPixmap.fromImage(image)
        self._fit_previews()

    def _fit_previews(self):
        for key, pixmap in self._pixmaps.items():
            pane = self.previews[key]
            pane.setPixmap(pixmap.scaled(pane.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_previews)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and Path(urls[0].toLocalFile()).suffix.lower() in (".lif", ".lof"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self._importing:
            self.open_file(event.mimeData().urls()[0].toLocalFile())
            event.acceptProposedAction()

    def reject(self):
        self.debounce.stop()
        self._closing = True
        self._pending = None
        if self.worker is not None:
            self.worker.requestInterruption()
            self.status.setText("Stopping the current read…")
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
            self.reject()
        else:
            event.accept()
