"""Exercise the actual Qt window and subprocess worker using synthetic fixtures."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
from PIL import Image
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from desktop.app import MainWindow, STYLE
from desktop.services import default_config, read_manifest, new_run_name


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    font = Path("C:/Windows/Fonts/segoeui.ttf")
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    errors = []
    window.error = lambda exc: errors.append(str(exc))
    window.show()
    window.rows = read_manifest(ROOT / "reference" / "samples.csv")
    window.refresh_fields()
    app.processEvents()
    window.grab().save(str(ROOT / "app_validation" / "gui_setup.png"))
    window.load_result(ROOT / "runs" / "gui_reference_validation")
    app.processEvents()
    window.viewer.fit()
    app.processEvents()
    window.grab().save(str(ROOT / "app_validation" / "gui_results.png"))
    window.result_tabs.setCurrentIndex(1)
    app.processEvents()
    window.viewer.fit()
    app.processEvents()
    window.grab().save(str(ROOT / "app_validation" / "gui_figure.png"))
    window.preview_choice.setCurrentIndex(1)
    app.processEvents()
    window.grab().save(str(ROOT / "app_validation" / "gui_overlay.png"))
    assert not errors, errors

    fixture = ROOT / "app_validation" / "synthetic_gui_inputs"
    fixture.mkdir(exist_ok=True)
    for channel in ("green", "red"):
        Image.fromarray(np.zeros((64, 64), dtype=np.uint8)).save(fixture / (channel + ".tif"))
    window.rows = [{"image_id": "blank", "replicate_id": "synthetic", "green": str(fixture / "green.tif"), "red": str(fixture / "red.tif"), "ebfp": ""}]
    config = default_config()
    config["input"]["expected_shape"] = [64, 64]
    window.config = config
    window.output_base.setText(str(ROOT / "runs"))
    window.run_name.setText(new_run_name("gui_synthetic_smoke"))
    window.run_analysis()
    out = window.job_out
    assert window.process is not None, errors
    deadline = time.monotonic() + 180
    events = 0
    while window.process is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.025)
        events += 1
    assert window.process is None, "Worker timed out"
    assert not errors, errors
    assert window.result["path"] == out
    assert window.result["image"][0]["total"] == "0"
    assert window.metric_labels[1][0].text() == "Not measured"
    window.verify_results()
    while window.process is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.025)
    assert window.process is None and not errors, errors
    assert "verification passed" in window.status_text.text().lower()
    result = {"status": "passed", "worker_run": str(out), "qt_event_loop_iterations_during_analysis": events,
              "reference_results_and_overlay_rendered": True, "blank_images_zero_objects": True,
              "missing_ebfp_displayed_as_not_measured": True, "worker_verification_passed": True}
    (ROOT / "app_validation" / "gui_smoke.json").write_text(json.dumps(result, indent=2) + "\n")
    window.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
