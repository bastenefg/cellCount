"""Opt-in source/frozen-app validation using a clearly synthetic 3D fixture."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
import traceback


def synthetic_stack(folder):
    import numpy as np
    folder.mkdir(parents=True, exist_ok=False)
    shape, spacing = (28, 128, 128), (2.0, 1.0, 1.0)
    z, y, x = np.indices(shape, dtype=np.float32)
    arrays = {role: np.zeros(shape, np.uint8) for role in ("green", "red")}
    # The first green/red pair has identical XY coordinates but different Z.
    # The second pair shares a center, with nuclear signal inside cytoplasmic signal.
    centers = {"green": [(6, 32, 32), (14, 80, 82), (14, 94, 28)],
               "red": [(21, 32, 32), (14, 80, 82), (14, 25, 100)]}
    for role, points in centers.items():
        for index, (cz, cy, cx) in enumerate(points):
            radius = 4 if role == "red" and index == 1 else 6
            mask = ((z - cz) * spacing[0])**2 + (y - cy)**2 + (x - cx)**2 <= radius**2
            arrays[role][mask] = 210 if role == "green" else 180
        np.save(folder / f"{role}.npy", arrays[role], allow_pickle=False)
    info = {"paths": {role: str((folder / f"{role}.npy").resolve()) for role in arrays},
            "shape_zyx": list(shape), "spacing_um": list(spacing), "dtype": "uint8",
            "z_positions_um": [float(i * spacing[0]) for i in range(shape[0])],
            "z_indices": list(range(shape[0])), "display": {role: [0, 255] for role in arrays},
            "cache_key": "synthetic-stack-smoke-v1", "field_id": "Synthetic depth example",
            "source": {"description": "Fabricated test volumes, not microscopy data"},
            "selection": {"mode": "max", "z_start": 0, "z_stop": shape[0], "time_index": 0}}
    settings = {"min_seed_distance_um": 8.0, "match_distance_um": 0.0, "exclude_border": False}
    for role in arrays:
        settings[role] = {"low": 100.0, "high": 150.0, "sigma_um": 0.0, "min_volume_um3": 30.0}
    return info, settings


def main(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-smoke-test", type=Path, required=True)
    args = parser.parse_args(arguments)
    output = args.stack_smoke_test.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["MPLBACKEND"] = "Agg"
    report = {"status": "failed", "fixture": "Synthetic two-channel 3D shapes; no biological validation implied",
              "timings_seconds": {}}
    dialog = None
    app = None
    try:
        import numpy as np
        from unittest.mock import patch
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtWidgets import QApplication, QFileDialog
        from .app import STYLE
        from .stack_dialog import StackReviewDialog

        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(STYLE)
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf"):
            if (fonts / name).exists():
                QFontDatabase.addApplicationFont(str(fonts / name))
        app.setFont(QFont("Segoe UI", 10))
        info, settings = synthetic_stack(output / "inputs")
        original_hashes = {role: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                           for role, path in info["paths"].items()}
        dialog = StackReviewDialog(config={"leica_imports": []}, output_base=output,
                                   cache_dir=output / "cache")
        dialog.setWindowTitle("Z review — synthetic validation example")
        dialog.resize(1400, 920)
        dialog.fields.addItem(info["field_id"], None)
        dialog.accept_stack_info({**deepcopy(info), "_ui_defaults": settings})
        assert dialog.arrays, dialog.status.text()
        dialog.show()
        app.processEvents()
        dialog._pick_xy(32.5 / 128, 32.5 / 128)
        assert np.argmax(dialog.profile.profiles["green"]) < np.argmax(dialog.profile.profiles["red"])
        profile_ids = {role: id(value) for role, value in dialog.profile.profiles.items()}
        samples = []
        for index in range(100):
            started = time.perf_counter()
            dialog.z_slider.setValue(index % 28)
            app.processEvents()
            samples.append(time.perf_counter() - started)
        assert profile_ids == {role: id(value) for role, value in dialog.profile.profiles.items()}, "Z scrubbing recalculated depth profiles"
        report["timings_seconds"].update({"slice_change_median": float(np.median(samples)),
                                           "slice_change_p95": float(np.percentile(samples, 95))})
        assert all(view.pixmap is not None and not view.pixmap.isNull() for view in dialog.views.values())
        dialog.z_slider.setValue(6)
        dialog.decision.setCurrentText("Separate in Z")
        dialog.note.setText("Synthetic green and red cells share XY and occupy different depths.")
        dialog._record_review()
        review_path = output / "manual_review.json"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(review_path), "Review JSON (*.json)")):
            dialog.save_review()
        review = json.loads(review_path.read_text(encoding="utf-8"))
        assert any(item["decision"] == "Separate in Z" for item in review["reviews"])
        dialog.grab().save(str(output / "synthetic_z_review.png"))

        ticks = []
        heartbeat = QTimer()
        heartbeat.setInterval(20)
        heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
        heartbeat.start()
        started = time.perf_counter()
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(output)):
            dialog.start_analysis()
        assert dialog.process is not None, dialog.status.text()
        deadline = time.monotonic() + 120
        while dialog.process is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        heartbeat.stop()
        assert dialog.process is None, "3D worker timed out"
        assert dialog.result is not None, dialog.status.text()
        assert len(ticks) >= 2, "GUI event loop stopped during 3D analysis"
        report["timings_seconds"]["3d_worker_with_startup_and_figure"] = time.perf_counter() - started
        result = deepcopy(dialog.result)
        summary = json.loads(Path(result["summary"]).read_text())
        counts = summary["counts"]
        assert {k: counts[k] for k in ("green_only", "red_only", "dual_positive_candidate", "unresolved")} == {
            "green_only": 2, "red_only": 2, "dual_positive_candidate": 1, "unresolved": 0}, counts
        assert counts["total_groups"] == 5
        assert json.loads(Path(result["settings"]).read_text()) == settings
        assert dialog.labels, "Completed 3D masks are not available for visual review"
        for role, path in info["paths"].items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == original_hashes[role]
        figure_copy = output / "exported_3d_figure.png"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(figure_copy), "PNG image (*.png)")):
            dialog.save_volume_figure()
        assert figure_copy.read_bytes() == Path(result["figure"]).read_bytes()
        dialog.thresholds["green", "low"].number.setValue(101)
        assert not dialog.labels, "Old 3D masks stayed visible after parameter edits"
        assert json.loads(Path(result["settings"]).read_text()) == settings
        dialog.thresholds["green", "low"].number.setValue(100)
        dialog.tabs.setCurrentIndex(1)
        app.processEvents()
        dialog.grab().save(str(output / "synthetic_3d_controls.png"))
        report.update(status="passed", counts=counts, gui_heartbeat_ticks=len(ticks),
                      checks=["Vertically separated same-XY cells remain separate", "Spanning-Z objects counted once",
                              "Shared-center dual signal retained as candidate", "Saved exact 3D settings and masks",
                              "Responsive GUI during worker", "Cached slice navigation does not recalculate depth profiles",
                              "Independent manual review export", "Byte-identical figure export", "Source volumes preserved",
                              "Parameter edits remove stale mask overlays"])
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if dialog is not None:
            dialog.reject()
            deadline = time.monotonic() + 10
            while (dialog.worker is not None or dialog.process is not None) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1
