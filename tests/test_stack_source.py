"""Saved Leica selections recover immutable ZYX volumes, not guessed projection depths."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from desktop import leica, stack_source
from tests.test_leica import FakeContainer, FakeImage


class StackSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source = self.folder / "acquisition.lof"
        self.source.write_bytes(b"original acquisition")
        self.cache = self.folder / "cache"
        self.image = FakeImage()
        self.reader = patch.object(leica.liffile, "LifFile", side_effect=lambda *args, **kwargs: FakeContainer(self.image))
        self.reader.start()

    def tearDown(self):
        self.reader.stop()
        self.temp.cleanup()

    def record(self, **overrides):
        request = {"source": str(self.source), "source_identity": leica.inspect_leica(self.source)["source"],
                   "series_index": 0, "channels": {"green": 0, "red": 1, "ebfp": 2},
                   "mode": "max", "z_start": 1, "z_stop": 3, "time_index": 1,
                   "pixel_size_um": [0.7, 0.8]}
        request.update(overrides)
        result = leica.import_selection(request, self.folder / "imports")
        record = json.loads(Path(result["provenance_path"]).read_text(encoding="utf-8"))
        record["provenance_path"] = result["provenance_path"]
        record["analysis_field_id"] = "field_42"
        record["analysis_channel_paths"] = {role: result["row"][role] for role in ("green", "red")}
        self.image.reads.clear()
        return record

    def load(self, prepared, role):
        # Tests may use a small in-memory copy; production returns only paths.
        return np.load(prepared["paths"][role], allow_pickle=False)

    def test_named_axes_selected_z_time_and_xy_override_are_preserved(self):
        self.image = FakeImage(frame_axes=("X", "Y"))
        record = self.record()
        before = deepcopy(record)
        progress = []
        result = stack_source.prepare_stack(record, self.cache, progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(result["shape_zyx"], [2, 4, 5])
        self.assertEqual(result["spacing_um"], [0.5, 0.7, 0.8])
        self.assertEqual(result["z_indices"], [1, 2])
        self.assertEqual(result["z_positions_um"], [0.5, 1.0])
        self.assertEqual(result["field_id"], "field_42")
        self.assertEqual(progress[-1], (4, 4))
        self.assertEqual(len(self.image.reads), 4)
        for role, channel in (("green", 0), ("red", 1)):
            expected = np.stack([self.image.pixels(channel, z, 1) for z in (1, 2)])
            np.testing.assert_array_equal(self.load(result, role), expected)
            self.assertEqual(result["projection_verification"][role]["state"], "verified")
        self.assertEqual(record, before)
        self.assertEqual(result["import_record"], record)
        result["import_record"]["selection"]["channels"]["green"] = 999
        self.assertEqual(record, before)
        self.assertEqual(self.source.read_bytes(), b"original acquisition")
        json.dumps(result, allow_nan=False)

    def test_swapped_analysis_roles_recover_correct_acquisition_channels(self):
        record = self.record()
        paths = record["analysis_channel_paths"]
        paths["green"], paths["red"] = paths["red"], paths["green"]
        result = stack_source.prepare_stack(record, self.cache)
        self.assertEqual(result["analysis_role_origins"], {"green": "red", "red": "green"})
        self.assertEqual(result["selection"]["channels"], {"green": 1, "red": 0, "ebfp": None})
        np.testing.assert_array_equal(self.load(result, "green")[0], self.image.pixels(1, 1, 1))

    def test_analysis_role_may_explicitly_use_original_optional_channel(self):
        record = self.record()
        record["analysis_channel_paths"]["green"] = record["result"]["row"]["ebfp"]
        result = stack_source.prepare_stack(record, self.cache)
        self.assertEqual(result["selection"]["channels"]["green"], 2)
        np.testing.assert_array_equal(self.load(result, "green")[1], self.image.pixels(2, 2, 1))

    def test_single_slice_only_recovers_that_acquisition_plane(self):
        record = self.record(mode="slice", z_index=2)
        result = stack_source.prepare_stack(record, self.cache)
        self.assertEqual(result["shape_zyx"], [1, 4, 5])
        self.assertEqual(result["z_indices"], [2])
        np.testing.assert_array_equal(self.load(result, "red")[0], self.image.pixels(1, 2, 1))

    def test_old_sidecar_without_z_metadata_or_analysis_fields_is_supported(self):
        record = self.record()
        record.pop("spatial_metadata", None)
        record.pop("analysis_channel_paths")
        record.pop("analysis_field_id")
        result = stack_source.prepare_stack(record, self.cache)
        self.assertEqual(result["z_positions_um"], [0.5, 1.0])
        self.assertEqual(result["field_id"], record["result"]["row"]["image_id"])

    def test_signed_z_coordinates_and_positive_spacing(self):
        z = self.image.xml_element.find(".//DimensionDescription[@DimID='3']")
        z.set("Origin", "14")
        z.set("Length", "-4")
        z.set("Unit", "um")
        result = stack_source.prepare_stack(self.record(), self.cache)
        self.assertEqual(result["z_positions_um"], [12.0, 10.0])
        self.assertEqual(result["spacing_um"], [2.0, 0.7, 0.8])

    def test_bad_or_missing_z_units_do_not_invent_calibration(self):
        z = self.image.xml_element.find(".//DimensionDescription[@DimID='3']")
        for name, value in (("Unit", "pixels"), ("Length", "nan"), ("Length", "0")):
            with self.subTest(name=name, value=value):
                z.set("Unit", "m")
                z.set("Length", "1e-6")
                z.set(name, value)
                result = stack_source.prepare_stack(self.record(), self.cache)
                self.assertIsNone(result["spacing_um"][0])
                self.assertIsNone(result["z_positions_um"])
                self.assertEqual(result["z_indices"], [1, 2])

    def test_new_import_records_calibrated_z_metadata(self):
        record = self.record()
        self.assertEqual(record["spatial_metadata"], {"z_spacing_um": 0.5, "z_positions_um": [0.0, 0.5, 1.0]})

    def test_missing_acquisition_reports_reconnect_and_does_not_create_cache(self):
        record = self.record()
        self.source.unlink()
        with self.assertRaisesRegex(FileNotFoundError, "Reconnect its drive"):
            stack_source.prepare_stack(record, self.cache)
        self.assertFalse(self.cache.exists())

    def test_changed_source_identity_rejected_even_if_cache_exists(self):
        record = self.record()
        first = stack_source.prepare_stack(record, self.cache)
        self.source.write_bytes(b"changed source")
        with self.assertRaisesRegex(ValueError, "changed since this run"):
            stack_source.prepare_stack(record, self.cache)
        self.assertTrue(Path(first["paths"]["green"]).is_file())

    def test_cache_reuses_verified_arrays_without_redecoding_planes(self):
        record = self.record()
        first = stack_source.prepare_stack(record, self.cache)
        self.image.reads.clear()
        second = stack_source.prepare_stack(record, self.cache)
        self.assertTrue(second["cached"])
        self.assertEqual(first["paths"], second["paths"])
        self.assertEqual(self.image.reads, [])

    def test_corrupted_cache_is_preserved_and_rebuilt_elsewhere(self):
        record = self.record()
        first = stack_source.prepare_stack(record, self.cache)
        path = Path(first["paths"]["red"])
        with path.open("r+b") as stream:
            stream.seek(-1, 2)
            stream.write(b"\xff")
        damaged = path.read_bytes()
        self.image.reads.clear()
        second = stack_source.prepare_stack(record, self.cache)
        self.assertFalse(second["cached"])
        self.assertNotEqual(first["paths"], second["paths"])
        self.assertEqual(path.read_bytes(), damaged)
        self.assertEqual(len(self.image.reads), 4)
        np.testing.assert_array_equal(self.load(second, "red")[1], self.image.pixels(1, 2, 1))

    def test_changed_imported_tiff_is_rejected_before_source_planes_are_read(self):
        record = self.record()
        Image.new("L", (5, 4)).save(record["analysis_channel_paths"]["red"])
        with self.assertRaisesRegex(ValueError, "TIFF changed"):
            stack_source.prepare_stack(record, self.cache)
        self.assertEqual(self.image.reads, [])

    def test_projection_mismatch_detects_source_pixels_inconsistent_with_saved_run(self):
        record = self.record()
        original = self.image.pixels
        self.image.pixels = lambda channel, z, time: original(channel, z, time) + np.uint16(1)
        with self.assertRaisesRegex(ValueError, "does not reproduce"):
            stack_source.prepare_stack(record, self.cache)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_missing_imported_tiff_is_explicitly_reported(self):
        record = self.record()
        Path(record["analysis_channel_paths"]["green"]).unlink()
        result = stack_source.prepare_stack(record, self.cache)
        self.assertEqual(result["projection_verification"]["green"]["state"], "unavailable")
        self.assertEqual(result["projection_verification"]["red"]["state"], "verified")

    def test_missing_and_ambiguous_role_mapping_rejected(self):
        for kind in ("missing", "different_folder", "same_channel", "ambiguous"):
            record = self.record()
            if kind == "missing":
                del record["analysis_channel_paths"]["red"]
            elif kind == "different_folder":
                record["analysis_channel_paths"]["green"] = str(self.folder / "unrecorded" / "green.tif")
            elif kind == "same_channel":
                record["analysis_channel_paths"]["green"] = record["analysis_channel_paths"]["red"]
            else:
                record["result"]["row"]["ebfp"] = record["analysis_channel_paths"]["green"]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                stack_source.prepare_stack(record, self.cache)
        self.assertFalse(self.cache.exists())

    def test_cancel_partial_memmaps_are_closed_and_only_own_staging_removed(self):
        record = self.record()
        self.cache.mkdir()
        sentinel = self.cache / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaises(leica.LeicaImportCancelled):
            stack_source.prepare_stack(record, self.cache, cancelled=lambda: len(self.image.reads) >= 1)
        self.assertEqual(list(self.cache.iterdir()), [sentinel])

    def test_source_change_during_decode_prevents_cache_publication(self):
        record = self.record()
        def progress(done, total):
            if done == 1:
                self.source.write_bytes(b"changed during decode")
        with self.assertRaisesRegex(ValueError, "changed while preparing"):
            stack_source.prepare_stack(record, self.cache, progress=progress)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_resource_guards_fail_without_partial_files(self):
        record = self.record()
        with patch.object(stack_source, "MAX_STACK_BYTES", 1), self.assertRaisesRegex(ValueError, "8 GiB"):
            stack_source.prepare_stack(record, self.cache)
        with patch.object(stack_source.shutil, "disk_usage", return_value=SimpleNamespace(free=0)), self.assertRaisesRegex(ValueError, "Not enough free"):
            stack_source.prepare_stack(record, self.cache)
        self.assertFalse(self.cache.exists())

    def test_cache_cannot_be_written_inside_completed_analysis(self):
        record = self.record()
        self.cache.mkdir()
        (self.cache / "run_manifest.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "outside completed"):
            stack_source.prepare_stack(record, self.cache / "stacks")


if __name__ == "__main__":
    unittest.main()
