"""Input boundary and configuration-preservation checks for the optional GUI."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QDialog
except ImportError:
    QApplication = None
else:
    from desktop.forms import ConfigDialog, FieldDialog


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(QApplication is None, "Install the GUI requirements to test desktop dialogs")
class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.config = json.loads((ROOT / "configs" / "reference_48h.json").read_text())
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()

    def dialog(self, cls, *args, **kwargs):
        result = cls(*args, **kwargs)
        self.dialogs.append(result)
        return result

    def test_unchanged_configuration_retains_exact_calibration_and_hidden_settings(self):
        self.config["extra_metadata"] = {"names": ["preserve", "me"]}
        dialog = self.dialog(ConfigDialog, self.config)
        result = dialog.result_config()
        self.assertEqual(result, self.config)
        self.assertEqual(result["input"]["pixel_size_um"], [2.272724609375] * 2)
        self.assertIs(type(result["segmentation"]["green"]["high"]), int)
        result["extra_metadata"]["names"].append("new")
        self.assertEqual(self.config["extra_metadata"]["names"], ["preserve", "me"])

    def test_configuration_edit_never_mutates_source(self):
        original = deepcopy(self.config)
        dialog = self.dialog(ConfigDialog, self.config)
        dialog.inputs["segmentation.green.high"].setText("11.5")
        dialog.inputs["input.pixel_size_um.0"].setText("1.2345678901234567")
        result = dialog.result_config()
        self.assertEqual(self.config, original)
        expected = deepcopy(original)
        expected["segmentation"]["green"]["high"] = 11.5
        expected["input"]["pixel_size_um"][0] = 1.2345678901234567
        self.assertEqual(result, expected)

    def test_pipeline_validation_rejects_invalid_thresholds(self):
        dialog = self.dialog(ConfigDialog, self.config)
        dialog.inputs["segmentation.green.low"].setText("11")
        with self.assertRaisesRegex(ValueError, "Invalid segmentation settings"):
            dialog.result_config()
        with patch("desktop.forms.QMessageBox.warning") as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_nonfinite_numbers_and_fractional_dimensions_are_rejected(self):
        dialog = self.dialog(ConfigDialog, self.config)
        dialog.inputs["segmentation.background_sigma_px"].setText("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            dialog.result_config()
        dialog.inputs["segmentation.background_sigma_px"].setText("12")
        dialog.inputs["input.expected_shape.0"].setText("1024.5")
        with self.assertRaisesRegex(ValueError, "whole number"):
            dialog.result_config()

    def test_field_requires_explicit_replicate_and_safe_ids(self):
        dialog = self.dialog(FieldDialog)
        dialog.fields["image_id"].setText("gel1_field01")
        self.assertEqual(dialog.fields["replicate_id"].text(), "")
        with self.assertRaisesRegex(ValueError, "Replicate ID is required"):
            dialog.result_row()
        dialog.fields["replicate_id"].setText("../gel1")
        with self.assertRaisesRegex(ValueError, "Replicate ID is required"):
            dialog.result_row()

    def test_field_returns_absolute_paths_with_optional_ebfp_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            green = Path(directory) / "green.tif"
            red = Path(directory) / "red.TIFF"
            # The dialog validates paths; pipeline input validation reads TIFF contents.
            green.touch()
            red.touch()
            row = {"image_id": "g1_f01", "replicate_id": "g1", "green": str(green), "red": str(red)}
            dialog = self.dialog(FieldDialog, row=row)
            result = dialog.result_row()
            self.assertEqual(result["ebfp"], "")
            self.assertEqual(result["green"], str(green.resolve()))
            self.assertEqual(result["red"], str(red.resolve()))
            dialog.fields["ebfp"].setText(str(green))
            with self.assertRaisesRegex(ValueError, "different TIFF"):
                dialog.result_row()

    def test_missing_channel_file_is_rejected(self):
        dialog = self.dialog(FieldDialog, row={"image_id": "field1", "replicate_id": "gel1",
                                             "green": str(ROOT / "missing_gui_test_image.tif")})
        with self.assertRaisesRegex(ValueError, "does not exist"):
            dialog.result_row()


if __name__ == "__main__":
    unittest.main()
