"""Recover selected Leica volumes into verified, disk-backed review inputs.

Saved counting runs and microscope acquisitions remain immutable. A stack is
decoded only once per source/selection; subsequent consumers open read-only
ZYX NumPy memmaps from an independent cache.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import uuid

import liffile
import numpy as np
from PIL import Image

from . import leica


STACK_SCHEMA = 1
STACK_MANIFEST = "stack_manifest.json"
ROLES = ("green", "red")
MAX_STACK_BYTES = 8 * 1024**3
MAX_PLANE_PIXELS = 64_000_000
MAX_Z_PLANES = 100_000
DISK_RESERVE_BYTES = 256 * 1024**2


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def _file_hash(path, cancelled):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            leica._check_cancelled(cancelled)
            block = stream.read(4 * 1024**2)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _pixel_hash(array):
    array = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(json.dumps({"shape": list(array.shape), "dtype": str(array.dtype)}, sort_keys=True).encode("ascii"))
    digest.update(memoryview(array).cast("B"))
    return digest.hexdigest()


def _stat(path):
    stat = Path(path).stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _source_identity(record):
    saved = record.get("source")
    if not isinstance(saved, dict) or not isinstance(saved.get("path"), str) or not saved["path"]:
        raise ValueError("This result has no recorded Leica acquisition. Import the original .lif or .lof file first.")
    path = Path(saved["path"]).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(
            f"Original Leica acquisition is unavailable: {path}\n"
            "Reconnect its drive or restore the file at its original location. "
            "A saved 2D projection cannot reconstruct the Z stack.")
    current = leica._identity(path)
    if not leica._same_identity(current, saved):
        raise ValueError("The original Leica acquisition has changed since this run. Reimport it and create a new run before reviewing its stack.")
    return current


def _role_mapping(record):
    """Map analysis roles back to acquisition channels, including user swaps."""
    result = record.get("result", {})
    selected = record.get("selection", {})
    exports = record.get("exports", {})
    row = result.get("row", {}) if isinstance(result, dict) else {}
    paths = record.get("analysis_channel_paths", row)
    channels = selected.get("channels", {}) if isinstance(selected, dict) else {}
    if not all(isinstance(value, dict) for value in (result, selected, exports, row, paths, channels)):
        raise ValueError("Invalid Leica import provenance; channel roles cannot be recovered.")
    sidecar = record.get("provenance_path") or result.get("provenance_path")
    parent = Path(sidecar).expanduser().resolve().parent if isinstance(sidecar, str) and sidecar else None
    mapping, references = {}, {}
    for role in ROLES:
        filename = paths.get(role)
        if not isinstance(filename, str) or not filename:
            raise ValueError("Stack review requires LIVE and DEAD channels from the same recorded Leica import.")
        path = Path(filename).expanduser().resolve()
        candidates = []
        for original_role, export in exports.items():
            if original_role not in leica.ROLES or not isinstance(export, dict) or export.get("filename") != original_role + ".tif":
                raise ValueError("Invalid Leica export filenames in the saved provenance.")
            declared = []
            if isinstance(row.get(original_role), str) and row[original_role]:
                declared.append(Path(row[original_role]).expanduser().resolve())
            if parent is not None:
                declared.append(parent / export["filename"])
            if path in declared:
                candidates.append(original_role)
        if len(candidates) != 1:
            raise ValueError(f"The {role.upper()} analysis TIFF cannot be matched unambiguously to its recorded Leica channel.")
        original_role = candidates[0]
        channel = channels.get(original_role)
        if isinstance(channel, bool) or not isinstance(channel, int):
            raise ValueError("A selected acquisition channel is missing from the Leica import record.")
        mapping[role] = {"original_role": original_role, "channel_index": channel}
        references[role] = {"path": str(path), "export": deepcopy(exports[original_role])}
    if mapping["green"]["channel_index"] == mapping["red"]["channel_index"]:
        raise ValueError("LIVE and DEAD resolve to the same Leica acquisition channel.")
    return mapping, references


def _validate_references(references, info, cancelled):
    """Hash available 2D analysis TIFFs and retain small pixel digests."""
    result = {}
    expected_shape = [info["sizes"]["Y"], info["sizes"]["X"]]
    for role, reference in references.items():
        leica._check_cancelled(cancelled)
        path = Path(reference["path"])
        export = reference["export"]
        if export.get("shape") != expected_shape or export.get("dtype") != info["dtype"]:
            raise ValueError("The saved Leica TIFF shape or dtype differs from the acquisition.")
        digest = export.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise ValueError("A Leica TIFF verification checksum is missing or invalid.")
        if not path.exists():
            result[role] = {"state": "unavailable", "path": str(path),
                            "message": "The imported 2D TIFF is unavailable; matching it to the recovered stack could not be verified."}
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"The imported TIFF is not a regular file: {path}")
        before = _stat(path)
        if before["size"] != export.get("bytes") or _file_hash(path, cancelled) != digest.lower():
            raise ValueError(f"The imported {role.upper()} TIFF changed since the run. Restore it or create a new analysis.")
        with Image.open(path) as image:
            pixels = np.asarray(image)
            if getattr(image, "n_frames", 1) != 1 or list(pixels.shape) != expected_shape or str(pixels.dtype) != info["dtype"]:
                raise ValueError("An imported Leica TIFF no longer matches its saved scalar image metadata.")
            pixel_digest = _pixel_hash(pixels)
        if _stat(path) != before:
            raise ValueError("An imported TIFF changed while it was being verified. Reopen the result.")
        result[role] = {"state": "verified", "path": str(path), "sha256": digest.lower(),
                        "pixel_sha256": pixel_digest, "stat": before}
    return result


def _check_references_unchanged(references):
    for reference in references.values():
        if reference["state"] == "verified":
            path = Path(reference["path"])
            if not path.is_file() or _stat(path) != reference["stat"]:
                raise ValueError("An imported TIFF changed while preparing the stack. Reopen the result.")


def _guard_volume(shape, dtype):
    z, y, x = shape
    if min(shape) < 1 or z > MAX_Z_PLANES or y * x > MAX_PLANE_PIXELS:
        raise ValueError("This stack exceeds the supported review dimensions. Import a smaller Z range or field.")
    required = math.prod(shape) * np.dtype(dtype).itemsize * len(ROLES)
    if required > MAX_STACK_BYTES:
        raise ValueError("The selected stack needs more than 8 GiB of cache. Import a smaller Z range for review.")
    return required


def _guard_disk(base, required):
    existing = base
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    reserve = max(DISK_RESERVE_BYTES, required // 10)
    if free < required + 8192 + reserve:
        gib = (required + reserve) / 1024**3
        raise ValueError(f"Not enough free space for this stack cache (about {gib:.2f} GiB including reserve). Choose a smaller Z range or free disk space.")


def _close_memmaps(arrays):
    error = None
    for array in list(arrays.values()):
        try:
            if isinstance(array, np.memmap):
                try:
                    if array.flags.writeable:
                        array.flush()
                finally:
                    array._mmap.close()
        except (OSError, ValueError, BufferError) as exc:
            error = error or exc
    arrays.clear()
    if error is not None:
        raise error


def _validate_cache(folder, key, shape, dtype, references, cancelled):
    try:
        if folder.is_symlink() or not folder.is_dir():
            return None
        manifest = folder / STACK_MANIFEST
        if manifest.is_symlink() or manifest.stat().st_size > 16 * 1024**2:
            return None
        record = json.loads(manifest.read_text(encoding="utf-8"))
        if record.get("schema_version") != STACK_SCHEMA or record.get("cache_key") != key:
            return None
        if record.get("shape_zyx") != shape or record.get("dtype") != dtype or set(record.get("files", {})) != set(ROLES):
            return None
        for role in ROLES:
            leica._check_cancelled(cancelled)
            path = folder / f"{role}.npy"
            entry = record["files"][role]
            if entry.get("filename") != path.name or path.is_symlink() or not path.is_file():
                return None
            before = _stat(path)
            if entry.get("bytes") != before["size"] or _file_hash(path, cancelled) != entry.get("sha256"):
                return None
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            try:
                if not isinstance(array, np.memmap) or list(array.shape) != shape or str(array.dtype) != dtype or not array.flags.c_contiguous:
                    return None
            finally:
                if isinstance(array, np.memmap):
                    array._mmap.close()
            if _stat(path) != before:
                return None
            expected = references[role].get("pixel_sha256")
            if expected is not None and entry.get("projection_pixel_sha256") != expected:
                raise ValueError("The recovered Leica stack does not reproduce the imported 2D TIFF. Reimport and review the acquisition before continuing.")
        return record
    except (OSError, ValueError, TypeError, KeyError, EOFError):
        # A damaged cache is kept untouched; rebuild separately from the source.
        return None


def _result(folder, key, source, selection, info, mapping, references, field_id, cached, import_record):
    indices = list(range(selection["z_start"], selection["z_stop"]))
    positions = info.get("z_positions_um")
    positions = [positions[index] for index in indices] if positions is not None else None
    return {"paths": {role: str(folder / f"{role}.npy") for role in ROLES},
            "shape_zyx": [len(indices), info["sizes"]["Y"], info["sizes"]["X"]],
            "spacing_um": [info.get("z_spacing_um"), *selection["pixel_size_um"]],
            "z_positions_um": positions, "z_indices": indices,
            "display": leica._display(info, selection), "dtype": info["dtype"],
            "cache_key": key, "source": deepcopy(source), "selection": deepcopy(selection),
            "analysis_role_origins": {role: value["original_role"] for role, value in mapping.items()},
            "field_id": field_id, "cached": cached,
            "import_record": deepcopy(import_record),
            "projection_verification": {role: {name: value for name, value in reference.items() if name not in ("stat", "pixel_sha256")}
                                        for role, reference in references.items()}}


def prepare_stack(import_record, cache_dir, progress=None, cancelled=None):
    """Return read-only ZYX .npy paths and calibrated metadata for one saved field.

    Source identity is stat-based, as explicitly recorded by the importer. When
    imported TIFFs are present, their hashes and reconstructed slice/projection
    pixels are checked as well. Missing TIFFs are reported in the return value;
    a missing original Leica acquisition always fails rather than guessing Z.
    """
    leica._check_cancelled(cancelled)
    if not isinstance(import_record, dict) or import_record.get("schema_version") != 1:
        raise ValueError("Unsupported or missing Leica import provenance.")
    source = _source_identity(import_record)
    mapping, original_references = _role_mapping(import_record)
    saved_selection = import_record["selection"]
    request = deepcopy(saved_selection)
    request.update({"source": source["path"], "source_identity": source,
                    "channels": {**{role: value["channel_index"] for role, value in mapping.items()}, "ebfp": None}})
    if request.get("pixel_size_um") is None:
        request["pixel_size_um"] = import_record.get("result", {}).get("input", {}).get("pixel_size_um")
    base = Path(cache_dir).expanduser().resolve()
    if any((parent / "run_manifest.json").exists() for parent in (base, *base.parents)):
        raise ValueError("Keep the stack cache outside completed analysis run folders.")
    staging = None
    arrays = {}
    try:
        with liffile.LifFile(source["path"], memmap=True, squeeze=False) as container:
            image, info, selection = leica._source_and_image(request, container, source, require_calibration=True)
            if saved_selection.get("series_path", info["path"]) != info["path"]:
                raise ValueError("The recorded Leica series path no longer matches the acquisition.")
            saved_sizes = saved_selection.get("sizes", {})
            if not isinstance(saved_sizes, dict) or any(
                    saved_sizes.get(axis, 1) != info["sizes"].get(axis, 1) for axis in set(saved_sizes) | set(info["sizes"])):
                raise ValueError("The recorded Leica series dimensions no longer match the acquisition.")
            shape = [selection["z_stop"] - selection["z_start"], info["sizes"]["Y"], info["sizes"]["X"]]
            required = _guard_volume(shape, info["dtype"])
            references = _validate_references(original_references, info, cancelled)
            key_data = {"schema_version": STACK_SCHEMA, "reader_version": liffile.__version__, "source": source,
                        "selection": selection, "dtype": info["dtype"],
                        "z_spacing_um": info.get("z_spacing_um"), "z_positions_um": info.get("z_positions_um"),
                        "exports": {role: reference["export"] for role, reference in original_references.items()}}
            key = _json_hash(key_data)
            prefix = "stack_" + key[:20]
            field_id = import_record.get("analysis_field_id") or import_record.get("result", {}).get("row", {}).get("image_id", info["name"])
            if base.is_dir():
                for candidate in sorted(base.glob(prefix + "*")):
                    if _validate_cache(candidate, key, shape, info["dtype"], references, cancelled) is not None:
                        _check_references_unchanged(references)
                        if leica._identity(source["path"]) != source:
                            raise ValueError("The Leica source changed while opening the stack cache.")
                        if progress is not None:
                            progress(1, 1)
                        return _result(candidate, key, source, selection, info, mapping, references, field_id, True, import_record)
            _guard_disk(base, required)
            base.mkdir(parents=True, exist_ok=True)
            final = base / prefix
            if final.exists():
                final = base / f"{prefix}_{uuid.uuid4().hex[:8]}"
            staging = Path(tempfile.mkdtemp(prefix=".stack_pending_", dir=base)).resolve()
            for role in ROLES:
                arrays[role] = np.lib.format.open_memmap(staging / f"{role}.npy", mode="w+", dtype=info["dtype"], shape=tuple(shape))
            projections = {}
            done, total = 0, shape[0] * len(ROLES)
            if progress is not None:
                progress(done, total)
            for local_z, acquisition_z in enumerate(range(selection["z_start"], selection["z_stop"])):
                for role in ROLES:
                    leica._check_cancelled(cancelled)
                    plane = leica._plane(image, selection["channels"][role], acquisition_z, selection["time_index"])
                    arrays[role][local_z] = plane
                    if role in projections:
                        np.maximum(projections[role], plane, out=projections[role])
                    else:
                        projections[role] = plane.copy()
                    done += 1
                    if progress is not None:
                        progress(done, total)
            _close_memmaps(arrays)
            files = {}
            for role in ROLES:
                leica._check_cancelled(cancelled)
                pixel_digest = _pixel_hash(projections[role])
                expected = references[role].get("pixel_sha256")
                if expected is not None and pixel_digest != expected:
                    raise ValueError("The recovered Leica stack does not reproduce the imported 2D TIFF. Reimport and review the acquisition before continuing.")
                path = staging / f"{role}.npy"
                files[role] = {"filename": path.name, "bytes": path.stat().st_size,
                               "sha256": _file_hash(path, cancelled), "projection_pixel_sha256": pixel_digest}
            manifest = {"schema_version": STACK_SCHEMA, "cache_key": key, "shape_zyx": shape, "dtype": info["dtype"],
                        "source": source, "selection": selection, "files": files}
            (staging / STACK_MANIFEST).write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        leica._check_cancelled(cancelled)
        _check_references_unchanged(references)
        if leica._identity(source["path"]) != source:
            raise ValueError("The original Leica source changed while preparing the stack. Reopen the run.")
        if staging.parent != base or final.resolve().parent != base or final.is_symlink():
            raise ValueError("The stack cache destination changed while saving. Choose the cache folder again.")
        if final.exists():
            raise FileExistsError("Another operation created this stack cache. Reopen stack review to reuse it.")
        staging.rename(final)
        return _result(final, key, source, selection, info, mapping, references, field_id, False, import_record)
    finally:
        _close_memmaps(arrays)
        if staging is not None and staging.exists() and staging.resolve() == staging and staging.parent == base and staging.name.startswith(".stack_pending_"):
            shutil.rmtree(staging)
