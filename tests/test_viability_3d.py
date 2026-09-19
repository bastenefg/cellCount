"""Viability uses reviewed identities, pooled counts and immutable revisions."""
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from desktop import viability_3d as viability


def obj(identifier, green="", red="", status=None):
    if status is None:
        status = "dual_positive_candidate" if green and red else "green_only" if green else "red_only"
    return {"object_id": str(identifier), "green_ids": green, "red_ids": red, "status": status}


def write_objects(path, objects):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("object_id", "green_ids", "red_ids", "status"))
        writer.writeheader()
        writer.writerows(objects)


class ViabilityArithmeticTests(unittest.TestCase):
    def test_pending_dual_is_a_range_not_an_automatic_dead_or_live_call(self):
        rows = [obj(1, "1"), obj(2, red="1"), obj(3, "2", "2")]
        result = viability.summarize_objects(rows)
        self.assertEqual((result["live_min"], result["live_max"]), (1, 2))
        self.assertEqual((result["total_min"], result["total_max"]), (3, 4))
        self.assertAlmostEqual(result["viability_min_pct"], 100 / 3)
        self.assertEqual(result["viability_max_pct"], 50)
        self.assertIsNone(result["viability_pct"])
        self.assertEqual(result["pending_groups"], 1)

    def test_same_cell_is_one_nonviable_not_two_cells(self):
        result = viability.summarize_objects([obj(1, "1", "1")], {"1": {"decision": "same_cell"}})
        self.assertEqual(result["viability_pct"], 0)
        self.assertEqual(result["total_min"], 1)
        self.assertEqual(result["dead_count"], 1)
        self.assertEqual(result["reviewed_groups"], 1)

    def test_separate_is_one_live_and_one_dead(self):
        result = viability.summarize_objects([obj(1, "1", "1")], {"1": {"decision": "separate"}})
        self.assertEqual(result["viability_pct"], 50)
        self.assertEqual(result["total_min"], 2)
        self.assertEqual(result["live_min"], 1)
        self.assertEqual(result["pending_groups"], 0)

    def test_partial_and_uncertain_reviews_remain_provisional(self):
        rows = [obj(1, "1", "1"), obj(2, "2", "2"), obj(3, "3", "3")]
        result = viability.summarize_objects(rows, {
            "1": {"decision": "same_cell"}, "2": {"decision": "uncertain"},
        })
        self.assertEqual(result["pending_groups"], 2)
        self.assertEqual(result["reviewed_groups"], 1)
        self.assertEqual(result["uncertain_groups"], 1)
        self.assertEqual(result["unreviewed_groups"], 1)
        self.assertEqual(result["paired_min"], 1)
        self.assertEqual(result["paired_max"], 3)
        self.assertEqual(result["viability_min_pct"], 0)
        self.assertEqual(result["viability_max_pct"], 40)
        self.assertIsNone(result["viability_pct"])

    def test_complex_group_requires_explicit_unique_pairs(self):
        rows = [obj(1, "1", "1;2", "unresolved")]
        pending = viability.summarize_objects(rows)
        self.assertEqual((pending["total_min"], pending["total_max"]), (2, 3))
        self.assertEqual(pending["viability_min_pct"], 0)
        self.assertAlmostEqual(pending["viability_max_pct"], 100 / 3)
        with self.assertRaisesRegex(ValueError, "specify pairs"):
            viability.summarize_objects(rows, {"1": {"decision": "same_cell"}})
        reviewed = viability.summarize_objects(rows, {"1": {"decision": "pairs", "pairs": [[1, 2]]}})
        self.assertEqual(reviewed["dead_count"], 2)
        self.assertEqual(reviewed["total_min"], 2)
        self.assertEqual(reviewed["viability_pct"], 0)

    def test_partial_membership_list_explicitly_leaves_other_objects_separate(self):
        rows = [obj(1, "1;2", "1;2;3", "unresolved")]
        metrics = viability.summarize_objects(rows, {"1": {"decision": "pairs", "pairs": [[2, 3]]}})
        self.assertEqual(metrics["pending_groups"], 0)
        self.assertEqual(metrics["live_min"], 1)
        self.assertEqual(metrics["dead_count"], 3)
        self.assertEqual(metrics["viability_pct"], 25)

    def test_empty_and_single_channel_results_have_valid_denominators(self):
        empty = viability.summarize_objects([])
        self.assertIsNone(empty["viability_pct"])
        self.assertIsNone(empty["viability_min_pct"])
        self.assertEqual(viability.summarize_objects([obj(1, "1")])["viability_pct"], 100)
        self.assertEqual(viability.summarize_objects([obj(1, red="1")])["viability_pct"], 0)

    def test_invalid_pair_membership_or_reuse_is_rejected(self):
        rows = [obj(1, "1;2", "1;2", "unresolved")]
        for pairs in ([[[1, 1], [1, 2]]], [[[1, 1], [2, 1]]], [[[3, 1]]], [[[1]]]):
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                viability.summarize_objects(rows, {"1": {"decision": "pairs", "pairs": pairs[0]}})
        with self.assertRaisesRegex(ValueError, "only valid"):
            viability.summarize_objects(rows, {"1": {"decision": "separate", "pairs": [[1, 1]]}})

    def test_duplicate_channel_ids_and_candidate_ids_are_rejected(self):
        for rows in ([obj(1, "1"), obj(1, red="1")],
                     [obj(1, "1"), obj(2, "1")],
                     [obj(1, "1;1", "1", "unresolved")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                viability.summarize_objects(rows)
        with self.assertRaisesRegex(ValueError, "Duplicate candidate review"):
            viability.summarize_objects([obj(1, "1", "1")], {1: {}, "1": {}})

    def test_unknown_decisions_identifiers_and_inconsistent_rows_are_rejected(self):
        rows = [obj(1, "1", "1")]
        for decisions in ({"2": {"decision": "separate"}}, {"1": {"decision": "alive"}},
                          {"1": {"decision": []}}, {"1": {"note": 42}}, {"0": {}}):
            with self.subTest(decisions=decisions), self.assertRaises(ValueError):
                viability.summarize_objects(rows, decisions)
        with self.assertRaisesRegex(ValueError, "disagrees"):
            viability.summarize_objects([obj(1, "1", "1", "green_only")])

    def test_pooling_uses_counts_not_average_percentages(self):
        small = viability.summarize_objects([obj(1, "1")])
        large = viability.summarize_objects([obj(i, red=str(i)) for i in range(1, 10)])
        pooled = viability.aggregate_metrics([small, large])
        self.assertEqual(pooled["viability_pct"], 10)

    def test_report_groups_fields_without_mutating_decisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fields = []
            for name, replicate, rows in (
                    ("f1", "r1", [obj(1, "1", "1")]),
                    ("f2", "r1", [obj(1, "1")]),
                    ("f3", "r2", [obj(1, red="1")])):
                path = root / (name + ".csv")
                write_objects(path, rows)
                fields.append({"image_id": name, "replicate_id": replicate, "result": {"objects": path}})
            decisions = {"f1": {"1": {"decision": "same_cell", "note": "Reviewed in Z."}}}
            before = deepcopy(decisions)
            report = viability.build_report({"fields": fields}, decisions)
            self.assertEqual(decisions, before)
            self.assertAlmostEqual(report["overall"]["viability_pct"], 100 / 3)
            self.assertEqual(report["replicate"][0]["viability_pct"], 50)
            self.assertEqual(report["replicate"][0]["n_fields"], 2)
            self.assertEqual(report["image"][0]["image_id"], "f1")
            with self.assertRaisesRegex(ValueError, "field absent"):
                viability.build_report({"fields": fields}, {"missing": {}})
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                viability.build_report({"fields": fields + fields[:1]})


class ReviewPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from desktop import analysis_3d, volume_analysis
        from desktop.stack_smoke import synthetic_stack
        cls.fixture = tempfile.TemporaryDirectory()
        root = Path(cls.fixture.name)
        info, settings = synthetic_stack(root / "inputs")
        cls.template = root / "run"
        field = cls.template / "fields" / "synthetic"
        with patch.object(volume_analysis, "_figure", side_effect=lambda *a: a[-1].write_bytes(b"synthetic figure")):
            volume_analysis.analyze_volume(info, settings, field)
        summary = json.loads((field / "summary.json").read_text())
        counts = summary["counts"]
        metadata = {"schema_version": 1, "analysis_mode": "3d", "status": "completed",
                    "fields": [{"image_id": "synthetic", "replicate_id": "rep1", "relative_path": "fields/synthetic"}]}
        for name, value in (("volume_run.json", metadata), ("effective_config.json", {}),
                            ("summary.json", {"counts": counts})):
            (cls.template / name).write_text(json.dumps(value), encoding="utf-8")
        (cls.template / "resolved_samples.csv").write_text("image_id,replicate_id\nsynthetic,rep1\n", encoding="utf-8")
        for kind in ("image", "replicate"):
            (cls.template / (kind + "_summary.csv")).write_text("image_id,replicate_id\nsynthetic,rep1\n", encoding="utf-8")
        names = ("volume_run.json", "effective_config.json", "summary.json", "resolved_samples.csv",
                 "image_summary.csv", "replicate_summary.csv", "fields/synthetic/output_checksums.json")
        checks = {name: hashlib.sha256((cls.template / name).read_bytes()).hexdigest() for name in names}
        (cls.template / "checksums_3d.json").write_text(json.dumps(checks), encoding="utf-8")
        cls.checksums = {p.relative_to(cls.template).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in cls.template.rglob("*") if p.is_file()}

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        from desktop.analysis_3d import read_analysis_3d
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        shutil.copytree(self.template, self.run)
        self.result = read_analysis_3d(self.run)
        self.cache = self.root / "reviews"
        with Path(self.result["fields"][0]["result"]["objects"]).open(encoding="utf-8", newline="") as stream:
            self.mixed = next(row["object_id"] for row in csv.DictReader(stream) if row["status"] == "dual_positive_candidate")
        self.decisions = {"synthetic": {self.mixed: {"decision": "same_cell", "note": "Synthetic fixture."}}}

    def test_immutable_revisions_restore_latest_and_preserve_source_bytes(self):
        self.assertIsNone(viability.load_review(self.result, self.cache))
        first = viability.save_review(self.result, self.decisions, self.cache)
        first_bytes = Path(first["path"]).read_bytes()
        self.decisions["synthetic"][self.mixed]["decision"] = "separate"
        second = viability.save_review(self.result, self.decisions, self.cache)
        self.assertNotEqual(first["revision_id"], second["revision_id"])
        self.assertEqual(Path(first["path"]).read_bytes(), first_bytes)
        self.assertEqual(viability.load_review(self.result, self.cache), second)
        for name, digest in self.checksums.items():
            self.assertEqual(hashlib.sha256((self.run / name).read_bytes()).hexdigest(), digest)

    def test_portable_review_works_with_a_relocated_identical_analysis(self):
        from desktop.analysis_3d import read_analysis_3d
        document = viability.save_review(self.result, self.decisions, self.cache)
        relocated = self.root / "relocated"
        shutil.copytree(self.run, relocated)
        portable = self.root / "portable.json"
        shutil.copyfile(document["path"], portable)
        loaded = viability.load_review_document(read_analysis_3d(relocated), portable)
        self.assertEqual(loaded["source_fingerprint"], document["source_fingerprint"])
        self.assertEqual(loaded["report"], document["report"])
        self.assertNotIn(str(self.run), portable.read_text(encoding="utf-8"))

    def test_portable_report_is_recomputed_and_invalid_edits_are_rejected(self):
        document = viability.save_review(self.result, self.decisions, self.cache)
        path = self.root / "edited_review.json"
        document["report"]["overall"]["viability_pct"] = 1234
        path.write_text(json.dumps(document), encoding="utf-8")
        self.assertNotEqual(viability.load_review_document(self.result, path)["report"]["overall"]["viability_pct"], 1234)
        document["decisions"]["synthetic"]["9999"] = {"decision": "same_cell"}
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "candidate absent"):
            viability.load_review_document(self.result, path)

    def test_wrong_source_fingerprint_and_changed_original_are_rejected(self):
        document = viability.save_review(self.result, self.decisions, self.cache)
        path = self.root / "wrong_review.json"
        document["source_fingerprint"] = "a" * 64
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different saved"):
            viability.load_review_document(self.result, path)
        (self.run / "effective_config.json").write_text('{"changed": true}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            viability.save_review(self.result, self.decisions, self.cache)

    def test_changed_cached_revision_and_pointer_path_escape_are_rejected(self):
        document = viability.save_review(self.result, self.decisions, self.cache)
        path = Path(document["path"])
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "has changed"):
            viability.load_review(self.result, self.cache)
        (path.parent / "latest.json").write_text(json.dumps({"schema_version": 1, "revision_id": "../outside"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "revision identifier"):
            viability.load_review(self.result, self.cache)

    def test_storage_inside_original_analysis_is_refused(self):
        with self.assertRaisesRegex(ValueError, "outside the original"):
            viability.save_review(self.result, self.decisions, self.run / "reviews")
        self.assertFalse((self.run / "reviews").exists())

    def test_atomic_pointer_failure_preserves_last_selected_review(self):
        first = viability.save_review(self.result, self.decisions, self.cache)
        with patch.object(viability.os, "replace", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                viability.save_review(self.result, {"synthetic": {}}, self.cache)
        self.assertEqual(viability.load_review(self.result, self.cache)["revision_id"], first["revision_id"])
        self.assertFalse(list(Path(first["path"]).parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
