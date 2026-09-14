"""Check ICO frames, exact Windows executable resources and optional Qt wiring.

Reads the supplied assets/executable without changing them. The optional Qt
check instantiates the source MainWindow without showing it or running analysis.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys


ROOT = Path(__file__).resolve().parents[1]


def ico_frames(path):
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    if (reserved, kind) != (0, 1) or not count:
        raise ValueError("Expected a nonempty Windows ICO file.")
    frames = []
    for index in range(count):
        width, height, colors, zero, planes, bits, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * index)
        payload = data[offset:offset + length]
        if not length or len(payload) != length or offset < 6 + 16 * count:
            raise ValueError("Invalid ICO image bounds.")
        frames.append({
            "header": (width, height, colors, zero, planes, bits, length),
            "size": [width or 256, height or 256],
            "payload": payload,
        })
    return frames


def executable_icon(executable, expected):
    import pefile

    with pefile.PE(str(executable), fast_load=True) as pe:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        resources = {}
        for kind in getattr(pe, "DIRECTORY_ENTRY_RESOURCE", ()).entries:
            if kind.id not in (3, 14):  # RT_ICON and RT_GROUP_ICON
                continue
            for name in kind.directory.entries:
                for language in name.directory.entries:
                    item = language.data.struct
                    resources[kind.id, name.id, language.id] = pe.get_data(item.OffsetToData, item.Size)
        for (kind, name, language), data in resources.items():
            if kind != 14 or len(data) < 6:
                continue
            reserved, image_type, count = struct.unpack_from("<HHH", data)
            if (reserved, image_type, count) != (0, 1, len(expected)):
                continue
            if len(data) != 6 + 14 * count:
                continue
            matched = True
            for index, frame in enumerate(expected):
                entry = struct.unpack_from("<BBBBHHIH", data, 6 + 14 * index)
                payload = resources.get((3, entry[-1], language))
                if tuple(entry[:-1]) != frame["header"] or payload != frame["payload"]:
                    matched = False
                    break
            if matched:
                return {"status": "passed", "group_id": name, "language_id": language,
                        "frames_matched": count, "exact_payloads_match": True}
    raise ValueError("No executable RT_GROUP_ICON/RT_ICON group matches the supplied ICO frames.")


def qt_window_icon(icon_path, frames):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    from PySide6.QtGui import QIcon, QImage
    from PySide6.QtWidgets import QApplication
    from desktop.app import MainWindow

    app = QApplication.instance() or QApplication([])
    expected = QIcon(str(icon_path))
    window = MainWindow()
    try:
        actual = window.windowIcon()
        if expected.isNull() or actual.isNull():
            raise ValueError("The expected or MainWindow icon is null.")
        sizes = []
        for frame in frames:
            width, height = frame["size"]
            wanted = expected.pixmap(width, height).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            found = actual.pixmap(width, height).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            if wanted.isNull() or found.size() != wanted.size() or bytes(found.bits()) != bytes(wanted.bits()):
                raise ValueError(f"MainWindow icon pixels differ at {width} x {height}.")
            sizes.append([width, height])
        return {"status": "passed", "scope": "source MainWindow", "pixel_matched_sizes": sizes}
    finally:
        window.close()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--icon", type=Path, default=ROOT / "desktop/assets/live-dead-cell-counter.ico")
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--qt", action="store_true", help="Check source MainWindow icon pixels without showing a window.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        frames = ico_frames(args.icon)
        report = {"status": "passed", "icon": str(args.icon.resolve()),
                  "icon_sha256": hashlib.sha256(args.icon.read_bytes()).hexdigest(),
                  "ico_sizes": [frame["size"] for frame in frames]}
        if args.executable:
            report["executable"] = str(args.executable.resolve())
            report["embedded_icon"] = executable_icon(args.executable, frames)
        if args.qt:
            report["qt_window_icon"] = qt_window_icon(args.icon, frames)
    except Exception as error:
        report = {"status": "failed", "error": str(error), "type": type(error).__name__}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
