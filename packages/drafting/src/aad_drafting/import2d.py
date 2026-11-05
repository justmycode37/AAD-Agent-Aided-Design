"""Exact, editable 2D imports from DXF and SVG.

The importer keeps source curves as analytic records. It never tessellates
Bezier or elliptical geometry to make it fit the current Sheet2D backend.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
from functools import wraps
import io
import math
from pathlib import Path
import re
from typing import Any
from xml.etree import ElementTree as ET


_UNIT_MM = {"mm": 1.0, "in": 25.4}
_DXF_UNITS_MM = {
    1: 25.4,          # inches
    2: 304.8,         # feet
    3: 1_609_344.0,   # miles
    4: 1.0,           # millimeters
    5: 10.0,          # centimeters
    6: 1_000.0,       # meters
    7: 1_000_000.0,   # kilometers
    8: 0.0000254,     # microinches
    9: 0.0254,        # mils
    10: 914.4,        # yards
    11: 1e-7,         # angstroms
    12: 1e-6,         # nanometers
    13: 0.001,        # microns
    14: 100.0,        # decimeters
    15: 10_000.0,     # dekameters
    16: 100_000.0,    # hectometers
    17: 1_000_000_000_000.0,  # gigameters
}

_CANONICAL_LAYERS = {
    "0": "cut",
    "layer0": "cut",
    "cut": "cut",
    "cuts": "cut",
    "cutting": "cut",
    "cutline": "cut",
    "cutpath": "cut",
    "lasercut": "cut",
    "outline": "cut",
    "profile": "cut",
    "score": "score",
    "scoring": "score",
    "fold": "score",
    "foldline": "score",
    "bend": "score",
    "crease": "score",
    "engrave": "engrave",
    "engraving": "engrave",
    "etch": "engrave",
    "etching": "engrave",
    "mark": "engrave",
    "marking": "engrave",
}

_SVG_TAGS = {
    "svg", "g", "line", "polyline", "polygon", "circle", "ellipse",
    "rect", "path", "text", "tspan", "title", "desc", "metadata",
    "namedview", "defs",
}
_SVG_NON_RENDERING_TAGS = {"title", "desc", "metadata", "namedview"}
_SVG_ACTIVE_TAGS = {
    "script", "foreignobject", "image", "use", "iframe", "object", "embed",
    "audio", "video", "animate", "animatemotion", "animatetransform", "set",
    "discard", "feimage",
}
_SVG_LENGTH_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z%]*)\s*$"
)
_SVG_NUMBER_RE = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
)


class Import2DError(ValueError):
    """A file is malformed, ambiguous, unsafe, or contains unsupported geometry."""


def _units(value: str | None, *, default: str | None = None) -> str | None:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    aliases = {
        "millimeter": "mm",
        "millimeters": "mm",
        "millimetre": "mm",
        "millimetres": "mm",
        "inch": "in",
        "inches": "in",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in _UNIT_MM:
        raise ValueError(f"Units must be 'mm' or 'in', got {value!r}.")
    return normalized


def _layer_mapper(layer_map: Mapping[str, str] | None) -> dict[str, str]:
    if layer_map is None:
        return {}
    if not isinstance(layer_map, Mapping):
        raise ValueError("layer_map must map source layer names to imported names.")
    normalized: dict[str, str] = {}
    for source, target in layer_map.items():
        if not isinstance(source, str) or not source.strip():
            raise ValueError("layer_map source names must be nonempty strings.")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("layer_map targets must be nonempty strings.")
        normalized[source.strip()] = target.strip()
    return normalized


def _mapped_layer(source: str | None, layer_map: Mapping[str, str]) -> tuple[str, str]:
    raw = str(source or "layer0").strip() or "layer0"
    target = layer_map.get(raw)
    if target is None:
        target = next((value for key, value in layer_map.items() if key.casefold() == raw.casefold()), raw)
    token = re.sub(r"[\s_-]+", "", target.casefold())
    return _CANONICAL_LAYERS.get(token, target), raw


def _finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise Import2DError(f"{label} must be a finite number, not a boolean.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise Import2DError(f"{label} must be a finite number.") from exc
    if not math.isfinite(result):
        raise Import2DError(f"{label} must be a finite number.")
    return result


def _point2(point: Sequence[Any], *, label: str = "point") -> tuple[float, float]:
    if len(point) < 2:
        raise Import2DError(f"{label} must have at least two coordinates.")
    return (_finite(point[0], label=f"{label}.x"), _finite(point[1], label=f"{label}.y"))


def _record(
    name: str,
    kind: str,
    layer: str,
    points: Sequence[Sequence[Any]] = (),
    *,
    radius: float | None = None,
    start_angle: float | None = None,
    end_angle: float | None = None,
    closed: bool = False,
    text: str | None = None,
    height: float | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(closed, bool):
        raise Import2DError(f"{name}.closed must be a boolean.")
    metadata_copy = deepcopy(dict(metadata or {}))
    for flag in ("ccw", "text_is_similarity_transform"):
        if flag in metadata_copy and not isinstance(metadata_copy[flag], bool):
            raise Import2DError(f"{name}.metadata.{flag} must be a boolean.")
    # Keep tuples in the in-memory records, but validate that provenance can be
    # serialized without leaking parser objects or non-finite values.
    _json_safe(metadata_copy)
    return {
        "name": str(name),
        "kind": str(kind),
        "layer": str(layer),
        "points": tuple(_point2(point) for point in points),
        "radius": _finite(radius, label=f"{name}.radius") if radius is not None else None,
        "start_angle": _finite(start_angle, label=f"{name}.start_angle") if start_angle is not None else None,
        "end_angle": _finite(end_angle, label=f"{name}.end_angle") if end_angle is not None else None,
        "closed": closed,
        "text": str(text) if text is not None else None,
        "height": _finite(height, label=f"{name}.height") if height is not None else None,
        "metadata": metadata_copy,
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Imported2D data cannot contain non-finite numbers.")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise TypeError(f"Imported2D data is not JSON-safe: {type(value).__name__}.")


def _sheet_transaction(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        from .model2d import _LATEST_SHEET
        token = _LATEST_SHEET.set(_LATEST_SHEET.get())
        try:
            return method(*args, **kwargs)
        except BaseException:
            _LATEST_SHEET.reset(token)
            raise
    return wrapped


@dataclass
class Imported2D:
    """Parsed 2D geometry plus its source and layer provenance."""

    units: str
    entities: list[dict[str, Any]]
    layers: list[str]
    source: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of the import."""
        return _json_safe({
            "units": self.units,
            "entities": self.entities,
            "layers": self.layers,
            "source": self.source,
            "warnings": self.warnings,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Imported2D":
        """Rebuild an Imported2D value from to_dict output."""
        if not isinstance(value, Mapping):
            raise TypeError("Imported2D.from_dict expects a mapping.")
        units = _units(value.get("units"))
        raw_entities = value.get("entities")
        if not isinstance(raw_entities, list):
            raise ValueError("Imported2D entities must be a list.")
        entities: list[dict[str, Any]] = []
        for raw in raw_entities:
            if not isinstance(raw, Mapping):
                raise ValueError("Each imported entity must be a mapping.")
            points = raw.get("points", ())
            metadata = raw.get("metadata", {})
            if not isinstance(metadata, Mapping):
                raise ValueError(f"Entity {raw.get('name')!r} metadata must be a mapping.")
            entities.append(_record(
                str(raw.get("name", "")),
                str(raw.get("kind", "")),
                str(raw.get("layer", "layer0")),
                points,
                radius=raw.get("radius"),
                start_angle=raw.get("start_angle"),
                end_angle=raw.get("end_angle"),
                closed=raw.get("closed", False),
                text=raw.get("text"),
                height=raw.get("height"),
                metadata=metadata,
            ))
        layers = value.get("layers", [])
        source = value.get("source", {})
        warnings = value.get("warnings", [])
        if not isinstance(layers, list) or not all(isinstance(item, str) for item in layers):
            raise ValueError("Imported2D layers must be a list of names.")
        if not isinstance(source, Mapping) or not isinstance(warnings, list):
            raise ValueError("Imported2D source metadata and warnings must be mappings and lists.")
        return cls(
            units=units or "mm",
            entities=entities,
            layers=list(layers),
            source=deepcopy(dict(source)),
            warnings=[str(item) for item in warnings],
        )

    @property
    def source_metadata(self) -> dict[str, Any]:
        """Alias that makes the provenance field explicit at call sites."""
        return self.source

    @_sheet_transaction
    def to_sheet(self, name: str | None = None) -> Any:
        """Adapt supported records to Sheet2D, importing its backend lazily."""
        unsupported: list[str] = []
        for entity in self.entities:
            kind = entity["kind"]
            metadata = entity["metadata"]
            if kind == "svg_arc":
                unsupported.append(f"{entity['name']} ({kind})")
            elif kind == "polyline" and (
                abs(float(metadata.get("const_width", 0))) > 0 or
                any(abs(float(width)) > 0 for pair in metadata.get("widths", ()) for width in pair)
            ):
                unsupported.append(f"{entity['name']} (nonzero polyline width)")
            elif kind == "text" and abs(float(metadata.get("rotation", 0.0))) > 1e-12:
                unsupported.append(f"{entity['name']} (rotated text)")
            elif kind == "text" and metadata.get("text_is_similarity_transform") is False:
                unsupported.append(f"{entity['name']} (nonuniformly transformed text)")
            elif kind not in {"line", "polyline", "circle", "arc", "text", "cubic_bezier", "quadratic_bezier", "ellipse", "elliptical_arc"}:
                unsupported.append(f"{entity['name']} ({kind})")
        if unsupported:
            raise NotImplementedError(
                "Sheet2D cannot preserve these exact imported record types yet: "
                + ", ".join(unsupported)
            )

        try:
            from .model2d import Sheet2D
        except ImportError as exc:
            raise RuntimeError(
                "Sheet2D is not available in this checkout; Imported2D parsing is independent."
            ) from exc

        default_name = Path(str(self.source.get("path", "imported-2d"))).stem or "imported-2d"
        sheet = Sheet2D(name=name or default_name, units=self.units)
        for layer in self.layers:
            sheet.layers.setdefault(layer, {})

        for entity in self.entities:
            kind = entity["kind"]
            layer = entity["layer"]
            points = entity["points"]
            metadata = deepcopy(entity["metadata"])
            metadata.setdefault("source_name", entity["name"])
            entity_name = entity["name"]
            if kind == "line":
                made = sheet.line(entity_name, points[0], points[1], layer=layer)
            elif kind == "circle":
                made = sheet.circle(entity_name, points[0], entity["radius"], layer=layer)
            elif kind == "arc":
                made = sheet.arc(
                    entity_name, points[0], entity["radius"], entity["start_angle"],
                    entity["end_angle"], layer=layer, clockwise=not metadata.get("ccw", True),
                )
            elif kind == "polyline":
                bulges = metadata.get("bulges", ())
                if any(abs(float(value)) > 1e-15 for value in bulges):
                    _bulged_polyline_to_sheet(sheet, entity)
                    continue
                made = sheet.polyline(entity_name, points, closed=entity["closed"], layer=layer)
            elif kind in {"cubic_bezier", "quadratic_bezier"}:
                made = sheet.bezier(entity_name, points, layer=layer)
            elif kind in {"ellipse", "elliptical_arc"}:
                conic = metadata["ellipse"]
                made = sheet.ellipse(entity_name, conic["center"], conic["rx"], conic["ry"],
                    rotation=conic["rotation"], start_angle=conic.get("start_parameter", 0),
                    end_angle=conic.get("end_parameter", 360),
                    clockwise=conic.get("sweep", 360) < 0, layer=layer)
            else:
                made = sheet.text(entity_name, entity["text"] or "", points[0],
                                  height=entity["height"], layer=layer)
            made.metadata.update(metadata)

        sheet.history.append({"operation": "import", "source": deepcopy(self.source),
                              "warnings": list(self.warnings),
                              "entity_names": list(sheet.entities)})

        return sheet


def _bulged_polyline_to_sheet(sheet, entity):
    points = entity["points"]
    bulges = entity["metadata"]["bulges"]
    count = len(points) if entity["closed"] else len(points) - 1
    for index in range(count):
        start, end = points[index], points[(index + 1) % len(points)]
        bulge = float(bulges[index]) if index < len(bulges) else 0.0
        name = f"{entity['name']}.segment{index}"
        if abs(bulge) <= 1e-15:
            made = sheet.line(name, start, end, layer=entity["layer"])
        else:
            dx, dy = end[0] - start[0], end[1] - start[1]
            chord = math.hypot(dx, dy)
            if chord <= 0:
                raise Import2DError(f"Bulged segment {name!r} has coincident endpoints")
            factor = (1 - bulge * bulge) / (4 * bulge)
            center = ((start[0] + end[0]) / 2 - dy * factor,
                      (start[1] + end[1]) / 2 + dx * factor)
            radius = chord * (1 + bulge * bulge) / (4 * abs(bulge))
            a0 = math.degrees(math.atan2(start[1] - center[1], start[0] - center[0]))
            a1 = math.degrees(math.atan2(end[1] - center[1], end[0] - center[0]))
            made = sheet.arc(name, center, radius, a0, a1, layer=entity["layer"], clockwise=bulge < 0)
        made.metadata.update(deepcopy(entity["metadata"]))
        made.metadata.update(source_name=entity["name"], segment_index=index, bulge=bulge)


def _unique_name(base: str, seen: set[str]) -> str:
    base = base.strip() or "entity"
    if base not in seen:
        seen.add(base)
        return base
    index = 2
    while f"{base}#{index}" in seen:
        index += 1
    result = f"{base}#{index}"
    seen.add(result)
    return result


def _source_path(path: Path, raw: bytes, fmt: str) -> dict[str, Any]:
    return {
        "format": fmt,
        "path": str(path.resolve()),
        "filename": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _vec3(value: Any) -> tuple[float, float, float]:
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        raw = (value.x, value.y, value.z)
    else:
        raw = (value[0], value[1], value[2])
    return tuple(_finite(component, label="3D coordinate") for component in raw)  # type: ignore[return-value]


def _dxf_unit_scale(doc: Any, requested_units: str | None) -> tuple[str, float, dict[str, Any], str | None]:
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    unit_name = {
        1: "in", 2: "ft", 3: "mi", 4: "mm", 5: "cm", 6: "m", 7: "km",
        8: "microin", 9: "mil", 10: "yd", 11: "angstrom", 12: "nm",
        13: "micron", 14: "dm", 15: "dam", 16: "hm", 17: "Gm",
    }.get(code)
    explicit = _units(requested_units)
    if code == 0:
        if explicit is None:
            raise Import2DError(
                "DXF $INSUNITS is unitless; pass units='mm' or units='in' to interpret its coordinates."
            )
        return explicit, 1.0, {
            "declared_code": 0,
            "declared_name": "unitless",
            "interpretation": explicit,
            "output_units": explicit,
        }, (
            f"Unitless DXF coordinates were interpreted as {explicit} because units={explicit!r} was supplied."
        )
    mm_per_source = _DXF_UNITS_MM.get(code)
    if mm_per_source is None:
        raise Import2DError(
            f"DXF $INSUNITS code {code} is not supported; pass a file with a recognized physical unit."
        )
    output_units = explicit or ("in" if code == 1 else "mm")
    scale = mm_per_source / _UNIT_MM[output_units]
    return output_units, scale, {
        "declared_code": code,
        "declared_name": unit_name or "unknown",
        "millimeters_per_source_unit": mm_per_source,
        "output_units": output_units,
        "scale_to_output_units": scale,
    }, None


def _dxf_normal(entity: Any, *, label: str) -> tuple[Any, tuple[float, float], tuple[float, float], float]:
    from ezdxf.math import Vec3

    try:
        ocs = entity.ocs()
        normal = Vec3(getattr(entity.dxf, "extrusion", (0, 0, 1))).normalize()
        if abs(normal.x) > 1e-9 or abs(normal.y) > 1e-9:
            raise Import2DError(
                f"{label} has a tilted extrusion normal {tuple(normal)}; only XY-planar entities are supported."
            )
        origin = ocs.to_wcs(Vec3(0, 0, 0))
        ux3 = ocs.to_wcs(Vec3(1, 0, 0)) - origin
        uy3 = ocs.to_wcs(Vec3(0, 1, 0)) - origin
        determinant = ux3.x * uy3.y - ux3.y * uy3.x
        return ocs, (float(ux3.x), float(ux3.y)), (float(uy3.x), float(uy3.y)), determinant
    except Import2DError:
        raise
    except Exception as exc:
        raise Import2DError(f"Could not interpret the OCS plane for {label}: {exc}") from exc


def _remember_plane_z(z: float, plane_z: float | None, label: str) -> float:
    if plane_z is None:
        return z
    tolerance = max(1.0, abs(z), abs(plane_z)) * 1e-9
    if abs(z - plane_z) > tolerance:
        raise Import2DError(
            f"{label} lies at z={z:g}, while other imported entities lie at z={plane_z:g}; "
            "a single planar XY drawing is required."
        )
    return plane_z


def _angle_in_wcs(angle_degrees: float, ux: tuple[float, float], uy: tuple[float, float]) -> float:
    angle = math.radians(angle_degrees)
    x = ux[0] * math.cos(angle) + uy[0] * math.sin(angle)
    y = ux[1] * math.cos(angle) + uy[1] * math.sin(angle)
    return math.degrees(math.atan2(y, x)) % 360.0


def parse_dxf(
    path: str | Path,
    units: str | None = None,
    layer_map: Mapping[str, str] | None = None,
) -> Imported2D:
    """Parse supported planar DXF entities into exact 2D records.

    If units is omitted, the DXF's INSUNITS chooses the output unit. For a
    unitless DXF, units is required and interprets its raw coordinate values.
    For a DXF with declared units, units selects the output unit and coordinates
    are converted accordingly.
    """
    source_path = Path(path)
    try:
        raw = source_path.read_bytes()
    except OSError as exc:
        raise Import2DError(f"Cannot read DXF file {source_path}: {exc}") from exc
    try:
        import ezdxf
    except ImportError as exc:
        raise Import2DError("DXF import requires ezdxf==1.4.5.") from exc

    try:
        doc = ezdxf.readfile(source_path)
    except Exception as exc:
        raise Import2DError(f"Could not read DXF file {source_path}: {exc}") from exc

    try:
        auditor = doc.audit()
    except Exception as exc:
        raise Import2DError(f"DXF audit failed for {source_path}: {exc}") from exc
    if auditor.errors:
        details = []
        for error in auditor.errors[:8]:
            entity = getattr(error, "entity", None)
            handle = getattr(getattr(entity, "dxf", None), "handle", None)
            details.append(f"{getattr(error, 'code', 'audit error')} (handle={handle or 'unknown'})")
        more = f"; and {len(auditor.errors) - len(details)} more" if len(auditor.errors) > len(details) else ""
        raise Import2DError(
            "DXF audit found errors and the file was rejected: " + ", ".join(details) + more
        )

    layer_mapper = _layer_mapper(layer_map)
    output_units, scale, unit_metadata, unit_warning = _dxf_unit_scale(doc, units)
    warnings = [unit_warning] if unit_warning else []
    fixes = list(getattr(auditor, "fixes", ()) or ())
    if fixes:
        warnings.append(f"DXF audit repaired {len(fixes)} issue(s) in memory before import.")

    imported_layers: list[str] = []
    layer_details: list[dict[str, Any]] = []
    layer_cache: dict[str, str] = {}

    def resolve_layer(raw_name: str | None) -> tuple[str, str]:
        source_name = str(raw_name or "0")
        if source_name not in layer_cache:
            layer, raw_value = _mapped_layer(source_name, layer_mapper)
            layer_cache[source_name] = layer
            if layer not in imported_layers:
                imported_layers.append(layer)
            layer_obj = doc.layers.get(source_name) if source_name in doc.layers else None
            detail: dict[str, Any] = {"name": layer, "source_name": raw_value}
            if layer_obj is not None:
                detail["handle"] = str(layer_obj.dxf.get("handle", ""))
                detail["color"] = int(layer_obj.dxf.get("color", 7))
                detail["linetype"] = str(layer_obj.dxf.get("linetype", "CONTINUOUS"))
            layer_details.append(detail)
        return layer_cache[source_name], source_name

    # Keep empty source layers too; they are part of the drawing organization.
    for layer_obj in doc.layers:
        resolve_layer(str(layer_obj.dxf.name))
    if "0" not in layer_cache:
        resolve_layer("0")

    records: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    plane_z: float | None = None

    def add_record(entity: Any, index: int, kind: str, **kwargs: Any) -> None:
        raw_layer = str(entity.dxf.get("layer", "0"))
        layer, source_layer = resolve_layer(raw_layer)
        handle = str(entity.dxf.get("handle", "")) or None
        base = f"{kind}:{handle or index}"
        metadata = {
            "handle": handle,
            "dxf_type": entity.dxftype(),
            "source_layer": source_layer,
            **kwargs.pop("metadata", {}),
        }
        records.append(_record(
            _unique_name(base, seen_names), kind, layer, metadata=metadata, **kwargs
        ))

    modelspace = doc.modelspace()
    for index, entity in enumerate(modelspace):
        dxf_type = entity.dxftype().upper()
        handle = str(entity.dxf.get("handle", "")) or f"index {index}"
        label = f"DXF {dxf_type} entity {handle}"

        if dxf_type == "LINE":
            start = _vec3(entity.dxf.start)
            end = _vec3(entity.dxf.end)
            if abs(start[2] - end[2]) > max(1.0, abs(start[2]), abs(end[2])) * 1e-9:
                raise Import2DError(f"{label} has different start and end z coordinates; nonplanar lines are unsupported.")
            plane_z = _remember_plane_z(start[2], plane_z, label)
            add_record(
                entity, index, "line",
                points=((start[0] * scale, start[1] * scale), (end[0] * scale, end[1] * scale)),
                metadata={"source_start": start, "source_end": end},
            )
            continue

        if dxf_type in {"CIRCLE", "ARC"}:
            ocs, ux, uy, determinant = _dxf_normal(entity, label=label)
            center_ocs = _vec3(entity.dxf.center)
            center_wcs = _vec3(ocs.to_wcs(center_ocs))
            plane_z = _remember_plane_z(center_wcs[2], plane_z, label)
            center = (center_wcs[0] * scale, center_wcs[1] * scale)
            radius = float(entity.dxf.radius) * scale
            if radius <= 0:
                raise Import2DError(f"{label} has a nonpositive radius.")
            if dxf_type == "CIRCLE":
                add_record(
                    entity, index, "circle",
                    points=(center,), radius=radius, closed=True,
                    metadata={
                        "source_center_ocs": center_ocs,
                        "extrusion": _vec3(entity.dxf.get("extrusion", (0, 0, 1))),
                        "ocs_determinant": determinant,
                    },
                )
            else:
                source_start = float(entity.dxf.start_angle)
                source_end = float(entity.dxf.end_angle)
                start_angle = _angle_in_wcs(source_start, ux, uy)
                end_angle = _angle_in_wcs(source_end, ux, uy)
                ccw = determinant > 0
                add_record(
                    entity, index, "arc",
                    points=(center,), radius=radius,
                    start_angle=start_angle, end_angle=end_angle,
                    metadata={
                        "source_center_ocs": center_ocs,
                        "source_start_angle": source_start,
                        "source_end_angle": source_end,
                        "source_ccw": True,
                        "ccw": ccw,
                        "extrusion": _vec3(entity.dxf.get("extrusion", (0, 0, 1))),
                        "ocs_determinant": determinant,
                    },
                )
            continue

        if dxf_type == "LWPOLYLINE":
            ocs, _, _, determinant = _dxf_normal(entity, label=label)
            elevation = _vec3(entity.dxf.get("elevation", (0, 0, 0)))
            vertices = list(entity.get_points("xyseb"))
            if not vertices:
                raise Import2DError(f"{label} has no vertices.")
            points: list[tuple[float, float]] = []
            source_bulges: list[float] = []
            bulges: list[float] = []
            source_widths: list[tuple[float, float]] = []
            point_z: float | None = None
            for vertex_index, vertex in enumerate(vertices):
                x, y = float(vertex[0]), float(vertex[1])
                start_width = float(vertex[2]) if len(vertex) > 2 else 0.0
                end_width = float(vertex[3]) if len(vertex) > 3 else 0.0
                bulge = float(vertex[4]) if len(vertex) > 4 else 0.0
                wcs = _vec3(ocs.to_wcs((x, y, elevation[2])))
                point_z = _remember_plane_z(wcs[2], point_z, f"{label} vertex {vertex_index}")
                points.append((wcs[0] * scale, wcs[1] * scale))
                source_bulges.append(bulge)
                bulges.append(bulge if determinant > 0 else -bulge)
                source_widths.append((start_width, end_width))
            plane_z = _remember_plane_z(point_z or 0.0, plane_z, label)
            add_record(
                entity, index, "polyline",
                points=points, closed=bool(entity.closed),
                metadata={
                    "source_bulges": source_bulges,
                    "bulges": bulges,
                    "source_widths": source_widths,
                    "widths": [(start * scale, end * scale) for start, end in source_widths],
                    "source_const_width": float(entity.dxf.get("const_width", 0)),
                    "const_width": float(entity.dxf.get("const_width", 0)) * scale,
                    "extrusion": _vec3(entity.dxf.get("extrusion", (0, 0, 1))),
                    "ocs_determinant": determinant,
                },
            )
            continue

        if dxf_type in {"TEXT", "MTEXT"}:
            ocs, ux, uy, determinant = _dxf_normal(entity, label=label)
            insert_ocs = _vec3(entity.dxf.insert)
            insert_wcs = _vec3(ocs.to_wcs(insert_ocs))
            plane_z = _remember_plane_z(insert_wcs[2], plane_z, label)
            raw_text = str(entity.dxf.get("text", ""))
            if dxf_type == "TEXT":
                height = float(entity.dxf.height)
                rotation_source = float(entity.dxf.get("rotation", 0.0))
            else:
                height = float(entity.dxf.get("char_height", 0.0))
                rotation_source = float(entity.dxf.get("rotation", 0.0))
            baseline_angle = math.radians(rotation_source)
            baseline_x = ux[0] * math.cos(baseline_angle) + uy[0] * math.sin(baseline_angle)
            baseline_y = ux[1] * math.cos(baseline_angle) + uy[1] * math.sin(baseline_angle)
            rotation = math.degrees(math.atan2(baseline_y, baseline_x)) % 360.0
            text_meta: dict[str, Any] = {
                "source_insert_ocs": insert_ocs,
                "source_rotation": rotation_source,
                "rotation": rotation,
                "extrusion": _vec3(entity.dxf.get("extrusion", (0, 0, 1))),
                "ocs_determinant": determinant,
                "style": str(entity.dxf.get("style", "Standard")),
            }
            for field_name in ("align_point", "halign", "valign", "width", "attachment_point", "text_direction"):
                if entity.dxf.hasattr(field_name):
                    value = entity.dxf.get(field_name)
                    try:
                        text_meta[field_name] = _vec3(value) if field_name in {"align_point", "text_direction"} else value
                    except (TypeError, ValueError):
                        text_meta[field_name] = str(value)
            add_record(
                entity, index, "text",
                points=((insert_wcs[0] * scale, insert_wcs[1] * scale),),
                text=raw_text, height=height * scale,
                metadata=text_meta,
            )
            continue

        raise Import2DError(
            f"{label} on layer {entity.dxf.get('layer', '0')!r} is unsupported. "
            "Supported DXF types are LINE, CIRCLE, ARC, LWPOLYLINE, TEXT, and MTEXT."
        )

    source = _source_path(source_path, raw, "dxf")
    source.update({
        "dxf_version": str(doc.dxfversion),
        "units": unit_metadata,
        "audit": {"errors": 0, "repairs": len(fixes)},
        "layers": layer_details,
        "layer_map": dict(layer_mapper),
        "plane_z": plane_z * scale if plane_z is not None else None,
    })
    return Imported2D(
        units=output_units,
        entities=records,
        layers=imported_layers,
        source=source,
        warnings=warnings,
    )


def _svg_local_name(value: str) -> str:
    return str(value).split("}")[-1].split(":")[-1].lower()


def _svg_attribute(attributes: Mapping[str, Any], wanted: str) -> str | None:
    if wanted in attributes:
        return str(attributes[wanted])
    if wanted not in {"label", "groupmode"}:
        return None
    for key, value in attributes.items():
        if str(key).split("}")[-1].split(":")[-1] == wanted:
            return str(value)
    return None


def _svg_length(value: str, output_units: str, *, label: str) -> float:
    match = _SVG_LENGTH_RE.fullmatch(value)
    if match is None:
        raise Import2DError(f"SVG {label} {value!r} is not a supported physical length.")
    number = _finite(match.group(1), label=f"SVG {label}")
    unit = match.group(2).lower()
    inches_per_unit = {
        "": 1.0 / 96.0,  # unitless root dimensions are CSS pixels
        "px": 1.0 / 96.0,
        "in": 1.0,
        "mm": 1.0 / 25.4,
        "cm": 10.0 / 25.4,
        "q": 0.25 / 25.4,
        "pt": 1.0 / 72.0,
        "pc": 1.0 / 6.0,
    }.get(unit)
    if inches_per_unit is None:
        if unit == "%":
            raise Import2DError(
                f"SVG {label} uses percentage units and has no physical viewport to resolve them."
            )
        raise Import2DError(f"SVG {label} unit {unit!r} is unsupported; use px, mm, cm, in, pt, pc, or q.")
    result = number * inches_per_unit * 25.4 / _UNIT_MM[output_units]
    if not math.isfinite(result) or result <= 0:
        raise Import2DError(f"SVG {label} must resolve to a positive physical size.")
    return result


def _svg_number_list(value: str, *, label: str) -> list[float]:
    tokens: list[float] = []
    position = 0
    for match in _SVG_NUMBER_RE.finditer(value):
        gap = value[position:match.start()]
        if gap.strip(" \t\r\n,"):
            raise Import2DError(f"SVG {label} contains invalid text near {gap!r}.")
        tokens.append(_finite(match.group(), label=label))
        position = match.end()
    if value[position:].strip(" \t\r\n,"):
        raise Import2DError(f"SVG {label} contains invalid trailing text.")
    return tokens


def _svg_viewbox(root: ET.Element) -> tuple[float, float, float, float] | None:
    raw = _svg_attribute(root.attrib, "viewBox")
    if raw is None:
        return None
    values = _svg_number_list(raw, label="viewBox")
    if len(values) != 4:
        raise Import2DError("SVG viewBox must contain exactly four numbers.")
    x, y, width, height = values
    if width <= 0 or height <= 0:
        raise Import2DError("SVG viewBox width and height must be positive.")
    return x, y, width, height


def _svg_viewport(
    root: ET.Element,
    output_units: str,
) -> tuple[float, float, tuple[float, float, float, float] | None]:
    viewbox = _svg_viewbox(root)
    width_raw = _svg_attribute(root.attrib, "width")
    height_raw = _svg_attribute(root.attrib, "height")
    if width_raw is None and height_raw is None:
        raise Import2DError(
            "SVG has no physical width or height; a viewBox alone does not establish a measurement scale."
        )
    width = _svg_length(width_raw, output_units, label="width") if width_raw is not None else None
    height = _svg_length(height_raw, output_units, label="height") if height_raw is not None else None
    if width is None or height is None:
        if viewbox is None:
            raise Import2DError("SVG needs both physical width and height when it has no viewBox.")
        _, _, viewbox_width, viewbox_height = viewbox
        if width is None:
            width = float(height) * viewbox_width / viewbox_height
        if height is None:
            height = float(width) * viewbox_height / viewbox_width
    assert width is not None and height is not None
    return width, height, viewbox


def _validate_svg(data: bytes, path: Path) -> ET.Element:
    try:
        xml_text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Import2DError("SVG XML must use UTF-8 encoding.") from exc
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml_text, flags=re.IGNORECASE):
        raise Import2DError("SVG DTDs and entity declarations are not supported.")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise Import2DError(f"Could not parse SVG file {path}: {exc}") from exc
    if _svg_local_name(root.tag) != "svg":
        raise Import2DError(f"File {path} does not have an SVG root element.")

    parent_for = {child: parent for parent in root.iter() for child in parent}
    for element in root.iter():
        tag = _svg_local_name(element.tag)
        if tag in _SVG_ACTIVE_TAGS:
            raise Import2DError(f"SVG element <{tag}> is active or external content and is rejected.")
        if tag not in _SVG_TAGS:
            raise Import2DError(f"SVG element <{tag}> is unsupported.")
        actual_tag = str(element.tag).split("}")[-1].split(":")[-1]
        if actual_tag != tag:
            raise Import2DError(f"SVG element <{actual_tag}> has an unsupported case-sensitive name.")
        if tag == "defs" and len(element):
            raise Import2DError("SVG defs with referenced or hidden content are unsupported.")
        if tag == "tspan":
            parent = parent_for.get(element)
            if parent is None or _svg_local_name(parent.tag) != "text":
                raise Import2DError("SVG tspan is supported only as plain text inside a text element.")
            if any(_svg_attribute(element.attrib, field_name) for field_name in ("x", "y", "dx", "dy", "rotate")):
                raise Import2DError("Positioned or rotated SVG tspan text is unsupported.")
        if tag == "text" and len(element):
            for child in element.iter():
                if child is not element and _svg_local_name(child.tag) != "tspan":
                    raise Import2DError("SVG text may contain plain text and tspan elements only.")

        for key, raw_value in element.attrib.items():
            local = _svg_local_name(str(key))
            if local == "viewbox" and str(key) != "viewBox":
                raise Import2DError("SVG attribute names are case-sensitive and unqualified; use viewBox.")
            value = str(raw_value)
            normalized_value = value.strip().casefold()
            if local.startswith("on"):
                raise Import2DError(f"SVG event attribute {key!r} is active content and is rejected.")
            if local in {"href", "src"}:
                raise Import2DError(f"SVG attribute {key!r} can load external content and is rejected.")
            if local == "display" and normalized_value == "none":
                raise Import2DError("Hidden SVG geometry is unsupported because it cannot be imported faithfully.")
            if local == "visibility" and normalized_value in {"hidden", "collapse"}:
                raise Import2DError("Hidden SVG geometry is unsupported because it cannot be imported faithfully.")
            if local == "opacity":
                opacity = normalized_value.removesuffix("%")
                try:
                    is_zero_opacity = float(opacity) == 0.0
                except ValueError:
                    is_zero_opacity = False
                if is_zero_opacity:
                    raise Import2DError("Invisible SVG geometry with opacity zero is unsupported.")
            if re.search(r"url\s*\(|@import|expression\s*\(", value, flags=re.IGNORECASE):
                raise Import2DError(f"SVG attribute {key!r} contains an external or active reference.")
            if local == "style":
                declarations = {
                    part.split(":", 1)[0].strip().lower(): part.split(":", 1)[1].strip().lower()
                    for part in value.split(";")
                    if ":" in part
                }
                if declarations.get("display") == "none" or declarations.get("visibility") in {"hidden", "collapse"}:
                    raise Import2DError("Hidden SVG geometry is unsupported because it cannot be imported faithfully.")
                opacity = declarations.get("opacity", "").removesuffix("%")
                try:
                    is_zero_opacity = float(opacity) == 0.0
                except ValueError:
                    is_zero_opacity = False
                if is_zero_opacity:
                    raise Import2DError("Invisible SVG geometry with opacity zero is unsupported.")
        for key in element.attrib:
            if _svg_local_name(str(key)) in {
                "clip-path", "mask", "filter", "marker-start", "marker-mid", "marker-end"
            }:
                raise Import2DError(f"SVG attribute {key!r} changes rendered geometry and is unsupported.")

    return root


def _svg_metadata_attributes(element: Any) -> dict[str, str]:
    raw = getattr(element, "values", {}).get("attributes", {})
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): str(value) for key, value in raw.items() if isinstance(value, (str, int, float))}


