"""Validate an executable with an existing local Leica request; publish only metrics.

Input paths, cell counts and microscope images stay in --work, outside the report.
Run this helper from a Python environment containing the app dependencies.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    job = deepcopy(json.loads(args.request.read_text(encoding="utf-8")))
    job["output_dir"] = str(work / "analysis")
    request = work / "request.json"
    request.write_text(json.dumps(job, indent=2), encoding="utf-8")
    source_stats = {}
    for record in job["config"]["leica_imports"]:
        path = Path(record["source"]["path"])
        source_stats[path] = (path.stat().st_size, path.stat().st_mtime_ns)
    command = [str(args.executable.resolve())] if args.executable else [sys.executable, str(ROOT / "run_app.py")]
    started = time.perf_counter()
    completed = subprocess.run([*command, "--stack-analysis", str(request), "--log", str(work / "analysis.log")],
                               timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elapsed = time.perf_counter() - started
    assert completed.returncode == 0, "Analysis worker failed; see private work log."
    from desktop.analysis_3d import read_analysis_3d
    result = read_analysis_3d(job["output_dir"], verify=True)
    assert result["config"] == job["config"], "The accepted settings changed."
    assert len(result["fields"]) == len(job["rows"]), "A field was skipped."
    shapes = []
    for field in result["fields"]:
        settings = json.loads(Path(field["result"]["settings"]).read_text(encoding="utf-8"))
        assert settings == {"mode": "projection_config", "config": job["config"]}
        summary = json.loads(Path(field["result"]["summary"]).read_text(encoding="utf-8"))
        assert summary["field_id"] == field["image_id"]
        shapes.append(summary["shape_zyx"])
        assert Path(field["result"]["figure"]).read_bytes().startswith(b"\x89PNG")
        if args.baseline:
            for filename in ("labels_green.npy", "labels_red.npy", "objects.csv"):
                before = args.baseline / "fields" / field["image_id"] / filename
                after = field["path"] / filename
                with before.open("rb") as a, after.open("rb") as b:
                    assert hashlib.file_digest(a, "sha256").digest() == hashlib.file_digest(b, "sha256").digest(), filename
    assert all((p.stat().st_size, p.stat().st_mtime_ns) == stat for p, stat in source_stats.items())
    verification = subprocess.run([*command, "--verify-stack-analysis", job["output_dir"],
                                   "--log", str(work / "verify.log")], timeout=90,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert verification.returncode == 0, "Packaged verification failed."
    report = {"status": "passed", "mode": "portable executable" if args.executable else "source subprocess",
              "scope": "Engineering workflow check; no biological accuracy assessment.",
              "shape_zyx_by_field": shapes, "elapsed_seconds_including_startup": elapsed,
              "cache_context": "Warm filesystem caches; existing verified stack cache reused when available.",
              "checks": ["Normal 3D Run CLI processes all selected fields", "Exact shared projection settings saved",
                         "Field identity recorded in each summary", "PNG generated from completed masks",
                         "All output integrity checks passed", "Original acquisition size and timestamps preserved",
                         "Verification CLI passed"]}
    if args.baseline:
        report["checks"].append("Both label volumes and object tables byte-identical to source baseline")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
