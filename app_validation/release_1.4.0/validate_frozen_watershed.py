"""Exercise the compiled watershed inside a portable executable, using synthetic cells."""
import argparse
import json
from pathlib import Path
import subprocess
import time

import numpy as np
from scipy import ndimage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    inputs = work / "inputs"
    inputs.mkdir()
    shape = (25, 40, 40)
    z, y, x = np.indices(shape)
    mask = (((z-12)**2 + (y-20)**2 + (x-15)**2 <= 25)
            | ((z-12)**2 + (y-20)**2 + (x-24)**2 <= 25))
    assert ndimage.label(mask)[1] == 1, "Fixture must be one touching component"
    arrays = {"green": mask.astype(np.uint8) * 200, "red": np.zeros(shape, np.uint8)}
    for role, array in arrays.items():
        np.save(inputs / f"{role}.npy", array, allow_pickle=False)
    settings = {"min_seed_distance_um": 6.0, "match_distance_um": 0.0, "exclude_border": False,
        **{role: {"low": 100.0, "high": 150.0, "sigma_um": 0.0, "min_volume_um3": 10.0} for role in arrays}}
    job = {"stack_info": {"paths": {role: str(inputs / f"{role}.npy") for role in arrays},
            "shape_zyx": list(shape), "spacing_um": [1., 1., 1.], "field_id": "Synthetic touching spheres"},
           "settings": settings, "output_dir": str(work / "result")}
    job_path = work / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    started = time.perf_counter()
    process = subprocess.run([str(args.executable.resolve()), "--volume-analysis", str(job_path)],
                             timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    status = json.loads((work / "job_status.json").read_text())
    assert process.returncode == 0 and status["status"] == "completed", status
    counts = json.loads((work / "result/summary.json").read_text())["counts"]
    assert counts["green_objects"] == counts["green_only"] == 2, counts
    assert counts["red_objects"] == 0, counts
    labels = np.load(work / "result/labels_green.npy")
    assert set(np.unique(labels)) == {0, 1, 2}
    assert np.all(labels[~mask] == 0)
    assert np.all(labels[mask] > 0)
    provenance = json.loads((work / "result/provenance.json").read_text())
    report = {"status": "passed", "fixture": "Two touching synthetic spheres, one connected input region",
              "checks": ["Portable worker loads compiled scikit-image watershed", "Touching spheres split into two objects",
                         "Labels cover all foreground and no background", "Saved settings and provenance available"],
              "elapsed_seconds": time.perf_counter() - started,
              "counts": counts, "algorithm": provenance["algorithm"]}
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