def _svg_matrix_values(matrix: Any) -> tuple[float, float, float, float, float, float]:
    return tuple(float(getattr(matrix, key)) for key in ("a", "b", "c", "d", "e", "f"))  # type: ignore[return-value]


def _svg_transform_point(matrix: Any, point: Sequence[float]) -> tuple[float, float]:
    x, y = float(point[0]), float(point[1])
    a, b, c, d, e, f = _svg_matrix_values(matrix)
    return a * x + c * y + e, b * x + d * y + f


def _svg_point(value: Any) -> tuple[float, float]:
    return _point2((float(value.x), float(value.y)), label="SVG point")


def _svg_attrs_layer(attrs: Mapping[str, str]) -> str | None:
    return (
        _svg_attribute(attrs, "data-layer")
        or _svg_attribute(attrs, "label")
    )


def _svg_add_layer(
    raw_layer: str,
    layer_mapper: Mapping[str, str],
    imported_layers: list[str],
    layer_details: list[dict[str, Any]],
    *,
    source_id: str | None = None,
) -> str:
    layer, source_name = _mapped_layer(raw_layer, layer_mapper)
    if layer not in imported_layers:
        imported_layers.append(layer)
    detail: dict[str, Any] = {"name": layer, "source_name": source_name}
    if source_id:
        detail["source_id"] = source_id
    if detail not in layer_details:
        layer_details.append(detail)
    return layer


