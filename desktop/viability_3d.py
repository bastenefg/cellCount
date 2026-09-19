"""Interpret saved 3D objects without rerunning segmentation.

A matched LIVE/DEAD pair represents one membrane-compromised cell. Unpaired
LIVE objects represent viable cells and every DEAD object represents a
nonviable cell. These are assay assumptions, not proof of cell identity or an
apoptosis diagnosis. Unreviewed associations produce a scenario range.
"""
from collections.abc import Mapping
from datetime import datetime, timezone
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


ASSUMPTIONS = (
    "Each segmented object within a channel represents one cell. A reviewed "
    "LIVE/DEAD pair represents one nonviable, EthD-1-positive cell. Remaining "
    "LIVE objects are viable; each DEAD object is nonviable. The range covers "
    "possible one-to-one pairings in pending groups, not a confidence interval. "
    "Signal identity and segmentation still require review; this does not "
    "establish apoptosis."
)
DECISIONS = frozenset(("same_cell", "separate", "pairs", "uncertain", "unreviewed"))
_SUM_KEYS = (
    "green_objects", "red_objects", "paired_min", "paired_max", "mixed_groups",
    "reviewed_groups", "pending_groups", "dual_candidates", "unresolved_groups",
    "uncertain_groups", "unreviewed_groups", "total_groups", "green_only",
    "red_only",
)
_REVISION = re.compile(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z_[0-9a-f]{12}\Z")


def _identifier(value, kind):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"Invalid {kind} identifier.")
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ValueError(f"Invalid {kind} identifier: {text!r}.")
    return text


def _members(value, role):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        items = value.split(";")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise ValueError(f"Invalid {role} object membership.")
    result = [_identifier(item, role) for item in items]
    if len(set(result)) != len(result):
        raise ValueError(f"Duplicate {role} object identifier.")
    return result


def _decision(value, green, red):
    if not isinstance(value, Mapping):
        raise ValueError("Each review decision must be an object.")
    name = value.get("decision", "unreviewed")
    if not isinstance(name, str) or name not in DECISIONS:
        raise ValueError(f"Unknown review decision: {name!r}.")
    note = value.get("note", "")
    if not isinstance(note, str):
        raise ValueError("A review note must be text.")
    pairs = value.get("pairs", [])
    if not isinstance(pairs, (list, tuple)):
        raise ValueError("Review pairs must be a list of LIVE/DEAD identifiers.")
    if name != "pairs" and pairs:
        raise ValueError("Explicit pairs are only valid with the pairs decision.")
    normalized_pairs = []
    used_green, used_red = set(), set()
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Each pair needs one LIVE and one DEAD identifier.")
        g, r = _identifier(pair[0], "LIVE"), _identifier(pair[1], "DEAD")
        if g not in green or r not in red:
            raise ValueError("A paired object does not belong to this candidate group.")
        if g in used_green or r in used_red:
            raise ValueError("Each LIVE or DEAD object can appear in only one pair.")
        used_green.add(g)
        used_red.add(r)
        normalized_pairs.append([g, r])
    if name == "same_cell" and (len(green) != 1 or len(red) != 1):
        raise ValueError("Same-cell review requires exactly one LIVE and one DEAD object; specify pairs for complex groups.")
    if name in ("same_cell", "pairs", "separate") and not (green and red):
        raise ValueError("Pairing decisions apply only to mixed LIVE/DEAD groups.")
    return {"decision": name, "pairs": normalized_pairs, "note": note}


def _finalize(counts):
    result = dict(counts)
    g, r = result["green_objects"], result["red_objects"]
    lo, hi = result["paired_min"], result["paired_max"]
    result.update(
        live_min=g - hi, live_max=g - lo, total_min=g + r - hi,
        total_max=g + r - lo, dead_count=r, definite_same_cell_pairs=lo,
        pending_pair_max=hi - lo,
    )
    result["viability_min_pct"] = (100.0 * result["live_min"] / result["total_min"]
                                     if result["total_min"] else None)
    result["viability_max_pct"] = (100.0 * result["live_max"] / result["total_max"]
                                     if result["total_max"] else None)
    result["viability_pct"] = (result["viability_min_pct"]
                               if not result["pending_groups"] else None)
    return result


