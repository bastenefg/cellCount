"""Reviewed reports retain exact saved image panels without source acquisitions."""
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from desktop.stack_smoke import synthetic_stack
from desktop.volume_analysis import analyze_volume
from desktop.viability_3d import summarize_objects
from desktop import viability_figure as figures


class ViabilityFigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_temp = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.fixture_temp.name)
        info, settings = synthetic_stack(cls.fixture / "source")
        cls.result = analyze_volume(info, settings, cls.fixture / "analysis")
        cls.summary = json.loads(Path(cls.result["summary"]).read_text())
        cls.metrics = cls.summary["viability"]
        # Offline collaborators can still render a reviewed report.
        for path in info["paths"].values():
            Path(path).unlink()
        cls.original_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in Path(cls.result["output_dir"]).iterdir() if p.is_file()}

    @classmethod
    def tearDownClass(cls):
        cls.fixture_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "reviewed.png"

    def copied_result(self):
        target = self.root / "original"
        shutil.copytree(self.result["output_dir"], target)
        return {key: str(target / Path(value).name) if key != "output_dir" else str(target)
                for key, value in self.result.items()}

    def rewrite_summary(self, result, summary):
        path = Path(result["summary"])
        path.write_text(json.dumps(summary), encoding="utf-8")
        check_path = Path(result["checksums"])
        checks = json.loads(check_path.read_text())
        checks[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        check_path.write_text(json.dumps(checks), encoding="utf-8")

    def test_source_summary_reports_known_synthetic_range(self):
        self.assertEqual(self.metrics["live_min"], 2)
        self.assertEqual(self.metrics["live_max"], 3)
        self.assertEqual(self.metrics["viability_min_pct"], 40)
        self.assertEqual(self.metrics["viability_max_pct"], 50)
        self.assertIsNone(self.metrics["viability_pct"])
        self.assertEqual(self.metrics["pending_groups"], 1)

    def test_exact_image_panels_and_threshold_labels_survive_without_sources(self):
        path = figures.prepare_viability_figure(self.result, self.metrics, self.output)
        with Image.open(self.result["figure"]) as image:
            source = np.asarray(image.convert("RGB").crop(tuple(figures.LEGACY_LAYOUT["panel_strip"])))
        with Image.open(path) as image:
            output = np.asarray(image.convert("RGB"))
        x, y = figures.PANEL_DESTINATION
        np.testing.assert_array_equal(output[y:y + source.shape[0], x:x + source.shape[1]], source)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in Path(self.result["output_dir"]).iterdir() if p.is_file()}
        self.assertEqual(self.original_hashes, after)
        sidecar = json.loads(path.with_suffix(".png.json").read_text())
        self.assertEqual(sidecar["metrics"], self.metrics)
        self.assertEqual(sidecar["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_legacy_figure_does_not_require_new_summary_fields(self):
        result = self.copied_result()
        summary = dict(self.summary)
        summary.pop("figure_layout")
        summary.pop("viability")
        self.rewrite_summary(result, summary)
        figures.prepare_viability_figure(result, self.metrics, self.output)
        self.assertTrue(self.output.is_file())

    def test_valid_cache_avoids_render_and_corrupt_cache_is_repaired(self):
        figures.prepare_viability_figure(self.result, self.metrics, self.output)
        before = self.output.read_bytes()
        with patch.object(figures, "_render", side_effect=AssertionError("Unexpected rerender")):
            figures.prepare_viability_figure(self.result, self.metrics, self.output)
        self.output.write_bytes(b"damaged cached PNG")
        figures.prepare_viability_figure(self.result, self.metrics, self.output)
        self.assertEqual(self.output.read_bytes(), before)
        self.output.with_suffix(".png.json").write_text("[]", encoding="utf-8")
        figures.prepare_viability_figure(self.result, self.metrics, self.output)
        self.assertEqual(self.output.read_bytes(), before)

    def test_review_revision_replaces_stale_cached_metrics(self):
        figures.prepare_viability_figure(self.result, self.metrics, self.output)
        before = self.output.read_bytes()
        with Path(self.result["objects"]).open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        dual = next(row for row in rows if row["status"] == "dual_positive_candidate")
        resolved = summarize_objects(rows, {dual["object_id"]: {"decision": "same_cell"}})
        figures.prepare_viability_figure(self.result, resolved, self.output, "review-1")
        self.assertNotEqual(self.output.read_bytes(), before)
        sidecar = json.loads(self.output.with_suffix(".png.json").read_text())
        self.assertEqual(sidecar["metrics"]["viability_pct"], 40)
        self.assertEqual(sidecar["revision_id"], "review-1")

    def test_saved_source_tampering_rejected_even_with_valid_cache(self):
        result = self.copied_result()
        figures.prepare_viability_figure(result, self.metrics, self.output)
        Path(result["figure"]).write_bytes(b"changed original PNG")
        with self.assertRaisesRegex(ValueError, "integrity"):
            figures.prepare_viability_figure(result, self.metrics, self.output)

    def test_metrics_from_another_field_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "do not belong"):
            figures.prepare_viability_figure(self.result, summarize_objects([]), self.output)

    def test_completed_run_and_unknown_layout_cannot_be_overwritten_or_cropped(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            figures.prepare_viability_figure(self.result, self.metrics, self.result["figure"])
        result = self.copied_result()
        summary = dict(self.summary, figure_layout={"kind": "unknown"})
        self.rewrite_summary(result, summary)
        with self.assertRaisesRegex(ValueError, "layout"):
            figures.prepare_viability_figure(result, self.metrics, self.output)

    def test_empty_and_reviewed_captions_do_not_invent_percentages(self):
        empty = summarize_objects([])
        self.assertIn("not measurable", figures.viability_caption(empty)[0])
        green = summarize_objects([{"object_id": 1, "status": "green_only", "green_ids": "1", "red_ids": ""}])
        self.assertEqual(figures.viability_caption(green)[0], "Apparent viability: 100.0%")


if __name__ == "__main__":
    unittest.main()
