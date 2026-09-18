"""Run Leica volumes with the settings accepted in the ordinary projection preview.

Only one field is resident at a time. The desktop starts this module in a worker
process; projected TIFFs, original acquisitions and completed runs are immutable.
"""
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from .services import (config_with_imports, output_path, safe_filename,
                       validate_rows, write_manifest)


COUNT_KEYS = ("green_only", "red_only", "dual_positive_candidate", "unresolved",
              "total_groups", "green_objects", "red_objects", "candidate_count_min",
              "candidate_count_max", "boundary_groups")
WARNINGS = [
    "3D uses the accepted projection settings on each optical section; projection preview counts are 2D and can differ.",
    "Dual-positive candidates and unresolved associations require review; no definitive viability is inferred.",
    "Optional EBFP measurements and extended EBFP diagnostics are available in 2D analysis only.",
]


def _json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _records(rows, config):
    """Never silently skip TIFF-only fields or mix acquisitions for one field."""
    result = []
    for row in rows:
        matches = [record for record in config.get("leica_imports", [])
                   if record.get("analysis_field_id") == row["image_id"]]
        if len(matches) != 1:
            raise ValueError(f"{row['image_id']}: 3D analysis needs LIVE and DEAD from one Leica Z stack. "
                             "Import a maximum projection with at least two Z slices, or choose 2D analysis.")
        record = matches[0]
        paths = record.get("analysis_channel_paths", {})
        if any(not paths.get(role) or Path(paths[role]).resolve() != Path(row[role]).resolve()
               for role in ("green", "red")):
            raise ValueError(f"{row['image_id']}: both channels must belong to the same Leica stack.")
        selection = record.get("selection", {})
        if selection.get("mode") != "max" or selection.get("z_stop", 0) - selection.get("z_start", 0) < 2:
            raise ValueError(f"{row['image_id']}: import a maximum projection with at least two Z slices for 3D analysis.")
        result.append(deepcopy(record))
    return result


def prepare_analysis_3d(base, name, rows, config, cache_dir=None):
    from pipeline.io import validate_config
    validate_rows(rows)
    config = config_with_imports(rows, config)
    validate_config(config)
    _records(rows, config)
    out = output_path(base, name)
    _protect_parent(out)
    inputs = out.parent / ".cho_inputs" / f"{name}_{uuid.uuid4().hex[:8]}"
    inputs.mkdir(parents=True, exist_ok=False)
    if cache_dir is None:
        cache_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "LiveDeadCellCounter" / "cache" / "stack_review"
    request = inputs / "analysis_3d.json"
    _json(request, {"schema_version": 1, "rows": deepcopy(rows), "config": config,
                    "output_dir": str(out), "cache_dir": str(Path(cache_dir).resolve())})
    return out, ["analyze-3d", str(request)], out.parent / f"{name}.log"


def _protect_parent(out):
    for parent in out.parents:
        if any((parent / marker).is_file() for marker in ("run_manifest.json", "volume_run.json", "result.json", "stack_manifest.json", "import_provenance.json")):
            raise ValueError("Choose a results folder outside existing analyses and source-image caches.")


def _write_table(path, rows, columns):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _check(cancelled):
    from .volume_analysis import VolumeAnalysisCancelled
    if cancelled and cancelled():
        raise VolumeAnalysisCancelled("3D analysis cancelled.")


def analyze_batch(request, progress=None, cancelled=None):
    from pipeline.io import validate_config
    from .stack_source import prepare_stack
    from .leica import LeicaImportCancelled
    from .volume_analysis import analyze_volume, settings_from_config, VolumeAnalysisCancelled
    rows, config = deepcopy(request["rows"]), deepcopy(request["config"])
    validate_rows(rows)
    validate_config(config)
    records = _records(rows, config)
    out = Path(request["output_dir"]).expanduser().resolve()
    _protect_parent(out)
    _check(cancelled)
    out.mkdir(parents=True, exist_ok=False)
    report = progress or (lambda message: None)
    metadata = {"schema_version": 1, "analysis_mode": "3d", "status": "running",
                "started_utc": datetime.now(timezone.utc).isoformat(), "fields": [], "warnings": WARNINGS}
    _json(out / "volume_run.json", metadata)
    try:
        _json(out / "effective_config.json", config)
        write_manifest(out / "resolved_samples.csv", rows)
        images = []
        for index, (row, record) in enumerate(zip(rows, records), 1):
            _check(cancelled)
            name = row["image_id"]
            report(f"Analyzing 3D field {index}/{len(rows)}: {name}")
            info = prepare_stack(record, request["cache_dir"], cancelled=cancelled,
                                 progress=lambda done, total: report(f"Computing {name}: loading Z stack {done}/{total}"))
            settings = settings_from_config(info, config)
            relative = f"fields/{name}"
            result = analyze_volume(info, settings, out / relative, progress=report, cancelled=cancelled)
            summary = json.loads(Path(result["summary"]).read_text(encoding="utf-8"))
            images.append({"image_id": name, "replicate_id": row["replicate_id"],
                           **{key: summary["counts"][key] for key in COUNT_KEYS}})
            metadata["fields"].append({"image_id": name, "replicate_id": row["replicate_id"], "relative_path": relative})
            _json(out / "volume_run.json", metadata)
        _check(cancelled)
        grouped = {}
        for row in images:
            group = grouped.setdefault(row["replicate_id"], {"replicate_id": row["replicate_id"],
                                       "n_fields": 0, **dict.fromkeys(COUNT_KEYS, 0)})
            group["n_fields"] += 1
            for key in COUNT_KEYS:
                group[key] += row[key]
        _write_table(out / "image_summary.csv", images, ("image_id", "replicate_id", *COUNT_KEYS))
        _write_table(out / "replicate_summary.csv", list(grouped.values()), ("replicate_id", "n_fields", *COUNT_KEYS))
        aggregate = {"analysis_mode": "3d", "n_fields": len(images), "n_replicates": len(grouped),
                     "counts": {key: sum(row[key] for row in images) for key in COUNT_KEYS}, "warnings": WARNINGS}
        _json(out / "summary.json", aggregate)
        metadata.update(status="completed", completed_utc=datetime.now(timezone.utc).isoformat())
        _json(out / "volume_run.json", metadata)
        files = ["volume_run.json", "effective_config.json", "resolved_samples.csv", "image_summary.csv", "replicate_summary.csv", "summary.json"]
        files.extend(field["relative_path"] + "/output_checksums.json" for field in metadata["fields"])
        _json(out / "checksums_3d.json", {name: _hash(out / name) for name in files})
        report(f"Completed 3D analysis: {len(images)} fields; saved exact projection settings.")
        return out
    except BaseException as exc:
        is_cancelled = isinstance(exc, (VolumeAnalysisCancelled, LeicaImportCancelled))
        metadata.update(status="cancelled" if is_cancelled else "failed", error=str(exc))
        _json(out / "volume_run.json", metadata)
        if is_cancelled and not isinstance(exc, VolumeAnalysisCancelled):
            raise VolumeAnalysisCancelled(str(exc)) from exc
        raise


