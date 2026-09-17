"""Opt-in, physically calibrated 3D fluorescence-object analysis.

This is deliberately separate from the reference 2D pipeline. Thresholds are
absolute detector intensities, optionally after a 3D Gaussian smoothing step.
Channel masks are retained separately: overlapping cells are never overwritten.
Cross-channel associations are candidates, not proof of cell identity or death
mechanism. No viability percentage is inferred from unresolved associations.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np
from scipy import ndimage as ndi


SCHEMA_VERSION = 1
ALGORITHM_VERSION = "1.0.0"
MAX_VOXELS = 128_000_000
MAX_COMPONENT_VOXELS = 16_000_000
MAX_COMPONENTS = 100_000
MAX_RAW_COMPONENTS = 2_000_000
MAX_SEEDS = 10_000
SLAB_VOXELS = 2_000_000
STATUSES = ("green_only", "red_only", "dual_positive_candidate", "unresolved")
ARTIFACTS = {"labels_green": "labels_green.npy", "labels_red": "labels_red.npy", "objects": "objects.csv",
             "summary": "summary.json", "settings": "settings.json", "figure": "figure.png",
             "provenance": "provenance.json", "checksums": "output_checksums.json"}
OBJECT_COLUMNS = ("object_id", "status", "green_ids", "red_ids", "z", "y", "x",
                  "z_um", "y_um", "x_um", "volume_um3", "candidate_count_min",
                  "candidate_count_max", "touches_boundary", "review_required", "reason")
LIMITATIONS = [
    "Experimental 3D fluorescence-object count; validate against reviewed cells before biological interpretation.",
    "Dual-positive candidates require review: 3D overlap alone does not establish the same cell.",
    "A proximity-only or one-to-many association remains unresolved; no viability percentage is reported.",
    "Dim signal, touching cells, axial blur, channel bleed-through, registration and undersampling can change objects and associations.",
    "L3224 dual signal is compatible with membrane compromise; these images do not diagnose apoptosis.",
    "Counts cover only the selected Z range and time point. Objects cut by its boundaries are flagged.",
]


class VolumeAnalysisCancelled(RuntimeError):
    pass


def _close_mapping(array):
    mapping = getattr(array, "_mmap", None)
    if mapping is not None and not mapping.closed:
        mapping.close()


def _check(cancelled):
    if cancelled is not None and cancelled():
        raise VolumeAnalysisCancelled("3D analysis cancelled.")


def _number(value, name, *, positive=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}.")
    return result


def _spacing(stack_info):
    values = stack_info.get("spacing_um")
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError("3D analysis requires calibrated spacing in micrometers [Z, Y, X].")
    return tuple(_number(value, f"{axis} spacing (µm)", positive=True)
                 for axis, value in zip("ZYX", values))


def _open_stack(stack_info):
    arrays = {}
    try:
        return _open_stack_inner(stack_info, arrays)
    except BaseException:
        for array in arrays.values():
            _close_mapping(array)
        raise


def _open_stack_inner(stack_info, arrays):
    spacing = _spacing(stack_info)
    paths = stack_info.get("paths", {})
    for role in ("green", "red"):
        path = Path(paths.get(role, "")).expanduser().resolve()
        if path.suffix.lower() != ".npy" or not path.is_file():
            raise ValueError(f"The cached {role} Z volume is missing.")
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        arrays[role] = array
        if array.ndim != 3 or min(array.shape) < 1 or array.dtype not in (np.uint8, np.uint16):
            raise ValueError("Expected nonempty uint8/uint16 volumes with Z, Y, X axes.")
    if arrays["green"].shape != arrays["red"].shape or arrays["green"].dtype != arrays["red"].dtype:
        raise ValueError("LIVE and DEAD volume shapes and detector types must agree.")
    shape = arrays["green"].shape
    if shape[0] < 2:
        raise ValueError("3D analysis needs at least two Z slices; use 2D counting for a single slice.")
    if stack_info.get("shape_zyx") is not None and tuple(stack_info["shape_zyx"]) != shape:
        raise ValueError("Cached volume dimensions differ from their recorded metadata.")
    if math.prod(shape) > MAX_VOXELS:
        raise ValueError(f"This volume exceeds the {MAX_VOXELS:,}-voxel 3D workload limit. Select a smaller Z range.")
    return arrays, spacing


def default_settings(stack_info, saved_config=None):
    """Suggest raw-intensity starting values, never reuse 2D contrast thresholds.

    The bounded sample is only a starting suggestion; the user must inspect
    masks. ``saved_config`` is accepted for callers but intentionally unused.
    """
    arrays, spacing = _open_stack(stack_info)
    try:
        return _default_settings(stack_info, arrays, spacing)
    finally:
        for array in arrays.values():
            _close_mapping(array)


def _default_settings(stack_info, arrays, spacing):
    result = {"min_seed_distance_um": 8.0, "match_distance_um": 0.0, "exclude_border": False}
    for role, volume in arrays.items():
        stride = max(1, int(math.ceil((volume.size / 200_000) ** (1 / 3))))
        sample = np.asarray(volume[::stride, ::stride, ::stride]).ravel()
        baseline, bright = np.percentile(sample, [50, 99.9])
        maximum = _display_bounds(stack_info, role, volume.dtype)[1]
        span = max(float(bright - baseline), maximum * 0.025)
        low = min(maximum, max(1.0, float(baseline) + 0.2 * span))
        high = min(maximum, max(low, float(baseline) + 0.5 * span))
        result[role] = {"low": round(low, 3), "high": round(high, 3), "sigma_um": 0.0,
                        "min_volume_um3": max(10.0, round(3 * math.prod(spacing), 6))}
    return result


def _settings(settings, dtype):
    if not isinstance(settings, dict):
        raise ValueError("3D settings must be an object.")
    if settings.get("background_sigma_um", 0) != 0:
        raise ValueError("3D thresholds use absolute intensities; background subtraction is not supported.")
    result = {"min_seed_distance_um": _number(settings.get("min_seed_distance_um", 8), "Seed spacing", positive=True),
              "match_distance_um": _number(settings.get("match_distance_um", 0), "Maximum channel surface gap"),
              "exclude_border": settings.get("exclude_border", False)}
    if not isinstance(result["exclude_border"], bool):
        raise ValueError("exclude_border must be true or false.")
    for role in ("green", "red"):
        params = settings.get(role, {})
        result[role] = {key: _number(params.get(key), f"{role} {key}", positive=key in ("low", "high", "min_volume_um3"))
                        for key in ("low", "high", "sigma_um", "min_volume_um3")}
        if not result[role]["low"] <= result[role]["high"] <= np.iinfo(dtype).max:
            raise ValueError(f"{role}: require 0 < low <= high <= the detector intensity maximum.")
    return result


def _display_bounds(stack_info, role, dtype):
    bounds = stack_info.get("display", {}).get(role, [0, int(np.iinfo(dtype).max)])
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
        raise ValueError(f"Invalid {role} display intensity range.")
    low, high = (_number(value, f"{role} display range") for value in bounds)
    if not low < high <= np.iinfo(dtype).max:
        raise ValueError(f"Invalid {role} display intensity range.")
    return [low, high]


def _identity(path):
    path = Path(path).resolve()
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _sha256(path, cancelled=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _check(cancelled)
            digest.update(chunk)
    return digest.hexdigest()


def _available_memory():
    """Read the Windows memory budget without adding a runtime dependency."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong) for name in ("total", "available", "total_page", "available_page",
                                                   "total_virtual", "available_virtual", "extended")]
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    return int(status.available) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None


