"""Shared projection controls retain contrast semantics in depth-aware analysis."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from desktop import volume_analysis as volume
from pipeline.core import segment


class SharedVolumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.shape = (13, 56, 56)
        self.config = json.loads((Path(__file__).resolve().parents[1] / "configs/reference_48h.json").read_text())
        self.config["input"].update(expected_shape=list(self.shape[1:]), pixel_size_um=[1., 1.])
        s = self.config["segmentation"]
        s.update(background_sigma_px=8., peak_window_px=5, min_peak_distance_px=6., exclude_border=False)
        for role in ("green", "red"):
            s[role].update(sigma_px=0.7, low=5., high=25., min_area_px=5)
        self.config["matching"].update(max_distance_px=6., dilation_px=1, overlap_window_radius_px=12)

    def tearDown(self):
        self.temp.cleanup()

    def stack(self, green=None, red=None, spacing=(1., 1., 1.)):
        paths = {}
        for role, array in (("green", green), ("red", red)):
            path = self.cache / (role + ".npy")
            np.save(path, np.zeros(self.shape, np.uint8) if array is None else np.asarray(array, np.uint8))
            paths[role] = str(path)
        return {"paths": paths, "shape_zyx": list(self.shape), "spacing_um": list(spacing), "dtype": "uint8",
                "field_id": "shared-test-cell"}

    def signal(self, z=slice(4, 9), xy=(slice(22, 32), slice(22, 32))):
        result = np.zeros(self.shape, np.uint8)
        height, width = (axis.stop - axis.start for axis in xy)
        if min(height, width) <= 2:
            result[z, xy[0], xy[1]] = 150
        else:
            yy, xx = np.indices((height, width))
            profile = 150 * np.exp(-(((yy - (height - 1) / 2) / (height / 5)) ** 2
                                    + ((xx - (width - 1) / 2) / (width / 5)) ** 2) / 2)
            result[z, xy[0], xy[1]] = profile.astype(np.uint8)
        return result

    def analyze(self, info, config=None, real_figure=False):
        settings = volume.settings_from_config(info, config or self.config)
        output = self.root / ("result_" + str(len(list(self.root.glob("result_*")))))
        def stub(*args):
            args[-1].write_bytes(b"test figure")
        with patch.object(volume, "_figure", wraps=volume._figure if real_figure else stub):
            result = volume.analyze_volume(info, settings, output)
        return result, json.loads(Path(result["summary"]).read_text())

    def test_each_plane_exactly_matches_usual_projection_contrast(self):
        rng = np.random.default_rng(10)
        plane = rng.integers(0, 256, self.shape[1:], dtype=np.uint8)
        for role in ("green", "red"):
            self.config["segmentation"][role]["high"] = 500
            expected = segment(plane, role, self.config)[2]
            np.testing.assert_array_equal(volume.projection_contrast(plane, role, self.config), expected)
        stack = np.stack([plane, np.zeros_like(plane), plane])
        expected = segment(plane, "green", self.config)[2]
        mappings = []
        try:
            result = volume._contrast_volume(stack, "green", self.config, self.root / "contrast.npy", None, mappings)
            np.testing.assert_array_equal(result[0], expected)
            np.testing.assert_array_equal(result[1], 0)
            np.testing.assert_array_equal(result[2], expected)
        finally:
            for array in mappings:
                volume._close_mapping(array)

    def test_repeated_projection_slab_has_same_foreground_and_is_counted_once(self):
        green = self.signal()
        expected, _, _ = segment(green.max(axis=0), "green", self.config)
        result, summary = self.analyze(self.stack(green))
        labels = np.load(result["labels_green"])
        np.testing.assert_array_equal(np.any(labels > 0, axis=0), expected > 0)
        self.assertEqual(summary["counts"]["green_objects"], 1)
        self.assertFalse(labels[:4].any())
        self.assertFalse(labels[9:].any())

    def test_same_xy_separated_z_is_not_dual_positive(self):
        _, summary = self.analyze(self.stack(self.signal(slice(1, 4)), self.signal(slice(9, 12))))
        self.assertEqual(summary["counts"]["green_only"], 1)
        self.assertEqual(summary["counts"]["red_only"], 1)
        self.assertEqual(summary["counts"]["dual_positive_candidate"], 0)

    def test_background_is_subtracted_and_shared_threshold_edits_are_effective(self):
        _, empty = self.analyze(self.stack(np.full(self.shape, 150, np.uint8)))
        self.assertEqual(empty["counts"]["green_objects"], 0)
        info = self.stack(self.signal())
        _, initial = self.analyze(info)
        edited = deepcopy(self.config)
        edited["segmentation"]["green"]["high"] = 200
        _, raised = self.analyze(info, edited)
        self.assertEqual(initial["counts"]["green_objects"], 1)
        self.assertEqual(raised["counts"]["green_objects"], 0)

    def test_minimum_area_means_maximum_cross_section_not_volume(self):
        green = self.signal(slice(1, 12), (slice(25, 27), slice(25, 27)))
        config = deepcopy(self.config)
        config["segmentation"]["green"].update(sigma_px=.1, min_area_px=5)
        _, excluded = self.analyze(self.stack(green), config)
        config["segmentation"]["green"]["min_area_px"] = 4
        _, retained = self.analyze(self.stack(green), config)
        self.assertEqual(excluded["counts"]["green_objects"], 0)
        self.assertEqual(retained["counts"]["green_objects"], 1)

    def test_border_control_includes_selected_z_faces(self):
        info = self.stack(self.signal(slice(0, 4)))
        _, included = self.analyze(info)
        config = deepcopy(self.config)
        config["segmentation"]["exclude_border"] = True
        _, excluded = self.analyze(info, config)
        self.assertEqual(included["counts"]["boundary_groups"], 1)
        self.assertEqual(excluded["counts"]["green_objects"], 0)

    def test_intensity_peak_window_and_minimum_distance_change_splitting(self):
        yy, xx = np.indices(self.shape[1:])
        plane = (160 * np.exp(-((yy - 28) ** 2 + (xx - 22) ** 2) / 18)
                 + 120 * np.exp(-((yy - 28) ** 2 + (xx - 33) ** 2) / 18)).astype(np.uint8)
        green = np.zeros(self.shape, np.uint8)
        green[4:9] = plane
        info = self.stack(green)
        _, split = self.analyze(info)
        distant = deepcopy(self.config)
        distant["segmentation"]["min_peak_distance_px"] = 20
        _, unsplit = self.analyze(info, distant)
        wider = deepcopy(self.config)
        wider["segmentation"]["peak_window_px"] = 25
        _, suppressed = self.analyze(info, wider)
        self.assertEqual(split["counts"]["green_objects"], 2)
        self.assertEqual(unsplit["counts"]["green_objects"], 1)
        self.assertEqual(suppressed["counts"]["green_objects"], 1)

    def test_exact_config_and_settings_are_saved_and_reopened(self):
        info = self.stack(self.signal(), self.signal())
        frozen = volume.settings_from_config(info, self.config)
        self.config["display"]["green"][1] = 180
        self.assertNotEqual(frozen["config"], self.config)
        result, summary = self.analyze(info, real_figure=True)
        self.assertEqual(json.loads(Path(result["settings"]).read_text()),
                         {"mode": "projection_config", "config": self.config})
        self.assertEqual(summary["display"], {role: self.config["display"][role] for role in ("green", "red")})
        self.assertEqual(summary["segmentation_mode"], "projection_config")
        self.assertEqual(summary["field_id"], "shared-test-cell")
        self.assertIn("do not commute", summary["threshold_semantics"])
        self.assertTrue(Path(result["figure"]).read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(volume.read_volume_result(result["output_dir"]), result)
        with (Path(result["output_dir"]) / "green_objects.csv").open() as stream:
            row = next(csv.DictReader(stream))
        self.assertIn("peak_contrast", row)
        self.assertGreaterEqual(int(row["max_xy_area_px"]), 5)

    def test_shared_configuration_must_match_the_source_calibration_and_shape(self):
        info = self.stack()
        with self.assertRaisesRegex(ValueError, "calibration"):
            volume.settings_from_config({**info, "spacing_um": [1., 2., 2.]}, self.config)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            volume.settings_from_config({**info, "shape_zyx": [13, 16, 16]}, self.config)
        with self.assertRaisesRegex(ValueError, "detector"):
            volume.settings_from_config({**info, "dtype": "uint16"}, self.config)

    def matching_fixture(self, green_box, red_boxes, spacing=(1., 1., 1.)):
        green_labels, red_labels = np.zeros(self.shape, np.int32), np.zeros(self.shape, np.int32)
        green_labels[green_box] = 1
        for ident, bounds in enumerate(red_boxes, 1):
            red_labels[bounds] = ident
        def objects(labels, boxes):
            return [{"id": ident, "bounds": bounds,
                     "centroid": np.argwhere(labels == ident).mean(axis=0).tolist(),
                     "voxels": int(np.count_nonzero(labels == ident)), "touches_boundary": False}
                    for ident, bounds in enumerate(boxes, 1)]
        return green_labels, red_labels, objects(green_labels, [green_box]), objects(red_labels, red_boxes), spacing

    def test_shared_matching_uses_dilation_centroid_distance_and_physical_z(self):
        fixture = self.matching_fixture((slice(5, 7), slice(24, 28), slice(24, 28)),
                                        [(slice(5, 7), slice(24, 28), slice(28, 32))])
        settings = {"mode": "projection_config", "config": deepcopy(self.config)}
        rows = volume._associate(*fixture, settings, None)
        self.assertEqual([row["status"] for row in rows], ["unresolved"])
        settings["config"]["matching"]["dilation_px"] = 0
        self.assertEqual(len(volume._associate(*fixture, settings, None)), 2)
        settings["config"]["matching"].update(dilation_px=1, max_distance_px=3)
        self.assertEqual(len(volume._associate(*fixture, settings, None)), 2)
        axial = self.matching_fixture((slice(5, 6), slice(24, 28), slice(24, 28)),
                                     [(slice(6, 7), slice(24, 28), slice(24, 28))])
        settings["config"]["matching"]["max_distance_px"] = 6
        self.assertEqual(len(volume._associate(*axial, settings, None)), 1)
        anisotropic = (*axial[:-1], (3., 1., 1.))
        self.assertEqual(len(volume._associate(*anisotropic, settings, None)), 2)

    def test_shared_matching_window_and_one_to_many_are_not_ignored(self):
        fixture = self.matching_fixture((slice(5, 7), slice(20, 36), slice(20, 36)),
                                        [(slice(5, 7), slice(20, 23), slice(20, 23)),
                                         (slice(5, 7), slice(32, 35), slice(32, 35))])
        config = deepcopy(self.config)
        config["matching"].update(max_distance_px=10, overlap_window_radius_px=12)
        settings = {"mode": "projection_config", "config": config}
        rows = volume._associate(*fixture, settings, None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "unresolved")
        self.assertEqual(rows[0]["red_ids"], "1;2")
        # Direct helper exercises the independent window admission rule. The
        # ordinary configuration validator separately requires window >= max
        # distance + dilation, making this tiny window intentionally invalid.
        config["matching"]["overlap_window_radius_px"] = 1
        unmatched = volume._associate(*fixture, settings, None)
        self.assertEqual(len(unmatched), 3)
        self.assertTrue(all("controls" in row["reason"] for row in unmatched))

    def test_transitive_group_union_includes_intersections_without_matching_edges(self):
        green, red = np.zeros(self.shape, np.int32), np.zeros(self.shape, np.int32)
        green[5:7, 20:30, 20:25], green[5:7, 20:30, 25:30] = 1, 2
        red[5:7, 20:25, 20:30], red[5:7, 25:30, 20:30] = 1, 2
        def records(labels):
            result = []
            for ident in (1, 2):
                points = np.argwhere(labels == ident)
                result.append({"id": ident, "centroid": points.mean(axis=0).tolist(), "voxels": len(points),
                               "bounds": tuple(slice(int(a), int(b) + 1) for a, b in zip(points.min(axis=0), points.max(axis=0))),
                               "touches_boundary": False})
            return result
        # All four masks overlap. One intersection fails matching admission,
        # but all objects are still connected through three admissible edges.
        edges = {(1, 1): 50, (2, 1): 50, (2, 2): 50}
        adjacency = {("green", 1): {("red", 1)}, ("green", 2): {("red", 1), ("red", 2)},
                     ("red", 1): {("green", 1), ("green", 2)}, ("red", 2): {("green", 2)}}
        with patch.object(volume, "_shared_edges", return_value=(edges, adjacency)):
            rows = volume._associate(green, red, records(green), records(red), (1., 1., 1.),
                                     {"mode": "projection_config", "config": self.config}, None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["volume_um3"], np.count_nonzero((green > 0) | (red > 0)))

    def test_peak_separation_uses_physical_z_spacing(self):
        strength = np.zeros((9, 9, 9), float)
        strength[2, 4, 4] = strength[4, 4, 4] = 100
        region = np.ones(strength.shape, bool)
        config = self.config["segmentation"]
        iso = volume._contrast_seeds(strength, region, (1., 1., 1.), config, 80)
        anisotropic = volume._contrast_seeds(strength, region, (4., 1., 1.), config, 80)
        self.assertEqual(len(iso), 1)
        self.assertEqual(len(anisotropic), 2)

    def test_bounded_parallel_contrast_is_bit_exact(self):
        stack = np.random.default_rng(4).integers(0, 256, (5, 256, 256), dtype=np.uint8)
        results = []
        for workers in (1, 4):
            resources = []
            try:
                with patch.object(volume.os, "cpu_count", return_value=workers):
                    working = volume._contrast_volume(stack, "green", self.config,
                                                      self.root / f"contrast-{workers}.npy", None, resources)
                results.append(np.array(working))
            finally:
                for mapping in resources:
                    volume._close_mapping(mapping)
        np.testing.assert_array_equal(*results)

    def test_fast_reopen_still_verifies_figures_and_settings(self):
        result, _ = self.analyze(self.stack(self.signal()))
        self.assertEqual(volume.read_volume_result(result["output_dir"], verify=False), result)
        Path(result["figure"]).write_bytes(b"changed figure")
        with self.assertRaisesRegex(ValueError, "figure.png"):
            volume.read_volume_result(result["output_dir"], verify=False)

    def test_fast_reopen_only_defers_large_label_payload_hashes(self):
        result, _ = self.analyze(self.stack(self.signal()))
        labels = np.load(result["labels_green"], mmap_mode="r+")
        labels[0, 0, 0] = 99
        labels.flush()
        volume._close_mapping(labels)
        self.assertEqual(volume.read_volume_result(result["output_dir"], verify=False), result)
        with self.assertRaisesRegex(ValueError, "labels_green.npy"):
            volume.read_volume_result(result["output_dir"], verify=True)


if __name__ == "__main__":
    unittest.main()
