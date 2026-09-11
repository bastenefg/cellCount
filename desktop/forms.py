"""Desktop input dialogs; analysis and configuration validation stay in pipeline."""
from copy import deepcopy
from pathlib import Path
import math
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from pipeline.io import validate_config


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("muted")
    return label


def _form_group(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    layout = QFormLayout(box)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    layout.setHorizontalSpacing(24)
    layout.setVerticalSpacing(12)
    return box, layout


class FieldDialog(QDialog):
    """Add or edit a single field with explicit biological replicate identity."""

    def __init__(self, parent=None, row=None):
        super().__init__(parent)
        self.setWindowTitle("Edit field" if row else "Add microscopy field")
        self.setMinimumWidth(660)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        title = QLabel("Edit microscopy field" if row else "Add microscopy field")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(_note(
            "Choose registered, single-plane scalar TIFF channels from the same field. "
            "Green and red are required; EBFP is optional."
        ))

        self.fields: dict[str, QLineEdit] = {}
        identity_box, identity_form = _form_group("Field identity")
        for key, label, placeholder in (
            ("image_id", "Field ID", "Unique field identifier, e.g. gel1_field01"),
            ("replicate_id", "Replicate ID", "Actual independent replicate, e.g. gel1"),
        ):
            edit = QLineEdit(str((row or {}).get(key) or ""))
            edit.setObjectName(key)
            edit.setPlaceholderText(placeholder)
            edit.setAccessibleName(label)
            edit.setClearButtonEnabled(True)
            edit.setToolTip("Required. Start with a letter or digit; use letters, digits, _, -, or .")
            self.fields[key] = edit
            identity_form.addRow(label + " *", edit)
        identity_form.addRow(_note(
            "Enter the independent biological replicate explicitly. Nonoverlapping fields "
            "from the same replicate must share its ID; a new field is not a new replicate."
        ))
        layout.addWidget(identity_box)

        channels_box, channels_form = _form_group("Channel images")
        for key, label in (("green", "Green TIFF *"), ("red", "Red TIFF *"), ("ebfp", "EBFP TIFF")):
            container = QWidget()
            channel_layout = QHBoxLayout(container)
            channel_layout.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(str((row or {}).get(key) or ""))
            edit.setObjectName(key)
            edit.setAccessibleName(label.replace(" *", ""))
            edit.setPlaceholderText("Optional EBFP channel" if key == "ebfp" else "Select a TIFF file")
            self.fields[key] = edit
            channel_layout.addWidget(edit, 1)
            browse = QPushButton("Browse…")
            browse.setAccessibleName("Browse " + label.replace(" *", ""))
            browse.clicked.connect(lambda checked=False, channel=key: self._browse(channel))
            channel_layout.addWidget(browse)
            if key == "ebfp":
                clear = QPushButton("Clear")
                clear.setAccessibleName("Clear optional EBFP TIFF")
                clear.clicked.connect(edit.clear)
                channel_layout.addWidget(clear)
            channel_label = QLabel(label)
            channel_label.setBuddy(edit)
            channels_form.addRow(channel_label, container)
        layout.addWidget(channels_box)
        layout.addWidget(_note(
            "Before analyzing a new acquisition, review image dimensions, intensity dtype, "
            "pixel calibration, and segmentation thresholds in Analysis settings."
        ))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save field" if row else "Add field")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.fields["image_id"].setFocus()

    def _browse(self, channel: str) -> None:
        current = self.fields[channel].text().strip()
        if not current:
            current = next((self.fields[k].text().strip() for k in ("green", "red", "ebfp")
                            if self.fields[k].text().strip()), "")
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select " + channel + " TIFF", current,
            "TIFF images (*.tif *.tiff *.TIF *.TIFF)",
        )
        if filename:
            self.fields[channel].setText(str(Path(filename).resolve()))

    def result_row(self) -> dict[str, str]:
        row = {key: field.text().strip() for key, field in self.fields.items()}
        for key, label in (("image_id", "Field ID"), ("replicate_id", "Replicate ID")):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", row[key]):
                raise ValueError(label + " is required. Start with a letter or digit and use only "
                                 "letters, digits, underscores, hyphens, or periods.")
        selected = set()
        for channel in ("green", "red", "ebfp"):
            if not row[channel]:
                if channel == "ebfp":
                    continue
                raise ValueError("Select the " + channel + " TIFF image.")
            path = Path(row[channel]).expanduser().resolve()
            if path.suffix.lower() not in (".tif", ".tiff"):
                raise ValueError(channel.upper() + " must be a .tif or .tiff image.")
            if not path.is_file():
                raise ValueError(channel.upper() + " image does not exist: " + str(path))
            if path in selected:
                raise ValueError("Each channel must use a different TIFF image.")
            selected.add(path)
            row[channel] = str(path)
        return row

    def accept(self) -> None:
        try:
            self.result_row()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Check field details", str(error))
            return
        super().accept()


