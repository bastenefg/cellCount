"""Boundary checks for the desktop adapter, without altering the scientific method."""
import copy
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from desktop import services


class DesktopAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.green = self.root / "live image.tif"
        self.red = self.root / "dead image.tif"
        self.green.touch()
        self.red.touch()
        self.row = {"image_id": "gel1_field1", "replicate_id": "gel1", "green": str(self.green), "red": str(self.red), "ebfp": ""}

    def test_import_resolves_against_csv_and_keeps_missing_channel(self):
        path = self.root / "samples.csv"
        path.write_text("image_id,replicate_id,green,red\ngel1_field1,gel1,live image.tif,dead image.tif\n", encoding="utf-8-sig")
        rows = services.read_manifest(path)
        self.assertEqual(rows, [self.row])

    def test_refuses_duplicate_fields_and_channel_reuse(self):
        with self.assertRaisesRegex(ValueError, "Duplicate field"):
            services.validate_rows([self.row, self.row])
        second = dict(self.row, image_id="field2")
        with self.assertRaisesRegex(ValueError, "assigned more than once"):
            services.validate_rows([self.row, second])

    def test_explicit_replicate_required(self):
        with self.assertRaisesRegex(ValueError, "replicate_id"):
            services.validate_rows([dict(self.row, replicate_id="")])

    def test_preparation_preserves_config_and_reserves_no_output_folder(self):
        config = services.default_config()
        before = copy.deepcopy(config)
        out, args, log = services.prepare_analysis(self.root, "new_run", [self.row], config)
        self.assertEqual(config, before)
        self.assertFalse(out.exists())
        config_path = Path(args[args.index("--config") + 1])
        self.assertEqual(json.loads(config_path.read_text()), before)
        self.assertEqual(log.parent, self.root)
        self.assertFalse(config_path.is_relative_to(out))

    def test_existing_run_and_unsafe_output_names_rejected(self):
        (self.root / "saved").mkdir()
        for name in ("saved", "../escape", "CON", "trailing.", ""):
            with self.subTest(name=name), self.assertRaises((ValueError, FileExistsError)):
                services.output_path(self.root, name)

    def test_failed_run_cannot_be_presented_as_results(self):
        (self.root / "run_manifest.json").write_text('{"status":"failed"}')
        with self.assertRaisesRegex(ValueError, "incomplete"):
            services.read_results(self.root)

    def test_missing_metrics_are_not_reported_as_zero(self):
        self.assertEqual(services.metric({"ebfp_live_percent": {"mean": None}}, "ebfp_live_percent")[0], "Not measured")
        self.assertEqual(services.number(""), "Not measured")
        self.assertEqual(services.metric({"viability_percent": {"mean": 0, "sample_sd": None}}, "viability_percent")[0], "0.0%")


if __name__ == "__main__":
    unittest.main()
