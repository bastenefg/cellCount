"""3D counting must preserve depth, physical units and association uncertainty."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from desktop import volume_analysis as volume


def sphere(shape, center, radius, spacing=(1., 1., 1.)):
    coordinates = np.ogrid[tuple(slice(0, value) for value in shape)]
    return sum(((axis - value) * scale) ** 2 for axis, value, scale in zip(coordinates, center, spacing)) <= radius ** 2


class VolumeAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.shape = (28, 40, 40)
        self.settings = {"green": {"low": 20., "high": 80., "sigma_um": 0., "min_volume_um3": 10.},
                         "red": {"low": 20., "high": 80., "sigma_um": 0., "min_volume_um3": 10.},
                         "min_seed_distance_um": 5., "match_distance_um": 0., "exclude_border": False}

    def tearDown(self):
        self.temp.cleanup()

    def stack(self, green=None, red=None, spacing=(1., 1., 1.)):
        paths = {}
        for role, array in (("green", green), ("red", red)):
            if array is None:
                array = np.zeros(self.shape, np.uint8)
            path = self.cache / (role + ".npy")
            np.save(path, np.asarray(array, dtype=np.uint8))
            paths[role] = str(path)
        return {"paths": paths, "shape_zyx": list(self.shape), "spacing_um": list(spacing),
                "selection": {"z_start": 4, "z_stop": 4 + self.shape[0], "time_index": 2}}

    def signal(self, *regions):
        array = np.zeros(self.shape, np.uint8)
        for region in regions:
            array[region] = 150
        return array

    def analyze(self, stack, settings=None, real_figure=False):
        output = self.root / ("result_" + str(len(list(self.root.glob("result_*")))))
        def quick_figure(*args):
            args[-1].write_bytes(b"test figure")
        with patch.object(volume, "_figure", wraps=volume._figure if real_figure else quick_figure):
            result = volume.analyze_volume(stack, settings or self.settings, output)
        summary = json.loads(Path(result["summary"]).read_text())
        with Path(result["objects"]).open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        return result, summary, rows

    def test_same_xy_separated_z_produces_two_objects_not_dual_signal(self):
        green = self.signal(sphere(self.shape, (7, 20, 20), 3))
        red = self.signal(sphere(self.shape, (20, 20, 20), 3))
        result, summary, rows = self.analyze(self.stack(green, red), real_figure=True)
        self.assertEqual(summary["counts"]["green_only"], 1)
        self.assertEqual(summary["counts"]["red_only"], 1)
        self.assertEqual(summary["counts"]["dual_positive_candidate"], 0)
        self.assertEqual(summary["counts"]["candidate_count_min"], 2)
        self.assertTrue(Path(result["figure"]).read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(json.loads((Path(result["output_dir"]) / "result.json").read_text()), result)
        self.assertIn("5–32", summary["selection_description"])

    def test_cell_spanning_z_counted_once_and_saved_masks_match_threshold(self):
        mask = sphere(self.shape, (12, 20, 20), 6)
        stack = self.stack(self.signal(mask))
        result, summary, rows = self.analyze(stack)
        self.assertEqual(summary["counts"]["green_objects"], 1)
        labels = np.load(result["labels_green"])
        np.testing.assert_array_equal(labels > 0, mask)
        self.assertEqual(json.loads(Path(result["settings"]).read_text()), self.settings)
        self.assertAlmostEqual(float(rows[0]["volume_um3"]), mask.sum())
        self.assertEqual(rows[0]["review_required"], "False")

    def test_overlap_is_candidate_with_uncertainty_not_definitive_viability(self):
        green = self.signal(sphere(self.shape, (12, 20, 20), 5))
        red = self.signal(sphere(self.shape, (12, 20, 20), 2))
        _, summary, rows = self.analyze(self.stack(green, red))
        self.assertEqual(summary["counts"]["dual_positive_candidate"], 1)
        self.assertEqual(summary["counts"]["candidate_count_min"], 1)
        self.assertEqual(summary["counts"]["candidate_count_max"], 2)
        self.assertEqual(rows[0]["review_required"], "True")
        self.assertNotIn("viability_percent", summary)

    def test_touching_cells_separated_by_physical_distance_watershed(self):
        green = self.signal(sphere(self.shape, (12, 20, 15), 5), sphere(self.shape, (12, 20, 24), 5))
        _, summary, _ = self.analyze(self.stack(green))
        self.assertEqual(summary["counts"]["green_objects"], 2)

    def test_one_to_many_association_stays_unresolved(self):
        green = self.signal(sphere(self.shape, (12, 20, 20), 7))
        red = self.signal(sphere(self.shape, (12, 20, 16), 2), sphere(self.shape, (12, 20, 24), 2))
        _, summary, rows = self.analyze(self.stack(green, red))
        self.assertEqual(summary["counts"]["unresolved"], 1)
        self.assertEqual(rows[0]["red_ids"], "1;2")
        self.assertEqual(rows[0]["green_ids"], "1")
        self.assertEqual(rows[0]["candidate_count_min"], "2")
        self.assertEqual(rows[0]["candidate_count_max"], "3")

    def test_physical_surface_gap_respects_anisotropic_z_spacing(self):
        green, red = self.signal(), self.signal()
        green[10, 18:23, 18:23] = 150
        red[12, 18:23, 18:23] = 150
        settings = deepcopy(self.settings)
        settings["match_distance_um"] = 3
        _, summary, _ = self.analyze(self.stack(green, red, spacing=(1, 1, 1)), settings)
        self.assertEqual(summary["counts"]["unresolved"], 1)
        _, summary, rows = self.analyze(self.stack(green, red, spacing=(3, 1, 1)), settings)
        self.assertEqual(summary["counts"]["unresolved"], 0)
        self.assertEqual(summary["counts"]["total_groups"], 2)
        self.assertEqual(float(rows[0]["volume_um3"]), 75)

    def test_low_region_and_high_peak_threshold_both_required(self):
        green = self.signal(sphere(self.shape, (12, 20, 20), 4))
        green[green > 0] = 70
        stack = self.stack(green)
        _, summary, _ = self.analyze(stack)
        self.assertEqual(summary["counts"]["green_objects"], 0)
        settings = deepcopy(self.settings)
        settings["green"]["high"] = 60
        _, summary, _ = self.analyze(stack, settings)
        self.assertEqual(summary["counts"]["green_objects"], 1)
        settings["green"].update(low=90, high=100)
        _, summary, _ = self.analyze(stack, settings)
        self.assertEqual(summary["counts"]["green_objects"], 0)

    def test_empty_volume_has_complete_outputs_and_zero_counts(self):
        result, summary, rows = self.analyze(self.stack())
        self.assertEqual(summary["counts"]["total_groups"], 0)
        self.assertEqual(rows, [])
        self.assertFalse(np.load(result["labels_green"]).any())

    def test_boundary_objects_flagged_and_optional_exclusion(self):
        green = self.signal(sphere(self.shape, (0, 20, 20), 5))
        stack = self.stack(green)
        _, summary, rows = self.analyze(stack)
        self.assertEqual(summary["counts"]["green_objects"], 1)
        self.assertEqual(rows[0]["touches_boundary"], "True")
        settings = deepcopy(self.settings)
        settings["exclude_border"] = True
        _, summary, _ = self.analyze(stack, settings)
        self.assertEqual(summary["counts"]["green_objects"], 0)

    def test_minimum_volume_is_physical_and_inclusive(self):
        green = self.signal()
        green[10:12, 18:20, 18:20] = 150
        stack = self.stack(green, spacing=(2, 2, 2))
        settings = deepcopy(self.settings)
        settings["green"]["min_volume_um3"] = 64
        _, summary, _ = self.analyze(stack, settings)
        self.assertEqual(summary["counts"]["green_objects"], 1)
        settings["green"]["min_volume_um3"] = 64.01
        _, summary, _ = self.analyze(stack, settings)
        self.assertEqual(summary["counts"]["green_objects"], 0)

    def test_requires_calibration_and_rejects_bad_settings(self):
        stack = self.stack()
        stack["spacing_um"][0] = None
        with self.assertRaisesRegex(ValueError, "spacing"):
            self.analyze(stack)
        stack["spacing_um"][0] = 1
        settings = deepcopy(self.settings)
        settings["green"]["low"] = 0
        with self.assertRaisesRegex(ValueError, "positive"):
            self.analyze(stack, settings)
        settings = deepcopy(self.settings)
        settings["background_sigma_um"] = 5
        with self.assertRaisesRegex(ValueError, "background subtraction"):
            self.analyze(stack, settings)

    def test_default_raw_thresholds_do_not_reuse_2d_contrast(self):
        stack = self.stack()
        default = volume.default_settings(stack, {"segmentation": {"green": {"low": 0.01, "high": 0.02}}})
        self.assertGreaterEqual(default["green"]["low"], 1)
        self.assertEqual(default["green"]["sigma_um"], 0)
        self.assertEqual(default["match_distance_um"], 0)

    def test_existing_run_and_source_cache_are_protected(self):
        stack = self.stack()
        with self.assertRaises(ValueError):
            volume.analyze_volume(stack, self.settings, self.cache / "bad")
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaises(FileExistsError):
            volume.analyze_volume(stack, self.settings, existing)
        stack["run_dir"] = str(existing)
        with self.assertRaises(ValueError):
            volume.analyze_volume(stack, self.settings, existing / "bad")
        other_run = self.root / "other_run"
        other_run.mkdir()
        (other_run / "run_manifest.json").write_text("{}")
        with self.assertRaises(ValueError):
            volume.analyze_volume(stack, self.settings, other_run / "bad")

    def test_cancellation_does_not_publish_partial_output(self):
        stack = self.stack()
        counter = [0]
        def cancelled():
            counter[0] += 1
            return counter[0] > 2
        with self.assertRaises(volume.VolumeAnalysisCancelled):
            volume.analyze_volume(stack, self.settings, self.root / "cancelled", cancelled=cancelled)
        self.assertFalse((self.root / "cancelled").exists())
        self.assertFalse(list(self.root.glob(".volume-pending-*")))

    def test_late_cancellation_releases_windows_label_mappings(self):
        stack = self.stack(self.signal(sphere(self.shape, (12, 20, 20), 4)))
        stopped = [False]
        def progress(message):
            if "associations" in message:
                stopped[0] = True
        with self.assertRaises(volume.VolumeAnalysisCancelled):
            volume.analyze_volume(stack, self.settings, self.root / "cancelled_late",
                                  progress=progress, cancelled=lambda: stopped[0])
        self.assertFalse((self.root / "cancelled_late").exists())
        self.assertFalse(list(self.root.glob(".volume-pending-*")))

    def test_reopen_verifies_saved_artifacts_and_supports_relocation(self):
        result, _, _ = self.analyze(self.stack(self.signal(sphere(self.shape, (12, 20, 20), 4))))
        reopened = volume.read_volume_result(result["output_dir"])
        self.assertEqual(reopened, result)
        moved = self.root / "moved"
        Path(result["output_dir"]).rename(moved)
        reopened = volume.read_volume_result(moved / "result.json")
        self.assertEqual(Path(reopened["labels_green"]).parent, moved)
        labels = np.load(reopened["labels_green"])
        labels[0, 0, 0] = 99
        np.save(reopened["labels_green"], labels)
        with self.assertRaisesRegex(ValueError, "integrity"):
            volume.read_volume_result(moved)

    def test_reopen_rejects_changed_thresholds_and_unsafe_manifest(self):
        result, _, _ = self.analyze(self.stack())
        path = Path(result["settings"])
        path.write_text(path.read_text().replace('"low": 20.0', '"low": 22.0'))
        with self.assertRaisesRegex(ValueError, "integrity"):
            volume.read_volume_result(result["output_dir"])
        manifest = Path(result["checksums"])
        checksums = json.loads(manifest.read_text())
        checksums["../outside.npy"] = "0" * 64
        manifest.write_text(json.dumps(checksums))
        with self.assertRaisesRegex(ValueError, "invalid entries"):
            volume.read_volume_result(result["output_dir"], verify=False)

    def test_roi_limit_fails_cleanly_and_preserves_sources(self):
        stack = self.stack(self.signal(sphere(self.shape, (12, 20, 20), 6)))
        with patch.object(volume, "MAX_COMPONENT_VOXELS", 10):
            with self.assertRaisesRegex(ValueError, "too large"):
                self.analyze(stack)
        self.assertFalse(list(self.root.glob(".volume-pending-*")))
        self.assertTrue(Path(stack["paths"]["green"]).is_file())

    def test_small_noise_removed_before_retained_component_limit(self):
        green = self.signal(sphere(self.shape, (15, 25, 25), 4))
        green[2, 1:10:2, 1:10:2] = 150
        with patch.object(volume, "MAX_COMPONENTS", 2):
            _, summary, _ = self.analyze(self.stack(green))
        self.assertEqual(summary["counts"]["green_objects"], 1)

    def test_low_memory_fails_before_creating_partial_output(self):
        with patch.object(volume, "_available_memory", return_value=1):
            with self.assertRaisesRegex(ValueError, "free memory"):
                self.analyze(self.stack())
        self.assertFalse(list(self.root.glob(".volume-pending-*")))

    def test_slab_gaussian_matches_full_3d_filter(self):
        from scipy import ndimage as ndi
        rng = np.random.default_rng(123)
        array = rng.integers(0, 255, self.shape, dtype=np.uint8)
        with patch.object(volume, "SLAB_VOXELS", 4000):
            actual = volume._smooth(array, 1.5, (2., 1., 1.), self.root / "smooth.npy", None)
        expected = ndi.gaussian_filter(array.astype(np.float32), [0.75, 1.5, 1.5], mode="reflect")
        np.testing.assert_allclose(actual, expected, rtol=1e-6)
        del actual

    def test_cli_writes_failure_status(self):
        path = self.root / "job.json"
        path.write_text(json.dumps({"stack_info": {}, "settings": {}, "output_dir": str(self.root / "bad")}))
        self.assertEqual(volume.run_job(path), 1)
        self.assertEqual(json.loads((self.root / "job_status.json").read_text())["status"], "failed")

    def test_cli_cancellation_sentinel_works_without_console(self):
        path = self.root / "cancel_job.json"
        stack = self.stack()
        path.write_text(json.dumps({"stack_info": stack, "settings": self.settings,
                                    "output_dir": str(self.root / "cancel_job_result")}))
        (self.root / "cancel_job_cancel").touch()
        with patch.object(volume.sys, "stdout", None):
            self.assertEqual(volume.run_job(path), 130)
        self.assertEqual(json.loads((self.root / "cancel_job_status.json").read_text())["status"], "cancelled")
        self.assertFalse((self.root / "cancel_job_result").exists())

    def test_12bit_display_uses_acquired_range_in_figure(self):
        from matplotlib.figure import Figure
        captured = []
        def capture(figure, path, **kwargs):
            captured.extend(np.asarray(ax.images[0].get_array()).copy() for ax in figure.axes)
        arrays = {"green": np.full((3, 5, 5), 4095, np.uint16), "red": np.zeros((3, 5, 5), np.uint16)}
        labels = {role: np.zeros((3, 5, 5), np.int32) for role in ("green", "red")}
        summary = {"counts": {key: 0 for key in volume.STATUSES}, "shape_zyx": [3, 5, 5], "spacing_um": [1, 1, 1],
                   "display": {"green": [0, 4095], "red": [0, 4095]}}
        with patch.object(Figure, "savefig", capture):
            volume._figure(arrays, labels, self.settings, summary, self.root / "figure.png")
        np.testing.assert_array_equal(captured[0][:, :, 1], 1.)
        np.testing.assert_array_equal(captured[2][:, :, 1], 1.)


if __name__ == "__main__":
    unittest.main()