def _summarize(objects, decisions):
    if not isinstance(decisions, Mapping):
        raise ValueError("Review decisions must be keyed by object identifier.")
    canonical = {}
    for key, value in decisions.items():
        identifier = _identifier(key, "candidate")
        if identifier in canonical:
            raise ValueError("Duplicate candidate review identifier.")
        canonical[identifier] = value
    counts = dict.fromkeys(_SUM_KEYS, 0)
    seen, all_green, all_red, normalized = set(), set(), set(), {}
    for obj in objects:
        if not isinstance(obj, Mapping):
            raise ValueError("Saved objects must be records.")
        identifier = _identifier(obj.get("object_id"), "candidate")
        if identifier in seen:
            raise ValueError("Duplicate candidate object identifier.")
        seen.add(identifier)
        green = _members(obj.get("green_ids"), "LIVE")
        red = _members(obj.get("red_ids"), "DEAD")
        if not green and not red:
            raise ValueError("A candidate group has no channel objects.")
        if all_green.intersection(green) or all_red.intersection(red):
            raise ValueError("A channel object belongs to more than one candidate group.")
        all_green.update(green)
        all_red.update(red)
        status = obj.get("status")
        if status not in ("green_only", "red_only", "dual_positive_candidate", "unresolved"):
            raise ValueError(f"Unknown saved object status: {status!r}.")
        mixed = bool(green and red)
        if ((status == "green_only" and (red or len(green) != 1))
                or (status == "red_only" and (green or len(red) != 1))
                or (status in ("dual_positive_candidate", "unresolved") and not mixed)
                or (status == "dual_positive_candidate" and (len(green) != 1 or len(red) != 1))):
            raise ValueError("Saved object status disagrees with its channel membership.")
        decision = _decision(canonical.get(identifier, {}), green, red)
        if identifier in canonical:
            normalized[identifier] = decision
        counts["total_groups"] += 1
        counts["green_objects"] += len(green)
        counts["red_objects"] += len(red)
        if status == "dual_positive_candidate":
            counts["dual_candidates"] += 1
        elif status == "unresolved":
            counts["unresolved_groups"] += 1
        else:
            counts[status] += 1
        if not mixed:
            continue
        counts["mixed_groups"] += 1
        name = decision["decision"]
        if name in ("unreviewed", "uncertain"):
            counts["pending_groups"] += 1
            counts["uncertain_groups" if name == "uncertain" else "unreviewed_groups"] += 1
            counts["paired_max"] += min(len(green), len(red))
        else:
            counts["reviewed_groups"] += 1
            n_pairs = 1 if name == "same_cell" else len(decision["pairs"])
            counts["paired_min"] += n_pairs
            counts["paired_max"] += n_pairs
    if set(canonical) - seen:
        raise ValueError("A review references a candidate absent from this saved analysis.")
    return _finalize(counts), normalized


def summarize_objects(objects, decisions=None):
    """Return counts and scenario bounds from immutable saved object records."""
    return _summarize(objects, {} if decisions is None else decisions)[0]


def aggregate_metrics(metrics):
    """Pool integer object counts; never average field viability percentages."""
    counts = dict.fromkeys(_SUM_KEYS, 0)
    for item in metrics:
        for key in _SUM_KEYS:
            value = item[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"Invalid integer count: {key}.")
            counts[key] += value
    return _finalize(counts)


def _report(result, decisions):
    if not isinstance(decisions, Mapping):
        raise ValueError("Reviews must be keyed by field identifier.")
    fields, images, grouped, normalized, seen = {}, [], {}, {}, set()
    for field in result["fields"]:
        name, replicate = field["image_id"], field["replicate_id"]
        if not isinstance(name, str) or not name or name.casefold() in seen:
            raise ValueError("Duplicate or invalid review field identifier.")
        if not isinstance(replicate, str) or not replicate:
            raise ValueError("Invalid review replicate identifier.")
        seen.add(name.casefold())
        with Path(field["result"]["objects"]).open(encoding="utf-8", newline="") as stream:
            metrics, field_decisions = _summarize(list(csv.DictReader(stream)), decisions.get(name, {}))
        fields[name] = metrics
        if name in decisions:
            normalized[name] = field_decisions
        images.append({"image_id": name, "replicate_id": replicate, **metrics})
        grouped.setdefault(replicate, []).append(metrics)
    if set(decisions) - set(fields):
        raise ValueError("A review references a field absent from this saved analysis.")
    replicas = [{"replicate_id": key, "n_fields": len(values), **aggregate_metrics(values)}
                for key, values in grouped.items()]
    report = {"overall": aggregate_metrics(fields.values()), "fields": fields,
              "image": images, "replicate": replicas, "assumptions": ASSUMPTIONS}
    return report, normalized


