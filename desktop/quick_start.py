"""Direct TIFF selection; the original pipeline still validates every pixel input."""
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget


CHANNELS = (("green", "LIVE · green"), ("red", "DEAD · red"), ("ebfp", "EBFP · optional"))


def prepare_quick_inputs(paths, config, imported=None):
    """Build one field, adopting only TIFF shape and dtype in a private config.

    Channel roles are supplied explicitly. Pixel calibration and all numerical
    analysis settings come from the selected preset, never inferred from names.
    """
    from PIL import Image
    import numpy as np
    from pipeline.io import read_scalar, validate_config
    from .services import config_with_imports, validate_rows

    for key, title in CHANNELS[:2]:
        if not str(paths.get(key, "")).strip():
            raise ValueError(f"Choose the {title.split(' · ')[0]} TIFF image first.")
    row = {"image_id": "field_1", "replicate_id": "sample_1"}
    if imported:
        row.update({key: imported["row"][key] for key in ("image_id", "replicate_id")})
    row.update({key: str(Path(paths[key]).expanduser().resolve()) if paths.get(key) else ""
                for key, _ in CHANNELS})
    validate_rows([row])
    settings = deepcopy(config)
    if imported:
        if not Path(imported["provenance_path"]).is_file():
            raise ValueError("The Leica import record is missing. Restore it beside the TIFFs or import the acquisition again.")
        settings["input"].update(deepcopy(imported["input"]))
        settings["display"].update(deepcopy(imported.get("display", {})))
    with Image.open(row["green"]) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("Choose one focal plane per TIFF. Image stacks are unsupported.")
        array = np.asarray(image)
        if array.ndim != 2:
            raise ValueError("Choose scalar 2D TIFFs. RGB composites are unsupported.")
        settings["input"]["expected_shape"] = list(array.shape)
        settings["input"]["dtype"] = str(array.dtype)
    settings = config_with_imports([row], settings)
    validate_config(settings)
    for key, _ in CHANNELS:
        if row[key]:
            read_scalar(row[key], settings)
    return [row], settings


class QuickStartWidget(QWidget):
    """An explicit two-channel file picker that retains its state across tabs."""
    changed = Signal()
    previewRequested = Signal()
    runRequested = Signal()
    leicaRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 17, 20, 17)
        layout.setSpacing(12)
        self.imported = None
        heading = QLabel("One field, two images")
        heading.setObjectName("section")
        layout.addWidget(heading)
        help_text = QLabel("Choose the LIVE and DEAD images of the same field. No spreadsheet or sample IDs needed.")
        help_text.setWordWrap(True)
        help_text.setObjectName("muted")
        layout.addWidget(help_text)
        import_actions = QHBoxLayout()
        self.leica_button = QPushButton("Import Leica .lif / .lof…")
        self.leica_button.clicked.connect(lambda: self.leicaRequested.emit())
        import_actions.addWidget(self.leica_button)
        import_actions.addStretch()
        layout.addLayout(import_actions)
        self.import_description = QLabel()
        self.import_description.setWordWrap(True)
        self.import_description.setObjectName("muted")
        self.import_description.hide()
        layout.addWidget(self.import_description)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.paths = {}
        for index, (key, title) in enumerate(CHANNELS):
            caption = QLabel(title)
            entry = QLineEdit()
            entry.setReadOnly(True)
            entry.setAccessibleName(title + " TIFF file")
            entry.setPlaceholderText("Not acquired" if key == "ebfp" else "Choose a .tif or .tiff file")
            choose = QPushButton("Choose TIFF…")
            choose.clicked.connect(lambda _checked=False, channel=key: self.choose_file(channel))
            grid.addWidget(caption, index, 0)
            grid.addWidget(entry, index, 1)
            grid.addWidget(choose, index, 2)
            self.paths[key] = entry
        self.clear_ebfp = QPushButton("Clear EBFP")
        self.clear_ebfp.clicked.connect(lambda: self.set_path("ebfp", ""))
        self.clear_ebfp.setEnabled(False)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        actions = QHBoxLayout()
        swap = QPushButton("Swap LIVE / DEAD")
        swap.clicked.connect(self.swap_channels)
        actions.addWidget(swap)
        actions.addWidget(self.clear_ebfp)
        actions.addStretch()
        preview = QPushButton("Preview segmentation…")
        preview.clicked.connect(lambda: self.previewRequested.emit())
        actions.addWidget(preview)
        self.run_button = QPushButton("Run analysis →")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(lambda: self.runRequested.emit())
        actions.addWidget(self.run_button)
        layout.addLayout(actions)
        hint = QLabel("Use aligned, single-plane TIFFs. Image size and uint8/uint16 format are read automatically; intensities are preserved.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def set_path(self, channel, path):
        value = str(Path(path).resolve()) if path else ""
        if value != self.paths[channel].text():
            self.imported = None
            self.import_description.clear()
            self.import_description.hide()
        self.paths[channel].setText(value)
        self.paths[channel].setToolTip(value)
        self.clear_ebfp.setEnabled(bool(self.paths["ebfp"].text()))
        self.changed.emit()

    def choose_file(self, channel):
        title = dict(CHANNELS)[channel]
        initial = self.paths[channel].text() or next((entry.text() for entry in self.paths.values() if entry.text()), "")
        path, _ = QFileDialog.getOpenFileName(self, f"Choose {title} TIFF", initial, "TIFF images (*.tif *.tiff)")
        if path:
            self.set_path(channel, path)

    def swap_channels(self):
        imported = deepcopy(self.imported)
        live, dead = self.paths["green"].text(), self.paths["red"].text()
        self.set_path("green", dead)
        self.set_path("red", live)
        if imported:
            imported["row"]["green"], imported["row"]["red"] = dead, live
            display = imported.get("display", {})
            if "green" in display and "red" in display:
                display["green"], display["red"] = display["red"], display["green"]
            self.set_imported(imported)

    def set_imported(self, bundle):
        for channel, _ in CHANNELS:
            self.set_path(channel, bundle["row"].get(channel, ""))
        self.imported = deepcopy(bundle)
        self.import_description.setText(bundle["description"])
        self.import_description.show()
        self.changed.emit()

    def prepare(self, config):
        return prepare_quick_inputs({key: entry.text() for key, entry in self.paths.items()}, config, self.imported)
