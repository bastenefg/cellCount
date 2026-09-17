"""Synthetic optical-section GUI regressions; no private microscopy inputs."""
from copy import deepcopy
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QFileDialog
except ImportError:
    QApplication = None
else:
    from desktop.stack_dialog import StackReviewDialog


@unittest.skipIf(QApplication is None, "Requires desktop dependencies")
class StackDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shape = (9, 32, 40)
        self.green = np.zeros(shape, dtype=np.uint16)
        self.red = np.zeros(shape, dtype=np.uint16)
        self.green[2, 12:19, 16:23] = 800
        self.red[6, 12:19, 16:23] = 1600
        self.info = {"paths": {}, "shape_zyx": list(shape), "spacing_um": [2, .5, .5],
            "z_positions_um": list(range(0, 18, 2)), "z_indices": list(range(3, 12)),
            "display": {"green": [0, 4095], "red": [0, 4095]}, "field_id": "synthetic_field",
            "cache_key": "synthetic_cache", "source": {"path": "synthetic.lof"}, "selection": {"mode": "max"}}
        for role in ("green", "red"):
            path = self.root / (role + ".npy")
            np.save(path, getattr(self, role))
            self.info["paths"][role] = str(path)
        self.dialog = StackReviewDialog(config={}, output_base=self.root, cache_dir=self.root / "cache")
        self.dialog.fields.blockSignals(True)
        self.dialog.fields.addItem("synthetic_field")
        self.dialog.fields.blockSignals(False)
        self.dialog.accept_stack_info(self.info)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_slice_changes_reuse_memmaps_and_profiles(self):
        self.assertTrue(all(isinstance(a, np.memmap) for a in self.dialog.arrays.values()))
        self.dialog._pick_xy(19.5 / 40, 15.5 / 32)
        profiles = self.dialog.profile.profiles
        with patch("numpy.load", side_effect=AssertionError("Slice changes must not reopen data")):
            self.dialog.z_slider.setValue(2)
            green = self.dialog.views["green"].pixmap.toImage()
            self.dialog.z_slider.setValue(6)
            red = self.dialog.views["red"].pixmap.toImage()
        self.assertIs(self.dialog.profile.profiles, profiles)
        self.assertEqual(np.argmax(profiles["green"]), 2)
        self.assertEqual(np.argmax(profiles["red"]), 6)
        self.assertGreater(green.pixelColor(19, 15).green(), 0)
        self.assertGreater(red.pixelColor(19, 15).red(), 0)
        self.assertEqual(self.dialog.analysis_z.value(), 6)

    def test_side_views_use_physical_spacing(self):
        self.assertAlmostEqual(self.dialog.views["xz"].aspect, 40 * .5 / (9 * 2))
        self.assertAlmostEqual(self.dialog.views["yz"].aspect, 32 * .5 / (9 * 2))
        self.dialog._pick_xz(.5, .75)
        self.assertEqual(self.dialog.z_slider.value(), 6)
        self.assertIn("Plane 10", self.dialog.z_text.text())
        self.assertIn("12.00 µm", self.dialog.z_text.text())

    def test_uncalibrated_or_single_plane_stack_still_reviewable(self):
        info = deepcopy(self.info)
        info["spacing_um"][0] = None
        info["z_positions_um"] = None
        self.dialog.accept_stack_info(info)
        self.assertTrue(self.dialog.z_slider.isEnabled())
        self.assertFalse(self.dialog.analyze_button.isEnabled())
        self.assertIn("Z spacing unavailable", self.dialog.context.text())
        for role in ("green", "red"):
            path = self.root / (role + "_one.npy")
            np.save(path, getattr(self, role)[:1])
            info["paths"][role] = str(path)
        info["shape_zyx"][0] = 1
        info["spacing_um"][0] = 2
        info["z_indices"] = [0]
        self.dialog.accept_stack_info(info)
        self.assertEqual(self.dialog.z_slider.maximum(), 0)
        self.assertTrue(self.dialog.export_review.isEnabled())
        self.assertFalse(self.dialog.analyze_button.isEnabled())

    def test_threshold_controls_use_detector_bit_depth_and_never_launch_worker(self):
        control = self.dialog.thresholds["green", "low"]
        self.assertEqual(control.number.maximum(), 4095)
        profiles = self.dialog.profile.profiles
        with patch.object(self.dialog, "start_analysis", side_effect=AssertionError("Explicit run required")):
            control.number.setValue(600)
            self.dialog.z_slider.setValue(2)
        self.assertTrue(self.dialog.preview_mask.isChecked())
        self.assertIsNone(self.dialog.process)
        self.assertIs(self.dialog.profile.profiles, profiles)
        self.assertEqual(self.dialog.analysis_settings()["green"]["low"], 600)

    def test_detected_object_selection_and_filter(self):
        self.dialog.detections = [
            {"image_id": "synthetic_field", "object_id": "1", "x": "7", "y": "9", "status": "live_only"},
            {"image_id": "synthetic_field", "object_id": "2", "x": "20", "y": "16", "status": "double_positive"},
            {"image_id": "other_field", "object_id": "3", "x": "2", "y": "3", "status": "double_positive"}]
        self.dialog._populate_objects()
        self.assertEqual(self.dialog.object_choice.count(), 3)
        self.dialog.only_double.setChecked(True)
        self.assertEqual(self.dialog.object_choice.count(), 2)
        self.dialog.object_choice.setCurrentIndex(1)
        self.assertEqual((self.dialog.x, self.dialog.y), (20, 16))
        self.assertEqual(self.dialog._review_target, "2d_2")

    def test_review_export_preserves_coordinates_and_provenance(self):
        self.dialog._pick_xy(.5, .5)
        self.dialog.z_slider.setValue(6)
        self.dialog.decision.setCurrentText("Separate in Z")
        self.dialog.note.setText("Distinct green and red depths in synthetic fixture")
        destination = self.root / "review.json"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
            self.dialog.save_review()
        saved = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(saved["origin_mode"], "imported_inputs")
        self.assertEqual(saved["reviews"][0]["decision"], "Separate in Z")
        self.assertEqual(saved["reviews"][0]["z_index_in_selection"], 6)
        self.assertEqual(saved["reviews"][0]["stack"]["source"], self.info["source"])
        with next(self.root.glob("review_*.csv")).open(encoding="utf-8", newline="") as stream:
            self.assertEqual(next(csv.DictReader(stream))["target"], "location_20_16")
        np.testing.assert_array_equal(np.load(self.info["paths"]["green"]), self.green)

    def test_exports_cannot_modify_completed_runs(self):
        original = self.root / "original_run"
        original.mkdir()
        (original / "run_manifest.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "completed analysis"):
            self.dialog._safe_export(original / "review.json")
        with self.assertRaisesRegex(ValueError, "cached files"):
            self.dialog._safe_export(self.dialog.cache / "review.json")

    def test_missing_imported_projection_warning_is_visible(self):
        info = deepcopy(self.info)
        info["projection_verification"] = {"green": {"state": "unavailable", "message": "Imported TIFF missing; source stack is available."}}
        self.dialog.accept_stack_info(info)
        self.assertIn("Imported TIFF missing", self.dialog.status.text())

    def test_old_stack_result_cannot_replace_newer_field_request(self):
        self.dialog._pending_record = {"pending": "newer"}
        self.dialog._loaded(None, {"invalid": "stale"})
        self.assertEqual(self.dialog.stack_info["cache_key"], "synthetic_cache")

    def test_changed_3d_settings_hide_stale_labels_but_preserve_saved_result(self):
        labels = np.zeros(self.green.shape, dtype=np.uint32)
        labels[2, 12:19, 16:23] = 1
        self.dialog.result = {}
        for role in ("green", "red"):
            path = self.root / ("labels_" + role + ".npy")
            np.save(path, labels)
            self.dialog.result["labels_" + role] = str(path)
        self.dialog._result_settings = deepcopy(self.dialog.analysis_settings())
        self.dialog._load_labels()
        original = deepcopy(self.dialog.result)
        self.dialog.volumes["green"].setValue(self.dialog.volumes["green"].value() + 1)
        self.assertFalse(self.dialog.labels)
        self.assertEqual(self.dialog.result, original)
        self.assertIn("Settings changed", self.dialog.analysis_summary.text())

    def test_zoom_crop_click_coordinates_stay_in_original_image_space(self):
        info = deepcopy(self.info)
        info["shape_zyx"] = [9, 128, 160]
        for role in ("green", "red"):
            path = self.root / (role + "_large.npy")
            np.save(path, np.zeros(info["shape_zyx"], dtype=np.uint16))
            info["paths"][role] = str(path)
        self.dialog.accept_stack_info(info)
        self.dialog._pick_xy(.5, .5)
        self.dialog.zoom.setCurrentIndex(3)
        self.assertEqual(self.dialog._view_bounds, (48, 32, 64, 64))
        self.dialog._pick_visible_xy(.25, .25)
        self.assertEqual((self.dialog.x, self.dialog.y), (64, 48))
        self.assertEqual(self.dialog.views["green"].pixmap.width(), 64)

    def test_stop_requests_cooperative_cleanup_before_forced_kill(self):
        process = Mock()
        self.dialog.process = process
        self.dialog._active_job = self.root / "worker.json"
        self.dialog._stop()
        self.assertTrue((self.root / "worker_cancel").is_file())
        process.kill.assert_not_called()
        self.dialog.process = None
        scratch = self.root / ".volume-pending-output-unique123"
        scratch.mkdir()
        (scratch / "partial.npy").write_bytes(b"partial")
        unrelated = self.root / ".volume-pending-other-unique123"
        unrelated.mkdir()
        self.dialog._cleanup_cancelled(self.root / "output")
        self.assertFalse(scratch.exists())
        self.assertTrue(unrelated.exists())

    def test_close_waits_for_reader_then_releases_memory_maps(self):
        worker = Mock()
        worker.isInterruptionRequested.return_value = True
        self.dialog.worker = worker
        self.dialog.reject()
        worker.requestInterruption.assert_called_once()
        self.assertTrue(self.dialog.arrays)
        self.dialog._load_finished(worker)
        self.assertFalse(self.dialog.arrays)
        self.assertIsNone(self.dialog.worker)

    def test_analysis_completion_marks_settings_changed_during_worker(self):
        settings = deepcopy(self.dialog.analysis_settings())
        output = self.root / "volume"
        output.mkdir()
        result = {"objects": str(output / "objects.csv"), "summary": str(output / "summary.json"),
                  "figure": str(output / "figure.png")}
        (output / "objects.csv").write_text("object_id,status,z,y,x\n1,green_only,2,15,19\n")
        (output / "summary.json").write_text(json.dumps({"counts": {"green_only": 1, "candidate_count_min": 1, "candidate_count_max": 1}}))
        for role in ("green", "red"):
            path = output / ("labels_" + role + ".npy")
            np.save(path, np.zeros(self.green.shape, dtype=np.uint32))
            result["labels_" + role] = str(path)
        (output / "result.json").write_text(json.dumps(result))
        self.dialog.volumes["green"].setValue(settings["green"]["min_volume_um3"] + 1)
        process = Mock()
        process.readAllStandardOutput.return_value = b""
        self.dialog.process = process
        self.dialog._analysis_finished(process, self.root / "job.json", output, settings, self.info, 0)
        self.assertFalse(self.dialog.labels)
        self.assertIn("Settings changed", self.dialog.analysis_summary.text())
        self.dialog.volumes["green"].setValue(settings["green"]["min_volume_um3"])
        self.assertTrue(self.dialog.labels)
        self.assertIn("Candidate count range: 1–1", self.dialog.analysis_summary.text())

    def test_export_cannot_replace_source_import_provenance(self):
        protected = self.root / "import_provenance.json"
        protected.write_text("original")
        self.dialog.stack_info["import_record"] = {"provenance_path": str(protected)}
        with self.assertRaisesRegex(ValueError, "original source image"):
            self.dialog._safe_export(protected)
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(self.root / "invalid.tif"), "")):
            self.dialog.save_review()
        self.assertIn(".json filename", self.dialog.status.text())
        self.assertEqual(protected.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
