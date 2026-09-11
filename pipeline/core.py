"""Deterministic live/dead segmentation and empirical EBFP enrichment.

The default algorithm reproduces the recovered S5--S7 analysis. Arrays are
never modified in place. Segmentation uses background-subtracted *working*
arrays; raw-channel measurements use the input intensities. A detected object
is an image-based object, not an independently validated cell.

Green/red matching deliberately retains the original last-write mask behavior.
Any pixels lost to later objects are reported so that this legacy limitation
is visible rather than silently changing the reference analysis.
"""

from __future__ import annotations

import heapq

import numpy as np
import pandas as pd
from scipy import ndimage as ndi
from scipy.optimize import linear_sum_assignment
from scipy.signal import correlate
from scipy.spatial.distance import cdist


SEGMENT_COLUMNS = [
    "channel", "id", "y", "x", "area", "peak_contrast", "mean_raw",
    "max_raw", "parent_count",
]
OBJECT_COLUMNS = [
    "object_id", "y", "x", "green_id", "red_id", "status", "area",
    "green_peak", "red_peak", "mask_pixels_final", "mask_pixels_overwritten",
    "mask_lost",
]
EBFP_COLUMNS = [
    "sample", "ebfp_roi_pixels", "ebfp_sum", "ebfp_mean",
    "ebfp_nonzero_pixels", "ebfp_bg_mean", "ebfp_bg_sd", "ebfp_bg_q99",
    "ebfp_shift_locations", "ebfp_p", "ebfp_enrichment", "ebfp_scorable",
    "ebfp_status",
]
NEIGHBORS = [
    (-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1),
]


def _image(array):
    """Get a floating-point 2-D view/copy without altering its source."""
    a = np.asarray(array, dtype=float)
    if a.ndim != 2 or 0 in a.shape:
        raise ValueError("Expected one nonempty, two-dimensional image.")
    if not np.isfinite(a).all():
        raise ValueError("Images must contain only finite intensities.")
    return a


def segment(array, channel, config, factor=1):
    """Return channel labels, object measurements, and working contrast.

    ``channel`` is ``green`` or ``red``. The CSV ``channel`` values remain
    ``live``/``dead`` for compatibility with the original reference export.
    Thresholds and sizes are supplied through ``config['segmentation']``.
    ``factor`` multiplies both high and low thresholds for sensitivity runs.
    """
    if channel not in ("green", "red"):
        raise ValueError("channel must be 'green' or 'red'.")
    if not np.isfinite(factor) or factor <= 0:
        raise ValueError("Threshold factor must be positive and finite.")
    a = _image(array)
    settings = config["segmentation"]
    params = settings[channel]
    smooth = ndi.gaussian_filter(a, params["sigma_px"])
    background = ndi.gaussian_filter(a, settings["background_sigma_px"])
    contrast = smooth - background
    high = params["high"] * factor
    low = params["low"] * factor
    min_area = params["min_area_px"]
    if not 0 < low <= high or min_area < 1:
        raise ValueError("Require 0 < low <= high and min_area_px >= 1.")
    mask = contrast >= low
    components, _ = ndi.label(mask, np.ones((3, 3)))
    out = np.zeros(a.shape, np.int32)
    rows = []
    legacy_channel = "live" if channel == "green" else "dead"

    for k, sl in enumerate(ndi.find_objects(components), 1):
        if sl is None:
            continue
        region = components[sl] == k
        if region.sum() < min_area:
            continue
        z = contrast[sl]
        if z[region].max() < high:
            continue

        # Original peak ordering/tie behavior and priority flood are retained.
        peaks = (
            (z == ndi.maximum_filter(z, size=settings["peak_window_px"], mode="constant"))
            & region & (z >= high)
        )
        yy, xx = np.where(peaks)
        order = np.argsort(z[yy, xx])[::-1]
        seeds = []
        for t in order:
            y, x = int(yy[t]), int(xx[t])
            if not seeds or min(
                (y - y0) ** 2 + (x - x0) ** 2 for y0, x0 in seeds
            ) >= settings["min_peak_distance_px"] ** 2:
                seeds.append((y, x))
        if not seeds:
            seeds = [np.unravel_index(np.argmax(np.where(region, z, -np.inf)), z.shape)]
        assigned = np.zeros(region.shape, np.int32)
        heap = []
        for j, (y, x) in enumerate(seeds, 1):
            assigned[y, x] = j
            heapq.heappush(heap, (-z[y, x], y, x, j))
        while heap:
            value, y, x, j = heapq.heappop(heap)
            for dy, dx in NEIGHBORS:
                ny, nx = y + dy, x + dx
                if (
                    0 <= ny < region.shape[0] and 0 <= nx < region.shape[1]
                    and region[ny, nx] and assigned[ny, nx] == 0
                ):
                    assigned[ny, nx] = j
                    heapq.heappush(heap, (max(value, -z[ny, nx]), ny, nx, j))

        for j in range(1, len(seeds) + 1):
            sub = assigned == j
            if sub.sum() < min_area:
                continue
            y, x = np.where(sub)
            gy, gx = y + sl[0].start, x + sl[1].start
            if settings["exclude_border"] and (
                (gy == 0).any() or (gx == 0).any()
                or (gy == a.shape[0] - 1).any() or (gx == a.shape[1] - 1).any()
            ):
                continue
            ident = len(rows) + 1
            out[gy, gx] = ident
            weights = z[y, x]
            rows.append(dict(
                channel=legacy_channel, id=ident,
                y=float(np.average(gy, weights=weights)),
                x=float(np.average(gx, weights=weights)),
                area=int(len(y)), peak_contrast=float(z[y, x].max()),
                mean_raw=float(a[gy, gx].mean()), max_raw=float(a[gy, gx].max()),
                parent_count=len(seeds),
            ))
    return out, pd.DataFrame(rows, columns=SEGMENT_COLUMNS), contrast


