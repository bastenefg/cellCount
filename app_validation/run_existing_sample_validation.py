"""Repeat the explicitly assigned test1 dataset through the desktop worker."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(__file__).resolve().parent


def main():
    from desktop import services
    from pipeline.io import environment
    import numpy as np
    import pandas as pd

    summary = {"status": "running", "purpose": "GUI adapter fidelity for existing, explicitly assigned test1 dataset; not biological validation of acquisition settings", "environment": environment()}
    summary_path = ARTIFACTS / "existing_sample_validation.json"
    try:
        rows = services.read_manifest(ROOT / "samples.csv")
        assert len(rows) == 1 and rows[0]["replicate_id"] == "test1"
        original_config = (ROOT / "configs" / "reference_48h.json").read_bytes()
        config = services.default_config()
        assert config == json.loads(original_config)
        out, arguments, log = services.prepare_analysis(ROOT / "runs", "gui_existing_sample_validation", rows, config)
        program, args = services.process_command(arguments, log)
        summary.update({"run_directory": str(out), "source_manifest": str(ROOT / "samples.csv"), "assigned_rows": rows,
                        "worker_command": [program, *args], "worker_log": str(log)})
        print("Running desktop analysis worker", flush=True)
        started = time.monotonic()
        result = subprocess.run([program, *args], cwd=ROOT, capture_output=True, text=True)
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        summary["worker_exit_code"] = result.returncode
        text = log.read_text(encoding="utf-8") if log.exists() else result.stdout + result.stderr
        (ARTIFACTS / "existing_sample.log").write_text(text, encoding="utf-8")
        print(text, flush=True)
        if result.returncode:
            raise RuntimeError("Desktop analysis worker failed")
        verify_log = ARTIFACTS / "existing_sample_verify.log"
        program, args = services.process_command(["verify", "--run", str(out)], verify_log)
        result = subprocess.run([program, *args], cwd=ROOT, capture_output=True, text=True)
        summary["verify_exit_code"] = result.returncode
        if result.returncode:
            raise RuntimeError("Desktop verification worker failed")
        print(verify_log.read_text(encoding="utf-8"), flush=True)
        previous = ROOT / "runs" / "new_experiment"
        summary["compared_run"] = str(previous)
        summary["mask_comparisons"] = []
        for row in rows:
            sample = row["image_id"]
            with np.load(out / "labels" / f"{sample}.npz") as actual, np.load(previous / "labels" / f"{sample}.npz") as expected:
                for channel in ("live", "dead", "objects"):
                    np.testing.assert_array_equal(actual[channel], expected[channel])
                    summary["mask_comparisons"].append({"image_id": sample, "labels": channel,
                        "shape": list(actual[channel].shape), "different_pixels": int(np.count_nonzero(actual[channel] != expected[channel]))})
            for channel in ("green", "red"):
                name = f"{sample}_{channel}.csv"
                pd.testing.assert_frame_equal(pd.read_csv(out / "channel_detections" / name), pd.read_csv(previous / "channel_detections" / name), check_exact=False, rtol=1e-12, atol=1e-12)
        for filename in ("image_summary.csv", "replicate_summary.csv", "objects.csv", "threshold_sensitivity.csv", "threshold_sensitivity_by_replicate.csv", "threshold_sensitivity_aggregate.csv", "ebfp_q_sensitivity.csv"):
            pd.testing.assert_frame_equal(pd.read_csv(out / filename), pd.read_csv(previous / filename), check_exact=False, rtol=1e-12, atol=1e-12)
        summary["tables_match_previous_run"] = True
        summary["numeric_table_tolerance"] = {"atol": 1e-12, "rtol": 1e-12}
        objects = pd.read_csv(out / "objects.csv")
        assert objects.ebfp_status.eq("channel_not_provided").all()
        assert objects.ebfp_detected.isna().all()
        assert objects.ebfp_q_pooled.isna().all()
        summary["object_records_checked"] = len(objects)
        summary["missing_ebfp_remains_undefined"] = True
        results = services.read_results(out)
        summary["desktop_result_reader_passed"] = True
        summary["displayed_viability"] = services.metric(results["stats"], "viability_percent")
        summary["displayed_ebfp"] = services.metric(results["stats"], "ebfp_live_percent")
        summary["image_summary"] = results["image"]
        assert config == json.loads(original_config)
        assert (ROOT / "configs" / "reference_48h.json").read_bytes() == original_config
        summary["original_config_unchanged"] = True
        package = json.loads((ROOT / "PACKAGE_SHA256.json").read_text())
        summary["release_hashes"] = {name: {"sha256": hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), "matches_release": hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest} for name, digest in package.items()}
        summary["original_release_files_checked"] = len(package)
        summary["original_release_files_unchanged"] = all(item["matches_release"] for item in summary["release_hashes"].values())
        assert summary["original_release_files_unchanged"]
        summary["status"] = "passed"
    except Exception as error:
        summary.update({"status": "failed", "error": str(error)})
        raise
    finally:
        summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Existing dataset desktop validation passed: {summary_path}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
