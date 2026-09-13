"""Read Leica containers into explicit, reproducible 2D counting inputs.

Only selected planes/channels are read. The original container is read-only;
neither intensities nor the counting pipeline are changed by this adapter.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import uuid

import liffile
import numpy as np
from PIL import Image


ROLES = ("green", "red", "ebfp")
PROVENANCE_NAME = "import_provenance.json"
SCHEMA_VERSION = 1


class LeicaImportCancelled(RuntimeError):
    """The caller cancelled a Leica read before a complete import was saved."""


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise LeicaImportCancelled("Leica import cancelled.")


def _identity(path):
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() not in (".lif", ".lof"):
        raise ValueError("Choose a Leica .lif or .lof file.")
    if not path.is_file():
        raise ValueError(f"Leica file not found: {path}")
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _same_identity(actual, expected):
    return isinstance(expected, dict) and all(actual[key] == expected.get(key) for key in actual)


def _pixel_size(image):
    factors = {"m": 1e6, "mm": 1e3, "um": 1.0, "µm": 1.0, "μm": 1.0, "nm": 1e-3}
    values = {}
    for element in image.xml_element.findall("./Data/Image/ImageDescription/Dimensions/DimensionDescription"):
        attrs = element.attrib
        if attrs.get("DimID") not in ("1", "2"):
            continue
        try:
            count = int(attrs["NumberOfElements"])
            value = abs(float(attrs["Length"])) * factors[attrs["Unit"]] / (count - 1)
            if math.isfinite(value) and value > 0:
                values[attrs["DimID"]] = value
        except (ValueError, KeyError, ZeroDivisionError):
            continue
    return [values["2"], values["1"]] if "1" in values and "2" in values else None


def _series_info(image, index):
    info = {"index": index, "name": image.name, "path": image.path, "sizes": {},
            "dtype": "", "channels": [], "pixel_size_um": None, "supported": False, "reason": ""}
    try:
        if image.is_flim:
            raise ValueError("Raw FLIM/histogram data are not scalar intensity images.")
        info["sizes"] = sizes = {str(axis): int(size) for axis, size in image.sizes.items()}
        info["dtype"] = dtype = str(image.dtype)
        info["pixel_size_um"] = _pixel_size(image)
        if not {"X", "Y"}.issubset(sizes) or sizes["X"] < 1 or sizes["Y"] < 1:
            raise ValueError("This series has no complete XY image plane.")
        if sizes.get("S", 1) > 1:
            raise ValueError("RGB/sample composites are unsupported. Choose separate scalar fluorescence channels.")
        unusual = [axis for axis, size in sizes.items() if axis not in "CZYXT" and size > 1]
        if unusual:
            raise ValueError("Unsupported varying dimensions: " + ", ".join(unusual) + ". Choose an XY/Z intensity series.")
        if image.tilescan is not None and len(image.tilescan) > 1:
            raise ValueError("Unstitched tile mosaics are unsupported. Export or select a merged XY image first.")
        if dtype not in ("uint8", "uint16"):
            raise ValueError("Only uint8 and uint16 intensity images can be counted; no automatic intensity conversion is performed.")
        elements = sorted(image.xml_element.findall("./Data/Image/ImageDescription/Channels/ChannelDescription"),
                          key=lambda element: int(element.attrib.get("BytesInc", "0")))
        dimensions = image.xml_element.findall("./Data/Image/ImageDescription/Dimensions/DimensionDescription")
        if any(int(element.attrib.get("BitInc", "0")) for element in [*elements, *dimensions]):
            raise ValueError("Bit-packed channel or dimension increments are unsupported.")
        count = sizes.get("C", 1)
        if len(elements) != count:
            raise ValueError("The channel layout is not a supported scalar fluorescence layout.")
        names = image.coords.get("C", ())
        for channel_index, element in enumerate(elements):
            attrs = element.attrib
            bits = int(attrs["Resolution"])
            if int(attrs.get("DataType", "0")) != 0 or not 1 <= bits <= 16:
                raise ValueError("Floating-point or incompatible channel data are unsupported.")
            if (bits <= 8) != (dtype == "uint8"):
                raise ValueError("Mixed channel data types are unsupported.")
            name = str(names[channel_index]) if len(names) == count else f"Ch{channel_index}"
            info["channels"].append({"index": channel_index, "name": name,
                                     "bit_depth": bits, "lut_name": attrs.get("LUTName", "")})
        frame_sizes = image.frames.frame_sizes
        if not {"Y", "X"}.issubset(frame_sizes) or any(
                size > 1 for axis, size in frame_sizes.items() if axis not in ("Y", "X")):
            raise ValueError("The stored frame layout is not a scalar XY image.")
        info["supported"] = True
    except (ValueError, KeyError, IndexError, NotImplementedError, AssertionError, TypeError) as exc:
        info["reason"] = str(exc) or "Unsupported Leica image layout."
    return info


def inspect_leica(path):
    """Return a JSON-serializable catalog without loading image pixel arrays."""
    source = _identity(path)
    with liffile.LifFile(source["path"], memmap=True, squeeze=False) as container:
        series = [_series_info(image, index) for index, image in enumerate(container.images)]
    if _identity(source["path"]) != source:
        raise ValueError("The Leica file changed while reading its metadata. Reopen the file.")
    return {"source": source, "series": series}


def _integer(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer.")
    return int(value)


def _calibration(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Confirm pixel size as two positive values in micrometers (Y, X).")
    try:
        result = [float(item) for item in value]
    except (ValueError, TypeError) as exc:
        raise ValueError("Pixel sizes must be finite positive numbers in micrometers.") from exc
    if any(not math.isfinite(item) or item <= 0 for item in result):
        raise ValueError("Pixel sizes must be finite positive numbers in micrometers.")
    return result


def _selection(request, image, info, *, require_calibration):
    if not info["supported"]:
        raise ValueError(info["reason"])
    sizes = info["sizes"]
    channels = request.get("channels", {})
    if not isinstance(channels, dict) or any(role not in ROLES for role in channels):
        raise ValueError("Assign Leica channels to LIVE, DEAD, and optionally EBFP.")
    selected = {}
    for role in ROLES:
        value = channels.get(role)
        if value is None:
            if role != "ebfp":
                raise ValueError(f"Choose a {'LIVE' if role == 'green' else 'DEAD'} channel.")
            selected[role] = None
            continue
        value = _integer(value, "Channel index")
        if not 0 <= value < len(info["channels"]):
            raise ValueError("Selected channel is outside this series.")
        selected[role] = value
    nonempty = [value for value in selected.values() if value is not None]
    if len(nonempty) != len(set(nonempty)):
        raise ValueError("Assign distinct channels to LIVE, DEAD, and EBFP.")
    mode = request.get("mode", "slice")
    if mode not in ("slice", "max"):
        raise ValueError("Choose a single Z slice or a maximum-intensity projection.")
    depth = sizes.get("Z", 1)
    if mode == "slice":
        z_index = _integer(request.get("z_index", 0), "Z index")
        if not 0 <= z_index < depth:
            raise ValueError("Selected Z slice is outside this series.")
        z_start, z_stop = z_index, z_index + 1
    else:
        z_start = _integer(request.get("z_start", 0), "First Z index")
        z_stop = _integer(request.get("z_stop", depth), "Last Z boundary")
        if not 0 <= z_start < z_stop <= depth:
            raise ValueError("Choose a nonempty Z range within this series.")
        z_index = None
    time_index = _integer(request.get("time_index", 0), "Time index")
    if not 0 <= time_index < sizes.get("T", 1):
        raise ValueError("Selected time point is outside this series.")
    pixel_size = request.get("pixel_size_um")
    if pixel_size is None and not require_calibration:
        pixel_size = info["pixel_size_um"]
    if pixel_size is not None or require_calibration:
        pixel_size = _calibration(pixel_size)
    return {"series_index": info["index"], "series_name": info["name"], "series_path": info["path"],
            "sizes": sizes, "channels": selected, "mode": mode, "z_index": z_index,
            "z_start": z_start, "z_stop": z_stop, "time_index": time_index,
            "pixel_size_um": pixel_size}


def _source_and_image(request, container, source, *, require_calibration):
    expected = request.get("source_identity")
    if expected is not None and not _same_identity(source, expected):
        raise ValueError("The Leica file changed since it was opened. Reopen it before importing.")
    index = _integer(request.get("series_index", 0), "Series index")
    if not 0 <= index < len(container.images):
        raise ValueError("Selected series is no longer available.")
    image = container.images[index]
    info = _series_info(image, index)
    selection = _selection(request, image, info, require_calibration=require_calibration)
    return image, info, selection


def _plane(image, channel, z_index, time_index):
    indices = {axis: 0 for axis in image.sizes if axis not in ("Y", "X")}
    for axis, value in (("C", channel), ("Z", z_index), ("T", time_index)):
        if axis in image.sizes:
            indices[axis] = value
    plane = image.frame(**indices)
    axes = list(image.frames.frame_sizes)
    for index in reversed(range(len(axes))):
        if axes[index] not in ("Y", "X"):
            if plane.shape[index] != 1:
                raise ValueError("A selected Leica frame is not scalar XY data.")
            plane = np.squeeze(plane, axis=index)
            axes.pop(index)
    if set(axes) != {"Y", "X"} or plane.ndim != 2:
        raise ValueError("A selected Leica frame is not an XY image.")
    plane = np.ascontiguousarray(plane.transpose(axes.index("Y"), axes.index("X")))
    if plane.shape != (image.sizes["Y"], image.sizes["X"]) or plane.dtype != image.dtype:
        raise ValueError("Decoded Leica frame shape or dtype does not match its metadata.")
    return plane


def _read_selection(image, selection, progress, cancelled):
    selected = [(role, index) for role, index in selection["channels"].items() if index is not None]
    total = (selection["z_stop"] - selection["z_start"]) * len(selected)
    arrays, done = {}, 0
    if progress is not None:
        progress(done, total)
    for z_index in range(selection["z_start"], selection["z_stop"]):
        for role, channel in selected:
            _check_cancelled(cancelled)
            plane = _plane(image, channel, z_index, selection["time_index"])
            if role in arrays:
                np.maximum(arrays[role], plane, out=arrays[role])
            else:
                arrays[role] = plane.copy()
            done += 1
            if progress is not None:
                progress(done, total)
    _check_cancelled(cancelled)
    return arrays


def _display(info, selection):
    return {role: [0, (1 << info["channels"][index]["bit_depth"]) - 1]
            for role, index in selection["channels"].items() if index is not None}


def preview_selection(request, cancelled=None):
    """Read a requested plane/projection; optional preview_max_size only decimates display arrays."""
    _check_cancelled(cancelled)
    source = _identity(request["source"])
    with liffile.LifFile(source["path"], memmap=True, squeeze=False) as container:
        image, info, selection = _source_and_image(request, container, source, require_calibration=False)
        arrays = _read_selection(image, selection, None, cancelled)
    if _identity(source["path"]) != source:
        raise ValueError("The Leica file changed during preview. Reopen it before importing.")
    stride = 1
    if request.get("preview_max_size") is not None:
        maximum = _integer(request["preview_max_size"], "Preview size")
        if maximum < 1:
            raise ValueError("Preview size must be positive.")
        stride = max(1, math.ceil(max(info["sizes"]["Y"], info["sizes"]["X"]) / maximum))
        arrays = {role: np.ascontiguousarray(array[::stride, ::stride]) for role, array in arrays.items()}
    return {"channels": arrays, "selection": selection, "source": source, "series": info,
            "display": _display(info, selection), "preview_stride": stride}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stem(name):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_.-")[:48] or "image"
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        value = "image_" + value
    return value


def _bundle(folder, info, selection, cache_key):
    z_description = (f"Z slice {selection['z_index'] + 1}/{info['sizes'].get('Z', 1)}"
                     if selection["mode"] == "slice" else
                     f"maximum projection Z {selection['z_start'] + 1}–{selection['z_stop']}")
    image_id = f"{_stem(info['name'])}_{cache_key[:8]}"
    row = {"image_id": image_id, "replicate_id": "sample_1"}
    row.update({role: str(folder / f"{role}.tif") if selection["channels"][role] is not None else "" for role in ROLES})
    return {"row": row, "input": {"expected_shape": [info["sizes"]["Y"], info["sizes"]["X"]],
                                   "dtype": info["dtype"], "pixel_size_um": selection["pixel_size_um"]},
            "display": _display(info, selection), "provenance_path": str(folder / PROVENANCE_NAME),
            "description": f"{info['name']} · {z_description} · time point {selection['time_index'] + 1}"}


def _cached_import(folder, key, info, selection, cancelled):
    """Validate exported bytes before reusing a successful import."""
    try:
        if folder.is_symlink() or not folder.is_dir():
            return None
        sidecar = folder / PROVENANCE_NAME
        if sidecar.is_symlink() or sidecar.stat().st_size > 1024 * 1024:
            return None
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        if record.get("schema_version") != SCHEMA_VERSION or record.get("cache_key") != key or record.get("selection") != selection:
            return None
        selected = {role for role, index in selection["channels"].items() if index is not None}
        if set(record.get("exports", {})) != selected:
            return None
        for role in selected:
            _check_cancelled(cancelled)
            entry = record["exports"][role]
            path = folder / f"{role}.tif"
            if (entry.get("filename") != path.name or path.is_symlink() or not path.is_file()
                    or path.stat().st_size != entry.get("bytes") or _sha256(path) != entry.get("sha256")
                    or entry.get("shape") != [info["sizes"]["Y"], info["sizes"]["X"]] or entry.get("dtype") != info["dtype"]):
                return None
        result = _bundle(folder, info, selection, key)
        result["cached"] = True
        return result
    except (OSError, ValueError, TypeError, KeyError):
        return None


def import_selection(request, output_base, progress=None, cancelled=None):
    """Materialize selected original pixels into immutable, cached TIFF inputs.

    Maximum projections stream one plane at a time and retain only one 2D
    accumulator per selected channel. Cache identity uses source stat metadata,
    selection, calibration, and the pinned reader version, not a whole-file hash.
    """
    _check_cancelled(cancelled)
    source = _identity(request["source"])
    base = Path(output_base).expanduser().resolve()
    if any((parent / "run_manifest.json").exists() for parent in (base, *base.parents)):
        raise ValueError("Save imported images outside completed analysis run folders.")
    with liffile.LifFile(source["path"], memmap=True, squeeze=False) as container:
        image, info, selection = _source_and_image(request, container, source, require_calibration=True)
        key_data = {"schema_version": SCHEMA_VERSION, "source": source, "selection": selection,
                    "reader_version": liffile.__version__}
        key = hashlib.sha256(json.dumps(key_data, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
        prefix = f"{_stem(info['name'])}_{key[:16]}"
        if base.is_dir():
            for candidate in sorted(base.glob(prefix + "*")):
                cached = _cached_import(candidate, key, info, selection, cancelled)
                if cached is not None:
                    if _identity(source["path"]) != source:
                        raise ValueError("The Leica file changed during import. Reopen it before importing.")
                    if progress is not None:
                        progress(1, 1)
                    return cached
        arrays = _read_selection(image, selection, progress, cancelled)
    if _identity(source["path"]) != source:
        raise ValueError("The Leica file changed during import. Reopen it before importing.")
    base.mkdir(parents=True, exist_ok=True)
    final = base / prefix
    if final.exists():
        final = base / f"{prefix}_{uuid.uuid4().hex[:8]}"
    staging = Path(tempfile.mkdtemp(prefix=".leica_pending_", dir=base)).resolve()
    try:
        result = _bundle(final, info, selection, key)
        exports = {}
        for role, array in arrays.items():
            _check_cancelled(cancelled)
            path = staging / f"{role}.tif"
            Image.fromarray(array).save(path, format="TIFF", compression="raw")
            exports[role] = {"filename": path.name, "sha256": _sha256(path), "bytes": path.stat().st_size,
                             "shape": list(array.shape), "dtype": str(array.dtype)}
        record = {"schema_version": SCHEMA_VERSION, "reader": {"name": "liffile", "version": liffile.__version__},
                  "source": {**source, "identity_method": "Path, byte size and modification time; source file not SHA256 hashed."},
                  "selection": selection, "channel_metadata": info["channels"],
                  "dimensionality": ("2D optical slice" if selection["mode"] == "slice" else
                                     "2D maximum-intensity projection; not a 3D cell count"),
                  "exports": exports, "cache_key": key, "result": result}
        (staging / PROVENANCE_NAME).write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        _check_cancelled(cancelled)
        if _identity(source["path"]) != source:
            raise ValueError("The Leica file changed during export. Reopen it before importing.")
        if staging.parent != base or final.resolve().parent != base or final.is_symlink():
            raise ValueError("The import destination changed while saving. Choose the import folder again.")
        if final.exists():
            raise FileExistsError("Another import created this destination. Import again to reuse or create a separate copy.")
        staging.rename(final)
        result["cached"] = False
        return deepcopy(result)
    finally:
        # Only delete the unique unfinished directory created by this call.
        if staging.exists() and staging.resolve() == staging and staging.parent == base and staging.name.startswith(".leica_pending_"):
            shutil.rmtree(staging)
