"""Desktop regression checks with synthetic fixtures and real failing workers.

All writable fixtures are temporary. These tables are fabricated test records,
not measurements or substitutes for the separate bundled reference validation.
"""
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from desktop import services

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEventLoop, QProcess, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:
    QApplication = None
else:
    from desktop.app import MainWindow


class FilenameReviewTests(unittest.TestCase):
    def test_case_insensitive_field_ids_rejected_before_outputs_can_collide(self):
        rows = [
            {"image_id": "field1", "replicate_id": "gel1", "green": "green1.tif", "red": "red1.tif"},
            {"image_id": "FIELD1", "replicate_id": "gel2", "green": "green2.tif", "red": "red2.tif"},
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate field"):
            services.validate_rows(rows, check_files=False)

    def test_windows_reserved_ids_and_trailing_dots_rejected(self):
        for image_id in ("CON", "con", "NUL", "PRN", "AUX", "COM1", "LPT9", "CON.notes", "field1."):
            row = {"image_id": image_id, "replicate_id": "gel1", "green": "green.tif", "red": "red.tif"}
            with self.subTest(image_id=image_id), self.assertRaisesRegex(ValueError, "Windows filename"):
                services.validate_rows([row], check_files=False)


@unittest.skipIf(QApplication is None, "Install GUI requirements to run desktop integration tests")
class WindowReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.window = MainWindow()

    def tearDown(self):
        if self.window.process is not None:
            self.window.cancelled = True
            self.window.process.kill()
            self._wait_until(lambda: self.window.process is None, timeout_ms=10000)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def _wait_until(self, predicate, timeout_ms=30000):
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(20)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        poll.start()
        timeout.start(timeout_ms)
        if not predicate():
            loop.exec()
        poll.stop()
        timeout.stop()
        return predicate()

    def _result_fixture(self, name, bad_counts=False, bad_notes=False):
        path = self.root / name
        path.mkdir()
        (path / "run_manifest.json").write_text(json.dumps({
            "status": "completed", "reference_run": False,
            "warnings": [123] if bad_notes else ["Synthetic GUI test fixture."],
        }), encoding="utf-8")
        (path / "aggregate_summary.json").write_text(json.dumps({
            "viability_percent": {"mean": 100 / 3, "sample_sd": None, "n_replicates_with_defined_metric": 1},
            "ebfp_live_percent": {"mean": None, "sample_sd": None, "n_replicates_with_defined_metric": 0},
        }), encoding="utf-8")
        row = {"image_id": "synthetic_field", "replicate_id": "synthetic_replicate", "live_only": 1,
               "dead_only": 1, "double_positive": 1, "total": "not a count" if bad_counts else 3,
               "viability_percent": 100 / 3, "ebfp_live_percent": ""}
        for level in ("image", "replicate"):
            record = dict(row)
            if level == "replicate":
                del record["image_id"]
            with (path / (level + "_summary.csv")).open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(record))
                writer.writeheader()
                writer.writerow(record)
        Image.new("RGB", (24, 24), "white").save(path / "figure.png")
        return path

    def _table_contents(self):
        table = self.window.result_table
        return [[table.item(row, column).text() for column in range(table.columnCount())]
                for row in range(table.rowCount())]

    def test_malformed_run_keeps_previous_results_and_visible_measurements(self):
        valid = self._result_fixture("valid")
        self.window.load_result(valid)
        previous_result = self.window.result
        previous_table = self._table_contents()
        previous_title = self.window.result_title.text()
        previous_metrics = [(value.text(), detail.text()) for value, detail in self.window.metric_labels]
        previous_preview = self.window.preview_choice.currentData()
        for kind, options in (("counts", {"bad_counts": True}), ("notes", {"bad_notes": True})):
            with self.subTest(malformed=kind):
                invalid = self._result_fixture("invalid_" + kind, **options)
                with patch("desktop.app.QFileDialog.getExistingDirectory", return_value=str(invalid)), \
                        patch.object(self.window, "error") as error:
                    self.window.open_run()
                error.assert_called_once()
                self.assertIs(self.window.result, previous_result)
                self.assertEqual(self.window.result_title.text(), previous_title)
                self.assertEqual(self._table_contents(), previous_table)
                self.assertEqual([(value.text(), detail.text()) for value, detail in self.window.metric_labels], previous_metrics)
                self.assertEqual(self.window.preview_choice.currentData(), previous_preview)
                self.assertTrue(self.window.csv_button.isEnabled())

    def test_verify_invalid_output_parent_reports_error_without_starting_worker(self):
        self.window.load_result(self._result_fixture("valid"))
        not_a_folder = self.root / "plain_file.txt"
        not_a_folder.write_text("Preserve this existing file.", encoding="utf-8")
        self.window.output_base.setText(str(not_a_folder))
        with patch.object(self.window, "error") as error:
            self.window.verify_results()
        error.assert_called_once()
        self.assertIsNone(self.window.process)
        self.assertTrue(self.window.run_button.isEnabled())
        self.assertTrue(self.window.verify_button.isEnabled())
        self.assertEqual(not_a_folder.read_text(encoding="utf-8"), "Preserve this existing file.")

    def test_invalid_tiff_worker_reports_failure_and_leaves_gui_responsive(self):
        self.window.input_modes.setCurrentIndex(1)
        self.window.output_base.setText(str(self.root / "runs"))
        self.window.config["input"]["expected_shape"] = [32, 32]
        self.window.config["input"]["dtype"] = "uint8"
        for kind, dtype, shape, expected_message in (
            ("dtype", np.uint16, (32, 32), "dtype uint16 differs"),
            ("shape", np.uint8, (33, 32), "shape (33, 32) differs"),
        ):
            with self.subTest(invalid_input=kind):
                inputs = self.root / kind
                inputs.mkdir()
                channels = []
                for channel in ("green", "red"):
                    path = inputs / (channel + ".tif")
                    Image.fromarray(np.zeros(shape, dtype=dtype)).save(path)
                    channels.append(path)
                before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in channels}
                self.window.rows = [{"image_id": "synthetic", "replicate_id": "gel1", "green": str(channels[0]),
                                     "red": str(channels[1]), "ebfp": ""}]
                self.window.run_name.setText("bad_" + kind)
                ticks = []
                heartbeat = QTimer()
                heartbeat.setInterval(10)
                heartbeat.timeout.connect(lambda: ticks.append(1))
                heartbeat.start()
                with patch.object(self.window, "error") as error:
                    self.window.run_analysis()
                    self.assertIsNotNone(self.window.process)
                    self.assertFalse(self.window.run_button.isEnabled())
                    completed = self._wait_until(lambda: self.window.process is None)
                    heartbeat.stop()
                    self.assertTrue(completed, "Analysis worker did not return within 30 seconds")
                error.assert_called_once()
                self.assertIn(expected_message, str(error.call_args.args[0]))
                self.assertIn("Run failed", self.window.status_text.text())
                self.assertIsNone(self.window.result)
                self.assertTrue(self.window.run_button.isEnabled())
                self.assertTrue(self.window.reference_button.isEnabled())
                self.assertFalse(self.window.timer.isActive())
                self.assertGreater(len(ticks), 0, "GUI event loop must keep processing while worker runs")
                self.assertFalse((self.root / "runs" / ("bad_" + kind)).exists())
                self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in channels}, before)

    def test_cancellation_stops_worker_and_preserves_incomplete_output(self):
        partial = self.root / "partial_run"
        partial.mkdir()
        manifest = partial / "run_manifest.json"
        manifest.write_text('{"status":"running"}', encoding="utf-8")
        # A real waiting subprocess isolates QProcess cancellation from the
        # duration of scientific calculations; the pipeline itself is unchanged.
        command = (sys.executable, ["-c", "import time; time.sleep(30)"])
        with patch("desktop.app.process_command", return_value=command), \
                patch("desktop.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(self.window, "error") as error:
            self.window.start_job("analyze", [], partial, self.root / "cancel.log")
            self.assertTrue(self._wait_until(lambda: self.window.process is not None
                                             and self.window.process.state() == QProcess.ProcessState.Running))
            self.window.cancel_job()
            self.assertTrue(self._wait_until(lambda: self.window.process is None))
        error.assert_not_called()
        self.assertIn("Run stopped", self.window.status_text.text())
        self.assertTrue(self.window.run_button.isEnabled())
        self.assertFalse(self.window.timer.isActive())
        self.assertEqual(manifest.read_text(encoding="utf-8"), '{"status":"running"}')
        with self.assertRaisesRegex(ValueError, "incomplete"):
            services.read_results(partial)


if __name__ == "__main__":
    unittest.main()
