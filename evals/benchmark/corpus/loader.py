"""Strict host-side loader and public task projection for the S5 corpus."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .references import REFERENCE_FACTORIES


CORPUS_DIR = Path(__file__).resolve().parent
TASKS_PATH = CORPUS_DIR / "tasks.json"
PUBLIC_PATH = CORPUS_DIR / "public.json"
ASSETS_DIR = CORPUS_DIR / "assets"

_ID = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CATEGORIES = {
    "part": ("part", 40),
    "edit_chain": ("part", 20),
    "assembly": ("assembly", 10),
    "2d_profile": ("2d", 15),
    "drawing": ("drawing", 5),
    "clarification": ("clarification", 5),
    "infeasible": ("clarification", 5),
}


class CorpusValidationError(ValueError):
    """Raised when corpus data is malformed or unsafe to stage."""


def _fail(where: str, message: str) -> None:
    raise CorpusValidationError(f"{where}: {message}")


def _object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(where, "must be an object")
    if any(not isinstance(key, str) for key in value):
        _fail(where, "object keys must be strings")
    return value


def _keys(
    value: Mapping[str, Any],
    required: set[str],
    optional: set[str],
    where: str,
) -> None:
    actual = set(value)
    missing = required - actual
    extra = actual - required - optional
    if missing:
        _fail(where, f"missing keys: {', '.join(sorted(missing))}")
    if extra:
        _fail(where, f"unknown keys: {', '.join(sorted(extra))}")


def _string(value: Any, where: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(where, "must be a nonempty string")
    if identifier and not _ID.fullmatch(value):
        _fail(where, "must be a lowercase snake_case identifier")
    return value


def _finite(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(where, "must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        _fail(where, "must be a finite number")
    if not math.isfinite(result):
        _fail(where, "must be a finite number")
    return result


def _vector(value: Any, where: str, *, unit: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        _fail(where, "must be a three-number list")
    result = [_finite(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if unit:
        length = math.sqrt(sum(item * item for item in result))
        if not math.isclose(length, 1.0, rel_tol=0.0, abs_tol=1e-6):
            _fail(where, "axis vector must have unit length")
    return result


def _point2(value: Any, where: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        _fail(where, "must be a two-number X/Y point")
    return [_finite(item, f"{where}[{index}]") for index, item in enumerate(value)]


def _range(value: Any, where: str, *, positive: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        _fail(where, "must be a two-number inclusive range")
    low = _finite(value[0], f"{where}[0]")
    high = _finite(value[1], f"{where}[1]")
    if low > high:
        _fail(where, "range lower bound must not exceed upper bound")
    if positive and low <= 0:
        _fail(where, "range must be positive")
    return [low, high]


def _ranges3(value: Any, where: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 3:
        _fail(where, "must contain three inclusive ranges")
    return [_range(item, f"{where}[{index}]") for index, item in enumerate(value)]


def _nonempty_strings(value: Any, where: str, *, unique: bool = True) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(where, "must be a nonempty list")
    items = [_string(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if unique and len(items) != len(set(items)):
        _fail(where, "contains duplicates")
    return items


def _finite_tree(value: Any, where: str) -> None:
    """Reject non-JSON values and non-finite numbers in caller-built mappings."""
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        _finite(value, where)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _finite_tree(item, f"{where}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(where, "object keys must be strings")
            _finite_tree(item, f"{where}.{key}")
        return
    _fail(where, f"contains unsupported value type {type(value).__name__}")


def _check_asset_path(path_text: str, corpus_dir: Path, where: str) -> Path:
    if "\\" in path_text or ":" in path_text or "\x00" in path_text:
        _fail(where, "path must use safe relative POSIX syntax")
    relative = PurePosixPath(path_text)
    if relative.is_absolute() or not relative.parts or relative.parts[0] != "assets":
        _fail(where, "path must be relative to the corpus assets directory")
    if any(part in ("", ".", "..") for part in relative.parts):
        _fail(where, "path may not contain empty, dot, or parent components")
    try:
        corpus_root = corpus_dir.resolve(strict=True)
        assets_root = (corpus_root / "assets").resolve(strict=True)
        candidate = corpus_root.joinpath(*relative.parts).resolve(strict=True)
    except (OSError, ValueError) as exc:
        _fail(where, f"does not resolve to an existing corpus asset: {exc}")
    try:
        assets_root.relative_to(corpus_root)
    except ValueError:
        _fail(where, "corpus assets directory resolves outside the corpus")
    try:
        candidate.relative_to(assets_root)
    except ValueError:
        _fail(where, "resolved path escapes the corpus assets directory")
    if not candidate.is_file():
        _fail(where, "asset path is not a regular file")
    return candidate


def _validate_asset(asset: Any, index: int, corpus_dir: Path, where: str) -> dict[str, str]:
    asset = _object(asset, where)
    _keys(asset, {"id", "path", "format", "sha256"}, set(), where)
    asset_id = _string(asset["id"], f"{where}.id", identifier=True)
    path_text = _string(asset["path"], f"{where}.path")
    fmt = _string(asset["format"], f"{where}.format")
    if fmt not in {"dxf", "step", "stl", "svg"}:
        _fail(f"{where}.format", "must be dxf, step, stl, or svg")
    if PurePosixPath(path_text).suffix.lower() != f".{fmt}":
        _fail(f"{where}.format", "must match the asset path extension")
    digest = _string(asset["sha256"], f"{where}.sha256")
    if not _SHA256.fullmatch(digest):
        _fail(f"{where}.sha256", "must be a lowercase SHA-256 hex digest")
    resolved = _check_asset_path(path_text, corpus_dir, f"{where}.path")
    actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if digest != actual:
        _fail(f"{where}.sha256", "does not match the declared asset bytes")
    return {"id": asset_id, "path": path_text, "format": fmt, "sha256": digest}


def _check_typed(check: Any, where: str, asset_ids: set[str]) -> dict[str, Any]:
    check = _object(check, where)
    if "id" not in check or "type" not in check:
        _fail(where, "requires id and type")
    _string(check["id"], f"{where}.id", identifier=True)
    kind = _string(check["type"], f"{where}.type")
    schemas: dict[str, tuple[set[str], set[str]]] = {
        "bbox_mm": ({"ranges"}, set()),
        "volume_mm3": ({"range"}, set()),
        "solids": ({"equals"}, set()),
        "point_material": ({"point_mm", "equals"}, {"additional_points_mm", "xy_translation_invariant"}),
        "cylindrical_bores": ({"bores"}, {"xy_translation_invariant"}),
        "clearance_mm": ({"asset_id", "range"}, set()),
        "asset_match": ({"asset_id", "symmetric_difference_volume_mm3", "surface_distance_mm"}, set()),
        "dxf_profile": (
            set(),
            {
                "cut_layers",
                "contour_count",
                "closed_contours",
                "circles",
                "circle_patterns",
                "regular_polygon",
                "extents_mm",
                "segments",
            },
        ),
        "drawing_dimensions": ({"dimensions", "required_views"}, set()),
        "clarification": (set(), {"missing", "conflicts"}),
        # Extensions are documented in COVERAGE.md. They are bounded until the
        # independent artifact oracle implements and validates their semantics.
        "slot_pattern": ({"slots"}, set()),
        "component_layout": ({"parts", "tolerance_mm"}, set()),
        "fastener_access": (
            {
                "axis_mm",
                "entry_point_mm",
                "seat_point_mm",
                "shank_diameter_mm",
                "head_diameter_mm",
                "head_height_mm",
                "minimum_clearance_mm",
            },
            set(),
        ),
        "rotational_pattern": (
            {"axis_mm", "center_mm", "count", "pitch_radius_mm", "feature_centers_mm", "tolerance_mm"},
            {"feature_tip_radius_mm", "xy_translation_invariant"},
        ),
        "linear_pattern": (
            {"axis_mm", "feature_centers_mm", "feature_width_mm", "feature_kind", "tolerance_mm"},
            {"axis_options_mm", "scan_point_mm", "spacing_tolerance_mm"},
        ),
    }
    if kind not in schemas:
        _fail(f"{where}.type", f"unsupported check type {kind!r}")
    required, optional = schemas[kind]
    _keys(check, {"id", "type", *required}, optional, where)
    payload = {key: value for key, value in check.items() if key not in {"id", "type"}}
    if "xy_translation_invariant" in payload and not isinstance(payload["xy_translation_invariant"], bool):
        _fail(f"{where}.xy_translation_invariant", "must be a boolean")

    if kind == "bbox_mm":
        _ranges3(payload["ranges"], f"{where}.ranges")
    elif kind == "volume_mm3":
        _range(payload["range"], f"{where}.range", positive=True)
    elif kind == "solids":
        value = payload["equals"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            _fail(f"{where}.equals", "must be a positive integer")
    elif kind == "point_material":
        _vector(payload["point_mm"], f"{where}.point_mm")
        if "additional_points_mm" in payload:
            points = payload["additional_points_mm"]
            if not isinstance(points, list) or not points:
                _fail(f"{where}.additional_points_mm", "must be a nonempty list")
            for index, point in enumerate(points):
                _vector(point, f"{where}.additional_points_mm[{index}]")
        if not isinstance(payload["equals"], str) or payload["equals"] not in {"solid", "void"}:
            _fail(f"{where}.equals", "must be solid or void")
    elif kind == "cylindrical_bores":
        bores = payload["bores"]
        if not isinstance(bores, list) or not bores:
            _fail(f"{where}.bores", "must be a nonempty list")
        for index, bore in enumerate(bores):
            bore_where = f"{where}.bores[{index}]"
            bore = _object(bore, bore_where)
            _keys(bore, {"axis_mm", "center_mm", "diameter_mm", "depth_mm", "through"}, {"center_tolerance_mm"}, bore_where)
            if "center_tolerance_mm" in bore and _finite(bore["center_tolerance_mm"], f"{bore_where}.center_tolerance_mm") < 0:
                _fail(f"{bore_where}.center_tolerance_mm", "must be nonnegative")
            _vector(bore["axis_mm"], f"{bore_where}.axis_mm", unit=True)
            _vector(bore["center_mm"], f"{bore_where}.center_mm")
            _range(bore["diameter_mm"], f"{bore_where}.diameter_mm", positive=True)
            _range(bore["depth_mm"], f"{bore_where}.depth_mm", positive=True)
            if not isinstance(bore["through"], bool):
                _fail(f"{bore_where}.through", "must be a boolean")
    elif kind == "clearance_mm":
        asset_id = _string(payload["asset_id"], f"{where}.asset_id", identifier=True)
        if asset_id not in asset_ids:
            _fail(f"{where}.asset_id", "must name a declared public input asset")
        _range(payload["range"], f"{where}.range", positive=True)
    elif kind == "asset_match":
        asset_id = _string(payload["asset_id"], f"{where}.asset_id", identifier=True)
        if asset_id not in asset_ids:
            _fail(f"{where}.asset_id", "must name a declared public input asset")
        _range(payload["symmetric_difference_volume_mm3"], f"{where}.symmetric_difference_volume_mm3")
        _range(payload["surface_distance_mm"], f"{where}.surface_distance_mm")
    elif kind == "dxf_profile":
        if not payload:
            _fail(where, "dxf_profile needs at least one expected profile feature")
        if "cut_layers" in payload:
            _nonempty_strings(payload["cut_layers"], f"{where}.cut_layers")
        if "contour_count" in payload:
            count = payload["contour_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                _fail(f"{where}.contour_count", "must be a positive integer")
        if "closed_contours" in payload and not isinstance(payload["closed_contours"], bool):
            _fail(f"{where}.closed_contours", "must be a boolean")
        if "circles" in payload:
            circles = payload["circles"]
            if not isinstance(circles, list):
                _fail(f"{where}.circles", "must be a list")
            for index, circle in enumerate(circles):
                circle_where = f"{where}.circles[{index}]"
                circle = _object(circle, circle_where)
                _keys(circle, {"center_mm", "diameter_mm"}, {"center_tolerance_mm"}, circle_where)
                _point2(circle["center_mm"], f"{circle_where}.center_mm")
                _range(circle["diameter_mm"], f"{circle_where}.diameter_mm", positive=True)
                if "center_tolerance_mm" in circle and _finite(
                    circle["center_tolerance_mm"], f"{circle_where}.center_tolerance_mm"
                ) < 0:
                    _fail(f"{circle_where}.center_tolerance_mm", "must be nonnegative")
        if "circle_patterns" in payload:
            patterns = payload["circle_patterns"]
            if not isinstance(patterns, list) or not patterns:
                _fail(f"{where}.circle_patterns", "must be a nonempty list")
            for index, pattern in enumerate(patterns):
                pattern_where = f"{where}.circle_patterns[{index}]"
                pattern = _object(pattern, pattern_where)
                _keys(
                    pattern,
                    {"center_mm", "count", "pitch_radius_mm", "diameter_mm", "center_tolerance_mm"},
                    {"equally_spaced"},
                    pattern_where,
                )
                _point2(pattern["center_mm"], f"{pattern_where}.center_mm")
                count = pattern["count"]
                if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                    _fail(f"{pattern_where}.count", "must be a positive integer")
                _range(pattern["pitch_radius_mm"], f"{pattern_where}.pitch_radius_mm", positive=True)
                _range(pattern["diameter_mm"], f"{pattern_where}.diameter_mm", positive=True)
                if _finite(pattern["center_tolerance_mm"], f"{pattern_where}.center_tolerance_mm") < 0:
                    _fail(f"{pattern_where}.center_tolerance_mm", "must be nonnegative")
                if "equally_spaced" in pattern and not isinstance(pattern["equally_spaced"], bool):
                    _fail(f"{pattern_where}.equally_spaced", "must be a boolean")
        if "regular_polygon" in payload:
            polygon = _object(payload["regular_polygon"], f"{where}.regular_polygon")
            polygon_where = f"{where}.regular_polygon"
            _keys(
                polygon,
                {"vertex_count", "center_mm", "across_flats_mm", "tolerance_mm"},
                set(),
                polygon_where,
            )
            count = polygon["vertex_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 4 or count % 2:
                _fail(f"{polygon_where}.vertex_count", "must be an even integer of at least four")
            _point2(polygon["center_mm"], f"{polygon_where}.center_mm")
            _range(polygon["across_flats_mm"], f"{polygon_where}.across_flats_mm", positive=True)
            if _finite(polygon["tolerance_mm"], f"{polygon_where}.tolerance_mm") < 0:
                _fail(f"{polygon_where}.tolerance_mm", "must be nonnegative")
        if "extents_mm" in payload:
            extents = payload["extents_mm"]
            if not isinstance(extents, list) or len(extents) != 2:
                _fail(f"{where}.extents_mm", "must contain X and Y ranges")
            for index, extent in enumerate(extents):
                _range(extent, f"{where}.extents_mm[{index}]")
        if "segments" in payload:
            segments = payload["segments"]
            if not isinstance(segments, list) or not segments:
                _fail(f"{where}.segments", "must be a nonempty list")
            for index, segment in enumerate(segments):
                seg_where = f"{where}.segments[{index}]"
                segment = _object(segment, seg_where)
                _keys(segment, {"start_mm", "end_mm", "tolerance_mm"}, set(), seg_where)
                _point2(segment["start_mm"], f"{seg_where}.start_mm")
                _point2(segment["end_mm"], f"{seg_where}.end_mm")
                if _finite(segment["tolerance_mm"], f"{seg_where}.tolerance_mm") < 0:
                    _fail(f"{seg_where}.tolerance_mm", "must be nonnegative")
    elif kind == "drawing_dimensions":
        dimensions = payload["dimensions"]
        if not isinstance(dimensions, list) or not dimensions:
            _fail(f"{where}.dimensions", "must be a nonempty list")
        dimension_ids: set[str] = set()
        for index, dimension in enumerate(dimensions):
            dim_where = f"{where}.dimensions[{index}]"
            dimension = _object(dimension, dim_where)
            _keys(dimension, {"id", "value_mm", "tolerance_mm"}, set(), dim_where)
            dim_id = _string(dimension["id"], f"{dim_where}.id", identifier=True)
            if dim_id in dimension_ids:
                _fail(dim_where, f"duplicate dimension id {dim_id!r}")
            dimension_ids.add(dim_id)
            _finite(dimension["value_mm"], f"{dim_where}.value_mm")
            if _finite(dimension["tolerance_mm"], f"{dim_where}.tolerance_mm") < 0:
                _fail(f"{dim_where}.tolerance_mm", "must be nonnegative")
        views = _nonempty_strings(payload["required_views"], f"{where}.required_views")
        allowed_views = {"front", "back", "left", "right", "top", "bottom", "iso", "section"}
        if any(view not in allowed_views for view in views):
            _fail(f"{where}.required_views", "contains an unsupported view name")
    elif kind == "clarification":
        if not payload:
            _fail(where, "clarification needs missing or conflicting requirements")
        for field in ("missing", "conflicts"):
            if field in payload:
                _nonempty_strings(payload[field], f"{where}.{field}")
        if not any(payload.get(field) for field in ("missing", "conflicts")):
            _fail(where, "clarification must identify at least one missing or conflicting item")
    elif kind == "slot_pattern":
        slots = payload["slots"]
        if not isinstance(slots, list) or not slots:
            _fail(f"{where}.slots", "must be a nonempty list")
        for index, slot in enumerate(slots):
            slot_where = f"{where}.slots[{index}]"
            slot = _object(slot, slot_where)
            _keys(slot, {"center_mm", "length_mm", "width_mm", "through_axis_mm", "through"}, set(), slot_where)
            _vector(slot["center_mm"], f"{slot_where}.center_mm")
            _range(slot["length_mm"], f"{slot_where}.length_mm", positive=True)
            _range(slot["width_mm"], f"{slot_where}.width_mm", positive=True)
            _vector(slot["through_axis_mm"], f"{slot_where}.through_axis_mm", unit=True)
            if not isinstance(slot["through"], bool):
                _fail(f"{slot_where}.through", "must be a boolean")
    elif kind == "component_layout":
        parts = payload["parts"]
        if not isinstance(parts, list) or len(parts) < 2:
            _fail(f"{where}.parts", "must list at least two component bounding boxes")
        for index, part in enumerate(parts):
            part_where = f"{where}.parts[{index}]"
            part = _object(part, part_where)
            _keys(part, {"ranges_mm"}, set(), part_where)
            _ranges3(part["ranges_mm"], f"{part_where}.ranges_mm")
        if _finite(payload["tolerance_mm"], f"{where}.tolerance_mm") < 0:
            _fail(f"{where}.tolerance_mm", "must be nonnegative")
    elif kind == "fastener_access":
        _vector(payload["axis_mm"], f"{where}.axis_mm", unit=True)
        _vector(payload["entry_point_mm"], f"{where}.entry_point_mm")
        _vector(payload["seat_point_mm"], f"{where}.seat_point_mm")
        for field in ("shank_diameter_mm", "head_diameter_mm", "head_height_mm"):
            _range(payload[field], f"{where}.{field}", positive=True)
        if _finite(payload["minimum_clearance_mm"], f"{where}.minimum_clearance_mm") < 0:
            _fail(f"{where}.minimum_clearance_mm", "must be nonnegative")
    elif kind == "rotational_pattern":
        _vector(payload["axis_mm"], f"{where}.axis_mm", unit=True)
        _vector(payload["center_mm"], f"{where}.center_mm")
        count = payload["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 2:
            _fail(f"{where}.count", "must be an integer of at least two")
        _range(payload["pitch_radius_mm"], f"{where}.pitch_radius_mm", positive=True)
        centers = payload["feature_centers_mm"]
        if not isinstance(centers, list) or len(centers) != count:
            _fail(f"{where}.feature_centers_mm", "must contain one center per repeated feature")
        for index, center in enumerate(centers):
            _vector(center, f"{where}.feature_centers_mm[{index}]")
        if "feature_tip_radius_mm" in payload:
            _range(payload["feature_tip_radius_mm"], f"{where}.feature_tip_radius_mm", positive=True)
        if _finite(payload["tolerance_mm"], f"{where}.tolerance_mm") < 0:
            _fail(f"{where}.tolerance_mm", "must be nonnegative")
    elif kind == "linear_pattern":
        _vector(payload["axis_mm"], f"{where}.axis_mm", unit=True)
        centers = payload["feature_centers_mm"]
        if not isinstance(centers, list) or len(centers) < 2:
            _fail(f"{where}.feature_centers_mm", "must contain at least two feature centers")
        for index, center in enumerate(centers):
            _vector(center, f"{where}.feature_centers_mm[{index}]")
        if "axis_options_mm" in payload:
            axes = payload["axis_options_mm"]
            if not isinstance(axes, list) or len(axes) < 2:
                _fail(f"{where}.axis_options_mm", "must contain at least two candidate axes")
            for index, axis in enumerate(axes):
                _vector(axis, f"{where}.axis_options_mm[{index}]", unit=True)
            if "scan_point_mm" not in payload:
                _fail(f"{where}.scan_point_mm", "is required when candidate axes are used")
        if "scan_point_mm" in payload:
            _vector(payload["scan_point_mm"], f"{where}.scan_point_mm")
        _range(payload["feature_width_mm"], f"{where}.feature_width_mm", positive=True)
        if not isinstance(payload["feature_kind"], str) or payload["feature_kind"] not in {"solid", "void"}:
            _fail(f"{where}.feature_kind", "must be solid or void")
        if _finite(payload["tolerance_mm"], f"{where}.tolerance_mm") < 0:
            _fail(f"{where}.tolerance_mm", "must be nonnegative")
        if "spacing_tolerance_mm" in payload and _finite(
            payload["spacing_tolerance_mm"], f"{where}.spacing_tolerance_mm"
        ) < 0:
            _fail(f"{where}.spacing_tolerance_mm", "must be nonnegative")

    _finite_tree(check, where)
    return dict(check)


def _factory_parameter_names(factory_name: str) -> tuple[set[str], set[str]]:
    try:
        factory = REFERENCE_FACTORIES[factory_name]
    except KeyError:
        _fail("reference.factory", f"unknown factory {factory_name!r}")
    signature = inspect.signature(factory)
    required: set[str] = set()
    optional: set[str] = set()
    for name, parameter in signature.parameters.items():
        if parameter.kind not in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            _fail("reference.factory", "factory has an unsupported signature")
        (required if parameter.default is inspect.Parameter.empty else optional).add(name)
    return required, optional


def _validate_factory_values(factory_name: str, params: Mapping[str, Any], where: str) -> None:
    pair_lists = {
        "bolt_centers_mm": True,
        "hole_centers_mm": False,
        "slot_centers_mm": True,
        "wall_hole_centers_xz_mm": True,
        "floor_hole_centers_mm": True,
    }
    nested = {"components", "contours", "circles", "views", "dimensions", "sheet_mm"}
    counts = {"fin_count", "lobe_count", "spoke_count", "rib_count"}
    for name, value in params.items():
        value_where = f"{where}.{name}"
        if name in pair_lists:
            if not isinstance(value, list) or (pair_lists[name] and not value):
                _fail(value_where, "must be a list of coordinate pairs")
            for index, point_value in enumerate(value):
                _point2(point_value, f"{value_where}[{index}]")
        elif name == "counterbore_center_mm":
            _point2(value, value_where)
        elif name in counts:
            if isinstance(value, bool) or not isinstance(value, int) or value < (2 if name in {"lobe_count", "spoke_count"} else 1):
                _fail(value_where, "must be a valid positive feature count")
        elif name in nested:
            continue
        elif name.endswith("_mm"):
            if _finite(value, value_where) <= 0:
                _fail(value_where, "must be positive")
        else:
            _fail(value_where, "unsupported reference parameter")


def _validate_reference(reference: Any, task: Mapping[str, Any], where: str) -> dict[str, Any]:
    reference = _object(reference, where)
    _keys(reference, {"factory", "stages"}, set(), where)
    factory_name = _string(reference["factory"], f"{where}.factory")
    if factory_name not in REFERENCE_FACTORIES:
        _fail(f"{where}.factory", f"unknown allowlisted factory {factory_name!r}")
    stage_params = _object(reference["stages"], f"{where}.stages")
    artifact_stage_ids = {
        stage["id"] for stage in task["stages"] if stage["expected_outcome"] == "artifact"
    }
    if set(stage_params) != artifact_stage_ids:
        _fail(where, "reference stage IDs must exactly match artifact stages")
    required, optional = _factory_parameter_names(factory_name)
    for stage_id, params in stage_params.items():
        param_where = f"{where}.stages.{stage_id}"
        params = _object(params, param_where)
        _keys(params, required, optional, param_where)
        _finite_tree(params, param_where)
        _validate_factory_values(factory_name, params, param_where)
        if factory_name in {"mounting_plate", "counterbore_flange"}:
            has_diameter = "counterbore_diameter_mm" in params
            has_depth = "counterbore_depth_mm" in params
            if has_diameter != has_depth:
                _fail(param_where, "counterbore diameter and depth must be declared together")
        if factory_name == "sleeve_bushing":
            has_diameter = "counterbore_diameter_mm" in params
            has_depth = "counterbore_depth_mm" in params
            if has_diameter != has_depth:
                _fail(param_where, "counterbore diameter and depth must be declared together")
        if factory_name == "u_channel":
            has_diameter = "floor_hole_diameter_mm" in params
            has_centers = "floor_hole_centers_mm" in params
            if has_diameter != has_centers:
                _fail(param_where, "floor-hole diameter and centers must be declared together")
        if factory_name == "assembly_primitives":
            components = params["components"]
            if not isinstance(components, list) or len(components) < 2:
                _fail(f"{param_where}.components", "must contain at least two primitives")
            names: set[str] = set()
            for index, component in enumerate(components):
                component_where = f"{param_where}.components[{index}]"
                component = _object(component, component_where)
                _keys(component, {"name", "shape", "center_mm", "size_mm"}, set(), component_where)
                name = _string(component["name"], f"{component_where}.name", identifier=True)
                if name in names:
                    _fail(component_where, f"duplicate component name {name!r}")
                names.add(name)
                if not isinstance(component["shape"], str) or component["shape"] not in {"box", "cylinder"}:
                    _fail(f"{component_where}.shape", "must be box or cylinder")
                _vector(component["center_mm"], f"{component_where}.center_mm")
                size = component["size_mm"]
                expected_len = 3 if component["shape"] == "box" else 2
                if not isinstance(size, list) or len(size) != expected_len:
                    _fail(f"{component_where}.size_mm", f"must contain {expected_len} dimensions")
                for dim_index, dim in enumerate(size):
                    if _finite(dim, f"{component_where}.size_mm[{dim_index}]") <= 0:
                        _fail(f"{component_where}.size_mm[{dim_index}]", "must be positive")
        if factory_name == "dxf_profile":
            contours = params["contours"]
            if not isinstance(contours, list):
                _fail(f"{param_where}.contours", "must be a list")
            for ci, contour in enumerate(contours):
                if not isinstance(contour, list) or len(contour) < 3:
                    _fail(f"{param_where}.contours[{ci}]", "must contain at least three points")
                for pi, point in enumerate(contour):
                    if not isinstance(point, list) or len(point) != 2:
                        _fail(f"{param_where}.contours[{ci}][{pi}]", "must be an X/Y pair")
                    for axis, coordinate in enumerate(point):
                        _finite(coordinate, f"{param_where}.contours[{ci}][{pi}][{axis}]")
            circles = params["circles"]
            if not isinstance(circles, list):
                _fail(f"{param_where}.circles", "must be a list")
            for ci, circle in enumerate(circles):
                circle_where = f"{param_where}.circles[{ci}]"
                circle = _object(circle, circle_where)
                _keys(circle, {"center_mm", "diameter_mm"}, set(), circle_where)
                center = circle["center_mm"]
                if not isinstance(center, list) or len(center) != 2:
                    _fail(f"{circle_where}.center_mm", "must be an X/Y pair")
                for axis, coordinate in enumerate(center):
                    _finite(coordinate, f"{circle_where}.center_mm[{axis}]")
                if _finite(circle["diameter_mm"], f"{circle_where}.diameter_mm") <= 0:
                    _fail(f"{circle_where}.diameter_mm", "must be positive")
        if factory_name == "drawing_sheet":
            _nonempty_strings(params["views"], f"{param_where}.views")
            sheet = params["sheet_mm"]
            if not isinstance(sheet, list) or len(sheet) != 2:
                _fail(f"{param_where}.sheet_mm", "must contain width and height")
            if any(_finite(dim, f"{param_where}.sheet_mm[{index}]") <= 0 for index, dim in enumerate(sheet)):
                _fail(f"{param_where}.sheet_mm", "sheet dimensions must be positive")
            dimensions = params["dimensions"]
            if not isinstance(dimensions, list) or not dimensions:
                _fail(f"{param_where}.dimensions", "must be nonempty")
            for index, dimension in enumerate(dimensions):
                dim_where = f"{param_where}.dimensions[{index}]"
                dimension = _object(dimension, dim_where)
                _keys(dimension, {"id", "value_mm"}, set(), dim_where)
                _string(dimension["id"], f"{dim_where}.id", identifier=True)
                _finite(dimension["value_mm"], f"{dim_where}.value_mm")
    return dict(reference)


def validate_corpus(data: Any, *, corpus_dir: Path | None = None, enforce_distribution: bool = True) -> dict[str, Any]:
    """Validate parsed JSON data and return a normalized copy.

    This performs schema and path checks only. It never imports or executes CAD
    source from a task. Factory references are resolved through a fixed allowlist.
    """
    corpus_dir = Path(corpus_dir) if corpus_dir is not None else CORPUS_DIR
    _finite_tree(data, "corpus")
    data = _object(data, "corpus")
    _keys(data, {"schema_version", "tasks"}, set(), "corpus")
    if data["schema_version"] != 1 or isinstance(data["schema_version"], bool):
        _fail("corpus.schema_version", "must equal integer 1")
    tasks = data["tasks"]
    if not isinstance(tasks, list) or not tasks:
        _fail("corpus.tasks", "must be a nonempty list")

    task_ids: set[str] = set()
    requests: set[str] = set()
    families: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    validated_tasks: list[dict[str, Any]] = []
    for ti, task_value in enumerate(tasks):
        where = f"corpus.tasks[{ti}]"
        task = _object(task_value, where)
        _keys(
            task,
            {"id", "title", "category", "difficulty", "kind", "tags", "family", "stages"},
            {"reference"},
            where,
        )
        task_id = _string(task["id"], f"{where}.id", identifier=True)
        if task_id in task_ids:
            _fail(f"{where}.id", f"duplicate task ID {task_id!r}")
        task_ids.add(task_id)
        _string(task["title"], f"{where}.title")
        category = _string(task["category"], f"{where}.category")
        if category not in _CATEGORIES:
            _fail(f"{where}.category", f"unsupported category {category!r}")
        expected_kind, _ = _CATEGORIES[category]
        if task["kind"] != expected_kind:
            _fail(f"{where}.kind", f"category {category!r} requires kind {expected_kind!r}")
        difficulty = _string(task["difficulty"], f"{where}.difficulty")
        if difficulty not in {"easy", "medium", "hard"}:
            _fail(f"{where}.difficulty", "must be easy, medium, or hard")
        tags = task["tags"]
        if not isinstance(tags, list):
            _fail(f"{where}.tags", "must be a list")
        tag_values = [_string(tag, f"{where}.tags[{index}]") for index, tag in enumerate(tags)]
        if len(tag_values) != len(set(tag_values)):
            _fail(f"{where}.tags", "contains duplicate tags")
        family = _string(task["family"], f"{where}.family", identifier=True)
        families[family] += 1
        category_counts[category] += 1

        stages_value = task["stages"]
        if not isinstance(stages_value, list) or not stages_value:
            _fail(f"{where}.stages", "must be a nonempty list")
        if category == "edit_chain" and len(stages_value) not in {2, 3}:
            _fail(f"{where}.stages", "edit chains must have two or three stages")
        if category != "edit_chain" and len(stages_value) != 1:
            _fail(f"{where}.stages", "non-edit tasks must have exactly one stage")

        validated_stages: list[dict[str, Any]] = []
        stage_ids: set[str] = set()
        prior_checks: dict[str, dict[str, Any]] | None = None
        for si, stage_value in enumerate(stages_value):
            stage_where = f"{where}.stages[{si}]"
            stage = _object(stage_value, stage_where)
            _keys(stage, {"id", "request", "expected_outcome", "input_assets", "oracle"}, set(), stage_where)
            stage_id = _string(stage["id"], f"{stage_where}.id", identifier=True)
            if stage_id in stage_ids:
                _fail(f"{stage_where}.id", f"duplicate stage ID {stage_id!r}")
            stage_ids.add(stage_id)
            request = _string(stage["request"], f"{stage_where}.request")
            if request in requests:
                _fail(f"{stage_where}.request", "exact request text is duplicated")
            requests.add(request)
            outcome = stage["expected_outcome"]
            if not isinstance(outcome, str) or outcome not in {"artifact", "clarification", "infeasible"}:
                _fail(f"{stage_where}.expected_outcome", "must be artifact, clarification, or infeasible")

            assets_value = stage["input_assets"]
            if not isinstance(assets_value, list):
                _fail(f"{stage_where}.input_assets", "must be a list")
            assets: list[dict[str, str]] = []
            asset_ids: set[str] = set()
            for ai, asset in enumerate(assets_value):
                validated_asset = _validate_asset(asset, ai, corpus_dir, f"{stage_where}.input_assets[{ai}]")
                if validated_asset["id"] in asset_ids:
                    _fail(f"{stage_where}.input_assets", f"duplicate asset ID {validated_asset['id']!r}")
                asset_ids.add(validated_asset["id"])
                assets.append(validated_asset)

            oracle = _object(stage["oracle"], f"{stage_where}.oracle")
            _keys(oracle, {"coverage", "checks", "preserve_check_ids"}, set(), f"{stage_where}.oracle")
            if not isinstance(oracle["coverage"], str) or oracle["coverage"] not in {"complete", "bounded"}:
                _fail(f"{stage_where}.oracle.coverage", "must be complete or bounded")
            checks_value = oracle["checks"]
            if not isinstance(checks_value, list) or not checks_value:
                _fail(f"{stage_where}.oracle.checks", "must be nonempty")
            checks: list[dict[str, Any]] = []
            check_map: dict[str, dict[str, Any]] = {}
            for ci, check_value in enumerate(checks_value):
                validated_check = _check_typed(check_value, f"{stage_where}.oracle.checks[{ci}]", asset_ids)
                if validated_check["id"] in check_map:
                    _fail(f"{stage_where}.oracle.checks", f"duplicate check ID {validated_check['id']!r}")
                checks.append(validated_check)
                check_map[validated_check["id"]] = validated_check
            extension_types = {"asset_match", "slot_pattern", "component_layout", "fastener_access", "rotational_pattern", "linear_pattern"}
            if any(check["type"] in extension_types for check in checks) and oracle["coverage"] != "bounded":
                _fail(f"{stage_where}.oracle.coverage", "extension evidence must remain bounded until an independent evaluator is implemented")

            preserve_value = oracle["preserve_check_ids"]
            if not isinstance(preserve_value, list):
                _fail(f"{stage_where}.oracle.preserve_check_ids", "must be a list")
            preserve_ids = [_string(cid, f"{stage_where}.oracle.preserve_check_ids[{i}]", identifier=True) for i, cid in enumerate(preserve_value)]
            if len(preserve_ids) != len(set(preserve_ids)):
                _fail(f"{stage_where}.oracle.preserve_check_ids", "contains duplicates")
            if si == 0 and preserve_ids:
                _fail(f"{stage_where}.oracle.preserve_check_ids", "the first stage cannot preserve prior checks")
            if si > 0:
                if category == "edit_chain" and not preserve_ids:
                    _fail(f"{stage_where}.oracle.preserve_check_ids", "each edit stage must retain at least one invariant")
                if not set(preserve_ids) <= set(check_map):
                    unknown = sorted(set(preserve_ids) - set(check_map))
                    _fail(f"{stage_where}.oracle.preserve_check_ids", f"IDs are not current-stage checks: {unknown}")
                assert prior_checks is not None
                if not set(preserve_ids) <= set(prior_checks):
                    unknown = sorted(set(preserve_ids) - set(prior_checks))
                    _fail(f"{stage_where}.oracle.preserve_check_ids", f"IDs are not prior-stage checks: {unknown}")
                for check_id in preserve_ids:
                    if check_map[check_id] != prior_checks[check_id]:
                        _fail(f"{stage_where}.oracle.preserve_check_ids", f"retained check {check_id!r} changed definition")
                shared = set(check_map) & set(prior_checks)
                if shared != set(preserve_ids):
                    _fail(
                        f"{stage_where}.oracle.preserve_check_ids",
                        f"all checks carried across stages must be declared; shared={sorted(shared)}, declared={sorted(preserve_ids)}",
                    )
                if not any(token in request.casefold() for token in ("retain", "keep", "preserve")):
                    _fail(f"{stage_where}.request", "edit request must explicitly say what to retain")

            if outcome == "artifact":
                if category in {"clarification", "infeasible"}:
                    _fail(stage_where, "clarification and infeasible categories cannot have artifact outcomes")
                if category in {"part", "edit_chain", "assembly", "2d_profile", "drawing"}:
                    feature_types = {
                        "cylindrical_bores", "point_material", "clearance_mm", "asset_match", "dxf_profile",
                        "drawing_dimensions", "slot_pattern", "component_layout", "fastener_access",
                        "rotational_pattern", "linear_pattern",
                    }
                    if not any(check["type"] in feature_types for check in checks):
                        _fail(stage_where, "artifact stage lacks a feature-specific check")
                if any(check["type"] == "clarification" for check in checks):
                    _fail(stage_where, "artifact stage cannot carry a clarification check")
            else:
                if category not in {"clarification", "infeasible"}:
                    _fail(stage_where, "only clarification or infeasible categories may omit an artifact")
                expected_disposition = "clarification" if category == "clarification" else "infeasible"
                if outcome != expected_disposition:
                    _fail(f"{stage_where}.expected_outcome", f"category {category!r} requires outcome {expected_disposition!r}")
                if assets or preserve_ids:
                    _fail(stage_where, "non-artifact stages cannot carry assets or preservation IDs")
                if len(checks) != 1 or checks[0]["type"] != "clarification":
                    _fail(stage_where, "non-artifact stage needs exactly one clarification check")
                clarification = checks[0]
                if outcome == "clarification" and not clarification.get("missing"):
                    _fail(stage_where, "vague request needs at least one missing requirement")
                if outcome == "infeasible" and not clarification.get("conflicts"):
                    _fail(stage_where, "infeasible request needs at least one explicit conflict")

            validated_stages.append(
                {
                    "id": stage_id,
                    "request": request,
                    "expected_outcome": outcome,
                    "input_assets": assets,
                    "oracle": {
                        "coverage": oracle["coverage"],
                        "checks": checks,
                        "preserve_check_ids": preserve_ids,
                    },
                }
            )
            prior_checks = check_map

        normalized = {
            "id": task_id,
            "title": task["title"],
            "category": category,
            "difficulty": difficulty,
            "kind": task["kind"],
            "tags": tag_values,
            "family": family,
            "stages": validated_stages,
        }
        if "reference" in task:
            reference = _validate_reference(task["reference"], normalized, f"{where}.reference")
            allowed_factories = {
                "part": set(REFERENCE_FACTORIES) - {"assembly_primitives", "dxf_profile", "drawing_sheet"},
                "edit_chain": set(REFERENCE_FACTORIES) - {"assembly_primitives", "dxf_profile", "drawing_sheet"},
                "assembly": {"assembly_primitives"},
                "2d_profile": {"dxf_profile"},
                "drawing": {"drawing_sheet"},
            }
            if reference["factory"] not in allowed_factories.get(category, set()):
                _fail(f"{where}.reference.factory", f"factory is not valid for category {category!r}")
            normalized["reference"] = reference
        validated_tasks.append(normalized)

    if len(families) < 20:
        _fail("corpus.tasks", f"requires at least 20 structural families; found {len(families)}")
    too_large = {family: count for family, count in families.items() if count > 5}
    if too_large:
        _fail("corpus.tasks", f"family has more than five tasks: {too_large}")
    if enforce_distribution:
        expected_counts = {category: amount for category, (_, amount) in _CATEGORIES.items()}
        if category_counts != Counter(expected_counts):
            _fail("corpus.tasks", f"category distribution must be {expected_counts}; found {dict(category_counts)}")
    for task, source in zip(validated_tasks, tasks, strict=True):
        category = task["category"]
        if category in {"part", "edit_chain", "assembly", "2d_profile", "drawing"} and "reference" not in task:
            _fail(f"task {task['id']}", "artifact tasks require a host-only positive-control reference")
        if category in {"clarification", "infeasible"} and "reference" in task:
            _fail(f"task {task['id']}", "vague or infeasible tasks must not have positive geometry references")
        if category == "assembly" and not any(
            check["type"] == "component_layout" for check in task["stages"][0]["oracle"]["checks"]
        ):
            _fail(f"task {task['id']}", "assembly needs a component-layout check")
        if category == "2d_profile" and not any(
            check["type"] == "dxf_profile" for check in task["stages"][0]["oracle"]["checks"]
        ):
            _fail(f"task {task['id']}", "2D profile needs a dxf_profile check")
        if category == "drawing" and not any(
            check["type"] == "drawing_dimensions" for check in task["stages"][0]["oracle"]["checks"]
        ):
            _fail(f"task {task['id']}", "drawing needs a drawing_dimensions check")

    return {"schema_version": 1, "tasks": validated_tasks}


def _reject_constant(token: str) -> None:
    raise CorpusValidationError(f"non-finite JSON number {token!r} is forbidden")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CorpusValidationError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def load_corpus(path: Path | str = TASKS_PATH, *, corpus_dir: Path | None = None) -> dict[str, Any]:
    """Load strict JSON corpus data; reject duplicate keys and NaN/Infinity."""
    path = Path(path)
    root = Path(corpus_dir) if corpus_dir is not None else path.parent
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except CorpusValidationError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusValidationError(f"cannot load corpus JSON {path}: {exc}") from exc
    return validate_corpus(data, corpus_dir=root)


def public_projection(corpus: Mapping[str, Any]) -> dict[str, Any]:
    """Project only public task identity, requests, and declared public assets."""
    projected_tasks = []
    for task in corpus["tasks"]:
        projected_tasks.append(
            {
                "id": task["id"],
                "title": task["title"],
                "kind": task["kind"],
                "stages": [
                    {
                        "id": stage["id"],
                        "request": stage["request"],
                        "input_assets": [dict(asset) for asset in stage["input_assets"]],
                    }
                    for stage in task["stages"]
                ],
            }
        )
    return {"schema_version": 1, "tasks": projected_tasks}


def load_public_projection(path: Path | str = PUBLIC_PATH) -> dict[str, Any]:
    """Load and validate the checked-in public projection for accidental leaks."""
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except CorpusValidationError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusValidationError(f"cannot load public projection {path}: {exc}") from exc
    value = _object(value, "public")
    _keys(value, {"schema_version", "tasks"}, set(), "public")
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        _fail("public.schema_version", "must equal integer 1")
    if not isinstance(value["tasks"], list):
        _fail("public.tasks", "must be a list")
    seen_tasks: set[str] = set()
    seen_requests: set[str] = set()
    for ti, task in enumerate(value["tasks"]):
        where = f"public.tasks[{ti}]"
        task = _object(task, where)
        _keys(task, {"id", "title", "kind", "stages"}, set(), where)
        task_id = _string(task["id"], f"{where}.id", identifier=True)
        if task_id in seen_tasks:
            _fail(f"{where}.id", "duplicate public task ID")
        seen_tasks.add(task_id)
        _string(task["title"], f"{where}.title")
        if task["kind"] not in {"part", "assembly", "2d", "drawing", "clarification"}:
            _fail(f"{where}.kind", "unsupported public task kind")
        if not isinstance(task["stages"], list) or not task["stages"]:
            _fail(f"{where}.stages", "must be nonempty")
        for si, stage in enumerate(task["stages"]):
            stage_where = f"{where}.stages[{si}]"
            stage = _object(stage, stage_where)
            _keys(stage, {"id", "request", "input_assets"}, set(), stage_where)
            _string(stage["id"], f"{stage_where}.id", identifier=True)
            request = _string(stage["request"], f"{stage_where}.request")
            if request in seen_requests:
                _fail(f"{stage_where}.request", "duplicate public request")
            seen_requests.add(request)
            if not isinstance(stage["input_assets"], list):
                _fail(f"{stage_where}.input_assets", "must be a list")
            for ai, asset in enumerate(stage["input_assets"]):
                _validate_asset(asset, ai, path.parent, f"{stage_where}.input_assets[{ai}]")
    return dict(value)
