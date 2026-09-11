"""Audit a built portable release without running it or changing its contents.

Scientific reference/GUI smoke checks are separate. Optional supplied reports
are recorded and must have passed; this audit does not manufacture those checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files():
    """Match the editable source/data mapping in CHO_Cell_Counter.spec."""
    mapped = {}
    for folder in ("reference", "configs", "tests", "validation"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                mapped[path] = path.relative_to(ROOT)
    for path in (ROOT / "pipeline").glob("*.py"):
        mapped[path] = path.relative_to(ROOT)
    for name in (
        "run_pipeline.py", "README.md", "APP_GUIDE.md", "requirements.txt",
        "requirements-lock.txt", "requirements-gui.txt", "requirements-build.txt",
        "samples_template.csv", "samples_live_dead_template.csv", "CHANGELOG.md",
        "PACKAGE_SHA256.json", ".python-version",
    ):
        mapped[ROOT / name] = Path(name)
    for name in ("run_app.py", "CHO_Cell_Counter.spec", "build_app.ps1", "Launch App.cmd"):
        mapped[ROOT / name] = Path("source") / name
    for folder in ("desktop", "packaging"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                mapped[path] = Path("source") / path.relative_to(ROOT)
    return mapped


def audit(args):
    app_dir = args.app_directory.resolve()
    archive_path = args.zip.resolve()
    internal = app_dir / "_internal"
    executable = app_dir / "Live-Dead Cell Counter.exe"
    issues = []
    if not executable.is_file():
        raise FileNotFoundError(executable)
    if not internal.is_dir():
        raise FileNotFoundError(internal)
    version = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "desktop/__init__.py").read_text()).group(1)
    hashes = {path: sha256(path) for path in app_dir.rglob("*") if path.is_file()}

    original_manifest = json.loads((ROOT / "PACKAGE_SHA256.json").read_text())
    original_mismatches = [relative for relative, expected in original_manifest.items()
                           if not (ROOT / relative).is_file() or sha256(ROOT / relative) != expected]
    if original_mismatches:
        issues.append("Original package files changed or are missing.")
    mapped = source_files()
    mismatches = []
    for source, relative in mapped.items():
        target = internal / relative
        if not target.is_file() or hashes.get(target) != sha256(source):
            mismatches.append(str(relative).replace("\\", "/"))
    for source, target in [(ROOT / "APP_GUIDE.md", app_dir / "APP_GUIDE.md")]:
        if not target.is_file() or hashes.get(target) != sha256(source):
            mismatches.append(target.name)
    if mismatches:
        issues.append("Bundled source/data files do not match the current source.")

    unwanted = [str(path.relative_to(app_dir)) for path in hashes
                if path.name.lower() in {"icuuc.dll", "icudt78.dll"}]
    if unwanted:
        issues.append("Incompatible build-host ICU libraries are bundled.")
    private_roots = [name for name in ("images", "runs", "app_validation") if (internal / name).exists()]
    if private_roots:
        issues.append("User image, run or local validation folders were bundled.")
    missing_notices = [name for name in ("THIRD_PARTY_NOTICES.md",)
                       if not (app_dir / name).is_file()]
    if not (internal / "third_party_notices/PYTHON_LICENSE.txt").is_file():
        missing_notices.append("_internal/third_party_notices/PYTHON_LICENSE.txt")
    if missing_notices:
        issues.append("Required packaged notices are missing.")

    zip_mismatches = []
    with zipfile.ZipFile(archive_path) as archive:
        records = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in records]
        if len(names) != len(set(names)):
            zip_mismatches.append("Duplicate ZIP entries")
        expected = {app_dir.name + "/" + path.relative_to(app_dir).as_posix(): digest for path, digest in hashes.items()}
        if set(names) != set(expected):
            zip_mismatches.extend(["Missing: " + name for name in sorted(set(expected) - set(names))])
            zip_mismatches.extend(["Unexpected: " + name for name in sorted(set(names) - set(expected))])
        for info in records:
            digest = hashlib.sha256()
            with archive.open(info) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)  # Reading the full stream also validates ZIP CRC.
            if info.filename in expected and digest.hexdigest() != expected[info.filename]:
                zip_mismatches.append("Contents differ: " + info.filename)
    if zip_mismatches:
        issues.append("ZIP entries differ from the audited app directory.")

    supplied_reports = []
    for report_path in args.smoke_report:
        data = json.loads(report_path.read_text())
        supplied_reports.append({"path": str(report_path.resolve()), "sha256": sha256(report_path), "report": data})
        if data.get("status") != "passed":
            issues.append("Supplied smoke report did not pass: " + str(report_path))
    if args.reference_run:
        report_path = args.reference_run / "reference_validation.json"
        data = json.loads(report_path.read_text())
        supplied_reports.append({"path": str(report_path.resolve()), "sha256": sha256(report_path), "report": data})
        if data.get("status") != "passed":
            issues.append("Supplied reference validation did not pass.")

    return {
        "status": "failed" if issues else "passed",
        "audit_scope": "Bundle source/data identity, original source preservation, dependency exclusions and exact ZIP contents. Runtime checks appear only when supplied separately.",
        "app_version": version,
        "audit_python": platform.python_version(),
        "app_directory": str(app_dir),
        "app_files": len(hashes),
        "app_bytes": sum(path.stat().st_size for path in hashes),
        "executable_sha256": hashes[executable],
        "original_package_files_checked": len(original_manifest),
        "original_package_mismatches": original_mismatches,
        "bundled_sources_checked": len(mapped) + 1,
        "bundled_source_mismatches": mismatches,
        "incompatible_icu_files": unwanted,
        "private_data_folders": private_roots,
        "missing_notices": missing_notices,
        "zip": str(archive_path),
        "zip_bytes": archive_path.stat().st_size,
        "zip_sha256": sha256(archive_path),
        "zip_crc_and_content_check": "failed" if zip_mismatches else "passed",
        "zip_mismatches": zip_mismatches,
        "supplied_runtime_reports": supplied_reports,
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-directory", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke-report", type=Path, action="append", default=[])
    parser.add_argument("--reference-run", type=Path)
    args = parser.parse_args()
    try:
        report = audit(args)
    except Exception as error:
        report = {"status": "failed", "error": str(error), "type": type(error).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