def _slabs(shape):
    step = max(1, SLAB_VOXELS // (shape[1] * shape[2]))
    for start in range(0, shape[0], step):
        yield slice(start, min(shape[0], start + step))


def _smooth(volume, sigma_um, spacing, path, cancelled, resources=None):
    if sigma_um == 0:
        return volume
    sigma = np.asarray([sigma_um / value for value in spacing])
    halo = int(math.ceil(4 * sigma[0]))
    if any(value > 50 for value in sigma):
        raise ValueError("Smoothing exceeds 50 voxels on an axis; reduce the physical smoothing radius.")
    result = np.lib.format.open_memmap(path, mode="w+", dtype=np.float32, shape=volume.shape)
    if resources is not None:
        resources.append(result)
    for slab in _slabs(volume.shape):
        _check(cancelled)
        start, stop = max(0, slab.start - halo), min(volume.shape[0], slab.stop + halo)
        if (stop - start) * volume.shape[1] * volume.shape[2] > MAX_COMPONENT_VOXELS:
            raise ValueError("Smoothing needs too large a Z halo. Reduce its radius or the Z range.")
        working = ndi.gaussian_filter(np.asarray(volume[start:stop], dtype=np.float32), sigma, mode="reflect")
        result[slab] = working[slab.start - start:slab.stop - start]
    result.flush()
    return result


def _seed_points(distance, region, spacing, separation):
    maxima = (distance == ndi.maximum_filter(distance, size=3, mode="constant")) & region
    plateaus, count = ndi.label(maxima, structure=ndi.generate_binary_structure(3, 1))
    if count > MAX_COMPONENTS:
        raise ValueError("Too many local maxima. Raise the intensity threshold or minimum volume.")
    candidates = []
    for ident, sl in enumerate(ndi.find_objects(plateaus), 1):
        if sl is None:
            continue
        coords = np.argwhere(plateaus[sl] == ident)
        center = coords.mean(axis=0)
        point = coords[np.argmin(np.sum(((coords - center) * spacing) ** 2, axis=1))]
        point = point + np.array([axis.start for axis in sl])
        candidates.append(tuple(int(value) for value in point))
    candidates.sort(key=lambda point: (-distance[point], point))
    bins, accepted = {}, []
    offsets = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)]
    for point in candidates:
        physical = np.asarray(point) * spacing
        key = tuple(np.floor(physical / separation).astype(int))
        neighbors = (old for offset in offsets for old in bins.get(tuple(k + o for k, o in zip(key, offset)), ()))
        if any(np.sum((physical - old) ** 2) < separation ** 2 for old in neighbors):
            continue
        accepted.append(point)
        bins.setdefault(key, []).append(physical)
        if len(accepted) > MAX_SEEDS:
            raise ValueError("Too many seeds in one connected region. Raise the threshold or seed spacing.")
    return accepted


