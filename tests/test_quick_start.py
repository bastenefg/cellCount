"""Direct image loading preserves pixels, calibration and the separate batch."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from desktop.app import MainWindow
from desktop.quick_start import prepare_quick_inputs
from desktop.services import default_config, ROOT, read_manifest


def sample_field():
    # Portable releases intentionally exclude the user's private image pair.
    manifest = ROOT / "samples.csv"
    return read_manifest(manifest if manifest.is_file() else ROOT / "reference" / "samples.csv")[0]


class QuickStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.config = default_config()
        self.paths = self.write_pair()

    def tearDown(self):
        self.temp.cleanup()

    def write_pair(self, shape=(40, 48), dtype=np.uint8):
        paths = {}
        for channel in ("green", "red"):
            array = np.arange(np.prod(shape), dtype=dtype).reshape(shape)
            path = self.folder / (channel + ".tiff")
            Image.fromarray(array).save(path)
            paths[channel] = str(path)
        return paths

    def test_shape_and_dtype_inferred_without_changing_pixels_or_preset(self):
        self.paths = self.write_pair((37, 51), np.uint16)
        before = deepcopy(self.config)
        hashes = {key: hashlib.sha256(Path(path).read_bytes()).hexdigest() for key, path in self.paths.items()}
        rows, settings = prepare_quick_inputs(self.paths, self.config)
        self.assertEqual(rows[0]["image_id"], "field_1")
        self.assertEqual(rows[0]["replicate_id"], "sample_1")
        self.assertEqual(rows[0]["ebfp"], "")
        self.assertEqual(settings["input"]["expected_shape"], [37, 51])
        self.assertEqual(settings["input"]["dtype"], "uint16")
        self.assertEqual(settings["segmentation"], before["segmentation"])
        self.assertEqual(settings["input"]["pixel_size_um"], before["input"]["pixel_size_um"])
        self.assertEqual(self.config, before)
        self.assertEqual(hashes, {key: hashlib.sha256(Path(path).read_bytes()).hexdigest() for key, path in self.paths.items()})

    def test_missing_or_duplicate_channel_rejected(self):
        with self.assertRaisesRegex(ValueError, "DEAD TIFF"):
            prepare_quick_inputs({"green": self.paths["green"]}, self.config)
        with self.assertRaisesRegex(ValueError, "assigned more than once"):
            prepare_quick_inputs({"green": self.paths["green"], "red": self.paths["green"]}, self.config)

    def test_mismatched_dimensions_or_dtype_rejected(self):
        for array, message in ((np.zeros((41, 48), np.uint8), "shape"),
                               (np.zeros((40, 48), np.uint16), "dtype")):
            with self.subTest(message=message):
                Image.fromarray(array).save(self.paths["red"])
                with self.assertRaisesRegex(ValueError, message):
                    prepare_quick_inputs(self.paths, self.config)

    def test_stacks_and_rgb_rejected(self):
        Image.new("L", (40, 48)).save(self.paths["green"], save_all=True, append_images=[Image.new("L", (40, 48))])
        with self.assertRaisesRegex(ValueError, "stacks"):
            prepare_quick_inputs(self.paths, self.config)
        Image.new("RGB", (40, 48)).save(self.paths["green"])
        with self.assertRaisesRegex(ValueError, "RGB"):
            prepare_quick_inputs(self.paths, self.config)

    def test_optional_ebfp_is_validated_and_never_assigned_automatically(self):
        path = self.folder / "optional.tif"
        Image.new("L", (48, 40)).save(path)
        self.paths["ebfp"] = str(path)
        rows, _ = prepare_quick_inputs(self.paths, self.config)
        self.assertEqual(rows[0]["ebfp"], str(path))
        Image.new("L", (48, 41)).save(path)
        with self.assertRaisesRegex(ValueError, "shape"):
            prepare_quick_inputs(self.paths, self.config)

    def test_sample_pair_uses_identical_inputs_and_settings_without_csv(self):
        sample = sample_field()
        rows, settings = prepare_quick_inputs(sample, self.config)
        self.assertEqual(settings, self.config)
        for channel in ("green", "red", "ebfp"):
            self.assertEqual(rows[0][channel], sample[channel])


class QuickWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.sample = sample_field()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_default_mode_runs_selected_pair_with_automatic_ids(self):
        self.assertEqual(self.window.windowTitle(), "Live/Dead Cell Counter")
        self.assertEqual(self.window.input_modes.currentIndex(), 0)
        for channel in ("green", "red"):
            self.window.quick_start.set_path(channel, self.sample[channel])
        with tempfile.TemporaryDirectory() as folder:
            self.window.output_base.setText(folder)
            with patch.object(self.window, "start_job") as start, patch.object(self.window, "error") as error:
                self.window.quick_start.run_button.click()
            error.assert_not_called()
            start.assert_called_once()
            manifest = Path(start.call_args.args[1][2])
            self.assertEqual(read_manifest(manifest)[0]["image_id"], "field_1")
            self.assertEqual(read_manifest(manifest)[0]["green"], self.sample["green"])

    def test_switching_modes_preserves_batch_and_quick_inputs(self):
        self.window.quick_start.set_path("green", self.sample["green"])
        self.window.quick_start.set_path("red", self.sample["red"])
        self.window.rows = [dict(self.sample)]
        self.window.refresh_fields()
        self.assertEqual(self.window.input_modes.currentIndex(), 1)
        before = deepcopy(self.window.rows)
        rows, _ = self.window.analysis_inputs()
        self.assertEqual(rows, before)
        self.window.input_modes.setCurrentIndex(0)
        rows, _ = self.window.analysis_inputs()
        self.assertEqual(rows[0]["image_id"], "field_1")
        self.assertEqual(self.window.rows, before)
        self.window.quick_start.swap_channels()
        self.assertEqual(self.window.quick_start.paths["green"].text(), self.sample["red"])
        self.window.input_modes.setCurrentIndex(1)
        rows, _ = self.window.analysis_inputs()
        self.assertEqual(rows, before)

    def test_quick_preview_applies_thresholds_without_overwriting_batch_dimensions(self):
        self.window.config["input"]["expected_shape"] = [64, 80]
        for channel in ("green", "red"):
            self.window.quick_start.set_path(channel, self.sample[channel])
        changed = default_config()
        changed["segmentation"]["red"]["high"] = 15
        with patch("desktop.segmentation_dialog.SegmentationDialog") as dialog_type, patch.object(self.window, "error") as error:
            dialog_type.return_value.exec.return_value = True
            dialog_type.return_value.result_config.return_value = changed
            self.window.preview_segmentation()
        error.assert_not_called()
        self.assertEqual(dialog_type.call_args.args[1]["input"]["expected_shape"], [1024, 1024])
        self.assertEqual(self.window.config["input"]["expected_shape"], [64, 80])
        self.assertEqual(self.window.config["segmentation"]["red"]["high"], 15)
        self.assertEqual(self.window.rows, [])


if __name__ == "__main__":
    unittest.main()
