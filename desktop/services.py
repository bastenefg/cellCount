"""File and process adapters; no image processing or scientific calculations."""
from datetime import datetime
from copy import deepcopy
from pathlib import Path
import csv
import hashlib
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


def input_compatible(first, second):
    """A batch has one spatial calibration and one scalar TIFF format."""
    return (list(first["expected_shape"]) == list(second["expected_shape"])
            and first["dtype"] == second["dtype"]
            and len(first["pixel_size_um"]) == len(second["pixel_size_um"]) == 2
            and all(math.isclose(float(a), float(b), rel_tol=1e-8, abs_tol=1e-12)
                    for a, b in zip(first["pixel_size_um"], second["pixel_size_um"])))


def discover_leica_imports(rows):
    """Recover import provenance from exported TIFFs, including after CSV reload.

    The stored Leica source identity is retained as described by the importer;
    only the exported TIFF bytes are checked against SHA256 here.
    """
    sidecars, verified, records = {}, {}, []
    for row in rows:
        row_records = {}
        for channel in ("green", "red", "ebfp"):
            if not row.get(channel):
                continue
            path = Path(row[channel]).resolve()
            sidecar = path.parent / "import_provenance.json"
            if not sidecar.is_file():
                continue
            if sidecar not in sidecars:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("schema_version") != 1:
                    raise ValueError(f"Unsupported Leica import record: {sidecar}")
                if not isinstance(data.get("exports"), dict) or not isinstance(data.get("result"), dict):
                    raise ValueError(f"Invalid Leica import record: {sidecar}")
                sidecars[sidecar] = data
            data = sidecars[sidecar]
            matched = None
            for original_role, exported in data["exports"].items():
                if original_role not in ("green", "red", "ebfp") or not isinstance(exported, dict):
                    raise ValueError(f"Invalid Leica export record: {sidecar}")
                if exported.get("filename") != original_role + ".tif":
                    raise ValueError(f"Invalid Leica export filename: {sidecar}")
                if path == (sidecar.parent / exported["filename"]).resolve():
                    matched = exported
                    break
            if matched is None:
                raise ValueError(f"This TIFF is not recorded in its Leica import: {path}")
            if path not in verified:
                if path.stat().st_size != matched.get("bytes"):
                    raise ValueError(f"Imported TIFF changed; import the Leica image again: {path}")
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != matched.get("sha256"):
                    raise ValueError(f"Imported TIFF changed; import the Leica image again: {path}")
                verified[path] = digest
            if sidecar not in row_records:
                record = deepcopy(data)
                record["provenance_path"] = str(sidecar)
                record["analysis_field_id"] = row["image_id"]
                record["analysis_replicate_id"] = row["replicate_id"]
                record["analysis_channel_paths"] = {}
                row_records[sidecar] = record
            row_records[sidecar]["analysis_channel_paths"][channel] = str(path)
        records.extend(row_records.values())
    return records


def config_with_imports(rows, config):
    """Restore Leica calibration and keep a verified provenance snapshot."""
    settings = deepcopy(config)
    settings.pop("leica_imports", None)
    imports = discover_leica_imports(rows)
    selected_paths = {str(Path(row[channel]).resolve()) for row in rows
                      for channel in ("green", "red", "ebfp") if row.get(channel)}
    recovered_paths = {path for item in imports for path in item["analysis_channel_paths"].values()}
    for previous in config.get("leica_imports", []):
        expected_paths = set(previous.get("analysis_channel_paths", {}).values()) & selected_paths
        if expected_paths - recovered_paths:
            raise ValueError("The Leica import record is missing for these TIFFs. Restore import_provenance.json beside the images, or import the Leica acquisition again.")
    if not imports:
        return settings
    imported_input = imports[0]["result"]["input"]
    if any(not input_compatible(imported_input, item["result"]["input"]) for item in imports[1:]):
        raise ValueError("These Leica fields have different image sizes, bit depths or pixel calibrations. Analyze them in separate batches.")
    imported_fields = {item["analysis_field_id"] for item in imports}
    all_imported = len(imported_fields) == len(rows)
    if not all_imported and not input_compatible(settings["input"], imported_input):
        raise ValueError("The Leica calibration or image format differs from the other TIFF fields. Analyze them in a separate batch.")
    if all_imported:
        settings["input"].update(deepcopy(imported_input))
        for item in imports:
            display = item["result"].get("display", {})
            folder = Path(item["provenance_path"]).parent
            for channel, filename in item["analysis_channel_paths"].items():
                for original_role, exported in item["exports"].items():
                    if Path(filename) == (folder / exported["filename"]).resolve() and original_role in display:
                        settings["display"][channel] = deepcopy(display[original_role])
    settings["leica_imports"] = imports
    return settings


def prepare_analysis(base, name, rows, config, extended=False):
    validate_rows(rows)
    from pipeline.io import validate_config
    config = config_with_imports(rows, config)
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
    imports = []
    settings_path = path / "effective_config.json"
    if settings_path.is_file():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        imports = settings.get("leica_imports", [])
        if not isinstance(imports, list) or any(not isinstance(item, dict) for item in imports):
            raise ValueError("Invalid saved Leica import records.")
    return {"path": path, "meta": meta, "stats": stats, "reference": reference, "leica_imports": imports, **tables}


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
