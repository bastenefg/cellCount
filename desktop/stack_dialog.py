"""Fast optical-section review and separate, explicit 3D candidate analysis.

Original run files are read-only. Slice scrubbing renders bounded NumPy views;
Leica reads and volume segmentation never execute on the GUI thread.
"""
from copy import deepcopy
import csv
import hashlib
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import uuid

import numpy as np
from PySide6.QtCore import QProcess, QProcessEnvironment, QRectF, QStandardPaths, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .services import ROOT

DECISIONS = ("Unreviewed", "Separate in Z", "Same-cell signal supported", "Uncertain")


def caption(text="", muted=False):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    if muted:
        result.setObjectName("muted")
    return result


class SectionView(QWidget):
    """Paint already-rendered pixels with physical aspect and exact click mapping."""
    picked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(130, 115)
        self.pixmap = None
        self.aspect = 1.0
        self.crosshair = None
        self.target = QRectF()

    def set_frame(self, rgb, aspect=None, crosshair=None):
        rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
        self.pixmap = QPixmap.fromImage(QImage(rgb.data, rgb.shape[1], rgb.shape[0],
            rgb.strides[0], QImage.Format.Format_RGB888).copy())
        self.aspect = aspect or rgb.shape[1] / rgb.shape[0]
        self.crosshair = crosshair
        self.update()

    def clear(self):
        self.pixmap = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#10252b"))
        if self.pixmap is None:
            painter.setPen(QColor("#c7d7d9"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No stack loaded")
            return
        width = min(float(self.width()), self.height() * self.aspect)
        height = width / self.aspect
        self.target = QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)
        painter.drawPixmap(self.target, self.pixmap, QRectF(self.pixmap.rect()))
        if self.crosshair:
            painter.setPen(QPen(QColor("#ffffff"), 1, Qt.PenStyle.DashLine))
            x = self.target.left() + self.crosshair[0] * width
            y = self.target.top() + self.crosshair[1] * height
            painter.drawLine(int(x), int(self.target.top()), int(x), int(self.target.bottom()))
            painter.drawLine(int(self.target.left()), int(y), int(self.target.right()), int(y))

    def mousePressEvent(self, event):
        if self.pixmap is not None and self.target.contains(event.position()):
            self.picked.emit((event.position().x() - self.target.left()) / self.target.width(),
                             (event.position().y() - self.target.top()) / self.target.height())


class ProfileView(QWidget):
    """Tiny QPainter plot; changing Z only moves the cursor."""
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(130)
        self.profiles = {}
        self.index = 0
        self.depth = 1
        self.sample_indices = None
        self.axis_text = "Plane order →"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#10252b"))
        area = self.rect().adjusted(12, 14, -12, -22)
        if not self.profiles:
            return
        length = len(next(iter(self.profiles.values())))
        maximum = max(float(np.max(v)) for v in self.profiles.values()) or 1
        for role, values in self.profiles.items():
            painter.setPen(QPen(QColor("#58ee80" if role == "green" else "#ff6868"), 2))
            for index in range(1, length):
                indices = self.sample_indices if self.sample_indices is not None else np.arange(length)
                x0 = area.left() + indices[index - 1] / max(1, self.depth - 1) * area.width()
                x1 = area.left() + indices[index] / max(1, self.depth - 1) * area.width()
                y0 = area.bottom() - float(values[index - 1]) / maximum * area.height()
                y1 = area.bottom() - float(values[index]) / maximum * area.height()
                painter.drawLine(int(x0), int(y0), int(x1), int(y1))
        painter.setPen(QPen(QColor("white"), 1, Qt.PenStyle.DashLine))
        x = area.left() + self.index / max(1, self.depth - 1) * area.width()
        painter.drawLine(int(x), area.top(), int(x), area.bottom())
        painter.setPen(QColor("#d6e6e6"))
        sampled = " · sampled Z" if length < self.depth else ""
        painter.drawText(12, self.height() - 5, f"Raw mean 0–{maximum:.1f}{sampled} · {self.axis_text}")


class StackLoader(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, record, cache, parent=None, volume_run=None):
        super().__init__(parent)
        self.record, self.cache = deepcopy(record), cache
        self.volume_run = volume_run

    def run(self):
        try:
            from .stack_source import prepare_stack
            result = prepare_stack(self.record, self.cache, progress=self.progress.emit,
                                   cancelled=self.isInterruptionRequested)
            result["import_record"] = deepcopy(self.record)
            # New analyses use the ordinary projection preview's exact settings.
            # Inspection never invents a second threshold preset.
            result["_ui_defaults"] = None
            if self.volume_run:
                from .volume_analysis import read_volume_result
                volume_result = read_volume_result(self.volume_run)
                previous = json.loads(Path(volume_result["provenance"]).read_text(encoding="utf-8"))["stack_info"]
                for key in ("cache_key", "shape_zyx", "source", "selection"):
                    if previous.get(key) != result.get(key):
                        raise ValueError("The saved 3D result belongs to a different acquisition or selection.")
                result["_ui_result"] = volume_result
                result["_ui_defaults"] = json.loads(Path(volume_result["settings"]).read_text(encoding="utf-8"))
            if not self.isInterruptionRequested():
                self.ready.emit(result)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))