def _priority_watershed(distance, region, seeds, cancelled):
    """Compiled marker watershed on the negative, physically calibrated EDT."""
    from skimage.segmentation import watershed
    _check(cancelled)
    markers = np.zeros(region.shape, np.int32)
    for ident, point in enumerate(seeds, 1):
        markers[point] = ident
    assigned = watershed(-distance, markers=markers, mask=region, connectivity=1)
    _check(cancelled)
    return assigned


def _segment(volume, role, settings, spacing, directory, progress, cancelled):
    resources = []
    try:
        result = _segment_inner(volume, role, settings, spacing, directory, progress, cancelled, resources)
        resources[:] = [array for array in resources if array is not result[0]]
        return result
    finally:
        for array in resources:
            _close_mapping(array)


def _segment_inner(volume, role, settings, spacing, directory, progress, cancelled, resources):
    params, shape = settings[role], volume.shape
    voxel_volume = math.prod(spacing)
    min_voxels = max(1, int(math.ceil(params["min_volume_um3"] / voxel_volume - 1e-10)))
    work_path = directory / ("_working_" + role + ".npy")
    component_path = directory / ("_components_" + role + ".npy")
    working = _smooth(volume, params["sigma_um"], spacing, work_path, cancelled, resources)
    mask = np.empty(shape, bool)
    for slab in _slabs(shape):
        _check(cancelled)
        np.greater_equal(working[slab], params["low"], out=mask[slab])
    components = np.lib.format.open_memmap(component_path, mode="w+", dtype=np.int32, shape=shape)
    resources.append(components)
    count = ndi.label(mask, structure=ndi.generate_binary_structure(3, 1), output=components)
    del mask
    _check(cancelled)
    if count > MAX_RAW_COMPONENTS:
        raise ValueError(f"{role}: more than {MAX_RAW_COMPONENTS:,} raw regions. Raise the threshold or smoothing.")
    # Discard tiny noise before constructing component ROIs. Count labels in
    # slabs to avoid np.bincount's whole-volume int64 conversion allocation.
    sizes = np.zeros(count + 1, np.int64)
    for slab in _slabs(shape):
        _check(cancelled)
        sizes += np.bincount(components[slab].ravel(), minlength=count + 1)
    retained = np.flatnonzero(sizes >= min_voxels)
    retained = retained[retained != 0]
    if len(retained) > MAX_COMPONENTS:
        raise ValueError(f"{role}: more than {MAX_COMPONENTS:,} regions. Raise the threshold or smoothing.")
    lut = np.zeros(count + 1, np.int32)
    lut[retained] = np.arange(1, len(retained) + 1)
    for slab in _slabs(shape):
        _check(cancelled)
        components[slab] = lut[components[slab]]
    count = len(retained)
    del sizes, retained, lut
    regions = ndi.find_objects(components, max_label=count)
    labels = np.lib.format.open_memmap(directory / ("labels_" + role + ".npy"), mode="w+", dtype=np.int32, shape=shape)
    resources.append(labels)
    labels[:] = 0
    records = []
    for component_id, bounds in enumerate(regions, 1):
        _check(cancelled)
        if bounds is None:
            continue
        if math.prod(axis.stop - axis.start for axis in bounds) > MAX_COMPONENT_VOXELS:
            raise ValueError(f"{role}: a connected region is too large for bounded-memory 3D splitting. Raise the threshold or select fewer Z slices.")
        region = components[bounds] == component_id
        if np.count_nonzero(region) < min_voxels or float(working[bounds][region].max()) < params["high"]:
            continue
        # A zero halo gives correct distances when a component fills its ROI.
        padded = np.pad(region, 1)
        distance = ndi.distance_transform_edt(padded, sampling=spacing)[1:-1, 1:-1, 1:-1]
        seeds = _seed_points(distance, region, spacing, settings["min_seed_distance_um"])
        if len(seeds) == 1:
            assigned = region.astype(np.int32)
        else:
            assigned = _priority_watershed(distance, region, seeds, cancelled)
        del distance, padded
        sizes = np.bincount(assigned.ravel(), minlength=len(seeds) + 1)
        local_bounds = ndi.find_objects(assigned, max_label=len(seeds))
        offset = np.array([axis.start for axis in bounds])
        lut = np.zeros(len(seeds) + 1, np.int32)
        for ident, sub_bounds in enumerate(local_bounds, 1):
            if sub_bounds is None or sizes[ident] < min_voxels:
                continue
            absolute_bounds = tuple(slice(axis.start + base, axis.stop + base) for axis, base in zip(sub_bounds, offset))
            submask = assigned[sub_bounds] == ident
            intensities = working[absolute_bounds][submask]
            if float(intensities.max()) < params["high"]:
                continue
            boundary = any(axis.start == 0 or axis.stop == full for axis, full in zip(absolute_bounds, shape))
            if boundary and settings["exclude_border"]:
                continue
            coords = np.argwhere(submask).mean(axis=0) + np.array([axis.start for axis in absolute_bounds])
            object_id = len(records) + 1
            lut[ident] = object_id
            records.append({"id": object_id, "centroid": coords.tolist(), "bounds": absolute_bounds,
                            "voxels": int(sizes[ident]), "volume_um3": float(sizes[ident] * voxel_volume),
                            "touches_boundary": boundary, "peak_intensity": float(intensities.max())})
        mapped = lut[assigned]
        np.copyto(labels[bounds], mapped, where=mapped > 0)
        if progress and component_id % 100 == 0:
            progress(f"{role}: reviewed {component_id:,}/{count:,} regions")
    labels.flush()
    _close_mapping(components)
    if working is not volume:
        _close_mapping(working)
    component_path.unlink()
    if work_path.exists():
        work_path.unlink()
    return labels, records


