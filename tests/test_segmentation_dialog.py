"""Qt integration checks with real isolated segmentation workers and synthetic TIFFs."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from desktop.services import default_config

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEventLoop, QProcess, QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QGraphicsSimpleTextItem
except ImportError:
    QApplication = None
else:
    from desktop.segmentation_dialog import SegmentationDialog


@unittest.skipIf(QApplication is None, "Install GUI requirements to run segmentation dialog tests")
class SegmentationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.settings = default_config()
        self.settings["input"]["expected_shape"] = [64, 64]
        self.settings["extra_metadata"] = {"review_notes": ["preserve this"]}
        yy, xx = np.indices((64, 64))
        self.row = {"image_id": "synthetic", "replicate_id": "gel1", "ebfp": ""}
        images = {
            "green": 100 * np.exp(-((yy - 24) ** 2 + (xx - 20) ** 2) / 18)
                     + 100 * np.exp(-((yy - 45) ** 2 + (xx - 44) ** 2) / 18),
            "red": 20 * np.exp(-((yy - 24) ** 2 + (xx - 20) ** 2) / 18)
                   + 100 * np.exp(-((yy - 24) ** 2 + (xx - 46) ** 2) / 18),
        }
        for channel, pixels in images.items():
            path = self.folder / f"{channel}.tif"
            Image.fromarray(pixels.astype(np.uint8)).save(path)
            self.row[channel] = str(path)
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if not dialog._closing:
                dialog.reject()
            dialog.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()

    def dialog(self, rows=None, **kwargs):
        dialog = SegmentationDialog(rows or [self.row], self.settings, **kwargs)
        self.dialogs.append(dialog)
        return dialog

    def wait_until(self, predicate, timeout_ms=15000):
        if predicate():
            return True
        loop = QEventLoop()
        poll, timeout = QTimer(), QTimer()
        poll.setInterval(10)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        poll.start()
        timeout.start(timeout_ms)
        loop.exec()
        poll.stop()
        timeout.stop()
        return predicate()

    def await_preview(self, dialog):
        completed = self.wait_until(lambda: dialog.worker is None and dialog._is_current())
        self.assertTrue(completed, dialog.status.text())
        self.assertTrue(dialog.apply_button.isEnabled())

    def test_real_worker_updates_masks_and_counts_and_retains_source_configuration(self):
        original_config, original_row = deepcopy(self.settings), deepcopy(self.row)
        source_files = {key: Path(self.row[key]).read_bytes() for key in ("green", "red")}
        dialog = self.dialog()
        ticks = []
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: ticks.append(1))
        heartbeat.start()
        self.await_preview(dialog)
        heartbeat.stop()
        self.assertGreater(len(ticks), 0, "The GUI must remain responsive during segmentation")
        first = dialog.preview
        server = dialog._server
        self.assertEqual(first["counts"]["red_detections"], 2)
        self.assertEqual(first["counts"]["double_positive"], 1)
        self.assertEqual(first["raw_red"].shape, (64, 64))
        dialog.auto.setChecked(False)
        dialog.inspect_pixel(20, 24, 1)
        self.assertIn("CURRENT", dialog.pixel_info.text())
        dialog.inputs["high"].setValue(50)
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertFalse(dialog._is_current())
        self.assertNotIn("Total 3", dialog.counts.text())
        self.assertNotIn("CURRENT", dialog.pixel_info.text())
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertFalse(dialog._closing)
        dialog.refresh_preview()
        self.await_preview(dialog)
        self.assertEqual(dialog.preview["counts"]["red_detections"], 1)
        self.assertEqual(dialog.preview["counts"]["double_positive"], 0)
        self.assertEqual(dialog.preview["counts"]["live_only"], 2)
        self.assertFalse(np.array_equal(first["labels_red"], dialog.preview["labels_red"]))
        np.testing.assert_array_equal(first["labels_green"], dialog.preview["labels_green"])
        self.assertEqual(self.settings, original_config)
        self.assertEqual(self.row, original_row)
        for channel in ("green", "red"):
            self.assertEqual(Path(self.row[channel]).read_bytes(), source_files[channel])
        returned = dialog.result_config()
        self.assertEqual(returned["segmentation"]["red"]["high"], 50)
        self.assertEqual(returned["input"]["pixel_size_um"], original_config["input"]["pixel_size_um"])
        returned["extra_metadata"]["review_notes"].append("new note")
        self.assertEqual(self.settings, original_config)
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_display_controls_channel_switch_and_zoom_do_not_restart_segmentation(self):
        dialog = self.dialog()
        self.await_preview(dialog)
        first = dialog.preview
        server = dialog._server
        before_config = dialog.result_config()
        requests = list(Path(dialog._temporary.name).glob("request_*.json"))
        generation = dialog._generation
        arrays = {name: first[name].copy() for name in ("raw_red", "labels_red", "labels_green")}
        dialog.black.setValue(1)
        dialog.white.setValue(80)
        dialog.show_contrast.setChecked(True)
        for mode in ("filled", "labels", "none", "outlines"):
            dialog.overlay_mode.setCurrentIndex(dialog.overlay_mode.findData(mode))
        dialog.show_ids.setChecked(True)
        ids = [item for item in dialog.overlay_view.scene().items()
               if isinstance(item, QGraphicsSimpleTextItem)]
        self.assertEqual(len(ids), first["counts"]["red_detections"])
        dialog.overlay_view.zoom(1.3)
        self.assertAlmostEqual(dialog.overlay_view.transform().m11(), dialog.raw_view.transform().m11())
        dialog.overlay_view.reset_full_size()
        self.assertAlmostEqual(dialog.raw_view.transform().m11(), 1.0)
        dialog.channel_choice.setCurrentIndex(dialog.channel_choice.findData("green"))
        dialog.auto_display()
        dialog.full_display()
        dialog.fit_views()
        self.app.processEvents()
        self.assertIs(dialog.preview, first)
        self.assertEqual(dialog._generation, generation)
        self.assertEqual(dialog.result_config(), before_config)
        self.assertEqual(list(Path(dialog._temporary.name).glob("request_*.json")), requests)
        self.assertIsNone(dialog.worker)
        self.assertIs(dialog._server, server)
        self.assertEqual(server.state(), QProcess.ProcessState.Running)
        self.assertFalse(dialog.debounce.isActive())
        for name, expected in arrays.items():
            np.testing.assert_array_equal(first[name], expected)

    def test_invalid_thresholds_hide_stale_counts_and_cannot_apply_or_launch_worker(self):
        dialog = self.dialog()
        self.await_preview(dialog)
        dialog.auto.setChecked(False)
        dialog.inputs["low"].setValue(100)
        requests = list(Path(dialog._temporary.name).glob("request_*.json"))
        dialog.refresh_preview()
        self.assertIsNone(dialog.worker)
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn("Invalid segmentation settings for red", dialog.status.text())
        self.assertNotIn("Total 3", dialog.counts.text())
        with self.assertRaisesRegex(ValueError, "red"):
            dialog.result_config()
        self.assertEqual(list(Path(dialog._temporary.name).glob("request_*.json")), requests)
        dialog.accept()
        self.assertFalse(dialog._closing)

    def test_manual_updates_coalesce_and_reuse_worker_when_auto_update_is_off(self):
        dialog = self.dialog()
        dialog.auto.setChecked(False)
        self.assertTrue(self.wait_until(lambda: dialog.worker is not None
                                       and dialog.worker.state() == QProcess.ProcessState.Running))
        old_worker = dialog.worker
        old_request = deepcopy(dialog.worker_request)
        dialog.inputs["high"].setValue(50)
        dialog.refresh_preview()
        dialog.inputs["high"].setValue(60)
        dialog.refresh_preview()
        self.assertIs(dialog._server, old_worker)
        self.await_preview(dialog)
        self.assertIs(dialog._server, old_worker)
        self.assertEqual(old_worker.state(), QProcess.ProcessState.Running)
        self.assertEqual(old_request["config"]["segmentation"]["red"]["high"], 4)
        self.assertEqual(dialog.preview["config"]["segmentation"]["red"]["high"], 60)
        self.assertEqual(dialog.preview["counts"]["red_detections"], 1)
        self.assertEqual(list(Path(dialog._temporary.name).iterdir()), [],
                         "Completed preview payloads must be removed after loading arrays")

    def test_idle_worker_is_reused_for_edits_and_stopped_on_close(self):
        dialog = self.dialog()
        self.await_preview(dialog)
        server = dialog._server
        pid = server.processId()
        dialog.inputs["high"].setValue(50)
        dialog.refresh_preview()
        self.await_preview(dialog)
        self.assertIs(dialog._server, server)
        self.assertEqual(server.processId(), pid)
        temporary = Path(dialog._temporary.name)
        dialog.reject()
        self.assertIsNone(dialog._server)
        self.assertIsNone(dialog.worker)
        self.assertFalse(temporary.exists())

    def test_failed_image_can_be_fixed_and_retried_in_same_worker(self):
        original = Path(self.row["red"]).read_bytes()
        Image.fromarray(np.zeros((32, 32), np.uint8)).save(self.row["red"])
        dialog = self.dialog()
        dialog.auto.setChecked(False)
        self.assertTrue(self.wait_until(lambda: dialog.worker is None
                                       and "Preview failed:" in dialog.status.text()))
        server = dialog._server
        Path(self.row["red"]).write_bytes(original)
        dialog.refresh_preview()
        self.await_preview(dialog)
        self.assertIs(dialog._server, server)
        self.assertEqual(dialog.preview["counts"]["red_detections"], 2)

    def test_interrupted_worker_can_restart_without_accepting_stale_data(self):
        dialog = self.dialog()
        dialog.auto.setChecked(False)
        self.assertTrue(self.wait_until(lambda: dialog.worker is not None
                                       and dialog.worker.state() == QProcess.ProcessState.Running))
        dialog.worker.kill()
        self.assertTrue(self.wait_until(lambda: dialog._server is None))
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn("Preview worker stopped", dialog.status.text())
        dialog.inputs["high"].setValue(50)
        dialog.refresh_preview()
        self.await_preview(dialog)
        self.assertEqual(dialog.preview["config"]["segmentation"]["red"]["high"], 50)
        self.assertEqual(dialog.preview["counts"]["red_detections"], 1)

    def test_close_kills_running_worker_and_cleans_private_preview_directory(self):
        dialog = self.dialog()
        dialog.auto.setChecked(False)
        self.assertTrue(self.wait_until(lambda: dialog.worker is not None
                                       and dialog.worker.state() == QProcess.ProcessState.Running))
        temporary = Path(dialog._temporary.name)
        self.assertTrue(temporary.is_dir())
        dialog.reject()
        self.assertTrue(dialog._closing)
        self.assertIsNone(dialog.worker)
        self.assertFalse(dialog.debounce.isActive())
        self.assertFalse(temporary.exists())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_worker_failure_surfaces_original_tiff_validation_error(self):
        # The preview worker, rather than the widget, validates scalar TIFF data.
        Image.fromarray(np.zeros((32, 32), np.uint8)).save(self.row["red"])
        dialog = self.dialog()
        dialog.auto.setChecked(False)
        self.assertTrue(self.wait_until(lambda: dialog.worker is None
                                       and "Preview failed:" in dialog.status.text()))
        self.assertIn("shape (32, 32) differs", dialog.status.text())
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIsNone(dialog.preview)

    def test_switching_fields_clears_old_images_and_measurements_before_new_worker_finishes(self):
        blank = {"image_id": "blank", "replicate_id": "gel2", "ebfp": ""}
        for channel in ("green", "red"):
            path = self.folder / f"blank_{channel}.tif"
            Image.fromarray(np.zeros((64, 64), np.uint8)).save(path)
            blank[channel] = str(path)
        dialog = self.dialog(rows=[self.row, blank])
        self.await_preview(dialog)
        dialog.auto.setChecked(False)
        dialog.inspect_pixel(20, 24, 1)
        self.assertIn("Object", dialog.pixel_info.text())
        dialog.field_choice.setCurrentIndex(1)
        self.assertIsNone(dialog.preview)
        self.assertEqual(dialog.raw_view.scene().items(), [])
        self.assertEqual(dialog.overlay_view.scene().items(), [])
        self.assertNotIn("Object 1", dialog.pixel_info.text())
        self.assertNotIn("Total 3", dialog.counts.text())
        self.assertEqual(dialog.baseline.text(), "")
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.refresh_preview()
        self.await_preview(dialog)
        self.assertEqual(dialog.preview["image_id"], "blank")
        self.assertEqual(dialog.preview["counts"]["total"], 0)
        self.assertIsNone(dialog.preview["counts"]["viability_percent"])


if __name__ == "__main__":
    unittest.main()