class ThresholdControl(QWidget):
    changed = Signal()

    def __init__(self, title):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(caption(title))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setAccessibleName(title)
        self.number = QDoubleSpinBox()
        self.number.setDecimals(6)
        self.number.setRange(0.01, 65535)
        self.number.setAccessibleName(title + " exact value")
        self.upper = 255.0
        self.slider.valueChanged.connect(self._slid)
        self.number.valueChanged.connect(self._number_changed)
        row.addWidget(self.slider, 1)
        row.addWidget(self.number)

    def configure(self, upper, value):
        self.upper = max(1.0, float(upper))
        self.number.setMaximum(self.upper)
        self.number.setValue(float(value))

    def _slid(self, value):
        self.number.setValue(value / 1000 * self.upper)

    def _number_changed(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(round(value / self.upper * 1000))
        self.slider.blockSignals(False)
        self.changed.emit()

    def value(self):
        return self.number.value()


class StackReviewDialog(QDialog):
    def __init__(self, run=None, initial_field=None, parent=None, rows=None, config=None, output_base=None, cache_dir=None, volume_run=None):
        super().__init__(parent)
        self.run_path = Path(run).resolve() if run is not None else None
        if self.run_path:
            config = json.loads((self.run_path / "effective_config.json").read_text(encoding="utf-8"))
        self._volume_to_load = Path(volume_run).resolve() if volume_run else None
        previous_settings = None
        if self._volume_to_load:
            previous = json.loads((self._volume_to_load / "provenance.json").read_text(encoding="utf-8"))["stack_info"]
            if not previous.get("import_record"):
                raise ValueError("This 3D output has no Leica import record. Reopen its original imported field instead.")
            previous_settings = json.loads((self._volume_to_load / "settings.json").read_text(encoding="utf-8"))
            config = deepcopy(previous_settings.get("config", {}))
            config["leica_imports"] = [previous["import_record"]]
        self.config = deepcopy(config or {})
        self._legacy_raw = bool(previous_settings is not None and previous_settings.get("mode") != "projection_config")
        self._inspection_only = not self._legacy_raw
        self._shared_settings = (deepcopy(previous_settings) if previous_settings and previous_settings.get("mode") == "projection_config"
                                 else {"mode": "projection_config", "config": deepcopy(self.config)} if self.config.get("segmentation") else None)
        self.records = self.config.get("leica_imports", [])
        self.origin_mode = "saved_2d_run" if self.run_path else "imported_inputs"
        self.output_base = Path(output_base or (self.run_path.parent if self.run_path else Path.home() / "Documents" / "Live-Dead Cell Counter" / "runs"))
        self.cache = Path(cache_dir) if cache_dir else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "stack_review"
        self.stack_info = None
        self.arrays = {}
        self.labels = {}
        self.detections = self._read_detections()
        self.reviews = {}
        self.worker = None
        self.process = None
        self._pending_record = None
        self._closing = False
        self._initializing = False
        self._analysis_cancelled = False
        self._analysis_text = ""
        self.result = None
        self._result_settings = None
        self._review_target = None
        self.x = self.y = 0
        self.setWindowTitle("Inspect Z stack")
        self.resize(1160, 900)
        self._build()
        for record in self.records:
            field = record.get("analysis_field_id") or record.get("result", {}).get("row", {}).get("image_id", "Leica field")
            self.fields.addItem(field, record)
        if initial_field:
            index = self.fields.findText(initial_field)
            if index >= 0:
                self.fields.setCurrentIndex(index)
        self.fields.currentIndexChanged.connect(self._field_changed)
        if self.records:
            QTimer.singleShot(0, self._field_changed)
        else:
            self.status.setText("Import a Leica LIF/LOF acquisition first. A projected TIFF alone cannot recover depth.")

    def _read_detections(self):
        if self.run_path is None or not (self.run_path / "objects.csv").is_file():
            return []
        with (self.run_path / "objects.csv").open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def _build(self):
        outer = QVBoxLayout(self)
        outer.addWidget(caption("Use the original optical sections to resolve projection overlap."))
        outer.addWidget(caption("Green calcein and red EthD-1 can occupy different parts of one cell. Review spatial continuity; depth peaks alone do not establish cell identity or apoptosis.", True))
        row = QHBoxLayout()
        row.addWidget(caption("Field"))
        self.fields = QComboBox()
        row.addWidget(self.fields, 1)
        row.addWidget(caption("XY view"))
        self.zoom = QComboBox()
        for text, size in (("Full field", None), ("256 px crop", 256), ("128 px crop", 128), ("64 px crop", 64)):
            self.zoom.addItem(text, size)
        self.zoom.currentIndexChanged.connect(self._render_slice)
        row.addWidget(self.zoom)
        self.stop = QPushButton("Stop")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self._stop)
        row.addWidget(self.stop)
        self.open_volume = QPushButton("Open 3D result…")
        self.open_volume.clicked.connect(self.open_volume_result)
        row.addWidget(self.open_volume)
        outer.addLayout(row)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        viewer = QWidget()
        view = QVBoxLayout(viewer)
        self.tabs.addTab(viewer, "Optical sections && review")
        detection_row = QHBoxLayout()
        self.object_choice = QComboBox()
        self.object_choice.currentIndexChanged.connect(self._object_changed)
        detection_row.addWidget(self.object_choice, 1)
        self.only_double = QCheckBox("Only apparent double-positive / unresolved")
        self.only_double.toggled.connect(self._populate_objects)
        detection_row.addWidget(self.only_double)
        view.addLayout(detection_row)
        self.context = caption("", True)
        view.addWidget(self.context)
        self.shared_settings_caption = caption("", True)
        view.addWidget(self.shared_settings_caption)
        grid = QGridLayout()
        self.views = {}
        for column, (key, title) in enumerate((("green", "LIVE · XY"), ("red", "DEAD · XY"), ("merged", "Merged · XY"))):
            grid.addWidget(caption(title), 0, column)
            pane = SectionView()
            pane.picked.connect(self._pick_visible_xy)
            grid.addWidget(pane, 1, column)
            self.views[key] = pane
        for column, (key, title) in enumerate((("xz", "XZ · through selected Y"), ("yz", "YZ · through selected X"))):
            grid.addWidget(caption(title), 2, column)
            pane = SectionView()
            pane.picked.connect(self._pick_xz if key == "xz" else self._pick_yz)
            grid.addWidget(pane, 3, column)
            self.views[key] = pane
        grid.addWidget(caption("LIVE / DEAD intensity through depth"), 2, 2)
        self.profile = ProfileView()
        grid.addWidget(self.profile, 3, 2)
        grid.setRowStretch(1, 3)
        grid.setRowStretch(3, 2)
        view.addLayout(grid, 1)
        zrow = QHBoxLayout()
        zrow.addWidget(caption("Z"))
        self.z_slider = QSlider(Qt.Orientation.Horizontal)
        self.z_slider.valueChanged.connect(self._render_slice)
        zrow.addWidget(self.z_slider, 1)
        self.z_text = caption("")
        zrow.addWidget(self.z_text)
        zrow.addWidget(caption("ROI radius (px)"))
        self.radius = QSpinBox()
        self.radius.setRange(1, 40)
        self.radius.setValue(4)
        self.radius.valueChanged.connect(self._render_sections)
        zrow.addWidget(self.radius)
        view.addLayout(zrow)
        review_row = QHBoxLayout()
        self.decision = QComboBox()
        self.decision.addItems(DECISIONS)
        self.decision.currentTextChanged.connect(self._record_review)
        review_row.addWidget(self.decision)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Review note (optional)")
        self.note.editingFinished.connect(self._record_review)
        review_row.addWidget(self.note, 1)
        self.export_review = QPushButton("Save review…")
        self.export_review.clicked.connect(self.save_review)
        review_row.addWidget(self.export_review)
        view.addLayout(review_row)
        view.addWidget(caption("Review annotations are saved separately; original 2D counts and figures remain unchanged.", True))
        self._build_analysis_tab()
        self.status = caption("Preparing stack…", True)
        outer.addWidget(self.status)
        bottom = QHBoxLayout()
        bottom.addWidget(self.analyze_button)
        bottom.addWidget(self.save_figure)
        bottom.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        outer.addLayout(bottom)
        self._set_ready(False)
        self._update_inspection_mode()

    def _update_inspection_mode(self):
        self.tabs.setTabVisible(1, self._legacy_raw)
        self.analyze_button.setVisible(self._legacy_raw)
        self.save_figure.setVisible(bool(self.result) or self._legacy_raw)
        if self._shared_settings:
            config = self._shared_settings["config"]
            segmentation = config.get("segmentation", {})
            parts = []
            for role, title in (("green", "LIVE"), ("red", "DEAD")):
                params = segmentation.get(role, {})
                parts.append(f"{title} region / peak {params.get('low', '?')} / {params.get('high', '?')}; σ {params.get('sigma_px', '?')} px; minimum {params.get('min_area_px', '?')} px²")
            self.shared_settings_caption.setText("Saved projection settings · " + " · ".join(parts) +
                ". Adjust these in Preview segmentation, then choose 2D or 3D and Run analysis.")
        else:
            self.shared_settings_caption.setText("Tune detection in Preview segmentation, then choose 2D or 3D beside the usual Run analysis button." if self._inspection_only else
                "Legacy 3D result: these independent raw-intensity settings are retained for this saved analysis.")

    def _build_analysis_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, "3D candidates · review required")
        layout.addWidget(caption("Count signal objects through the volume once, using calibrated XYZ distances."))
        layout.addWidget(caption("This is a separate experimental analysis. Dual-positive candidates and unresolved groups need review; no definitive viability is inferred. Absolute raw-intensity thresholds differ from the 2D background-contrast thresholds.", True))
        previews = QGridLayout()
        self.analysis_views = {}
        for column, (role, text) in enumerate((("green", "LIVE"), ("red", "DEAD"), ("merged", "Merged"))):
            previews.addWidget(caption(text + " · current optical section"), 0, column)
            pane = SectionView()
            pane.setMinimumHeight(150)
            pane.picked.connect(self._pick_visible_xy)
            self.analysis_views[role] = pane
            previews.addWidget(pane, 1, column)
        layout.addLayout(previews)
        zrow = QHBoxLayout()
        zrow.addWidget(caption("Z"))
        self.analysis_z = QSlider(Qt.Orientation.Horizontal)
        self.analysis_z.valueChanged.connect(self.z_slider.setValue)
        self.z_slider.valueChanged.connect(self.analysis_z.setValue)
        zrow.addWidget(self.analysis_z, 1)
        self.analysis_z_text = caption("")
        zrow.addWidget(self.analysis_z_text)
        layout.addLayout(zrow)
        grid = QGridLayout()
        self.thresholds = {}
        self.volumes = {}
        self.sigmas = {}
        for column, (role, title) in enumerate((("green", "LIVE"), ("red", "DEAD"))):
            group = QGroupBox(title)
            controls = QVBoxLayout(group)
            for key, name in (("low", "Region threshold"), ("high", "Peak threshold")):
                control = ThresholdControl(f"{title} {name}")
                control.changed.connect(self._threshold_changed)
                self.thresholds[role, key] = control
                controls.addWidget(control)
            form = QFormLayout()
            minimum = QDoubleSpinBox()
            minimum.setRange(0.001, 1e9)
            minimum.setDecimals(6)
            minimum.valueChanged.connect(self._settings_changed)
            form.addRow("Minimum signal volume (µm³)", minimum)
            self.volumes[role] = minimum
            sigma = QDoubleSpinBox()
            sigma.setRange(0, 100)
            sigma.setDecimals(6)
            sigma.valueChanged.connect(self._settings_changed)
            form.addRow("3D smoothing sigma (µm)", sigma)
            self.sigmas[role] = sigma
            controls.addLayout(form)
            grid.addWidget(group, 0, column)
        layout.addLayout(grid)
        form = QFormLayout()
        self.seed_distance = QDoubleSpinBox()
        self.seed_distance.setRange(0.001, 1000)
        self.seed_distance.setDecimals(6)
        self.seed_distance.valueChanged.connect(self._settings_changed)
        form.addRow("Minimum seed spacing (µm)", self.seed_distance)
        self.match_distance = QDoubleSpinBox()
        self.match_distance.setRange(0, 1000)
        self.match_distance.setDecimals(6)
        self.match_distance.valueChanged.connect(self._settings_changed)
        form.addRow("Maximum channel matching gap (µm)", self.match_distance)
        self.exclude_border = QCheckBox("Exclude objects touching a volume boundary")
        self.exclude_border.toggled.connect(self._settings_changed)
        form.addRow(self.exclude_border)
        layout.addLayout(form)
        self.preview_mask = QCheckBox("Preview raw region / peak thresholds on the current XY slice")
        self.preview_mask.toggled.connect(self._render_slice)
        layout.addWidget(self.preview_mask)
        layout.addWidget(caption("Raw screening: cyan / amber = region threshold, white = peak threshold. This immediate preview does not apply 3D smoothing, size filtering, seed splitting or matching. Completed 3D masks appear as outlines; rerun explicitly to update them.", True))
        actions = QHBoxLayout()
        self.analyze_button = QPushButton("Analyze this field in 3D…")
        self.analyze_button.clicked.connect(self.start_analysis)
        actions.addWidget(self.analyze_button)
        self.save_figure = QPushButton("Save 3D figure…")
        self.save_figure.clicked.connect(self.save_volume_figure)
        self.save_figure.setEnabled(False)
        actions.addWidget(self.save_figure)
        layout.addLayout(actions)
        self.analysis_summary = caption("No 3D analysis yet.")
        layout.addWidget(self.analysis_summary)
        self.analysis_log = QPlainTextEdit()
        self.analysis_log.setReadOnly(True)
        self.analysis_log.setMaximumBlockCount(100)
        layout.addWidget(self.analysis_log, 1)

    def _set_ready(self, ready):
        self.z_slider.setEnabled(ready)
        self.analysis_z.setEnabled(ready)
        self.export_review.setEnabled(ready)
        self.object_choice.setEnabled(ready)
        self.preview_mask.setEnabled(ready and self._legacy_raw)
        calibrated = (ready and self.stack_info and self.stack_info.get("spacing_um", [None])[0] is not None
                      and self.stack_info["shape_zyx"][0] > 1)
        self.analyze_button.setEnabled(bool(calibrated and self.process is None and not self._inspection_only))

    def _field_changed(self, *_):
        record = self.fields.currentData()
        if record is None or self._closing:
            return
        self._record_review()
        self._pending_record = deepcopy(record)
        self._set_ready(False)
        self.stack_info = None
        self.arrays = {}
        self.labels = {}
        self.result = None
        self._result_settings = None
        self.save_figure.setEnabled(False)
        self._review_target = None
        self.analysis_summary.setText("No 3D analysis for this field.")
        self.profile.profiles = {}
        self.profile.update()
        for pane in self.views.values():
            pane.clear()
        for pane in self.analysis_views.values():
            pane.clear()
        if self.worker is not None:
            self.worker.requestInterruption()
        else:
            self._start_load()

    def _start_load(self):
        if self._closing or self._pending_record is None:
            return
        record, self._pending_record = self._pending_record, None
        self.status.setText("Loading selected channels from the original stack; subsequent visits reuse its cache…")
        worker = StackLoader(record, self.cache, self, volume_run=self._volume_to_load)
        self._volume_to_load = None
        self.worker = worker
        self.stop.setEnabled(True)
        worker.ready.connect(lambda info: self._loaded(worker, info))
        worker.failed.connect(lambda message: self.status.setText(message))
        worker.progress.connect(lambda done, total: self.status.setText(f"Loading optical sections: {done}/{total}"))
        worker.finished.connect(lambda: self._load_finished(worker))
        worker.start()

    def _load_finished(self, worker):
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()
        self.stop.setEnabled(self.process is not None)
        if self._closing and self.process is None:
            self._finish_close()
        elif self._pending_record is not None:
            self._start_load()
        elif worker.isInterruptionRequested():
            self.status.setText("Stack loading stopped.")

    def _loaded(self, worker, info):
        if self.worker is not worker or self._closing or self._pending_record is not None:
            return
        try:
            arrays = {role: np.load(info["paths"][role], mmap_mode="r", allow_pickle=False) for role in ("green", "red")}
            if any(a.ndim != 3 or a.shape != tuple(info["shape_zyx"]) for a in arrays.values()):
                raise ValueError("Cached channel dimensions do not match this stack.")
            defaults = info.pop("_ui_defaults", None)
            volume_result = info.pop("_ui_result", None)
            self.stack_info, self.arrays = info, arrays
            depth, height, width = info["shape_zyx"]
            self.x, self.y = width // 2, height // 2
            self.z_slider.setRange(0, depth - 1)
            self.analysis_z.setRange(0, depth - 1)
            self.z_slider.setValue(depth // 2)
            self._configure_analysis(defaults)
            if self._shared_settings:
                saved_display = self._shared_settings["config"].get("display", {})
                for role in ("green", "red"):
                    if role in saved_display:
                        self.stack_info.setdefault("display", {})[role] = deepcopy(saved_display[role])
            if volume_result:
                self.result = volume_result
                self._result_settings = deepcopy(defaults)
                self._show_summary()
                if self.analysis_settings() == self._result_settings:
                    self._load_labels()
                else:
                    self._show_mismatch()
                self.save_figure.setEnabled(True)
            self._populate_objects()
            self._render_sections()
            self._set_ready(True)
            self._update_inspection_mode()
            spacing = info.get("spacing_um", [None, 1, 1])
            units = f"XYZ spacing {spacing[2]:.3g}, {spacing[1]:.3g}, {spacing[0]:.3g} µm" if spacing[0] else "Z spacing unavailable: side views use plane indices; 3D analysis disabled"
            self.context.setText(f"{depth} selected optical sections · {width} × {height} px · {units}. Click an XY panel to inspect a location.")
            warnings = [item.get("message", "Imported TIFF unavailable; projection comparison could not be performed.")
                        for item in info.get("projection_verification", {}).values() if item.get("state") == "unavailable"]
            self.status.setText("Stack ready. " + (" ".join(dict.fromkeys(warnings)) if warnings else
                "Slice navigation uses cached pixels; review and 3D output are separate from the 2D run."))
        except Exception as exc:
            self.stack_info, self.arrays = None, {}
            self.status.setText(str(exc))
            self._set_ready(False)

    def accept_stack_info(self, info):
        """Inject a prepared stack for deterministic GUI/frozen-app smoke checks."""
        self._loaded(self.worker, deepcopy(info))

    def _configure_analysis(self, defaults=None):
        if defaults and defaults.get("mode") == "projection_config":
            self._shared_settings = deepcopy(defaults)
            self._legacy_raw = False
            self._inspection_only = True
            self.preview_mask.setChecked(False)
            return
        self._initializing = True
        try:
            if defaults is None:
                defaults = {"min_seed_distance_um": 8.0, "match_distance_um": 0.0, "exclude_border": False}
                for role in ("green", "red"):
                    upper = self.stack_info.get("display", {}).get(role, [0, np.iinfo(self.arrays[role].dtype).max])[1]
                    defaults[role] = {"low": max(1, upper * .1), "high": max(1, upper * .2),
                                      "sigma_um": 0, "min_volume_um3": 10}
            for role in ("green", "red"):
                bounds = self.stack_info.get("display", {}).get(role, [0, np.iinfo(self.arrays[role].dtype).max])
                upper = max(bounds[1], defaults[role]["high"], defaults[role]["low"])
                for key in ("low", "high"):
                    self.thresholds[role, key].configure(upper, defaults[role][key])
                self.volumes[role].setValue(defaults[role]["min_volume_um3"])
                self.sigmas[role].setValue(defaults[role].get("sigma_um", 0))
            self.seed_distance.setValue(defaults["min_seed_distance_um"])
            self.match_distance.setValue(defaults["match_distance_um"])
            self.exclude_border.setChecked(defaults.get("exclude_border", False))
        finally:
            self._initializing = False

    def analysis_settings(self):
        if self._shared_settings is not None:
            return deepcopy(self._shared_settings)
        settings = {role: {"low": self.thresholds[role, "low"].value(),
                          "high": self.thresholds[role, "high"].value(),
                          "sigma_um": self.sigmas[role].value(),
                          "min_volume_um3": self.volumes[role].value()} for role in ("green", "red")}
        settings.update(min_seed_distance_um=self.seed_distance.value(),
                        match_distance_um=self.match_distance.value(), exclude_border=self.exclude_border.isChecked())
        return settings

    def _settings_changed(self, *_):
        if self._initializing:
            return
        had_labels = bool(self.labels)
        if self.result and self.analysis_settings() != self._result_settings:
            self.labels = {}
            self._show_mismatch()
        elif self.result:
            self._load_labels()
            self._show_summary()
        self._render_slice()
        if had_labels != bool(self.labels):
            self._render_sections(update_profile=False)

    def _threshold_changed(self):
        if self._initializing or self._inspection_only:
            return
        self.preview_mask.setChecked(True)
        self._settings_changed()

    def _map(self, array, role):
        bounds = self.stack_info.get("display", {}).get(role, [0, np.iinfo(array.dtype).max])
        lo, hi = float(bounds[0]), float(bounds[1])
        return np.asarray(np.clip((array.astype(np.float32) - lo) * (255 / max(hi - lo, 1)), 0, 255), dtype=np.uint8)

    def _rgb(self, green, red):
        g, r = self._map(green, "green"), self._map(red, "red")
        zeros = np.zeros_like(g)
        return np.stack((r, g, zeros), axis=-1)

    @staticmethod
    def _outline(rgb, labels, color):
        edge = np.zeros(labels.shape, dtype=bool)
        edge[1:] |= labels[1:] != labels[:-1]
        edge[:, 1:] |= labels[:, 1:] != labels[:, :-1]
        edge[0] = edge[-1] = True
        edge[:, 0] = edge[:, -1] = True
        rgb[edge & (labels > 0)] = color

    def _render_slice(self, *_):
        if not self.arrays:
            return
        z = self.z_slider.value()
        depth, height, width = self.stack_info["shape_zyx"]
        crop = self.zoom.currentData()
        crop_h, crop_w = min(height, crop or height), min(width, crop or width)
        y0 = int(np.clip(self.y - crop_h // 2, 0, height - crop_h))
        x0 = int(np.clip(self.x - crop_w // 2, 0, width - crop_w))
        self._view_bounds = (x0, y0, crop_w, crop_h)
        selection = (z, slice(y0, y0 + crop_h), slice(x0, x0 + crop_w))
        step = max(1, math.ceil(max(crop_h, crop_w) / 600))
        raw = {role: array[selection][::step, ::step] for role, array in self.arrays.items()}
        combined = self._rgb(raw["green"], raw["red"])
        crosshair = ((self.x - x0 + .5) / crop_w, (self.y - y0 + .5) / crop_h)
        spacing = self.stack_info.get("spacing_um", [None, 1, 1])
        aspect = crop_w * spacing[2] / (crop_h * spacing[1])
        for role, channel in (("green", 1), ("red", 0)):
            rgb = np.zeros_like(combined)
            rgb[..., channel] = combined[..., channel]
            if self.preview_mask.isChecked():
                rgb[raw[role] >= self.thresholds[role, "low"].value()] = [70, 230, 255] if role == "green" else [255, 195, 50]
                rgb[raw[role] >= self.thresholds[role, "high"].value()] = [255, 255, 255]
            elif self.labels:
                self._outline(rgb, self.labels[role][selection][::step, ::step], (70, 230, 255) if role == "green" else (255, 195, 50))
            self.views[role].set_frame(rgb, aspect, crosshair)
            self.analysis_views[role].set_frame(rgb, aspect, crosshair)
        if self.labels:
            for role, color in (("green", (70, 230, 255)), ("red", (255, 195, 50))):
                labels = self.labels[role][selection][::step, ::step]
                self._outline(combined, labels, color)
        self.views["merged"].set_frame(combined, aspect, crosshair)
        self.analysis_views["merged"].set_frame(combined, aspect, crosshair)
        indices = self.stack_info.get("z_indices", list(range(depth)))
        positions = self.stack_info.get("z_positions_um")
        physical = f" · {positions[z]:.2f} µm" if positions is not None else ""
        self.z_text.setText(f"Plane {indices[z] + 1} ({z + 1}/{depth}){physical}")
        self.analysis_z_text.setText(self.z_text.text())
        self.profile.index = z
        self.profile.update()
        for key in ("xz", "yz"):
            pane = self.views[key]
            coordinate = (self.x + .5) / width if key == "xz" else (self.y + .5) / height
            pane.crosshair = (coordinate, (z + .5) / depth)
            pane.update()

    def _render_sections(self, *_, update_profile=True):
        if not self.arrays:
            return
        depth, height, width = self.stack_info["shape_zyx"]
        spacing = self.stack_info.get("spacing_um", [None, 1, 1])
        # Missing physical Z calibration is clearly labelled; use indices then.
        dz = spacing[0] or 1
        for key in ("xz", "yz"):
            planes = {role: a[:, self.y, :] if key == "xz" else a[:, :, self.x] for role, a in self.arrays.items()}
            step = max(1, math.ceil(max(next(iter(planes.values())).shape) / 700))
            planes = {role: a[::step, ::step] for role, a in planes.items()}
            extent = (width * spacing[2] if key == "xz" else height * spacing[1]) if spacing[0] else (width if key == "xz" else height)
            rgb = self._rgb(planes["green"], planes["red"])
            if self.labels:
                for role, color in (("green", (70, 230, 255)), ("red", (255, 195, 50))):
                    labels = self.labels[role][:, self.y, :] if key == "xz" else self.labels[role][:, :, self.x]
                    self._outline(rgb, labels[::step, ::step], color)
            self.views[key].set_frame(rgb, extent / (depth * dz))
        if update_profile:
            radius = self.radius.value()
            y0, y1 = max(0, self.y - radius), min(height, self.y + radius + 1)
            x0, x1 = max(0, self.x - radius), min(width, self.x + radius + 1)
            indices = np.unique(np.linspace(0, depth - 1, min(depth, 512), dtype=int))
            self.profile.profiles = {role: np.mean(a[indices, y0:y1, x0:x1], axis=(1, 2), dtype=np.float64) for role, a in self.arrays.items()}
            self.profile.depth = depth
            self.profile.sample_indices = indices
            positions = self.stack_info.get("z_positions_um")
            acquisition_indices = self.stack_info.get("z_indices", list(range(depth)))
            self.profile.axis_text = (f"{positions[0]:.1f} → {positions[-1]:.1f} µm" if positions is not None else
                                      f"Planes {acquisition_indices[0] + 1} → {acquisition_indices[-1] + 1}")
        self._render_slice()

    def _pick_visible_xy(self, x, y):
        if self.arrays:
            x0, y0, width, height = self._view_bounds
            self._pick_xy((x0 + x * width) / self.stack_info["shape_zyx"][2],
                          (y0 + y * height) / self.stack_info["shape_zyx"][1])

    def _pick_xy(self, x, y):
        if not self.arrays:
            return
        self._record_review()
        _, height, width = self.stack_info["shape_zyx"]
        self.x, self.y = min(width - 1, int(x * width)), min(height - 1, int(y * height))
        self.object_choice.blockSignals(True)
        self.object_choice.setCurrentIndex(0)
        self.object_choice.blockSignals(False)
        self._review_target = f"location_{self.x}_{self.y}"
        self._restore_review()
        self._render_sections()

    def _pick_xz(self, x, z):
        if self.arrays:
            self._pick_xy(x, (self.y + .5) / self.stack_info["shape_zyx"][1])
            self.z_slider.setValue(min(self.stack_info["shape_zyx"][0] - 1, int(z * self.stack_info["shape_zyx"][0])))

    def _pick_yz(self, y, z):
        if self.arrays:
            self._pick_xy((self.x + .5) / self.stack_info["shape_zyx"][2], y)
            self.z_slider.setValue(min(self.stack_info["shape_zyx"][0] - 1, int(z * self.stack_info["shape_zyx"][0])))

    def _populate_objects(self, *_):
        self.object_choice.blockSignals(True)
        self.object_choice.clear()
        self.object_choice.addItem("Click an XY image to select a location", None)
        field = self.fields.currentText()
        objects = self.detections
        prefix = "2D projection"
        if self.result:
            with Path(self.result["objects"]).open(encoding="utf-8", newline="") as stream:
                objects = list(csv.DictReader(stream))
            prefix = "3D candidate"
        for obj in objects:
            if not self.result and obj.get("image_id", obj.get("field_id")) != field:
                continue
            status = obj.get("status", "unknown")
            if self.only_double.isChecked() and status not in ("double_positive", "dual_positive_candidate", "unresolved"):
                continue
            self.object_choice.addItem(f"{prefix} {obj['object_id']} · {status.replace('_', ' ')}", obj)
        self.object_choice.blockSignals(False)
        self._review_target = f"location_{self.x}_{self.y}"
        self._restore_review()

    def _object_changed(self, *_):
        if not self.arrays:
            return
        self._record_review()
        obj = self.object_choice.currentData()
        if obj is None:
            self._review_target = f"location_{self.x}_{self.y}"
        else:
            _, height, width = self.stack_info["shape_zyx"]
            self.x = int(np.clip(round(float(obj["x"])), 0, width - 1))
            self.y = int(np.clip(round(float(obj["y"])), 0, height - 1))
            self._review_target = ("3d_" if self.result else "2d_") + str(obj["object_id"])
            if self.result and "z" in obj:
                self.z_slider.setValue(int(np.clip(round(float(obj["z"])), 0, self.stack_info["shape_zyx"][0] - 1)))
        self._restore_review()
        self._render_sections()

    def _review_key(self):
        field = self.stack_info.get("field_id", self.fields.currentText()) if self.stack_info else self.fields.currentText()
        result_key = str(Path(self.result["objects"]).parent) if self.result else "2d"
        return field + ":" + result_key + ":" + str(self._review_target)

    def _restore_review(self):
        record = self.reviews.get(self._review_key(), {})
        self.decision.blockSignals(True)
        self.decision.setCurrentText(record.get("decision", "Unreviewed"))
        self.decision.blockSignals(False)
        self.note.setText(record.get("note", ""))

    def _record_review(self, *_):
        if not self.stack_info or self._review_target is None:
            return
        if self.decision.currentText() == "Unreviewed" and not self.note.text().strip():
            self.reviews.pop(self._review_key(), None)
            return
        self.reviews[self._review_key()] = {"field_id": self.stack_info.get("field_id", self.fields.currentText()), "target": self._review_target,
            "decision": self.decision.currentText(), "note": self.note.text().strip(),
            "x": self.x, "y": self.y, "z_index_in_selection": self.z_slider.value(),
            "roi_radius_px": self.radius.value(), "stack": deepcopy(self.stack_info),
            "analysis_result": deepcopy(self.result), "analysis_settings": deepcopy(self._result_settings),
            "projection_settings": deepcopy(self.config),
            "reviewed_at": datetime.now(timezone.utc).isoformat()}

    def _safe_export(self, destination):
        destination = Path(destination).expanduser().resolve()
        protected = [ROOT / "pipeline", ROOT / "reference", ROOT / "configs", self.cache]
        if self.run_path:
            protected.append(self.run_path)
        if self.result:
            protected.append(Path(self.result["objects"]).parent)
        if any(destination == p.resolve() or p.resolve() in destination.parents for p in protected):
            raise ValueError("Save exports outside original data, cached files and completed analysis folders.")
        if any((p / "run_manifest.json").exists() or (p / "result.json").exists() for p in destination.parents):
            raise ValueError("Choose a destination outside a completed analysis folder.")
        records = list(self.records)
        if self.stack_info:
            records.append(self.stack_info.get("import_record", {}))
            protected_files = [self.stack_info.get("source", {}).get("path"), *self.stack_info.get("paths", {}).values()]
        else:
            protected_files = []
        for record in records:
            protected_files.extend([record.get("source", {}).get("path"), record.get("provenance_path")])
            protected_files.extend(record.get("analysis_channel_paths", {}).values())
            protected_files.extend(record.get("result", {}).get("row", {}).get(role) for role in ("green", "red", "ebfp"))
            protected_files.append(record.get("result", {}).get("provenance_path"))
        if any(destination == Path(value).expanduser().resolve() for value in protected_files if value):
            raise ValueError("An export must not overwrite an original source image or its import provenance.")
        return destination

    def save_review(self):
        self._record_review()
        name, _ = QFileDialog.getSaveFileName(self, "Save Z review", str(self.output_base / "z_review.json"), "Review JSON (*.json)")
        if not name:
            return
        try:
            if Path(name).suffix.lower() != ".json":
                raise ValueError("Save the review with a .json filename.")
            destination = self._safe_export(name)
            document = {"schema_version": 1, "kind": "manual_z_review", "origin_mode": self.origin_mode,
                "original_run": str(self.run_path) if self.run_path else None,
                "interpretation": "Manual annotations only; original counts unchanged. Same-cell support does not establish apoptosis.",
                "saved_at": datetime.now(timezone.utc).isoformat(), "reviews": list(self.reviews.values())}
            destination.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            # The JSON is the authoritative export; avoid silently overwriting an
            # unrelated CSV beside a user-selected file.
            csv_path = destination.with_name(destination.stem + "_" + uuid.uuid4().hex[:6] + ".csv")
            columns = ("field_id", "target", "decision", "note", "x", "y", "z_index_in_selection", "roi_radius_px", "reviewed_at")
            with csv_path.open("x", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(document["reviews"])
            self.status.setText(f"Review saved: {destination.name} and {csv_path.name}")
        except Exception as exc:
            self.status.setText(str(exc))

    def start_analysis(self):
        if self._inspection_only:
            self.status.setText("Use the usual Preview segmentation controls, choose 3D on New analysis, then Run analysis.")
            return
        if not self.stack_info or self.process is not None:
            return
        settings = self.analysis_settings()
        if any(settings[role]["high"] < settings[role]["low"] for role in ("green", "red")):
            self.status.setText("Each peak threshold must be at least its region threshold.")
            return
        parent = QFileDialog.getExistingDirectory(self, "Choose parent folder for a new 3D result", str(self.output_base))
        if not parent:
            return
        try:
            output = self._safe_export(Path(parent) / ("volume_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]))
            jobs = self.cache / "jobs"
            jobs.mkdir(parents=True, exist_ok=True)
            job = jobs / (uuid.uuid4().hex + ".json")
            snapshot = deepcopy(self.stack_info)
            job.write_text(json.dumps({"stack_info": snapshot, "settings": settings, "output_dir": str(output)}, indent=2, allow_nan=False), encoding="utf-8")
            self._analysis_cancelled = False
            self._active_job = job
            self._analysis_text = ""
            self.analysis_log.clear()
            process = QProcess(self)
            self.process = process
            process.setWorkingDirectory(str(ROOT))
            env = QProcessEnvironment.systemEnvironment()
            env.insert("PYTHONUTF8", "1")
            env.insert("PYTHONUNBUFFERED", "1")
            env.insert("MPLBACKEND", "Agg")
            process.setProcessEnvironment(env)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.readyReadStandardOutput.connect(lambda: self._read_process(process))
            process.finished.connect(lambda code, status: self._analysis_finished(process, job, output, settings, snapshot, code))
            process.errorOccurred.connect(lambda error: self._process_error(process, error))
            progress_timer = QTimer(process)
            progress_timer.setInterval(200)
            progress_timer.timeout.connect(lambda: self._poll_progress(process, job))
            progress_timer.start()
            prefix = [] if getattr(sys, "frozen", False) else [str(ROOT / "run_app.py")]
            self.fields.setEnabled(False)
            self.analyze_button.setEnabled(False)
            self.stop.setEnabled(True)
            self.status.setText("Analyzing 3D candidates in a separate worker. Slice review remains available.")
            process.start(sys.executable, [*prefix, "--volume-analysis", str(job)])
        except Exception as exc:
            self.status.setText(str(exc))

    def _read_process(self, process):
        text = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._analysis_text = (self._analysis_text + text)[-12000:]
        if text.strip():
            self.analysis_log.appendPlainText(text.rstrip())

    def _poll_progress(self, process, job):
        if self.process is not process:
            return
        try:
            status = json.loads(job.with_name(job.stem + "_status.json").read_text(encoding="utf-8"))
            message = status.get("progress")
            if status.get("status") == "running" and message:
                self.status.setText(str(message))
        except (OSError, ValueError):
            pass

    def _process_error(self, process, error):
        if error == QProcess.ProcessError.FailedToStart and self.process is process:
            self.status.setText("Could not start the 3D worker: " + process.errorString())
            self.process = None
            process.deleteLater()
            self.fields.setEnabled(True)
            self.stop.setEnabled(self.worker is not None)
            self._set_ready(bool(self.arrays))
            if self._closing and self.worker is None:
                self._finish_close()

    def _analysis_finished(self, process, job, output, settings, snapshot, code):
        if self.process is not process:
            return
        self._read_process(process)
        self.process = None
        process.deleteLater()
        self.fields.setEnabled(True)
        self.stop.setEnabled(self.worker is not None)
        self._set_ready(bool(self.arrays))
        if self._analysis_cancelled:
            self._cleanup_cancelled(output)
        if self._closing:
            if self.worker is None:
                self._finish_close()
            return
        if self._analysis_cancelled:
            self.status.setText("3D analysis stopped. Incomplete output is not loaded; completed results remain intact.")
            return
        try:
            if code != 0:
                status_path = job.with_name(job.stem + "_status.json")
                status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
                raise ValueError(status.get("error") or "3D analysis failed. See the worker log.")
            result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            if "result" in result:
                result = result["result"]
            self.result = result
            self._result_settings = deepcopy(settings)
            if self.analysis_settings() == settings:
                self._load_labels()
            else:
                self.labels = {}
            summary = json.loads(Path(result["summary"]).read_text(encoding="utf-8"))
            self._show_summary(summary)
            if self.analysis_settings() != self._result_settings:
                self._show_mismatch()
            self.save_figure.setEnabled(True)
            self._populate_objects()
            self._render_sections(update_profile=False)
            self.status.setText(f"3D results saved in {output}. Select candidates on the optical-sections tab to review.")
        except Exception as exc:
            self.status.setText(str(exc))

    def _load_labels(self):
        if self.result:
            self.labels = {role: np.load(self.result[f"labels_{role}"], mmap_mode="r", allow_pickle=False) for role in ("green", "red")}

    def _cleanup_cancelled(self, output):
        """Only remove this cancelled job's unique scratch children after exit."""
        import shutil
        output = Path(output).resolve()
        base, prefix = output.parent, ".volume-pending-" + output.name + "-"
        for stage in base.glob(prefix + "*"):
            if (stage.is_dir() and not stage.is_symlink() and stage.resolve().parent == base
                    and stage.name.startswith(prefix) and len(stage.name) > len(prefix)):
                try:
                    shutil.rmtree(stage)
                except OSError:
                    self.analysis_log.appendPlainText(f"Temporary files could not be removed: {stage}")

    def _show_summary(self, summary=None):
        summary = summary or json.loads(Path(self.result["summary"]).read_text(encoding="utf-8"))
        counts = summary.get("counts", {})
        self.analysis_summary.setText("3D candidates — review required\n" + " · ".join(
            f"{name.replace('_', ' ')}: {counts.get(name, 0)}" for name in
            ("green_only", "red_only", "dual_positive_candidate", "unresolved")) +
            f"\nCandidate count range: {counts.get('candidate_count_min', '?')}–{counts.get('candidate_count_max', '?')}. Original 2D counts are unchanged.")

    def _show_mismatch(self):
        self.analysis_summary.setText("Settings changed. The saved 3D result is preserved; its counts, candidate list and saved figure use the previous settings. Rerun to update detections. Stale outlines are hidden.")

    def open_volume_result(self):
        if self.worker is not None or self.process is not None:
            self.status.setText("Wait for the current job to finish, or stop it before opening another result.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Open a completed 3D result", str(self.output_base))
        if not folder:
            return
        try:
            dialog = StackReviewDialog(volume_run=folder, parent=self, output_base=self.output_base, cache_dir=self.cache)
            dialog.exec()
        except Exception as exc:
            self.status.setText(str(exc))

    def save_volume_figure(self):
        if not self.result:
            return
        source = Path(self.result["figure"])
        try:
            expected = json.loads(Path(self.result["checksums"]).read_text(encoding="utf-8"))["figure.png"]
        except Exception as exc:
            self.status.setText("Cannot verify the saved 3D figure before export: " + str(exc))
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save 3D candidate figure", str(self.output_base / "3d_candidates.png"), "PNG image (*.png)")
        if filename:
            try:
                if Path(filename).suffix.lower() != ".png":
                    raise ValueError("Save the 3D figure with a .png filename.")
                destination = self._safe_export(filename)
                content = source.read_bytes()
                if hashlib.sha256(content).hexdigest() != expected:
                    raise ValueError("The saved 3D figure changed after analysis. Reopen or verify this result before exporting.")
                destination.write_bytes(content)
                self.status.setText(f"Saved 3D figure with its completed analysis settings: {destination}")
            except Exception as exc:
                self.status.setText(str(exc))

    def _stop(self):
        self._pending_record = None
        if self.worker is not None:
            self.worker.requestInterruption()
            self.status.setText("Stopping stack read…")
        if self.process is not None:
            self._analysis_cancelled = True
            process = self.process
            try:
                self._active_job.with_name(self._active_job.stem + "_cancel").write_text("cancel\n", encoding="utf-8")
                self.status.setText("Stopping 3D analysis and cleaning its temporary files…")
                QTimer.singleShot(10000, lambda: process.kill() if self.process is process else None)
            except (AttributeError, OSError):
                process.kill()

    def reject(self):
        self._record_review()
        self._closing = True
        self._stop()
        if self.worker is None and self.process is None:
            self._finish_close()

    def _finish_close(self):
        self.arrays, self.labels = {}, {}
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None or self.process is not None:
            event.ignore()
            self.reject()
        else:
            self.arrays, self.labels = {}, {}
            event.accept()
