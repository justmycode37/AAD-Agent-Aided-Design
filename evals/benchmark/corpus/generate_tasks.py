"""Deterministically generate the checked-in S5 task and public JSON files."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .loader import public_projection, validate_corpus


HERE = Path(__file__).resolve().parent
ASSET_PATH = HERE / "assets" / "fan_envelope.stl"
MM_PER_INCH = 25.4


def span(value: float, tolerance: float = 0.1) -> list[float]:
    return [round(value - tolerance, 6), round(value + tolerance, 6)]


def point(check_id: str, coordinates: list[float], material: str) -> dict[str, Any]:
    return {"id": check_id, "type": "point_material", "point_mm": coordinates, "equals": material}


def bbox_check(dimensions: list[float]) -> dict[str, Any]:
    return {"id": "overall_bbox", "type": "bbox_mm", "ranges": [span(dim) for dim in dimensions]}


def solids_check(count: int = 1) -> dict[str, Any]:
    return {"id": "solid_count", "type": "solids", "equals": count}


def bore(
    center: list[float],
    diameter: float,
    depth: float,
    *,
    axis: list[float] | None = None,
    through: bool = True,
) -> dict[str, Any]:
    delta = 0.1 if through else 0.05
    return {
        "axis_mm": axis or [0.0, 0.0, 1.0],
        "center_mm": center,
        "diameter_mm": span(diameter, 0.05),
        "depth_mm": span(depth, delta),
        "through": through,
    }


def bore_check(check_id: str, bores: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": check_id, "type": "cylindrical_bores", "bores": bores}


def solid_box(center: list[float], size: list[float]) -> dict[str, Any]:
    return {"shape": "box", "center_mm": center, "size_mm": size}


def solid_cylinder(name: str, center: list[float], diameter: float, height: float) -> dict[str, Any]:
    return {"name": name, "shape": "cylinder", "center_mm": center, "size_mm": [diameter, height]}


def component(name: str, shape: str, center: list[float], size: list[float]) -> dict[str, Any]:
    return {"name": name, "shape": shape, "center_mm": center, "size_mm": size}


def _expected_bbox(factory: str, p: dict[str, Any]) -> list[float]:
    if factory in {"mounting_plate", "slotted_rail", "enclosure_lid", "ribbed_cover"}:
        if factory == "ribbed_cover":
            return [p["width_mm"], p["depth_mm"], p["plate_thickness_mm"] + p["rib_height_mm"]]
        return [p["width_mm"], p["depth_mm"], p["thickness_mm"]]
    if factory in {"l_bracket", "gusseted_bracket", "clevis", "u_channel", "c_channel", "open_enclosure", "split_clamp", "pipe_saddle"}:
        if factory == "split_clamp":
            return [p["width_mm"], p["depth_mm"], p["height_mm"]]
        if factory == "pipe_saddle":
            return [p["width_mm"], p["depth_mm"], p["height_mm"]]
        return [p["width_mm"], p["depth_mm"], p["height_mm"]]
    if factory == "sleeve_bushing":
        return [p["outer_diameter_mm"], p["outer_diameter_mm"], p["length_mm"]]
    if factory == "counterbore_flange":
        return [p["outer_diameter_mm"], p["outer_diameter_mm"], p["thickness_mm"]]
    if factory == "step_bushing":
        total = p["body_length_mm"] + p["shoulder_length_mm"]
        return [p["shoulder_diameter_mm"], p["shoulder_diameter_mm"], total]
    if factory == "hose_adapter":
        diameter = max(p["left_diameter_mm"], p["middle_diameter_mm"], p["right_diameter_mm"])
        return [diameter, diameter, p["length_mm"]]
    if factory == "lobe_wheel":
        radius = p["disc_diameter_mm"] / 2
        tip = radius + p["lobe_height_mm"]
        half_width = p["lobe_width_mm"] / 2
        angles = [2 * math.pi * index / p["lobe_count"] for index in range(p["lobe_count"])]
        x_extent = max(radius, *(abs(tip * math.cos(angle)) + half_width * abs(math.sin(angle)) for angle in angles))
        y_extent = max(radius, *(abs(tip * math.sin(angle)) + half_width * abs(math.cos(angle)) for angle in angles))
        return [2 * x_extent, 2 * y_extent, p["disc_thickness_mm"] + p["hub_length_mm"]]
    if factory == "spoked_wheel":
        return [p["outer_diameter_mm"], p["outer_diameter_mm"], p["width_mm"]]
    if factory == "heat_sink":
        return [p["width_mm"], p["depth_mm"], p["base_thickness_mm"] + p["fin_height_mm"]]
    if factory == "pulley":
        return [p["outer_diameter_mm"], p["outer_diameter_mm"], p["thickness_mm"] + p["hub_length_mm"]]
    if factory == "fan_carrier":
        return [
            p["fan_width_mm"] + 2 * p["clearance_mm"] + 2 * p["rail_thickness_mm"],
            p["fan_depth_mm"] + 2 * p["clearance_mm"] + 2 * p["rail_thickness_mm"],
            p["fan_height_mm"] + p["rail_thickness_mm"],
        ]
    if factory == "blocked_counterbore_bracket":
        return [p["width_mm"], p["depth_mm"], p["wall_height_mm"]]
    raise ValueError(f"no bbox arithmetic for factory {factory!r}")


def _core_witness(factory: str, p: dict[str, Any]) -> list[float]:
    if factory in {"mounting_plate", "enclosure_lid"}:
        return [0.0, 0.0, p.get("thickness_mm", 1.0) / 2]
    if factory == "slotted_rail":
        return [-p["width_mm"] / 2 + 3, 0.0, p["thickness_mm"] / 2]
    if factory in {"l_bracket", "gusseted_bracket"}:
        return [0.0, -p["depth_mm"] / 4, p["thickness_mm"] / 2]
    if factory == "sleeve_bushing":
        return [p["outer_diameter_mm"] / 2 - 3, 0.0, 0.0]
    if factory == "counterbore_flange":
        return [p["outer_diameter_mm"] / 2 - 4, 0.0, 0.0]
    if factory == "step_bushing":
        return [p["body_diameter_mm"] / 2 - 3, 0.0, p["body_length_mm"] / 2]
    if factory == "clevis":
        return [0.0, -p["depth_mm"] / 4, p["thickness_mm"] / 2]
    if factory in {"u_channel", "c_channel"}:
        return [0.0, 0.0, p["wall_mm"] / 2]
    if factory == "open_enclosure":
        return [0.0, 0.0, p["floor_mm"] / 2]
    if factory == "hose_adapter":
        return [max(p["left_diameter_mm"], p["middle_diameter_mm"], p["right_diameter_mm"]) / 2 - 3, 0.0, p["end_length_mm"] / 2]
    if factory == "lobe_wheel":
        return [p["disc_diameter_mm"] / 4, 0.0, p["disc_thickness_mm"] / 2]
    if factory == "spoked_wheel":
        return [p["outer_diameter_mm"] / 2 - p["rim_thickness_mm"] / 2, 0.0, 0.0]
    if factory == "split_clamp":
        return [-p["width_mm"] / 3, 0.0, p["height_mm"] / 2]
    if factory == "heat_sink":
        return [0.0, 0.0, p["base_thickness_mm"] / 2]
    if factory == "ribbed_cover":
        return [0.0, 0.0, p["plate_thickness_mm"] / 2]
    if factory == "pipe_saddle":
        return [0.0, 0.0, 1.0]
    if factory == "pulley":
        return [p["hub_diameter_mm"] / 2 - 3, 0.0, p["thickness_mm"] + p["hub_length_mm"] / 2]
    if factory == "fan_carrier":
        x = p["fan_width_mm"] / 2 + p["clearance_mm"] + p["rail_thickness_mm"] / 2
        return [x, 0.0, (p["fan_height_mm"] + p["rail_thickness_mm"]) / 2]
    if factory == "blocked_counterbore_bracket":
        return [-p["width_mm"] / 4, 0.0, p["base_thickness_mm"] / 2]
    raise ValueError(factory)


def _bore_centers(factory: str, p: dict[str, Any]) -> tuple[list[dict[str, Any]], str] | None:
    if factory in {"mounting_plate", "enclosure_lid"}:
        diameter = p["hole_diameter_mm"]
        return ([bore([x, y, p["thickness_mm"] / 2], diameter, p["thickness_mm"]) for x, y in p["hole_centers_mm"]], "mounting_bores")
    if factory == "l_bracket":
        y = p["depth_mm"] / 2 - p["thickness_mm"] / 2
        return ([bore([x, y, z], p["wall_hole_diameter_mm"], p["thickness_mm"], axis=[0.0, 1.0, 0.0]) for x, z in p["wall_hole_centers_xz_mm"]], "wall_bores")
    if factory == "gusseted_bracket":
        y = p["depth_mm"] / 2 - p["thickness_mm"] / 2
        return ([bore([x, y, z], p["wall_hole_diameter_mm"], p["thickness_mm"], axis=[0.0, 1.0, 0.0]) for x, z in p["wall_hole_centers_xz_mm"]], "wall_bores")
    if factory == "u_channel" and p.get("floor_hole_centers_mm"):
        return ([bore([x, y, p["wall_mm"] / 2], p["floor_hole_diameter_mm"], p["wall_mm"]) for x, y in p["floor_hole_centers_mm"]], "floor_bores")
    if factory == "sleeve_bushing":
        return ([bore([0.0, 0.0, 0.0], p["bore_diameter_mm"], p["length_mm"])], "axial_bore")
    if factory == "counterbore_flange":
        bores = [bore([0.0, 0.0, 0.0], p["bore_diameter_mm"], p["thickness_mm"])]
        bores.extend(bore([x, y, 0.0], p["bolt_diameter_mm"], p["thickness_mm"]) for x, y in p["bolt_centers_mm"])
        return bores, "flange_bores"
    if factory == "step_bushing":
        depth = p["body_length_mm"] + p["shoulder_length_mm"]
        return ([bore([0.0, 0.0, depth / 2], p["bore_diameter_mm"], depth)], "axial_bore")
    if factory == "clevis":
        ear_x = p["width_mm"] / 2 - p["ear_thickness_mm"] / 2
        hole_z = p.get("pin_hole_center_z_mm", p["thickness_mm"] + (p["height_mm"] - p["thickness_mm"]) * 0.62)
        return ([bore([sx * ear_x, 0.0, hole_z], p["pin_diameter_mm"], p["ear_thickness_mm"], axis=[1.0, 0.0, 0.0]) for sx in (-1, 1)], "pin_bores")
    if factory == "open_enclosure":
        return ([bore([x, y, p["floor_mm"] / 2], p["hole_diameter_mm"], p["floor_mm"]) for x, y in p["hole_centers_mm"]], "floor_bores") if p["hole_centers_mm"] else None
    if factory == "hose_adapter":
        return ([bore([0.0, 0.0, p["length_mm"] / 2], p["bore_diameter_mm"], p["length_mm"])], "axial_bore")
    if factory == "lobe_wheel":
        length = p["disc_thickness_mm"] + p["hub_length_mm"]
        return ([bore([0.0, 0.0, length / 2], p["bore_diameter_mm"], length)], "axial_bore")
    if factory == "spoked_wheel":
        return ([bore([0.0, 0.0, 0.0], p["bore_diameter_mm"], p["width_mm"])], "axial_bore")
    if factory == "split_clamp":
        bores = [bore([0.0, 0.0, p["height_mm"] / 2], p["bore_diameter_mm"], p["height_mm"])]
        if p.get("bolt_diameter_mm") is not None:
            bores.append(bore([0.0, 0.0, p["height_mm"] * 0.72], p["bolt_diameter_mm"], p["width_mm"], axis=[1.0, 0.0, 0.0]))
        return bores, "clamp_bores"
    if factory == "ribbed_cover":
        return ([bore([x, y, p["plate_thickness_mm"] / 2], p["hole_diameter_mm"], p["plate_thickness_mm"]) for x, y in p["hole_centers_mm"]], "mounting_bores") if p["hole_centers_mm"] else None
    if factory == "pulley":
        depth = p["thickness_mm"] + p["hub_length_mm"]
        return ([bore([0.0, 0.0, depth / 2], p["bore_diameter_mm"], depth)], "axial_bore")
    if factory == "blocked_counterbore_bracket":
        x, y = p["counterbore_center_mm"]
        small = bore([x, y, p["base_thickness_mm"] / 2], p["through_diameter_mm"], p["base_thickness_mm"])
        large = bore(
            [x, y, p["base_thickness_mm"] - p["counterbore_depth_mm"] / 2],
            p["counterbore_diameter_mm"],
            p["counterbore_depth_mm"],
            through=False,
        )
        return [small, large], "counterbore_bores"
    return None


def _pattern_check(factory: str, p: dict[str, Any]) -> dict[str, Any] | None:
    if factory == "slotted_rail":
        return {
            "id": "slot_pattern",
            "type": "slot_pattern",
            "slots": [
                {
                    "center_mm": [x, y, p["thickness_mm"] / 2],
                    "length_mm": span(p["slot_length_mm"]),
                    "width_mm": span(p["slot_width_mm"], 0.05),
                    "through_axis_mm": [0.0, 0.0, 1.0],
                    "through": True,
                }
                for x, y in p["slot_centers_mm"]
            ],
        }
    if factory == "heat_sink":
        pitch = p["width_mm"] / p["fin_count"]
        centers = [
            [-p["width_mm"] / 2 + pitch * (index + 0.5), 0.0, p["base_thickness_mm"] + p["fin_height_mm"] / 2]
            for index in range(p["fin_count"])
        ]
        return {
            "id": "fin_pattern",
            "type": "linear_pattern",
            "axis_mm": [1.0, 0.0, 0.0],
            "feature_centers_mm": centers,
            "feature_width_mm": span(p["fin_thickness_mm"], 0.05),
            "feature_kind": "solid",
            "tolerance_mm": 0.1,
        }
    if factory == "ribbed_cover":
        pitch = p["width_mm"] / (p["rib_count"] + 1)
        centers = [
            [-p["width_mm"] / 2 + pitch * (index + 1), 0.0, -p["rib_height_mm"] / 2]
            for index in range(p["rib_count"])
        ]
        return {
            "id": "rib_pattern",
            "type": "linear_pattern",
            "axis_mm": [1.0, 0.0, 0.0],
            "feature_centers_mm": centers,
            "feature_width_mm": span(p["rib_thickness_mm"], 0.05),
            "feature_kind": "solid",
            "tolerance_mm": 0.1,
        }
    if factory == "lobe_wheel":
        radius = p["disc_diameter_mm"] / 2 + p["lobe_height_mm"] / 2
        centers = [
            [radius * math.cos(2 * math.pi * index / p["lobe_count"]), radius * math.sin(2 * math.pi * index / p["lobe_count"]), p["disc_thickness_mm"] / 2]
            for index in range(p["lobe_count"])
        ]
        return {
            "id": "lobe_pattern",
            "type": "rotational_pattern",
            "axis_mm": [0.0, 0.0, 1.0],
            "center_mm": [0.0, 0.0, p["disc_thickness_mm"] / 2],
            "count": p["lobe_count"],
            "pitch_radius_mm": span(radius, 0.05),
            "feature_centers_mm": centers,
            "tolerance_mm": 0.1,
        }
    if factory == "spoked_wheel":
        outer_r = p["outer_diameter_mm"] / 2
        hub_r = p["hub_diameter_mm"] / 2
        spoke_length = outer_r - p["rim_thickness_mm"] - hub_r + 2
        radius = hub_r + spoke_length / 2 - 0.5
        centers = [
            [radius * math.cos(2 * math.pi * index / p["spoke_count"]), radius * math.sin(2 * math.pi * index / p["spoke_count"]), 0.0]
            for index in range(p["spoke_count"])
        ]
        return {
            "id": "spoke_pattern",
            "type": "rotational_pattern",
            "axis_mm": [0.0, 0.0, 1.0],
            "center_mm": [0.0, 0.0, 0.0],
            "count": p["spoke_count"],
            "pitch_radius_mm": span(radius, 0.05),
            "feature_centers_mm": centers,
            "tolerance_mm": 0.1,
        }
    return None


def part_checks(factory: str, p: dict[str, Any]) -> list[dict[str, Any]]:
    dims = _expected_bbox(factory, p)
    checks: list[dict[str, Any]] = [
        bbox_check(dims),
        solids_check(),
        point("core_material", _core_witness(factory, p), "solid"),
    ]
    bore_data = _bore_centers(factory, p)
    if bore_data and bore_data[0]:
        checks.append(bore_check(bore_data[1], bore_data[0]))

    if factory == "mounting_plate" and p.get("counterbore_diameter_mm") is not None:
        checks.append(
            bore_check(
                "counterbore_seats",
                [
                    bore(
                        [x, y, p["thickness_mm"] - p["counterbore_depth_mm"] / 2],
                        p["counterbore_diameter_mm"],
                        p["counterbore_depth_mm"],
                        through=False,
                    )
                    for x, y in p["hole_centers_mm"]
                ],
            )
        )
    elif factory == "counterbore_flange" and p.get("counterbore_diameter_mm") is not None:
        checks.append(
            bore_check(
                "counterbore_seats",
                [
                    bore(
                        [x, y, p["thickness_mm"] / 2 - p["counterbore_depth_mm"] / 2],
                        p["counterbore_diameter_mm"],
                        p["counterbore_depth_mm"],
                        through=False,
                    )
                    for x, y in p["bolt_centers_mm"]
                ],
            )
        )
    elif factory == "sleeve_bushing" and p.get("counterbore_diameter_mm") is not None:
        checks.append(
            bore_check(
                "counterbore_seat",
                [
                    bore(
                        [0.0, 0.0, p["length_mm"] / 2 - p["counterbore_depth_mm"] / 2],
                        p["counterbore_diameter_mm"],
                        p["counterbore_depth_mm"],
                        through=False,
                    )
                ],
            )
        )
    elif factory == "step_bushing":
        total = p["body_length_mm"] + p["shoulder_length_mm"]
        checks.append(
            bore_check(
                "counterbore_seat",
                [
                    bore(
                        [0.0, 0.0, total - p["counterbore_depth_mm"] / 2],
                        p["counterbore_diameter_mm"],
                        p["counterbore_depth_mm"],
                        through=False,
                    )
                ],
            )
        )

    pattern = _pattern_check(factory, p)
    if pattern is not None:
        checks.append(pattern)

    if factory == "mounting_plate" and p["hole_centers_mm"]:
        x, y = p["hole_centers_mm"][0]
        checks.append(point("hole_void", [x, y, p["thickness_mm"] / 2], "void"))
    elif factory in {"slotted_rail", "u_channel", "c_channel", "open_enclosure", "split_clamp", "pipe_saddle"}:
        if factory == "slotted_rail":
            x, y = p["slot_centers_mm"][0]
            checks.append(point("slot_void", [x, y, p["thickness_mm"] / 2], "void"))
        elif factory == "u_channel":
            checks.extend(
                [
                    point("channel_floor", [0.0, 0.0, p["wall_mm"] / 2], "solid"),
                    point("channel_cavity", [0.0, 0.0, p["height_mm"] - p["wall_mm"] / 2], "void"),
                ]
            )
        elif factory == "c_channel":
            checks.extend(
                [
                    point("channel_web", [-p["width_mm"] / 2 + p["wall_mm"] / 2, 0.0, p["height_mm"] / 2], "solid"),
                    point("channel_opening", [p["width_mm"] / 4, 0.0, p["height_mm"] / 2], "void"),
                ]
            )
        elif factory == "open_enclosure":
            checks.extend(
                [
                    point("cavity_void", [0.0, 0.0, p["floor_mm"] + (p["height_mm"] - p["floor_mm"]) / 2], "void"),
                    point("side_wall", [p["width_mm"] / 2 - p["wall_mm"] / 2, 0.0, p["height_mm"] / 2], "solid"),
                ]
            )
        elif factory == "split_clamp":
            checks.append(point("split_void", [p["width_mm"] / 4, 0.0, p["height_mm"] / 2], "void"))
        else:
            checks.append(point("saddle_void", [0.0, 0.0, p["height_mm"]], "void"))
    elif factory == "l_bracket":
        checks.append(point("open_corner", [0.0, -p["depth_mm"] / 4, p["height_mm"] / 2], "void"))
    elif factory == "gusseted_bracket":
        checks.append(point("gusset_web", [0.0, 0.0, p["height_mm"] * 0.35], "solid"))
    elif factory == "hose_adapter":
        checks.append(
            point(
                "step_wall",
                [p["middle_diameter_mm"] / 2 - 2, 0.0, p["end_length_mm"] + (p["length_mm"] - 2 * p["end_length_mm"]) / 2],
                "solid",
            )
        )
    elif factory == "lobe_wheel":
        checks.append(point("lobe_tip", [_expected_bbox(factory, p)[0] / 2 - 1, 0.0, p["disc_thickness_mm"] / 2], "solid"))
    elif factory == "spoked_wheel":
        checks.append(point("rim_material", [p["outer_diameter_mm"] / 2 - p["rim_thickness_mm"] / 2, 0.0, 0.0], "solid"))
        checks.append(point("window_void", [p["outer_diameter_mm"] / 4, p["outer_diameter_mm"] / 4, 0.0], "void"))
    elif factory == "heat_sink":
        pitch = p["width_mm"] / p["fin_count"]
        gap_x = -p["width_mm"] / 2 + pitch
        checks.append(point("fin_gap", [gap_x, 0.0, p["base_thickness_mm"] + p["fin_height_mm"] / 2], "void"))
    elif factory == "ribbed_cover":
        pitch = p["width_mm"] / (p["rib_count"] + 1)
        gap_x = -p["width_mm"] / 2 + pitch * 1.5
        checks.append(point("rib_gap", [gap_x, 0.0, -p["rib_height_mm"] / 2], "void"))
    elif factory == "pulley":
        checks.append(point("hub_material", _core_witness(factory, p), "solid"))
    elif factory == "fan_carrier":
        checks.append(point("carrier_rail", _core_witness(factory, p), "solid"))
        checks.append(
            {
                "id": "asset_clearance",
                "type": "clearance_mm",
                "asset_id": "fan_envelope",
                "range": [1.9, 2.1],
            }
        )
    elif factory == "blocked_counterbore_bracket":
        x, y = p["counterbore_center_mm"]
        checks.extend(
            [
                point("counterbore_void", [x, y, p["base_thickness_mm"] - p["counterbore_depth_mm"] / 2], "void"),
                {
                    "id": "screw_access",
                    "type": "fastener_access",
                    "axis_mm": [0.0, 0.0, -1.0],
                    "entry_point_mm": [x, y, p["wall_height_mm"] + 5],
                    "seat_point_mm": [x, y, p["base_thickness_mm"] - p["counterbore_depth_mm"]],
                    "shank_diameter_mm": span(p["through_diameter_mm"], 0.05),
                    "head_diameter_mm": span(p["counterbore_diameter_mm"], 0.05),
                    "head_height_mm": span(p["counterbore_depth_mm"], 0.05),
                    "minimum_clearance_mm": 2.0,
                },
            ]
        )

    return checks


def _coverage(checks: list[dict[str, Any]]) -> str:
    # Local witnesses and a well-formed definition cannot prove every requested
    # surface, thickness, absence of extra features, or drawing association.
    return "bounded"


def stage(stage_id: str, request: str, checks: list[dict[str, Any]], *, preserve: list[str] | None = None, assets: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "id": stage_id,
        "request": request,
        "expected_outcome": "artifact",
        "input_assets": assets or [],
        "oracle": {"coverage": _coverage(checks), "checks": checks, "preserve_check_ids": preserve or []},
    }


def _frame(factory: str) -> str:
    if factory == "ribbed_cover":
        return "Coordinates are in millimetres, X/Y origin is the footprint center, the cover plate's underside is z=0, and ribs extend in -Z. Use ±0.10 mm for overall dimensions and ±0.05 mm for feature centers and diameters."
    if factory in {"sleeve_bushing", "counterbore_flange", "spoked_wheel"}:
        return "Coordinates are in millimetres, the cylindrical axis is +Z, and z=0 is the axial mid-plane unless a stepped length is explicitly measured from its lower end. Use ±0.10 mm for the outer envelope and ±0.05 mm for bore centers and diameters."
    return "Coordinates are in millimetres, X/Y origin is at the footprint center, +Z points up from z=0 at the underside. Use ±0.10 mm for overall dimensions and ±0.05 mm for feature centers and diameters."


def part_task(task_id: str, title: str, family: str, difficulty: str, factory: str, params: dict[str, Any], description: str, *, assets: list[dict[str, str]] | None = None, asset_request: str = "") -> dict[str, Any]:
    request = f"{_frame(factory)} {description} {asset_request}".strip()
    checks = part_checks(factory, params)
    stage_record = stage("build", request, checks, assets=assets)
    return {
        "id": task_id,
        "title": title,
        "category": "part",
        "difficulty": difficulty,
        "kind": "part",
        "tags": [f"family:{family}", "units:mm", f"difficulty:{difficulty}"],
        "family": family,
        "stages": [stage_record],
        "reference": {"factory": factory, "stages": {"build": params}},
    }


def _rename_changed_checks(prior: list[dict[str, Any]], current: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    prior_by_id = {check["id"]: check for check in prior}
    result = [dict(check) for check in current]
    for check in result:
        previous = prior_by_id.get(check["id"])
        if previous is not None and previous != check:
            check["id"] = f"{check['id']}_edited"
    preserve = [
        check["id"]
        for check in result
        if check["id"] in prior_by_id and prior_by_id[check["id"]] == check
    ]
    return result, preserve


def edit_task(task_id: str, title: str, family: str, factory: str, p1: dict[str, Any], p2: dict[str, Any], initial_description: str, edit_description: str) -> dict[str, Any]:
    c1 = part_checks(factory, p1)
    c2, preserve = _rename_changed_checks(c1, part_checks(factory, p2))
    if not preserve:
        raise ValueError(f"edit task {task_id} has no retained check")
    dimensions = _expected_bbox(factory, p2)
    initial = f"{_frame(factory)} Initial design: {initial_description}"
    edited = (
        f"{_frame(factory)} Revise the previous artifact: {edit_description} "
        f"The resulting overall envelope is {dimensions[0]:g} × {dimensions[1]:g} × {dimensions[2]:g} mm. "
        "Preserve all geometry outside the requested change and keep one connected solid."
    )
    first = stage("initial", initial, c1)
    second = stage("edit_1", edited, c2, preserve=preserve)
    return {
        "id": task_id,
        "title": title,
        "category": "edit_chain",
        "difficulty": "medium",
        "kind": "part",
        "tags": [f"family:{family}", "edit_chain", "retention", "units:mm"],
        "family": family,
        "stages": [first, second],
        "reference": {"factory": factory, "stages": {"initial": p1, "edit_1": p2}},
    }


def public_asset() -> dict[str, str]:
    return {
        "id": "fan_envelope",
        "path": "assets/fan_envelope.stl",
        "format": "stl",
        "sha256": hashlib.sha256(ASSET_PATH.read_bytes()).hexdigest(),
    }


def _task_family(tag: str) -> str:
    return tag


def part_tasks() -> list[dict[str, Any]]:
    rows: list[tuple[str, str, str, str, dict[str, Any], str]] = [
        ("part_plate_001", "Four-hole mounting plate 80", "mounting_plate", "easy", {"width_mm": 80.0, "depth_mm": 50.0, "thickness_mm": 6.0, "hole_diameter_mm": 5.5, "hole_centers_mm": [[-25.0, -15.0], [-25.0, 15.0], [25.0, -15.0], [25.0, 15.0]]}, "Make an 80 × 50 × 6 mm rectangular mounting plate with four Ø5.5 mm through holes at X=±25 mm and Y=±15 mm."),
        ("part_plate_002", "Six-hole mounting plate 100", "mounting_plate", "medium", {"width_mm": 100.0, "depth_mm": 60.0, "thickness_mm": 8.0, "hole_diameter_mm": 6.5, "hole_centers_mm": [[-30.0, -15.0], [-30.0, 0.0], [-30.0, 15.0], [30.0, -15.0], [30.0, 0.0], [30.0, 15.0]]}, "Make a 100 × 60 × 8 mm mounting plate with six Ø6.5 mm through holes in two columns at X=±30 mm and rows Y=−15, 0, +15 mm."),
        ("part_slot_rail_001", "Three-slot guide rail", "slot_rail", "medium", {"width_mm": 100.0, "depth_mm": 24.0, "thickness_mm": 8.0, "slot_length_mm": 16.0, "slot_width_mm": 8.0, "slot_centers_mm": [[-32.0, 0.0], [0.0, 0.0], [32.0, 0.0]]}, "Make a 100 × 24 × 8 mm guide rail with three rounded through slots, each 16 mm long by 8 mm wide, centered at X=−32, 0, +32 mm."),
        ("part_slot_rail_002", "Four-slot guide rail", "slot_rail", "hard", {"width_mm": 120.0, "depth_mm": 30.0, "thickness_mm": 10.0, "slot_length_mm": 20.0, "slot_width_mm": 8.0, "slot_centers_mm": [[-40.0, 0.0], [-13.333, 0.0], [13.333, 0.0], [40.0, 0.0]]}, "Make a 120 × 30 × 10 mm guide rail with four rounded through slots, each 20 mm long by 8 mm wide, at X=−40, −13.333, +13.333, +40 mm."),
        ("part_l_bracket_001", "L bracket with two wall holes", "l_bracket", "medium", {"width_mm": 70.0, "depth_mm": 40.0, "height_mm": 60.0, "thickness_mm": 5.0, "wall_hole_diameter_mm": 6.0, "wall_hole_centers_xz_mm": [[-20.0, 40.0], [20.0, 40.0]]}, "Make a 70 mm wide L bracket with a 40 mm deep, 5 mm thick foot and a 60 mm high upright at +Y; put two Ø6 mm through holes in the upright at X=±20 mm, Z=40 mm."),
        ("part_l_bracket_002", "L bracket with three wall holes", "l_bracket", "hard", {"width_mm": 90.0, "depth_mm": 50.0, "height_mm": 80.0, "thickness_mm": 6.0, "wall_hole_diameter_mm": 8.0, "wall_hole_centers_xz_mm": [[-25.0, 55.0], [0.0, 55.0], [25.0, 55.0]]}, "Make a 90 mm wide L bracket with a 50 mm deep, 6 mm thick foot and an 80 mm high upright at +Y; add three Ø8 mm through holes at X=−25, 0, +25 mm, Z=55 mm."),
        ("part_gusset_001", "Centered gusseted angle", "gusseted_bracket", "hard", {"width_mm": 60.0, "depth_mm": 40.0, "height_mm": 50.0, "thickness_mm": 5.0, "gusset_width_mm": 25.0, "gusset_thickness_mm": 8.0, "wall_hole_diameter_mm": 6.0, "wall_hole_centers_xz_mm": [[-18.0, 38.0], [18.0, 38.0]]}, "Make a 60 × 40 × 50 mm right-angle bracket with 5 mm plates, two Ø6 mm upright holes at X=±18, Z=38 mm, and one centered triangular web 25 mm deep by 8 mm thick."),
        ("part_gusset_002", "Wide gusseted angle", "gusseted_bracket", "hard", {"width_mm": 80.0, "depth_mm": 50.0, "height_mm": 70.0, "thickness_mm": 6.0, "gusset_width_mm": 30.0, "gusset_thickness_mm": 10.0, "wall_hole_diameter_mm": 8.0, "wall_hole_centers_xz_mm": [[-25.0, 52.0], [25.0, 52.0]]}, "Make an 80 × 50 × 70 mm right-angle bracket with 6 mm plates, two Ø8 mm upright holes at X=±25, Z=52 mm, and one centered triangular web 30 mm deep by 10 mm thick."),
        ("part_sleeve_001", "Bronze sleeve 32 by 24", "sleeve_bushing", "easy", {"outer_diameter_mm": 32.0, "length_mm": 24.0, "bore_diameter_mm": 16.0}, "Make a coaxial sleeve bushing with Ø32 mm outside, Ø16 mm through bore, and 24 mm axial length."),
        ("part_sleeve_002", "Sleeve 45 by 30", "sleeve_bushing", "medium", {"outer_diameter_mm": 45.0, "length_mm": 30.0, "bore_diameter_mm": 20.0}, "Make a coaxial sleeve bushing with Ø45 mm outside, Ø20 mm through bore, and 30 mm axial length."),
        ("part_flange_001", "Four-bolt counterbore flange", "counterbore_flange", "medium", {"outer_diameter_mm": 70.0, "thickness_mm": 8.0, "bore_diameter_mm": 20.0, "bolt_diameter_mm": 6.0, "bolt_centers_mm": [[-22.0, -22.0], [-22.0, 22.0], [22.0, -22.0], [22.0, 22.0]], "counterbore_diameter_mm": 10.0, "counterbore_depth_mm": 2.5}, "Make a Ø70 × 8 mm flange with a Ø20 mm through bore and four Ø6 mm through bolt holes at (±22, ±22) mm. Counterbore each bolt hole to Ø10 mm × 2.5 mm deep from +Z."),
        ("part_flange_002", "Six-bolt counterbore flange", "counterbore_flange", "hard", {"outer_diameter_mm": 90.0, "thickness_mm": 10.0, "bore_diameter_mm": 25.0, "bolt_diameter_mm": 8.0, "bolt_centers_mm": [[-30.0, 0.0], [-15.0, -25.981], [15.0, -25.981], [30.0, 0.0], [15.0, 25.981], [-15.0, 25.981]], "counterbore_diameter_mm": 12.0, "counterbore_depth_mm": 3.0}, "Make a Ø90 × 10 mm flange with a Ø25 mm through bore and six Ø8 mm through bolt holes on a 60 mm bolt circle. Counterbore each bolt hole to Ø12 mm × 3 mm deep from +Z."),
        ("part_step_bushing_001", "Stepped bushing 24 body", "step_bushing", "medium", {"body_diameter_mm": 24.0, "body_length_mm": 18.0, "shoulder_diameter_mm": 36.0, "shoulder_length_mm": 4.0, "bore_diameter_mm": 10.0, "counterbore_diameter_mm": 14.0, "counterbore_depth_mm": 3.0}, "Make a stepped bushing with a Ø24 × 18 mm body, a Ø36 × 4 mm shoulder, a Ø10 mm through bore, and a Ø14 mm counterbore 3 mm deep from the shoulder end."),
        ("part_step_bushing_002", "Stepped bushing 32 body", "step_bushing", "hard", {"body_diameter_mm": 32.0, "body_length_mm": 25.0, "shoulder_diameter_mm": 44.0, "shoulder_length_mm": 5.0, "bore_diameter_mm": 12.0, "counterbore_diameter_mm": 18.0, "counterbore_depth_mm": 4.0}, "Make a stepped bushing with a Ø32 × 25 mm body, a Ø44 × 5 mm shoulder, a Ø12 mm through bore, and a Ø18 mm counterbore 4 mm deep from the shoulder end."),
        ("part_clevis_001", "Clevis fork 50 wide", "clevis", "medium", {"width_mm": 50.0, "depth_mm": 30.0, "height_mm": 45.0, "thickness_mm": 6.0, "ear_thickness_mm": 6.0, "pin_diameter_mm": 10.0, "pin_hole_center_z_mm": 30.0}, "Make a 50 × 30 × 45 mm clevis with a 6 mm base, two 6 mm ears, and aligned Ø10 mm through pin holes at Z=30 mm."),
        ("part_clevis_002", "Clevis fork 70 wide", "clevis", "hard", {"width_mm": 70.0, "depth_mm": 40.0, "height_mm": 60.0, "thickness_mm": 8.0, "ear_thickness_mm": 8.0, "pin_diameter_mm": 12.0, "pin_hole_center_z_mm": 40.0}, "Make a 70 × 40 × 60 mm clevis with an 8 mm base, two 8 mm ears, and aligned Ø12 mm through pin holes at Z=40 mm."),
        ("part_u_channel_001", "U channel 60 by 80", "u_channel", "easy", {"width_mm": 60.0, "depth_mm": 80.0, "height_mm": 30.0, "wall_mm": 4.0}, "Make an open-top U channel 60 mm wide, 80 mm long, 30 mm high, with a 4 mm floor and 4 mm side walls."),
        ("part_u_channel_002", "U channel 80 by 100", "u_channel", "medium", {"width_mm": 80.0, "depth_mm": 100.0, "height_mm": 40.0, "wall_mm": 5.0}, "Make an open-top U channel 80 mm wide, 100 mm long, 40 mm high, with a 5 mm floor and 5 mm side walls."),
        ("part_c_channel_001", "C channel 60 wide", "c_channel", "medium", {"width_mm": 60.0, "depth_mm": 40.0, "height_mm": 30.0, "wall_mm": 4.0}, "Make a 60 × 40 × 30 mm C section with a 4 mm web and 4 mm top and bottom flanges extending toward +X."),
        ("part_c_channel_002", "C channel 80 wide", "c_channel", "hard", {"width_mm": 80.0, "depth_mm": 50.0, "height_mm": 40.0, "wall_mm": 5.0}, "Make an 80 × 50 × 40 mm C section with a 5 mm web and 5 mm top and bottom flanges extending toward +X."),
        ("part_enclosure_001", "Open enclosure 80 by 60", "open_enclosure", "medium", {"width_mm": 80.0, "depth_mm": 60.0, "height_mm": 40.0, "wall_mm": 3.0, "floor_mm": 4.0, "hole_diameter_mm": 4.0, "hole_centers_mm": [[-30.0, -20.0], [-30.0, 20.0], [30.0, -20.0], [30.0, 20.0]]}, "Make an open-top 80 × 60 × 40 mm enclosure with 3 mm side walls, a 4 mm floor, and four Ø4 mm through floor holes at (±30, ±20) mm."),
        ("part_enclosure_002", "Open enclosure 120 by 80", "open_enclosure", "hard", {"width_mm": 120.0, "depth_mm": 80.0, "height_mm": 55.0, "wall_mm": 4.0, "floor_mm": 5.0, "hole_diameter_mm": 5.0, "hole_centers_mm": [[-48.0, -28.0], [-48.0, 28.0], [48.0, -28.0], [48.0, 28.0]]}, "Make an open-top 120 × 80 × 55 mm enclosure with 4 mm side walls, a 5 mm floor, and four Ø5 mm through floor holes at (±48, ±28) mm."),
        ("part_lid_001", "Lid plate 60 by 40", "enclosure_lid", "easy", {"width_mm": 60.0, "depth_mm": 40.0, "thickness_mm": 3.0, "hole_diameter_mm": 4.0, "hole_centers_mm": [[-24.0, -14.0], [-24.0, 14.0], [24.0, -14.0], [24.0, 14.0]]}, "Make a 60 × 40 × 3 mm enclosure lid with four Ø4 mm through holes at X=±24 mm and Y=±14 mm."),
        ("part_lid_002", "Inch-specified lid plate", "enclosure_lid", "medium", {"width_mm": 4.0 * MM_PER_INCH, "depth_mm": 3.0 * MM_PER_INCH, "thickness_mm": 0.2 * MM_PER_INCH, "hole_diameter_mm": 0.2 * MM_PER_INCH, "hole_centers_mm": [[-1.6 * MM_PER_INCH, -1.0 * MM_PER_INCH], [-1.6 * MM_PER_INCH, 1.0 * MM_PER_INCH], [1.6 * MM_PER_INCH, -1.0 * MM_PER_INCH], [1.6 * MM_PER_INCH, 1.0 * MM_PER_INCH]]}, "Make a 4 × 3 × 0.2 in enclosure lid with four Ø0.2 in through holes at X=±1.6 in and Y=±1.0 in."),
        ("part_hose_adapter_001", "Stepped hose adapter 54", "hose_adapter", "medium", {"left_diameter_mm": 30.0, "middle_diameter_mm": 34.0, "right_diameter_mm": 28.0, "length_mm": 54.0, "bore_diameter_mm": 14.0, "end_length_mm": 12.0}, "Make a coaxial stepped hose adapter 54 mm long: Ø30 mm lower end for 12 mm, Ø34 mm middle section, Ø28 mm upper end for 12 mm, with a Ø14 mm through bore."),
        ("part_hose_adapter_002", "Stepped hose adapter 72", "hose_adapter", "hard", {"left_diameter_mm": 38.0, "middle_diameter_mm": 42.0, "right_diameter_mm": 36.0, "length_mm": 72.0, "bore_diameter_mm": 18.0, "end_length_mm": 16.0}, "Make a coaxial stepped hose adapter 72 mm long: Ø38 mm lower end for 16 mm, Ø42 mm middle section, Ø36 mm upper end for 16 mm, with a Ø18 mm through bore."),
        ("part_lobe_wheel_001", "Eight-lobe drive wheel", "lobe_wheel", "hard", {"disc_diameter_mm": 50.0, "disc_thickness_mm": 8.0, "hub_diameter_mm": 20.0, "hub_length_mm": 8.0, "bore_diameter_mm": 8.0, "lobe_count": 8, "lobe_width_mm": 8.0, "lobe_height_mm": 8.0}, "Make an 8 mm thick Ø50 mm wheel with eight equally spaced rectangular drive lobes, each 8 mm tangential width and 8 mm radial projection, an 8 mm long Ø20 mm rear hub, and an Ø8 mm axial through bore."),
        ("part_lobe_wheel_002", "Ten-lobe drive wheel", "lobe_wheel", "hard", {"disc_diameter_mm": 70.0, "disc_thickness_mm": 10.0, "hub_diameter_mm": 28.0, "hub_length_mm": 10.0, "bore_diameter_mm": 12.0, "lobe_count": 10, "lobe_width_mm": 10.0, "lobe_height_mm": 8.0}, "Make a 10 mm thick Ø70 mm wheel with ten equally spaced rectangular drive lobes, each 10 mm tangential width and 8 mm radial projection, a 10 mm long Ø28 mm rear hub, and an Ø12 mm axial through bore."),
        ("part_spoked_wheel_001", "Four-spoke wheel", "spoked_wheel", "hard", {"outer_diameter_mm": 80.0, "hub_diameter_mm": 20.0, "width_mm": 10.0, "rim_thickness_mm": 4.0, "spoke_count": 4, "bore_diameter_mm": 8.0}, "Make an Ø80 mm wheel 10 mm wide with a 4 mm rim, Ø20 mm hub, four equally spaced radial spokes, and Ø8 mm through bore."),
        ("part_spoked_wheel_002", "Six-spoke wheel", "spoked_wheel", "hard", {"outer_diameter_mm": 100.0, "hub_diameter_mm": 30.0, "width_mm": 12.0, "rim_thickness_mm": 5.0, "spoke_count": 6, "bore_diameter_mm": 12.0}, "Make an Ø100 mm wheel 12 mm wide with a 5 mm rim, Ø30 mm hub, six equally spaced radial spokes, and Ø12 mm through bore."),
        ("part_split_clamp_001", "Split shaft clamp 40", "split_clamp", "hard", {"width_mm": 40.0, "depth_mm": 30.0, "height_mm": 25.0, "bore_diameter_mm": 16.0, "split_width_mm": 2.0, "bolt_diameter_mm": 6.0}, "Make a 40 × 30 × 25 mm split shaft clamp with a Ø16 mm vertical bore, a 2 mm slit opening to +X, and a Ø6 mm cross bolt passage along X."),
        ("part_split_clamp_002", "Split shaft clamp 50", "split_clamp", "hard", {"width_mm": 50.0, "depth_mm": 38.0, "height_mm": 30.0, "bore_diameter_mm": 20.0, "split_width_mm": 3.0, "bolt_diameter_mm": 8.0}, "Make a 50 × 38 × 30 mm split shaft clamp with a Ø20 mm vertical bore, a 3 mm slit opening to +X, and a Ø8 mm cross bolt passage along X."),
        ("part_heat_sink_001", "Seven-fin heat sink", "heat_sink", "medium", {"width_mm": 80.0, "depth_mm": 50.0, "base_thickness_mm": 6.0, "fin_height_mm": 24.0, "fin_thickness_mm": 2.0, "fin_count": 7}, "Make an 80 × 50 × 6 mm base with seven equally spaced 2 mm thick fins, each 24 mm high and spanning the full 50 mm depth."),
        ("part_heat_sink_002", "Nine-fin heat sink", "heat_sink", "hard", {"width_mm": 100.0, "depth_mm": 60.0, "base_thickness_mm": 8.0, "fin_height_mm": 32.0, "fin_thickness_mm": 2.5, "fin_count": 9}, "Make a 100 × 60 × 8 mm base with nine equally spaced 2.5 mm thick fins, each 32 mm high and spanning the full 60 mm depth."),
        ("part_ribbed_cover_001", "Five-rib cover", "ribbed_cover", "medium", {"width_mm": 100.0, "depth_mm": 60.0, "plate_thickness_mm": 4.0, "rib_height_mm": 6.0, "rib_thickness_mm": 3.0, "rib_count": 5, "hole_diameter_mm": 5.0, "hole_centers_mm": [[-42.0, -22.0], [-42.0, 22.0], [42.0, -22.0], [42.0, 22.0]]}, "Make a 100 × 60 × 4 mm cover with five underside ribs, each 3 mm thick and 6 mm deep, plus four Ø5 mm through mounting holes at (±42, ±22) mm."),
        ("part_ribbed_cover_002", "Six-rib cover", "ribbed_cover", "hard", {"width_mm": 120.0, "depth_mm": 80.0, "plate_thickness_mm": 5.0, "rib_height_mm": 8.0, "rib_thickness_mm": 4.0, "rib_count": 6, "hole_diameter_mm": 6.0, "hole_centers_mm": [[-52.0, -32.0], [-52.0, 32.0], [52.0, -32.0], [52.0, 32.0]]}, "Make a 120 × 80 × 5 mm cover with six underside ribs, each 4 mm thick and 8 mm deep, plus four Ø6 mm through mounting holes at (±52, ±32) mm."),
    ]
    factories = {"slot_rail": "slotted_rail", "lid": "enclosure_lid"}
    tasks = [part_task(row[0], row[1], row[2], row[3], factories.get(row[2], row[2]), row[4], row[5]) for row in rows]
    inch_task = next(task for task in tasks if task["id"] == "part_lid_002")
    inch_task["tags"] = [tag for tag in inch_task["tags"] if tag != "units:mm"] + ["units:in"]
    inch_task["stages"][0]["request"] = (
        "Use inch units. The XY origin is at the lid center and z=0 is its underside. Make a 4.000 × 3.000 × 0.200 in "
        "enclosure lid with four Ø0.200 in through holes at X=±1.600 in and Y=±1.000 in. Overall tolerance is ±0.004 in; "
        "hole-center and diameter tolerance is ±0.002 in."
    )
    inch_params = inch_task["reference"]["stages"]["build"]
    for check in inch_task["stages"][0]["oracle"]["checks"]:
        if check["type"] == "bbox_mm":
            check["ranges"] = [span(value, 0.004 * MM_PER_INCH) for value in _expected_bbox("enclosure_lid", inch_params)]
        elif check["type"] == "cylindrical_bores":
            for hole in check["bores"]:
                hole["diameter_mm"] = span(inch_params["hole_diameter_mm"], 0.002 * MM_PER_INCH)
                hole["depth_mm"] = span(inch_params["thickness_mm"], 0.004 * MM_PER_INCH)
                hole["center_tolerance_mm"] = 0.002 * MM_PER_INCH

    tasks.append(
        part_task(
            "part_pipe_saddle_001",
            "Pipe saddle block",
            "pipe_saddle",
            "medium",
            "pipe_saddle",
            {"width_mm": 80.0, "depth_mm": 45.0, "height_mm": 20.0, "pipe_diameter_mm": 30.0},
            "Make an 80 × 45 × 20 mm support block with a centered semicircular cradle for a Ø30 mm pipe, running through the full 80 mm width.",
        )
    )
    tasks.append(
        part_task(
            "part_pulley_001",
            "Hubbed belt sheave",
            "pulley",
            "medium",
            "pulley",
            {"outer_diameter_mm": 60.0, "thickness_mm": 12.0, "hub_diameter_mm": 24.0, "hub_length_mm": 8.0, "bore_diameter_mm": 10.0},
            "Make a Ø60 × 12 mm sheave disk with an 8 mm long Ø24 mm rear hub and an Ø10 mm axial through bore.",
        )
    )
    asset = public_asset()
    tasks.append(
        part_task(
            "part_fan_carrier_001",
            "Fan-envelope carrier with clearance",
            "fan_carrier",
            "hard",
            "fan_carrier",
            {"fan_width_mm": 40.0, "fan_depth_mm": 40.0, "fan_height_mm": 20.0, "rail_thickness_mm": 3.0, "clearance_mm": 2.0},
            "Make four upright carrier rails around the declared 40 × 40 × 20 mm fan-envelope asset. Keep a 2.0 mm gap from the asset to each inner rail face; rail thickness is 3 mm and rail height is 23 mm.",
            assets=[asset],
            asset_request="Use only the declared public asset fan_envelope as the fit reference; the minimum measured clearance must be 1.9–2.1 mm.",
        )
    )
    tasks.append(
        part_task(
            "part_blocked_counterbore_001",
            "Accessible counterbore beside upright web",
            "blocked_counterbore_bracket",
            "hard",
            "blocked_counterbore_bracket",
            {"width_mm": 60.0, "depth_mm": 40.0, "base_thickness_mm": 6.0, "wall_height_mm": 50.0, "wall_thickness_mm": 6.0, "counterbore_center_mm": [0.0, 7.0], "through_diameter_mm": 4.4, "counterbore_diameter_mm": 10.0, "counterbore_depth_mm": 3.0},
            "Make a 60 × 40 × 50 mm right-angle bracket with a 6 mm base and 6 mm upright at +Y. Put an M4-clearance Ø4.4 mm through hole at (0, 7) mm in the base, with a Ø10 mm counterbore 3 mm deep from +Z. Preserve a straight +Z screw insertion path and at least 2.0 mm clearance around the Ø10 mm head envelope beside the upright web.",
        )
    )
    return tasks


def edit_tasks() -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    def add(*args: Any) -> None:
        edits.append(edit_task(*args))

    add(
        "edit_plate_widen_001", "Widen a plate while preserving bores", "mounting_plate", "mounting_plate",
        {"width_mm": 80.0, "depth_mm": 50.0, "thickness_mm": 6.0, "hole_diameter_mm": 5.5, "hole_centers_mm": [[-25.0, -15.0], [25.0, -15.0]]},
        {"width_mm": 100.0, "depth_mm": 50.0, "thickness_mm": 6.0, "hole_diameter_mm": 5.5, "hole_centers_mm": [[-25.0, -15.0], [25.0, -15.0]]},
        "Create an 80 × 50 × 6 mm plate with two Ø5.5 mm through holes at (−25, −15) and (+25, −15) mm.",
        "Increase only the centered plate's X width from 80 to 100 mm. Retain both Ø5.5 mm through holes at (−25, −15) and (+25, −15) mm, the 50 mm depth and 6 mm thickness.",
    )
    add(
        "edit_plate_counterbore_002", "Add counterbores to a six-hole plate", "mounting_plate", "mounting_plate",
        {"width_mm": 100.0, "depth_mm": 60.0, "thickness_mm": 8.0, "hole_diameter_mm": 6.5, "hole_centers_mm": [[-30.0, -15.0], [-30.0, 0.0], [-30.0, 15.0], [30.0, -15.0], [30.0, 0.0], [30.0, 15.0]]},
        {"width_mm": 100.0, "depth_mm": 60.0, "thickness_mm": 8.0, "hole_diameter_mm": 6.5, "hole_centers_mm": [[-30.0, -15.0], [-30.0, 0.0], [-30.0, 15.0], [30.0, -15.0], [30.0, 0.0], [30.0, 15.0]], "counterbore_diameter_mm": 11.0, "counterbore_depth_mm": 3.0},
        "Create a 100 × 60 × 8 mm plate with six Ø6.5 mm through holes at X=±30 mm and Y=−15, 0, +15 mm.",
        "Add a Ø11 mm counterbore 3 mm deep from +Z at each of the six existing holes, retaining their centers and through diameters.",
    )
    add(
        "edit_slot_rail_add_center_003", "Add a center slot to the guide rail", "slot_rail", "slotted_rail",
        {"width_mm": 100.0, "depth_mm": 24.0, "thickness_mm": 8.0, "slot_length_mm": 16.0, "slot_width_mm": 8.0, "slot_centers_mm": [[-32.0, 0.0], [32.0, 0.0]]},
        {"width_mm": 100.0, "depth_mm": 24.0, "thickness_mm": 8.0, "slot_length_mm": 16.0, "slot_width_mm": 8.0, "slot_centers_mm": [[-32.0, 0.0], [0.0, 0.0], [32.0, 0.0]]},
        "Create a 100 × 24 × 8 mm rail with two rounded through slots, each 16 × 8 mm, centered at X=±32 mm.",
        "Add a third matching 16 × 8 mm through slot at X=0 mm while retaining the two outer slots.",
    )
    add(
        "edit_slot_rail_widen_004", "Widen the rail slots", "slot_rail", "slotted_rail",
        {"width_mm": 120.0, "depth_mm": 30.0, "thickness_mm": 10.0, "slot_length_mm": 20.0, "slot_width_mm": 6.0, "slot_centers_mm": [[-40.0, 0.0], [0.0, 0.0], [40.0, 0.0]]},
        {"width_mm": 120.0, "depth_mm": 30.0, "thickness_mm": 10.0, "slot_length_mm": 20.0, "slot_width_mm": 8.0, "slot_centers_mm": [[-40.0, 0.0], [0.0, 0.0], [40.0, 0.0]]},
        "Create a 120 × 30 × 10 mm rail with three rounded 20 × 6 mm through slots at X=−40, 0, +40 mm.",
        "Widen those three slots to 8 mm while retaining their centers and 20 mm overall slot length.",
    )
    add(
        "edit_l_bracket_add_hole_005", "Add a second upright mounting hole", "l_bracket", "l_bracket",
        {"width_mm": 70.0, "depth_mm": 40.0, "height_mm": 60.0, "thickness_mm": 5.0, "wall_hole_diameter_mm": 6.0, "wall_hole_centers_xz_mm": [[-20.0, 40.0]]},
        {"width_mm": 70.0, "depth_mm": 40.0, "height_mm": 60.0, "thickness_mm": 5.0, "wall_hole_diameter_mm": 6.0, "wall_hole_centers_xz_mm": [[-20.0, 40.0], [20.0, 40.0]]},
        "Create a 70 × 40 × 60 mm L bracket with 5 mm plates and one Ø6 mm upright through hole at X=−20, Z=40 mm.",
        "Add a matching Ø6 mm upright through hole at X=+20, Z=40 mm while retaining the original hole.",
    )
    add(
        "edit_l_bracket_third_hole_006", "Extend upright hole row to three", "l_bracket", "l_bracket",
        {"width_mm": 90.0, "depth_mm": 50.0, "height_mm": 80.0, "thickness_mm": 6.0, "wall_hole_diameter_mm": 8.0, "wall_hole_centers_xz_mm": [[-25.0, 55.0], [25.0, 55.0]]},
        {"width_mm": 90.0, "depth_mm": 50.0, "height_mm": 80.0, "thickness_mm": 6.0, "wall_hole_diameter_mm": 8.0, "wall_hole_centers_xz_mm": [[-25.0, 55.0], [0.0, 55.0], [25.0, 55.0]]},
        "Create a 90 × 50 × 80 mm L bracket with 6 mm plates and two Ø8 mm upright through holes at X=±25, Z=55 mm.",
        "Add a third Ø8 mm upright hole at X=0, Z=55 mm, retaining the original pair.",
    )
    add(
        "edit_sleeve_counterbore_007", "Add a lead-in counterbore", "sleeve_bushing", "sleeve_bushing",
        {"outer_diameter_mm": 32.0, "length_mm": 24.0, "bore_diameter_mm": 16.0},
        {"outer_diameter_mm": 32.0, "length_mm": 24.0, "bore_diameter_mm": 16.0, "counterbore_diameter_mm": 20.0, "counterbore_depth_mm": 2.0},
        "Create a coaxial sleeve Ø32 mm outside, Ø16 mm through bore, 24 mm long.",
        "Add a Ø20 mm counterbore 2 mm deep at the +Z end, retaining the Ø16 mm through bore and outer envelope.",
    )
    add(
        "edit_sleeve_bore_008", "Increase sleeve bore diameter", "sleeve_bushing", "sleeve_bushing",
        {"outer_diameter_mm": 45.0, "length_mm": 30.0, "bore_diameter_mm": 18.0},
        {"outer_diameter_mm": 45.0, "length_mm": 30.0, "bore_diameter_mm": 20.0},
        "Create a coaxial sleeve Ø45 mm outside, Ø18 mm through bore, 30 mm long.",
        "Increase the axial through bore to Ø20 mm while retaining the Ø45 mm outside and 30 mm length.",
    )
    add(
        "edit_flange_counterbores_009", "Add bolt-hole counterbores", "counterbore_flange", "counterbore_flange",
        {"outer_diameter_mm": 70.0, "thickness_mm": 8.0, "bore_diameter_mm": 20.0, "bolt_diameter_mm": 6.0, "bolt_centers_mm": [[-22.0, -22.0], [-22.0, 22.0], [22.0, -22.0], [22.0, 22.0]]},
        {"outer_diameter_mm": 70.0, "thickness_mm": 8.0, "bore_diameter_mm": 20.0, "bolt_diameter_mm": 6.0, "bolt_centers_mm": [[-22.0, -22.0], [-22.0, 22.0], [22.0, -22.0], [22.0, 22.0]], "counterbore_diameter_mm": 10.0, "counterbore_depth_mm": 2.5},
        "Create a Ø70 × 8 mm flange with a Ø20 mm center bore and four Ø6 mm through holes at (±22, ±22) mm.",
        "Add a Ø10 mm × 2.5 mm counterbore from +Z at each bolt hole, retaining all through bores and centers.",
    )
    add(
        "edit_flange_add_bolts_010", "Add two holes to the flange bolt circle", "counterbore_flange", "counterbore_flange",
        {"outer_diameter_mm": 90.0, "thickness_mm": 10.0, "bore_diameter_mm": 25.0, "bolt_diameter_mm": 8.0, "bolt_centers_mm": [[-30.0, 0.0], [0.0, -30.0], [30.0, 0.0], [0.0, 30.0]]},
        {"outer_diameter_mm": 90.0, "thickness_mm": 10.0, "bore_diameter_mm": 25.0, "bolt_diameter_mm": 8.0, "bolt_centers_mm": [[-30.0, 0.0], [-15.0, -25.981], [15.0, -25.981], [30.0, 0.0], [15.0, 25.981], [-15.0, 25.981]]},
        "Create a Ø90 × 10 mm flange with a Ø25 mm center bore and four Ø8 mm through holes at the cardinal points of a 60 mm bolt circle.",
        "Add two Ø8 mm holes at (−15, −25.981) and (+15, +25.981) mm to make six equally spaced positions on the same 60 mm bolt circle.",
    )
    add(
        "edit_enclosure_add_mounts_011", "Add four floor mounting holes", "open_enclosure", "open_enclosure",
        {"width_mm": 80.0, "depth_mm": 60.0, "height_mm": 40.0, "wall_mm": 3.0, "floor_mm": 4.0, "hole_diameter_mm": 4.0, "hole_centers_mm": []},
        {"width_mm": 80.0, "depth_mm": 60.0, "height_mm": 40.0, "wall_mm": 3.0, "floor_mm": 4.0, "hole_diameter_mm": 4.0, "hole_centers_mm": [[-30.0, -20.0], [-30.0, 20.0], [30.0, -20.0], [30.0, 20.0]]},
        "Create an open-top 80 × 60 × 40 mm enclosure with 3 mm walls and a 4 mm floor.",
        "Add four Ø4 mm through holes in the floor at (±30, ±20) mm while retaining the open cavity and walls.",
    )
    add(
        "edit_enclosure_add_pair_012", "Add two more enclosure mounts", "open_enclosure", "open_enclosure",
        {"width_mm": 120.0, "depth_mm": 80.0, "height_mm": 55.0, "wall_mm": 4.0, "floor_mm": 5.0, "hole_diameter_mm": 5.0, "hole_centers_mm": [[-48.0, -28.0], [48.0, -28.0], [-48.0, 28.0], [48.0, 28.0]]},
        {"width_mm": 120.0, "depth_mm": 80.0, "height_mm": 55.0, "wall_mm": 4.0, "floor_mm": 5.0, "hole_diameter_mm": 5.0, "hole_centers_mm": [[-48.0, -28.0], [-48.0, 28.0], [48.0, -28.0], [48.0, 28.0], [0.0, -28.0], [0.0, 28.0]]},
        "Create an open-top 120 × 80 × 55 mm enclosure with 4 mm walls, a 5 mm floor, and four Ø5 mm floor holes at the corners.",
        "Add two more Ø5 mm through floor holes at X=0, Y=±28 mm; retain the original four holes and cavity dimensions.",
    )
    add(
        "edit_channel_add_drains_013", "Add floor drain holes to U channel", "u_channel", "u_channel",
        {"width_mm": 60.0, "depth_mm": 80.0, "height_mm": 30.0, "wall_mm": 4.0},
        {"width_mm": 60.0, "depth_mm": 80.0, "height_mm": 30.0, "wall_mm": 4.0, "floor_hole_diameter_mm": 6.0, "floor_hole_centers_mm": [[-15.0, 0.0], [15.0, 0.0]]},
        "Create a 60 × 80 × 30 mm open-top U channel with 4 mm floor and side walls.",
        "Add two Ø6 mm through drain holes in the floor at X=±15, Y=0 mm while retaining the channel envelope and walls.",
    )
    add(
        "edit_channel_add_four_drains_014", "Add a second drain row", "u_channel", "u_channel",
        {"width_mm": 80.0, "depth_mm": 100.0, "height_mm": 40.0, "wall_mm": 5.0, "floor_hole_diameter_mm": 8.0, "floor_hole_centers_mm": [[-20.0, -25.0], [20.0, -25.0]]},
        {"width_mm": 80.0, "depth_mm": 100.0, "height_mm": 40.0, "wall_mm": 5.0, "floor_hole_diameter_mm": 8.0, "floor_hole_centers_mm": [[-20.0, -25.0], [20.0, -25.0], [-20.0, 25.0], [20.0, 25.0]]},
        "Create an 80 × 100 × 40 mm U channel with 5 mm walls and two Ø8 mm floor holes at X=±20, Y=−25 mm.",
        "Add a matching drain-hole pair at Y=+25 mm; retain the first pair, channel dimensions, and 5 mm walls.",
    )
    add(
        "edit_clamp_add_bolt_015", "Add a cross-bolt passage", "split_clamp", "split_clamp",
        {"width_mm": 40.0, "depth_mm": 30.0, "height_mm": 25.0, "bore_diameter_mm": 16.0, "split_width_mm": 2.0},
        {"width_mm": 40.0, "depth_mm": 30.0, "height_mm": 25.0, "bore_diameter_mm": 16.0, "split_width_mm": 2.0, "bolt_diameter_mm": 6.0},
        "Create a 40 × 30 × 25 mm split clamp with a Ø16 mm axial bore and a 2 mm slit to +X.",
        "Add a Ø6 mm cross-bolt passage along X at 72% of the clamp height, retaining the axial bore and slit.",
    )
    add(
        "edit_clamp_widen_split_016", "Widen the clamp slit", "split_clamp", "split_clamp",
        {"width_mm": 50.0, "depth_mm": 38.0, "height_mm": 30.0, "bore_diameter_mm": 20.0, "split_width_mm": 2.0, "bolt_diameter_mm": 8.0},
        {"width_mm": 50.0, "depth_mm": 38.0, "height_mm": 30.0, "bore_diameter_mm": 20.0, "split_width_mm": 3.0, "bolt_diameter_mm": 8.0},
        "Create a 50 × 38 × 30 mm split clamp with a Ø20 mm axial bore, 2 mm slit to +X, and Ø8 mm cross-bolt passage.",
        "Widen the slit to 3 mm while retaining the bore, bolt passage, envelope, and connected clamp body.",
    )
    add(
        "edit_heat_sink_add_fins_017", "Increase heat-sink fin count", "heat_sink", "heat_sink",
        {"width_mm": 80.0, "depth_mm": 50.0, "base_thickness_mm": 6.0, "fin_height_mm": 24.0, "fin_thickness_mm": 2.0, "fin_count": 5},
        {"width_mm": 80.0, "depth_mm": 50.0, "base_thickness_mm": 6.0, "fin_height_mm": 24.0, "fin_thickness_mm": 2.0, "fin_count": 7},
        "Create an 80 × 50 × 6 mm heat-sink base with five equally spaced 2 mm thick, 24 mm high fins.",
        "Re-space the fins as seven equally spaced 2 mm thick, 24 mm high fins while retaining the base and total envelope.",
    )
    add(
        "edit_heat_sink_add_two_more_018", "Add two fins to the larger sink", "heat_sink", "heat_sink",
        {"width_mm": 100.0, "depth_mm": 60.0, "base_thickness_mm": 8.0, "fin_height_mm": 32.0, "fin_thickness_mm": 2.5, "fin_count": 6},
        {"width_mm": 100.0, "depth_mm": 60.0, "base_thickness_mm": 8.0, "fin_height_mm": 32.0, "fin_thickness_mm": 2.5, "fin_count": 8},
        "Create a 100 × 60 × 8 mm heat-sink base with six equally spaced 2.5 mm thick, 32 mm high fins.",
        "Change the array to eight equally spaced fins of the same thickness and height; retain the full base envelope.",
    )
    imported = edit_task(
        "edit_import_mesh_019",
        "Import and edit a public STL base",
        "imported_block",
        "mounting_plate",
        {"width_mm": 40.0, "depth_mm": 40.0, "thickness_mm": 20.0, "hole_diameter_mm": 4.0, "hole_centers_mm": []},
        {"width_mm": 40.0, "depth_mm": 40.0, "thickness_mm": 20.0, "hole_diameter_mm": 4.0, "hole_centers_mm": [[-12.0, -12.0], [-12.0, 12.0], [12.0, -12.0], [12.0, 12.0]]},
        "Import the declared fan_envelope.stl as a 40 × 40 × 20 mm base solid without changing its geometry.",
        "Add four Ø4 mm through holes at X=±12, Y=±12 mm while retaining the imported 40 × 40 × 20 mm envelope and base solid.",
    )
    asset = public_asset()
    first = imported["stages"][0]
    first["request"] = (
        "Import the declared fan_envelope.stl in millimetres as the base solid. Use X/Y origin at the footprint center, +Z upward, and z=0 at the lower face; its footprint is 40 × 40 mm and z spans 0–20 mm. "
        "Preserve the imported geometry exactly within 0.05 mm surface distance and 0.5 mm³ symmetric-difference volume; do not add features in this stage."
    )
    first["input_assets"] = [asset]
    first["oracle"]["checks"].append(
        {
            "id": "imported_asset_geometry",
            "type": "asset_match",
            "asset_id": "fan_envelope",
            "symmetric_difference_volume_mm3": [0.0, 0.5],
            "surface_distance_mm": [0.0, 0.05],
        }
    )
    first["oracle"]["coverage"] = "bounded"
    second = imported["stages"][1]
    second["request"] = (
        "Edit the base imported from fan_envelope.stl. Add four Ø4 mm through holes at X=±12, Y=±12 mm. Use the same centered XY frame and z=0 at the lower face. "
        "Retain the 40 × 40 × 20 mm envelope, all material outside the requested holes, and one connected solid; hole center/diameter tolerance is ±0.05 mm."
    )
    second["input_assets"] = [asset]
    second["oracle"]["coverage"] = "bounded"
    imported["tags"] = ["family:imported_block", "edit_chain", "retention", "import", "units:mm"]
    edits.append(imported)
    add(
        "edit_lid_enlarge_holes_020", "Enlarge lid fastener holes", "enclosure_lid", "enclosure_lid",
        {"width_mm": 100.0, "depth_mm": 70.0, "thickness_mm": 5.0, "hole_diameter_mm": 4.0, "hole_centers_mm": [[-42.0, -27.0], [-42.0, 27.0], [42.0, -27.0], [42.0, 27.0]]},
        {"width_mm": 100.0, "depth_mm": 70.0, "thickness_mm": 5.0, "hole_diameter_mm": 6.0, "hole_centers_mm": [[-42.0, -27.0], [-42.0, 27.0], [42.0, -27.0], [42.0, 27.0]]},
        "Create a 100 × 70 × 5 mm lid with four Ø4 mm through holes at X=±42, Y=±27 mm.",
        "Enlarge the same four holes to Ø6 mm, retaining their centers and the lid envelope.",
    )
    return edits


def _component_ranges(item: dict[str, Any]) -> list[list[float]]:
    center = item["center_mm"]
    if item["shape"] == "box":
        size = item["size_mm"]
    else:
        diameter, height = item["size_mm"]
        size = [diameter, diameter, height]
    return [[center[i] - size[i] / 2, center[i] + size[i] / 2] for i in range(3)]


def assembly_task(task_id: str, title: str, family: str, description: str, components: list[dict[str, Any]]) -> dict[str, Any]:
    ranges = [_component_ranges(item) for item in components]
    overall = [
        max(part[axis][1] for part in ranges) - min(part[axis][0] for part in ranges)
        for axis in range(3)
    ]
    checks = [bbox_check(overall), solids_check(len(components))]
    checks.append(
        {
            "id": "component_layout",
            "type": "component_layout",
            "parts": [{"ranges_mm": box} for box in ranges],
            "tolerance_mm": 0.1,
        }
    )
    for index, item in enumerate(components):
        checks.append(point(f"component_{index + 1}_center", item["center_mm"], "solid"))
    request = (
        "Use millimetres in a world frame with X/Y centered on the base, +Z upward, and z=0 at its lower face. "
        "Place named components at the stated centers within ±0.10 mm; component dimensions and overall envelope tolerance are ±0.10 mm. "
        + description
    )
    return {
        "id": task_id,
        "title": title,
        "category": "assembly",
        "difficulty": "hard",
        "kind": "assembly",
        "tags": [f"family:{family}", "assembly", "units:mm"],
        "family": family,
        "stages": [stage("build", request, checks)],
        "reference": {"factory": "assembly_primitives", "stages": {"build": {"components": components}}},
    }


def assembly_tasks() -> list[dict[str, Any]]:
    return [
        assembly_task(
            "assembly_standoffs_001",
            "Four-standoff panel support",
            "standoff_plate_assembly",
            "Assemble one 60 × 40 × 6 mm base plate centered at (0,0,3) with four Ø8 × 12 mm vertical standoffs centered at (±20, ±10, 12) mm.",
            [component("base_plate", "box", [0.0, 0.0, 3.0], [60.0, 40.0, 6.0])]
            + [solid_cylinder(f"standoff_{i + 1}", [x, y, 12.0], 8.0, 12.0) for i, (x, y) in enumerate([(-20.0, -10.0), (-20.0, 10.0), (20.0, -10.0), (20.0, 10.0)])],
        ),
        assembly_task(
            "assembly_spool_002",
            "Three-piece stepped spool",
            "stacked_spool_assembly",
            "Stack three coaxial parts on +Z: a Ø50 × 6 mm lower flange centered at z=3, a Ø20 × 20 mm core centered at z=16, and a Ø40 × 6 mm upper flange centered at z=29 mm.",
            [
                solid_cylinder("lower_flange", [0.0, 0.0, 3.0], 50.0, 6.0),
                solid_cylinder("core", [0.0, 0.0, 16.0], 20.0, 20.0),
                solid_cylinder("upper_flange", [0.0, 0.0, 29.0], 40.0, 6.0),
            ],
        ),
        assembly_task(
            "assembly_rail_slide_003",
            "Rail with twin sliding blocks",
            "twin_rail_slide_assembly",
            "Place a 100 × 20 × 10 mm rail centered at (0,0,5) and two separate 20 × 30 × 12 mm sliders centered at X=−25 and +25, Y=0, Z=16 mm.",
            [
                component("rail", "box", [0.0, 0.0, 5.0], [100.0, 20.0, 10.0]),
                component("left_slider", "box", [-25.0, 0.0, 16.0], [20.0, 30.0, 12.0]),
                component("right_slider", "box", [25.0, 0.0, 16.0], [20.0, 30.0, 12.0]),
            ],
        ),
        assembly_task(
            "assembly_hinge_004",
            "Twin-leaf hinge with vertical pin",
            "twin_leaf_hinge_assembly",
            "Place two 30 × 20 × 4 mm leaves centered at X=−17 and +17, Y=0, Z=2 mm, plus one Ø8 × 24 mm vertical pin centered at (0,0,16) mm. The pin starts at the leaf top; keep the three components separate.",
            [
                component("left_leaf", "box", [-17.0, 0.0, 2.0], [30.0, 20.0, 4.0]),
                component("right_leaf", "box", [17.0, 0.0, 2.0], [30.0, 20.0, 4.0]),
                solid_cylinder("hinge_pin", [0.0, 0.0, 16.0], 8.0, 24.0),
            ],
        ),
        assembly_task(
            "assembly_panel_spacers_005",
            "Panel with four spacer columns",
            "panel_spacer_assembly",
            "Assemble two 80 × 50 × 4 mm plates centered at z=2 and z=14 mm. Put four Ø10 × 8 mm spacer cylinders at (±30, ±15, 8) mm between them.",
            [
                component("lower_panel", "box", [0.0, 0.0, 2.0], [80.0, 50.0, 4.0]),
                component("upper_panel", "box", [0.0, 0.0, 14.0], [80.0, 50.0, 4.0]),
            ]
            + [solid_cylinder(f"spacer_{i + 1}", [x, y, 8.0], 10.0, 8.0) for i, (x, y) in enumerate([(-30.0, -15.0), (-30.0, 15.0), (30.0, -15.0), (30.0, 15.0)])],
        ),
        assembly_task(
            "assembly_case_lid_006",
            "Enclosure body with removable lid",
            "case_lid_assembly",
            "Place an 80 × 60 × 30 mm body centered at z=15 mm and a separate 80 × 60 × 4 mm lid centered at z=34 mm, leaving a 2 mm gap between them.",
            [
                component("body", "box", [0.0, 0.0, 15.0], [80.0, 60.0, 30.0]),
                component("lid", "box", [0.0, 0.0, 34.0], [80.0, 60.0, 4.0]),
            ],
        ),
        assembly_task(
            "assembly_fan_support_007",
            "Fan support with four posts",
            "fan_post_assembly",
            "Place an 80 × 80 × 4 mm base at z=0..4, four Ø8 × 20 mm posts at X/Y=±30 mm centered at z=14 mm, and a separate 40 × 40 × 20 mm fan block centered at (0,0,36) mm.",
            [
                component("base", "box", [0.0, 0.0, 2.0], [80.0, 80.0, 4.0]),
            ]
            + [solid_cylinder(f"post_{i + 1}", [x, y, 14.0], 8.0, 20.0) for i, (x, y) in enumerate([(-30.0, -30.0), (-30.0, 30.0), (30.0, -30.0), (30.0, 30.0)])]
            + [component("fan_block", "box", [0.0, 0.0, 36.0], [40.0, 40.0, 20.0])],
        ),
        assembly_task(
            "assembly_corner_stop_008",
            "Right-angle locating stop",
            "right_angle_stop_assembly",
            "Assemble a 70 × 50 × 8 mm base centered at z=4, an 8 × 50 × 30 mm left stop centered at (−31,0,23) mm, and a separate 62 × 8 × 30 mm back stop centered at (4,21,23) mm. Both stops start at z=8 mm and meet at the outside corner without overlapping.",
            [
                component("base", "box", [0.0, 0.0, 4.0], [70.0, 50.0, 8.0]),
                component("left_stop", "box", [-31.0, 0.0, 23.0], [8.0, 50.0, 30.0]),
                component("back_stop", "box", [4.0, 21.0, 23.0], [62.0, 8.0, 30.0]),
            ],
        ),
        assembly_task(
            "assembly_two_rotors_009",
            "Side-by-side rotor pair",
            "twin_rotor_assembly",
            "Place two separate Ø30 × 10 mm vertical rotors centered at X=−22 and +22 mm, Y=0, Z=5 mm, with a 14 mm gap between their outer circular envelopes.",
            [
                solid_cylinder("left_rotor", [-22.0, 0.0, 5.0], 30.0, 10.0),
                solid_cylinder("right_rotor", [22.0, 0.0, 5.0], 30.0, 10.0),
            ],
        ),
        assembly_task(
            "assembly_spacer_stack_010",
            "Four-piece coaxial spacer stack",
            "coaxial_spacer_stack_assembly",
            "Stack four separate vertical cylinders on +Z with a 1 mm axial gap between neighbors: Ø40 × 8 mm centered at z=4, Ø24 × 12 mm at z=15, Ø32 × 6 mm at z=25, and Ø18 × 10 mm at z=34 mm.",
            [
                solid_cylinder("lower_ring", [0.0, 0.0, 4.0], 40.0, 8.0),
                solid_cylinder("spacer_a", [0.0, 0.0, 15.0], 24.0, 12.0),
                solid_cylinder("spacer_b", [0.0, 0.0, 25.0], 32.0, 6.0),
                solid_cylinder("upper_spacer", [0.0, 0.0, 34.0], 18.0, 10.0),
            ],
        ),
    ]


def _profile_extent(contours: list[list[list[float]]], circles: list[dict[str, Any]]) -> list[list[float]]:
    xs = [point[0] for contour in contours for point in contour]
    ys = [point[1] for contour in contours for point in contour]
    for circle in circles:
        x, y = circle["center_mm"]
        radius = circle["diameter_mm"] / 2
        xs.extend((x - radius, x + radius))
        ys.extend((y - radius, y + radius))
    if not xs or not ys:
        raise ValueError("profile must contain a closed contour or circle")
    return [[min(xs), max(xs)], [min(ys), max(ys)]]


def profile_task(task_id: str, title: str, family: str, description: str, contours: list[list[list[float]]], circles: list[dict[str, Any]]) -> dict[str, Any]:
    extents = _profile_extent(contours, circles)
    segments = []
    for contour in contours:
        for index, start in enumerate(contour):
            end = contour[(index + 1) % len(contour)]
            segments.append({"start_mm": start, "end_mm": end, "tolerance_mm": 0.1})
    check = {
        "id": "profile_geometry",
        "type": "dxf_profile",
        "cut_layers": ["CUT"],
        "contour_count": len(contours) + len(circles),
        "closed_contours": True,
        "extents_mm": [span(pair[1] - pair[0]) for pair in extents],
        "circles": [
            {"center_mm": item["center_mm"], "diameter_mm": span(item["diameter_mm"], 0.05)}
            for item in circles
        ],
    }
    if segments:
        check["segments"] = segments
    request = (
        "Use a millimetre XY sketch with origin [0,0], +X right and +Y up. Center the outside XY bounding rectangle at the origin. Put every closed cut contour on the CUT layer. "
        "Outer extents and segment endpoints must be within ±0.10 mm; circle centers and diameters within ±0.05 mm. "
        + description
    )
    return {
        "id": task_id,
        "title": title,
        "category": "2d_profile",
        "difficulty": "medium",
        "kind": "2d",
        "tags": [f"family:{family}", "profile", "dxf", "units:mm"],
        "family": family,
        "stages": [stage("profile", request, [check])],
        "reference": {"factory": "dxf_profile", "stages": {"profile": {"contours": contours, "circles": circles}}},
    }


def profile_tasks() -> list[dict[str, Any]]:
    rect = lambda x, y: [[-x / 2, -y / 2], [x / 2, -y / 2], [x / 2, y / 2], [-x / 2, y / 2]]
    return [
        profile_task("profile_round_plate_001", "Round plate with four holes", "round_plate_profile", "Cut one Ø80 mm circular plate with four Ø6 mm holes at (±25, ±25) mm.", [], [{"center_mm": [0.0, 0.0], "diameter_mm": 80.0}] + [{"center_mm": [x, y], "diameter_mm": 6.0} for x, y in [(-25.0, -25.0), (-25.0, 25.0), (25.0, -25.0), (25.0, 25.0)]]),
        profile_task("profile_octagon_002", "Octagonal panel with center bore", "octagonal_panel_profile", "Cut a centered regular octagonal panel with 80 mm overall X/Y extents and one Ø20 mm center bore.", [[[-40.0, -16.569], [-16.569, -40.0], [16.569, -40.0], [40.0, -16.569], [40.0, 16.569], [16.569, 40.0], [-16.569, 40.0], [-40.0, 16.569]]], [{"center_mm": [0.0, 0.0], "diameter_mm": 20.0}]),
        profile_task("profile_l_plate_003", "L-shaped plate profile", "l_profile", "Cut one L contour with outer legs 80 × 60 mm and a 20 mm leg width; add a Ø6 mm hole at (−30, −20) mm.", [[[-40.0, -30.0], [40.0, -30.0], [40.0, -10.0], [-20.0, -10.0], [-20.0, 30.0], [-40.0, 30.0]]], [{"center_mm": [-30.0, -20.0], "diameter_mm": 6.0}]),
        profile_task("profile_u_plate_004", "U-shaped mounting profile", "u_profile", "Cut a U profile with 90 × 60 mm outer extents, 15 mm side legs, and a 15 mm bottom web.", [[[-45.0, -30.0], [45.0, -30.0], [45.0, 30.0], [30.0, 30.0], [30.0, -15.0], [-30.0, -15.0], [-30.0, 30.0], [-45.0, 30.0]]], []),
        profile_task("profile_t_plate_005", "T-shaped mounting profile", "t_profile", "Cut one T contour 100 mm wide by 70 mm high: the top bar is 100 × 20 mm and the centered stem is 20 mm wide.", [[[-50.0, 35.0], [50.0, 35.0], [50.0, 15.0], [10.0, 15.0], [10.0, -35.0], [-10.0, -35.0], [-10.0, 15.0], [-50.0, 15.0]]], []),
        profile_task("profile_keyhole_006", "Keyhole tab profile", "keyhole_tab_profile", "Cut a 70 × 40 mm tab with a rectangular through slot 30 mm long by 12 mm wide centered at X=−10 mm and a Ø10 mm circular hole at X=20 mm.", [rect(70.0, 40.0), [[-25.0, -6.0], [5.0, -6.0], [5.0, 6.0], [-25.0, 6.0]]], [{"center_mm": [20.0, 0.0], "diameter_mm": 10.0}]),
        profile_task("profile_washer_007", "Concentric washer profile", "washer_profile", "Cut a washer profile with Ø60 mm outside diameter and Ø24 mm concentric inside diameter.", [], [{"center_mm": [0.0, 0.0], "diameter_mm": 60.0}, {"center_mm": [0.0, 0.0], "diameter_mm": 24.0}]),
        profile_task("profile_strap_008", "Two-slot strap profile", "slotted_strap_profile", "Cut a 140 × 32 mm strap with two rectangular slot openings 24 × 8 mm centered at X=−45 and +45 mm, Y=0.", [rect(140.0, 32.0), [[-57.0, -4.0], [-33.0, -4.0], [-33.0, 4.0], [-57.0, 4.0]], [[33.0, -4.0], [57.0, -4.0], [57.0, 4.0], [33.0, 4.0]]], []),
        profile_task("profile_hex_flange_009", "Hexagonal flange with seven holes", "hex_flange_profile", "Cut a regular hexagonal flange with 80 mm across-flats, one Ø16 mm center bore, and six Ø6 mm holes on a 56 mm bolt circle.", [[[-40.0, -23.094], [-40.0, 23.094], [0.0, 46.188], [40.0, 23.094], [40.0, -23.094], [0.0, -46.188]]], [{"center_mm": [0.0, 0.0], "diameter_mm": 16.0}] + [{"center_mm": [28.0 * math.cos(index * math.pi / 3), 28.0 * math.sin(index * math.pi / 3)], "diameter_mm": 6.0} for index in range(6)]),
        profile_task("profile_tab_010", "Offset tab with one hole", "offset_tab_profile", "Cut one offset tab with a 90 × 60 mm outer envelope, a 20 × 20 mm square removed from its lower-right corner, and a Ø8 mm hole at (20, 15) mm.", [[[-45.0, -30.0], [25.0, -30.0], [25.0, -10.0], [45.0, -10.0], [45.0, 30.0], [-45.0, 30.0]]], [{"center_mm": [20.0, 15.0], "diameter_mm": 8.0}]),
        profile_task("profile_five_hole_rail_011", "Five-hole rail profile", "five_hole_rail_profile", "Cut a 150 × 30 mm flat rail with five Ø6 mm holes on its centerline at X=−60, −30, 0, +30, +60 mm.", [rect(150.0, 30.0)], [{"center_mm": [x, 0.0], "diameter_mm": 6.0} for x in [-60.0, -30.0, 0.0, 30.0, 60.0]]),
        profile_task("profile_gasket_012", "Stepped gasket ring profile", "gasket_ring_profile", "Cut two concentric closed circles for a ring gasket: Ø90 mm outer boundary and Ø70 mm inner opening.", [], [{"center_mm": [0.0, 0.0], "diameter_mm": 90.0}, {"center_mm": [0.0, 0.0], "diameter_mm": 70.0}]),
        profile_task("profile_clipped_panel_013", "Clipped-corner panel", "clipped_panel_profile", "Cut a 100 × 70 mm plate with 10 × 10 mm clipped corners and four Ø5 mm holes at (±38, ±23) mm.", [[[-40.0, -35.0], [40.0, -35.0], [50.0, -25.0], [50.0, 25.0], [40.0, 35.0], [-40.0, 35.0], [-50.0, 25.0], [-50.0, -25.0]]], [{"center_mm": [x, y], "diameter_mm": 5.0} for x, y in [(-38.0, -23.0), (-38.0, 23.0), (38.0, -23.0), (38.0, 23.0)]]),
        profile_task("profile_hinge_leaf_014", "Three-hole hinge leaf", "hinge_leaf_profile", "Cut a 90 × 30 mm hinge leaf with three Ø8 mm holes on the Y=0 centerline at X=−30, 0, +30 mm.", [rect(90.0, 30.0)], [{"center_mm": [x, 0.0], "diameter_mm": 8.0} for x in [-30.0, 0.0, 30.0]]),
        profile_task("profile_pipe_gauge_015", "Pipe gauge template", "pipe_gauge_profile", "Cut three concentric gauge rings centered at the origin with diameters 64, 50, and 38 mm; retain all three circular contours on CUT.", [], [{"center_mm": [0.0, 0.0], "diameter_mm": 64.0}, {"center_mm": [0.0, 0.0], "diameter_mm": 50.0}, {"center_mm": [0.0, 0.0], "diameter_mm": 38.0}]),
    ]


def drawing_task(task_id: str, title: str, family: str, description: str, dimensions: list[dict[str, Any]], views: list[str], sheet_mm: list[float]) -> dict[str, Any]:
    check = {"id": "drawing_measurements", "type": "drawing_dimensions", "dimensions": dimensions, "required_views": views}
    request = (
        "Use millimetres in a sheet XY frame with origin at the lower-left corner, +X right, +Y up, and third-angle orthographic projection. "
        "Dimension values must be measured from the stated geometry within ±0.10 mm; include each required view as a named view. "
        + description
    )
    params = {"views": views, "dimensions": [{"id": d["id"], "value_mm": d["value_mm"]} for d in dimensions], "sheet_mm": sheet_mm}
    return {
        "id": task_id,
        "title": title,
        "category": "drawing",
        "difficulty": "medium",
        "kind": "drawing",
        "tags": [f"family:{family}", "drawing", "dimensioned", "units:mm"],
        "family": family,
        "stages": [stage("drawing", request, [check])],
        "reference": {"factory": "drawing_sheet", "stages": {"drawing": params}},
    }


def drawing_tasks() -> list[dict[str, Any]]:
    d = lambda key, value: {"id": key, "value_mm": value, "tolerance_mm": 0.1}
    return [
        drawing_task("drawing_fan_plate_001", "Dimensioned fan plate drawing", "fan_plate_drawing", "Create a drawing for a 100 × 60 × 8 mm plate with four Ø6.5 mm holes at X=±30, Y=±15 mm. Show overall width, overall height, thickness, hole diameter, and hole spacing. Required views: front, top, right.", [d("overall_width", 100.0), d("overall_height", 60.0), d("thickness", 8.0), d("hole_diameter", 6.5), d("hole_pitch_x", 60.0), d("hole_pitch_y", 30.0)], ["front", "top", "right"], [420.0, 297.0]),
        drawing_task("drawing_clevis_002", "Dimensioned clevis drawing", "clevis_drawing", "Create a drawing for a 50 × 30 × 45 mm clevis with 6 mm plates and aligned Ø10 mm pin holes at Z=30 mm. Show overall width, depth, height, pin diameter, and pin center height. Required views: front, top, right.", [d("overall_width", 50.0), d("overall_depth", 30.0), d("overall_height", 45.0), d("pin_diameter", 10.0), d("pin_center_height", 30.0)], ["front", "top", "right"], [297.0, 210.0]),
        drawing_task("drawing_enclosure_003", "Dimensioned enclosure drawing", "enclosure_drawing", "Create a drawing for an open-top 120 × 80 × 55 mm enclosure with 4 mm walls and a 5 mm floor. Show outside width, depth, height, wall thickness, and floor thickness. Required views: front, top, right, section.", [d("outside_width", 120.0), d("outside_depth", 80.0), d("outside_height", 55.0), d("wall_thickness", 4.0), d("floor_thickness", 5.0)], ["front", "top", "right", "section"], [420.0, 297.0]),
        drawing_task("drawing_flange_004", "Dimensioned flange drawing", "flange_drawing", "Create a drawing for a Ø90 × 10 mm flange with a Ø25 mm center bore and six Ø8 mm holes on a 60 mm bolt circle. Show outside diameter, thickness, center bore, bolt-hole diameter, and bolt circle. Required views: front, top, right.", [d("outside_diameter", 90.0), d("thickness", 10.0), d("center_bore", 25.0), d("bolt_hole_diameter", 8.0), d("bolt_circle", 60.0)], ["front", "top", "right"], [297.0, 210.0]),
        drawing_task("drawing_channel_005", "Dimensioned C-channel drawing", "channel_drawing", "Create a drawing for an 80 × 50 × 40 mm C channel with 5 mm web and flange thickness. Show width, depth, height, web thickness, and flange thickness. Required views: front, right, section.", [d("overall_width", 80.0), d("overall_depth", 50.0), d("overall_height", 40.0), d("web_thickness", 5.0), d("flange_thickness", 5.0)], ["front", "right", "section"], [297.0, 210.0]),
    ]


def disposition_task(task_id: str, title: str, category: str, request: str, *, missing: list[str] | None = None, conflicts: list[str] | None = None) -> dict[str, Any]:
    check: dict[str, Any] = {"id": "clarification_required", "type": "clarification"}
    if missing:
        check["missing"] = missing
    if conflicts:
        check["conflicts"] = conflicts
    outcome = "clarification" if category == "clarification" else "infeasible"
    return {
        "id": task_id,
        "title": title,
        "category": category,
        "difficulty": "medium" if category == "clarification" else "hard",
        "kind": "clarification",
        "tags": ["disposition", "no_geometry", category],
        "family": f"{category}_{task_id.rsplit('_', 1)[-1]}",
        "stages": [
            {
                "id": "request",
                "request": request,
                "expected_outcome": outcome,
                "input_assets": [],
                "oracle": {"coverage": "complete", "checks": [check], "preserve_check_ids": []},
            }
        ],
    }


def disposition_tasks() -> list[dict[str, Any]]:
    vague = [
        disposition_task("clarify_pump_bracket_001", "Underspecified pump bracket", "clarification", "Design a bracket to hold my pump. Make it sturdy and compact; ask me before choosing dimensions.", missing=["pump mounting-hole pattern and datum", "pump mass and operating loads", "allowable envelope and nearby obstructions", "material and manufacturing process", "units and dimensional tolerances"]),
        disposition_task("clarify_electronics_box_002", "Underspecified electronics enclosure", "clarification", "Make a small enclosure for my controller board with room for the connectors.", missing=["board length, width, thickness, and hole pattern", "connector locations and clearance envelopes", "lid or open-top preference and sealing requirement", "material, process, and wall limits", "units and tolerances"]),
        disposition_task("clarify_hinge_003", "Underspecified hinge", "clarification", "Create a strong hinge that swings smoothly and looks clean.", missing=["leaf dimensions and mounting pattern", "pin diameter and swing range", "load, cycle life, and allowable play", "material and manufacturing process", "units and tolerances"]),
        disposition_task("clarify_pipe_adapter_004", "Underspecified pipe adapter", "clarification", "Make an adapter for this pipe so it seals well.", missing=["pipe standard or measured outside diameter", "mating-port dimensions and insertion depth", "pressure and temperature range", "seal type and permitted leakage", "material, process, units, and tolerances"]),
        disposition_task("clarify_laser_panel_005", "Underspecified laser-cut panel", "clarification", "Make a nice panel with the right holes for my project.", missing=["overall panel dimensions and outline", "hole count, diameters, and center coordinates", "material thickness and kerf allowance", "mounting datum and edge margins", "units and dimensional tolerances"]),
    ]
    infeasible = [
        disposition_task("infeasible_double_width_001", "Conflicting overall widths", "infeasible", "Make one plate whose overall X width is exactly 80.0 ±0.1 mm and exactly 90.0 ±0.1 mm at the same time. Do not choose one value silently; explain the conflict and ask which width governs.", conflicts=["the required X-width intervals [79.9, 80.1] mm and [89.9, 90.1] mm do not overlap"]),
        disposition_task("infeasible_wall_stack_002", "Impossible enclosure wall stack", "infeasible", "Make an enclosure 6.0 ±0.1 mm wide with two 5.0 ±0.1 mm side walls and a positive inside width of at least 2.0 mm. Keep all dimensions in mm and do not return geometry.", conflicts=["the two required walls alone need at least 9.8 mm of width, exceeding the 6.0 mm outside width", "the required positive inside width cannot fit between the walls"]),
        disposition_task("infeasible_hole_depth_003", "Contradictory hole depth", "infeasible", "In an 8.0 ±0.1 mm plate, make the same Ø6 mm hole both through the full plate and blind to a depth of 3.0 ±0.1 mm from the top face. Identify the conflict and ask which hole intent is required.", conflicts=["one hole cannot both pass through an 8 mm plate and terminate at 3 mm depth from its top face"]),
        disposition_task("infeasible_ring_wall_004", "Negative wall thickness ring", "infeasible", "Make a ring with Ø20.0 ±0.1 mm outside diameter and a Ø24.0 ±0.1 mm through bore. Do not produce geometry; explain the incompatible diameter requirements.", conflicts=["the requested bore diameter exceeds the outside diameter for every value within tolerance"]),
        disposition_task("infeasible_envelope_005", "Part exceeds its fixed envelope", "infeasible", "Fit a 110.0 ±0.1 mm long cartridge inside a fixed 105.0 ±0.1 mm internal bracket span without changing either dimension. Identify whether the requirements can coexist before making geometry.", conflicts=["the minimum cartridge length 109.9 mm exceeds the maximum available span 105.1 mm"]),
    ]
    return vague + infeasible


def build_records() -> dict[str, Any]:
    tasks = part_tasks() + edit_tasks() + assembly_tasks() + profile_tasks() + drawing_tasks() + disposition_tasks()
    return validate_corpus({"schema_version": 1, "tasks": tasks}, corpus_dir=HERE)


def main() -> None:
    corpus = build_records()
    public = public_projection(corpus)
    (HERE / "tasks.json").write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (HERE / "public.json").write_text(json.dumps(public, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(corpus['tasks'])} tasks and {sum(len(task['stages']) for task in corpus['tasks'])} stages")


if __name__ == "__main__":
    main()
