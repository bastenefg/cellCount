"""Fast viability reports assembled from immutable, threshold-matched 3D figures.

No original acquisition or volume masks are reopened. The central image panels
and their threshold labels are copied pixel-for-pixel from the verified saved
figure; only the surrounding report text is replaced. Cache entries are bound
to the source digests, review metrics and rendering version.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import textwrap


RENDER_VERSION = 1
LEGACY_LAYOUT = {"kind": "three_panel_3d", "canvas_pixels": [1500, 900],
                 "panel_strip": [0, 180, 1500, 690]}
PANEL_DESTINATION = (0, 205)


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def viability_caption(metrics):
    """Return a concise, explicit percent/range label and review progress."""
    pending = int(metrics["pending_groups"])
    reviewed = int(metrics["reviewed_groups"])
    mixed = int(metrics["mixed_groups"])
    low, high = metrics["viability_min_pct"], metrics["viability_max_pct"]
    if low is None or high is None:
        caption = "Viability not measurable · no detected cells"
    elif pending:
        caption = f"Provisional viability: {low:.1f}–{high:.1f}%"
    else:
        prefix = "Reviewed viability" if mixed else "Apparent viability"
        caption = f"{prefix}: {metrics['viability_pct']:.1f}%"
    progress = f"Mixed groups reviewed: {reviewed}/{mixed} · Pending: {pending}"
    if pending:
        progress += " · Association scenarios, not a confidence interval"
    return caption, progress


def _settings_lines(settings, summary):
    if settings.get("mode") == "projection_config":
        config = settings["config"]
        segmentation, matching = config["segmentation"], config["matching"]
        lines = [
            f"Saved projection controls: background σ {segmentation['background_sigma_px']:g} px; "
            f"min. XY area LIVE / DEAD {segmentation['green']['min_area_px']} / {segmentation['red']['min_area_px']} px",
            f"Peak window {segmentation['peak_window_px']} px; peak separation {segmentation['min_peak_distance_px']:g} px; "
            f"match distance {matching['max_distance_px']:g} px; dilation {matching['dilation_px']} px; "
            f"overlap window {matching['overlap_window_radius_px']:g} px; exclude XYZ border {segmentation['exclude_border']}",
        ]
    else:
        lines = [
            f"Min. volume LIVE / DEAD: {settings['green']['min_volume_um3']:g} / {settings['red']['min_volume_um3']:g} µm³; "
            f"seed spacing: {settings['min_seed_distance_um']:g} µm; maximum channel gap: {settings['match_distance_um']:g} µm"
        ]
    lines.append(f"Spacing Z/Y/X: {' / '.join(f'{v:g}' for v in summary['spacing_um'])} µm; "
                 f"volume: {' × '.join(map(str, summary['shape_zyx']))} voxels (Z/Y/X)")
    if summary.get("selection_description"):
        lines.append(summary["selection_description"])
    return lines


def _verified_sources(field_result):
    folder = Path(field_result["output_dir"]).resolve()
    checks_path = Path(field_result["checksums"]).resolve()
    if checks_path != folder / "output_checksums.json":
        raise ValueError("The figure integrity record is outside its saved result.")
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    paths, digests = {}, {}
    for key, filename in (("figure", "figure.png"), ("settings", "settings.json"), ("summary", "summary.json")):
        path = Path(field_result[key]).resolve()
        if path != folder / filename or not path.is_file() or _hash(path) != checks.get(filename):
            raise ValueError(f"Saved 3D artifact failed integrity verification: {filename}")
        paths[key], digests[key] = path, checks[filename]
    return folder, paths, digests


def _safe_destination(destination, folder):
    if destination.suffix.lower() != ".png":
        raise ValueError("A derived 3D summary figure must be a PNG file.")
    if destination == folder or destination.is_relative_to(folder):
        raise ValueError("Save the reviewed figure outside the original analysis.")
    for parent in destination.parents:
        if any((parent / marker).is_file() for marker in
               ("volume_run.json", "run_manifest.json", "result.json", "stack_manifest.json", "import_provenance.json")):
            raise ValueError("Save the reviewed figure outside completed analyses and source caches.")


def _render(original, metrics, settings, summary, revision_id):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from PIL import Image

    layout = summary.get("figure_layout", LEGACY_LAYOUT)
    if (layout != LEGACY_LAYOUT or original.size != tuple(layout["canvas_pixels"])):
        raise ValueError("This saved 3D figure layout is not supported; its image panels cannot be preserved safely.")
    figure = Figure(figsize=(10, 7), dpi=150, facecolor="white")
    canvas = FigureCanvasAgg(figure)
    figure.text(0.5, 0.969, "3D LIVE / DEAD viability review", ha="center", fontsize=16)
    field = summary.get("field_id") or "Saved 3D field"
    figure.text(0.5, 0.937, str(field), ha="center", fontsize=10)
    caption, progress = viability_caption(metrics)
    figure.text(0.5, 0.891, caption, ha="center", fontsize=17, color="#16756d")
    figure.text(0.5, 0.86, progress, ha="center", fontsize=9)
    live = str(metrics["live_min"]) if metrics["live_min"] == metrics["live_max"] else f"{metrics['live_min']}–{metrics['live_max']}"
    total = str(metrics["total_min"]) if metrics["total_min"] == metrics["total_max"] else f"{metrics['total_min']}–{metrics['total_max']}"
    figure.text(0.5, 0.828, f"LIVE: {live}    Nonviable (EthD-1-positive): {metrics['dead_count']}    Total: {total}",
                ha="center", fontsize=10)
    lines = [
        "Image panels and threshold labels copied unchanged from the saved segmentation; review adjusts cell identity only.",
        "L3224 assumptions: each channel object represents one cell; each true EthD-1-positive cell counts as nonviable.",
        "One confirmed LIVE/DEAD pair counts as one nonviable cell. Segmentation errors can exceed the displayed range.",
        "Neither a dual signal nor this percentage establishes apoptosis.",
        *_settings_lines(settings, summary),
        f"Review revision: {revision_id}",
    ]
    wrapped = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, width=143, break_long_words=True, break_on_hyphens=False))
    figure.text(0.5, 0.296, "\n".join(wrapped), va="top", ha="center", fontsize=8, linespacing=1.55)
    canvas.draw()
    output = Image.frombuffer("RGBA", canvas.get_width_height(), canvas.buffer_rgba(), "raw", "RGBA", 0, 1).convert("RGB")
    # No resizing, display re-normalization or source-pixel/mask access occurs.
    output.paste(original.convert("RGB").crop(tuple(layout["panel_strip"])), PANEL_DESTINATION)
    figure.clear()
    return output


def prepare_viability_figure(field_result, metrics, output_path, revision_id="provisional"):
    """Write or reuse a verified derived PNG, preserving every original file."""
    from PIL import Image

    folder, paths, digests = _verified_sources(field_result)
    destination = Path(output_path).expanduser().resolve()
    _safe_destination(destination, folder)
    settings = json.loads(paths["settings"].read_text(encoding="utf-8"))
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    if any(metrics.get(key) != summary["counts"][key] for key in ("green_objects", "red_objects")):
        raise ValueError("The viability metrics do not belong to this saved field.")
    sidecar = destination.with_suffix(destination.suffix + ".json")
    payload = {"render_version": RENDER_VERSION, "source_sha256": digests,
               "metrics": metrics, "revision_id": str(revision_id)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if destination.is_file() and sidecar.is_file():
        try:
            cached = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint and cached.get("sha256") == _hash(destination):
                return destination
        except (OSError, ValueError, TypeError):
            pass
    with Image.open(paths["figure"]) as original:
        rendered = _render(original, metrics, settings, summary, str(revision_id))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = []
    try:
        with tempfile.NamedTemporaryFile(prefix=".viability-", suffix=".png", dir=destination.parent, delete=False) as stream:
            png_path = Path(stream.name)
            temporary.append(png_path)
            rendered.save(stream, format="PNG", dpi=(150, 150))
        record = {**payload, "fingerprint": fingerprint, "sha256": _hash(png_path)}
        with tempfile.NamedTemporaryFile(mode="w", prefix=".viability-", suffix=".json", dir=destination.parent,
                                         encoding="utf-8", delete=False) as stream:
            record_path = Path(stream.name)
            temporary.append(record_path)
            json.dump(record, stream, indent=2, allow_nan=False)
        # Recheck the small source artifacts immediately before publication.
        if any(_hash(paths[key]) != digest for key, digest in digests.items()):
            raise ValueError("A saved 3D artifact changed while preparing its reviewed figure.")
        png_path.replace(destination)
        record_path.replace(sidecar)
        return destination
    finally:
        rendered.close()
        for path in temporary:
            path.unlink(missing_ok=True)
