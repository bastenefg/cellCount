"""Read-only diagnostic of supplied raw TIFFs and unchanged baseline segmentation.

This script writes only into its own diagnostic directory. It does not tune
parameters or infer ground-truth cell identities from intensity measurements.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image
from scipy import ndimage as ndi

from pipeline.core import segment

OUT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "runs/new_experiment/effective_config.json").read_text())


def read(path):
    with Image.open(ROOT / path) as im:
        return np.asarray(im).copy()


def edges(labels):
    return (labels > 0) & (ndi.minimum_filter(labels, size=3) != labels)


def raw_rgb(green, red):
    rgb = np.zeros((*green.shape, 3), float)
    rgb[..., 0] = np.clip(red / 30, 0, 1)
    rgb[..., 1] = np.clip(green / 120, 0, 1)
    return rgb


def image_panel(ax, image, labels=None, color=(0, 1, 1), title="", crop=None, vmax=30):
    crop = crop or (slice(None), slice(None))
    a = image[crop]
    if a.ndim == 2:
        a = np.repeat(np.clip(a.astype(float) / vmax, 0, 1)[..., None], 3, axis=-1)
    else:
        a = a.copy()
    if labels is not None:
        a[edges(labels)[crop]] = color
    ax.imshow(a, interpolation="nearest")
    ax.set_title(title, fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])


def summarize(name, raw, labels, objects):
    values, counts = np.unique(raw, return_counts=True)
    order = np.argsort(counts)[::-1]
    return {
        "image": name,
        "shape": list(raw.shape),
        "dtype": str(raw.dtype),
        "n_unique_raw_levels": len(values),
        "raw_percentiles": dict(zip(["0", "25", "50", "75", "90", "95", "99", "99.9", "100"], np.percentile(raw, [0, 25, 50, 75, 90, 95, 99, 99.9, 100]).tolist())),
        "most_common_raw_levels": [{"value": int(values[i]), "pixels": int(counts[i]), "percent": float(counts[i] * 100 / raw.size)} for i in order[:12]],
        "detections": len(objects),
        "segmented_pixels": int(np.count_nonzero(labels)),
        "area_median_px": float(objects.area.median()),
        "area_le_20_px_count": int((objects.area <= 20).sum()),
        "area_lt_25_px_count": int((objects.area < 25).sum()),
        "peak_contrast_median": float(objects.peak_contrast.median()),
        "peak_contrast_lt_6_count": int((objects.peak_contrast < 6).sum()),
        "raw_max_le_15_count": int((objects.max_raw <= 15).sum()),
        "single_seed_parent_count": int((objects.parent_count == 1).sum()),
    }


def main():
    green = read("images/test1_live.tif")
    red = read("images/test1_dead.tif")
    with np.load(ROOT / "runs/new_experiment/labels/alexandra_test1.npz") as saved:
        gl, rl = saved["live"].copy(), saved["dead"].copy()
    gdf = pd.read_csv(ROOT / "runs/new_experiment/channel_detections/alexandra_test1_green.csv")
    rdf = pd.read_csv(ROOT / "runs/new_experiment/channel_detections/alexandra_test1_red.csv")
    fresh_red, fresh_df, contrast = segment(red, "red", CONFIG)
    assert np.array_equal(fresh_red, rl), "Saved baseline labels differ from original core."
    assert len(fresh_df) == len(rdf)
    crop = (slice(384, 640), slice(384, 640))
    fig, ax = plt.subplots(2, 3, figsize=(13, 9), constrained_layout=True)
    for row, sl, prefix in [(0, None, "Full 1024 x 1024 field"), (1, crop, "Central 256 x 256 crop")]:
        image_panel(ax[row, 0], green, title=f"{prefix}: LIVE raw (display 0-120)", crop=sl, vmax=120)
        image_panel(ax[row, 1], red, title=f"{prefix}: DEAD raw (display 0-30)", crop=sl)
        combined = raw_rgb(green, red)
        combined[edges(gl)] = (0, 1, 1)
        combined[edges(rl)] = (1, 0.65, 0)
        image_panel(ax[row, 2], combined, title="Original contours: LIVE cyan / DEAD amber", crop=sl)
    fig.suptitle("samples.csv / test1: 106 LIVE detections; 1,402 DEAD detections", fontsize=16)
    fig.supxlabel("Original arrays and baseline masks; contrast scaling is for display only. Detected objects are not manually validated cells.", fontsize=10)
    fig.savefig(OUT / "test1_raw_and_original_boundaries.png", dpi=180)
    plt.close(fig)

    # Raw and contour images have identical fixed display limits in each row.
    fig, ax = plt.subplots(2, 3, figsize=(12, 8.5), constrained_layout=True)
    refs = []
    for name in ["S5", "S6", "S7"]:
        raw = read(f"reference/raw/CHO-{name}-dead.tif")
        df = pd.read_csv(ROOT / f"reference/expected/{name}_red.csv")
        with np.load(ROOT / f"reference/expected/{name}_masks.npz") as saved:
            label = saved["dead"].copy()
        refs.append((name, raw, label, df))
    s5name, s5raw, s5labels, s5df = refs[0]
    for row, (name, raw, labels) in enumerate([("Reference S5", s5raw, s5labels), ("test1", red, rl)]):
        image_panel(ax[row, 0], raw, title=f"{name} DEAD: raw, full field", vmax=30)
        image_panel(ax[row, 1], raw, title=f"{name} DEAD: central crop, raw", crop=crop, vmax=30)
        image_panel(ax[row, 2], raw, labels, color=(1, 0.65, 0), title=f"{name}: unchanged DEAD contours", crop=crop, vmax=30)
    fig.suptitle("Same DEAD display range (0-30): reference versus supplied test1", fontsize=15)
    fig.supxlabel("Central crop: x=384:640, y=384:640. Amber contours are baseline labels; no threshold change or denoising.", fontsize=10)
    fig.savefig(OUT / "dead_reference_comparison.png", dpi=180)
    plt.close(fig)

    stats = [summarize("test1 LIVE", green, gl, gdf), summarize("test1 DEAD", red, rl, rdf)]
    for name, raw, label, df in refs:
        stats.append(summarize(f"{name} DEAD", raw, label, df))
    report = {
        "purpose": "Inspect raw input and unchanged segmentation, without choosing a target viability or biological ground truth.",
        "baseline_red_labels_exactly_reproduced": True,
        "baseline_segmentation": CONFIG["segmentation"],
        "sample_summary": pd.read_csv(ROOT / "runs/new_experiment/image_summary.csv").fillna("").to_dict("records")[0],
        "images": stats,
    }
    (OUT / "dead_signal_diagnostics.json").write_text(json.dumps(report, indent=2) + "\n")
    pd.DataFrame([{k: v for k, v in x.items() if not isinstance(v, (list, dict))} for x in stats]).to_csv(OUT / "detection_comparison.csv", index=False)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
