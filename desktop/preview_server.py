"""Persistent private preview worker; atomic files also work in a windowed EXE."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time


def publish_json(path, data):
    """Publish a complete message so readers never observe a partial JSON file."""
    path = Path(path)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
    pending.replace(path)


def serve(folder, parent_pid=None):
    from .segmentation import PreviewSession, write_preview_data

    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("A private preview session directory is required.")
    parent_handle = None
    if parent_pid and os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        parent_handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if not parent_handle:
            return 1
    session = PreviewSession()
    last_id = None
    try:
        while root.is_dir():
            if parent_handle and kernel.WaitForSingleObject(parent_handle, 0) != 258:
                break
            if parent_pid and os.name != "nt" and os.getppid() != parent_pid:
                break
            request_path = root / "request.json"
            if not request_path.exists():
                time.sleep(.02)
                continue
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, PermissionError):
                # The GUI removes acknowledged requests; a read can race with
                # that cleanup on Windows without invalidating the session.
                time.sleep(.02)
                continue
            identity = request.get("id", "")
            if not re.fullmatch(r"preview_\d+_[0-9a-f]{8}", identity):
                raise ValueError("Invalid private preview request ID.")
            if identity == last_id:
                time.sleep(.02)
                continue
            last_id = identity
            started = time.perf_counter()
            try:
                preview = session.compute(request["row"], request["config"])
                write_preview_data(preview, root / identity, compressed=False)
                response = {"id": identity, "ok": True}
            except Exception as exc:
                response = {"id": identity, "ok": False, "error": str(exc),
                            "error_type": type(exc).__name__}
            response["elapsed_seconds"] = time.perf_counter() - started
            publish_json(root / "response.json", response)
    finally:
        if parent_handle:
            kernel.CloseHandle(parent_handle)
    return 0
