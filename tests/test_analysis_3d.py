"""The normal Run workflow keeps projection settings and all selected fields."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from desktop import analysis_3d, leica, services, volume_analysis
from tests.test_leica import FakeContainer, FakeImage


class SharedAnalysisRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "stack.lif"
        self.source.write_bytes(b"immutable microscope acquisition")
        self.reader = patch.object(leica.liffile, "LifFile", side_effect=lambda *a, **kw: FakeContainer(FakeImage()))
        self.reader.start()
        self.addCleanup(self.reader.stop)
        self.config = services.default_config()
        self.config["segmentation"]["green"].update(low=3.25, high=9.75, min_area_px=2)
        self.config["segmentation"]["red"].update(low=1.5, high=4.75, min_area_px=2)
        self.config["segmentation"]["exclude_border"] = False
        self.rows = []
        for index in (0, 1):
            request = {"source": str(self.source), "source_identity": leica.inspect_leica(self.source)["source"],
                       "series_index": 0, "channels": {"green": 0, "red": 1, "ebfp": None},
                       "mode": "max", "z_start": 0, "z_stop": 3, "time_index": index,
                       "pixel_size_um": [0.7, 0.8]}
            bundle = leica.import_selection(request, self.root / "imports")
            self.rows.append(dict(bundle["row"], image_id=f"field_{index}", replicate_id="sample_1"))

    def prepare(self, name="run"):
        return analysis_3d.prepare_analysis_3d(self.root, name, self.rows, self.config, self.root / "cache")

    def run_batch(self, name="run"):
        out, args, log = self.prepare(name)
        request = json.loads(Path(args[1]).read_text())
        with patch.object(volume_analysis, "_figure", side_effect=lambda *a: a[-1].write_bytes(b"synthetic figure")):
            analysis_3d.analyze_batch(request)
        return out, request

    def test_prepare_uses_accepted_settings_without_creating_results(self):
        before = deepcopy(self.config)
        out, args, log = self.prepare()
        self.assertFalse(out.exists())
        self.assertEqual(self.config, before)
        self.assertEqual(args[0], "analyze-3d")
        request = json.loads(Path(args[1]).read_text())
        self.assertEqual(request["config"]["segmentation"], before["segmentation"])
        self.assertEqual(request["config"]["matching"], before["matching"])
        self.assertEqual(len(request["rows"]), 2)
        _, command = services.process_command(args, log)
        self.assertIn("--stack-analysis", command)
        self.assertNotIn("--pipeline", command)

    def test_both_fields_share_exact_settings_and_aggregate_once(self):
        out, request = self.run_batch()
        saved = analysis_3d.read_analysis_3d(out, verify=True)
        self.assertEqual(saved["config"], request["config"])
        self.assertEqual(len(saved["fields"]), 2)
        self.assertEqual(saved["summary"]["n_replicates"], 1)
        self.assertEqual(saved["replicate"][0]["n_fields"], "2")
        for field in saved["fields"]:
            settings = json.loads(Path(field["result"]["settings"]).read_text())
            self.assertEqual(settings, {"mode": "projection_config", "config": saved["config"]})
        for key in analysis_3d.COUNT_KEYS:
            self.assertEqual(saved["summary"]["counts"][key], sum(int(row[key]) for row in saved["image"]))
        self.assertEqual(self.source.read_bytes(), b"immutable microscope acquisition")

    def test_relocated_results_reopen_and_tampering_is_detected(self):
        out, _ = self.run_batch()
        copied = self.root / "shared_copy"
        shutil.copytree(out, copied)
        result = analysis_3d.read_analysis_3d(copied, verify=True)
        self.assertTrue(all(field["path"].is_relative_to(copied) for field in result["fields"]))
        (copied / "effective_config.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "changed"):
            analysis_3d.read_analysis_3d(copied, verify=True)

    def test_tiff_only_field_cannot_silently_fall_back_to_2d(self):
        plain = self.root / "plain"
        plain.mkdir()
        row = dict(self.rows[0])
        for role in ("green", "red"):
            destination = plain / f"{role}.tif"
            shutil.copy2(row[role], destination)
            row[role] = str(destination)
        with self.assertRaisesRegex(ValueError, "3D analysis needs"):
            analysis_3d.prepare_analysis_3d(self.root, "invalid", [row], self.config)
        self.assertFalse((self.root / "invalid").exists())

    def test_cancelled_and_failed_runs_are_not_completed_results(self):
        out, args, _ = self.prepare()
        request = json.loads(Path(args[1]).read_text())
        with patch("desktop.stack_source.prepare_stack", side_effect=leica.LeicaImportCancelled("Stopped")):
            with self.assertRaises(volume_analysis.VolumeAnalysisCancelled):
                analysis_3d.analyze_batch(request)
        self.assertEqual(json.loads((out / "volume_run.json").read_text())["status"], "cancelled")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            analysis_3d.read_analysis_3d(out)
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_completed_analysis_cannot_be_used_as_parent_folder(self):
        out, _ = self.run_batch()
        with self.assertRaisesRegex(ValueError, "outside existing"):
            analysis_3d.prepare_analysis_3d(out, "nested", self.rows, self.config)

    def test_field_path_cannot_escape_saved_run(self):
        out, _ = self.run_batch()
        path = out / "volume_run.json"
        metadata = json.loads(path.read_text())
        metadata["fields"][0]["relative_path"] = "../outside"
        path.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "field path"):
            analysis_3d.read_analysis_3d(out)

    def test_cancel_cleanup_removes_only_owned_unpublished_scratch(self):
        out, args, _ = self.prepare()
        request = json.loads(Path(args[1]).read_text())
        with patch("desktop.stack_source.prepare_stack", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                analysis_3d.analyze_batch(request)
        fields = out / "fields"
        fields.mkdir()
        scratch = fields / ".volume-pending-field_0-abc"
        scratch.mkdir()
        (scratch / "temporary.npy").write_bytes(b"scratch")
        completed = fields / "field_1"
        completed.mkdir()
        (completed / "preserve.txt").write_text("published field")
        unrelated = fields / ".volume-pending-another-abc"
        unrelated.mkdir()
        analysis_3d.finish_cancelled_batch(out)
        self.assertFalse(scratch.exists())
        self.assertTrue(completed.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(json.loads((out / "volume_run.json").read_text())["status"], "cancelled")

    def test_late_cancel_does_not_rewrite_completed_run(self):
        out, _ = self.run_batch()
        before = (out / "volume_run.json").read_bytes()
        analysis_3d.finish_cancelled_batch(out)
        self.assertEqual((out / "volume_run.json").read_bytes(), before)
        analysis_3d.read_analysis_3d(out, verify=True)


if __name__ == "__main__":
    unittest.main()
