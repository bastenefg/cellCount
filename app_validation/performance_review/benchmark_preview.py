"""Reproducible full-resolution benchmark; no source-image/config writes."""
from copy import deepcopy
import json
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np

from desktop.segmentation import PreviewSession, read_preview, write_preview_data
from desktop.services import default_config, read_manifest
from pipeline.core import combine, segment
from pipeline.io import read_scalar


def legacy_preview(row, config):
    tick = perf_counter()
    raw_green, raw_red = read_scalar(row["green"], config), read_scalar(row["red"], config)
    loaded = perf_counter()
    lg, g, cg = segment(raw_green, "green", config)
    lr, r, cr = segment(raw_red, "red", config)
    segmented = perf_counter()
    lo, objects = combine(g, r, lg, lr, config)
    finished = perf_counter()
    return {"labels_green": lg, "labels_red": lr, "labels_objects": lo,
            "contrast_green": cg, "contrast_red": cr,
            "detections_green": g.to_dict("records"),
            "detections_red": r.to_dict("records"), "objects": objects.to_dict("records")}, {
        "read_seconds": loaded - tick, "segment_seconds": segmented - loaded,
        "matching_seconds": finished - segmented, "total_seconds": finished - tick}


def assert_parity(actual, expected):
    for key, value in expected.items():
        if isinstance(value, np.ndarray):
            np.testing.assert_array_equal(actual[key], value)
        else:
            assert actual[key] == value, key


def main():
    row, config = read_manifest(ROOT / "samples.csv")[0], default_config()
    red_config = deepcopy(config)
    red_config["segmentation"]["red"]["high"] = 5
    match_config = deepcopy(red_config)
    match_config["matching"]["max_distance_px"] = 5
    report = {"input": row, "shape": config["input"]["expected_shape"],
              "red_edit": "high 4 to 5", "matching_edit": "max_distance_px 6 to 5",
              "excludes": "Worker startup, GUI rendering, and debounce are excluded from compute timings.",
              "original": [], "cached": [], "payload": []}
    for repeat in range(3):
        expected, original = legacy_preview(row, config)
        report["original"].append(original)
        session, measurements = PreviewSession(), {}
        for name, settings in (("cold", config), ("unchanged", config),
                               ("red_edit", red_config), ("matching_edit", match_config)):
            preview = session.compute(row, settings)
            measurements[name] = deepcopy(session.last_timings)
            if name in ("cold", "unchanged"):
                assert_parity(preview, expected)
            elif repeat == 0:
                edited_expected, _ = legacy_preview(row, settings)
                assert_parity(preview, edited_expected)
        report["cached"].append(measurements)
        with TemporaryDirectory(prefix="cell_preview_benchmark_") as temp:
            payload_timings = {}
            for compressed in (True, False):
                folder = Path(temp) / str(compressed)
                tick = perf_counter()
                write_preview_data(preview, folder, compressed=compressed)
                written = perf_counter()
                reloaded = read_preview(folder)
                finished = perf_counter()
                assert_parity(reloaded, {key: preview[key] for key in expected})
                payload_timings["compressed" if compressed else "uncompressed"] = {
                    "write_seconds": written - tick, "read_seconds": finished - written,
                    "bytes": sum(path.stat().st_size for path in folder.iterdir())}
            report["payload"].append(payload_timings)
    report["median_seconds"] = {
        "original_compute": statistics.median(item["total_seconds"] for item in report["original"]),
        **{name: statistics.median(item[name]["total_seconds"] for item in report["cached"])
           for name in ("cold", "unchanged", "red_edit", "matching_edit")},
        **{f"payload_{kind}": statistics.median(item[kind]["write_seconds"] + item[kind]["read_seconds"]
                                                for item in report["payload"])
           for kind in ("compressed", "uncompressed")}}
    report["exact_parity"] = "All original/unchanged masks, contrasts and object records checked for all three repeats; red and matching edits checked once against the original pipeline."
    output = Path(__file__).with_name("benchmark_results.json")
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["median_seconds"], indent=2))
    print(report["exact_parity"])


if __name__ == "__main__":
    main()