class ConfigDialog(QDialog):
    """Edit frequently used settings without changing unexposed configuration."""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._source = deepcopy(config)
        self.inputs: dict[str, QLineEdit | QComboBox] = {}
        self._number_types: dict[str, type] = {}
        self.setWindowTitle("Analysis settings")
        self.resize(740, 780)
        self.setMinimumSize(580, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        title = QLabel("Analysis settings")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(_note(
            "Channels must be registered before analysis. Review calibration and thresholds "
            "for each new acquisition; intensity values are used without automatic rescaling."
        ))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(16)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        input_box, input_form = _form_group("Image format and calibration")
        self._number(input_form, "input.expected_shape.0", "Image height (pixels)", int)
        self._number(input_form, "input.expected_shape.1", "Image width (pixels)", int)
        dtype = QComboBox()
        dtype.setObjectName("input_dtype")
        dtype.setAccessibleName("Intensity dtype")
        dtype.addItems(["uint8", "uint16"])
        dtype.setCurrentText(config["input"]["dtype"])
        self.inputs["input.dtype"] = dtype
        input_form.addRow("Intensity dtype", dtype)
        self._number(input_form, "input.pixel_size_um.0", "Pixel size Y (µm/pixel)", float)
        self._number(input_form, "input.pixel_size_um.1", "Pixel size X (µm/pixel)", float)
        input_form.addRow(_note("Enter calibration from the acquisition metadata. Y is vertical; X is horizontal."))
        content_layout.addWidget(input_box)

        for channel in ("green", "red"):
            box, form = _form_group(channel.capitalize() + " segmentation")
            prefix = "segmentation." + channel + "."
            self._number(form, prefix + "sigma_px", "Smoothing sigma (pixels)", float)
            self._number(form, prefix + "high", "High threshold (intensity)", float)
            self._number(form, prefix + "low", "Low threshold (intensity)", float)
            self._number(form, prefix + "min_area_px", "Minimum area (pixels²)", int)
            content_layout.addWidget(box)

        other_box, other_form = _form_group("Background, matching, and optional EBFP")
        self._number(other_form, "segmentation.background_sigma_px", "Background sigma (pixels)", float)
        self._number(other_form, "matching.max_distance_px", "Maximum matching distance (pixels)", float)
        self._number(other_form, "ebfp.q_threshold", "EBFP q threshold", float)
        other_form.addRow(_note(
            "EBFP is evaluated only when a channel is supplied. Matching distance must fit "
            "within the configured overlap window. Advanced settings remain exactly as loaded."
        ))
        content_layout.addWidget(other_box)
        content_layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply settings")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _number(self, form: QFormLayout, path: str, label: str, number_type: type) -> None:
        value = self._source
        for part in path.split("."):
            value = value[int(part)] if isinstance(value, list) else value[part]
        edit = QLineEdit(str(value))
        edit.setObjectName(path.replace(".", "_"))
        edit.setAccessibleName(label)
        edit.setToolTip("Whole number required" if number_type is int else "Number; decimals or scientific notation accepted")
        self.inputs[path] = edit
        self._number_types[path] = number_type
        form.addRow(label, edit)

    def result_config(self) -> dict:
        result = deepcopy(self._source)
        for path, widget in self.inputs.items():
            if isinstance(widget, QComboBox):
                value = widget.currentText()
            else:
                try:
                    value = self._number_types[path](widget.text().strip())
                except ValueError as error:
                    raise ValueError(widget.accessibleName() + " must be a valid "
                                     + ("whole number." if self._number_types[path] is int else "number.")) from error
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(widget.accessibleName() + " must be finite.")
                # Preserve the exact original value/type when its text was not edited.
                original = self._source
                for part in path.split("."):
                    original = original[int(part)] if isinstance(original, list) else original[part]
                if widget.text().strip() == str(original):
                    value = original
            parts = path.split(".")
            target = result
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) else target[part]
            target[int(parts[-1]) if isinstance(target, list) else parts[-1]] = value
        validate_config(result)
        return result

    def accept(self) -> None:
        try:
            self.result_config()
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            QMessageBox.warning(self, "Check analysis settings", str(error))
            return
        super().accept()
