"""Threshold interaction checks without launching a scientific worker."""
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from desktop.services import default_config

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None
else:
    from desktop.segmentation_dialog import SegmentationDialog


@unittest.skipIf(QApplication is None, "Install GUI requirements to run slider tests")
class ThresholdSliderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = default_config()
        self.dialogs = []
        self.publish = self.enterContext(patch("desktop.segmentation_dialog.publish_json"))
        self.enterContext(patch.object(SegmentationDialog, "_start_server",
                                      lambda dialog: setattr(dialog, "_server", Mock())))

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            dialog.deleteLater()
        self.app.processEvents()

    def dialog(self):
        dialog = SegmentationDialog([{
            "image_id": "field_1", "replicate_id": "sample_1",
            "red": "synthetic_red.tif", "green": "synthetic_green.tif", "ebfp": "",
        }], self.settings)
        self.dialogs.append(dialog)
        self.app.processEvents()
        return dialog

    def test_loading_switching_channels_and_slider_range_preserve_exact_values(self):
        self.settings["segmentation"]["red"]["high"] = 4.123456789
        expected = deepcopy(self.settings)
        dialog = self.dialog()
        generation = dialog._generation
        self.assertEqual(dialog.threshold_range.currentData(), 25)
        self.assertEqual(dialog.threshold_sliders["high"].value(), 412)
        dialog.threshold_range.setCurrentIndex(dialog.threshold_range.findData(100))
        self.assertEqual(dialog.threshold_sliders["high"].maximum(), 10000)
        dialog.channel_choice.setCurrentIndex(dialog.channel_choice.findData("green"))
        self.assertEqual(dialog.threshold_sliders["high"].value(), 1000)
        dialog.channel_choice.setCurrentIndex(dialog.channel_choice.findData("red"))
        self.assertEqual(dialog.threshold_range.currentData(), 100)
        self.assertEqual(dialog.result_config(), expected)
        self.assertEqual(self.settings, expected)
        self.assertEqual(dialog._generation, generation)

    def test_numeric_edits_sync_thumbs_without_rounding_and_expand_range(self):
        self.settings["input"]["dtype"] = "uint16"
        dialog = self.dialog()
        dialog.inputs["high"].setValue(1200.123456)
        self.assertEqual(dialog.threshold_range.currentData(), 4095)
        self.assertEqual(dialog.threshold_sliders["high"].value(), 120012)
        self.assertEqual(dialog.result_config()["segmentation"]["red"]["high"], 1200.123456)
        dialog.inputs["high"].setValue(800000.123456)
        self.assertEqual(dialog.threshold_range.currentData(), 1000000)
        self.assertEqual(dialog.result_config()["segmentation"]["red"]["high"], 800000.123456)
        dialog.reset_settings()
        self.assertEqual(dialog.threshold_range.currentData(), 25)
        self.assertEqual(dialog.threshold_sliders["high"].value(), 400)
        self.assertEqual(dialog.result_config(), self.settings)

    def test_sliders_cannot_cross_thresholds_or_set_zero(self):
        dialog = self.dialog()
        dialog.auto.setChecked(False)
        dialog.threshold_sliders["low"].setValue(1000)
        self.assertEqual(dialog.inputs["low"].value(), 4)
        self.assertEqual(dialog.threshold_sliders["low"].value(), 400)
        dialog.threshold_sliders["low"].setValue(0)
        self.assertEqual(dialog.inputs["low"].value(), .000001)
        dialog.threshold_sliders["high"].setValue(0)
        self.assertEqual(dialog.inputs["high"].value(), .000001)
        self.assertEqual(dialog.result_config()["segmentation"]["red"]["low"], .000001)

    def test_drag_waits_for_release_even_when_previous_worker_completes(self):
        dialog = self.dialog()
        self.assertEqual(self.publish.call_count, 1)
        self.publish.reset_mock()
        slider = dialog.threshold_sliders["high"]
        slider.setSliderDown(True)
        for value in (700, 1100, 1450):
            slider.setValue(value)
            QTest.qWait(220)
            self.assertEqual(dialog.inputs["high"].value(), value / 100)
            self.assertFalse(dialog.debounce.isActive())
        # An older preview arriving while the mouse is held must not submit
        # intermediate settings through the worker's automatic retry path.
        response = Path(dialog._temporary.name) / "response.json"
        response.write_text(json.dumps({"id": dialog._request_id, "ok": True}), encoding="utf-8")
        dialog._poll_response()
        QTest.qWait(220)
        dialog.refresh_preview()
        self.assertEqual(self.publish.call_count, 0)
        slider.setSliderDown(False)
        QTest.qWait(250)
        self.assertEqual(self.publish.call_count, 1)
        request = self.publish.call_args.args[1]
        self.assertEqual(request["config"]["segmentation"]["red"]["high"], 14.5)

    def test_auto_update_off_leaves_slider_edits_for_manual_update(self):
        dialog = self.dialog()
        dialog.worker = None
        dialog.response_timer.stop()
        dialog.auto.setChecked(False)
        self.publish.reset_mock()
        slider = dialog.threshold_sliders["high"]
        slider.setSliderDown(True)
        slider.setValue(900)
        slider.setSliderDown(False)
        QTest.qWait(250)
        self.assertEqual(self.publish.call_count, 0)
        dialog.refresh_preview()
        self.assertEqual(self.publish.call_count, 1)
        self.assertEqual(self.publish.call_args.args[1]["config"]["segmentation"]["red"]["high"], 9)


if __name__ == "__main__":
    unittest.main()
