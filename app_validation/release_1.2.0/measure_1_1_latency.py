"""Measure the old release's full-image request path; do not modify that release."""
from pathlib import Path
import json
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
APP = ROOT / "dist/1.1.0/CHO Cell Counter/CHO Cell Counter.exe"

def main():
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    config = json.loads((ROOT / "configs/reference_48h.json").read_text())
    timings = []
    for index, high in enumerate((4, 5, 6)):
        config["segmentation"]["red"]["high"] = high
        request = OUT / f"baseline_1.1_request_{index}.json"
        output = OUT / f"baseline_1.1_preview_{index}"
        request.write_text(json.dumps({"row": {
            "image_id": "test1", "replicate_id": "alexandra_test1",
            "green": str(ROOT / "images/test1_live.tif"),
            "red": str(ROOT / "images/test1_dead.tif"),
        }, "config": config}))
        started = time.perf_counter()
        completed = subprocess.run([str(APP), "--segmentation-preview", str(request),
                                    "--preview-output", str(output)], cwd=ROOT,
                                   startupinfo=info, timeout=120)
        elapsed = time.perf_counter() - started
        assert completed.returncode == 0, completed.returncode
        metadata = json.loads((output / "metadata.json").read_text())
        timings.append({"red_high": high, "seconds": elapsed, "counts": metadata["counts"]})
        print(json.dumps(timings[-1]), flush=True)
    report = {"status": "passed", "version": "1.1.0",
              "metric": "Full-image one-shot frozen preview worker launch to exit, excluding GUI debounce and drawing",
              "image": "test1", "timings": timings}
    (OUT / "baseline_1.1_latency.json").write_text(json.dumps(report, indent=2) + "\n")

if __name__ == "__main__":
    main()
