"""Preview parity with archived masks and the full-image analysis contract."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd
from PIL import Image

from desktop import segmentation as backend
from desktop.segmentation import (ARRAY_KEYS, PreviewSession, compute_preview,
                                  read_preview, write_preview, write_preview_data)
from desktop.preview_matching import combine_preview
from desktop.services import default_config, read_manifest
from pipeline.core import combine, segment, SEGMENT_COLUMNS
from pipeline.io import read_scalar


ROOT = Path(__file__).resolve().parents[1]


class SegmentationPreviewTests(unittest.TestCase):
    def assert_same_preview(self, actual, expected):
        for key in ARRAY_KEYS:
            np.testing.assert_array_equal(actual[key], expected[key])
        for key in expected.keys() - set(ARRAY_KEYS):
            self.assertEqual(actual[key], expected[key])

    def fixture(self, folder, empty=False):
        settings = default_config()
        shape = (83, 121)
        settings["input"]["expected_shape"] = list(shape)
        yy, xx = np.indices(shape)
        green = np.zeros(shape, dtype=np.uint8)
        red = green.copy()
        if not empty:
            green = (100 * np.exp(-((yy - 30) ** 2 + (xx - 30) ** 2) / 18)
                     + 100 * np.exp(-((yy - 60) ** 2 + (xx - 85) ** 2) / 18)).astype(np.uint8)
            red = (100 * np.exp(-((yy - 30) ** 2 + (xx - 30) ** 2) / 18)
                   + 100 * np.exp(-((yy - 25) ** 2 + (xx - 90) ** 2) / 18)).astype(np.uint8)
        row = {"image_id": "synthetic", "replicate_id": "synthetic"}
        for channel, pixels in (("green", green), ("red", red)):
            path = folder / f"{channel}.tif"
            Image.fromarray(pixels).save(path)
            row[channel] = str(path)
        # Preview deliberately does not read the optional third channel.
        row["ebfp"] = str(folder / "ebfp-not-present.tif")
        return row, settings

    def test_reference_s5_matches_every_original_label_and_viability(self):
        row = read_manifest(ROOT / "reference" / "samples.csv")[0]
        preview = compute_preview(row, default_config())
        with np.load(ROOT / "reference" / "expected" / "S5_masks.npz") as expected:
            for channel, original in (("green", "live"), ("red", "dead"), ("objects", "objects")):
                np.testing.assert_array_equal(preview[f"labels_{channel}"], expected[original])
        self.assertEqual(preview["counts"], {
            "green_detections": 283, "red_detections": 248,
            "live_only": 190, "dead_only": 155, "double_positive": 93,
            "total": 438, "viability_percent": 100 * 190 / 438,
        })
        self.assertEqual(preview["raw_red"].shape, (1024, 1024))

    def test_full_image_counts_are_independent_of_display_and_inputs_are_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            row, settings = self.fixture(Path(temporary))
            original_row, original_settings = deepcopy(row), deepcopy(settings)
            original_bytes = {channel: Path(row[channel]).read_bytes() for channel in ("green", "red")}
            first = compute_preview(row, settings)
            self.assertEqual(row, original_row)
            self.assertEqual(settings, original_settings)
            changed = deepcopy(settings)
            changed["display"]["central_crop_fraction"] = 0.05
            changed["display"]["red"] = [0, 1]
            changed["display"]["green"] = [0, 255]
            second = compute_preview(row, changed)
            for key in ARRAY_KEYS:
                np.testing.assert_array_equal(first[key], second[key])
            self.assertEqual(first["counts"], second["counts"])
            self.assertEqual(first["counts"]["total"], 3)
            self.assertEqual(first["counts"]["double_positive"], 1)
            first["config"]["segmentation"]["red"]["high"] = 200
            first["row"]["image_id"] = "edited"
            self.assertEqual(settings, original_settings)
            self.assertEqual(row, original_row)
            for channel in ("green", "red"):
                self.assertEqual(Path(row[channel]).read_bytes(), original_bytes[channel])

    def test_empty_field_serializes_null_viability_and_no_objects(self):
        with tempfile.TemporaryDirectory() as temporary:
            row, settings = self.fixture(Path(temporary), empty=True)
            preview = compute_preview(row, settings)
            self.assertIsNone(preview["counts"]["viability_percent"])
            self.assertEqual(preview["counts"]["total"], 0)
            for key in ("detections_green", "detections_red", "objects"):
                self.assertEqual(preview[key], [])
            for key in ("labels_green", "labels_red", "labels_objects"):
                self.assertFalse(preview[key].any())
            json.dumps({key: value for key, value in preview.items() if key not in ARRAY_KEYS}, allow_nan=False)

    def test_preview_files_round_trip_arrays_and_exact_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            row, settings = self.fixture(folder)
            request = folder / "request.json"
            request.write_text(json.dumps({"row": row, "config": settings}), encoding="utf-8")
            output = folder / "new-preview"
            self.assertEqual(write_preview(request, output), 0)
            loaded = read_preview(output)
            expected = compute_preview(row, settings)
            for key in ARRAY_KEYS:
                np.testing.assert_array_equal(loaded[key], expected[key])
            for key in expected.keys() - set(ARRAY_KEYS):
                self.assertEqual(loaded[key], expected[key])
            self.assertFalse((output / "error.json").exists())

    def test_invalid_settings_are_reported_and_existing_output_is_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            row, settings = self.fixture(folder)
            settings["segmentation"]["red"]["low"] = 100
            request = folder / "request.json"
            request.write_text(json.dumps({"row": row, "config": settings}), encoding="utf-8")
            output = folder / "new-preview"
            self.assertEqual(write_preview(request, output), 1)
            error = json.loads((output / "error.json").read_text(encoding="utf-8"))
            self.assertIn("red", error["error"])
            self.assertEqual(error["error_type"], "ValueError")
            with self.assertRaisesRegex(ValueError, "red"):
                read_preview(output)
            before = {path.name: path.read_bytes() for path in output.iterdir()}
            self.assertEqual(write_preview(request, output), 1)
            self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, before)

    def test_session_reuses_only_the_unchanged_computations(self):
        with tempfile.TemporaryDirectory() as temporary:
            row, settings = self.fixture(Path(temporary))
            session = PreviewSession()
            with patch.object(backend, "read_scalar", wraps=read_scalar) as reads, \
                    patch.object(backend, "segment", wraps=segment) as segments, \
                    patch.object(backend, "combine_preview", wraps=combine_preview) as matches:
                first = session.compute(row, settings)
                self.assertEqual((reads.call_count, segments.call_count, matches.call_count), (2, 2, 1))
                second = session.compute(row, settings)
                self.assert_same_preview(first, second)
                self.assertEqual((reads.call_count, segments.call_count, matches.call_count), (2, 2, 1))

                red_edit = deepcopy(settings)
                red_edit["segmentation"]["red"]["high"] += 3
                edited = session.compute(row, red_edit)
                self.assertIs(edited["labels_green"], first["labels_green"])
                self.assertEqual((reads.call_count, segments.call_count, matches.call_count), (2, 3, 2))
                self.assertEqual(segments.call_args.args[1], "red")
                self.assertTrue(session.last_timings["green_segmentation_cached"])
                self.assertFalse(session.last_timings["red_segmentation_cached"])

                match_edit = deepcopy(red_edit)
                match_edit["matching"]["max_distance_px"] = 5
                matched = session.compute(row, match_edit)
                self.assertIs(matched["labels_red"], edited["labels_red"])
                self.assertEqual((reads.call_count, segments.call_count, matches.call_count), (2, 3, 3))

                display_edit = deepcopy(match_edit)
                display_edit["display"]["red"] = [0, 90]
                display_edit["ebfp"]["q_threshold"] = 0.1
                session.compute(row, display_edit)
                self.assertEqual((reads.call_count, segments.call_count, matches.call_count), (2, 3, 3))

                shared_edit = deepcopy(display_edit)
                shared_edit["segmentation"]["background_sigma_px"] = 10
                shared = session.compute(row, shared_edit)
                self.assertEqual((reads.call_count, segments.call_count, matches.call_count), (2, 5, 4))
            for actual, config in ((edited, red_edit), (matched, match_edit), (shared, shared_edit)):
                self.assert_same_preview(actual, compute_preview(row, config))

    def test_session_invalidates_changed_input_and_input_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            row, settings = self.fixture(Path(temporary))
            session = PreviewSession()
            first = session.compute(row, settings)
            path = Path(row["red"])
            before = path.stat()
            Image.fromarray(np.zeros(first["raw_red"].shape, np.uint8)).save(path)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
            changed = session.compute(row, settings)
            self.assertIs(first["labels_green"], changed["labels_green"])
            self.assertEqual(changed["counts"]["red_detections"], 0)
            self.assertFalse(session.last_timings["red_input_cached"])
            self.assert_same_preview(changed, compute_preview(row, settings))
            wrong_shape = deepcopy(settings)
            wrong_shape["input"]["expected_shape"] = [99, 99]
            with self.assertRaisesRegex(ValueError, "shape"):
                session.compute(row, wrong_shape)
            wrong_dtype = deepcopy(settings)
            wrong_dtype["input"]["dtype"] = "uint16"
            with self.assertRaisesRegex(ValueError, "dtype"):
                session.compute(row, wrong_dtype)

    def test_session_metadata_is_detached_arrays_readonly_and_memory_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            row, settings = self.fixture(Path(temporary))
            session = PreviewSession()
            first = session.compute(row, settings)
            first["objects"][0]["area"] = -1
            first["counts"]["total"] = -1
            first["detections_red"][0]["area"] = -1
            first["config"]["segmentation"]["red"]["high"] = -1
            first["row"]["image_id"] = "changed"
            for key in ARRAY_KEYS:
                with self.assertRaises(ValueError):
                    first[key][0, 0] = 123
            self.assert_same_preview(session.compute(row, settings), compute_preview(row, settings))
            for high in range(5, 12):
                config = deepcopy(settings)
                config["segmentation"]["red"]["high"] = high
                session.compute(row, config)
            self.assertLessEqual(len(session._inputs), 4)
            self.assertTrue(all(len(cache) <= 2 for cache in session._channels.values()))
            self.assertLessEqual(len(session._combined), 2)
            session.clear()
            self.assertFalse(session._inputs)
            self.assertFalse(session._combined)
            self.assertFalse(session.last_timings)

    def test_uncompressed_worker_payload_is_exact_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            row, settings = self.fixture(folder)
            preview = compute_preview(row, settings)
            output = folder / "payload"
            write_preview_data(preview, output)
            self.assert_same_preview(read_preview(output), preview)
            with zipfile.ZipFile(output / "arrays.npz") as archive:
                self.assertTrue(all(item.compress_type == zipfile.ZIP_STORED for item in archive.infolist()))
            with self.assertRaises(FileExistsError):
                write_preview_data(preview, output)
            self.assert_same_preview(read_preview(output), preview)

    def test_fast_matching_matches_original_all_reference_and_test1_records(self):
        rows = read_manifest(ROOT / "reference" / "samples.csv")
        # The private test pair is exercised in this workspace, never shipped.
        if (ROOT / "samples.csv").is_file():
            rows += read_manifest(ROOT / "samples.csv")
        settings = default_config()
        for row in rows:
            with self.subTest(image_id=row["image_id"]):
                preview = compute_preview(row, settings)
                labels, records = combine(
                    pd.DataFrame(preview["detections_green"], columns=SEGMENT_COLUMNS),
                    pd.DataFrame(preview["detections_red"], columns=SEGMENT_COLUMNS),
                    preview["labels_green"], preview["labels_red"], settings)
                np.testing.assert_array_equal(preview["labels_objects"], labels)
                self.assertEqual(preview["objects"], records.to_dict("records"))

    def test_fast_matching_preserves_overlap_lost_masks_ties_dilation_and_empty_channels(self):
        def detections(labels, channel, override=None):
            rows = []
            for ident in np.unique(labels):
                if ident == 0:
                    continue
                yy, xx = np.where(labels == ident)
                y, x = override if override is not None else (float(yy.mean()), float(xx.mean()))
                rows.append(dict(channel=channel, id=int(ident), y=y, x=x,
                                 area=len(yy), peak_contrast=12.5, mean_raw=25.,
                                 max_raw=50., parent_count=1))
            return pd.DataFrame(rows, columns=SEGMENT_COLUMNS)

        def parity(lg, lr, config, g_override=None, r_override=None):
            g, r = detections(lg, "live", g_override), detections(lr, "dead", r_override)
            before_g, before_r = lg.copy(), lr.copy()
            expected_labels, expected_records = combine(g, r, lg, lr, config)
            labels, records = combine_preview(g, r, lg, lr, config)
            np.testing.assert_array_equal(labels, expected_labels)
            pd.testing.assert_frame_equal(records, expected_records, check_exact=True)
            np.testing.assert_array_equal(lg, before_g)
            np.testing.assert_array_equal(lr, before_r)
            return records

        settings = default_config()
        simple = np.zeros((21, 24), np.int32)
        simple[5:8, 5:8] = 1
        matched = parity(simple, simple, settings)
        self.assertEqual(matched.iloc[0].area, 9)
        lost = parity(simple, simple, settings, (0., 0.), (20., 20.))
        self.assertTrue(lost.iloc[0].mask_lost)
        for lg, lr in ((simple, simple * 0), (simple * 0, simple), (simple * 0, simple * 0)):
            parity(lg, lr, settings)
        for seed in range(10):
            random = np.random.default_rng(seed)
            lg = random.integers(0, 5, simple.shape, dtype=np.int32)
            lr = random.integers(0, 6, simple.shape, dtype=np.int32)
            for dilation in (0, 1):
                settings["matching"]["dilation_px"] = dilation
                parity(lg, lr, settings)
                # Equal centroids create Hungarian cost ties; order must match.
                parity(lg, lr, settings, (10.5, 10.5), (10.5, 10.5))


if __name__ == "__main__":
    unittest.main()