def combine(gdf, rdf, greenlabels, redlabels, config):
    """Match green/red detections and create a single object table.

    A match requires both centroid proximity and overlap after square-mask
    dilation. Hungarian matching ensures at most one partner per detection.
    Green/red double-positive objects are counted once. Overlapping unmatched
    objects retain the original last-write label assignment and are flagged.
    """
    lg, lr = np.asarray(greenlabels), np.asarray(redlabels)
    if lg.ndim != 2 or lg.shape != lr.shape:
        raise ValueError("Green and red labels must have the same 2-D shape.")
    g, r = gdf.reset_index(drop=True), rdf.reset_index(drop=True)
    settings = config["matching"]
    dilation = int(settings["dilation_px"])
    radius = int(settings["overlap_window_radius_px"])
    pairs = {}
    if len(g) and len(r):
        distance = cdist(g[["y", "x"]], r[["y", "x"]])
        admissible = distance <= settings["max_distance_px"]
        for gi in range(len(g)):
            for ri in np.where(admissible[gi])[0]:
                cy, cx = g.iloc[gi][["y", "x"]]
                yy, xx = int(round(cy)), int(round(cx))
                sl = (
                    slice(max(0, yy - radius), min(lg.shape[0], yy + radius + 1)),
                    slice(max(0, xx - radius), min(lg.shape[1], xx + radius + 1)),
                )
                greenmask = lg[sl] == int(g.iloc[gi].id)
                if dilation:
                    greenmask = ndi.binary_dilation(
                        greenmask, structure=np.ones((2 * dilation + 1, 2 * dilation + 1))
                    )
                if not np.any(greenmask & (lr[sl] == int(r.iloc[ri].id))):
                    admissible[gi, ri] = False
        cost = np.where(admissible, distance, 1e6)
        gi, ri = linear_sum_assignment(cost)
        pairs = {int(i): int(j) for i, j in zip(gi, ri) if admissible[i, j]}

    records = []
    union = np.zeros(lg.shape, np.int32)
    for i, row in g.iterrows():
        matched = pairs.get(i)
        mask = lg == row.id
        if matched is not None:
            mask = mask | (lr == r.iloc[matched].id)
        cid = len(records) + 1
        union[mask] = cid
        records.append(dict(
            object_id=cid, y=row.y, x=row.x, green_id=int(row.id),
            red_id=int(r.iloc[matched].id) if matched is not None else 0,
            status="double_positive" if matched is not None else "live_only",
            area=int(mask.sum()), green_peak=row.peak_contrast,
            red_peak=float(r.iloc[matched].peak_contrast) if matched is not None else 0,
        ))
    used = set(pairs.values())
    for i, row in r.iterrows():
        if i in used:
            continue
        mask = lr == row.id
        cid = len(records) + 1
        union[mask] = cid
        records.append(dict(
            object_id=cid, y=row.y, x=row.x, green_id=0, red_id=int(row.id),
            status="dead_only", area=int(mask.sum()), green_peak=0,
            red_peak=row.peak_contrast,
        ))
    final_areas = np.bincount(union.ravel(), minlength=len(records) + 1)
    for row in records:
        remaining = int(final_areas[row["object_id"]])
        row["mask_pixels_final"] = remaining
        row["mask_pixels_overwritten"] = row["area"] - remaining
        row["mask_lost"] = remaining == 0
    return union, pd.DataFrame(records, columns=OBJECT_COLUMNS)


def bh(p):
    """Benjamini--Hochberg adjustment of finite p-values, preserving NaNs.

    The original formula and ordering are used for entirely finite inputs.
    Unscorable measurements remain missing and are not treated as negatives.
    """
    p = np.asarray(p, float)
    if p.ndim != 1:
        raise ValueError("p-values must be a one-dimensional array.")
    if np.isinf(p).any() or ((p < 0) | (p > 1)).any():
        raise ValueError("p-values must lie in [0, 1] or be NaN.")
    out = np.full_like(p, np.nan)
    valid = np.isfinite(p)
    values = p[valid]
    if not len(values):
        return out
    order = np.argsort(values)
    adjusted = np.empty_like(values)
    adjusted[order] = np.minimum.accumulate(
        (values[order] * len(values) / np.arange(1, len(values) + 1))[::-1]
    )[::-1].clip(0, 1)
    out[valid] = adjusted
    return out


