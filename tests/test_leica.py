"""Leica import preserves selected pixels, axis meaning, and immutable exports."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image

from desktop import leica


class FakeImage:
    def __init__(self, dtype=np.uint16, frame_axes=("Y", "X")):
        self.name = "Series 1"
        self.path = "Experiment/Series 1"
        self.is_flim = False
        self.tilescan = None
        self.dtype = np.dtype(dtype)
        self.sizes = {"T": 2, "Z": 3, "C": 3, "Y": 4, "X": 5}
        self.frames = SimpleNamespace(frame_sizes={axis: self.sizes[axis] for axis in frame_axes})
        self.coords = {"C": np.array(["LIVE dye", "DEAD dye", "EBFP"])}
        self.xml_element = ET.Element("Element")
        description = ET.SubElement(ET.SubElement(ET.SubElement(self.xml_element, "Data"), "Image"), "ImageDescription")
        dimensions = ET.SubElement(description, "Dimensions")
        for axis, dim_id in (("X", "1"), ("Y", "2"), ("Z", "3"), ("T", "4")):
            ET.SubElement(dimensions, "DimensionDescription", DimID=dim_id, NumberOfElements=str(self.sizes[axis]),
                          Length=str((self.sizes[axis] - 1) * 0.5e-6), Origin="0", Unit="m", BitInc="0")
        channels = ET.SubElement(description, "Channels")
        for index in range(3):
            ET.SubElement(channels, "ChannelDescription", Resolution="12" if self.dtype == np.uint16 else "8",
                          DataType="0", BytesInc=str(index * 20), BitInc="0", LUTName=("Green", "Red", "Blue")[index])
        self.reads = []

    def pixels(self, channel, z, time):
        yy, xx = np.indices((4, 5))
        # Vary the brightest Z per pixel so a last-slice shortcut is detectable.
        value = channel * 70 + time * 5 + yy * 2 + xx + (30 if z == 0 else 0) * (xx % 2) + z * 7
        if self.dtype == np.uint16:
            value = value * 10 + 1024
        return value.astype(self.dtype)

    def frame(self, **indices):
        self.reads.append(indices)
        pixels = self.pixels(indices.get("C", 0), indices.get("Z", 0), indices.get("T", 0))
        return pixels if list(self.frames.frame_sizes) == ["Y", "X"] else pixels.T

    def asarray(self, *args, **kwargs):
        raise AssertionError("Import must never load the full Leica series.")


class FakeContainer:
    def __init__(self, image):
        self.images = [image]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class LeicaImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source = self.folder / "experiment.lif"
        self.source.write_bytes(b"source unchanged")
        self.output = self.folder / "imports"
        self.image = FakeImage()
        self.reader = patch.object(leica.liffile, "LifFile", side_effect=lambda *args, **kwargs: FakeContainer(self.image))
        self.mock_reader = self.reader.start()

    def tearDown(self):
        self.reader.stop()
        self.temp.cleanup()

    def request(self, **overrides):
        request = {"source": str(self.source), "source_identity": leica.inspect_leica(self.source)["source"],
                   "series_index": 0, "channels": {"green": 0, "red": 1, "ebfp": None}, "mode": "slice",
                   "z_index": 1, "time_index": 1, "pixel_size_um": [0.6, 0.7]}
        request.update(overrides)
        return request

    def test_catalog_is_metadata_only_and_retains_physical_pixel_pitch(self):
        catalog = leica.inspect_leica(self.source)
        info = catalog["series"][0]
        self.assertEqual(self.image.reads, [])
        self.assertTrue(info["supported"], info["reason"])
        self.assertEqual(info["pixel_size_um"], [0.5, 0.5])
        self.assertEqual(info["channels"][1]["name"], "DEAD dye")
        self.assertEqual(info["channels"][1]["bit_depth"], 12)
        self.mock_reader.assert_called_with(str(self.source.resolve()), memmap=True, squeeze=False)
        json.dumps(catalog, allow_nan=False)

    def test_slice_reads_only_selected_channels_and_preserves_uint16(self):
        result = leica.preview_selection(self.request())
        self.assertEqual(len(self.image.reads), 2)
        for role, channel in (("green", 0), ("red", 1)):
            np.testing.assert_array_equal(result["channels"][role], self.image.pixels(channel, 1, 1))
            self.assertEqual(result["channels"][role].dtype, np.uint16)
        self.assertEqual(result["display"], {"green": [0, 4095], "red": [0, 4095]})
        self.assertEqual(self.source.read_bytes(), b"source unchanged")

    def test_reversed_frame_axes_are_transposed_to_yx(self):
        self.image = FakeImage(frame_axes=("X", "Y"))
        result = leica.preview_selection(self.request())
        np.testing.assert_array_equal(result["channels"]["red"], self.image.pixels(1, 1, 1))
        self.assertEqual(result["channels"]["red"].shape, (4, 5))

    def test_projection_streams_only_selected_z_range_and_time(self):
        request = self.request(mode="max", z_start=0, z_stop=2, time_index=0,
                               channels={"green": 2, "red": 0, "ebfp": 1})
        progress = []
        result = leica.import_selection(request, self.output, progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(len(self.image.reads), 6)
        self.assertEqual(progress[-1], (6, 6))
        for role, channel in request["channels"].items():
            with Image.open(result["row"][role]) as image:
                np.testing.assert_array_equal(np.asarray(image), np.maximum(self.image.pixels(channel, 0, 0), self.image.pixels(channel, 1, 0)))
        record = json.loads(Path(result["provenance_path"]).read_text())
        self.assertEqual(record["selection"]["z_stop"], 2)
        self.assertIn("not a 3D cell count", record["dimensionality"])
        self.assertEqual(result["input"]["pixel_size_um"], [0.6, 0.7])
        self.assertIn("not SHA256 hashed", record["source"]["identity_method"])

    def test_preview_decimation_cannot_change_imported_full_resolution_pixels(self):
        request = self.request(preview_max_size=2)
        preview = leica.preview_selection(request)
        self.assertEqual(preview["preview_stride"], 3)
        np.testing.assert_array_equal(preview["channels"]["green"], self.image.pixels(0, 1, 1)[::3, ::3])
        imported = leica.import_selection(request, self.output)
        with Image.open(imported["row"]["green"]) as image:
            np.testing.assert_array_equal(np.asarray(image), self.image.pixels(0, 1, 1))

    def test_reimport_reuses_only_hash_verified_complete_exports(self):
        request = self.request()
        first = leica.import_selection(request, self.output)
        read_count = len(self.image.reads)
        second = leica.import_selection(request, self.output)
        self.assertTrue(second["cached"])
        self.assertEqual(first["row"], second["row"])
        self.assertEqual(len(self.image.reads), read_count)
        green = Path(first["row"]["green"])
        green.write_bytes(b"modified export")
        third = leica.import_selection(request, self.output)
        self.assertFalse(third["cached"])
        self.assertNotEqual(third["provenance_path"], first["provenance_path"])
        self.assertEqual(green.read_bytes(), b"modified export")

    def test_distinct_channel_roles_required(self):
        for channels in ({"green": 0, "red": 0}, {"green": 0, "red": 1, "ebfp": 1},
                         {"green": 0}, {"green": 0, "red": 3}, {"green": 0, "red": True}):
            with self.subTest(channels=channels), self.assertRaises(ValueError):
                leica.preview_selection(self.request(channels=channels))
        self.assertEqual(self.image.reads, [])

    def test_invalid_z_time_and_calibration_rejected_before_reading(self):
        for overrides in ({"z_index": 3}, {"z_index": -1}, {"mode": "max", "z_start": 2, "z_stop": 2},
                          {"mode": "max", "z_start": 0, "z_stop": 4}, {"time_index": 2},
                          {"pixel_size_um": None}, {"pixel_size_um": [0, 1]},
                          {"pixel_size_um": [float("nan"), 1]}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                leica.import_selection(self.request(**overrides), self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.image.reads, [])

    def test_missing_calibration_still_allows_visual_preview(self):
        request = self.request(pixel_size_um=None)
        self.assertEqual(leica.preview_selection(request)["selection"]["pixel_size_um"], [0.5, 0.5])

    def test_changed_source_identity_rejected(self):
        request = self.request()
        self.source.write_bytes(b"modified source")
        with self.assertRaisesRegex(ValueError, "changed since"):
            leica.import_selection(request, self.output)
        self.assertEqual(self.image.reads, [])

    def test_source_change_during_read_never_produces_export(self):
        def progress(done, total):
            if done == 1:
                self.source.write_bytes(b"changed during read")
        with self.assertRaisesRegex(ValueError, "changed during import"):
            leica.import_selection(self.request(), self.output, progress=progress)
        self.assertFalse(self.output.exists())

    def test_cancellation_during_projection_stops_before_remaining_planes(self):
        with self.assertRaises(leica.LeicaImportCancelled):
            leica.import_selection(self.request(mode="max", z_start=0, z_stop=3), self.output,
                                   cancelled=lambda: len(self.image.reads) >= 2)
        self.assertEqual(len(self.image.reads), 2)
        self.assertFalse(self.output.exists())

    def test_failed_tiff_save_cleans_only_its_partial_directory(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep")
        original_save = Image.Image.save
        def save(image, path, *args, **kwargs):
            if Path(path).name == "red.tif":
                raise OSError("disk full")
            return original_save(image, path, *args, **kwargs)
        with patch.object(Image.Image, "save", save), self.assertRaisesRegex(OSError, "disk full"):
            leica.import_selection(self.request(), self.output)
        self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_unsupported_axes_rgb_float_bitpacking_and_tiles_are_reported(self):
        for kind in ("axis", "rgb", "float", "bits", "tiles", "no_y"):
            self.image = FakeImage()
            if kind == "axis":
                self.image.sizes["L"] = 2
            elif kind == "rgb":
                self.image.sizes["S"] = 3
            elif kind == "float":
                self.image.dtype = np.dtype(np.float32)
            elif kind == "bits":
                self.image.xml_element.find(".//ChannelDescription").set("BitInc", "1")
            elif kind == "tiles":
                self.image.tilescan = [0, 1]
            elif kind == "no_y":
                del self.image.sizes["Y"]
            with self.subTest(kind=kind):
                info = leica.inspect_leica(self.source)["series"][0]
                self.assertFalse(info["supported"])
                self.assertTrue(info["reason"])
                self.assertEqual(self.image.reads, [])

    def test_single_tile_metadata_is_not_mistaken_for_unstitched_mosaic(self):
        self.image.tilescan = [0]
        self.assertTrue(leica.inspect_leica(self.source)["series"][0]["supported"])

    def test_cache_key_includes_role_mapping_calibration_z_and_time(self):
        request = self.request()
        original = deepcopy(request)
        locations = [leica.import_selection(request, self.output)["provenance_path"]]
        for override in ({"z_index": 0}, {"time_index": 0}, {"pixel_size_um": [1, 1]},
                         {"channels": {"green": 1, "red": 0, "ebfp": None}}):
            modified = deepcopy(request)
            modified.update(override)
            locations.append(leica.import_selection(modified, self.output)["provenance_path"])
        self.assertEqual(len(locations), len(set(locations)))
        self.assertEqual(request, original)

    def test_imports_cannot_modify_completed_run(self):
        completed = self.folder / "completed"
        completed.mkdir()
        (completed / "run_manifest.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "outside completed"):
            leica.import_selection(self.request(), completed / "imports")


if __name__ == "__main__":
    unittest.main()