def _associate(green_labels, red_labels, green, red, spacing, settings, cancelled):
    """Sparse physical surface-gap graph; no all-pairs centroid matrix."""
    edges, adjacency = {}, {}
    gap = settings["match_distance_um"]
    padding = np.ceil(gap / np.asarray(spacing)).astype(int)
    shape = green_labels.shape
    for item in green:
        _check(cancelled)
        bounds = tuple(slice(max(0, sl.start - int(pad)), min(size, sl.stop + int(pad)))
                       for sl, pad, size in zip(item["bounds"], padding, shape))
        if math.prod(axis.stop - axis.start for axis in bounds) > MAX_COMPONENT_VOXELS:
            raise ValueError("The channel matching neighborhood is too large. Reduce the maximum surface gap.")
        live = green_labels[bounds] == item["id"]
        candidates = red_labels[bounds]
        if gap:
            close = ndi.distance_transform_edt(~live, sampling=spacing) <= gap + 1e-10
        else:
            close = live
        red_ids = np.unique(candidates[close])
        for red_id in red_ids[red_ids > 0]:
            red_id = int(red_id)
            overlap = int(np.count_nonzero(live & (candidates == red_id)))
            key = (item["id"], red_id)
            edges[key] = overlap
            gkey, rkey = ("green", item["id"]), ("red", red_id)
            adjacency.setdefault(gkey, set()).add(rkey)
            adjacency.setdefault(rkey, set()).add(gkey)
    lookup = {("green", row["id"]): row for row in green}
    lookup.update({("red", row["id"]): row for row in red})
    visited, records = set(), []
    for node in lookup:
        if node in visited:
            continue
        members, pending = set(), [node]
        while pending:
            current = pending.pop()
            if current in members:
                continue
            members.add(current)
            pending.extend(adjacency.get(current, ()))
        visited.update(members)
        gids = sorted(ident for role, ident in members if role == "green")
        rids = sorted(ident for role, ident in members if role == "red")
        if not rids:
            status, reason = "green_only", "No DEAD object within the selected 3D surface gap."
        elif not gids:
            status, reason = "red_only", "No LIVE object within the selected 3D surface gap."
        elif len(gids) == len(rids) == 1 and edges.get((gids[0], rids[0]), 0):
            status, reason = "dual_positive_candidate", "One LIVE and one DEAD object overlap in 3D; same-cell identity needs review."
        elif len(gids) == len(rids) == 1:
            status, reason = "unresolved", "Objects are nearby in 3D but do not share voxels; review cell identity."
        else:
            status, reason = "unresolved", "One-to-many or many-to-many channel association; no forced pairing."
        member_rows = [lookup[key] for key in members]
        weights = [row["voxels"] for row in member_rows]
        center = np.average([row["centroid"] for row in member_rows], weights=weights, axis=0)
        shared = sum(edges[(gid, rid)] for gid in gids for _, rid in adjacency.get(("green", gid), ()))
        # Within each channel labels do not overlap; pair intersections are disjoint.
        volume_um3 = (sum(weights) - shared) * math.prod(spacing)
        row = {"object_id": len(records) + 1, "status": status,
               "green_ids": ";".join(map(str, gids)), "red_ids": ";".join(map(str, rids)),
               "volume_um3": float(volume_um3), "candidate_count_min": max(len(gids), len(rids)),
               "candidate_count_max": len(members), "touches_boundary": any(row["touches_boundary"] for row in member_rows),
               "review_required": bool(gids and rids), "reason": reason}
        row.update({axis: float(value) for axis, value in zip("zyx", center)})
        row.update({axis + "_um": float(value * scale) for axis, value, scale in zip("zyx", center, spacing)})
        records.append(row)
    return records


