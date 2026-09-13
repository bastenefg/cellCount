"""Opt-in packaging check: render real widgets and verify through the real worker."""
import argparse
import json
import os
from pathlib import Path
import time
import traceback


def main(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--preview-manifest", type=Path)
    parser.add_argument("--results-only", action="store_true", help="Check saved-result display, export and verification without rerunning analysis.")
    args = parser.parse_args(arguments)
    output = args.smoke_test.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtWidgets import QApplication, QFileDialog
        from .app import MainWindow, STYLE
        from .services import ROOT, read_manifest
        from copy import deepcopy
        preview_rows = read_manifest(args.preview_manifest or ROOT / "reference" / "samples.csv")
        app = QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(STYLE)
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
        app.setFont(QFont("Segoe UI", 10))
        window = MainWindow()
        errors = []
        window.error = lambda error: errors.append(str(error))
        window.show()
        assert window.windowTitle() == "Live/Dead Cell Counter"
        assert window.input_modes.currentIndex() == 0, "Quick analysis is not the default"
        for channel in ("green", "red"):
            window.quick_start.set_path(channel, preview_rows[0][channel])
        quick_rows, quick_config = window.analysis_inputs()
        assert len(quick_rows) == 1 and not quick_rows[0]["ebfp"]
        app.processEvents()
        window.grab().save(str(output / "quick_setup.png"))
        window.rows = read_manifest(ROOT / "reference" / "samples.csv")
        window.refresh_fields()
        assert window.input_modes.currentIndex() == 1
        app.processEvents()
        window.grab().save(str(output / "setup.png"))
        window.load_result(args.run)
        window.result_tabs.setCurrentIndex(0)
        app.processEvents()
        window.grab().save(str(output / "results.png"))
        window.result_tabs.setCurrentIndex(1)
        window.preview_choice.setCurrentIndex(0)
        app.processEvents()
        window.viewer.fit()
        window.grab().save(str(output / "figure.png"))
        window.preview_choice.setCurrentIndex(1)
        app.processEvents()
        window.viewer.fit()
        window.grab().save(str(output / "detections.png"))
        # Export the summary while a detection overlay is selected. Both formats
        # must be exact copies of this run's saved figure, independent of zoom.
        saved_exports = []
        original_save_dialog = QFileDialog.getSaveFileName
        try:
            for extension in (".png", ".svg"):
                source = args.run / ("figure" + extension)
                if not source.is_file():
                    continue
                destination = output / ("exported_summary" + extension)
                QFileDialog.getSaveFileName = lambda *a, p=destination: (str(p), "")
                window.figure_button.click()
                assert destination.read_bytes() == source.read_bytes(), "Export differs from the loaded run's summary"
                saved_exports.append(extension)
        finally:
            QFileDialog.getSaveFileName = original_save_dialog
        assert saved_exports, "No summary figure exported"
        window.resize(1000, 650)
        app.processEvents()
        window.grab().save(str(output / "results_compact.png"))
        window.resize(1280, 910)
        window.output_base.setText(str(output))
        window.verify_results()
        deadline = time.monotonic() + 120
        while window.process is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.025)
        if window.process is not None:
            window.process.kill()
            window.process.waitForFinished(10000)
            raise RuntimeError("Verification worker timed out.")
        if errors or "verification passed" not in window.status_text.text().lower():
            raise RuntimeError(str(errors) or window.status_text.text())
        if args.results_only:
            window.close()
            (output / "smoke_test.json").write_text(json.dumps({
                "status": "passed", "gui_rendered": True,
                "worker_verification_passed": True,
                "summary_export_byte_identical": saved_exports,
                "export_while_detection_selected": True,
            }, indent=2) + "\n", encoding="utf-8")
            return 0
        import numpy as np
        from .segmentation_dialog import SegmentationDialog
        from .services import default_config
        initial_config = default_config()
        settings_before = deepcopy(initial_config)
        preview_started = time.perf_counter()
        review = SegmentationDialog(preview_rows, initial_config, window)
        review.resize(1460, 900)
        review.show()
        deadline = time.monotonic() + 90
        while not review._is_current() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.025)
        if not review._is_current():
            message = review.status.text()
            review.reject()
            raise RuntimeError("Segmentation preview failed: " + message)
        initial_preview_seconds = time.perf_counter() - preview_started
        preview_worker_pid = review._server.processId()
        assert preview_worker_pid > 0
        preview_counts = deepcopy(review.preview["counts"])
        labels_before = review.preview["labels_red"].copy()
        generation_before = review._generation
        review.white.setValue(30)
        review.show_ids.setChecked(True)
        review.overlay_view.set_view({"scale": 2, "cx": 512, "cy": 512})
        review.raw_view.set_view(review.overlay_view.get_view())
        app.processEvents()
        review.grab().save(str(output / "segmentation.png"))
        assert review._generation == generation_before
        np.testing.assert_array_equal(labels_before, review.preview["labels_red"])
        assert len(review.overlay_view.scene().items()) > 1, "Object IDs did not render"
        edit_timings = []
        for offset in (1, 2):
            high = settings_before["segmentation"]["red"]["high"] + offset
            edit_started = time.perf_counter()
            review.inputs["high"].setValue(high)
            assert not review.apply_button.isEnabled(), "Stale settings can be applied"
            deadline = time.monotonic() + 90
            while (not review._is_current() or review.worker is not None) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.025)
            if not review._is_current():
                message = review.status.text()
                review.reject()
                raise RuntimeError("Updated segmentation preview failed: " + message)
            elapsed = time.perf_counter() - edit_started
            assert review.result_config()["segmentation"]["red"]["high"] == high
            assert review._server.processId() == preview_worker_pid, "Preview worker restarted for an edit"
            edit_timings.append({"red_high": high, "seconds": elapsed,
                                 "counts": deepcopy(review.preview["counts"])})
        assert initial_config == settings_before
        review.reject()
        window.show_page(0)
        window.input_modes.setCurrentIndex(0)
        window.run_name.setText("quick_pair_run")
        window.run_analysis()
        deadline = time.monotonic() + 600
        while window.process is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.025)
        if window.process is not None:
            window.process.kill()
            window.process.waitForFinished(10000)
            raise RuntimeError("Quick analysis worker timed out.")
        if errors or window.result["path"] != output / "quick_pair_run":
            raise RuntimeError(str(errors) or "Quick analysis did not load its new results.")
        quick_result = window.result["image"][0]
        for key in ("live_only", "dead_only", "double_positive", "total"):
            assert int(quick_result[key]) == preview_counts[key], (key, quick_result[key], preview_counts[key])
        assert quick_result["image_id"] == "field_1"
        assert window.config == settings_before, "Quick analysis changed the stored preset"
        app.processEvents()
        window.grab().save(str(output / "quick_results.png"))
        window.close()
        (output / "smoke_test.json").write_text(json.dumps({"status": "passed", "gui_rendered": True, "worker_verification_passed": True,
            "segmentation_preview_passed": True, "segmentation_updated_from_controls": True,
            "display_did_not_change_labels": True, "opening_preset_unchanged": True,
            "preview_counts": preview_counts, "quick_pair_analysis_passed": True,
            "quick_counts_match_preview": True, "persistent_preview_worker": True,
            "initial_preview_seconds": initial_preview_seconds,
            "edit_timing_metric": "GUI control edit to current rendered preview, including debounce and display rendering",
            "edit_timings": edit_timings}, indent=2) + "\n", encoding="utf-8")
        return 0
    except Exception:
        (output / "smoke_test.json").write_text(json.dumps({"status": "failed", "error": traceback.format_exc()}, indent=2) + "\n", encoding="utf-8")
        return 1
