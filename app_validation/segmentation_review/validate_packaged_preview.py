"""Exercise this release's own GUI, preview subprocess, and Windows startup."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time

import numpy as np
from PyInstaller.archive.readers import CArchiveReader


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
APP = ROOT / "dist/1.1.0/CHO Cell Counter/CHO Cell Counter.exe"


def startup():
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return info


def run(arguments, timeout=180):
    result = subprocess.run([str(APP), *map(str, arguments)], cwd=ROOT,
                            startupinfo=startup(), timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Frozen process returned {result.returncode}: {arguments}")


def main():
    archive = CArchiveReader(str(APP))
    embedded = archive.open_embedded_archive("PYZ.pyz")
    required = ["desktop.segmentation", "desktop.segmentation_dialog", "desktop.segmentation_view", "desktop.smoke"]
    missing = [module for module in required if module not in embedded.toc]
    if missing:
        raise RuntimeError(f"Missing compiled modules: {missing}")
    (OUT / "compiled_preview_modules.json").write_text(json.dumps({"status": "passed", "modules": required}, indent=2) + "\n")

    smoke = OUT / "packaged_smoke"
    run(["--smoke-test", smoke, "--run", ROOT / "runs/gui_existing_sample_validation", "--preview-manifest", ROOT / "samples.csv"])
    report = json.loads((smoke / "smoke_test.json").read_text())
    assert report["status"] == "passed", report
    assert report["preview_counts"]["red_detections"] == 1402
    assert report["preview_counts"]["green_detections"] == 106
    print("Packaged test1 GUI/preview/update/display checks passed.", flush=True)

    request = OUT / "packaged_reference_request.json"
    request.write_text(json.dumps({
        "row": {"image_id": "S5", "replicate_id": "S5", "green": str(ROOT / "reference/raw/CHO-S5-live.tif"), "red": str(ROOT / "reference/raw/CHO-S5-dead.tif")},
        "config": json.loads((ROOT / "configs/reference_48h.json").read_text()),
    }, indent=2) + "\n")
    preview = OUT / "packaged_reference_preview"
    run(["--segmentation-preview", request, "--preview-output", preview])
    comparisons = {}
    with np.load(preview / "arrays.npz") as actual, np.load(ROOT / "reference/expected/S5_masks.npz") as expected:
        for actual_key, expected_key in [("labels_green", "live"), ("labels_red", "dead"), ("labels_objects", "objects")]:
            comparisons[actual_key] = int(np.count_nonzero(actual[actual_key] != expected[expected_key]))
            assert comparisons[actual_key] == 0
    (OUT / "packaged_reference_preview_check.json").write_text(json.dumps({"status": "passed", "image": "S5", "different_pixels": comparisons}, indent=2) + "\n")
    print("Packaged S5 preview matches all three original masks exactly.", flush=True)

    # Inspect only windows created by this specific test process; leave all other
    # CHO Cell Counter processes and the user's windows untouched.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "windows"
    proc = subprocess.Popen([str(APP)], cwd=ROOT, env=environment, startupinfo=startup())
    found = []
    forced_cleanup = False
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and proc.poll() is None:
            candidates = []
            @callback_type
            def visit(hwnd, lparam):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == proc.pid:
                    length = user32.GetWindowTextLengthW(hwnd)
                    title = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, title, length + 1)
                    if title.value:
                        candidates.append((hwnd, title.value))
                return True
            user32.EnumWindows(visit, 0)
            found = candidates
            if any(title == "CHO Cell Counter" for _, title in found):
                break
            time.sleep(.1)
        matching = [hwnd for hwnd, title in found if title == "CHO Cell Counter"]
        if not matching:
            raise RuntimeError(f"Native app window not found: return={proc.poll()}, titles={found}")
        for hwnd in matching:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE for test process only.
        code = proc.wait(timeout=15)
        assert code == 0, code
        native = {"status": "passed", "tracked_pid": proc.pid, "platform": "windows", "hidden_startup_requested": True,
                  "window_titles": [title for _, title in found], "exit_code": code, "other_app_processes_touched": False}
        (OUT / "native_launch_check.json").write_text(json.dumps(native, indent=2) + "\n")
        print("Tracked native Windows startup and clean shutdown passed.", flush=True)
    finally:
        if proc.poll() is None:
            forced_cleanup = True
            proc.terminate()
            proc.wait(timeout=10)
        if forced_cleanup:
            (OUT / "native_launch_check.json").write_text(json.dumps({"status": "failed", "tracked_pid": proc.pid, "reason": "Test process required forced cleanup; no other app process touched."}, indent=2) + "\n")


if __name__ == "__main__":
    main()
