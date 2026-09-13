"""Opt-in source/frozen-app check of Leica import and real segmentation widgets."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import time
import traceback
import uuid


def main(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--leica-smoke-test", type=Path, required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--analyze", action="store_true", help="Also run one imported field and check saved provenance and counts.")
    args = parser.parse_args(arguments)
    output = args.leica_smoke_test.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    started = time.monotonic()
    deadline = started + 170
    report = {"status": "failed", "timings_seconds": {}}
    app = window = importer = review = None
    errors = []

    def wait_for(predicate, message, *, failure=None):
        while True:
            app.processEvents()
            if errors:
                raise RuntimeError("; ".join(errors))
            if predicate():
                return
            if failure and failure():
                raise RuntimeError(message())
            if time.monotonic() >= deadline:
                raise TimeoutError(message())
            time.sleep(.015)

    def import_idle():
        return importer.worker is None and importer._pending is None and not importer.debounce.isActive()

    def await_import_preview():
        wait_for(lambda: importer._preview_current and import_idle(),
                 lambda: "Leica preview did not complete: " + importer.status.text(),
                 failure=lambda: import_idle() and not importer._preview_current)

    try:
        import numpy as np
        from PIL import Image
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtWidgets import QApplication, QDialog
        from pipeline.io import read_scalar
        from .app import MainWindow, STYLE
        from .leica_dialog import LeicaImportDialog
        from .segmentation_dialog import SegmentationDialog

        source = args.file.resolve()
        source_stat = source.stat()
        source_identity = (source_stat.st_size, source_stat.st_mtime_ns)
        app = QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(STYLE)
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
        app.setFont(QFont("Segoe UI", 10))
        window = MainWindow()
        window.error = lambda error: errors.append(str(error))
        window.show()
        window.output_base.setText(str(output / "runs"))
        opening_config = deepcopy(window.config)
        importer = LeicaImportDialog(window.config, output / "imports", window)
        importer.show()
        opened = time.monotonic()
        importer.open_file(source)
        wait_for(lambda: importer.catalog is not None,
                 lambda: "Leica catalog did not load: " + importer.status.text(),
                 failure=lambda: import_idle() and importer.catalog is None)
        series = next((entry for entry in importer.catalog["series"]
                       if entry["supported"] and len(entry["channels"]) >= 2), None)
        assert series is not None, "Fixture needs a supported acquisition with at least two channels"
        importer.series.setCurrentIndex(importer.series.findData(series["index"]))
        await_import_preview()
        assert not importer.import_button.isEnabled(), "Channel review must precede import"
        assert importer.channels["green"].currentData() != importer.channels["red"].currentData()
        importer.channel_confirmation.setChecked(True)
        assert importer.import_button.isEnabled()
        z_count = series["sizes"].get("Z", 1)
        if z_count > 1:
            plane = 1 if importer.z_index.value() != 1 else z_count
            importer.z_slider.setValue(plane)
            assert importer.z_index.value() == plane
            assert not importer.import_button.isEnabled(), "A stale Z preview must not be imported"
            await_import_preview()
            # A short selected interval exercises streaming projection without
            # making this packaging check scale with arbitrary stack depth.
            importer.z_start.setValue(1)
            importer.z_end.setValue(min(3, z_count))
            importer.mode.setCurrentIndex(importer.mode.findData("max"))
            await_import_preview()
            assert "not a 3D cell count" in importer.stack_note.text()
        else:
            assert not importer.mode.isEnabled()
        selected_request = importer._request()
        for pane in importer.previews.values():
            assert pane.pixmap() is not None and not pane.pixmap().isNull(), "A selected channel preview is missing"
        report["timings_seconds"]["catalog_and_selection_previews"] = time.monotonic() - opened
        for width, height in ((1040, 820), (880, 720)):
            importer.resize(width, height)
            app.processEvents()
            assert importer.grab().save(str(output / f"leica_import_{width}.png"))
        imported_at = time.monotonic()
        importer.import_button.click()
        wait_for(lambda: importer.result_bundle is not None and importer.worker is None,
                 lambda: "Leica import did not complete: " + importer.status.text(),
                 failure=lambda: import_idle() and not importer._importing and importer.result_bundle is None)
        assert importer.result() == QDialog.DialogCode.Accepted
        bundle = importer.result_bundle
        report["timings_seconds"]["import"] = time.monotonic() - imported_at
        assert window.config == opening_config, "Import modified the stored preset"
        window.quick_start.set_imported(bundle)
        rows, config = window.analysis_inputs()
        assert rows == [bundle["row"]]
        assert config["input"] == bundle["input"], "Quick analysis lost imported dtype, dimensions or calibration"
        assert len(config["leica_imports"]) == 1, "Quick analysis lost import provenance"
        original_pixels = {}
        for role in ("green", "red"):
            with Image.open(rows[0][role]) as image:
                original_pixels[role] = np.asarray(image).copy()
            np.testing.assert_array_equal(read_scalar(rows[0][role], config), original_pixels[role])
        for pixels in original_pixels.values():
            assert list(pixels.shape) == bundle["input"]["expected_shape"]
            assert str(pixels.dtype) == bundle["input"]["dtype"]
        provenance = json.loads(Path(bundle["provenance_path"]).read_text(encoding="utf-8"))
        assert provenance["selection"]["mode"] == selected_request["mode"]
        assert provenance["selection"]["channels"] == selected_request["channels"]
        app.processEvents()
        assert window.grab().save(str(output / "leica_quick_setup.png"))

        preview_started = time.monotonic()
        review = SegmentationDialog(rows, config, window)
        review.show()
        wait_for(lambda: review._is_current() and review.worker is None,
                 lambda: "Imported segmentation preview failed: " + review.status.text(),
                 failure=lambda: review.worker is None and review.status.text().startswith(("Preview failed:", "Preview worker stopped:", "Preview could not start:")))
        report["timings_seconds"]["initial_segmentation_preview"] = time.monotonic() - preview_started
        for role, pixels in original_pixels.items():
            np.testing.assert_array_equal(review.preview["raw_" + role], pixels)
        pid = review._server.processId()
        assert pid > 0
        previous_request = deepcopy(review.worker_request)
        previous_key = review.preview_field
        slider = review.threshold_sliders["high"]
        slider.setSliderDown(True)
        slider.setValue(slider.value() + 150)
        intended_high = review.inputs["high"].value()
        assert intended_high > config["segmentation"]["red"]["high"]
        pause_until = time.monotonic() + .35
        while time.monotonic() < pause_until:
            app.processEvents()
            time.sleep(.015)
        assert review.worker is None, "Dragging started an intermediate preview"
        assert review.worker_request == previous_request
        assert review.preview_field == previous_key
        assert not review.debounce.isActive()
        assert not review.apply_button.isEnabled()
        edited_at = time.monotonic()
        slider.setSliderDown(False)
        wait_for(lambda: review._is_current() and review.worker is None,
                 lambda: "Released threshold slider did not update: " + review.status.text(),
                 failure=lambda: review.worker is None and review.status.text().startswith(("Preview failed:", "Preview worker stopped:", "Preview could not start:", "Review settings:")))
        report["timings_seconds"]["slider_release_to_preview"] = time.monotonic() - edited_at
        assert review._server.processId() == pid, "Slider edit restarted the preview worker"
        assert review.result_config()["segmentation"]["red"]["high"] == intended_high
        assert review.preview["config"]["segmentation"]["red"]["high"] == intended_high
        assert review.inputs["low"].value() <= intended_high
        reviewed_config = review.result_config()
        expected_counts = deepcopy(review.preview["counts"])
        review.resize(1460, 900)
        app.processEvents()
        assert review.grab().save(str(output / "leica_threshold_sliders.png"))
        review.accept()
        assert review.result() == QDialog.DialogCode.Accepted
        assert review._server is None, "Preview worker did not stop on close"
        report.update({"reader_import_passed": True, "source_format": source.suffix.lower(),
                       "series_name": series["name"], "input": bundle["input"],
                       "selected_mode": selected_request["mode"], "z_planes": z_count,
                       "import_cache_hit": bool(bundle.get("cached")),
                       "channel_previews_rendered": True, "pixel_values_preserved": True,
                       "quick_analysis_provenance_preserved": True, "threshold_drag_deferred": True,
                       "persistent_preview_worker": True, "red_peak_threshold": intended_high,
                       "preview_counts": expected_counts, "analysis_requested": args.analyze})

        if args.analyze:
            window.config = reviewed_config
            run_name = "leica_smoke_" + uuid.uuid4().hex[:8]
            window.run_name.setText(run_name)
            window.extended.setChecked(False)
            analysis_at = time.monotonic()
            window.run_analysis()
            wait_for(lambda: window.process is None, lambda: "Imported analysis did not finish: " + window.status_text.text())
            assert window.result and window.result["path"] == output / "runs" / run_name
            report["timings_seconds"]["analysis"] = time.monotonic() - analysis_at
            counts = window.result["image"][0]
            for key in ("live_only", "dead_only", "double_positive", "total"):
                assert int(counts[key]) == expected_counts[key], f"Analysis/preview mismatch: {key}"
            saved_imports = window.result["leica_imports"]
            assert len(saved_imports) == 1
            assert saved_imports[0]["selection"] == provenance["selection"]
            note = "2D maximum-intensity projection" if selected_request["mode"] == "max" else "2D optical slice"
            assert note in window.result_notes.toPlainText()
            app.processEvents()
            assert window.grab().save(str(output / "leica_analysis_results.png"))
            report.update({"analysis_passed": True, "saved_provenance_passed": True,
                           "analysis_counts_match_preview": True, "run_folder": str(window.result["path"])})
        after = source.stat()
        assert (after.st_size, after.st_mtime_ns) == source_identity, "Source Leica file changed"
        report["source_identity_unchanged"] = True
        report["status"] = "passed"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        try:
            if review is not None and not review._closing:
                review.reject()
            if importer is not None:
                importer.reject()
                cleanup_deadline = time.monotonic() + 8
                while importer.worker is not None and time.monotonic() < cleanup_deadline:
                    app.processEvents()
                    time.sleep(.015)
                if importer.worker is not None:
                    raise RuntimeError("Leica reader did not stop after cancellation")
            if window is not None:
                if window.process is not None:
                    window.cancelled = True
                    window.process.kill()
                    window.process.waitForFinished(1000)
                    app.processEvents()
                window.close()
            if app is not None:
                app.processEvents()
        except Exception:
            report["cleanup_error"] = traceback.format_exc()
            report["status"] = "failed"
        report["elapsed_seconds"] = time.monotonic() - started
        (output / "smoke_test.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1