def _svg_element_record_metadata(
    *,
    tag: str,
    source_id: str | None,
    source_layer: str,
    groups: Sequence[str],
    attributes: Mapping[str, str],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source_tag": tag,
        "source_id": source_id,
        "source_layer": source_layer,
        "source_group_ids": list(groups),
        "source_element_attributes": dict(attributes),
    }
    if extra:
        result.update(extra)
    return result


def _svg_path_from_element(element: Any, viewport_height: float, unit_scale: float) -> Any:
    from svgelements import Matrix, Path as SVGPath

    path = SVGPath(element)
    path.reify()
    # The parser works in CSS pixels. Convert to requested units, then flip y.
    path.transform = Matrix(f"scale({unit_scale:.15g})")
    path.reify()
    path.transform = Matrix(f"translate(0,{viewport_height:.15g}) scale(1,-1)")
    path.reify()
    return path


def _svg_arc_record_data(segment: Any) -> tuple[tuple[float, float], float, float, float, float, float, float]:
    center = _svg_point(segment.center)
    # svgelements transforms the ellipse's two radius vectors independently.
    # After shear they are not orthogonal: their lengths/rotation alone do not
    # describe the transformed conic. Normalize that affine basis with SVD.
    ux, uy = float(segment.prx.x) - center[0], float(segment.prx.y) - center[1]
    vx, vy = float(segment.pry.x) - center[0], float(segment.pry.y) - center[1]
    rx, ry = math.hypot(ux, uy), math.hypot(vx, vy)
    if min(rx, ry) <= 0 or abs(ux * vy - uy * vx) <= rx * ry * 1e-14:
        raise Import2DError("SVG transform collapses an elliptical curve")
    if abs(ux * vx + uy * vy) > rx * ry * 1e-10:
        import numpy as np
        basis, radii, _ = np.linalg.svd([[ux, vx], [uy, vy]])
        if np.linalg.det(basis) < 0:
            basis[:, 1] *= -1
        if float(basis[0, 0]) * ux + float(basis[1, 0]) * uy < 0:
            basis *= -1
        rx, ry = (float(radius) for radius in radii)
        ex, ey = float(basis[0, 0]), float(basis[1, 0])
    else:
        ex, ey = ux / rx, uy / rx
    rotation = math.degrees(math.atan2(ey, ex))
    dx, dy = float(segment.start.x) - center[0], float(segment.start.y) - center[1]
    start = math.degrees(math.atan2((-ey * dx + ex * dy) / ry, (ex * dx + ey * dy) / rx))
    sweep = math.degrees(float(segment.sweep))
    return center, rx, ry, rotation, start, start + sweep, sweep


