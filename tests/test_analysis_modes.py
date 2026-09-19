"""The familiar preview and Run controls route both counting dimensions."""
from copy import deepcopy
import json
import os
import csv
import time
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QFileDialog

from desktop import analysis_3d, services, volume_analysis
from desktop.app import MainWindow
from desktop.stack_dialog import StackReviewDialog
from tests import test_analysis_3d as fixtures


class AnalysisModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = fixtures.SharedAnalysisRunTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.window = MainWindow()
        self.window.summary_cache = self.root / "summary_cache"
        self.window.volume_review_cache = self.root / "review_cache"
        self.window.output_base.setText(str(self.root))
        self.window.rows = deepcopy(self.fixture.rows)
        self.window.config = deepcopy(self.fixture.config)
        self.window.input_modes.setCurrentIndex(1)
        self.window.refresh_fields()
        self.error = patch.object(self.window, "error")
        self.error.start()
        self.addCleanup(self.error.stop)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def completed_run(self, name="volume_run"):
        out, args, _ = self.fixture.prepare(name)
        request = json.loads(Path(args[1]).read_text())
        with patch.object(volume_analysis, "_figure", side_effect=lambda *a: Image.new("RGB", (1500, 900), "green").save(a[-1])):
            analysis_3d.analyze_batch(request)
        return out

    def wait_for_figures(self):
        deadline = time.monotonic() + 15
        while self.window.volume_workers and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.app.processEvents()
        self.assertFalse(self.window.volume_workers)
        self.assertTrue(self.window.figure_button.isEnabled(), self.window.result_notes.toPlainText())

    def test_dimension_choice_is_shared_between_quick_and_batch(self):
        self.assertEqual(self.window.analysis_mode.currentData(), "2d")
        self.window.analysis_mode.setCurrentIndex(1)
        self.window.input_modes.setCurrentIndex(0)
        self.assertEqual(self.window.analysis_mode.currentData(), "3d")
        self.assertFalse(self.window.extended.isEnabled())
        self.assertIn("Preview segmentation", self.window.mode_description.text())
        self.assertEqual(self.window.stack_input_button.text(), "Inspect Z stack…")

    def test_normal_preview_settings_are_passed_unchanged_to_3d_run(self):
        reviewed = services.config_with_imports(self.fixture.rows, self.fixture.config)
        reviewed["segmentation"]["red"].update(low=3.125, high=7.625)
        dialog = Mock()
        dialog.exec.return_value = True
        dialog.result_config.return_value = deepcopy(reviewed)
        with patch("desktop.segmentation_dialog.SegmentationDialog", return_value=dialog):
            self.window.preview_segmentation()
        self.assertEqual(self.window.config, reviewed)
        self.window.analysis_mode.setCurrentIndex(1)
        prepared = (self.root / "out", ["analyze-3d", str(self.root / "request.json")], self.root / "run.log")
        with patch.object(analysis_3d, "prepare_analysis_3d", return_value=prepared) as prepare, patch.object(self.window, "start_job") as start:
            self.window.run_analysis()
        self.assertEqual(prepare.call_args.args[3]["segmentation"], reviewed["segmentation"])
        self.assertEqual(prepare.call_args.args[3]["matching"], reviewed["matching"])
        self.assertEqual(len(prepare.call_args.args[2]), 2)
        self.assertEqual(start.call_args.args[0], "analyze3d")

    def test_two_dimensional_run_keeps_original_dispatch(self):
        prepared = (self.root / "out", ["analyze"], self.root / "run.log")
        with patch("desktop.app.prepare_analysis", return_value=prepared) as prepare, patch.object(self.window, "start_job") as start:
            self.window.run_analysis()
        self.assertEqual(start.call_args.args[0], "analyze")
        self.assertEqual(prepare.call_args.args[3]["segmentation"], self.window.config["segmentation"])

    def test_3d_run_opens_in_normal_results_and_exports_selected_field(self):
        out = self.completed_run()
        self.window.load_result(out)
        self.assertEqual(self.window.result["mode"], "3d")
        self.assertEqual(self.window.pages.currentIndex(), 1)
        self.assertIn("viability", self.window.metric_titles[0].text().lower())
        self.assertEqual(self.window.preview_choice.count(), 2)
        self.assertEqual(self.window.result_table.columnCount(), 8)
        self.wait_for_figures()
        self.window.preview_choice.setCurrentIndex(1)
        selected = self.window._selected_volume_field()
        self.assertEqual(selected["image_id"], "field_1")
        destination = self.root / "exported.png"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "PNG image (*.png)")):
            self.window.save_summary_figure()
        self.assertEqual(destination.read_bytes(), Path(self.window.summary_paths["png"]).read_bytes())
        self.assertNotEqual(destination.read_bytes(), Path(selected["result"]["figure"]).read_bytes())
        csv_copy = self.root / "exported.csv"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(csv_copy), "")):
            self.window.save_summary()
        with csv_copy.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        self.assertIn("viability_min_pct", rows[0])
        self.window.load_result(out)
        self.assertEqual(self.window.result["mode"], "3d")
        with patch.object(self.window, "start_job") as start:
            self.window.verify_results()
        self.assertEqual(start.call_args.args[1], ["verify-3d", str(out)])

    def test_shared_3d_inspection_has_no_independent_threshold_or_run_controls(self):
        from desktop.stack_source import prepare_stack
        config = services.config_with_imports(self.fixture.rows, self.fixture.config)
        info = prepare_stack(config["leica_imports"][0], self.root / "inspect_cache")
        settings = {"mode": "projection_config", "config": deepcopy(config)}
        display_config = deepcopy(config)
        display_config["leica_imports"] = []
        dialog = StackReviewDialog(config=display_config, cache_dir=self.root / "ui_cache")
        info["_ui_defaults"] = settings
        dialog.accept_stack_info(info)
        self.assertFalse(dialog.tabs.isTabVisible(1))
        self.assertTrue(dialog.analyze_button.isHidden())
        self.assertFalse(dialog.preview_mask.isEnabled())
        self.assertEqual(dialog.analysis_settings(), settings)
        self.assertIn("3.25", dialog.shared_settings_caption.text())
        self.assertTrue(dialog.z_slider.isEnabled())
        with patch.object(QFileDialog, "getExistingDirectory", side_effect=AssertionError("No separate run flow")):
            dialog.start_analysis()
        self.assertIn("New analysis", dialog.status.text())
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()

    def test_3d_exports_cannot_change_root_or_field_outputs(self):
        out = self.completed_run()
        self.window.load_result(out)
        for path in (out / "copy.csv", out / "fields" / "field_0" / "copy.png"):
            with self.assertRaisesRegex(ValueError, "analysis run"):
                self.window.check_export_path(path, path.suffix)

    def test_3d_figure_modified_after_loading_cannot_be_exported_as_saved_result(self):
        self.window.load_result(self.completed_run())
        self.wait_for_figures()
        Path(self.window.summary_paths["png"]).write_bytes(b"changed image")
        destination = self.root / "modified.png"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "PNG image (*.png)")), patch.object(self.window, "error") as error:
            self.window.save_summary_figure()
        self.assertFalse(destination.exists())
        self.assertIn("changed after analysis", str(error.call_args.args[0]))

    def test_review_is_saved_restored_and_exported_without_changing_original_run(self):
        import hashlib
        out = self.completed_run()
        before = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob("*") if p.is_file()}
        self.window.load_result(out)
        self.wait_for_figures()
        field = self.window._selected_volume_field()
        with Path(field["result"]["objects"]).open(newline="", encoding="utf-8") as stream:
            objects = list(csv.DictReader(stream))
        decisions = {row["object_id"]: {"decision": "separate", "note": "Synthetic review", "pairs": []}
                     for row in objects if row["green_ids"] and row["red_ids"]}
        with patch.object(volume_analysis, "analyze_volume", side_effect=AssertionError("Review must not resegment")):
            self.assertTrue(self.window.apply_volume_review(field["image_id"], decisions))
        self.wait_for_figures()
        report = deepcopy(self.window.result["viability_report"])
        self.assertEqual(report["overall"]["reviewed_groups"], 1)
        self.assertEqual(report["overall"]["pending_groups"], 1)
        self.assertEqual(report["overall"]["viability_min_pct"], 100 / 3)
        self.assertEqual(report["overall"]["viability_max_pct"], 50)
        exported = self.root / "portable_review.json"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(exported), "")):
            self.window.export_volume_review()
        self.assertTrue(exported.is_file())
        self.window.load_result(out)
        self.assertEqual(self.window.result["review_decisions"][field["image_id"]], decisions)
        self.assertEqual(self.window.result["viability_report"], report)
        self.assertEqual(before, {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob("*") if p.is_file()})

    def test_portable_review_export_stays_bound_to_run_at_click(self):
        from desktop.viability_3d import load_review_document
        original = self.completed_run("first")
        other = self.completed_run("second")
        self.window.load_result(original)
        snapshot = self.window.result
        destination = self.root / "first_review.json"
        def switch_run(*args):
            self.window.load_result(other)
            return str(destination), ""
        with patch.object(QFileDialog, "getSaveFileName", side_effect=switch_run):
            self.window.export_volume_review()
        document = load_review_document(snapshot, destination)
        self.assertEqual(document["decisions"], {})
        self.assertEqual(self.window.result["path"], other)
        self.assertNotIn("review_document", self.window.result)

    def test_cancelled_batch_cleanup_runs_only_after_worker_completion(self):
        process = Mock()
        self.window.process = process
        self.window.job_kind = "analyze3d"
        self.window.job_out = self.root / "cancelled_batch"
        self.window.cancelled = True
        with patch.object(self.window, "poll_log"), patch.object(analysis_3d, "finish_cancelled_batch") as cleanup:
            self.window.job_finished(130, QProcess.ExitStatus.NormalExit)
        cleanup.assert_called_once_with(self.window.job_out)
        self.assertIsNone(self.window.process)
        self.assertIn("Run stopped", self.window.status_text.text())


if __name__ == "__main__":
    unittest.main()
