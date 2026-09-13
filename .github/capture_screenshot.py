"""Capture the README's real Qt widgets with verified fonts and no user data.

Run from the project with: .venv/Scripts/python.exe .github/capture_screenshot.py
Visually inspect .github/images/quick-analysis.png before publishing it.
"""
import os
from pathlib import Path
import string
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QRawFont
from PySide6.QtWidgets import QApplication, QWidget

from desktop.app import MainWindow, STYLE


def main():
    app = QApplication([])
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf"):
        font_id = QFontDatabase.addApplicationFont(str(font_dir / filename))
        if font_id < 0:
            raise RuntimeError(f"Cannot load screenshot font {filename}; no image was captured.")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.resize(1280, 1040)
    # Demonstration values only. Do not capture personal paths or acquisitions.
    window.output_base.setText("C:/CellCounter/runs")
    window.run_name.setText("analysis_example")
    window.show()
    loop = QEventLoop()
    QTimer.singleShot(200, loop.quit)
    loop.exec()
    widgets = [window, *window.findChildren(QWidget)]
    visible = [widget for widget in widgets if widget.isVisible()]
    if not visible:
        raise RuntimeError("The window is not visible to the renderer; no image was captured.")
    if not QRawFont.fromFont(QFont("Segoe UI Symbol", 10)).supportsCharacter(ord("↗")):
        raise RuntimeError("The link icon font is missing; no image was captured.")
    for widget in visible:
        font = QRawFont.fromFont(widget.font())
        if not font.isValid() or not all(font.supportsCharacter(ord(char)) for char in string.ascii_letters + string.digits):
            raise RuntimeError("A visible widget has missing font glyphs; no image was captured.")
    destination = ROOT / ".github" / "images" / "quick-analysis.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(destination)):
        raise RuntimeError("Screenshot could not be saved.")
    window.close()
    print(f"Verified fonts for {len(visible)} visible widgets; captured {destination.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
