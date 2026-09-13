"""Opt-in packaging check: render real widgets and verify through the real worker."""
import argparse
import hashlib
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
        window.summary_cache = output / "summary_cache"
        errors = []
        window.error = lambda error: errors.append(str(error))

        def run_figures(run):
            return {extension: (run / ("figure" + extension)).read_bytes()
                    if (run / ("figure" + extension)).is_file() else None
                    for extension in (".png", ".svg")}

        def await_full_field_summary():
            deadline = time.monotonic() + 90
            while True:
                app.processEvents()
                if errors:
                    raise RuntimeError(str(errors))
                if window.summary_process is None:
                    if not window.summary_paths:
                        raise RuntimeError("Full-field summary did not load: " + window.result_notes.toPlainText())
                    break
                if time.monotonic() >= deadline:
                    window.summary_process.kill()
                    window.summary_process.waitForFinished(10000)
                    raise RuntimeError("Full-field summary worker timed out.")
                time.sleep(.025)
            for key in ("png", "svg", "metadata"):
                assert Path(window.summary_paths[key]).is_file(), f"Missing full-field summary {key}"
                assert not Path(window.summary_paths[key]).resolve().is_relative_to(window.result["path"]), "Summary cache was written inside the completed run"
            metadata = json.loads(Path(window.summary_paths["metadata"]).read_text(encoding="utf-8"))
            saved_config = json.loads((window.result["path"] / "effective_config.json").read_text(encoding="utf-8"))
            assert metadata["segmentation"] == saved_config["segmentation"], "Summary thresholds/settings differ from those used for the saved detections"
            stored_masks = metadata["stored_detection_masks"]
            mask_path = window.result["path"] / "labels" / (metadata["representative_image_id"] + ".npz")
            assert Path(stored_masks["path"]).resolve() == mask_path, "Summary uses masks from a different field"
            with mask_path.open("rb") as stream:
                assert stored_masks["sha256"] == hashlib.file_digest(stream, "sha256").hexdigest(), "Summary masks differ from the saved detection masks"
            assert metadata["view"] == "full_field"
            assert Path(metadata["run"]).resolve() == window.result["path"], "Summary belongs to a different run"
            height, width = metadata["shape_yx"]
            assert height > 0 and width > 0
            assert metadata["crop_yx"] == [0, height, 0, width], "Summary still uses a central crop"
            assert Path(window.preview_choice.itemData(0)).resolve() == Path(window.summary_paths["png"]).resolve(), "Summary dropdown still points at the legacy cropped figure"
            assert window.figure_button.isEnabled(), "Full-field export is unavailable after rendering"

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
        original_figures = run_figures(args.run)
        window.load_result(args.run)
        await_full_field_summary()
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
        # must be exact copies of this run's full-field cache, independent of zoom.
        saved_exports = []
        original_save_dialog = QFileDialog.getSaveFileName
        try:
            for extension in (".png", ".svg"):
                source = Path(window.summary_paths[extension.removeprefix(".")])
                destination = output / ("exported_summary" + extension)
                QFileDialog.getSaveFileName = lambda *a, p=destination: (str(p), "")
                window.figure_button.click()
                assert destination.read_bytes() == source.read_bytes(), "Export differs from the loaded run's full-field summary"
                saved_exports.append(extension)
        finally:
            QFileDialog.getSaveFileName = original_save_dialog
        assert saved_exports, "No summary figure exported"
        assert run_figures(args.run) == original_figures, "Displaying/exporting the full field modified a legacy run figure"
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
                "full_field_summary_passed": True,
                "legacy_run_figures_unchanged": True,
                "saved_segmentation_parameters_used": True,
                "stored_detection_masks_used": True,
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
        quick_figures = run_figures(window.result["path"])
        await_full_field_summary()
        assert run_figures(window.result["path"]) == quick_figures, "Full-field rendering changed the new run's saved figures"
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
            "full_field_summary_passed": True, "legacy_run_figures_unchanged": True,
            "saved_segmentation_parameters_used": True,
            "stored_detection_masks_used": True,
            "summary_export_byte_identical": saved_exports,
            "initial_preview_seconds": initial_preview_seconds,
            "edit_timing_metric": "GUI control edit to current rendered preview, including debounce and display rendering",
            "edit_timings": edit_timings}, indent=2) + "\n", encoding="utf-8")
        return 0
    except Exception:
        (output / "smoke_test.json").write_text(json.dumps({"status": "failed", "error": traceback.format_exc()}, indent=2) + "\n", encoding="utf-8")
        return 1
