"""Leica selection UI: explicit roles, coordinates and asynchronous handoff."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog
from desktop.leica_dialog import LeicaImportDialog
from desktop.services import default_config


class LeicaDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dialog = LeicaImportDialog(default_config(), Path(self.temp.name))
        self.catalog = {"source": {"path": "synthetic.lof", "size": 123, "mtime_ns": 456},
            "series": [{"index": 0, "name": "Synthetic stack", "path": "Synthetic stack",
                "sizes": {"C": 2, "Z": 3, "T": 2, "Y": 24, "X": 32}, "dtype": "uint16",
                "channels": [{"index": 0, "name": "Ch0", "bit_depth": 12},
                             {"index": 1, "name": "Ch1", "bit_depth": 12}],
                "pixel_size_um": [.5, .6], "supported": True, "reason": ""}]}

    def tearDown(self):
        self.dialog.reject()
        self.wait_for(lambda: self.dialog.worker is None)
        self.dialog.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    @staticmethod
    def preview(request, **_):
        values = np.full((24, 32), request["z_index"] + 1, dtype=np.uint16)
        return {"channels": {"green": values, "red": values * 2}}

    def test_z_coordinates_channel_confirmation_and_import_handoff(self):
        bundle = {"description": "Synthetic import", "row": {}, "input": {}}
        with patch("desktop.leica.inspect_leica", return_value=self.catalog), \
                patch("desktop.leica.preview_selection", side_effect=self.preview), \
                patch("desktop.leica.import_selection", return_value=bundle) as imported:
            self.dialog.open_file("synthetic.lof")
            self.wait_for(lambda: self.dialog._preview_current and self.dialog.worker is None)
            self.assertEqual(self.dialog.z_index.value(), 2)
            self.assertFalse(self.dialog.import_button.isEnabled())
            self.assertEqual(self.dialog.pixel_y.value(), .5)
            self.dialog.mode.setCurrentIndex(1)
            self.dialog.z_start.setValue(2)
            self.dialog.z_end.setValue(3)
            self.dialog.time.setValue(2)
            self.wait_for(lambda: self.dialog._preview_current and self.dialog.worker is None)
            request = self.dialog._request()
            self.assertEqual((request["z_start"], request["z_stop"], request["time_index"]), (1, 3, 1))
            self.assertEqual(request["mode"], "max")
            self.dialog.channel_confirmation.setChecked(True)
            self.dialog.import_button.click()
            self.wait_for(lambda: self.dialog.worker is None and self.dialog.result_bundle is not None)
            self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
            self.assertEqual(imported.call_args.args[0], request)

    def test_changed_selection_cannot_import_stale_preview_or_duplicate_channels(self):
        with patch("desktop.leica.inspect_leica", return_value=self.catalog), \
                patch("desktop.leica.preview_selection", side_effect=self.preview):
            self.dialog.open_file("synthetic.lof")
            self.wait_for(lambda: self.dialog._preview_current and self.dialog.worker is None)
            self.dialog.channel_confirmation.setChecked(True)
            self.dialog.z_index.setValue(3)
            self.assertFalse(self.dialog.import_button.isEnabled())
            old_token = self.dialog._token - 1
            self.dialog._received(old_token, "preview", self.preview({"z_index": 0}))
            self.assertFalse(self.dialog._preview_current)
            self.wait_for(lambda: self.dialog._preview_current and self.dialog.worker is None)
            self.assertEqual(self.dialog._request()["z_index"], 2)
            self.dialog.channels["red"].setCurrentIndex(0)
            self.assertFalse(self.dialog.channel_confirmation.isChecked())
            self.assertFalse(self.dialog.import_button.isEnabled())
            with self.assertRaisesRegex(ValueError, "different acquired channels"):
                self.dialog._request()


if __name__ == "__main__":
    unittest.main()