def measure_ebfp(blue, labels, objectsDF, sample, config, dilation=None, shift=(0, 0)):
    """Measure blue enrichment against translated shape-matched cell-free ROIs.

    This returns empirical p-values. The caller applies pooled BH correction
    across the entire run, plus per-sample correction if desired. It is an
    image-background enrichment test, not a reporter-specific negative control
    or evidence of ongoing protein production. ``shift`` uses circular shifts
    only for diagnostic null checks and must be (0, 0) for primary analysis.
    """
    blue = _image(blue)
    labels = np.asarray(labels)
    if labels.shape != blue.shape:
        raise ValueError("Blue image and object labels must have the same shape.")
    settings = config["ebfp"]
    dilation = int(settings["roi_dilation_px"] if dilation is None else dilation)
    inner, outer = int(settings["null_inner_px"]), int(settings["null_outer_px"])
    occupied_dilation = int(settings["occupied_dilation_px"])
    if dilation < 0 or occupied_dilation < 0 or not 0 <= inner < outer:
        raise ValueError("Require nonnegative dilation and 0 <= inner < outer.")
    if len(shift) != 2 or any(int(s) != s for s in shift):
        raise ValueError("shift must contain two integer pixel offsets.")
    if any(shift):
        blue = np.roll(blue, tuple(int(s) for s in shift), axis=(0, 1))
    occupied = labels > 0
    if occupied_dilation:
        occupied = ndi.binary_dilation(occupied, iterations=occupied_dilation)
    result = []
    for row in objectsDF.to_dict("records"):
        object_id = int(row["object_id"])
        ys, xs = np.where(labels == object_id)
        if not len(ys):
            # Retain the denominator and expose this legacy mask failure.
            result.append({
                **row, "sample": sample, "ebfp_roi_pixels": 0,
                "ebfp_sum": np.nan, "ebfp_mean": np.nan,
                "ebfp_nonzero_pixels": 0, "ebfp_bg_mean": np.nan,
                "ebfp_bg_sd": np.nan, "ebfp_bg_q99": np.nan,
                "ebfp_shift_locations": 0, "ebfp_p": np.nan,
                "ebfp_enrichment": np.nan, "ebfp_scorable": False,
                "ebfp_status": "missing_object_mask",
            })
            continue
        y0 = max(0, int(ys.min()) - dilation)
        y1 = min(blue.shape[0], int(ys.max()) + dilation + 1)
        x0 = max(0, int(xs.min()) - dilation)
        x1 = min(blue.shape[1], int(xs.max()) + dilation + 1)
        kernel = labels[y0:y1, x0:x1] == object_id
        if dilation:
            kernel = ndi.binary_dilation(kernel, iterations=dilation)
        kernel &= (
            (labels[y0:y1, x0:x1] == 0)
            | (labels[y0:y1, x0:x1] == object_id)
        )
        kernel = kernel.astype(float)
        area = kernel.sum()
        py0, py1 = max(0, y0 - outer), min(blue.shape[0], y1 + outer)
        px0, px1 = max(0, x0 - outer), min(blue.shape[1], x1 + outer)
        sums = correlate(blue[py0:py1, px0:px1], kernel, mode="valid", method="fft")
        covered = correlate(
            occupied[py0:py1, px0:px1].astype(float), kernel, mode="valid", method="fft"
        )
        dy = np.arange(sums.shape[0]) + py0 - y0
        dx = np.arange(sums.shape[1]) + px0 - x0
        rr = dy[:, None] ** 2 + dx[None, :] ** 2
        eligible = (rr >= inner ** 2) & (rr <= outer ** 2) & (covered < .5)
        null = sums[eligible]
        observation = float((blue[y0:y1, x0:x1] * kernel).sum())
        scorable = len(null) >= settings["min_null_locations"]
        p = float((1 + np.count_nonzero(
            null >= observation - settings["comparison_tolerance"]
        )) / (len(null) + 1)) if scorable else np.nan
        mean = float(observation / area)
        result.append({
            **row, "sample": sample, "ebfp_roi_pixels": int(area),
            "ebfp_sum": observation, "ebfp_mean": mean,
            "ebfp_nonzero_pixels": int(((blue[y0:y1, x0:x1] > 0) * kernel).sum()),
            "ebfp_bg_mean": float(np.mean(null) / area) if len(null) else np.nan,
            "ebfp_bg_sd": float(np.std(null, ddof=1) / area) if len(null) > 1 else np.nan,
            "ebfp_bg_q99": float(np.quantile(null, .99) / area) if len(null) else np.nan,
            "ebfp_shift_locations": len(null), "ebfp_p": p,
            "ebfp_enrichment": mean / float(np.mean(null) / area)
            if len(null) and np.mean(null) > 0 else np.nan,
            "ebfp_scorable": bool(scorable),
            "ebfp_status": "scorable" if scorable else "insufficient_null_locations",
        })
    columns = list(objectsDF.columns) + [c for c in EBFP_COLUMNS if c not in objectsDF.columns]
    return pd.DataFrame(result, columns=columns)
