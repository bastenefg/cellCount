"""Local interface for the original 2D pipeline and separate stack analysis."""
from pathlib import Path
from copy import deepcopy
import csv
import hashlib
import io
import json
import sys
import time
import uuid

from PySide6.QtCore import QProcess, QProcessEnvironment, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QTabWidget, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from . import __version__
from .forms import ConfigDialog, FieldDialog
from .quick_start import QuickStartWidget
from .services import (
    ROOT, config_with_imports, default_config, input_compatible, metric, new_run_name, number, output_path,
    prepare_analysis, process_command, read_manifest, read_results, validate_rows, write_manifest,
)

STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 13px; color: #253c47; }
QMainWindow, #workspace { background: #f2f5f4; }
#sidebar { background: #163b40; }
#sidebar QLabel { color: #cededb; background: transparent; }
#sidebar #brand { color: white; font-size: 23px; font-weight: 650; }
#sidebar QPushButton { text-align: left; color: #d7e6e3; background: transparent; border: 0; padding: 13px 16px; border-radius: 7px; }
#sidebar QPushButton:hover { background: #234d50; }
#sidebar QPushButton:checked { background: #2d5e5e; color: white; font-weight: 600; }
QLabel { background: transparent; }
#eyebrow { color: #27796c; font-size: 11px; font-weight: 700; }
#title { font-size: 29px; font-weight: 650; color: #183c40; }
#subtitle, #muted { color: #677d84; }
#section { font-size: 16px; font-weight: 650; }
#card { background: white; border: 1px solid #dfe7e4; border-radius: 10px; }
#metricValue { color: #176c5d; font-size: 27px; font-weight: 650; }
#badge { color: #226d5b; background: #e5f1eb; border-radius: 5px; padding: 6px 10px; font-weight: 600; }
QPushButton { background: white; border: 1px solid #cbd9d5; border-radius: 6px; padding: 8px 13px; font-weight: 600; }
QPushButton:hover { background: #ecf5f1; border-color: #81b4a7; }
QPushButton:pressed { background: #dcebe5; }
QPushButton:disabled { color: #9caaa7; background: #eff2f1; border-color: #e0e5e3; }
QPushButton#primary { background: #1c7967; color: white; border: 1px solid #1c7967; }
QPushButton#primary:hover { background: #146453; }
QPushButton#primary:disabled { background: #b6cbc3; border-color: #b6cbc3; }
QPushButton#danger { color: #a14646; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #cad8d3; padding: 7px; border-radius: 5px; min-height: 19px; }
QLineEdit:focus, QComboBox:focus { border-color: #258b73; }
QTableWidget { background: white; alternate-background-color: #f7faf8; gridline-color: #edf1ef; border: 1px solid #e1e8e5; border-radius: 5px; selection-background-color: #daeee5; selection-color: #204d43; }
QHeaderView::section { background: #f1f6f3; color: #5d746c; border: 0; border-bottom: 1px solid #dfe7e4; padding: 9px 7px; font-size: 12px; font-weight: 600; }
QTableWidget::item { padding: 6px; }
QPlainTextEdit { background: #f7faf8; border: 1px solid #dce6e0; border-radius: 6px; padding: 9px; }
QProgressBar { border: 0; background: #dfebe5; border-radius: 3px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #31a488; border-radius: 3px; }
QCheckBox { spacing: 8px; }
QScrollArea { border: 0; background: transparent; }
QSplitter::handle { background: #e7eeea; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #e7eeea; border: 0; padding: 10px 20px; margin-right: 5px; color: #536e63; }
QTabBar::tab:selected { background: white; color: #146453; border-top: 2px solid #1c7967; font-weight: 600; }
QToolTip { color: #243e34; background: #fffef5; border: 1px solid #d5dfd7; padding: 5px; }
"""


def application_icon():
    """The module-relative asset path is identical in source and frozen layouts."""
    return QIcon(str(Path(__file__).resolve().parent / "assets" / "live-dead-cell-counter.ico"))


def label(text, name=None, wrap=False):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        item.setObjectName(name)
    item.setWordWrap(wrap)
    return item


def button(text, callback, primary=False):
    item = QPushButton(text)
    if primary:
        item.setObjectName("primary")
    item.clicked.connect(callback)
    return item


def card():
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 17, 20, 17)
    layout.setSpacing(12)
    return frame, layout


def row_layout(*widgets):
    layout = QHBoxLayout()
    layout.setSpacing(10)
    for item in widgets:
        if item is None:
            layout.addStretch()
        else:
            layout.addWidget(item)
    return layout


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.setAlternatingRowColors(True)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.verticalHeader().setDefaultSectionSize(39)
    return widget


class ImageViewer(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(Qt.GlobalColor.white)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMinimumHeight(230)
        self.fit_mode = True

    def load(self, path):
        self.scene().clear()
        self.scene().setSceneRect(0, 0, 0, 0)
        pixmap = QPixmap()
        pixmap.loadFromData(Path(path).read_bytes())
        if pixmap.isNull():
            raise ValueError(f"Could not load preview: {path}")
        self.scene().addPixmap(pixmap)
        self.scene().setSceneRect(pixmap.rect())
        self.fit()

    def fit(self):
        self.fit_mode = True
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor):
        self.fit_mode = False
        self.scale(factor, factor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit()

    def wheelEvent(self, event):
        self.zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Live/Dead Cell Counter")
        self.setWindowIcon(application_icon())
        self.resize(1280, 910)
        self.setMinimumSize(1000, 650)
        self.rows = []
        self.config = default_config()
        self.config_name = "Reference 48 h"
        self.result = None
        self.process = None
        self.summary_process = None
        self.summary_paths = None
        self.summary_error = ""
        self.volume_figure_worker = None
        self.volume_workers = set()
        self.volume_review_cache = None
        self.summary_cache = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation)) / "Live-Dead Cell Counter" / "full_field_summaries"
        self.job_log = None
        self.job_out = None
        self.job_kind = None
        self.job_request = None
        self.job_verifies_volume = False
        self.cancelled = False
        self.log_offset = 0
        self.log_tail = ""
        self.started = 0
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.poll_log)
        self._build()

    def _build(self):
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(208)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(20, 30, 20, 23)
        nav.setSpacing(9)
        nav.addWidget(label("Live/Dead\nCell Counter", "brand"))
        nav.addWidget(label("Microscopy analysis", "muted"))
        nav.addSpacing(31)
        self.nav_buttons = []
        for index, text in enumerate(("01   New analysis", "02   Results", "03   Reference check")):
            item = button(text, lambda checked=False, i=index: self.show_page(i))
            item.setCheckable(True)
            nav.addWidget(item)
            self.nav_buttons.append(item)
        nav.addStretch()
        nav.addWidget(button("User guide  ↗", self.open_guide))
        nav.addWidget(label("Runs locally on your computer.\nImages stay on your device.", wrap=True))
        nav.addSpacing(15)
        nav.addWidget(label(f"App {__version__} · Pipeline 1.1.0"))
        layout.addWidget(sidebar)
        workspace = QWidget()
        workspace.setObjectName("workspace")
        body = QVBoxLayout(workspace)
        body.setContentsMargins(28, 20, 28, 19)
        body.setSpacing(15)
        body.addLayout(row_layout(label("LIVE / DEAD  ·  OPTIONAL EBFP", "eyebrow"), None, label("LOCAL ANALYSIS", "badge")))
        self.pages = QStackedWidget()
        for page in (self._setup_page(), self._results_page(), self._reference_page()):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        body.addWidget(self.pages, 1)
        self.status_text = label("Ready. Choose a LIVE and DEAD image to start.", "muted")
        self.status_text.setWordWrap(True)
        self.cancel_button = button("Stop run", self.cancel_job)
        self.cancel_button.setObjectName("danger")
        self.cancel_button.hide()
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_text, 1)
        status_row.addWidget(self.cancel_button)
        body.addLayout(status_row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        body.addWidget(self.progress)
        layout.addWidget(workspace, 1)
        self.setCentralWidget(central)
        self.show_page(0)

    def _page(self, title, subtitle):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(subtitle, "subtitle", True))
        return page, layout

    def _setup_page(self):
        page, layout = self._page("From images to counts.", "Load a pair of LIVE / DEAD images, inspect the cells, and save your results.")
        self.analysis_mode = QComboBox()
        self.analysis_mode.setAccessibleName("Counting dimension")
        self.analysis_mode.addItem("2D · projection / single image", "2d")
        self.analysis_mode.addItem("3D · original Z stack", "3d")
        self.mode_description = label("", "muted", True)
        layout.addLayout(row_layout(label("Count in", "section"), self.analysis_mode, None))
        layout.addWidget(self.mode_description)
        self.input_modes = QTabWidget()
        self.quick_start = QuickStartWidget()
        self.quick_start.setObjectName("card")
        self.quick_start.previewRequested.connect(self.preview_segmentation)
        self.quick_start.runRequested.connect(self.run_analysis)
        self.quick_start.leicaRequested.connect(self.import_leica)
        self.input_modes.addTab(self.quick_start, "Quick analysis")
        fields, contents = card()
        self.field_count = label("Fields · 0", "section")
        contents.addLayout(row_layout(self.field_count, None,
            button("Import CSV", self.import_csv), button("Export CSV", self.export_csv),
            button("+ Add field", self.add_field, True)))
        self.batch_leica_button = button("Import Leica .lif / .lof…", self.import_leica)
        contents.addLayout(row_layout(self.batch_leica_button, None))
        contents.addWidget(label("One row per field. Nonoverlapping fields from the same biological sample share a replicate ID.", "muted", True))
        self.field_table = table(["Field ID", "Replicate ID", "Live · green", "Dead · red", "EBFP · optional"])
        self.field_table.setMinimumHeight(140)
        self.field_table.cellDoubleClicked.connect(lambda *_: self.edit_field())
        contents.addWidget(self.field_table, 1)
        self.batch_run_button = button("Run analysis →", self.run_analysis, True)
        contents.addLayout(row_layout(button("Edit selected", self.edit_field), button("Remove selected", self.remove_field), None,
                                      button("Preview segmentation…", self.preview_segmentation), self.batch_run_button))
        self.input_modes.addTab(fields, "Batch / CSV")
        layout.addWidget(self.input_modes, 1)
        self.stack_input_button = button("Inspect Z stack…", self.review_input_stack)
        self.stack_input_button.setToolTip("Open an imported Leica stack without first running a 2D analysis.")
        layout.addLayout(row_layout(self.stack_input_button,
            label("Optional: inspect optical sections and overlapping signals.", "muted", True), None))
        settings, content = card()
        self.preset_title = label("Analysis settings · Reference 48 h", "section")
        content.addLayout(row_layout(self.preset_title, None, button("Load settings", self.load_config), button("Review / edit", self.edit_config)))
        self.config_summary = label("", "muted", True)
        content.addWidget(self.config_summary)
        self.update_config_summary()
        content.addWidget(label("The starting preset was calibrated on CHO reference images. Review segmentation and µm/pixel for your acquisition; these settings are not specific to a cell type.", "muted", True))
        self.extended = QCheckBox("Include extended EBFP diagnostics (slower)")
        content.addWidget(self.extended)
        layout.addWidget(settings)
        output, content = card()
        content.addWidget(label("Save this run", "section"))
        self.output_base = QLineEdit(str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / "Live-Dead Cell Counter" / "runs"))
        self.output_base.setAccessibleName("Results parent folder")
        self.run_name = QLineEdit(new_run_name())
        self.run_name.setAccessibleName("New run name")
        self.run_name.setMinimumWidth(220)
        content.addLayout(row_layout(label("Folder"), self.output_base, button("Browse…", self.choose_output)))
        self.run_button = button("Run analysis  →", self.run_analysis, True)
        content.addLayout(row_layout(label("Run name"), self.run_name, None, self.run_button))
        content.addWidget(label("Each run creates a new folder with counts, overlays, figures, settings and verification records.", "muted", True))
        layout.addWidget(output)
        self.input_modes.currentChanged.connect(lambda _: self.update_config_summary())
        self.quick_start.changed.connect(self.update_config_summary)
        self.analysis_mode.currentIndexChanged.connect(self.update_analysis_mode)
        self.update_analysis_mode()
        return page

    def update_analysis_mode(self, *_):
        volume = self.analysis_mode.currentData() == "3d"
        self.mode_description.setText(
            "Use Preview segmentation to tune the projection as usual. The 3D run applies those saved settings through each original Leica Z stack; associations that need review stay flagged. EBFP scoring remains available in 2D."
            if volume else "Use Preview segmentation to tune the image, then Run analysis. The same reviewed settings can also be used for 3D counting when an original Leica stack is available.")
        self.extended.setEnabled(not volume)

    def _results_page(self):
        page, layout = self._page("Inspect the results.", "Review counts and numbered detections before interpreting a new dataset.")
        self.result_title = label("No completed run loaded", "section")
        self.verify_button = button("Verify files", self.verify_results)
        self.verify_button.setEnabled(False)
        self.folder_button = button("Open run folder  ↗", self.open_result_folder)
        self.folder_button.setEnabled(False)
        layout.addLayout(row_layout(self.result_title, None, button("Open run…", self.open_run), self.verify_button, self.folder_button))
        metrics = QHBoxLayout()
        self.metric_labels = []
        self.metric_titles = []
        for title in ("Apparent viability", "EBFP among green only", "Counted objects", "Replicate groups"):
            frame, content = card()
            content.setContentsMargins(15, 14, 15, 14)
            heading = label(title, "muted")
            content.addWidget(heading)
            self.metric_titles.append(heading)
            value, detail = label("—", "metricValue"), label("Awaiting a run", "muted", True)
            content.addWidget(value)
            content.addWidget(detail)
            metrics.addWidget(frame, 1)
            self.metric_labels.append((value, detail))
        layout.addLayout(metrics)
        self.review_bar = QWidget()
        review_layout = QHBoxLayout(self.review_bar)
        review_layout.setContentsMargins(0, 0, 0, 0)
        self.review_caption = label("", "muted", True)
        review_layout.addWidget(self.review_caption, 1)
        review_layout.addWidget(button("Load review…", self.load_volume_review))
        review_layout.addWidget(button("Save review…", self.export_volume_review))
        self.review_bar.hide()
        layout.addWidget(self.review_bar)
        self.result_tabs = QTabWidget()
        summary, contents = card()
        self.table_level = QComboBox()
        self.table_level.addItems(["By field", "By replicate"])
        self.table_level.currentIndexChanged.connect(self.populate_result_table)
        self.csv_button = button("Save summary CSV…", self.save_summary)
        self.csv_button.setEnabled(False)
        contents.addLayout(row_layout(label("Counts & measurements", "section"), self.table_level, None, self.csv_button))
        self.result_table = table(["Field", "Replicate", "Green only", "Red only", "Both", "Total", "Viability %", "EBFP / green %"])
        self.result_table.setMinimumHeight(150)
        contents.addWidget(self.result_table, 1)
        self.counts_note = label("Viability = green only / total. Double-positive objects receive red priority. Blank EBFP stays not measured.", "muted", True)
        contents.addWidget(self.counts_note)
        self.result_tabs.addTab(summary, "Counts")
        visual, contents = card()
        self.preview_choice = QComboBox()
        self.preview_choice.setMinimumWidth(210)
        self.preview_choice.currentIndexChanged.connect(self.select_preview)
        self.inspect_button = button("Inspect segmentation…", self.inspect_saved_segmentation, True)
        self.inspect_button.setEnabled(False)
        contents.addLayout(row_layout(self.preview_choice, self.inspect_button, None,
            button("−", lambda: self.viewer.zoom(1 / 1.25)), button("+", lambda: self.viewer.zoom(1.25)), button("Fit", lambda: self.viewer.fit())))
        self.preview_caption = label("", "muted", True)
        self.figure_button = button("Save summary figure…", self.save_summary_figure)
        self.figure_button.setToolTip("Save the full field with this run's detection outlines and saved threshold values as PNG or SVG.")
        self.figure_button.setEnabled(False)
        self.stack_result_button = button("Inspect Z stack…", self.review_result_stack)
        self.stack_result_button.setEnabled(False)
        self.stack_result_button.setToolTip("Reopen the original Leica stack to inspect projected overlaps.")
        contents.addLayout(row_layout(self.preview_caption, None, self.stack_result_button, self.figure_button))
        self.viewer = ImageViewer()
        contents.addWidget(self.viewer, 1)
        self.result_tabs.addTab(visual, "Figure && detections")
        self.result_tabs.currentChanged.connect(lambda _: QTimer.singleShot(0, self.viewer.fit))
        layout.addWidget(self.result_tabs, 1)
        self.result_notes = QPlainTextEdit()
        self.result_notes.setReadOnly(True)
        self.result_notes.setMaximumHeight(85)
        self.result_notes.setPlaceholderText("Run notes, measurement coverage and verification status appear here.")
        layout.addWidget(self.result_notes)
        return page

    def _reference_page(self):
        page, layout = self._page("Check against the reference.", "Reproduce the bundled S5–S7 images using the original settings, masks and per-object measurements.")
        reference, contents = card()
        contents.addLayout(row_layout(label("Bundled example · S5, S6, S7", "section"), None, label("3 FIELDS · 9 TIFFs", "badge")))
        contents.addWidget(label("The check compares every segmentation mask and object record, including EBFP classifications. A match in rounded percentages alone is insufficient.", "muted", True))
        expected = table(["Field", "Green only", "Red only", "Both", "Total", "Viability"])
        expected.setRowCount(3)
        for i, row in enumerate([("S5", "190", "155", "93", "438", "43.4%"), ("S6", "199", "234", "93", "526", "37.8%"), ("S7", "120", "137", "76", "333", "36.0%")]):
            for j, value in enumerate(row):
                expected.setItem(i, j, QTableWidgetItem(value))
        expected.setFixedHeight(159)
        contents.addWidget(expected)
        contents.addWidget(label("Expected apparent viability: 39.1 ± 3.8%   ·   EBFP among green-only objects: 80.6 ± 4.2%", "section", True))
        contents.addWidget(label("These SDs describe three images; their status as independent biological replicates remains unconfirmed.", "muted", True))
        self.reference_button = button("Run reference check  →", self.run_reference, True)
        contents.addLayout(row_layout(label("Saves a new run in the results folder selected under New analysis.", "muted", True), None, self.reference_button))
        layout.addWidget(reference)
        log_card, contents = card()
        contents.addLayout(row_layout(label("Run activity", "section"), None, button("Open log  ↗", self.open_log)))
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(QFont("Consolas", 10))
        self.log_box.setPlaceholderText("Analysis progress and diagnostic messages appear here for all runs.")
        contents.addWidget(self.log_box, 1)
        layout.addWidget(log_card, 1)
        return page

    def show_page(self, index):
        self.pages.setCurrentIndex(index)
        for i, item in enumerate(self.nav_buttons):
            item.setChecked(index == i)

    def preview_segmentation(self):
        try:
            from .segmentation_dialog import SegmentationDialog
            quick = self.input_modes.currentIndex() == 0
            rows, config = self.analysis_inputs()
            dialog = SegmentationDialog(rows, config, self,
                                        selected=0 if quick else max(0, self.field_table.currentRow()))
            screen = self.screen().availableGeometry()
            dialog.resize(min(1460, screen.width() - 40), min(900, screen.height() - 50))
            if dialog.exec():
                reviewed = dialog.result_config()
                if quick:
                    # TIFF dimensions are per-input convenience, not a change to
                    # the batch preset when a quick preview is accepted.
                    for key in ("expected_shape", "dtype", "pixel_size_um"):
                        reviewed["input"][key] = self.config["input"][key]
                    reviewed["display"] = deepcopy(self.config["display"])
                    reviewed.pop("leica_imports", None)
                self.config = reviewed
                self.config_name = "Visually reviewed settings"
                self.update_config_summary()
                self.status_text.setText("Reviewed settings applied to the next run. The full run will save their exact values with its outputs.")
        except Exception as exc:
            self.error(exc)

    def inspect_saved_segmentation(self):
        if not self.result:
            return
        try:
            from .segmentation_dialog import SegmentationDialog
            out = self.result["path"]
            rows = read_manifest(out / "resolved_samples.csv")
            config = json.loads((out / "effective_config.json").read_text(encoding="utf-8"))
            selected = 0
            choice = self._selected_volume_field().get("image_id") if self.result.get("mode") == "3d" else self.preview_choice.currentText().removeprefix("Detections · ")
            for i, row in enumerate(rows):
                if row["image_id"] == choice:
                    selected = i
                    break
            dialog = SegmentationDialog(rows, config, self, selected=selected, from_results=True)
            screen = self.screen().availableGeometry()
            dialog.resize(min(1460, screen.width() - 40), min(900, screen.height() - 50))
            if dialog.exec():
                self.rows, self.config = rows, dialog.result_config()
                self.config_name = "Visually reviewed settings"
                self.analysis_mode.setCurrentIndex(self.analysis_mode.findData(self.result.get("mode", "2d")))
                self.refresh_fields()
                self.update_config_summary()
                self.run_name.setText(new_run_name())
                self.show_page(0)
                self.status_text.setText("Field list and reviewed settings loaded for a new run. Existing results are preserved.")
        except Exception as exc:
            self.error("Segmentation review needs the original channel images and saved settings. " + str(exc))

    def review_input_stack(self):
        if self.process:
            return
        try:
            from .stack_dialog import StackReviewDialog
            rows, config = self.analysis_inputs()
            if not config.get("leica_imports"):
                raise ValueError("Import the original Leica .lif or .lof first. A projected TIFF alone has no depth information.")
            selected = 0 if self.input_modes.currentIndex() == 0 else max(0, self.field_table.currentRow())
            dialog = StackReviewDialog(rows=rows, config=config, initial_field=rows[selected]["image_id"],
                                       output_base=Path(self.output_base.text()), parent=self)
            self._open_stack_dialog(dialog)
        except Exception as exc:
            self.error(exc)

    def review_result_stack(self):
        if not self.result or self.process:
            return
        try:
            from .stack_dialog import StackReviewDialog
            if self.result.get("mode") == "3d":
                field = self._selected_volume_field()
                dialog = StackReviewDialog(volume_run=field["path"],
                    review_decisions=deepcopy(self.result.get("review_decisions", {}).get(field["image_id"], {})),
                    output_base=Path(self.output_base.text()), parent=self)
                dialog.reviewApplied.connect(lambda decisions: self.apply_volume_review(field["image_id"], decisions, dialog))
            else:
                field = self.preview_choice.currentText().removeprefix("Detections · ")
                dialog = StackReviewDialog(run=self.result["path"], initial_field=field,
                                           output_base=Path(self.output_base.text()), parent=self)
            self._open_stack_dialog(dialog)
        except Exception as exc:
            self.error(exc)

    def _open_stack_dialog(self, dialog):
        screen = self.screen().availableGeometry()
        dialog.resize(min(1480, screen.width() - 40), min(960, screen.height() - 50))
        dialog.exec()
        dialog.deleteLater()

    def error(self, error):
        QMessageBox.warning(self, "Please review", str(error))

    def refresh_fields(self):
        self.input_modes.setCurrentIndex(1)
        self.field_table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            for j, key in enumerate(("image_id", "replicate_id", "green", "red", "ebfp")):
                text = row.get(key, "")
                item = QTableWidgetItem((Path(text).name if text else "Not acquired") if j > 1 else text)
                item.setToolTip(text or "EBFP not acquired; measurements remain missing.")
                self.field_table.setItem(i, j, item)
        count = len({row["replicate_id"] for row in self.rows})
        self.field_count.setText(f"Fields · {len(self.rows)}   /   Replicate groups · {count}")

    def add_field(self):
        dialog = FieldDialog(self)
        if dialog.exec():
            row = dialog.result_row()
            try:
                validate_rows([*self.rows, row])
            except ValueError as exc:
                return self.error(exc)
            self.rows.append(row)
            self.refresh_fields()

    def edit_field(self):
        index = self.field_table.currentRow()
        if index < 0:
            return self.error("Select a field to edit.")
        dialog = FieldDialog(self, self.rows[index])
        if dialog.exec():
            row = dialog.result_row()
            try:
                validate_rows([row if i == index else item for i, item in enumerate(self.rows)])
            except ValueError as exc:
                return self.error(exc)
            self.rows[index] = row
            self.refresh_fields()

    def remove_field(self):
        index = self.field_table.currentRow()
        if index >= 0:
            del self.rows[index]
            self.refresh_fields()

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import image manifest", str(ROOT), "CSV manifests (*.csv)")
        if not path:
            return
        try:
            rows = read_manifest(path)
            config = config_with_imports(rows, self.config)
            if self.rows and QMessageBox.question(self, "Replace field list?", "Importing this CSV will replace the current field list.") != QMessageBox.StandardButton.Yes:
                return
            self.rows = rows
            self.config = config
            self.refresh_fields()
            self.update_config_summary()
        except Exception as exc:
            self.error(exc)

    def import_leica(self):
        try:
            from .leica_dialog import LeicaImportDialog
            output = Path(self.output_base.text()).expanduser().resolve().parent / "imports"
            dialog = LeicaImportDialog(self.config, output, self)
            screen = self.screen().availableGeometry()
            dialog.resize(min(1040, screen.width() - 40), min(820, screen.height() - 50))
            if not dialog.exec():
                return
            bundle = dialog.result_bundle
            if self.input_modes.currentIndex() == 0:
                self.quick_start.set_imported(bundle)
            else:
                self.append_leica_import(bundle)
            self.status_text.setText(bundle["description"] + " Review segmentation before running the analysis.")
            self.update_config_summary()
        except Exception as exc:
            self.error(exc)

    def append_leica_import(self, bundle):
        if self.rows and not input_compatible(self.config["input"], bundle["input"]):
            raise ValueError("This Leica image has a different size, bit depth or pixel calibration from the current batch. Use Quick analysis or start a separate batch.")
        row = deepcopy(bundle["row"])
        for key in ("image_id", "replicate_id"):
            taken = {existing[key].casefold() for existing in self.rows}
            stem = row[key]
            suffix = 2
            while row[key].casefold() in taken:
                row[key] = f"{stem}_{suffix}"
                suffix += 1
        rows = [*self.rows, row]
        validate_rows(rows)
        config = deepcopy(self.config)
        if not self.rows:
            config["input"].update(deepcopy(bundle["input"]))
            config["display"].update(deepcopy(bundle.get("display", {})))
        config = config_with_imports(rows, config)
        self.rows, self.config = rows, config
        self.config_name = "Leica acquisition settings"
        self.refresh_fields()
        self.update_config_summary()

    def export_csv(self):
        if not self.rows:
            return self.error("Add fields before exporting a manifest.")
        path, _ = QFileDialog.getSaveFileName(self, "Export image manifest", "samples.csv", "CSV manifests (*.csv)")
        if path:
            try:
                self.check_export_path(path, ".csv")
                write_manifest(path, self.rows)
            except Exception as exc:
                self.error(exc)

    def update_config_summary(self):
        c = self.config
        imported = self.quick_start.imported if self.input_modes.currentIndex() == 0 else None
        input_settings = imported["input"] if imported else c["input"]
        height, width = input_settings["expected_shape"]
        py, px = input_settings["pixel_size_um"]
        self.preset_title.setText("Analysis settings · " + self.config_name)
        image_format = "Size and bit depth read from TIFFs" if self.input_modes.currentIndex() == 0 and not imported else f"{width} × {height} pixels · {input_settings['dtype']}"
        self.config_summary.setText(f"{image_format} · {px:g} × {py:g} µm/pixel\nLIVE low / high: {c['segmentation']['green']['low']:g} / {c['segmentation']['green']['high']:g}    ·    DEAD low / high: {c['segmentation']['red']['low']:g} / {c['segmentation']['red']['high']:g}    ·    EBFP q ≤ {c['ebfp']['q_threshold']:g}")

    def load_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load analysis settings", str(ROOT / "configs"), "JSON settings (*.json)")
        if path:
            try:
                from pipeline.io import validate_config
                config = validate_config(json.loads(Path(path).read_text(encoding="utf-8-sig")))
                self.config = config
                self.config_name = Path(path).stem
                self.update_config_summary()
            except Exception as exc:
                self.error(exc)

    def edit_config(self):
        dialog = ConfigDialog(self.config, self)
        if dialog.exec():
            config = dialog.result_config()
            if config == self.config:
                return
            path, _ = QFileDialog.getSaveFileName(self, "Save reviewed settings as a new preset", "reviewed_settings.json", "JSON settings (*.json)")
            if not path:
                return
            try:
                self.check_export_path(path, ".json")
                if Path(path).resolve() == (ROOT / "configs" / "reference_48h.json").resolve():
                    raise ValueError("Keep the bundled reference preset intact. Choose a different preset filename.")
                Path(path).write_text(json.dumps(config, indent=2, allow_nan=False) + "\n", encoding="utf-8")
                self.config, self.config_name = config, Path(path).stem
                self.update_config_summary()
            except Exception as exc:
                self.error(exc)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select parent folder for new runs", self.output_base.text())
        if path:
            self.output_base.setText(path)

    def run_analysis(self):
        if self.process:
            return
        try:
            rows, config = self.analysis_inputs()
            if self.analysis_mode.currentData() == "3d":
                from .analysis_3d import prepare_analysis_3d
                cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "stack_review"
                out, args, log = prepare_analysis_3d(self.output_base.text(), self.run_name.text().strip(), rows, config, cache_dir=cache)
                self.start_job("analyze3d", args, out, log)
            else:
                out, args, log = prepare_analysis(self.output_base.text(), self.run_name.text().strip(), rows, config, self.extended.isChecked())
                self.start_job("analyze", args, out, log)
        except Exception as exc:
            self.error(exc)

    def analysis_inputs(self):
        if self.input_modes.currentIndex() == 0:
            return self.quick_start.prepare(self.config)
        validate_rows(self.rows)
        return self.rows, config_with_imports(self.rows, self.config)

    def run_reference(self):
        if self.process:
            return
        try:
            out = output_path(self.output_base.text(), new_run_name("reference"))
            out.parent.mkdir(parents=True, exist_ok=True)
            self.start_job("reference", ["reference", "--output", str(out)], out, out.with_suffix(".log"))
        except Exception as exc:
            self.error(exc)

    def start_job(self, kind, args, out, log):
        self.job_kind, self.job_out, self.job_log = kind, Path(out), Path(log)
        self.job_request = Path(args[1]) if kind == "analyze3d" else None
        self.job_verifies_volume = kind == "verify" and bool(args) and args[0] == "verify-3d"
        self.cancelled = False
        self.log_offset = 0
        self.log_tail = ""
        self.started = time.monotonic()
        self.log_box.clear()
        self.log_box.appendPlainText("Starting " + ("file verification" if kind == "verify" else "reference check" if kind == "reference" else "analysis") + "…")
        self.log_box.appendPlainText("Run folder: " + str(out))
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("MPLBACKEND", "Agg")
        environment.insert("PYTHONUTF8", "1")
        self.process.setProcessEnvironment(environment)
        self.process.finished.connect(self.job_finished)
        self.process.errorOccurred.connect(self.process_error)
        executable, arguments = process_command(args, log)
        self.process.start(executable, arguments)
        self.set_busy(True)
        self.timer.start()
        self.show_page(2)

    def set_busy(self, busy):
        self.analysis_mode.setEnabled(not busy)
        self.run_button.setEnabled(not busy)
        self.quick_start.run_button.setEnabled(not busy)
        self.batch_run_button.setEnabled(not busy)
        self.quick_start.leica_button.setEnabled(not busy)
        self.batch_leica_button.setEnabled(not busy)
        self.stack_input_button.setEnabled(not busy)
        self.stack_result_button.setEnabled(not busy and bool(self.result and self.result.get("leica_imports")))
        self.reference_button.setEnabled(not busy)
        self.verify_button.setEnabled(not busy and self.result is not None)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 100)
        if busy:
            self.status_text.setText("Starting analysis worker…")

    def poll_log(self):
        if self.job_log and self.job_log.exists():
            with self.job_log.open("rb") as stream:
                stream.seek(self.log_offset)
                chunk = stream.read()
                self.log_offset = stream.tell()
            if chunk:
                text = chunk.decode("utf-8", errors="replace")
                self.log_tail = (self.log_tail + text)[-12000:]
                cursor = self.log_box.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                cursor.insertText(text)
                self.log_box.setTextCursor(cursor)
                self.log_box.ensureCursorVisible()
        if self.process:
            elapsed = int(time.monotonic() - self.started)
            stage = "Validating inputs and starting analysis"
            for line in self.log_tail.splitlines():
                if line.startswith(("Analyzing ", "Computing ", "Preparing ", "Segmenting ", "Checking ", "Saving ", "REFERENCE PASSED", "Verified ")):
                    stage = line[:115]
            self.status_text.setText(f"{stage} · {elapsed // 60}:{elapsed % 60:02d} elapsed")

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.log_tail += "\nCould not start worker: " + self.process.errorString()
            self.job_finished(1, QProcess.ExitStatus.CrashExit)

    def job_finished(self, code, exit_status):
        if self.process is None:
            return
        self.poll_log()
        process, self.process = self.process, None
        process.deleteLater()
        self.timer.stop()
        self.set_busy(False)
        if self.cancelled:
            if self.job_kind == "analyze3d":
                try:
                    from .analysis_3d import finish_cancelled_batch
                    finish_cancelled_batch(self.job_out)
                except Exception as exc:
                    self.log_box.appendPlainText("Temporary 3D files could not be fully cleaned: " + str(exc))
            self.status_text.setText("Run stopped. Any partial output is incomplete; use a new run name to retry.")
            self.log_box.appendPlainText("\nStopped by user. Partial outputs are not completed results.")
            if self.job_kind != "verify":
                self.run_name.setText(new_run_name())
            return
        if code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            self.status_text.setText("Run failed. Review the message in Run activity; source images are unchanged.")
            self.log_box.appendPlainText("\nWorker exited with code " + str(code))
            if self.job_kind == "verify" and self.result and self.result["path"] == self.job_out:
                self.result_notes.appendPlainText("File verification FAILED. Review the activity log.")
            self.error(self.log_tail[-2400:] or "The analysis worker exited unexpectedly. See Run activity.")
            return
        if self.job_kind == "verify":
            self.status_text.setText("File verification passed: saved 3D masks, settings, tables and figures match their recorded hashes."
                                    if self.job_verifies_volume else "File verification passed: source images, analysis code and saved outputs match their recorded hashes.")
            if self.result and self.result["path"] == self.job_out:
                self.result_notes.appendPlainText("File verification passed just now. This checks integrity; it does not recompute measurements.")
                self.show_page(1)
            return
        try:
            self.load_result(self.job_out)
            self.run_name.setText(new_run_name())
            text = "Reference check passed: exact masks and classifications; numerical measurements within the reference tolerance." if self.job_kind == "reference" else "Analysis completed. Review counts, detection overlays and run notes."
            self.status_text.setText(text)
        except Exception as exc:
            self.error(exc)

    def cancel_job(self):
        if not self.process:
            return
        if QMessageBox.question(self, "Stop this run?", "Stop the current worker? Any partial output will remain incomplete. You can retry with a new run name.") == QMessageBox.StandardButton.Yes:
            self.cancelled = True
            if self.job_kind == "analyze3d" and self.job_request:
                self.job_request.with_name(self.job_request.stem + "_cancel").write_text("cancel\n", encoding="utf-8")
                process = self.process
                self.status_text.setText("Stopping 3D analysis and cleaning temporary files…")
                QTimer.singleShot(10000, lambda: process.kill() if self.process is process else None)
            else:
                self.process.kill()

    def closeEvent(self, event):
        if self.process:
            QMessageBox.information(self, "Analysis is running", "Use Stop run before closing, or wait for the current run to finish.")
            event.ignore()
        else:
            self.stop_summary_worker()
            for worker in list(self.volume_workers):
                worker.requestInterruption()
                if not worker.wait(5000):
                    event.ignore()
                    self.status_text.setText("Finishing the summary export. Close again in a moment.")
                    return
            event.accept()

    def open_run(self):
        path = QFileDialog.getExistingDirectory(self, "Open a completed run", self.output_base.text())
        if path:
            try:
                if (Path(path) / "result.json").is_file() and (Path(path) / "summary.json").is_file():
                    from .stack_dialog import StackReviewDialog
                    self._open_stack_dialog(StackReviewDialog(volume_run=Path(path),
                        output_base=Path(self.output_base.text()), parent=self))
                    return
                self.load_result(Path(path))
                if not self.process:
                    self.status_text.setText("Saved results loaded. Use Verify files to check their integrity.")
            except Exception as exc:
                self.error(exc)

    def load_result(self, path):
        if (Path(path) / "volume_run.json").is_file():
            return self.load_volume_result(path)
        result = read_results(path)
        result["mode"] = "2d"
        self.stop_summary_worker()
        self.summary_paths = None
        self.summary_error = ""
        self.figure_button.setEnabled(False)
        self.figure_button.setToolTip("Save the full field with this run's detection outlines and saved threshold values as PNG or SVG.")
        self.result = result
        self.review_bar.hide()
        self.result_title.setText(result["path"].name)
        self.result_title.setToolTip(str(result["path"]))
        for title, text in zip(self.metric_titles, ("Apparent viability", "EBFP among green only", "Counted objects", "Replicate groups")):
            title.setText(text)
        self.counts_note.setText("Viability = green only / total. Double-positive objects receive red priority. Blank EBFP stays not measured.")
        for index, key in enumerate(("viability_percent", "ebfp_live_percent")):
            value, detail = metric(result["stats"], key)
            self.metric_labels[index][0].setText(value)
            self.metric_labels[index][1].setText(detail)
        total = sum(int(row["total"]) for row in result["image"])
        self.metric_labels[2][0].setText(f"{total:,}")
        self.metric_labels[2][1].setText(f"Across {len(result['image'])} fields")
        self.metric_labels[3][0].setText(str(len(result["replicate"])))
        self.metric_labels[3][1].setText("Fields pooled within groups")
        notes = ["Saved results loaded; file integrity has not been checked in this session."]
        if result["reference"]:
            check = result["reference"]
            notes.append(f"Recorded reference comparison: {check.get('status', 'unknown')}; {check.get('objects_checked', '?')} objects checked.")
        notes.extend(result["meta"].get("warnings", []))
        for imported in result.get("leica_imports", []):
            field = imported.get("analysis_field_id", "field")
            selection = imported.get("selection", {})
            if selection.get("mode") == "max":
                notes.append(f"Leica field {field}: counts are from a 2D maximum-intensity projection, not a 3D cell count; objects overlapping in Z can merge.")
            else:
                notes.append(f"Leica field {field}: counts are from the selected 2D optical slice.")
        self.result_notes.setPlainText("\n".join(notes))
        self.populate_result_table()
        self.preview_choice.blockSignals(True)
        self.preview_choice.clear()
        self.viewer.scene().clear()
        self.preview_choice.addItem("Summary figure", None)
        for item in sorted((result["path"] / "qc").glob("*.png")):
            self.preview_choice.addItem("Detections · " + item.stem.removesuffix("_detections"), str(item))
        if self.preview_choice.count() > 1:
            self.preview_choice.setCurrentIndex(1)
        self.preview_choice.blockSignals(False)
        self.select_preview()
        self.folder_button.setEnabled(True)
        self.csv_button.setEnabled(True)
        self.verify_button.setEnabled(self.process is None)
        self.inspect_button.setEnabled(True)
        self.stack_result_button.setEnabled(self.process is None and bool(result.get("leica_imports")))
        self.result_tabs.setCurrentIndex(1)
        self.show_page(1)
        self.prepare_summary()

    def load_volume_result(self, path):
        from .analysis_3d import read_analysis_3d
        from .viability_3d import build_report, load_review
        result = read_analysis_3d(Path(path))
        result["mode"] = "3d"
        result["leica_imports"] = result["config"].get("leica_imports", [])
        self.stop_summary_worker()
        self.summary_paths = None
        self.summary_error = ""
        self.result = result
        self.review_bar.show()
        self.figure_button.setToolTip("Save the selected field's 3D summary as PNG, with its saved masks and exact reviewed settings.")
        self.result_title.setText(result["path"].name + " · 3D")
        self.result_title.setToolTip(str(result["path"]))
        notes = ["Saved 3D metadata checked; use Verify files to check full label volumes."]
        notes.extend(item for item in result["meta"].get("warnings", []) if "no definitive viability" not in item)
        self.result_notes.setPlainText("\n".join(notes))
        result["review_decisions"] = {}
        result["viability_report"] = build_report(result, {})
        try:
            document = load_review(result, cache_dir=self.volume_review_cache)
            if document:
                result["review_document"] = document
                result["review_decisions"] = document["decisions"]
                result["viability_report"] = document["report"]
        except Exception as exc:
            self.result_notes.appendPlainText("Saved review could not be loaded; showing unreviewed results: " + str(exc))
        self.refresh_volume_metrics()
        self.preview_choice.blockSignals(True)
        self.preview_choice.clear()
        self.viewer.scene().clear()
        for field in result["fields"]:
            index = self.preview_choice.count()
            self.preview_choice.addItem("3D summary · " + field["image_id"], None)
            self.preview_choice.setItemData(index, field["image_id"], Qt.ItemDataRole.UserRole + 1)
        self.preview_choice.blockSignals(False)
        self.select_preview()
        self.folder_button.setEnabled(True)
        self.csv_button.setEnabled(True)
        self.verify_button.setEnabled(self.process is None)
        self.inspect_button.setEnabled(True)
        self.stack_result_button.setEnabled(self.process is None and bool(result["fields"]))
        self.result_tabs.setCurrentIndex(1)
        self.show_page(1)
        self.prepare_volume_figures()

    @staticmethod
    def _count_range(record, name):
        low, high = record[name + "_min"], record[name + "_max"]
        return f"{low:,}" if low == high else f"{low:,}–{high:,}"

    @staticmethod
    def _viability_text(record):
        low, high = record["viability_min_pct"], record["viability_max_pct"]
        if low is None:
            return "Not measured"
        if record["viability_pct"] is not None:
            return f"{record['viability_pct']:.1f}%"
        return f"{low:.1f}–{high:.1f}%"

    def refresh_volume_metrics(self):
        report = self.result["viability_report"]
        metrics = report["overall"]
        pending = metrics["pending_groups"]
        title = "Provisional viability" if pending else "Reviewed viability" if metrics["mixed_groups"] else "Apparent viability"
        cards = ((title, self._viability_text(metrics), "Assumption-based range" if pending else "LIVE / total cells"),
                 ("LIVE cells", self._count_range(metrics, "live"), "After channel-pair interpretation"),
                 ("Nonviable cells", f"{metrics['dead_count']:,}", "EthD-positive, including true dual cells"),
                 ("Needs review", f"{pending:,}", f"{metrics['reviewed_groups']} / {metrics['mixed_groups']} mixed groups resolved"))
        for index, (heading, value, detail) in enumerate(cards):
            self.metric_titles[index].setText(heading)
            self.metric_labels[index][0].setText(value)
            self.metric_labels[index][1].setText(detail)
        counts = self.result["summary"]["counts"]
        self.counts_note.setText(
            "L3224 interpretation: each channel object represents one cell; a confirmed LIVE/DEAD pair counts once as nonviable. "
            "Unreviewed and uncertain groups remain in the range, not excluded. The range reflects possible pairings, not a confidence interval. "
            "Values pool cells across fields and replicates. EBFP is not scored in 3D. "
            f"Original segmentation: {counts['green_only']} LIVE only, {counts['red_only']} DEAD only, "
            f"{counts['dual_positive_candidate']} dual candidates, {counts['unresolved']} unresolved groups.")
        document = self.result.get("review_document")
        suffix = "Review saved locally; use Save review to share it with the run." if document else "Use Inspect Z stack to resolve mixed candidates."
        self.review_caption.setText(f"{self._count_range(metrics, 'total')} total cells · {suffix}")
        self.populate_result_table()

    def apply_volume_review(self, field_id, decisions, dialog=None):
        from .viability_3d import save_review
        try:
            updated = deepcopy(self.result["review_decisions"])
            updated[field_id] = decisions
            document = save_review(self.result, updated, cache_dir=self.volume_review_cache)
            self._install_volume_review(document)
            if dialog is not None:
                dialog.acknowledge_review_saved(True)
            return True
        except Exception as exc:
            if dialog is not None:
                dialog.acknowledge_review_saved(False, str(exc))
            self.error("Review could not be saved: " + str(exc))
            return False

    def _install_volume_review(self, document):
        self.result["review_document"] = document
        self.result["review_decisions"] = document["decisions"]
        self.result["viability_report"] = document["report"]
        self.refresh_volume_metrics()
        self.prepare_volume_figures()
        self.status_text.setText("Review saved. Counts and viability updated without repeating segmentation.")

    def load_volume_review(self):
        if not self.result or self.result.get("mode") != "3d":
            return
        result = self.result
        name, _ = QFileDialog.getOpenFileName(self, "Load a review for this 3D run", "", "Review JSON (*.json)")
        if not name:
            return
        try:
            from .viability_3d import load_review_document, save_review
            if self.result is not result:
                raise ValueError("The loaded run changed while choosing a review. Select Load review again for the displayed run.")
            document = load_review_document(result, name)
            saved = save_review(result, document["decisions"], cache_dir=self.volume_review_cache)
            self._install_volume_review(saved)
        except Exception as exc:
            self.error(exc)

    def export_volume_review(self):
        if not self.result or self.result.get("mode") != "3d":
            return
        result = self.result
        decisions = deepcopy(result["review_decisions"])
        document = deepcopy(result.get("review_document"))
        name, _ = QFileDialog.getSaveFileName(self, "Save portable count review", result["path"].name + "_review.json", "Review JSON (*.json)")
        if not name:
            return
        try:
            from .viability_3d import save_review
            destination = Path(name)
            if not destination.suffix:
                destination = destination.with_suffix(".json")
            self.check_export_path(destination, ".json")
            if document is None:
                document = save_review(result, decisions, cache_dir=self.volume_review_cache)
                if self.result is result:
                    self._install_volume_review(document)
            if destination.resolve().is_relative_to(Path(document["path"]).parent):
                raise ValueError("Save the review outside its local revision store.")
            destination.write_bytes(Path(document["path"]).read_bytes())
            self.status_text.setText(f"Review exported to {destination.resolve()}. Share it with the original run folder.")
        except Exception as exc:
            self.error(exc)

    def prepare_volume_figures(self):
        try:
            self._start_volume_figures()
        except Exception as exc:
            self.summary_paths = None
            self.figure_button.setEnabled(False)
            self.summary_error = "Summary unavailable. See run notes below."
            self.result_notes.appendPlainText("3D summary: " + str(exc))

    def _start_volume_figures(self):
        from .viability_worker import ViabilityFigureWorker
        from .viability_3d import source_fingerprint
        if self.volume_figure_worker:
            self.volume_figure_worker.requestInterruption()
            self.volume_figure_worker = None
        result = self.result
        document = result.get("review_document")
        revision = document["revision_id"] if document else "provisional"
        self.preview_choice.blockSignals(True)
        for index, field in enumerate(result["fields"]):
            field.pop("review_figure_sha256", None)
            self.preview_choice.setItemData(index, None)
        self.preview_choice.blockSignals(False)
        self.summary_error = "Preparing summary with current viability and review decisions…"
        self.select_preview()
        output = self.summary_cache / "3d" / source_fingerprint(result) / revision
        worker = ViabilityFigureWorker(deepcopy(result["fields"]), deepcopy(result["viability_report"]), output, revision, self)
        self.volume_figure_worker = worker
        self.volume_workers.add(worker)
        worker.ready.connect(lambda ident, path, digest: self._volume_figure_ready(worker, ident, path, digest))
        worker.failed.connect(lambda ident, error: self._volume_figure_failed(worker, ident, error))
        worker.finished.connect(lambda: self._volume_worker_finished(worker))
        worker.start()

    def _volume_figure_ready(self, worker, ident, path, digest):
        if worker is not self.volume_figure_worker or not self.result or self.result.get("mode") != "3d":
            return
        for index, field in enumerate(self.result["fields"]):
            if field["image_id"] == ident:
                field["review_figure_sha256"] = digest
                self.preview_choice.setItemData(index, path)
                if self.preview_choice.currentIndex() == index:
                    self.select_preview()
                break

    def _volume_figure_failed(self, worker, ident, error):
        if worker is self.volume_figure_worker:
            self.result_notes.appendPlainText(f"Summary for {ident}: {error}")
            self.summary_error = "Summary unavailable. See run notes below."
            self.select_preview()

    def _volume_worker_finished(self, worker):
        self.volume_workers.discard(worker)
        if self.volume_figure_worker is worker:
            self.volume_figure_worker = None
        worker.deleteLater()

    def _selected_volume_field(self):
        if not self.result or self.result.get("mode") != "3d":
            return {}
        ident = self.preview_choice.currentData(Qt.ItemDataRole.UserRole + 1)
        return next((field for field in self.result["fields"] if field["image_id"] == ident),
                    self.result["fields"][0] if self.result["fields"] else {})

    def stop_summary_worker(self):
        if self.volume_figure_worker is not None:
            self.volume_figure_worker.requestInterruption()
            self.volume_figure_worker = None
        process, self.summary_process = self.summary_process, None
        if process is not None:
            process.kill()
            process.waitForFinished(1000)
            process.deleteLater()

    def prepare_summary(self):
        """Render saved measurements off the GUI thread, outside the audited run."""
        from .full_field_summary import cache_key
        run = self.result["path"]
        try:
            key = cache_key(run)
            cache = self.summary_cache / key
            cache.mkdir(parents=True, exist_ok=True)
            for output in sorted(cache.iterdir()):
                if output.is_dir() and not output.name.startswith(".") and self.accept_summary(output, key):
                    return
            output = cache / uuid.uuid4().hex
            process = QProcess(self)
            self.summary_process = process
            process.setWorkingDirectory(str(ROOT))
            environment = QProcessEnvironment.systemEnvironment()
            environment.insert("MPLBACKEND", "Agg")
            environment.insert("PYTHONUTF8", "1")
            process.setProcessEnvironment(environment)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.finished.connect(lambda code, status: self.summary_finished(process, run, output, key, code))
            process.errorOccurred.connect(lambda error: self.summary_start_error(process, error))
            timeout = QTimer(process)
            timeout.setSingleShot(True)
            timeout.timeout.connect(lambda: self.summary_timeout(process))
            timeout.start(120000)
            prefix = [] if getattr(sys, "frozen", False) else [str(ROOT / "run_app.py")]
            process.start(sys.executable, [*prefix, "--full-field-summary", str(run), "--summary-output", str(output)])
        except Exception as exc:
            self.summary_failed(str(exc))

    def accept_summary(self, output, key):
        paths = {"png": output / "figure.png", "svg": output / "figure.svg",
                 "metadata": output / "figure_metadata.json"}
        try:
            metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
            if (metadata.get("cache_key") != key or metadata.get("view") != "full_field"
                    or metadata.get("run") != str(self.result["path"])
                    or not all(p.is_file() and p.stat().st_size for p in paths.values())):
                return False
            for extension in ("png", "svg"):
                with paths[extension].open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if metadata.get("artifacts", {}).get(extension, {}).get("sha256") != digest:
                    return False
            pixmap = QPixmap()
            if not pixmap.loadFromData(paths["png"].read_bytes()):
                return False
        except (OSError, ValueError, TypeError, AttributeError):
            return False
        self.summary_paths = {name: str(path) for name, path in paths.items()}
        self.summary_error = ""
        self.preview_choice.setItemData(0, self.summary_paths["png"])
        self.figure_button.setEnabled(True)
        if self.preview_choice.currentIndex() == 0:
            self.select_preview()
        return True

    def summary_finished(self, process, run, output, key, code):
        if self.summary_process is not process:
            return
        self.summary_process = None
        message = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        process.deleteLater()
        if self.result is None or self.result["path"] != run:
            return
        if code != 0 or not self.accept_summary(output, key):
            self.summary_failed(message[-2000:] or "The full-field summary could not be generated.")

    def summary_start_error(self, process, error):
        if self.summary_process is process and error == QProcess.ProcessError.FailedToStart:
            self.summary_process = None
            self.summary_failed(process.errorString())
            process.deleteLater()

    def summary_timeout(self, process):
        if self.summary_process is process:
            self.stop_summary_worker()
            self.summary_failed("The full-field summary took too long to generate. Reopen the run to retry.")

    def summary_failed(self, message):
        self.summary_paths = None
        self.summary_error = "Full-field summary unavailable. See run notes below."
        self.preview_choice.setItemData(0, None)
        self.figure_button.setEnabled(False)
        self.result_notes.appendPlainText("Full-field summary: " + message)
        if self.preview_choice.currentIndex() == 0:
            self.select_preview()

    def populate_result_table(self, *_):
        if not self.result:
            return
        is_image = self.table_level.currentIndex() == 0
        rows = self.result["image" if is_image else "replicate"]
        volume = self.result.get("mode") == "3d"
        if volume:
            rows = self.result["viability_report"]["image" if is_image else "replicate"]
        headers = (["Field", "Replicate", "LIVE cells", "Nonviable", "Total cells", "Viability %", "Reviewed", "Pending"]
                   if volume else ["Field", "Replicate", "Green only", "Red only", "Both", "Total", "Viability %", "EBFP / green %"])
        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(headers)
        self.result_table.setRowCount(len(rows))
        for i, record in enumerate(rows):
            if volume:
                values = [record.get("image_id", "Pooled"), record.get("replicate_id", ""),
                          self._count_range(record, "live"), record["dead_count"], self._count_range(record, "total"),
                          self._viability_text(record), record["reviewed_groups"], record["pending_groups"]]
            else:
                values = [record.get("image_id", "Pooled"), record.get("replicate_id", ""), *(record[key] for key in ("live_only", "dead_only", "double_positive", "total")), number(record.get("viability_percent")), number(record.get("ebfp_live_percent"))]
            for j, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if j >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(str(value))
                self.result_table.setItem(i, j, item)

    def select_preview(self, *_):
        self.viewer.scene().clear()
        self.viewer.scene().setSceneRect(0, 0, 0, 0)
        self.preview_caption.clear()
        self.preview_caption.setToolTip("")
        path = self.preview_choice.currentData()
        volume = self.result and self.result.get("mode") == "3d"
        if volume:
            self.summary_paths = {"png": path} if path else None
            self.figure_button.setEnabled(bool(path and Path(path).is_file()))
        if self.result and (volume or self.preview_choice.currentIndex() == 0) and not path:
            self.preview_caption.setText(self.summary_error or "Preparing full-field summary…")
            return
        if path:
            description = (self.preview_choice.currentText() if volume else
                           "Summary figure (full field · saved detections)" if self.preview_choice.currentIndex() == 0 else self.preview_choice.currentText())
            self.preview_caption.setText(f"{description} · {self.result['path'].name}")
            self.preview_caption.setToolTip(path)
            try:
                self.viewer.load(path)
            except Exception as exc:
                self.preview_caption.setText("Preview unavailable for this run. See run notes below.")
                self.result_notes.appendPlainText(str(exc))

    def save_summary_figure(self):
        if not self.result:
            return
        run = self.result["path"]
        # A new analysis may finish while the native save dialog is open.
        # Keep the export tied to the run for which the user clicked Save.
        paths = dict(self.summary_paths or {})
        formats = {".png": "PNG image (*.png)", ".svg": "SVG vector figure (*.svg)"}
        available = {ext: title for ext, title in formats.items()
                     if paths.get(ext[1:]) and Path(paths[ext[1:]]).is_file()}
        if not available:
            return self.error(self.summary_error or "The full-field summary is still being prepared.")
        default_ext = next(iter(available))
        expected_png = None
        if self.result.get("mode") == "3d":
            try:
                field = self._selected_volume_field()
                expected_png = field["review_figure_sha256"]
            except Exception as exc:
                return self.error("Cannot verify the selected 3D figure before export: " + str(exc))
        field_suffix = "_" + self._selected_volume_field().get("image_id", "field") if self.result.get("mode") == "3d" else ""
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save summary figure", run.name + field_suffix + "_summary" + default_ext,
            ";;".join(available.values()))
        if not path:
            return
        try:
            destination = Path(path)
            if not destination.suffix:
                extension = next((ext for ext, title in available.items() if title == selected_filter), default_ext)
                destination = destination.with_suffix(extension)
            extension = destination.suffix.lower()
            if extension not in available:
                raise ValueError("Choose an available summary figure format: " + ", ".join(available) + ".")
            self.check_export_path(destination, extension)
            if destination.resolve().is_relative_to(run):
                raise ValueError("Save exported copies outside the completed run to preserve its verification records.")
            if destination.resolve().parent == Path(paths["png"]).parent:
                raise ValueError("Save exported copies outside the summary preview cache.")
            content = Path(paths[extension[1:]]).read_bytes()
            if expected_png and hashlib.sha256(content).hexdigest() != expected_png:
                raise ValueError("The saved 3D figure changed after analysis. Reopen or verify the run before exporting.")
            destination.write_bytes(content)
            self.status_text.setText(f"Summary figure saved to {destination.resolve()}")
        except Exception as exc:
            self.error(exc)

    def save_summary(self):
        if not self.result:
            return
        run = self.result["path"]
        name = "image_summary.csv" if self.table_level.currentIndex() == 0 else "replicate_summary.csv"
        volume_rows = None
        if self.result.get("mode") == "3d":
            volume_rows = deepcopy(self.result["viability_report"]["image" if self.table_level.currentIndex() == 0 else "replicate"])
            revision = self.result.get("review_document", {}).get("revision_id", "provisional")
            for record in volume_rows:
                record["review_revision"] = revision
                record["assumptions"] = self.result["viability_report"]["assumptions"]
        path, _ = QFileDialog.getSaveFileName(self, "Save a copy of the summary", name, "CSV table (*.csv)")
        if path:
            try:
                self.check_export_path(path, ".csv")
                source = run / name
                if Path(path).resolve().is_relative_to(run):
                    raise ValueError("Save exported copies outside the completed run to preserve its verification records.")
                if volume_rows is not None:
                    stream = io.StringIO(newline="")
                    writer = csv.DictWriter(stream, fieldnames=list(volume_rows[0]))
                    writer.writeheader()
                    writer.writerows(volume_rows)
                    Path(path).write_text(stream.getvalue(), encoding="utf-8", newline="")
                else:
                    Path(path).write_bytes(source.read_bytes())
            except Exception as exc:
                self.error(exc)

    def verify_results(self):
        if self.result and not self.process:
            try:
                out = self.result["path"]
                log = Path(self.output_base.text()).expanduser().resolve() / (new_run_name("verify") + ".log")
                log.parent.mkdir(parents=True, exist_ok=True)
                arguments = ["verify-3d", str(out)] if self.result.get("mode") == "3d" else ["verify", "--run", str(out)]
                self.start_job("verify", arguments, out, log)
            except Exception as exc:
                self.error(exc)

    def check_export_path(self, path, extension):
        destination = Path(path).resolve()
        if destination.suffix.lower() != extension:
            raise ValueError(f"Choose a {extension} filename.")
        if destination.is_relative_to(ROOT / "reference"):
            raise ValueError("Save your files outside the bundled reference folder.")
        # Editing an export inside any run would invalidate its audit record.
        if any(any((parent / filename).exists() for filename in ("run_manifest.json", "volume_run.json", "result.json")) for parent in destination.parents):
            raise ValueError("Save exported copies outside analysis run folders.")

    def open_result_folder(self):
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result["path"])))

    def open_log(self):
        if self.job_log and self.job_log.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.job_log)))
        else:
            self.error("Run an analysis or reference check to create a log.")

    def open_guide(self):
        path = ROOT / "APP_GUIDE.md"
        if path.is_file():
            # Render a local, read-only guide in the app without file associations.
            from PySide6.QtWidgets import QDialog, QTextBrowser
            dialog = QDialog(self)
            dialog.setWindowTitle("Live/Dead Cell Counter · User guide")
            dialog.resize(810, 650)
            layout = QVBoxLayout(dialog)
            browser = QTextBrowser()
            browser.setMarkdown(path.read_text(encoding="utf-8"))
            browser.setOpenExternalLinks(True)
            layout.addWidget(browser)
            layout.addWidget(button("Close", dialog.accept))
            dialog.exec()


def main():
    if sys.platform == "win32":
        # Give source launches their own taskbar identity instead of Python's.
        import ctypes
        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        set_app_id("LiveDeadMicroscopy.CellCounter")
    app = QApplication(sys.argv)
    app.setApplicationName("Live/Dead Cell Counter")
    app.setOrganizationName("Live-Dead Microscopy")
    app.setWindowIcon(application_icon())
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    available = app.primaryScreen().availableGeometry()
    window.resize(min(1280, available.width() - 40), min(910, available.height() - 50))
    window.show()
    return app.exec()
