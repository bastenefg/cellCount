"""Opt-in source/frozen validation of the complete synthetic 3D review workflow."""
import argparse
from copy import deepcopy
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import time
import traceback


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _snapshot(folder):
    return {path.relative_to(folder).as_posix(): _hash(path)
            for path in sorted(folder.rglob("*")) if path.is_file()}


def _json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _synthetic_run(output):
    """Wrap an actual synthetic volume analysis in the batch run format."""
    from .analysis_3d import COUNT_KEYS
    from .stack_smoke import synthetic_stack
    from .volume_analysis import analyze_volume

    info, settings = synthetic_stack(output / "inputs")
    info["field_id"] = "synthetic"
    run = output / "analysis"
    field_path = run / "fields" / "synthetic"
    analyze_volume(info, settings, field_path)
    summary = json.loads((field_path / "summary.json").read_text(encoding="utf-8"))
    counts = summary["counts"]
    expected = {"green_only": 2, "red_only": 2, "dual_positive_candidate": 1, "unresolved": 0}
    assert {key: counts[key] for key in expected} == expected, counts
    metadata = {"schema_version": 1, "analysis_mode": "3d", "status": "completed", "warnings": [],
                "fields": [{"image_id": "synthetic", "replicate_id": "synthetic_rep1", "relative_path": "fields/synthetic"}]}
    _json(run / "volume_run.json", metadata)
    _json(run / "effective_config.json", {})
    _json(run / "summary.json", {"analysis_mode": "3d", "n_fields": 1, "n_replicates": 1, "counts": counts})
    with (run / "resolved_samples.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("image_id", "replicate_id", "green", "red", "ebfp"))
        writer.writeheader()
        writer.writerow({"image_id": "synthetic", "replicate_id": "synthetic_rep1", **info["paths"], "ebfp": ""})
    for kind in ("image", "replicate"):
        prefix = {"image_id": "synthetic", "replicate_id": "synthetic_rep1"} if kind == "image" else {"replicate_id": "synthetic_rep1", "n_fields": 1}
        with (run / (kind + "_summary.csv")).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=(*prefix, *COUNT_KEYS))
            writer.writeheader()
            writer.writerow({**prefix, **{key: counts[key] for key in COUNT_KEYS}})
    names = ("volume_run.json", "effective_config.json", "summary.json", "resolved_samples.csv",
             "image_summary.csv", "replicate_summary.csv", "fields/synthetic/output_checksums.json")
    _json(run / "checksums_3d.json", {name: _hash(run / name) for name in names})
    return run, info, settings


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--viability-smoke-test", type=Path, required=True)
    output = parser.parse_args(argv).viability_smoke_test.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["MPLBACKEND"] = "Agg"
    from . import __version__
    report = {"status": "failed", "app_version": __version__,
              "fixture": "Synthetic two-channel 3D shapes only; no biological validation implied",
              "timings_seconds": {}, "checks": []}
    app = window = dialog = None
    started_all = time.perf_counter()
    try:
        from unittest.mock import patch
        import numpy as np
        from PIL import Image
        from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
        from PySide6.QtGui import QFont, QFontDatabase, QMouseEvent, QWheelEvent
        from PySide6.QtWidgets import QApplication, QFileDialog
        from .app import MainWindow, STYLE
        from .analysis_3d import read_analysis_3d
        from .stack_dialog import StackReviewDialog
        from .viability_figure import LEGACY_LAYOUT, PANEL_DESTINATION

        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(STYLE)
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf"):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))
        app.setFont(QFont("Segoe UI", 10))
        started = time.perf_counter()
        run, info, settings = _synthetic_run(output)
        report["timings_seconds"]["synthetic_analysis"] = time.perf_counter() - started
        originals = _snapshot(run)
        sources = _snapshot(output / "inputs")

        window = MainWindow()
        window.resize(1366, 1000)
        window.summary_cache = output / "figure_cache"
        window.volume_review_cache = output / "review_cache"
        errors = []
        window.error = lambda message: errors.append(str(message))
        window.show()

        def wait_figures():
            deadline = time.monotonic() + 60
            ticks = 0
            while window.volume_workers and time.monotonic() < deadline:
                app.processEvents()
                ticks += 1
                time.sleep(.005)
            app.processEvents()
            assert not window.volume_workers, "Reviewed figure worker timed out"
            assert not errors, errors
            assert window.figure_button.isEnabled(), window.result_notes.toPlainText()
            assert window.summary_paths and Path(window.summary_paths["png"]).is_file()
            return ticks

        def export_figure(name):
            destination = output / name
            expected = Path(window.summary_paths["png"]).read_bytes()
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "PNG image (*.png)")):
                window.save_summary_figure()
            assert destination.read_bytes() == expected, "GUI export changed figure bytes"
            # Every image panel and threshold label must survive the review.
            with Image.open(run / "fields/synthetic/figure.png") as original, Image.open(destination) as saved:
                strip = LEGACY_LAYOUT["panel_strip"]
                width, height = strip[2] - strip[0], strip[3] - strip[1]
                x, y = PANEL_DESTINATION
                np.testing.assert_array_equal(np.asarray(original.convert("RGB").crop(tuple(strip))),
                                              np.asarray(saved.convert("RGB").crop((x, y, x + width, y + height))))
            return destination

        started = time.perf_counter()
        window.load_result(run)
        ticks = wait_figures()
        initial = window.result["viability_report"]["overall"]
        assert (initial["viability_min_pct"], initial["viability_max_pct"], initial["pending_groups"]) == (40.0, 50.0, 1)
        assert window.metric_labels[0][0].text() == "40.0–50.0%"
        assert window.metric_titles[0].text() == "Provisional viability"
        export_figure("provisional_summary.png")
        assert window.grab().save(str(output / "provisional_results.png"))
        report["timings_seconds"]["provisional_results_and_figure"] = time.perf_counter() - started
        report["checks"].append("Provisional 3D results display the synthetic 40–50% association range")

        field = window.result["fields"][0]
        dialog = StackReviewDialog(config={}, output_base=output, cache_dir=output / "stack_cache", review_decisions={})
        dialog.fields.addItem("synthetic")
        dialog.accept_stack_info({**deepcopy(info), "_ui_result": deepcopy(field["result"]), "_ui_defaults": deepcopy(settings)})
        assert dialog.arrays and dialog.labels, dialog.status.text()
        dialog.reviewApplied.connect(lambda decisions: window.apply_volume_review("synthetic", decisions, dialog))
        candidates = [index for index in range(1, dialog.object_choice.count())
                      if dialog.object_choice.itemData(index)["status"] == "dual_positive_candidate"]
        assert len(candidates) == 1
        dialog.object_choice.setCurrentIndex(candidates[0])
        candidate_id = str(dialog.object_choice.currentData()["object_id"])
        dialog.show()
        app.processEvents()

        # Exercise real GUI input in the executable, not only helper methods.
        pane = dialog.views["green"]
        target_before = dialog._review_target
        point_before = (dialog.x, dialog.y)
        profiles_before = dialog.profile.profiles
        rectangle = pane._target_rect()
        position = QPointF(rectangle.left() + pane.crosshair[0] * rectangle.width(),
                           rectangle.top() + pane.crosshair[1] * rectangle.height())
        started = time.perf_counter()
        with patch("numpy.load", side_effect=AssertionError("Navigation must reuse mapped arrays")), \
             patch("desktop.volume_analysis.analyze_volume", side_effect=AssertionError("Navigation must not segment")):
            event = QWheelEvent(position, QPointF(pane.mapToGlobal(position.toPoint())), QPoint(), QPoint(0, 720),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            app.sendEvent(pane, event)
            app.processEvents()
            assert pane.zoom_factor > 3
            start, end = position, position + QPointF(24, 15)
            for kind, point, button, buttons in (
                    (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
                    (QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
                    (QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)):
                app.sendEvent(pane, QMouseEvent(kind, point, QPointF(pane.mapToGlobal(point.toPoint())),
                    button, buttons, Qt.KeyboardModifier.NoModifier))
            app.processEvents()
            viewport = (pane.zoom_factor, pane.view_center)
            z = dialog.z_slider.value()
            dialog.z_slider.setValue(z + 1)
            dialog.z_slider.setValue(z)
            assert all((item.zoom_factor, item.view_center) == viewport for item in
                       [dialog.views[role] for role in ("green", "red", "merged")] + list(dialog.analysis_views.values()))
            assert dialog._review_target == target_before and (dialog.x, dialog.y) == point_before
            assert dialog.profile.profiles is profiles_before
            assert dialog.grab().save(str(output / "zoomed_z_dialog.png"))
            dialog.fit_views()
            assert all(item.zoom_factor == 1 for item in dialog.views.values())
            dialog.zoom.setCurrentIndex(3)
            pane.set_viewport(4, (.75, .75), emit=True)
            dialog._pick_visible_xy(.75, .75)
            assert pane.visible_rect().contains(QPointF(*pane.crosshair)), "A click in a zoomed crop hid the selected point"
            dialog.fit_views()
            dialog.object_choice.setCurrentIndex(candidates[0])
            assert dialog._review_target == target_before
        report["timings_seconds"]["zoom_pan_and_two_slice_changes"] = time.perf_counter() - started
        report["checks"].append("Mouse-wheel zoom, drag pan, synchronized XY, retained Z magnification and Fit views work without source reload, recounting or review-target changes")
        report["checks"].append("Clicking within a zoomed fixed crop keeps the selected point visible")

        def apply_choice(name, expected):
            started = time.perf_counter()
            dialog.decision.setCurrentIndex(dialog.decision.findData(name))
            dialog.note.setText("Synthetic identity choice for software validation only.")
            # The full review-to-GUI route must use the completed masks.
            with patch("desktop.volume_analysis.analyze_volume", side_effect=AssertionError("Review must not repeat segmentation")):
                assert dialog.apply_to_results(), dialog.status.text()
                worker_ticks = wait_figures()
            assert dialog._review_ack is True
            metrics = window.result["viability_report"]["overall"]
            assert metrics["viability_pct"] == expected and metrics["pending_groups"] == 0, metrics
            assert window.metric_labels[0][0].text() == f"{expected:.1f}%"
            assert window.metric_titles[0].text() == "Reviewed viability"
            report["timings_seconds"][name + "_review_and_figure"] = time.perf_counter() - started
            return worker_ticks

        ticks += apply_choice("same_cell", 40.0)
        export_figure("reviewed_same_cell_summary.png")
        assert dialog.grab().save(str(output / "reviewed_z_dialog.png"))
        dialog.hide()
        assert window.grab().save(str(output / "reviewed_results.png"))
        portable = output / "portable_review.json"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(portable), "Review JSON (*.json)")):
            window.export_volume_review()
        portable_document = json.loads(portable.read_text(encoding="utf-8"))
        assert portable_document["decisions"]["synthetic"][candidate_id]["decision"] == "same_cell"
        assert str(run) not in portable.read_text(encoding="utf-8"), "Portable review leaked absolute run paths"
        report["checks"].append("Same-cell review is acknowledged and yields 40%; original image panels remain pixel-identical")

        ticks += apply_choice("separate", 50.0)
        export_figure("reviewed_separate_summary.png")
        report["checks"].append("Separate-cell review yields 50% without rerunning segmentation")
        dialog.reject()
        assert not dialog.arrays

        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(portable), "Review JSON (*.json)")):
            window.load_volume_review()
        ticks += wait_figures()
        assert window.result["viability_report"]["overall"]["viability_pct"] == 40.0
        window.load_result(run)
        ticks += wait_figures()
        assert window.result["viability_report"]["overall"]["viability_pct"] == 40.0
        assert window.result["review_decisions"]["synthetic"][candidate_id]["note"] == "Synthetic identity choice for software validation only."
        summary_csv = output / "reviewed_counts.csv"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(summary_csv), "CSV table (*.csv)")):
            window.save_summary()
        with summary_csv.open(encoding="utf-8", newline="") as stream:
            exported = next(csv.DictReader(stream))
        assert float(exported["viability_pct"]) == 40.0 and int(exported["pending_groups"]) == 0
        assert exported["review_revision"] == window.result["review_document"]["revision_id"]
        export_figure("reopened_reviewed_summary.png")
        assert _snapshot(run) == originals, "Original analysis artifacts changed during review"
        assert _snapshot(output / "inputs") == sources, "Synthetic source volumes changed during review"
        read_analysis_3d(run, verify=True)
        assert not errors, errors
        report["checks"].extend([
            "Portable review reload and subsequent run reopen restore decisions and notes",
            "Exported CSV and PNG use the selected review revision",
            "All original analysis artifacts and input volumes remain byte-identical",
            "Original run passes full integrity verification after reviews",
            "GUI event processing remains active during background figure updates",
        ])
        report.update(status="passed", original_artifact_count=len(originals), source_volume_count=len(sources),
                      gui_event_loop_iterations=ticks, provisional_viability_pct=[40.0, 50.0],
                      reviewed_same_cell_viability_pct=40.0, reviewed_separate_viability_pct=50.0)
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if dialog is not None:
            dialog._saved_decisions = deepcopy(dialog.review_decisions)
            dialog.reject()
        if window is not None:
            window.stop_summary_worker()
            deadline = time.monotonic() + 15
            while window.volume_workers and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            if not window.volume_workers:
                window.close()
        report["timings_seconds"]["total"] = time.perf_counter() - started_all
        if not all(math.isfinite(value) and value >= 0 for value in report["timings_seconds"].values()):
            report.update(status="failed", error="Invalid validation runtime measurement")
        _json(output / "report.json", report)
    return 0 if report["status"] == "passed" else 1
