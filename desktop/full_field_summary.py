"""Full-field presentation of saved measurements; never repeats segmentation."""
from pathlib import Path
import csv
import hashlib
import json
import math
import shutil
import statistics
import tempfile

from .services import read_manifest, read_results


RENDERER_VERSION = "2"
REQUIRED_FILES = ("run_manifest.json", "effective_config.json", "resolved_samples.csv",
                  "image_summary.csv", "replicate_summary.csv", "aggregate_summary.json")


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cache_key(run):
    """Hash small saved records plus source identities, without reading TIFF pixels."""
    run = Path(run).resolve()
    files = {name: _hash(run / name) for name in REQUIRED_FILES}
    if (run / "figure_metadata.json").is_file():
        files["figure_metadata.json"] = _hash(run / "figure_metadata.json")
    if (run / "output_checksums.json").is_file():
        files["output_checksums.json"] = _hash(run / "output_checksums.json")
    with (run / "image_summary.csv").open(newline="", encoding="utf-8-sig") as stream:
        selected = _representative(run, list(csv.DictReader(stream)))
    entries = read_manifest(run / "resolved_samples.csv")
    if selected not in {entry["image_id"] for entry in entries}:
        raise ValueError("The representative field is not in this run's saved input manifest.")
    files["labels/" + selected + ".npz"] = _hash(run / "labels" / (selected + ".npz"))
    sources = []
    for entry in entries:
        for role in ("green", "red", "ebfp"):
            if entry.get(role):
                path = Path(entry[role]).resolve()
                info = path.stat()
                sources.append({"path": str(path), "bytes": info.st_size, "mtime_ns": info.st_mtime_ns})
    state = {"renderer_version": RENDERER_VERSION, "run": str(run), "files": files, "sources": sources}
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode("utf-8")).hexdigest()


def _number(value):
    if value is None or value == "":
        return float("nan")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Saved summary contains a nonfinite measurement.")
    return number


def _representative(run, rows):
    try:
        selected = json.loads((run / "figure_metadata.json").read_text(encoding="utf-8")).get("representative_image_id")
    except (OSError, ValueError, AttributeError):
        selected = None
    if any(row["image_id"] == selected for row in rows):
        return selected
    values = [_number(row.get("viability_percent")) for row in rows]
    valid = [index for index, value in enumerate(values) if math.isfinite(value)]
    median = statistics.median(values[index] for index in valid) if valid else 0
    index = min(valid, key=lambda index: abs(values[index] - median)) if valid else 0
    return rows[index]["image_id"]


def _verify_saved_outputs(run, names):
    checksums = run / "output_checksums.json"
    if not checksums.is_file():
        return False
    recorded = json.loads(checksums.read_text(encoding="utf-8"))
    if not isinstance(recorded, dict):
        raise ValueError("Invalid saved output checksum record.")
    recorded = {name.replace("\\", "/"): digest for name, digest in recorded.items()}
    for name in names:
        name = name.replace("\\", "/")
        if name not in recorded or _hash(run / name) != recorded[name]:
            raise ValueError("A saved analysis output has changed or cannot be verified: " + name)
    return True


def _outline_segments(labels):
    """Pixel-edge segments from saved integer labels, including touching cells."""
    import numpy as np
    left = np.pad(labels, ((0, 0), (1, 0)))
    right = np.pad(labels, ((0, 0), (0, 1)))
    y, x = np.nonzero(left != right)
    vertical = np.stack([np.column_stack((x - .5, y - .5)), np.column_stack((x - .5, y + .5))], axis=1)
    top = np.pad(labels, ((1, 0), (0, 0)))
    bottom = np.pad(labels, ((0, 1), (0, 0)))
    y, x = np.nonzero(top != bottom)
    horizontal = np.stack([np.column_stack((x - .5, y - .5)), np.column_stack((x + .5, y - .5))], axis=1)
    return np.concatenate((vertical, horizontal), axis=0)


