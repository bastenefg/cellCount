"""Missing EBFP acquisition is distinct from a measured, all-zero blue image.

The small TIFFs and arrays in this module are synthetic correctness fixtures,
not experimental data. These tests do not tune any analysis thresholds.
"""
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd
from PIL import Image

from pipeline import cli, core, io


ROOT = Path(__file__).resolve().parents[1]


def fixture_config():
    config = json.loads((ROOT / "configs" / "reference_48h.json").read_text())
    config["input"]["expected_shape"] = [64, 64]
    return config


def one_object():
    labels = np.zeros((64, 64), dtype=np.int32)
    yy, xx = np.indices(labels.shape)
    labels[(yy - 32) ** 2 + (xx - 32) ** 2 <= 3 ** 2] = 1
    pixels = int((labels > 0).sum())
    objects = pd.DataFrame([{
        "object_id": 1, "y": 32., "x": 32., "green_id": 1,
        "red_id": 0, "status": "live_only", "area": pixels,
        "green_peak": 50., "red_peak": 0., "mask_pixels_final": pixels,
        "mask_pixels_overwritten": 0, "mask_lost": False,
    }])
    return labels, objects


class OptionalEbfpManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.config = fixture_config()
        for sample in ("a", "b"):
            for channel in ("green", "red", "ebfp"):
                Image.fromarray(np.zeros((64, 64), dtype=np.uint8)).save(
                    self.directory / f"{sample}_{channel}.tif")

    def manifest(self, rows, fields=None):
        path = self.directory / "samples.csv"
        if fields is None:
            fields = ["image_id", "replicate_id", "green", "red", "ebfp"]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    @staticmethod
    def row(sample="a", ebfp=""):
        return {"image_id": sample, "replicate_id": "gel1",
                "green": f"{sample}_green.tif", "red": f"{sample}_red.tif",
                "ebfp": ebfp}

    def test_manifest_can_omit_entire_ebfp_column(self):
        row = self.row()
        row.pop("ebfp")
        path = self.manifest([row], list(row))
        entries, files = io.load_manifest(path, self.config)
        self.assertIsNone(entries[0]["ebfp"])
        self.assertEqual({item["channel"] for item in files}, {"green", "red"})
        self.assertEqual(len(files), 2)

    def test_manifest_accepts_blank_ebfp_cell(self):
        entries, files = io.load_manifest(self.manifest([self.row()]), self.config)
        self.assertIsNone(entries[0]["ebfp"])
        self.assertEqual(len(files), 2)

    def test_explicit_missing_ebfp_path_raises(self):
        path = self.manifest([self.row(ebfp="does_not_exist.tif")])
        with self.assertRaises(FileNotFoundError):
            io.load_manifest(path, self.config)

    def test_live_and_dead_files_remain_required(self):
        for channel in ("green", "red"):
            with self.subTest(channel=channel):
                row = self.row()
                row[channel] = ""
                with self.assertRaises(ValueError):
                    io.load_manifest(self.manifest([row]), self.config)

    def test_live_and_dead_columns_remain_required(self):
        for channel in ("green", "red"):
            with self.subTest(channel=channel):
                row = self.row()
                row.pop(channel)
                with self.assertRaises(ValueError):
                    io.load_manifest(self.manifest([row], list(row)), self.config)

    def test_mixed_manifest_preserves_channel_availability(self):
        rows = [self.row("a", "a_ebfp.tif"), self.row("b")]
        entries, files = io.load_manifest(self.manifest(rows), self.config)
        self.assertEqual(entries[0]["ebfp"], self.directory / "a_ebfp.tif")
        self.assertIsNone(entries[1]["ebfp"])
        self.assertEqual(len(files), 5)
        self.assertEqual([(item["image_id"], item["channel"]) for item in files
                          if item["channel"] == "ebfp"], [("a", "ebfp")])


class OptionalEbfpMeasurementTests(unittest.TestCase):
    def test_missing_channel_preserves_cells_but_has_no_ebfp_measurements(self):
        labels, objects = one_object()
        expected_objects = objects.copy(deep=True)
        measured = cli.measure_optional_ebfp(
            None, labels, objects, "missing", fixture_config())
        self.assertEqual(len(measured), len(objects))
        pd.testing.assert_frame_equal(measured[objects.columns], expected_objects)
        pd.testing.assert_frame_equal(objects, expected_objects)
        self.assertFalse(bool(measured.iloc[0].ebfp_channel_provided))
        self.assertFalse(bool(measured.iloc[0].ebfp_scorable))
        self.assertEqual(measured.iloc[0].ebfp_status, "channel_not_provided")
        for column in core.EBFP_COLUMNS:
            if column not in ("sample", "ebfp_scorable", "ebfp_status"):
                with self.subTest(column=column):
                    self.assertTrue(pd.isna(measured.iloc[0][column]))
        classified = cli.classify(measured, fixture_config())
        self.assertTrue(pd.isna(classified.iloc[0].ebfp_detected))
        self.assertTrue(pd.isna(classified.iloc[0].ebfp_q_pooled))
        self.assertTrue(pd.isna(classified.iloc[0].ebfp_q_sample))

    def test_supplied_zero_blue_is_measured_negative_not_missing(self):
        labels, objects = one_object()
        blue = np.zeros(labels.shape)
        before = blue.copy()
        measured = cli.measure_optional_ebfp(
            blue, labels, objects, "zero", fixture_config())
        classified = cli.classify(measured, fixture_config())
        row = classified.iloc[0]
        self.assertTrue(bool(row.ebfp_channel_provided))
        self.assertTrue(bool(row.ebfp_scorable))
        self.assertEqual(row.ebfp_status, "scorable")
        self.assertEqual(row.ebfp_mean, 0.)
        self.assertEqual(row.ebfp_p, 1.)
        self.assertEqual(row.ebfp_q_pooled, 1.)
        self.assertFalse(bool(row.ebfp_detected))
        np.testing.assert_array_equal(blue, before)

    def test_supplied_channel_matches_unchanged_core_algorithm(self):
        labels, objects = one_object()
        blue = np.zeros(labels.shape)
        blue[labels > 0] = 20.
        expected = core.measure_ebfp(
            blue, labels, objects, "positive", fixture_config())
        measured = cli.measure_optional_ebfp(
            blue, labels, objects, "positive", fixture_config())
        pd.testing.assert_frame_equal(measured[expected.columns], expected)
        self.assertTrue(measured.ebfp_channel_provided.all())

    def test_missing_channel_objects_do_not_change_measured_q_values(self):
        labels, objects = one_object()
        blue = np.zeros(labels.shape)
        blue[labels > 0] = 20.
        provided = cli.measure_optional_ebfp(
            blue, labels, objects, "provided", fixture_config())
        zero = cli.measure_optional_ebfp(
            np.zeros(labels.shape), labels, objects, "zero", fixture_config())
        missing = cli.measure_optional_ebfp(
            None, labels, objects, "missing", fixture_config())
        baseline = cli.classify(pd.concat([provided, zero], ignore_index=True),
                                fixture_config())
        combined = cli.classify(
            pd.concat([provided, zero, missing], ignore_index=True), fixture_config())
        for column in ("ebfp_q_pooled", "ebfp_q_sample", "ebfp_detected"):
            pd.testing.assert_series_equal(baseline[column], combined.loc[:1, column])
        self.assertTrue(pd.isna(combined.iloc[2].ebfp_detected))
        self.assertTrue(pd.isna(combined.iloc[2].ebfp_q_pooled))


if __name__ == "__main__":
    unittest.main()