def read_analysis_3d(path, verify=False):
    from .volume_analysis import read_volume_result
    folder = Path(path).expanduser().resolve()
    meta = json.loads((folder / "volume_run.json").read_text(encoding="utf-8"))
    if meta.get("schema_version") != 1 or meta.get("analysis_mode") != "3d" or meta.get("status") != "completed":
        raise ValueError("This 3D run is incomplete. Review its log and start a new run.")
    fields = []
    identifiers = set()
    for item in meta.get("fields", []):
        name = item.get("image_id")
        if not isinstance(name, str) or not safe_filename(name) or name.casefold() in identifiers:
            raise ValueError("Invalid 3D field identifier.")
        identifiers.add(name.casefold())
        if item.get("relative_path") != f"fields/{name}":
            raise ValueError("Invalid saved 3D field path.")
        field = folder / item["relative_path"]
        if not field.resolve().is_relative_to(folder):
            raise ValueError("A saved field points outside this 3D run.")
        result = read_volume_result(field, verify=verify)
        fields.append({**item, "path": field, "result": result})
    if not fields:
        raise ValueError("The 3D run contains no completed fields.")
    expected = {"volume_run.json", "effective_config.json", "resolved_samples.csv", "image_summary.csv", "replicate_summary.csv", "summary.json"}
    expected.update(item["relative_path"] + "/output_checksums.json" for item in meta["fields"])
    checks = json.loads((folder / "checksums_3d.json").read_text(encoding="utf-8"))
    if not isinstance(checks, dict) or set(checks) != expected:
        raise ValueError("The 3D run verification record is incomplete.")
    for name, digest in checks.items():
        if not (folder / name).is_file():
            raise ValueError(f"Missing 3D result: {name}")
        # These are small metadata files; always verify them. Full-volume label
        # hashes remain an explicit check so ordinary result opening stays fast.
        if _hash(folder / name) != digest:
            raise ValueError(f"Saved 3D file changed: {name}")
    tables = {}
    for key in ("image", "replicate"):
        with (folder / f"{key}_summary.csv").open(encoding="utf-8", newline="") as stream:
            tables[key] = list(csv.DictReader(stream))
        if not tables[key]:
            raise ValueError("Empty saved 3D summary table.")
    return {"path": folder, "meta": meta, "fields": fields, **tables,
            "config": json.loads((folder / "effective_config.json").read_text(encoding="utf-8")),
            "summary": json.loads((folder / "summary.json").read_text(encoding="utf-8"))}


def finish_cancelled_batch(path):
    """After the worker exits, remove only its unpublished field scratch maps."""
    folder = Path(path).expanduser().resolve()
    manifest = folder / "volume_run.json"
    if not manifest.is_file():
        return
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    if metadata.get("status") == "completed" or (folder / "checksums_3d.json").exists():
        return
    if metadata.get("analysis_mode") != "3d":
        return
    samples = folder / "resolved_samples.csv"
    fields = folder / "fields"
    if samples.is_file() and fields.is_dir() and not fields.is_symlink():
        with samples.open(encoding="utf-8", newline="") as stream:
            names = [row["image_id"] for row in csv.DictReader(stream)]
        prefixes = tuple(".volume-pending-" + name + "-" for name in names if safe_filename(name))
        # Resolved scratch targets must be direct children of this new run's
        # fields directory. Completed field folders and source caches are kept.
        if fields.resolve().parent == folder:
            for candidate in fields.iterdir():
                if (prefixes and candidate.name.startswith(prefixes) and candidate.is_dir()
                        and not candidate.is_symlink() and candidate.resolve().parent == fields.resolve()):
                    shutil.rmtree(candidate)
    metadata.update(status="cancelled", error="Stopped by user before completion.")
    _json(manifest, metadata)


def run_request(path):
    from .volume_analysis import VolumeAnalysisCancelled
    path = Path(path)
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
        cancel = path.with_name(path.stem + "_cancel")
        analyze_batch(request, progress=lambda message: print(message, flush=True), cancelled=cancel.exists)
        return 0
    except VolumeAnalysisCancelled as exc:
        print(str(exc), flush=True)
        return 130
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