def _leica_context(imports, selected):
    descriptions = []
    for imported in imports:
        if imported.get("analysis_field_id") != selected:
            continue
        selection = imported.get("selection", {})
        name = selection.get("series_name", selected)
        total = selection.get("sizes", {}).get("Z", "?")
        if selection.get("mode") == "max":
            z = f"maximum projection Z {selection.get('z_start', 0) + 1}–{selection.get('z_stop', '?')} of {total}"
        else:
            z = f"Z slice {(selection.get('z_index') or 0) + 1} of {total}"
        descriptions.append(f"Leica {name} · {z} · time point {selection.get('time_index', 0) + 1}")
    return "; ".join(descriptions)


def _render(result, entry, config, channels, selected, context, masks):
    import numpy as np
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from pipeline.reporting import rgb
    has_ebfp = any(row.get("ebfp") for row in result["entries"])
    combined, mapped = rgb(channels, config)
    h, w = channels["green"].shape
    ncols = 4 if has_ebfp else 3
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none"}):
        fig = plt.figure(figsize=(18 if has_ebfp else 15, 11))
        title = "Cell viability and detectable EBFP fluorescence" if has_ebfp else "Cell viability from live/dead fluorescence"
        fig.text(.05, .955, title, fontsize=21, weight="bold")
        fig.text(.05, .917, f"{len(result['image'])} fields · {len(result['replicate'])} replicate groups · mean ± sample SD · exploratory object counts", color="#59636e")
        fig.text(.05, .885, "Run: " + result["path"].name, fontsize=10, color="#59636e")
        if context:
            fig.text(.05, .852, context, fontsize=10, color="#59636e")
        gs = fig.add_gridspec(3, ncols, left=.065, right=.96, bottom=.17, top=.81,
                              height_ratios=[.85, .25, 1.55], hspace=.29, wspace=.20)
        charts = [(slice(0, 2) if has_ebfp else slice(0, 3), "A   Image-based viability", ["viability_percent"], "#008d54")]
        if has_ebfp:
            charts.append((slice(2, 4), "B   Detectable EBFP", ["ebfp_all_percent", "ebfp_live_percent"], "#4466aa"))
        for columns, heading, metrics, color in charts:
            ax = fig.add_subplot(gs[0, columns])
            ax.set_title(heading, loc="left", weight="bold")
            ax.set_ylim(0, 100)
            ax.set_ylabel("Objects (%)")
            ax.grid(axis="y", alpha=.2)
            ax.set_axisbelow(True)
            for j, metric in enumerate(metrics):
                if metric not in result["stats"] or any(metric not in row for row in result["replicate"]):
                    raise ValueError("Saved run is missing the summary measurement " + metric)
                values = np.asarray([_number(row.get(metric)) for row in result["replicate"]])
                ax.scatter(j + np.linspace(-.15, .15, len(values)), values, c=color, s=35, zorder=3)
                record = result["stats"][metric]
                mean, sd = _number(record.get("mean")), _number(record.get("sample_sd"))
                if np.isfinite(mean):
                    ax.errorbar(j + .29, mean, yerr=sd if np.isfinite(sd) else None, fmt="_", color="#25313b", capsize=7, markersize=13)
                text = (f"{mean:.1f} ± {sd:.1f}%" if np.isfinite(sd) else
                        f"{mean:.1f}% (SD undefined)" if np.isfinite(mean) else "Undefined")
                ax.text(j, 5, text, ha="center", color=color, weight="bold", fontsize=11)
            ax.set_xlim(-.45, len(metrics) - .4)
            labels = ["Live only / all detected"] if metrics == ["viability_percent"] else ["All scorable objects", "Scorable live-only objects"]
            ax.set_xticks(range(len(metrics)), labels)
        heading = fig.add_subplot(gs[1, :])
        heading.axis("off")
        heading.text(0, .45, f"{'C' if has_ebfp else 'B'}   Full field {selected} · {w} × {h} pixels", transform=heading.transAxes, weight="bold", fontsize=14)
        zero = np.zeros_like(mapped["green"])
        panels = [(np.stack([zero, mapped["green"], zero], axis=2), "Live · green", False, "live", "#6fffff", "green"),
                  (np.stack([mapped["red"], zero, zero], axis=2), "Dead · red", False, "dead", "#ffcc66", "red")]
        if has_ebfp:
            panels.append((mapped.get("ebfp"), "EBFP · grayscale", True, "objects", "white", None))
        panels.append((combined, "Merged · RGB" if "ebfp" in channels else "Merged · live/dead", False, "objects", "white", None))
        for j, (panel, title, gray, mask_key, color, role) in enumerate(panels):
            ax = fig.add_subplot(gs[2, j])
            if role:
                settings = config["segmentation"][role]
                title += f"\nRegion {settings['low']} · Peak {settings['high']}\nσ {settings['sigma_px']} px · area ≥ {settings['min_area_px']} px"
            else:
                title += "\nSaved combined-object outlines"
            ax.set_title(title, fontsize=10)
            ax.axis("off")
            if panel is None:
                ax.text(.5, .5, "Not acquired", ha="center", va="center", transform=ax.transAxes, color="#65717b")
                ax.set_aspect("equal")
                continue
            ax.imshow(panel, cmap="gray" if gray else None, vmin=0, vmax=1, interpolation="nearest",
                      extent=(-.5, w - .5, h - .5, -.5))
            ax.add_collection(LineCollection(_outline_segments(masks[mask_key]), colors=color,
                                            linewidths=.55, label="saved_" + mask_key + "_outlines"))
            ax.set_xlim(-.5, w - .5)
            ax.set_ylim(h - .5, -.5)
            bar = config["display"]["scale_bar_um"]
            length = bar / config["input"]["pixel_size_um"][1]
            if length < w * .6:
                x, y = w * .92, h * .91
                ax.plot([x - length, x], [y, y], c="white", lw=3)
                ax.text(x - length / 2, y - h * .035, f"{bar:g} µm", ha="center", color="white", fontsize=9)
        bounds = "; ".join(f"{role} {config['display'][role][0]:g}–{config['display'][role][1]:g}" for role in channels)
        fig.text(.05, .135, "Outlines: saved detections. LIVE: cyan · DEAD: amber · merged: white combined objects. Segmentation is not recalculated.", fontsize=9, color="#465961")
        segmentation = config["segmentation"]
        fig.text(.05, .11, f"Detection thresholds apply to corrected signal; background σ {segmentation['background_sigma_px']:g} px · peak spacing {segmentation['min_peak_distance_px']:g} px · border exclusion {'on' if segmentation['exclude_border'] else 'off'}.", fontsize=9, color="#65717b")
        fig.text(.05, .085, "Full image extent; fixed linear display scaling: " + bounds + ". Values above bounds display-clipped.", fontsize=9, color="#65717b")
        fig.text(.05, .06, "Saved measurements are unchanged. Raw channel images remain visible beneath the saved detection outlines.", fontsize=9, color="#65717b")
        coverage = "EBFP fractions use scorable objects from fields where EBFP was acquired. " if has_ebfp else ""
        projection_note = " Projection counts represent 2D objects, not a 3D cell count." if "maximum projection" in context else ""
        fig.text(.05, .035, coverage + "Double-positive objects count as compromised." + projection_note, fontsize=9, color="#65717b")
        return fig


