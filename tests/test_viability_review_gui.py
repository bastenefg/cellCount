"""Count-affecting Z reviews use saved objects without segmentation or I/O scans."""
from copy import deepcopy
import csv
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QDialog
except ImportError:
    QApplication = None
else:
    from desktop.stack_dialog import PairReviewDialog, StackReviewDialog
    from desktop.viability_3d import summarize_objects


@unittest.skipIf(QApplication is None, "Requires desktop dependencies")
class ViabilityReviewGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.objects = [
            {"object_id": "1", "status": "green_only", "green_ids": "1", "red_ids": "", "z": "2", "y": "12", "x": "10"},
            {"object_id": "2", "status": "red_only", "green_ids": "", "red_ids": "1", "z": "4", "y": "18", "x": "25"},
            {"object_id": "3", "status": "dual_positive_candidate", "green_ids": "2", "red_ids": "2", "z": "3", "y": "14", "x": "16"},
            {"object_id": "4", "status": "unresolved", "green_ids": "3", "red_ids": "3;4", "z": "4", "y": "20", "x": "20"},
        ]
        self.info = {"paths": {}, "shape_zyx": [9, 32, 40], "spacing_um": [2, 1, 1],
            "z_indices": list(range(9)), "field_id": "synthetic", "display": {"green": [0, 255], "red": [0, 255]}}
        self.result = {"objects": str(self.root / "objects.csv")}
        with Path(self.result["objects"]).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.objects[0]))
            writer.writeheader()
            writer.writerows(self.objects)
        for role, count in (("green", 3), ("red", 4)):
            data = np.zeros(self.info["shape_zyx"], dtype=np.uint8)
            labels = np.zeros_like(data, dtype=np.uint32)
            for ident in range(1, count + 1):
                data[ident, 10:15, ident * 4:ident * 4 + 3] = 200
                labels[ident, 10:15, ident * 4:ident * 4 + 3] = ident
            source = self.root / (role + ".npy")
            mask = self.root / ("labels_" + role + ".npy")
            np.save(source, data)
            np.save(mask, labels)
            self.info["paths"][role] = str(source)
            self.result["labels_" + role] = str(mask)
            with (self.root / (role + "_objects.csv")).open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["id", "z", "y", "x"])
                writer.writeheader()
                writer.writerows({"id": ident, "z": ident, "y": 12, "x": ident * 4 + 1} for ident in range(1, count + 1))
        self.dialog = self.make_dialog({})
        self.applied = []
        self.dialog.reviewApplied.connect(self.accept_review)

    def make_dialog(self, decisions):
        dialog = StackReviewDialog(config={}, output_base=self.root, cache_dir=self.root / "cache", review_decisions=decisions)
        dialog.accept_stack_info(deepcopy(self.info))
        dialog.result = deepcopy(self.result)
        dialog._load_labels()
        dialog._populate_objects()
        dialog._set_ready(True)
        return dialog

    def accept_review(self, value):
        self.applied.append(value)
        self.dialog.acknowledge_review_saved(True)

    def tearDown(self):
        self.dialog.review_decisions = deepcopy(self.dialog._saved_decisions)
        self.dialog._count_target = None
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def select(self, ident):
        for index in range(1, self.dialog.object_choice.count()):
            if self.dialog.object_choice.itemData(index)["object_id"] == str(ident):
                self.dialog.object_choice.setCurrentIndex(index)
                return
        self.fail("Missing candidate")

    def decide(self, name):
        self.dialog.decision.setCurrentIndex(self.dialog.decision.findData(name))

    def test_simple_same_cell_and_separate_update_without_segmentation(self):
        self.select(3)
        with patch("desktop.volume_analysis.analyze_volume", side_effect=AssertionError("Must not resegment")), \
             patch("numpy.load", side_effect=AssertionError("Must not reopen volume")):
            self.decide("same_cell")
            self.dialog.note.setText("Supported continuity in optical sections")
            self.assertTrue(self.dialog.apply_to_results())
            metrics = summarize_objects(self.objects, self.applied[-1])
            self.assertEqual(metrics["paired_min"], 1)
            self.assertEqual(metrics["pending_groups"], 1)
            self.decide("separate")
            self.assertTrue(self.dialog.apply_to_results())
            self.assertEqual(summarize_objects(self.objects, self.applied[-1])["paired_min"], 0)
        self.assertIsNone(self.dialog.process)
        self.assertEqual(self.applied[-1]["3"]["note"], "Supported continuity in optical sections")

    def test_complex_group_disables_whole_group_merge_and_accepts_explicit_pair(self):
        self.select(4)
        item = self.dialog.decision.model().item(self.dialog.decision.findData("same_cell"))
        self.assertFalse(item.isEnabled())
        with patch("desktop.stack_dialog.PairReviewDialog") as editor:
            editor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            editor.return_value.pairs = [[3, 4]]
            self.dialog._edit_pairs()
        self.assertTrue(self.dialog.apply_to_results())
        self.assertEqual(self.applied[-1]["4"]["pairs"], [[3, 4]])
        self.assertIn("G3 ↔ R4", self.dialog.review_progress.text())
        self.assertEqual(summarize_objects(self.objects, self.applied[-1])["paired_min"], 1)

    def test_pair_editor_requires_confirmation_and_unique_dead_membership(self):
        obj = {"green_ids": "2;3", "red_ids": "3;4"}
        editor = PairReviewDialog(obj)
        try:
            self.assertFalse(editor.ok.isEnabled())
            editor.choices[2].setCurrentIndex(editor.choices[2].findData(3))
            editor.choices[3].setCurrentIndex(editor.choices[3].findData(3))
            editor.confirm.setChecked(True)
            self.assertFalse(editor.ok.isEnabled())
            self.assertIn("only one pair", editor.message.text())
            editor.choices[3].setCurrentIndex(editor.choices[3].findData(4))
            self.assertTrue(editor.ok.isEnabled())
            editor.accept()
            self.assertEqual(editor.pairs, [[2, 3], [3, 4]])
        finally:
            editor.deleteLater()

    def test_cancel_pair_edit_restores_previous_decision(self):
        self.select(4)
        self.decide("uncertain")
        with patch("desktop.stack_dialog.PairReviewDialog") as editor:
            editor.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.decide("pairs")
        self.assertEqual(self.dialog.decision.currentData(), "uncertain")
        self.assertEqual(self.dialog.review_decisions["4"]["decision"], "uncertain")

    def test_restored_decisions_and_notes_survive_selection_changes(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.dialog = self.make_dialog({"3": {"decision": "same_cell", "pairs": [], "note": "same cell"},
                                        "4": {"decision": "pairs", "pairs": [["3", "4"]], "note": "second red is separate"}})
        self.select(3)
        self.assertEqual(self.dialog.decision.currentData(), "same_cell")
        self.assertEqual(self.dialog.note.text(), "same cell")
        self.select(4)
        self.assertEqual(self.dialog.decision.currentData(), "pairs")
        self.assertEqual(self.dialog.note.text(), "second red is separate")
        self.select(3)
        self.assertEqual(self.dialog.review_decisions, self.dialog._saved_decisions)

    def test_focus_member_uses_saved_centroid_and_retains_candidate(self):
        self.select(4)
        self.dialog.member_focus.setCurrentIndex(self.dialog.member_focus.findText("DEAD R4"))
        self.assertEqual((self.dialog.x, self.dialog.y, self.dialog.z_slider.value()), (17, 12, 4))
        self.assertEqual(self.dialog._review_target, "3d_4")
        self.assertEqual(self.dialog._count_target["object_id"], "4")
        self.assertIn("DEAD R4", self.dialog.status.text())

    def test_close_saves_pending_decisions_before_releasing_volumes(self):
        self.select(3)
        self.decide("same_cell")
        self.dialog.reject()
        self.assertEqual(self.applied[-1]["3"]["decision"], "same_cell")
        self.assertFalse(self.dialog.arrays)

    def test_failed_persistence_prevents_close_and_preserves_edits(self):
        self.dialog.reviewApplied.disconnect(self.accept_review)
        self.dialog.reviewApplied.connect(lambda value: self.dialog.acknowledge_review_saved(False, "Disk unavailable"))
        self.select(3)
        self.decide("same_cell")
        self.dialog.reject()
        self.assertTrue(self.dialog.arrays)
        self.assertFalse(self.dialog._closing)
        self.assertIn("Disk unavailable", self.dialog.status.text())
        self.assertEqual(self.dialog.review_decisions["3"]["decision"], "same_cell")
        self.assertEqual(self.dialog._saved_decisions, {})


if __name__ == "__main__":
    unittest.main()
