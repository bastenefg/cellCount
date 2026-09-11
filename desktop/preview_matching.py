"""Exact preview equivalent of ``pipeline.core.combine`` with indexed masks.

The matching decisions and record order retain the original implementation.
Only the repeated full-image mask scans are replaced: each channel label maps
to its object ID, and the larger ID wins at overlaps, exactly reproducing the
original last-write behavior. The batch-analysis implementation is unchanged.
"""
import numpy as np
import pandas as pd
from scipy import ndimage as ndi
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist

from pipeline.core import OBJECT_COLUMNS


def combine_preview(gdf, rdf, greenlabels, redlabels, config):
    """Combine the dense nonnegative labels produced by ``segment`` exactly."""
    lg, lr = np.asarray(greenlabels), np.asarray(redlabels)
    if lg.ndim != 2 or lg.shape != lr.shape:
        raise ValueError("Green and red labels must have the same 2-D shape.")
    g, r = gdf.reset_index(drop=True), rdf.reset_index(drop=True)
    settings = config["matching"]
    dilation = int(settings["dilation_px"])
    radius = int(settings["overlap_window_radius_px"])
    pairs = {}
    if len(g) and len(r):
        # Intentionally identical to the original matching path, including
        # pandas centroid values, Python rounding and Hungarian tie behavior.
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

    # Channel labels are small positive integers assigned by segment. Lookup
    # tables avoid allocating and summing a megapixel mask for every detection.
    gsize = max(int(lg.max(initial=0)), int(g.id.max()) if len(g) else 0) + 1
    rsize = max(int(lr.max(initial=0)), int(r.id.max()) if len(r) else 0) + 1
    green_map = np.zeros(gsize, dtype=np.int32)
    red_map = np.zeros(rsize, dtype=np.int32)
    green_areas = np.bincount(lg.ravel(), minlength=gsize)
    red_areas = np.bincount(lr.ravel(), minlength=rsize)
    records = []
    for i, row in g.iterrows():
        matched = pairs.get(i)
        cid = len(records) + 1
        green_id = int(row.id)
        red_id = int(r.iloc[matched].id) if matched is not None else 0
        green_map[green_id] = cid
        if red_id:
            red_map[red_id] = cid
        records.append(dict(
            object_id=cid, y=row.y, x=row.x, green_id=green_id,
            red_id=red_id,
            status="double_positive" if matched is not None else "live_only",
            area=int(green_areas[green_id] + (red_areas[red_id] if red_id else 0)),
            green_peak=row.peak_contrast,
            red_peak=float(r.iloc[matched].peak_contrast) if matched is not None else 0,
        ))
    used = set(pairs.values())
    for i, row in r.iterrows():
        if i in used:
            continue
        cid = len(records) + 1
        red_id = int(row.id)
        red_map[red_id] = cid
        records.append(dict(
            object_id=cid, y=row.y, x=row.x, green_id=0, red_id=red_id,
            status="dead_only", area=int(red_areas[red_id]), green_peak=0,
            red_peak=row.peak_contrast,
        ))

    green_objects, red_objects = green_map[lg], red_map[lr]
    # The area of a matched object is a set union, so subtract pixels where
    # both its channel masks overlap. Distinct objects are not subtracted.
    shared = (green_objects == red_objects) & (green_objects != 0)
    intersections = np.bincount(green_objects[shared], minlength=len(records) + 1)
    union = np.maximum(green_objects, red_objects)
    final_areas = np.bincount(union.ravel(), minlength=len(records) + 1)
    for row in records:
        cid = row["object_id"]
        row["area"] -= int(intersections[cid])
        remaining = int(final_areas[cid])
        row["mask_pixels_final"] = remaining
        row["mask_pixels_overwritten"] = row["area"] - remaining
        row["mask_lost"] = remaining == 0
    return union, pd.DataFrame(records, columns=OBJECT_COLUMNS)
