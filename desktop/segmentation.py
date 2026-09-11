"""Full-image previews using the same unmodified analysis as a complete run.

Display controls belong to the viewer: neither brightness nor a viewport crop
is applied here. The returned raw intensities, working contrast, and labels
all retain the original image dimensions and coordinates.
"""
from __future__ import annotations

from copy import deepcopy
from collections import OrderedDict
import json
import os
from pathlib import Path
from time import perf_counter

import numpy as np

from pipeline.core import segment
from pipeline.io import read_scalar, validate_config
from .preview_matching import combine_preview


ARRAY_KEYS = (
    "raw_green", "raw_red", "contrast_green", "contrast_red",
    "labels_green", "labels_red", "labels_objects",
)


def _json_value(value):
    """Detach metadata and convert Path/numpy values to standard JSON types."""
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    return value


def _settings_key(value):
    return json.dumps(_json_value(value), sort_keys=True, allow_nan=False)


def _remember(cache, key, value, limit):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)
    return value


def _cached(cache, key):
    if key not in cache:
        return None
    cache.move_to_end(key)
    return cache[key]


def _file_key(path):
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    return (os.path.normcase(str(path)), stat.st_size, stat.st_mtime_ns,
            stat.st_ctime_ns, stat.st_ino)


class PreviewSession:
    """Bounded, full-resolution caches for one persistent preview worker.

    Reading is keyed by resolved TIFF path/stat plus input settings. Each
    channel is keyed by its own parameters and the shared segmentation
    settings; matching changes therefore reuse both channel segmentations.
    The optional EBFP path is metadata only. Returned arrays are read-only,
    while returned metadata is detached from the cache and caller inputs.

    ``last_timings`` reports seconds and cache hits for diagnostics; timings
    are deliberately separate from reproducible scientific result metadata.
    Instances are intended for serial use by the isolated worker process.
    """

    def __init__(self):
        self._inputs = OrderedDict()
        self._channels = {channel: OrderedDict() for channel in ("green", "red")}
        self._combined = OrderedDict()
        self.last_timings = {}

    def clear(self):
        self._inputs.clear()
        for cache in self._channels.values():
            cache.clear()
        self._combined.clear()
        self.last_timings = {}

    def compute(self, row: dict, config: dict) -> dict:
        started = perf_counter()
        settings = deepcopy(config)
        field = _json_value(deepcopy(row))
        validate_config(settings)
        if not field.get("image_id"):
            raise ValueError("A field image_id is required for segmentation preview.")
        for channel in ("green", "red"):
            if not field.get(channel):
                raise ValueError(f"{field['image_id']}: a {channel} image is required.")
        timing = {}
        raw, channels, keys = {}, {}, {}
        shared = {key: value for key, value in settings["segmentation"].items()
                  if key not in ("green", "red")}
        for channel in ("green", "red"):
            tick = perf_counter()
            source_key = _file_key(field[channel])
            input_key = (source_key, _settings_key(settings["input"]))
            pixels = _cached(self._inputs, input_key)
            timing[f"{channel}_input_cached"] = pixels is not None
            if pixels is None:
                pixels = read_scalar(field[channel], settings)
                if _file_key(field[channel]) != source_key:
                    raise ValueError(f"{field[channel]} changed while it was being read. Try again.")
                pixels.setflags(write=False)
                _remember(self._inputs, input_key, pixels, 4)
            raw[channel] = pixels
            timing[f"read_{channel}_seconds"] = perf_counter() - tick
            channel_key = (input_key, _settings_key(shared),
                           _settings_key(settings["segmentation"][channel]))
            keys[channel] = channel_key
            tick = perf_counter()
            result = _cached(self._channels[channel], channel_key)
            timing[f"{channel}_segmentation_cached"] = result is not None
            if result is None:
                labels, detections, contrast = segment(pixels, channel, settings)
                labels.setflags(write=False)
                contrast.setflags(write=False)
                result = (labels, detections, contrast,
                          _json_value(detections.to_dict("records")))
                _remember(self._channels[channel], channel_key, result, 2)
            channels[channel] = result
            timing[f"segment_{channel}_seconds"] = perf_counter() - tick

        tick = perf_counter()
        combined_key = (keys["green"], keys["red"], _settings_key(settings["matching"]))
        combined = _cached(self._combined, combined_key)
        timing["matching_cached"] = combined is not None
        if combined is None:
            labels_objects, objects = combine_preview(
                channels["green"][1], channels["red"][1],
                channels["green"][0], channels["red"][0], settings)
            labels_objects.setflags(write=False)
            status_counts = objects["status"].value_counts()
            live_only = int(status_counts.get("live_only", 0))
            total = len(objects)
            counts = {
                "green_detections": len(channels["green"][1]),
                "red_detections": len(channels["red"][1]),
                "live_only": live_only,
                "dead_only": int(status_counts.get("dead_only", 0)),
                "double_positive": int(status_counts.get("double_positive", 0)),
                "total": total,
                "viability_percent": 100.0 * live_only / total if total else None,
            }
            combined = (labels_objects, _json_value(objects.to_dict("records")), counts)
            _remember(self._combined, combined_key, combined, 2)
        timing["matching_seconds"] = perf_counter() - tick
        result = {
            "schema_version": 1, "image_id": field["image_id"],
            "row": field, "config": _json_value(settings),
            "raw_green": raw["green"], "raw_red": raw["red"],
            "contrast_green": channels["green"][2], "contrast_red": channels["red"][2],
            "labels_green": channels["green"][0], "labels_red": channels["red"][0],
            "labels_objects": combined[0],
            "detections_green": deepcopy(channels["green"][3]),
            "detections_red": deepcopy(channels["red"][3]),
            "objects": deepcopy(combined[1]), "counts": deepcopy(combined[2]),
        }
        timing["total_seconds"] = perf_counter() - started
        self.last_timings = timing
        return result


