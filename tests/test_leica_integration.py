"""Synthetic TIFF fixtures verify Leica import provenance and GUI integration."""
from copy import deepcopy
import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from desktop.app import MainWindow
from desktop.services import (config_with_imports, default_config, discover_leica_imports,
                              prepare_analysis, read_manifest, write_manifest)


class LeicaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def bundle(self, name, pixel_size=(.4, .5), mode="max"):
        folder = self.folder / name
        folder.mkdir()
        row = {"image_id": "Leica_field", "replicate_id": "sample_1", "ebfp": ""}
        exports = {}
        for index, role in enumerate(("green", "red")):
            path = folder / (role + ".tif")
            Image.fromarray(np.full((24, 32), 100 + index, dtype=np.uint16)).save(path)
            row[role] = str(path)
            exports[role] = {"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             "bytes": path.stat().st_size, "shape": [24, 32], "dtype": "uint16"}
        result = {"row": row, "input": {"expected_shape": [24, 32], "dtype": "uint16", "pixel_size_um": list(pixel_size)},
                  "display": {"green": [0, 1000], "red": [0, 2000]},
                  "provenance_path": str(folder / "import_provenance.json"),
                  "description": "Synthetic series · maximum projection Z 1–3"}
        record = {"schema_version": 1, "reader": {"name": "liffile", "version": "synthetic"},
                  "source": {"path": "synthetic.lif", "identity_method": "Synthetic fixture; no microscopy source."},
                  "selection": {"mode": mode, "channels": {"green": 0, "red": 1, "ebfp": None}},
                  "exports": exports, "result": result}
        Path(result["provenance_path"]).write_text(json.dumps(record), encoding="utf-8")
        return result

    def test_quick_import_uses_calibration_without_changing_batch_preset(self):
        bundle = self.bundle("quick")
        before = deepcopy(self.window.config)
        self.window.quick_start.set_imported(bundle)
        rows, config = self.window.analysis_inputs()
        self.assertEqual(rows, [bundle["row"]])
        self.assertEqual(config["input"], {**before["input"], **bundle["input"]})
        self.assertEqual(config["display"]["red"], [0, 2000])
        self.assertEqual(self.window.config, before)
        self.assertEqual(config["leica_imports"][0]["analysis_field_id"], "Leica_field")
        self.window.quick_start.swap_channels()
        swapped, config = self.window.analysis_inputs()
        self.assertEqual(swapped[0]["green"], bundle["row"]["red"])
        self.assertEqual(config["display"]["green"], [0, 2000])
        self.assertEqual(config["input"]["pixel_size_um"], [.4, .5])

    def test_plain_tiff_replacement_clears_previous_import_identity(self):
        self.window.quick_start.set_imported(self.bundle("imported"))
        for role in ("green", "red"):
            path = self.folder / (role + ".tif")
            Image.fromarray(np.zeros((18, 20), np.uint8)).save(path)
            self.window.quick_start.set_path(role, path)
        rows, config = self.window.analysis_inputs()
        self.assertEqual(rows[0]["image_id"], "field_1")
        self.assertNotIn("leica_imports", config)
        self.assertIsNone(self.window.quick_start.imported)
        self.assertEqual(config["input"]["pixel_size_um"], default_config()["input"]["pixel_size_um"])

    def test_batch_adopts_first_calibration_and_assigns_distinct_ids(self):
        first, second = self.bundle("one"), self.bundle("two")
        self.window.append_leica_import(first)
        self.window.append_leica_import(second)
        self.assertEqual([row["image_id"] for row in self.window.rows], ["Leica_field", "Leica_field_2"])
        self.assertEqual([row["replicate_id"] for row in self.window.rows], ["sample_1", "sample_1_2"])
        self.assertEqual(self.window.config["input"]["pixel_size_um"], [.4, .5])
        before = deepcopy(self.window.rows)
        with self.assertRaisesRegex(ValueError, "different size, bit depth or pixel calibration"):
            self.window.append_leica_import(self.bundle("incompatible", pixel_size=(1, 1)))
        self.assertEqual(self.window.rows, before)

    def test_csv_reload_and_analysis_snapshot_keep_verified_provenance(self):
        bundle = self.bundle("csv_source")
        manifest = self.folder / "samples.csv"
        write_manifest(manifest, [bundle["row"]])
        rows = read_manifest(manifest)
        config = config_with_imports(rows, default_config())
        before = deepcopy(config)
        out, arguments, _ = prepare_analysis(self.folder / "runs", "snapshot", rows, config)
        snapshot = json.loads(Path(arguments[arguments.index("--config") + 1]).read_text(encoding="utf-8"))
        self.assertEqual(snapshot["leica_imports"][0]["selection"]["mode"], "max")
        self.assertEqual(snapshot["input"]["pixel_size_um"], [.4, .5])
        self.assertEqual(snapshot["leica_imports"][0]["analysis_channel_paths"]["green"], rows[0]["green"])
        self.assertFalse(out.exists())
        self.assertEqual(config, before)
        with patch("desktop.app.QFileDialog.getOpenFileName", return_value=(str(manifest), "CSV manifests (*.csv)")):
            self.window.import_csv()
        self.assertEqual(self.window.config["input"]["pixel_size_um"], [.4, .5])

    def test_changed_imported_tiff_is_rejected_before_snapshot_creation(self):
        bundle = self.bundle("tampered")
        path = Path(bundle["row"]["green"])
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "Imported TIFF changed"):
            prepare_analysis(self.folder / "runs", "blocked", [bundle["row"]], default_config())
        self.assertFalse((self.folder / "runs").exists())

    def test_missing_sidecar_cannot_silently_drop_projection_provenance(self):
        bundle = self.bundle("missing_record")
        config = config_with_imports([bundle["row"]], default_config())
        self.window.quick_start.set_imported(bundle)
        Path(bundle["provenance_path"]).unlink()
        with self.assertRaisesRegex(ValueError, "import record is missing"):
            self.window.analysis_inputs()
        with self.assertRaisesRegex(ValueError, "import record is missing"):
            prepare_analysis(self.folder / "runs", "blocked", [bundle["row"]], config)
        self.assertFalse((self.folder / "runs").exists())

    def test_results_identify_projection_as_two_dimensional_counts(self):
        bundle = self.bundle("projection")
        run = self.folder / "completed"
        run.mkdir()
        (run / "run_manifest.json").write_text(json.dumps({"status": "completed", "warnings": []}))
        metrics = {key: {"mean": None, "sample_sd": None} for key in ("viability_percent", "ebfp_live_percent")}
        (run / "aggregate_summary.json").write_text(json.dumps(metrics))
        row = {"image_id": "Leica_field", "replicate_id": "sample_1", "live_only": 0, "dead_only": 0,
               "double_positive": 0, "total": 0}
        for level in ("image", "replicate"):
            with (run / (level + "_summary.csv")).open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
        (run / "effective_config.json").write_text(json.dumps({"leica_imports": discover_leica_imports([bundle["row"]])}))
        Image.new("RGB", (24, 24), "white").save(run / "figure.png")
        self.window.load_result(run)
        self.assertIn("2D maximum-intensity projection", self.window.result_notes.toPlainText())
        self.assertIn("not a 3D cell count", self.window.result_notes.toPlainText())


if __name__ == "__main__":
    unittest.main()