def build_report(result, decisions=None):
    """Build field, replicate and pooled viability tables from saved CSVs."""
    return _report(result, {} if decisions is None else decisions)[0]


def _verified_source(result):
    # This verifies the small metadata/CSV files, not large label volumes.
    from .analysis_3d import read_analysis_3d
    saved = read_analysis_3d(result["path"], verify=False)
    fingerprint = hashlib.sha256((saved["path"] / "checksums_3d.json").read_bytes()).hexdigest()
    return saved, fingerprint


def source_fingerprint(result):
    """Path-independent binding to a verified, completed 3D analysis."""
    return _verified_source(result)[1]


def _cache(result, fingerprint, cache_dir):
    if cache_dir is None:
        cache_dir = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache")))
                     / "LiveDeadCellCounter" / "reviews3d")
    folder = (Path(cache_dir).expanduser().resolve() / fingerprint).resolve()
    source = Path(result["path"]).resolve()
    if folder.is_relative_to(source):
        raise ValueError("Review revisions must be saved outside the original analysis.")
    for ancestor in (folder, *folder.parents):
        if any((ancestor / name).is_file() for name in
               ("run_manifest.json", "volume_run.json", "result.json", "stack_manifest.json", "import_provenance.json")):
            raise ValueError("Review revisions must be saved outside analyses and source-image caches.")
    return folder


def _bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")


def save_review(result, decisions, cache_dir=None):
    """Save an immutable review revision and atomically select it as latest.

    ``cache_dir`` is an optional review storage root (primarily for tests).
    Originals, figures and segmentation parameters are never changed.
    """
    saved, fingerprint = _verified_source(result)
    report, normalized = _report(saved, decisions)
    now = datetime.now(timezone.utc)
    revision = now.strftime("%Y%m%dT%H%M%S.%fZ_") + uuid.uuid4().hex[:12]
    document = {"schema_version": 1, "kind": "live_dead_3d_review",
                "source_fingerprint": fingerprint, "revision_id": revision,
                "created_utc": now.isoformat(), "decisions": normalized, "report": report}
    folder = _cache(saved, fingerprint, cache_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (revision + ".json")
    payload = _bytes(document)
    with path.open("xb") as stream:
        stream.write(payload)
    pointer = {"schema_version": 1, "revision_id": revision,
               "document_sha256": hashlib.sha256(payload).hexdigest()}
    temporary = folder / (".latest-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(_bytes(pointer))
        os.replace(temporary, folder / "latest.json")
    finally:
        temporary.unlink(missing_ok=True)
    return {**document, "path": str(path)}


def _read_document(saved, fingerprint, path):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if (not isinstance(document, dict) or document.get("schema_version") != 1
            or document.get("kind") != "live_dead_3d_review"):
        raise ValueError("This is not a supported 3D review document.")
    if document.get("source_fingerprint") != fingerprint:
        raise ValueError("This review belongs to a different saved 3D analysis.")
    if not isinstance(document.get("revision_id"), str) or not _REVISION.fullmatch(document["revision_id"]):
        raise ValueError("Invalid review revision identifier.")
    report, normalized = _report(saved, document.get("decisions"))
    # Recompute derived values: a portable review never controls the arithmetic.
    return {**document, "decisions": normalized, "report": report, "path": str(Path(path).resolve())}


def load_review_document(result, path):
    """Validate portable review binding and decisions, and recalculate counts."""
    saved, fingerprint = _verified_source(result)
    return _read_document(saved, fingerprint, path)


def load_review(result, cache_dir=None):
    """Load the last saved revision, detecting stale or modified cache files."""
    saved, fingerprint = _verified_source(result)
    folder = _cache(saved, fingerprint, cache_dir)
    latest = folder / "latest.json"
    if not latest.is_file():
        return None
    pointer = json.loads(latest.read_text(encoding="utf-8"))
    if not isinstance(pointer, dict) or pointer.get("schema_version") != 1:
        raise ValueError("Invalid latest 3D review record.")
    revision = pointer.get("revision_id")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise ValueError("Invalid review revision identifier.")
    path = folder / (revision + ".json")
    if path.resolve().parent != folder.resolve():
        raise ValueError("A review revision points outside its storage folder.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != pointer.get("document_sha256"):
        raise ValueError("The saved review revision has changed.")
    document = _read_document(saved, fingerprint, path)
    if document["revision_id"] != revision:
        raise ValueError("Review revision does not match the latest record.")
    return document
