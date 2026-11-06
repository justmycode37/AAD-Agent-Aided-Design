"""CAD operations build123d does not package: assemblies, sheet metal, direct edits and more.

Everything here returns real build123d shapes and measures its own results. A helper that
cannot produce valid geometry raises ``CadOperationError`` instead of returning a near miss.
Imports of the kernel stay inside functions, so ``aad.engine.types`` remains kernel-free.
"""

from __future__ import annotations

import math
import errno
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._runtime import ModelInputError

_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,254}\Z")
_asset_dir: Path | None = None
_asset_units = "mm"


class CadOperationError(ModelInputError):
    """A helper's inputs or its kernel result could not make valid geometry."""


def _require_valid(shape: Any, what: str) -> Any:
    if shape is None or not shape.is_valid:
        raise CadOperationError(f"{what} did not produce a valid shape.")
    return shape


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
        raise CadOperationError(f"{name} must be a positive finite number, not {value!r}.")
    return float(value)


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise CadOperationError(f"{name} must be a finite number, not {value!r}.")
    return float(value)


def _nonnegative(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result < 0:
        raise CadOperationError(f"{name} must be non-negative, not {value!r}.")
    return result


def _shape(value: Any) -> Any:
    """Accept either a build123d shape or an active/inactive BuildPart."""
    if hasattr(value, "wrapped"):
        return value
    if hasattr(value, "part"):
        try:
            return value.part
        except Exception as exc:
            raise CadOperationError("The supplied BuildPart has no completed shape yet.") from exc
    raise CadOperationError(f"Expected a build123d shape or BuildPart, not {type(value).__name__}.")


def _publish_to_active_builder(source: Any, result: Any) -> Any:
    """Replace the active builder only when the helper edited its current part."""
    import build123d

    builder = build123d.BuildPart._get_context(log=False)
    if builder is None:
        return result
    try:
        active = builder.part
        source = _shape(source)
        is_current = bool(source.wrapped.IsSame(active.wrapped))
    except Exception:
        is_current = False
    if is_current and not active.wrapped.IsSame(result.wrapped):
        # add() would move a copy to each active location, which breaks topology
        # identity and hands every earlier feature's faces to the current feature.
        builder._add_to_context(result, clean=False, mode=build123d.Mode.REPLACE)
        return builder.part
    return result


def _record_finishing_report(report: dict[str, Any]) -> None:
    # The worker attaches this JSON data to the owning feature and emits a
    # warning check for unresolved blends. Keep the import lazy: cad.py is
    # also imported by kernel-free type consumers.
    from .modeling import record_finishing_report

    record_finishing_report(report)


# --- Imported files -------------------------------------------------------------------------


def configure_assets(directory: Path | None, units: str = "mm") -> None:
    """Point asset() at this build's staged copies and project units. The worker calls this before model code."""
    global _asset_dir, _asset_units
    _asset_dir = directory
    _asset_units = units


def asset(name: str, *, source_units: str | None = None) -> Any:
    """Load a file added with ``aad_import``: STEP, IGES, BREP, STL, or DXF/SVG as Imported2D.

    Files are copied into the job before the sandboxed build starts, and this is the only
    file access model code has. Geometry is converted into the project's units. STEP and
    IGES declare their own. For BREP, STL, DXF and SVG, ``source_units`` states what the
    file's numbers mean, and defaults to mm for BREP and STL and to the project's for sheets.
    Convert a DXF/SVG ``Imported2D`` to a ``Sheet2D`` with ``.to_sheet()`` before using it
    as the result of a 2D project.
    """
    if not isinstance(name, str) or not _ASSET_NAME.fullmatch(name) or name in {".", ".."}:
        raise CadOperationError(f"Asset name {name!r} must be a plain file name from aad_import.")
    if source_units not in {None, "mm", "in"}:
        raise CadOperationError(f"source_units must be 'mm' or 'in', not {source_units!r}.")
    path = _asset_dir / name if _asset_dir is not None else None
    if path is None or not path.is_file():
        raise CadOperationError(
            f"Asset {name!r} was not found. Add it with aad_import first. Files over 256 MiB are not available to builds."
        )
    suffix = path.suffix.lower()
    if suffix in {".dxf", ".svg"}:
        try:
            from aad_drafting import parse_dxf, parse_svg
        except ImportError as exc:
            raise CadOperationError("DXF and SVG assets need the aad_drafting package.") from exc
        parse = parse_dxf if suffix == ".dxf" else parse_svg
        return parse(path, units=source_units or _asset_units)
    if suffix in {".step", ".stp", ".iges", ".igs"} and source_units is not None:
        raise CadOperationError(f"{suffix[1:].upper()} files declare their own units, so source_units does not apply.")
    from .engine.fileio import import_file

    return import_file(path, path.parent, units=_asset_units, source_units=source_units).context.shape


# --- Direct edits on existing geometry ------------------------------------------------------


def _shapes(items: Any) -> list[Any]:
    if hasattr(items, "wrapped"):
        return [items]
    return list(items)


@dataclass
class BlendResult:
    """A shape plus the disposition of every edge requested by :func:`blend`.

    ``shape`` is also exposed as ``part`` so a result can be assigned directly
    to model.py's ``result``. Iteration supports ``shape, report = blend(...)``.
    """

    shape: Any
    report: dict[str, Any]

    @property
    def part(self) -> Any:
        return self.shape

    def __iter__(self) -> Iterator[Any]:
        yield self.shape
        yield self.report


def _edge_groups(edges: Any) -> list[list[tuple[int, Any]]]:
    """Keep explicit groups atomic; treat a flat selection as short-first singletons."""
    if hasattr(edges, "wrapped"):
        raw_groups = [[edges]]
        explicit_groups = False
    else:
        try:
            raw = list(edges)
        except TypeError as exc:
            raise CadOperationError("blend edges must be an Edge, iterable of edges, or nested edge groups.") from exc
        if not raw:
            raise CadOperationError("blend needs at least one edge.")
        explicit_groups = not all(hasattr(item, "wrapped") for item in raw)
        raw_groups = [list(group) for group in raw] if explicit_groups else [[item] for item in raw]
    groups: list[list[tuple[int, Any]]] = []
    index = 0
    for group in raw_groups:
        if not group:
            raise CadOperationError("blend edge groups cannot be empty.")
        indexed = []
        for item in group:
            if not hasattr(item, "wrapped") or not hasattr(item, "length"):
                raise CadOperationError(f"blend expected an Edge, not {type(item).__name__}.")
            indexed.append((index, item))
            index += 1
        # For a flat selector, a short-first order makes small local details
        # resolve before broad edges change the surrounding topology.
        indexed.sort(key=lambda pair: float(pair[1].length))
        groups.append(indexed)
    if not explicit_groups:
        groups.sort(key=lambda group: float(group[0][1].length))
    return groups


def _edge_signature(edge: Any) -> tuple[Any, ...]:
    box = edge.bounding_box()
    center = edge.center()
    return (
        edge.geom_type.name,
        float(edge.length),
        tuple(round(float(getattr(box.min, axis)), 6) for axis in "XYZ"),
        tuple(round(float(getattr(box.max, axis)), 6) for axis in "XYZ"),
        tuple(round(float(getattr(center, axis)), 6) for axis in "XYZ"),
    )


def _same_edge(left: Any, right: Any) -> bool:
    try:
        return bool(left.wrapped.IsSame(right.wrapped))
    except Exception:
        return False


def _find_current_edge(edge: Any, current_edges: list[Any], signature: tuple[Any, ...]) -> Any | None:
    for candidate in current_edges:
        if _same_edge(edge, candidate):
            return candidate
    # Boolean and fillet builders can wrap unchanged topology in a fresh
    # Python object. A geometric signature is a safe fallback only when it is
    # unique; ambiguity is surfaced as a failure instead of guessing.
    matches = []
    for candidate in current_edges:
        candidate_signature = _edge_signature(candidate)
        if candidate_signature[0] != signature[0]:
            continue
        if abs(candidate_signature[1] - signature[1]) > 1e-5:
            continue
        if candidate_signature[2:] == signature[2:]:
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def blend(shape: Any, edges: Any, radius: float, fallback: Sequence[float] = ()) -> BlendResult:
    """Blend edge groups from largest requested radius through explicit fallbacks.

    A single edge or a flat iterable is tried one edge at a time, shortest
    first. A nested iterable makes each inner group one atomic fillet request.
    Each group is attempted on the geometry left by earlier successful groups.
    ``fallback`` values must be positive and strictly decrease from ``radius``.

    Every requested edge appears in ``report["edges"]`` with ``applied``,
    ``reduced`` or ``failed`` status. Failed groups leave their source geometry
    intact and are recorded in feature metadata plus a build warning check.
    """
    from build123d import Shape
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

    source = _shape(shape)
    requested_radius = _positive(radius, "radius")
    try:
        fallback_values = list(fallback)
    except TypeError as exc:
        raise CadOperationError("blend fallback must be an iterable of explicit radii.") from exc
    reductions = [_positive(value, f"fallback[{index}]") for index, value in enumerate(fallback_values)]
    previous = requested_radius
    for index, value in enumerate(reductions):
        if value >= previous:
            raise CadOperationError("blend fallback radii must be explicit, unique and strictly decreasing.")
        previous = value
    groups = _edge_groups(edges)
    indexed_edges = {index: edge for group in groups for index, edge in group}
    signatures = {index: _edge_signature(edge) for index, edge in indexed_edges.items()}
    statuses: dict[int, dict[str, Any]] = {
        index: {
            "index": index,
            "group": group_index,
            "edge": {
                "curve": signatures[index][0],
                "length": signatures[index][1],
                "center": list(signatures[index][4]),
                "bounds": [list(signatures[index][2]), list(signatures[index][3])],
            },
            "requested_radius": requested_radius,
            "applied_radius": None,
            "status": "failed",
            "reason": "No blend attempt completed.",
        }
        for group_index, group in enumerate(groups)
        for index, _edge in group
    }
    current = source
    group_reports = []
    for group_index, group in enumerate(groups):
        current_edges = list(current.edges())
        mapped: list[tuple[int, Any]] = []
        for index, edge in group:
            match = _find_current_edge(edge, current_edges, signatures[index])
            if match is None:
                statuses[index]["reason"] = "The requested edge no longer maps uniquely after an earlier blend."
            else:
                mapped.append((index, match))
        if not mapped:
            group_reports.append({
                "group": group_index,
                "requested_edges": [index for index, _edge in group],
                "mapped_edges": [],
                "status": "failed",
                "attempts": [],
                "reason": "No requested edge mapped uniquely to the current shape.",
            })
            continue
        group_reason = "The kernel did not produce a valid fillet for this edge group."
        group_attempts = []
        successful_shape = None
        successful_radius = None
        for candidate_radius in (requested_radius, *reductions):
            maker = BRepFilletAPI_MakeFillet(current.wrapped)
            try:
                for _index, edge in mapped:
                    maker.Add(candidate_radius, edge.wrapped)
                maker.Build()
                if not maker.IsDone():
                    group_reason = f"The kernel reported IsDone=False at radius {candidate_radius:g}."
                    group_attempts.append({"radius": candidate_radius, "status": "failed", "reason": group_reason})
                    continue
                candidate = Shape.cast(maker.Shape())
                if candidate is None or not candidate.is_valid:
                    group_reason = f"The kernel returned an invalid shape at radius {candidate_radius:g}."
                    group_attempts.append({"radius": candidate_radius, "status": "invalid", "reason": group_reason})
                    continue
                if len(candidate.solids()) != len(current.solids()):
                    group_reason = f"The kernel changed the solid count at radius {candidate_radius:g}."
                    group_attempts.append({"radius": candidate_radius, "status": "invalid", "reason": group_reason})
                    continue
                successful_shape = candidate
                successful_radius = candidate_radius
                group_attempts.append({"radius": candidate_radius, "status": "applied", "reason": None})
                break
            except MemoryError:
                raise
            except OSError as exc:
                if exc.errno == errno.ENOMEM:
                    raise
                group_reason = f"{type(exc).__name__} at radius {candidate_radius:g}: {str(exc) or type(exc).__name__}"
                group_attempts.append({"radius": candidate_radius, "status": "failed", "reason": group_reason})
            except Exception as exc:
                group_reason = f"{type(exc).__name__} at radius {candidate_radius:g}: {str(exc) or type(exc).__name__}"
                group_attempts.append({"radius": candidate_radius, "status": "failed", "reason": group_reason})
        mapped_indices = {requested_index for requested_index, _mapped_edge in mapped}
        group_failed = len(group) - len(mapped)
        for index, _edge in group:
            if successful_shape is None:
                if index in mapped_indices:
                    statuses[index]["reason"] = group_reason
                continue
            if index not in mapped_indices:
                continue
            statuses[index]["applied_radius"] = successful_radius
            statuses[index]["status"] = "applied" if successful_radius == requested_radius else "reduced"
            statuses[index]["reason"] = None
        if successful_shape is not None:
            current = successful_shape
            group_status = "partial" if group_failed else statuses[mapped[0][0]]["status"]
        else:
            group_status = "failed"
        group_reports.append({
            "group": group_index,
            "requested_edges": [index for index, _edge in group],
            "mapped_edges": sorted(mapped_indices),
            "status": group_status,
            "applied_radius": successful_radius,
            "attempts": group_attempts,
            "reason": None if successful_shape is not None else group_reason,
        })

    edge_report = [statuses[index] for index in sorted(statuses)]
    failed = sum(item["status"] == "failed" for item in edge_report)
    reduced = sum(item["status"] == "reduced" for item in edge_report)
    applied = sum(item["status"] == "applied" for item in edge_report)
    report = {
        "operation": "blend",
        "requested_radius": requested_radius,
        "fallback": reductions,
        "status": "failed" if failed else "reduced" if reduced else "applied",
        "counts": {"applied": applied, "reduced": reduced, "failed": failed},
        "groups": group_reports,
        "edges": edge_report,
    }
    _record_finishing_report(report)
    current = _publish_to_active_builder(shape, current)
    return BlendResult(current, report)


def draft_faces(shape: Any, faces: Any, angle: float, neutral_plane: Any = None) -> Any:
    """Apply a degree-based mold draft to selected faces of one solid."""
    from build123d import Plane

    source = _shape(shape)
    selected = _shapes(faces)
    if not selected:
        raise CadOperationError("draft needs at least one face.")
    amount = _finite(angle, "angle")
    if amount == 0:
        raise CadOperationError("draft angle must not be zero.")
    plane = neutral_plane if neutral_plane is not None else Plane.XY
    solids = list(source.solids())
    if len(solids) != 1:
        raise CadOperationError("draft requires a shape with exactly one solid.")
    result = solids[0].draft(selected, plane, amount)
    result = _require_valid(result, "draft")
    return _publish_to_active_builder(shape, result)


def rib(shape: Any, profile: Any, direction: Any) -> Any:
    """Extrude a closed face/wire by a direction vector and fuse it as a rib."""
    from build123d import Face, Solid, Vector

    source = _shape(shape)
    if getattr(profile, "wrapped", None) is None:
        raise CadOperationError("rib profile must be a closed build123d Face or Wire.")
    section = profile if isinstance(profile, Face) else Face(profile)
    vector = Vector(direction)
    if vector.length <= 0:
        raise CadOperationError("rib direction must have nonzero length.")
    rib_solid = Solid.extrude(section, vector)
    result = source.fuse(rib_solid)
    result = _require_valid(result, "rib")
    return _publish_to_active_builder(shape, result)


def boss(
    shape: Any,
    center: Any,
    radius: float,
    height: float,
    *,
    axis: Any = (0, 0, 1),
    hole: float = 0.0,
    root_fillet: float = 0.0,
    hole_depth: float = 0.0,
) -> Any:
    """Fuse a round boss with an optional through-bore, drill depth and swept root blend."""
    from build123d import Axis, Edge, Face, Plane, Solid, Vector, Wire

    source = _shape(shape)
    outer_radius, boss_height = _positive(radius, "radius"), _positive(height, "height")
    bore = _nonnegative(hole, "hole")
    root = _nonnegative(root_fillet, "root_fillet")
    drill = _nonnegative(hole_depth, "hole_depth")
    if bore >= 2 * outer_radius:
        raise CadOperationError("boss hole diameter must be smaller than its outside diameter.")
    if root >= boss_height:
        raise CadOperationError("boss root_fillet must be smaller than boss height.")
    direction = Vector(axis)
    if direction.length <= 0:
        raise CadOperationError("boss axis must have nonzero length.")
    direction = direction.normalized()
    hole_radius = bore / 2
    edges = []
    p0 = Vector(hole_radius, 0, 0)
    p1 = Vector(outer_radius + root, 0, 0)
    p2 = Vector(outer_radius, 0, root)
    p3 = Vector(outer_radius, 0, boss_height)
    p4 = Vector(hole_radius, 0, boss_height)
    edges.append(Edge.make_line(p0, p1))
    if root:
        midpoint = Vector(
            outer_radius + root - root / math.sqrt(2),
            0,
            root - root / math.sqrt(2),
        )
        edges.append(Edge.make_three_point_arc(p1, midpoint, p2))
    else:
        p2 = p1
    edges.extend((Edge.make_line(p2, p3), Edge.make_line(p3, p4), Edge.make_line(p4, p0)))
    boss_local = Solid.revolve(Face(Wire(edges)), 360, Axis.Z)
    plane = Plane(origin=Vector(center), z_dir=direction)
    boss_world = boss_local.moved(plane.location)
    result = source.fuse(boss_world)
    if bore and drill:
        cutter = Solid.make_cylinder(
            hole_radius,
            boss_height + drill,
            Plane(origin=Vector(center) - direction * drill, z_dir=direction),
        )
        result = result.cut(cutter)
    result = _require_valid(result, "boss")
    return _publish_to_active_builder(shape, result)


def split_flange(
    shape: Any,
    seam: Any,
    *,
    width: float,
    thickness: float,
    bolt_diameter: float,
    bolt_count: int,
    bolt_axis: Any = (0, 0, 1),
    end_margin: float | None = None,
    gap: float = 0.0,
    hole_depth: float = 0.0,
) -> Any:
    """Add two rails along a straight housing split and drill a paired bolt pattern.

    ``seam`` is the centerline edge. The flange rails sit on either side of it;
    ``bolt_axis`` is normal to the mating flange face, and ``hole_depth`` is
    the extra depth the bores continue into the parent housing.
    """
    from build123d import Edge, Face, Plane, Solid, Vector, Wire

    source = _shape(shape)
    rail_width = _positive(width, "width")
    rail_thickness = _positive(thickness, "thickness")
    bolt_d = _positive(bolt_diameter, "bolt_diameter")
    split_gap = _nonnegative(gap, "gap")
    drill = _nonnegative(hole_depth, "hole_depth")
    if isinstance(bolt_count, bool) or not isinstance(bolt_count, int) or bolt_count < 2:
        raise CadOperationError("split_flange bolt_count must be an integer of at least 2.")
    if not isinstance(seam, Edge) or seam.geom_type.name != "LINE":
        raise CadOperationError("split_flange seam must be a straight Edge.")
    start, end = seam.start_point(), seam.end_point()
    tangent = (end - start).normalized()
    length = float(seam.length)
    margin = rail_width if end_margin is None else _nonnegative(end_margin, "end_margin")
    if 2 * margin >= length:
        raise CadOperationError("split_flange end margins must leave a positive bolt-pattern span.")
    axis = Vector(bolt_axis)
    if axis.length <= 0:
        raise CadOperationError("split_flange bolt_axis must have nonzero length.")
    axis = axis.normalized()
    across = axis.cross(tangent)
    if across.length <= 1e-8:
        raise CadOperationError("split_flange bolt_axis must be perpendicular to the seam.")
    across = across.normalized()
    if abs(float(axis.dot(tangent))) > 1e-6:
        raise CadOperationError("split_flange bolt_axis must be perpendicular to the seam.")
    # Holes sit on rail centerlines, so paired holes are one rail width plus the gap apart.
    if bolt_d >= rail_width:
        raise CadOperationError("split_flange bolt diameter must be smaller than the rail width.")

    rails = []
    hole_centers = []
    pitch = (length - 2 * margin) / (bolt_count - 1)
    for side in (-1, 1):
        rail_offset = -split_gap / 2 - rail_width if side < 0 else split_gap / 2
        rail_origin = start + across * rail_offset
        corners = [
            rail_origin,
            rail_origin + tangent * length,
            rail_origin + tangent * length + across * rail_width,
            rail_origin + across * rail_width,
        ]
        profile = Face(Wire.make_polygon(corners, close=True))
        rails.append(Solid.extrude(profile, axis * rail_thickness))
        for index in range(bolt_count):
            along = margin + index * pitch
            hole_centers.append(rail_origin + tangent * along + across * (rail_width / 2))
    result = source.fuse(*rails)
    for center in hole_centers:
        cutter = Solid.make_cylinder(
            bolt_d / 2,
            rail_thickness + drill,
            Plane(origin=center - axis * drill, z_dir=axis),
        )
        result = result.cut(cutter)
    result = _require_valid(result, "split_flange")
    return _publish_to_active_builder(shape, result)


def variable_fillet(shape: Any, edges: Any, start_radius: float, end_radius: float) -> Any:
    """Fillet edges with a radius that changes linearly from start to end of each edge."""
    from build123d import Shape
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

    r1, r2 = _positive(start_radius, "start_radius"), _positive(end_radius, "end_radius")
    selected = _shapes(edges)
    if not selected:
        raise CadOperationError("variable_fillet needs at least one edge.")
    maker = BRepFilletAPI_MakeFillet(shape.wrapped)
    for item in selected:
        maker.Add(r1, r2, item.wrapped)
    try:
        maker.Build()
        if not maker.IsDone():
            raise CadOperationError(f"The kernel could not fillet from {r1:g} to {r2:g}; try smaller radii.")
        result = Shape.cast(maker.Shape())
    except CadOperationError:
        raise
    except Exception as exc:
        raise CadOperationError(f"The kernel could not fillet from {r1:g} to {r2:g}: {exc}") from exc
    return _require_valid(result, "variable_fillet")


def press_pull(shape: Any, faces: Any, distance: float) -> Any:
    """Move planar faces along their outward normals: positive adds material, negative removes it."""
    from build123d import Solid, Vector

    amount = _finite(distance, "distance")
    if amount == 0:
        raise CadOperationError("press_pull distance must not be zero.")
    result = shape
    for item in _shapes(faces):
        if item.geom_type.name != "PLANE":
            raise CadOperationError(f"press_pull supports planar faces, not {item.geom_type.name}.")
        normal = item.normal_at()
        tool = Solid.extrude(item, Vector(normal.X, normal.Y, normal.Z) * amount)
        result = result + tool if amount > 0 else result - tool
    if not result.solids():
        raise CadOperationError(f"press_pull by {amount:g} removed the whole body.")
    return _require_valid(result, "press_pull")


def delete_faces(shape: Any, faces: Any) -> Any:
    """Defeature: remove faces such as fillets, holes or bosses and heal the solid around them."""
    from build123d import Shape
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing

    selected = _shapes(faces)
    if not selected:
        raise CadOperationError("delete_faces needs at least one face.")
    maker = BRepAlgoAPI_Defeaturing()
    maker.SetShape(shape.wrapped)
    for item in selected:
        maker.AddFaceToRemove(item.wrapped)
    maker.SetRunParallel(False)
    maker.Build()
    if not maker.IsDone():
        raise CadOperationError("The kernel could not heal the solid around the removed faces.")
    result = _require_valid(Shape.cast(maker.Shape()), "delete_faces")
    # The kernel can report success and return the solid untouched when no healing exists.
    if len(result.faces()) == len(shape.faces()) and math.isclose(result.volume, shape.volume, rel_tol=1e-9):
        raise CadOperationError("The kernel left the solid unchanged, so these faces cannot be removed this way.")
    return result


def sew_solid(faces: Iterable[Any], tolerance: float = 1e-3) -> Any:
    """Sew surface faces into a shell, and into a solid when that shell closes."""
    from build123d import Shape, Solid
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing

    sewing = BRepBuilderAPI_Sewing(_positive(tolerance, "tolerance"))
    count = 0
    for item in faces:
        sewing.Add(item.wrapped)
        count += 1
    if not count:
        raise CadOperationError("sew_solid needs at least one face.")
    sewing.Perform()
    sewn = Shape.cast(sewing.SewedShape())
    shells = list(sewn.shells())
    if len(shells) == 1 and BRep_Tool.IsClosed_s(shells[0].wrapped):
        return _require_valid(Solid(shells[0]), "sew_solid")
    return sewn


def pattern_along(shape: Any, path: Any, count: int, *, align: bool = True) -> list[Any]:
    """Copy a shape along a curve at equal arc-length spacing, including both ends of an open path.

    The shape's current position counts as the path start, so place it there first.
    """
    from build123d import Location

    if isinstance(count, bool) or not isinstance(count, int) or count < 2:
        raise CadOperationError("pattern_along count must be an integer of at least 2.")
    closed = bool(path.is_closed)
    stops = count if closed else count - 1
    first = path.location_at(0.0) if align else Location(path.position_at(0.0))
    copies = []
    for index in range(count):
        position = index / stops
        target = path.location_at(position) if align else Location(path.position_at(position))
        copies.append(shape.moved(target * first.inverse()))
    return copies


def exploded(shape: Any, factor: float = 1.0) -> Any:
    """Return a shape with its bodies moved radially from the shared center for an exploded view."""
    from build123d import Compound, Location, Vector

    amount = _finite(factor, "factor")
    bodies = list(shape.solids())
    if len(bodies) < 2:
        raise CadOperationError("exploded needs at least two solids.")
    centers = [body.center() for body in bodies]
    middle = sum(centers, Vector(0, 0, 0)) / len(centers)
    moved = [body.moved(Location((center - middle) * amount)) for body, center in zip(bodies, centers, strict=True)]
    return Compound(label="exploded", children=moved)


# --- Assemblies ------------------------------------------------------------------------------


@dataclass
class Component:
    name: str
    shape: Any
    part_number: str | None = None
    material: str | None = None
    description: str | None = None


class Assembly:
    """Named components, a bill of materials, exploded output and motion clearance.

    Add components, then assign ``result = asm``. Position parts with ``at=`` (any build123d
    Location), or with build123d joints before adding them. Reusing one ``part_number`` makes
    rows in the BOM combine; geometry with a different volume under one number is refused.
    """

    def __init__(self, name: str = "assembly") -> None:
        if not isinstance(name, str) or not name.strip():
            raise CadOperationError("Assembly name must be non-empty text.")
        self.name = name
        self.components: list[Component] = []

    def add(
        self,
        name: str,
        shape: Any,
        *,
        at: Any = None,
        part_number: str | None = None,
        material: str | None = None,
        description: str | None = None,
    ) -> Any:
        from build123d import Location

        if not isinstance(name, str) or not name.strip():
            raise CadOperationError("Component name must be non-empty text.")
        if any(item.name == name for item in self.components):
            raise CadOperationError(f"Component name {name!r} is already used in this assembly.")
        if hasattr(shape, "part") and not hasattr(shape, "wrapped"):
            shape = shape.part
        if not hasattr(shape, "moved"):
            raise CadOperationError(f"Component {name!r} is {type(shape).__name__}, not a build123d shape.")
        # moved() copies, so the same source shape can be added more than once under different names.
        placed = shape.moved(at if at is not None else Location())
        placed.label = name
        self.components.append(Component(name, placed, part_number, material, description))
        return placed

    def __getitem__(self, name: str) -> Any:
        for item in self.components:
            if item.name == name:
                return item.shape
        raise KeyError(name)

    def compound(self) -> Any:
        from build123d import Compound

        if not self.components:
            raise CadOperationError(f"Assembly {self.name!r} has no components.")
        return Compound(label=self.name, children=[item.shape for item in self.components])

    def bom(self) -> list[dict[str, Any]]:
        """Rows grouped by part number, or by name when a component has no number."""
        rows: dict[str, dict[str, Any]] = {}
        for item in self.components:
            key = item.part_number or item.name
            volume = float(item.shape.volume)
            row = rows.get(key)
            if row is None:
                rows[key] = {
                    "item": len(rows) + 1,
                    "part_number": item.part_number or item.name,
                    "description": item.description or item.name,
                    "material": item.material,
                    "quantity": 1,
                    "unit_volume": volume,
                    "components": [item.name],
                }
                continue
            if not math.isclose(row["unit_volume"], volume, rel_tol=1e-6, abs_tol=1e-9):
                raise CadOperationError(
                    f"Part number {key!r} is used for different geometry "
                    f"(volumes {row['unit_volume']:g} and {volume:g}); use separate numbers."
                )
            if row["material"] != item.material:
                raise CadOperationError(
                    f"Part number {key!r} is used with materials {row['material']!r} and {item.material!r}; use separate numbers."
                )
            row["quantity"] += 1
            row["components"].append(item.name)
        return list(rows.values())

    def exploded(self, factor: float = 1.0) -> Any:
        return exploded(self.compound(), factor)

    def interference(self, threshold: float = 1e-6) -> list[dict[str, Any]]:
        """Named component pairs whose solids overlap by more than ``threshold`` cubic units."""
        found = []
        for index, first in enumerate(self.components):
            for second in self.components[index + 1 :]:
                overlap = overlap_volume(first.shape, second.shape)
                if overlap > threshold:
                    found.append({"a": first.name, "b": second.name, "volume": overlap})
        return found

    def record(self) -> dict[str, Any]:
        return {"name": self.name, "components": [item.name for item in self.components], "bom": self.bom()}


def assembly(name: str = "assembly") -> Assembly:
    """Start a named assembly. Assign it to ``result``."""
    return Assembly(name)


def overlap_volume(first: Any, second: Any) -> float:
    """Shared solid volume of two shapes, using disjoint bounding boxes as a fast exit."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from build123d import Shape

    one, two = first.bounding_box(), second.bounding_box()
    if any(
        getattr(one.max, axis) < getattr(two.min, axis) or getattr(two.max, axis) < getattr(one.min, axis)
        for axis in "XYZ"
    ):
        return 0.0
    common = BRepAlgoAPI_Common(first.wrapped, second.wrapped)
    common.Build()
    if not common.IsDone():
        raise CadOperationError("The kernel could not compute a boolean common for these shapes.")
    shared = Shape.cast(common.Shape())
    return float(shared.volume) if shared is not None else 0.0


def motion_clearance(moving: Any, fixed: Any, locations: Sequence[Any]) -> dict[str, Any]:
    """Kinematic sweep: test finite poses of a moving part against fixed parts.

    This samples only the given poses. It says nothing about collisions between them, loads,
    or dynamics, so space the poses according to the smallest gap that matters.
    """
    if not locations:
        raise CadOperationError("motion_clearance needs at least one location.")
    poses = []
    for index, location in enumerate(locations):
        placed = moving.moved(location)
        overlap = overlap_volume(placed, fixed)
        gap = 0.0 if overlap > 0 else float(placed.distance_to(fixed))
        poses.append({"pose": index, "overlap_volume": overlap, "clearance": gap})
    worst = min(poses, key=lambda pose: (-pose["overlap_volume"], pose["clearance"]))
    return {
        "poses": poses,
        "collides": any(pose["overlap_volume"] > 0 for pose in poses),
        "worst_pose": worst["pose"],
        "min_clearance": worst["clearance"],
        "definition": "finite_pose_samples_not_continuous_sweep",
    }


# --- Sheet metal -----------------------------------------------------------------------------


@dataclass
class SheetMetalPart:
    """A folded sheet metal strip and its flat pattern, derived from one bend sequence."""

    folded: Any
    bends: list[dict[str, float]]
    flat_length: float
    width: float
    thickness: float
    holes_flat: list[tuple[float, float, float]] = field(default_factory=list)

    def flat_sheet(self, name: str = "flat_pattern", units: str = "mm") -> Any:
        """The flat pattern as an editable Sheet2D: cut outline, holes and score bend lines."""
        from aad_drafting import sheet2d

        sheet = sheet2d(name, units=units)
        sheet.rectangle("outline", self.flat_length, self.width, origin=(0, 0))
        for index, (x, y, diameter) in enumerate(self.holes_flat):
            sheet.circle(f"hole{index}", (x, y), diameter / 2)
        for index, bend in enumerate(self.bends):
            x = bend["flat_center"]
            sheet.line(f"bend{index}", (x, 0), (x, self.width), layer="score")
        return sheet


def sheet_metal(
    segments: Sequence[float],
    bend_angles: Sequence[float],
    *,
    width: float,
    thickness: float,
    inner_radius: float,
    k_factor: float = 0.44,
    holes: Sequence[tuple[int, float, float, float]] = (),
) -> SheetMetalPart:
    """Build a folded strip and its flat pattern from flange lengths and bend angles.

    ``segments`` are flat lengths between bend tangents, ``bend_angles`` are signed degrees
    (positive bends toward +Z), one fewer than segments. Each hole is ``(segment, along,
    across, diameter)``, measured from the segment's start and the strip's Y=0 edge.
    Allowance is ``angle * (inner_radius + k_factor * thickness)``. The k-factor is a stated
    assumption here, so check it against the fabricator's bend table.
    """
    from build123d import Axis, Face, Plane, Solid, Vector, Wire

    t, r, w = _positive(thickness, "thickness"), _positive(inner_radius, "inner_radius"), _positive(width, "width")
    k = _finite(k_factor, "k_factor")
    if not 0 <= k <= 1:
        raise CadOperationError("k_factor must be between 0 and 1.")
    lengths = [_positive(value, f"segments[{index}]") for index, value in enumerate(segments)]
    angles = [_finite(value, f"bend_angles[{index}]") for index, value in enumerate(bend_angles)]
    if not lengths or len(angles) != len(lengths) - 1:
        raise CadOperationError("sheet_metal needs at least one segment and exactly one fewer bend angle.")
    if any(angle == 0 or abs(angle) >= 180 for angle in angles):
        raise CadOperationError("Each bend angle must be nonzero and under 180 degrees.")

    def skins(px: float, pz: float, direction: float) -> tuple[Any, Any]:
        """The two sheet surfaces at one point on the mid-surface, in the XZ plane."""
        nx, nz = -math.sin(direction), math.cos(direction)
        return Vector(px + nx * t / 2, 0, pz + nz * t / 2), Vector(px - nx * t / 2, 0, pz - nz * t / 2)

    across_width = Vector(0, w, 0)
    mid_radius = r + t / 2
    x, z, heading, flat_x = 0.0, 0.0, 0.0, 0.0
    frames: list[tuple[float, float, float]] = []
    starts: list[float] = []
    pieces: list[Any] = []
    bends: list[dict[str, float]] = []
    for index, length in enumerate(lengths):
        starts.append(flat_x)
        frames.append((x, z, heading))
        end_x, end_z = x + length * math.cos(heading), z + length * math.sin(heading)
        top0, bottom0 = skins(x, z, heading)
        top1, bottom1 = skins(end_x, end_z, heading)
        pieces.append(Solid.extrude(Face(Wire.make_polygon([top0, top1, bottom1, bottom0], close=True)), across_width))
        flat_x += length
        x, z = end_x, end_z
        if index == len(angles):
            break
        angle = math.radians(angles[index])
        sign = 1.0 if angle > 0 else -1.0
        center_x = x - sign * math.sin(heading) * mid_radius
        center_z = z + sign * math.cos(heading) * mid_radius
        end_cap = Face(Wire.make_polygon([top1, bottom1, bottom1 + across_width, top1 + across_width], close=True))
        pieces.append(
            Solid.revolve(end_cap, math.degrees(abs(angle)), Axis((center_x, 0, center_z), (0, -sign, 0)))
        )
        allowance = abs(angle) * (r + k * t)
        bends.append(
            {
                "angle_deg": angles[index],
                "inner_radius": r,
                "k_factor": k,
                "allowance": allowance,
                "flat_center": flat_x + allowance / 2,
            }
        )
        flat_x += allowance
        heading += angle
        x = center_x + sign * math.sin(heading) * mid_radius
        z = center_z - sign * math.cos(heading) * mid_radius

    folded = pieces[0]
    for piece in pieces[1:]:
        folded = folded + piece

    holes_flat: list[tuple[float, float, float]] = []
    for hole in holes:
        segment, along, across, diameter = hole
        if isinstance(segment, bool) or not isinstance(segment, int) or not 0 <= segment < len(lengths):
            raise CadOperationError(f"Hole segment {segment!r} is not a segment index.")
        d = _positive(diameter, "hole diameter")
        if not (d / 2 <= along <= lengths[segment] - d / 2 and d / 2 <= across <= w - d / 2):
            raise CadOperationError(f"Hole {hole!r} does not fit inside segment {segment}.")
        fx, fz, fheading = frames[segment]
        normal = Vector(-math.sin(fheading), 0, math.cos(fheading))
        origin = Vector(fx + along * math.cos(fheading), across, fz + along * math.sin(fheading)) - normal * t
        folded = folded - Solid.make_cylinder(d / 2, 2 * t, Plane(origin=origin, z_dir=normal))
        holes_flat.append((starts[segment] + along, across, d))

    _require_valid(folded, "sheet_metal")
    if len(folded.solids()) != 1:
        raise CadOperationError("The folded strip self-intersects or came apart; check the bends and flange lengths.")
    return SheetMetalPart(folded, bends, flat_x, w, t, holes_flat)


__all__ = [
    "Assembly",
    "BlendResult",
    "CadOperationError",
    "Component",
    "SheetMetalPart",
    "assembly",
    "asset",
    "blend",
    "boss",
    "delete_faces",
    "draft_faces",
    "exploded",
    "motion_clearance",
    "overlap_volume",
    "pattern_along",
    "press_pull",
    "rib",
    "sew_solid",
    "sheet_metal",
    "split_flange",
    "variable_fillet",
]
