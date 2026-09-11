"""Record fresh validation of the original scientific pipeline, without edits."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(__file__).resolve().parent
OUTPUT = ROOT / "runs" / "gui_reference_validation"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def command(args, filename):
    started = time.monotonic()
    print("Running: " + " ".join(args), flush=True)
    result = subprocess.run([sys.executable, *args], cwd=ROOT, text=True,
                            encoding="utf-8", errors="replace", capture_output=True)
    log = "$ " + subprocess.list2cmdline([sys.executable, *args]) + "\n\n"
    log += result.stdout + result.stderr
    (ARTIFACTS / filename).write_text(log, encoding="utf-8")
    print(log, flush=True)
    item = {"arguments": args, "returncode": result.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3), "log": filename}
    if result.returncode:
        raise RuntimeError(f"Validation failed; see {filename}")
    return item, result.stdout + result.stderr


def main():
    from pipeline.io import environment
    import numpy as np
    import pandas as pd

    ARTIFACTS.mkdir(exist_ok=True)
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite {OUTPUT}")
    summary = {"status": "running", "run_directory": str(OUTPUT),
               "environment": environment(), "commands": [], "original_tests_passed": 0}
    summary_path = ARTIFACTS / "scientific_validation.json"
    write_json(summary_path, summary)
    try:
        for name in ("test_core.py", "test_reporting.py", "test_optional_ebfp.py"):
            result, log = command(["-m", "unittest", "discover", "-s", "tests", "-p", name, "-v"], name.replace(".py", ".log"))
            summary["commands"].append(result)
            summary["original_tests_passed"] += int(re.search(r"Ran (\d+) tests?", log).group(1))
        result, _ = command(["run_pipeline.py", "reference", "--output", str(OUTPUT)], "reference.log")
        summary["commands"].append(result)
        result, _ = command(["run_pipeline.py", "verify", "--run", str(OUTPUT)], "verify.log")
        summary["commands"].append(result)
        summary["reference_check"] = json.loads((OUTPUT / "reference_validation.json").read_text())
        summary["checksums_verified"] = True
        summary["mask_comparisons"] = []
        for sample in ("S5", "S6", "S7"):
            with np.load(OUTPUT / "labels" / f"{sample}.npz") as actual, np.load(ROOT / "reference" / "expected" / f"{sample}_masks.npz") as expected:
                for channel in ("live", "dead", "objects"):
                    differences = int(np.count_nonzero(actual[channel] != expected[channel]))
                    summary["mask_comparisons"].append({"sample": sample, "labels": channel,
                        "shape": list(actual[channel].shape), "different_pixels": differences})
        actual = pd.read_csv(OUTPUT / "objects.csv").sort_values(["sample", "object_id"]).reset_index(drop=True)
        expected = pd.read_csv(ROOT / "reference" / "expected" / "ebfp_objects.csv").sort_values(["sample", "object_id"]).reset_index(drop=True)
        summary["object_columns"] = {}
        for name in expected.columns:
            if pd.api.types.is_numeric_dtype(expected[name]) and not pd.api.types.is_bool_dtype(expected[name]):
                a, b = actual[name].to_numpy(float), expected[name].to_numpy(float)
                valid = np.isfinite(a) & np.isfinite(b)
                maximum = float(np.max(np.abs(a[valid] - b[valid]))) if valid.any() else 0.0
                exact = bool(np.array_equal(a, b, equal_nan=True))
                summary["object_columns"][name] = {"maximum_absolute_difference": maximum, "exact": exact}
            else:
                summary["object_columns"][name] = {"exact": bool(actual[name].equals(expected[name]))}
        package = json.loads((ROOT / "PACKAGE_SHA256.json").read_text())
        protected = [name for name in package if name == "run_pipeline.py" or name.startswith(("pipeline/", "configs/", "reference/", "tests/")) or name in ("requirements.txt", "requirements-lock.txt")]
        summary["original_scientific_files_sha256"] = {}
        for name in protected:
            digest = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            summary["original_scientific_files_sha256"][name] = {"sha256": digest, "matches_release": digest == package[name]}
        summary["original_scientific_files_unchanged"] = all(v["matches_release"] for v in summary["original_scientific_files_sha256"].values())
        archived = json.loads((ROOT / "reference" / "original_code" / "environment.json").read_text())
        summary["recorded_reference_environment"] = archived
        summary["environment_differences"] = {name: {"reference": value, "actual": summary["environment"].get(name)} for name, value in archived.items() if summary["environment"].get(name) != value}
        summary["image_summary"] = pd.read_csv(OUTPUT / "image_summary.csv").to_dict(orient="records")
        summary["aggregate_summary"] = json.loads((OUTPUT / "aggregate_summary.json").read_text())
        assert summary["original_tests_passed"] == 22
        assert summary["original_scientific_files_unchanged"]
        assert all(v["different_pixels"] == 0 for v in summary["mask_comparisons"])
        summary["status"] = "passed"
    except Exception as error:
        summary["status"] = "failed"
        summary["error"] = str(error)
        raise
    finally:
        write_json(summary_path, summary)
    print(f"Scientific validation passed. Evidence: {summary_path}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
