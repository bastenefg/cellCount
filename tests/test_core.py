"""Correctness checks for future data, separate from the archived-data regression.

These synthetic arrays are test fixtures only; they are not experimental data.
Run: python -m unittest discover -s tests -v
"""
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd
from pipeline import core

ROOT = Path(__file__).resolve().parents[1]


def config():
    return json.loads((ROOT / "configs" / "reference_48h.json").read_text())


def channel_table(labels, channel):
    rows = []
    for object_id in sorted(set(np.unique(labels)) - {0}):
        y, x = np.where(labels == object_id)
        rows.append({"channel": channel, "id": int(object_id),
                     "y": float(y.mean()), "x": float(x.mean()),
                     "area": len(y), "peak_contrast": 50.0,
                     "mean_raw": 60.0, "max_raw": 60.0, "parent_count": 1})
    columns = ["channel", "id", "y", "x", "area", "peak_contrast", "mean_raw", "max_raw", "parent_count"]
    return pd.DataFrame(rows, columns=columns)


def disk(shape, y, x, radius):
    yy, xx = np.indices(shape)
    return (yy - y) ** 2 + (xx - x) ** 2 <= radius ** 2


class CoreCorrectnessTests(unittest.TestCase):
    def test_constant_images_have_no_cells_and_empty_union_is_valid(self):
        image = np.full((83, 121), 17.0)
        original = image.copy()
        green, g, _ = core.segment(image, "green", config())
        red, r, _ = core.segment(image, "red", config())
        labels, objects = core.combine(g, r, green, red, config())
        self.assertEqual(labels.shape, image.shape)
        self.assertEqual(len(objects), 0)
        self.assertEqual(int(labels.max()), 0)
        self.assertIn("status", objects.columns)
        np.testing.assert_array_equal(image, original)

    def test_border_exclusion_uses_actual_non_square_image_dimensions(self):
        yy, xx = np.indices((91, 137))
        image = (100 * np.exp(-((yy - 45) ** 2 + (xx - 55) ** 2) / 18)
                 + 100 * np.exp(-((yy - 45) ** 2 + (xx - 136) ** 2) / 18))
        labels, objects, _ = core.segment(image, "green", config())
        self.assertEqual(len(objects), 1)
        self.assertAlmostEqual(float(objects.iloc[0].x), 55.0, delta=1.0)
        self.assertFalse(np.any(labels[:, -1]))

    def test_dead_only_field_does_not_require_any_green_objects(self):
        green = np.zeros((96, 113), dtype=np.int32)
        red = np.zeros_like(green)
        red[disk(red.shape, 45, 50, 3)] = 1
        labels, objects = core.combine(channel_table(green, "green"),
                                       channel_table(red, "red"), green, red, config())
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects.iloc[0].status, "dead_only")
        self.assertEqual(int(objects.iloc[0].green_id), 0)
        self.assertEqual(int(objects.iloc[0].red_id), 1)
        np.testing.assert_array_equal(labels > 0, red > 0)

    def test_one_red_object_cannot_be_matched_to_two_green_objects(self):
        green = np.zeros((100, 100), dtype=np.int32)
        red = np.zeros_like(green)
        green[disk(green.shape, 45, 40, 3)] = 1
        green[disk(green.shape, 45, 47, 3)] = 2
        red[disk(red.shape, 45, 43, 3)] = 1
        _, objects = core.combine(channel_table(green, "green"),
                                  channel_table(red, "red"), green, red, config())
        self.assertEqual(len(objects), 2)
        self.assertEqual(objects.status.value_counts().to_dict(),
                         {"double_positive": 1, "live_only": 1})
        matched = objects[objects.status == "double_positive"].iloc[0]
        self.assertEqual(int(matched.green_id), 1)
        self.assertEqual(int(matched.red_id), 1)
        self.assertEqual(int((objects.red_id > 0).sum()), 1)
        self.assertEqual(float((objects.status == "live_only").mean()), 0.5)

    def test_close_centroids_alone_do_not_merge_separate_cells(self):
        green = np.zeros((80, 80), dtype=np.int32)
        red = np.zeros_like(green)
        green[25:28, 25:28] = 1
        red[25:28, 31:34] = 1
        _, objects = core.combine(channel_table(green, "green"),
                                  channel_table(red, "red"), green, red, config())
        self.assertEqual(len(objects), 2)
        self.assertEqual(set(objects.status), {"live_only", "dead_only"})

    def test_bh_known_values_and_input_order(self):
        p = np.array([0.2, 0.001, 0.04, 0.01])
        expected = np.array([0.2, 0.004, 0.04 * 4 / 3, 0.02])
        np.testing.assert_allclose(core.bh(p), expected, rtol=0, atol=1e-14)
        self.assertEqual(core.bh(np.array([])).size, 0)

    def test_one_unscorable_object_does_not_invalidate_all_bh_results(self):
        q = core.bh(np.array([0.001, np.nan, 0.1]))
        self.assertAlmostEqual(q[0], 0.002)
        self.assertTrue(np.isnan(q[1]))
        self.assertAlmostEqual(q[2], 0.1)

    def test_no_cell_free_background_is_unscorable_not_ebfp_negative(self):
        labels = np.ones((40, 50), dtype=np.int32)
        objects = pd.DataFrame([{"object_id": 1, "status": "live_only"}])
        measured = core.measure_ebfp(np.full(labels.shape, 23.0), labels,
                                     objects, "synthetic", config()).iloc[0]
        self.assertEqual(measured.ebfp_shift_locations, 0)
        self.assertFalse(measured.ebfp_scorable)
        self.assertTrue(np.isnan(measured.ebfp_p))
        self.assertEqual(measured.ebfp_status, "insufficient_null_locations")

    def test_flat_blue_signal_is_not_significant_cell_enrichment(self):
        shape = (140, 160)
        labels = np.zeros(shape, dtype=np.int32)
        labels[disk(shape, 70, 80, 3)] = 1
        objects = pd.DataFrame([{"object_id": 1, "y": 70., "x": 80.,
                                 "green_id": 1, "red_id": 0, "status": "live_only",
                                 "area": int((labels > 0).sum()), "green_peak": 50., "red_peak": 0.}])
        blue = np.full(shape, 23.0)
        original = blue.copy()
        measured = core.measure_ebfp(blue, labels, objects, "synthetic", config())
        self.assertEqual(len(measured), 1)
        self.assertGreater(measured.iloc[0].ebfp_shift_locations, 100)
        self.assertEqual(measured.iloc[0].ebfp_p, 1.0)
        np.testing.assert_array_equal(blue, original)

    def test_blue_detection_is_invariant_to_uniform_intensity_offset(self):
        shape = (140, 160)
        labels = np.zeros(shape, dtype=np.int32)
        labels[disk(shape, 70, 80, 3)] = 1
        objects = pd.DataFrame([{"object_id": 1, "y": 70., "x": 80.,
                                 "green_id": 1, "red_id": 0, "status": "live_only",
                                 "area": int((labels > 0).sum()), "green_peak": 50., "red_peak": 0.}])
        blue = np.zeros(shape)
        blue[labels == 1] = 15.0
        first = core.measure_ebfp(blue, labels, objects, "synthetic", config()).iloc[0]
        second = core.measure_ebfp(blue + 20.0, labels, objects, "synthetic", config()).iloc[0]
        self.assertAlmostEqual(first.ebfp_p, second.ebfp_p, places=12)
        self.assertAlmostEqual(second.ebfp_mean - first.ebfp_mean, 20.0, places=12)
        self.assertLess(first.ebfp_p, 0.001)


if __name__ == "__main__":
    unittest.main()
