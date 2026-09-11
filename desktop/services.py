"""File and process adapters; no image processing or scientific calculations."""
from datetime import datetime
from pathlib import Path
import csv
import json
import math
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
COLUMNS = ["image_id", "replicate_id", "green", "red", "ebfp"]
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name):
    return bool(name and ID_PATTERN.fullmatch(name) and not name.endswith(".")
                and name.split(".")[0].upper() not in RESERVED_NAMES)


def default_config():
    return json.loads((ROOT / "configs" / "reference_48h.json").read_text(encoding="utf-8"))


def validate_rows(rows, check_files=True):
    if not rows:
        raise ValueError("Add at least one field, with a live and a dead image.")
    ids, used = set(), set()
    for row in rows:
        for key in ("image_id", "replicate_id"):
            if not ID_PATTERN.fullmatch(str(row.get(key, ""))):
                raise ValueError(f"{key} is required. Use letters, numbers, underscores, dots or hyphens; start with a letter or number.")
        if not safe_filename(row["image_id"]):
            raise ValueError("Choose a field ID that is valid as a Windows filename; reserved names and trailing dots are unsupported.")
        if row["image_id"].casefold() in ids:
            raise ValueError(f"Duplicate field ID: {row['image_id']}")
        ids.add(row["image_id"].casefold())
        for channel in ("green", "red", "ebfp"):
            value = row.get(channel) or ""
            if not value:
                if channel == "ebfp":
                    continue
                raise ValueError(f"{row['image_id']}: a {channel} image is required.")
            path = Path(value).resolve()
            if path.suffix.lower() not in (".tif", ".tiff"):
                raise ValueError(f"Select an original TIFF image: {path}")
            if check_files and not path.is_file():
                raise ValueError(f"Image not found: {path}")
            if path in used:
                raise ValueError(f"An image is assigned more than once: {path.name}")
            used.add(path)


def read_manifest(path):
    path = Path(path).resolve()
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not set(COLUMNS[:4]).issubset(reader.fieldnames or []):
            raise ValueError("CSV requires image_id, replicate_id, green and red columns. EBFP is optional.")
        rows = []
        for record in reader:
            row = {key: (record.get(key) or "").strip() for key in COLUMNS}
            for channel in COLUMNS[2:]:
                if row[channel]:
                    row[channel] = str((path.parent / row[channel]).resolve())
            rows.append(row)
    validate_rows(rows)
    return rows


def write_manifest(path, rows):
    validate_rows(rows)
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in COLUMNS} for row in rows)


def new_run_name(prefix="analysis"):
    return f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}"


def output_path(base, name):
    if not name or not ID_PATTERN.fullmatch(name):
        raise ValueError("Use a run name starting with a letter or number, with only letters, numbers, underscores, dots or hyphens.")
    if not safe_filename(name):
        raise ValueError("Choose a run name that is valid as a Windows folder name.")
    if not str(base).strip():
        raise ValueError("Choose a folder for your results.")
    path = Path(base).expanduser().resolve() / name
    if path.exists():
        raise FileExistsError("This run folder already exists. Enter a new run name; existing results are preserved.")
    return path


def prepare_analysis(base, name, rows, config, extended=False):
    validate_rows(rows)
    from pipeline.io import validate_config
    validate_config(config)
    out = output_path(base, name)
    # Stable input snapshots live outside the run, so the pipeline can create its
    # own new output directory and its output checksums remain authoritative.
    inputs = out.parent / ".cho_inputs" / f"{name}_{uuid.uuid4().hex[:8]}"
    inputs.mkdir(parents=True)
    manifest = inputs / "samples.csv"
    write_manifest(manifest, rows)
    settings = inputs / "config.json"
    settings.write_text(json.dumps(config, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    args = ["analyze", "--manifest", str(manifest), "--config", str(settings), "--output", str(out)]
    if extended:
        args.append("--extended-qc")
    return out, args, out.parent / f"{name}.log"


def process_command(arguments, log_path):
    prefix = [] if getattr(sys, "frozen", False) else [str(ROOT / "run_app.py")]
    return sys.executable, [*prefix, "--pipeline", *map(str, arguments), "--log", str(log_path)]


def read_results(path):
    path = Path(path).resolve()
    meta = json.loads((path / "run_manifest.json").read_text(encoding="utf-8"))
    if meta.get("status") != "completed":
        raise ValueError("This run is incomplete. Review its log and start a new run.")
    stats = json.loads((path / "aggregate_summary.json").read_text(encoding="utf-8"))
    if not isinstance(stats, dict):
        raise ValueError("Invalid aggregate summary: expected named measurements.")
    for key in ("viability_percent", "ebfp_live_percent"):
        record = stats.get(key)
        if not isinstance(record, dict):
            raise ValueError(f"Missing summary metric: {key}")
        for field in ("mean", "sample_sd"):
            value = record.get(field)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError(f"Invalid summary value: {key}.{field}")
    tables = {}
    for key in ("image", "replicate"):
        with (path / f"{key}_summary.csv").open(newline="", encoding="utf-8-sig") as stream:
            tables[key] = list(csv.DictReader(stream))
        if not tables[key]:
            raise ValueError(f"The {key} summary is empty.")
        for row in tables[key]:
            try:
                counts = [int(row[column]) for column in ("live_only", "dead_only", "double_positive", "total")]
                if any(n < 0 for n in counts) or sum(counts[:3]) != counts[3]:
                    raise ValueError("inconsistent counts")
                if not row["replicate_id"] or (key == "image" and not row["image_id"]):
                    raise ValueError("missing identifier")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid {key} summary: missing identifiers or inconsistent counts.") from exc
    reference = None
    reference_path = path / "reference_validation.json"
    if meta.get("reference_run") and reference_path.is_file():
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        if not isinstance(reference, dict):
            raise ValueError("Invalid reference validation record.")
    warnings = meta.get("warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise ValueError("Invalid run notes.")
    return {"path": path, "meta": meta, "stats": stats, "reference": reference, **tables}


def number(value, places=1):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "Not measured"
    return f"{value:.{places}f}" if math.isfinite(value) else "Not measured"


def metric(stats, key):
    record = stats.get(key, {})
    mean = number(record.get("mean"))
    if mean == "Not measured":
        return "Not measured", "No defined measurements"
    sd = number(record.get("sample_sd"))
    count = record.get("n_replicates_with_defined_metric", 0)
    detail = f"SD {sd} · {count} replicate groups" if sd != "Not measured" else "1 replicate group · SD undefined"
    return mean + "%", detail