def _svg_layer_for_object(
    attrs: Mapping[str, str],
    inherited_layer: str,
    layer_mapper: Mapping[str, str],
    imported_layers: list[str],
    layer_details: list[dict[str, Any]],
    *,
    group: bool,
) -> str:
    raw = _svg_attrs_layer(attrs)
    group_mode = _svg_attribute(attrs, "groupmode")
    source_id = _svg_attribute(attrs, "id")
    if raw is None and group and group_mode == "layer":
        raw = source_id
    if raw is None:
        raw = inherited_layer
    _svg_add_layer(
        raw, layer_mapper, imported_layers, layer_details,
        source_id=source_id if group and (group_mode == "layer" or _svg_attrs_layer(attrs)) else None,
    )
    return raw


def parse_svg(
    path: str | Path,
    units: str = "mm",
    layer_map: Mapping[str, str] | None = None,
) -> Imported2D:
    """Parse static mechanical SVG geometry into exact 2D records.

    Root width and height establish the physical viewport. Unitless root lengths
    follow the SVG/CSS rule of 96 pixels per inch. A viewBox maps its coordinates
    into that viewport. Output coordinates use the requested mm or in unit and
    are converted to a y-up drawing plane.
    """
    source_path = Path(path)
    output_units = _units(units, default="mm") or "mm"
    layer_mapper = _layer_mapper(layer_map)
    try:
        raw = source_path.read_bytes()
    except OSError as exc:
        raise Import2DError(f"Cannot read SVG file {source_path}: {exc}") from exc
    root = _validate_svg(raw, source_path)
    viewport_width, viewport_height, viewbox = _svg_viewport(root, output_units)

    try:
        from svgelements import (
            Arc as SVGArc,
            Circle as SVGCircle,
            Ellipse as SVGEllipse,
            Group as SVGGroup,
            Text as SVGText,
            SVG,
        )
    except ImportError as exc:
        raise Import2DError("SVG import requires the permissive svgelements parser (MIT license).") from exc

    # Normalize the root viewport to CSS pixels using our exact physical-unit
    # conversion. svgelements then handles viewBox and nested affine transforms.
    # Its parser works in CSS pixels; converting the whole result afterward also
    # keeps unitless geometry, explicit physical geometry, and nested viewports
    # in one consistent coordinate system.
    output_mm = _UNIT_MM[output_units]
    css_pixels_per_output_unit = 96.0 * output_mm / 25.4
    css_to_output = 1.0 / css_pixels_per_output_unit
    parser_root = deepcopy(root)
    parser_root.set("width", f"{viewport_width * css_pixels_per_output_unit:.15g}px")
    parser_root.set("height", f"{viewport_height * css_pixels_per_output_unit:.15g}px")
    parser_bytes = ET.tostring(parser_root, encoding="utf-8", xml_declaration=True)
    try:
        parsed = SVG.parse(io.BytesIO(parser_bytes), ppi=96.0, reify=True, on_error="raise")
    except Exception as exc:
        raise Import2DError(f"SVG geometry parser rejected {source_path}: {exc}") from exc

    imported_layers: list[str] = []
    layer_details: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    element_index = 0

    def emit(
        base_name: str,
        kind: str,
        layer: str,
        points: Sequence[Sequence[Any]],
        *,
        radius: float | None = None,
        start_angle: float | None = None,
        end_angle: float | None = None,
        closed: bool = False,
        text: str | None = None,
        height: float | None = None,
        metadata: Mapping[str, Any],
    ) -> None:
        records.append(_record(
            _unique_name(base_name, seen_names), kind, layer, points,
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
            closed=closed,
            text=text,
            height=height,
            metadata=metadata,
        ))

    def walk(
        parent: Any,
        inherited_layer: str,
        group_ids: tuple[str, ...],
    ) -> None:
        nonlocal element_index
        for element in parent:
            tag = _svg_local_name(
                str(getattr(element, "values", {}).get("tag", type(element).__name__))
            )
            if tag in _SVG_NON_RENDERING_TAGS:
                continue
            attrs = _svg_metadata_attributes(element)
            source_id = _svg_attribute(attrs, "id")
            is_group = isinstance(element, SVGGroup)
            active_layer = _svg_layer_for_object(
                attrs, inherited_layer, layer_mapper, imported_layers, layer_details, group=is_group
            )
            current_groups = group_ids
            if is_group:
                if source_id:
                    current_groups = (*group_ids, source_id)
                walk(element, active_layer, current_groups)
                continue

            source_layer = _svg_attrs_layer(attrs) or inherited_layer
            layer = _svg_add_layer(source_layer, layer_mapper, imported_layers, layer_details)
            base_name = source_id or f"{tag}-{element_index}"
            element_index += 1
            base_metadata = _svg_element_record_metadata(
                tag=tag,
                source_id=source_id,
                source_layer=source_layer,
                groups=current_groups,
                attributes=attrs,
            )

            if isinstance(element, SVGText) or tag == "text":
                text_value = str(getattr(element, "text", "") or "")
                if not text_value:
                    continue
                matrix = element.transform
                local_x, local_y = float(element.x), float(element.y)
                px, py = _svg_transform_point(matrix, (local_x, local_y))
                x, y = px * css_to_output, viewport_height - py * css_to_output
                a, b, c, d, e, f = _svg_matrix_values(matrix)
                font_size = float(getattr(element, "font_size", 16.0))
                height = font_size * math.hypot(b, d) * css_to_output
                matrix_with_flip = (
                    css_to_output * a,
                    -css_to_output * b,
                    css_to_output * c,
                    -css_to_output * d,
                    css_to_output * e,
                    viewport_height - css_to_output * f,
                )
                rotation = math.degrees(math.atan2(matrix_with_flip[1], matrix_with_flip[0]))
                metadata = {
                    **base_metadata,
                    "rotation": rotation,
                    "transform": matrix_with_flip,
                    "text_is_similarity_transform": (
                        abs(math.hypot(matrix_with_flip[0], matrix_with_flip[1])
                            - math.hypot(matrix_with_flip[2], matrix_with_flip[3])) < 1e-9
                        and abs(matrix_with_flip[0] * matrix_with_flip[2]
                                + matrix_with_flip[1] * matrix_with_flip[3]) < 1e-9
                    ),
                }
                emit(
                    base_name, "text", layer, ((x, y),), text=text_value,
                    height=height, metadata=metadata,
                )
                continue

            if isinstance(element, (SVGCircle, SVGEllipse)) or tag in {"circle", "ellipse"}:
                shape_path = _svg_path_from_element(element, viewport_height, css_to_output)
                arcs = [segment for segment in shape_path if isinstance(segment, SVGArc)]
                if len(arcs) < 4:
                    raise Import2DError(f"SVG {tag} {source_id or base_name!r} could not be represented as an exact ellipse.")
                center, rx, ry, rotation, _, _, _ = _svg_arc_record_data(arcs[0])
                phi = math.radians(rotation)
                axis_u = (rx * math.cos(phi), rx * math.sin(phi))
                axis_v = (-ry * math.sin(phi), ry * math.cos(phi))
                ellipse_meta = {
                    **base_metadata,
                    "ellipse": {
                        "center": center,
                        "axis_u": axis_u,
                        "axis_v": axis_v,
                        "rx": rx,
                        "ry": ry,
                        "rotation": rotation,
                    },
                }
                tolerance = max(rx, ry, 1.0) * 1e-10
                if tag == "circle" and abs(rx - ry) <= tolerance:
                    emit(base_name, "circle", layer, (center,), radius=(rx + ry) / 2,
                         closed=True, metadata=ellipse_meta)
                else:
                    emit(base_name, "ellipse", layer, (center,), closed=True, metadata=ellipse_meta)
                continue

            if tag not in {"line", "polyline", "polygon", "rect", "path"}:
                raise Import2DError(f"SVG element <{tag}> could not be imported as supported geometry.")

            shape_path = _svg_path_from_element(element, viewport_height, css_to_output)
            segments = list(shape_path)
            if tag in {"polyline", "polygon", "rect"}:
                line_segments = [segment for segment in segments if type(segment).__name__ == "Line"]
                arc_segments = [segment for segment in segments if isinstance(segment, SVGArc)]
                closed = any(type(segment).__name__ == "Close" for segment in segments)
                if tag in {"polyline", "polygon"} or (tag == "rect" and not arc_segments):
                    if line_segments:
                        poly_points = [_svg_point(line_segments[0].start)]
                        poly_points.extend(_svg_point(segment.end) for segment in line_segments)
                        if closed and len(poly_points) > 1 and math.dist(poly_points[0], poly_points[-1]) < 1e-10:
                            poly_points.pop()
                        emit(
                            base_name, "polyline", layer, poly_points,
                            closed=closed or tag == "polygon",
                            metadata={**base_metadata, "source_closed": closed or tag == "polygon"},
                        )
                    continue

            subpath_index = 0
            has_subpath = False
            segment_ids: dict[int, int] = {}
            closed_positions: set[int] = set()
            pending_subpath_positions: list[int] = []
            next_segment_id = 0
            for position, source_segment in enumerate(segments):
                source_type = type(source_segment).__name__
                if source_type == "Move":
                    pending_subpath_positions = []
                    continue
                segment_ids[position] = next_segment_id
                next_segment_id += 1
                pending_subpath_positions.append(position)
                if source_type == "Close":
                    closed_positions.update(pending_subpath_positions)

            for position, segment in enumerate(segments):
                segment_type = type(segment).__name__
                if segment_type == "Move":
                    if has_subpath:
                        subpath_index += 1
                    has_subpath = True
                    continue
                segment_index = segment_ids[position]
                subpath_closed = position in closed_positions
                if segment_type == "Close":
                    start, end = _svg_point(segment.start), _svg_point(segment.end)
                    metadata = {
                        **base_metadata,
                        "source_command": "Z",
                        "source_path_data": attrs.get("d"),
                        "segment_index": segment_index,
                        "subpath_index": subpath_index,
                        "subpath_closed": True,
                    }
                    emit(f"{base_name}.s{segment_index:03d}", "line", layer, (start, end),
                         closed=True, metadata=metadata)
                    continue

                metadata = {
                    **base_metadata,
                    "source_path_data": attrs.get("d"),
                    "segment_index": segment_index,
                    "subpath_index": subpath_index,
                    "subpath_closed": subpath_closed,
                }
                segment_name = f"{base_name}.s{segment_index:03d}"

                if segment_type == "Line":
                    emit(segment_name, "line", layer,
                         (_svg_point(segment.start), _svg_point(segment.end)), metadata=metadata)
                elif segment_type == "CubicBezier":
                    points = (
                        _svg_point(segment.start),
                        _svg_point(segment.control1),
                        _svg_point(segment.control2),
                        _svg_point(segment.end),
                    )
                    emit(
                        segment_name, "cubic_bezier", layer, points,
                        metadata={**metadata, "control_points": points[1:3]},
                    )
                elif segment_type == "QuadraticBezier":
                    points = (
                        _svg_point(segment.start),
                        _svg_point(segment.control),
                        _svg_point(segment.end),
                    )
                    emit(
                        segment_name, "quadratic_bezier", layer, points,
                        metadata={**metadata, "control_point": points[1]},
                    )
                elif isinstance(segment, SVGArc):
                    center, rx, ry, rotation, start, end, sweep = _svg_arc_record_data(segment)
                    start_point, end_point = _svg_point(segment.start), _svg_point(segment.end)
                    if abs(rx - ry) <= max(rx, ry, 1.0) * 1e-10:
                        start_angle = math.degrees(math.atan2(start_point[1] - center[1], start_point[0] - center[0]))
                        end_angle = math.degrees(math.atan2(end_point[1] - center[1], end_point[0] - center[0]))
                        emit(
                            segment_name, "arc", layer, (center,),
                            radius=(rx + ry) / 2,
                            start_angle=start_angle,
                            end_angle=end_angle,
                            metadata={
                                **metadata,
                                "ccw": sweep > 0,
                                "ellipse": {
                                    "center": center, "rx": rx, "ry": ry,
                                    "rotation": rotation, "start_parameter": start,
                                    "end_parameter": end, "sweep": sweep,
                                },
                            },
                        )
                    else:
                        emit(
                            segment_name, "elliptical_arc", layer, (start_point, end_point),
                            start_angle=start, end_angle=end,
                            metadata={
                                **metadata,
                                "ellipse": {
                                    "center": center, "rx": rx, "ry": ry,
                                    "rotation": rotation, "start_parameter": start,
                                    "end_parameter": end, "sweep": sweep,
                                },
                            },
                        )
                else:
                    raise Import2DError(
                        f"SVG path command produced unsupported segment {segment_type} in {source_id or base_name!r}."
                    )

    _svg_add_layer("layer0", layer_mapper, imported_layers, layer_details)
    walk(parsed, "layer0", ())

    source = _source_path(source_path, raw, "svg")
    source.update({
        "units": {
            "output_units": output_units,
            "root_width": _svg_attribute(root.attrib, "width"),
            "root_height": _svg_attribute(root.attrib, "height"),
            "scale_rule": "SVG/CSS px are 1/96 inch; physical root units convert to the requested output unit.",
        },
        "viewport": {"width": viewport_width, "height": viewport_height},
        "viewBox": list(viewbox) if viewbox is not None else None,
        "layers": layer_details,
        "layer_map": dict(layer_mapper),
        "svg_parser": "svgelements",
    })
    return Imported2D(
        units=output_units,
        entities=records,
        layers=imported_layers,
        source=source,
        warnings=[],
    )