def _write_csv(path, rows, columns):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _figure(arrays, labels, settings, summary, path):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    figure = Figure(figsize=(10, 6), dpi=150, facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.subplots(1, 3)
    stride = max(1, int(math.ceil(max(arrays["green"].shape[1:]) / 1024)))
    projections = {role: np.max(array[:, ::stride, ::stride], axis=0) for role, array in arrays.items()}
    rgb = np.zeros((*projections["green"].shape, 3), np.float32)
    for role, channel in (("green", 1), ("red", 0)):
        low, high = summary["display"][role]
        rgb[:, :, channel] = np.clip((projections[role].astype(np.float32) - low) / (high - low), 0, 1)
    for ax, role in zip(axes[:2], ("green", "red")):
        single = np.zeros_like(rgb)
        channel = 1 if role == "green" else 0
        single[:, :, channel] = rgb[:, :, channel]
        ax.imshow(single, interpolation="nearest")
        present = np.any(labels[role][:, ::stride, ::stride] > 0, axis=0)
        if present.any() and not present.all():
            ax.contour(present, levels=[0.5], colors=["#62e6ff" if role == "green" else "#ffd166"], linewidths=0.45)
        params = settings[role]
        ax.set_title(f"{'LIVE' if role == 'green' else 'DEAD'} · 3D mask projection\nLow {params['low']:g} / high {params['high']:g}; σ {params['sigma_um']:g} µm", fontsize=9)
    axes[2].imshow(rgb, interpolation="nearest")
    axes[2].set_title("Raw maximum projection\nDepth is inspected in the Z viewer", fontsize=9)
    for ax in axes:
        ax.set_axis_off()
    counts = summary["counts"]
    figure.suptitle("3D LIVE / DEAD fluorescence-object analysis", fontsize=15, y=0.92)
    figure.text(0.5, 0.82, f"Green only: {counts['green_only']}    Red only: {counts['red_only']}    "
                f"Dual candidates: {counts['dual_positive_candidate']}    Unresolved groups: {counts['unresolved']}",
                ha="center", fontsize=10)
    figure.text(0.5, 0.08, "Candidates require review; no viability percentage or apoptosis diagnosis is inferred.\n"
                f"Min. volume LIVE / DEAD: {settings['green']['min_volume_um3']:g} / {settings['red']['min_volume_um3']:g} µm³; "
                f"seed spacing: {settings['min_seed_distance_um']:g} µm; maximum channel gap: {settings['match_distance_um']:g} µm\n"
                f"Spacing Z/Y/X: {' / '.join(f'{v:g}' for v in summary['spacing_um'])} µm; "
                f"volume: {' × '.join(map(str, summary['shape_zyx']))} voxels (Z/Y/X)\n"
                f"{summary.get('selection_description', '')}", ha="center", fontsize=8, linespacing=1.6)
    figure.subplots_adjust(top=0.73, bottom=0.26, left=0.025, right=0.975, wspace=0.08)
    figure.savefig(path, dpi=150)
    figure.clear()


def analyze_volume(stack_info, settings, output_dir, progress=None, cancelled=None):
    """Save a complete independent 3D result atomically; return artifact paths."""
    _check(cancelled)
    arrays, spacing = _open_stack(stack_info)
    try:
        return _analyze_volume(stack_info, settings, output_dir, progress, cancelled, arrays, spacing)
    finally:
        for array in arrays.values():
            _close_mapping(array)


def _analyze_volume(stack_info, settings, output_dir, progress, cancelled, arrays, spacing):
    settings = _settings(settings, arrays["green"].dtype)
    display = {role: _display_bounds(stack_info, role, array.dtype) for role, array in arrays.items()}
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise FileExistsError("3D output already exists. Choose a new folder; saved results are preserved.")
    protected = {Path(path).resolve().parent for path in stack_info["paths"].values() if path}
    for key in ("run_dir", "run_path", "original_run", "cache_dir"):
        if stack_info.get(key):
            protected.add(Path(stack_info[key]).resolve())
    if any(output == folder or folder in output.parents for folder in protected):
        raise ValueError("Save 3D results in a separate folder outside the source cache and original run.")
    if any((folder / "run_manifest.json").is_file() or
           ((folder / "result.json").is_file() and (folder / "summary.json").is_file()) for folder in output.parents):
        raise ValueError("Save 3D results outside completed analysis folders.")
    identities = {role: _identity(stack_info["paths"][role]) for role in ("green", "red")}
    available = _available_memory()
    minimum_ram = 256_000_000 + math.prod(arrays["green"].shape) * 5
    if available is not None and available < minimum_ram:
        raise ValueError("Insufficient free memory for this 3D volume. Close other applications or select fewer Z slices.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Scratch storage: two label volumes plus one temporary component volume.
    estimated_disk = math.prod(arrays["green"].shape) * 16 + 16_000_000
    if shutil.disk_usage(output.parent).free < estimated_disk:
        raise ValueError("Insufficient disk space for memory-mapped 3D masks and scratch storage.")
    stage = Path(tempfile.mkdtemp(prefix=".volume-pending-" + output.name + "-", dir=output.parent))
    try:
        input_digests = {role: _sha256(identity["path"], cancelled) for role, identity in identities.items()}
        label_arrays, detections = {}, {}
        for role in ("green", "red"):
            if progress:
                progress(f"Segmenting {'LIVE' if role == 'green' else 'DEAD'} in 3D…")
            label_arrays[role], detections[role] = _segment(arrays[role], role, settings, spacing, stage, progress, cancelled)
        if progress:
            progress("Checking channel associations in physical 3D space…")
        rows = _associate(label_arrays["green"], label_arrays["red"], detections["green"], detections["red"], spacing, settings, cancelled)
        counts = {status: sum(row["status"] == status for row in rows) for status in STATUSES}
        counts.update({"total_groups": len(rows), "green_objects": len(detections["green"]), "red_objects": len(detections["red"]),
                       "candidate_count_min": sum(row["candidate_count_min"] for row in rows),
                       "candidate_count_max": sum(row["candidate_count_max"] for row in rows),
                       "boundary_groups": sum(row["touches_boundary"] for row in rows)})
        selection = stack_info.get("selection", {})
        z_start = selection.get("z_start", 0)
        z_stop = selection.get("z_stop", z_start + arrays["green"].shape[0])
        selection_description = f"Selected source Z slices {z_start + 1}–{z_stop}; time point {selection.get('time_index', 0) + 1}"
        summary = {"schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
                   "analysis": "experimental_3d_fluorescence_objects", "counts": counts, "display": display,
                   "shape_zyx": list(arrays["green"].shape), "spacing_um": list(spacing),
                   "source_dtype": str(arrays["green"].dtype),
                   "selection_description": selection_description,
                   "created_utc": datetime.now(timezone.utc).isoformat(), "limitations": LIMITATIONS,
                   "count_range_meaning": "Association bounds assuming each detected channel object is one cell; segmentation errors can exceed these bounds.",
                   "threshold_semantics": "Absolute detector intensity after optional 3D Gaussian smoothing; no background subtraction.",
                   "matching_semantics": "Maximum Euclidean distance between segmented LIVE/DEAD voxel centers in micrometers; zero requires a shared voxel. This is not a measured membrane-to-membrane distance.",
                   "coordinates": "Zero-based local Z/Y/X voxels. z_um is distance from the first selected slice along acquisition order, not signed microscope Z; y_um/x_um are relative pixel-center positions."}
        _write_csv(stage / "objects.csv", rows, OBJECT_COLUMNS)
        for role in ("green", "red"):
            output_rows = [{"id": row["id"], **dict(zip("zyx", row["centroid"])), "volume_um3": row["volume_um3"],
                            "voxels": row["voxels"], "touches_boundary": row["touches_boundary"], "peak_intensity": row["peak_intensity"]}
                           for row in detections[role]]
            _write_csv(stage / (role + "_objects.csv"), output_rows,
                       ("id", "z", "y", "x", "volume_um3", "voxels", "touches_boundary", "peak_intensity"))
        (stage / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
        (stage / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        from importlib.metadata import version
        (stage / "provenance.json").write_text(json.dumps({"schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
            "software": {name: version(name) for name in ("numpy", "scipy", "scikit-image")}, "stack_info": stack_info,
            "input_files": {role: {**identity, "sha256": input_digests[role]} for role, identity in identities.items()},
            "algorithm": "6-connected hysteresis / physical EDT seeds / compiled watershed / 3D surface-gap association"}, indent=2), encoding="utf-8")
        _check(cancelled)
        if progress:
            progress("Saving threshold-matched 3D summary figure…")
        _figure(arrays, label_arrays, settings, summary, stage / "figure.png")
        result = {key: str(output / filename) for key, filename in ARTIFACTS.items()}
        result["output_dir"] = str(output)
        (stage / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        checksums = {path.name: _sha256(path, cancelled) for path in sorted(stage.iterdir()) if path.is_file()}
        (stage / ARTIFACTS["checksums"]).write_text(json.dumps(checksums, indent=2), encoding="utf-8")
        if any(_identity(stack_info["paths"][role]) != identity for role, identity in identities.items()):
            raise ValueError("A cached source volume changed during analysis. Reopen the stack.")
        _check(cancelled)
        for array in label_arrays.values():
            _close_mapping(array)
        label_arrays.clear()
        stage.rename(output)
        return result
    except BaseException:
        if "label_arrays" in locals():
            for array in label_arrays.values():
                _close_mapping(array)
            label_arrays.clear()
        # Only this freshly-created child of the requested output parent is removed.
        if (stage.exists() and not stage.is_symlink() and stage.resolve().parent == output.parent
                and stage.name.startswith(".volume-pending-")):
            shutil.rmtree(stage, ignore_errors=True)
        raise


def read_volume_result(path, verify=True):
    """Validate and reopen a complete result, including after folder relocation.

    Absolute paths in result.json are informational; paths returned here always
    point inside the selected folder. No source pixels are needed for export.
    """
    folder = Path(path).expanduser().resolve()
    if folder.is_file():
        folder = folder.parent
    required = (set(ARTIFACTS.values()) - {ARTIFACTS["checksums"]}) | {"result.json", "green_objects.csv", "red_objects.csv"}
    if not folder.is_dir() or any(not (folder / name).is_file() for name in required | {ARTIFACTS["checksums"]}):
        raise ValueError("This is not a complete 3D analysis folder.")
    try:
        checksums = json.loads((folder / ARTIFACTS["checksums"]).read_text(encoding="utf-8"))
        if not isinstance(checksums, dict) or not required.issubset(checksums):
            raise ValueError("The 3D integrity manifest is incomplete.")
        for filename, expected in checksums.items():
            target = folder / filename
            if (Path(filename).name != filename or target.resolve().parent != folder or
                    not isinstance(expected, str) or len(expected) != 64 or
                    any(character not in "0123456789abcdef" for character in expected)):
                raise ValueError("The 3D integrity manifest contains invalid entries.")
            if not target.is_file() or (verify and _sha256(target) != expected):
                raise ValueError(f"Saved 3D artifact failed integrity verification: {filename}")
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        settings = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
        provenance = json.loads((folder / "provenance.json").read_text(encoding="utf-8"))
        if summary.get("schema_version") != SCHEMA_VERSION or provenance.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported 3D result schema.")
        _spacing(summary)
        _settings(settings, np.dtype(summary["source_dtype"]))
        shape = summary["shape_zyx"]
        if (not isinstance(shape, list) or len(shape) != 3 or
                any(not isinstance(value, int) or value < 1 for value in shape) or math.prod(shape) > MAX_VOXELS):
            raise ValueError("Invalid saved 3D dimensions.")
        for role in ("green", "red"):
            labels = np.load(folder / ARTIFACTS["labels_" + role], mmap_mode="r", allow_pickle=False)
            try:
                if labels.shape != tuple(shape) or labels.dtype != np.int32:
                    raise ValueError("Saved 3D labels disagree with their dimensions or type.")
            finally:
                _close_mapping(labels)
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read this 3D result: {exc}") from exc
    result = {key: str(folder / name) for key, name in ARTIFACTS.items()}
    result["output_dir"] = str(folder)
    return result


def run_job(path):
    """Frozen-app friendly subprocess entry point; no GUI imports."""
    path = Path(path).resolve()
    status_path = path.with_name(path.stem + "_status.json")
    cancel_path = path.with_name(path.stem + "_cancel")
    def write_status(status):
        temporary = status_path.with_suffix(".pending")
        temporary.write_text(json.dumps(status, indent=2), encoding="utf-8")
        temporary.replace(status_path)
    def report(message):
        write_status({"status": "running", "progress": message})
        if sys.stdout is not None:
            print(message, flush=True)
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
        result = analyze_volume(job["stack_info"], job["settings"], job["output_dir"], progress=report,
                                cancelled=cancel_path.exists)
        status = {"status": "completed", "result": result}
        code = 0
    except VolumeAnalysisCancelled as exc:
        status = {"status": "cancelled", "error": str(exc)}
        code = 130
    except Exception as exc:
        status = {"status": "failed", "error": str(exc)}
        report(str(exc))
        code = 1
    write_status(status)
    return code