def build_full_field_summary(run, output):
    """Create a new PNG/SVG presentation without changing saved run contents."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from pipeline.io import read_scalar, validate_config
    run, output = Path(run).resolve(), Path(output).resolve()
    if output == run or output.is_relative_to(run) or any((parent / "run_manifest.json").exists() for parent in (output, *output.parents)):
        raise ValueError("Save full-field summary copies outside completed analysis run folders.")
    if output.exists():
        raise FileExistsError("Summary output already exists; choose a new cache folder.")
    key = cache_key(run)
    result = read_results(run)
    config = validate_config(json.loads((run / "effective_config.json").read_text(encoding="utf-8")))
    entries = read_manifest(run / "resolved_samples.csv")
    result["entries"] = entries
    selected = _representative(run, result["image"])
    entry = next((entry for entry in entries if entry["image_id"] == selected), None)
    if entry is None:
        raise ValueError("The representative field is not in this run's saved input manifest.")
    mask_name = "labels/" + selected + ".npz"
    audited = [*REQUIRED_FILES, mask_name]
    if (run / "figure_metadata.json").is_file():
        audited.append("figure_metadata.json")
    verified_outputs = _verify_saved_outputs(run, audited)
    with np.load(run / mask_name, allow_pickle=False) as archive:
        masks = {key: archive[key].copy() for key in ("live", "dead", "objects")}
    shape = tuple(config["input"]["expected_shape"])
    if any(array.shape != shape or not np.issubdtype(array.dtype, np.integer) or (array < 0).any() for array in masks.values()):
        raise ValueError("Saved detection masks must be nonnegative integer label images matching this run's TIFF dimensions.")
    expected = {str(Path(item["path"]).resolve()): item["sha256"] for item in result["meta"].get("input_files", [])}
    channels, hashes = {}, {}
    for role in ("green", "red", "ebfp"):
        if not entry.get(role):
            continue
        path = Path(entry[role]).resolve()
        digest = _hash(path)
        if str(path) in expected and digest != expected[str(path)]:
            raise ValueError("An input image has changed since analysis: " + str(path))
        hashes[role] = digest
        channels[role] = read_scalar(path, config)
    context = _leica_context(result.get("leica_imports", []), selected)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".full_field_pending_", dir=output.parent)).resolve()
    fig = None
    try:
        fig = _render(result, entry, config, channels, selected, context, masks)
        with plt.rc_context({"svg.fonttype": "none"}):
            fig.savefig(staging / "figure.png", format="png", dpi=240, facecolor="white")
            fig.savefig(staging / "figure.svg", format="svg", facecolor="white")
        h, w = channels["green"].shape
        metadata = {"renderer_version": RENDERER_VERSION, "cache_key": key, "run": str(run),
                    "representative_image_id": selected, "view": "full_field", "shape_yx": [h, w],
                    "crop_yx": [0, h, 0, w], "image_extent_xy": [-.5, w - .5, h - .5, -.5],
                    "representative_source_channel_paths": {role: entry[role] for role in channels},
                    "source_sha256": hashes, "display": config["display"], "pixel_size_um": config["input"]["pixel_size_um"],
                    "run_total_objects": sum(int(row["total"]) for row in result["image"]),
                    "representative_counts": next(row for row in result["image"] if row["image_id"] == selected),
                    "saved_statistics": result["stats"], "leica_context": context,
                    "segmentation": config["segmentation"], "saved_output_checksums_verified": verified_outputs,
                    "stored_detection_masks": {"path": str(run / mask_name), "sha256": _hash(run / mask_name),
                                               "arrays": ["live", "dead", "objects"],
                                               "positive_label_counts": {name: int(np.count_nonzero(np.unique(array))) for name, array in masks.items()}},
                    "artifacts": {ext: {"filename": "figure." + ext, "sha256": _hash(staging / ("figure." + ext))}
                                  for ext in ("png", "svg")}}
        (staging / "figure_metadata.json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if cache_key(run) != key:
            raise ValueError("The run or its source images changed while rendering. Reopen the run and try again.")
        staging.rename(output)
    finally:
        if fig is not None:
            plt.close(fig)
        if staging.exists() and staging.parent == output.parent and staging.name.startswith(".full_field_pending_"):
            shutil.rmtree(staging)
    return {"png": str(output / "figure.png"), "svg": str(output / "figure.svg"),
            "metadata": str(output / "figure_metadata.json"), "representative_image_id": selected}
