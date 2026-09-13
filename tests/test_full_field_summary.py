"""Full-field report rendering uses saved measurements and uncropped TIFFs."""
from copy import deepcopy
import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

from desktop import full_field_summary as summary
from desktop.services import default_config


class FullFieldSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / "analysis_test"
        self.run.mkdir()
        self.arrays = {}

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, two_fields=False, representative="first", ebfp=False):
        config = default_config()
        config["input"].update({"expected_shape": [64, 80], "dtype": "uint8", "pixel_size_um": [2., 2.]})
        config["display"].update({"green": [0, 255], "red": [0, 255], "ebfp": [0, 255], "scale_bar_um": 10})
        entries, image_rows, rep_rows, files = [], [], [], []
        (self.run / "labels").mkdir()
        fields = ("first", "second") if two_fields else ("first",)
        for index, name in enumerate(fields):
            folder = self.root / name
            folder.mkdir()
            entry = {"image_id": name, "replicate_id": name, "ebfp": ""}
            for role in ("green", "red", "ebfp") if ebfp else ("green", "red"):
                array = np.full((64, 80), 20 + 30 * index, np.uint8)
                array[0, :] = 255
                array[-1, :] = 192
                array[:, 0] = 128
                array[:, -1] = 64
                path = folder / (role + ".tif")
                Image.fromarray(array).save(path)
                self.arrays[(name, role)] = array
                entry[role] = str(path)
                files.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            entries.append(entry)
            live, dead, objects = [np.zeros((64, 80), np.int32) for _ in range(3)]
            live[2:5, 3:7] = 1
            dead[30:35, 40:45] = 1
            objects[live > 0] = 1
            objects[dead > 0] = 2
            np.savez_compressed(self.run / "labels" / (name + ".npz"), live=live, dead=dead, objects=objects)
            row = {"image_id": name, "replicate_id": name, "live_only": 80 if index == 0 else 20,
                   "dead_only": 15 if index == 0 else 75, "double_positive": 13, "total": 108,
                   "viability_percent": (80 if index == 0 else 20) / 108 * 100,
                   "ebfp_all_percent": 50 if ebfp else "", "ebfp_live_percent": 50 if ebfp else ""}
            image_rows.append(row)
            rep_rows.append({key: value for key, value in row.items() if key != "image_id"})
        for filename, rows in (("resolved_samples.csv", entries), ("image_summary.csv", image_rows), ("replicate_summary.csv", rep_rows)):
            with (self.run / filename).open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        values = [row["viability_percent"] for row in rep_rows]
        stats = {"viability_percent": {"mean": float(np.mean(values)), "sample_sd": float(np.std(values, ddof=1)) if two_fields else None},
                 "ebfp_live_percent": {"mean": 50 if ebfp else None, "sample_sd": None},
                 "ebfp_all_percent": {"mean": 50 if ebfp else None, "sample_sd": None}}
        config["leica_imports"] = [{"analysis_field_id": "first", "selection": {"series_name": "Synthetic", "mode": "max",
                                   "z_start": 0, "z_stop": 62, "sizes": {"Z": 62}, "time_index": 0}}]
        (self.run / "effective_config.json").write_text(json.dumps(config))
        (self.run / "aggregate_summary.json").write_text(json.dumps(stats))
        (self.run / "run_manifest.json").write_text(json.dumps({"status": "completed", "input_files": files, "warnings": []}))
        (self.run / "figure_metadata.json").write_text(json.dumps({"representative_image_id": representative, "crop_yx": [20, 40, 25, 50]}))
        Image.new("RGB", (24, 24), "white").save(self.run / "figure.png")
        (self.run / "figure.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        return config

    def test_full_boundary_pixels_and_saved_counts_are_preserved(self):
        config = self.fixture()
        before = {path: path.read_bytes() for path in self.run.rglob("*") if path.is_file()}
        captured = []
        original = summary._render
        def inspect(*args, **kwargs):
            figure = original(*args, **kwargs)
            for ax in figure.axes:
                for image in ax.images:
                    captured.append((np.asarray(image.get_array()).copy(), image.get_extent(), tuple(ax.get_xlim()), tuple(ax.get_ylim())))
            return figure
        with patch.object(summary, "_render", side_effect=inspect):
            paths = summary.build_full_field_summary(self.run, self.root / "full")
        self.assertEqual(len(captured), 3)
        expected = self.arrays[("first", "green")] / 255.
        np.testing.assert_array_equal(captured[0][0][:, :, 1], expected)
        np.testing.assert_array_equal(captured[1][0][:, :, 0], self.arrays[("first", "red")] / 255.)
        for pixels, extent, xlim, ylim in captured:
            self.assertEqual(pixels.shape[:2], (64, 80))
            self.assertEqual(tuple(extent), (-.5, 79.5, 63.5, -.5))
            self.assertEqual(xlim, (-.5, 79.5))
            self.assertEqual(ylim, (63.5, -.5))
        with Image.open(paths["png"]) as image:
            image.verify()
        ET.parse(paths["svg"])
        self.assertIn("74.1% (SD undefined)", Path(paths["svg"]).read_text(encoding="utf-8"))
        metadata = json.loads(Path(paths["metadata"]).read_text())
        self.assertEqual(metadata["crop_yx"], [0, 64, 0, 80])
        self.assertEqual(metadata["view"], "full_field")
        self.assertEqual(metadata["run_total_objects"], 108)
        self.assertEqual(metadata["pixel_size_um"], config["input"]["pixel_size_um"])
        self.assertEqual(metadata["segmentation"], config["segmentation"])
        self.assertEqual(metadata["stored_detection_masks"]["positive_label_counts"], {"live": 1, "dead": 1, "objects": 2})
        self.assertIn("Z 1–62", metadata["leica_context"])
        self.assertEqual(metadata["cache_key"], summary.cache_key(self.run))
        for ext in ("png", "svg"):
            self.assertEqual(metadata["artifacts"][ext]["sha256"], hashlib.sha256(Path(paths[ext]).read_bytes()).hexdigest())
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_existing_representative_overrides_median_fallback(self):
        self.fixture(two_fields=True, representative="second")
        paths = summary.build_full_field_summary(self.run, self.root / "chosen")
        metadata = json.loads(Path(paths["metadata"]).read_text())
        self.assertEqual(metadata["representative_image_id"], "second")
        self.assertEqual(metadata["representative_source_channel_paths"]["green"], str(self.root / "second" / "green.tif"))
        (self.run / "figure_metadata.json").write_text('{"representative_image_id":"unknown"}')
        rows = summary.read_results(self.run)["image"]
        self.assertEqual(summary._representative(self.run, rows), "first")

    def test_cache_key_reads_no_pixel_arrays_and_changes_with_inputs(self):
        self.fixture()
        with patch("PIL.Image.open", side_effect=AssertionError("cache key decoded pixels")):
            first = summary.cache_key(self.run)
        path = self.root / "first" / "green.tif"
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
        self.assertNotEqual(summary.cache_key(self.run), first)
        second = summary.cache_key(self.run)
        (self.run / "figure_metadata.json").write_text('{"representative_image_id":"different"}')
        self.assertNotEqual(summary.cache_key(self.run), second)

    def test_never_overwrites_run_or_existing_output(self):
        self.fixture()
        with self.assertRaisesRegex(ValueError, "outside completed"):
            summary.build_full_field_summary(self.run, self.run / "replacement")
        output = self.root / "existing"
        output.mkdir()
        marker = output / "preserve.txt"
        marker.write_text("Existing output")
        with self.assertRaises(FileExistsError):
            summary.build_full_field_summary(self.run, output)
        self.assertEqual(marker.read_text(), "Existing output")

    def test_changed_source_cannot_be_presented_with_old_measurements(self):
        self.fixture()
        path = self.root / "first" / "green.tif"
        Image.fromarray(np.zeros((64, 80), np.uint8)).save(path)
        with self.assertRaisesRegex(ValueError, "changed since analysis"):
            summary.build_full_field_summary(self.run, self.root / "invalid")
        self.assertFalse((self.root / "invalid").exists())

    def test_optional_ebfp_adds_full_field_panel(self):
        self.fixture(ebfp=True)
        captured = []
        original = summary._render
        def inspect(*args, **kwargs):
            figure = original(*args, **kwargs)
            captured.extend(np.asarray(image.get_array()).copy() for ax in figure.axes for image in ax.images)
            return figure
        with patch.object(summary, "_render", side_effect=inspect):
            paths = summary.build_full_field_summary(self.run, self.root / "ebfp")
        self.assertEqual(len(captured), 4)
        np.testing.assert_array_equal(captured[2], self.arrays[("first", "ebfp")] / 255.)
        self.assertIn("Detectable EBFP", Path(paths["svg"]).read_text(encoding="utf-8"))

    def test_saved_outline_edges_and_exact_threshold_text(self):
        config = self.fixture()
        config["segmentation"]["green"].update({"low": 12345.67, "high": 54321.123456789})
        (self.run / "effective_config.json").write_text(json.dumps(config))
        captured = {}
        original = summary._render
        def inspect(*args, **kwargs):
            figure = original(*args, **kwargs)
            for ax in figure.axes:
                for collection in ax.collections:
                    if collection.get_label().startswith("saved_"):
                        captured[collection.get_label()] = {tuple(segment.ravel()) for segment in collection.get_segments()}
            return figure
        with patch.object(summary, "_render", side_effect=inspect):
            paths = summary.build_full_field_summary(self.run, self.root / "outlines")
        expected = {(2.5, y - .5, 2.5, y + .5) for y in range(2, 5)}
        expected |= {(6.5, y - .5, 6.5, y + .5) for y in range(2, 5)}
        expected |= {(x - .5, 1.5, x + .5, 1.5) for x in range(3, 7)}
        expected |= {(x - .5, 4.5, x + .5, 4.5) for x in range(3, 7)}
        self.assertEqual(captured["saved_live_outlines"], expected)
        svg = Path(paths["svg"]).read_text(encoding="utf-8")
        self.assertIn("Region 12345.67", svg)
        self.assertIn("Peak 54321.123456789", svg)
        self.assertIn("Outlines: saved detections", svg)
        metadata = json.loads(Path(paths["metadata"]).read_text())
        self.assertEqual(metadata["segmentation"]["green"]["high"], 54321.123456789)

    def test_changed_thresholds_or_masks_fail_saved_checksum_verification(self):
        config = self.fixture()
        checksums = {str(path.relative_to(self.run)).replace("/", "\\"): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in self.run.rglob("*") if path.is_file()}
        (self.run / "output_checksums.json").write_text(json.dumps(checksums))
        key = summary.cache_key(self.run)
        config["segmentation"]["red"]["high"] += 1
        (self.run / "effective_config.json").write_text(json.dumps(config))
        self.assertNotEqual(summary.cache_key(self.run), key)
        with self.assertRaisesRegex(ValueError, "effective_config.json"):
            summary.build_full_field_summary(self.run, self.root / "bad_thresholds")
        config["segmentation"]["red"]["high"] -= 1
        (self.run / "effective_config.json").write_text(json.dumps(config))
        mask = self.run / "labels" / "first.npz"
        data = bytearray(mask.read_bytes())
        data[-1] ^= 1
        mask.write_bytes(data)
        self.assertNotEqual(summary.cache_key(self.run), key)
        with self.assertRaisesRegex(ValueError, "labels/first.npz"):
            summary.build_full_field_summary(self.run, self.root / "bad_masks")


if __name__ == "__main__":
    unittest.main()