def compute_preview(row: dict, config: dict) -> dict:
    """One-shot compatible preview. Reuse PreviewSession for parameter edits."""
    return PreviewSession().compute(row, config)


def _write_payload(preview, output, compressed):
    metadata = {key: value for key, value in preview.items() if key not in ARRAY_KEYS}
    metadata_text = json.dumps(metadata, indent=2, allow_nan=False) + "\n"
    save = np.savez_compressed if compressed else np.savez
    save(output / "arrays.npz", **{key: preview[key] for key in ARRAY_KEYS})
    (output / "metadata.json").write_text(metadata_text, encoding="utf-8")


def write_preview_data(preview: dict, output_dir, compressed=False):
    """Write an already computed preview to a NEW directory; raise on error.

    Uncompressed NumPy arrays avoid expensive compression in local interactive
    traffic. Callers publish completion only after this function returns.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    _write_payload(preview, output, compressed)


def write_preview(request_path, output_dir) -> int:
    """Execute a JSON {row, config} request in a new private output folder.

    Returns zero on success, one on failure. Only a directory created by this
    invocation may receive output or error.json; existing results are never
    overwritten. Errors before directory creation cannot produce an error file.
    """
    output = Path(output_dir)
    created = False
    try:
        output.mkdir(parents=True, exist_ok=False)
        created = True
        request = json.loads(Path(request_path).read_text(encoding="utf-8-sig"))
        preview = compute_preview(request["row"], request["config"])
        _write_payload(preview, output, compressed=False)
        return 0
    except Exception as exc:
        if created:
            try:
                # Exclusive creation keeps even error handling non-destructive.
                with (output / "error.json").open("x", encoding="utf-8") as stream:
                    json.dump({"error": str(exc), "error_type": type(exc).__name__},
                              stream, indent=2, allow_nan=False)
                    stream.write("\n")
            except OSError:
                pass
        return 1


def read_preview(output_dir) -> dict:
    """Load preview metadata and numeric arrays; never enable pickle loading."""
    output = Path(output_dir)
    error_path = output / "error.json"
    if error_path.is_file():
        error = json.loads(error_path.read_text(encoding="utf-8"))
        raise ValueError(error.get("error", "Segmentation preview failed."))
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise ValueError("Unsupported segmentation preview metadata.")
    with np.load(output / "arrays.npz", allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in ARRAY_KEYS}
    shape = arrays["raw_green"].shape
    if len(shape) != 2 or 0 in shape or any(array.shape != shape for array in arrays.values()):
        raise ValueError("Preview arrays must share the original two-dimensional image shape.")
    return {**metadata, **arrays}
