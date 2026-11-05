"""Geometric hard checks for a built B-rep shape."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, fields
from math import dist, floor, fsum, hypot, isfinite
from operator import index as integer_index
from pathlib import Path
import tempfile
from typing import Any

from .types import Check, ModelContext


@dataclass(frozen=True)
class GateTolerances:
    """Comparison tolerances; length, area, and volume values are in mm units."""

    min_edge_length_mm: float = 0.01
    min_face_area_mm2: float = 0.001
    min_face_width_mm: float = 0.001
    step_volume_abs_mm3: float = 1e-6
    step_volume_rel: float = 1e-6
    mesh_vertex_weld_mm: float = 1e-6
    mesh_triangle_area_mm2: float = 1e-12
    mesh_deflection_mm: float = 0.1
    mesh_angular_deflection_rad: float = 0.35


_GEOMETRY_CHECK_IDS = (
    "brep_valid",
    "closed_shells",
    "solid_count",
    "edge_quality",
    "face_quality",
    "step_volume_drift",
    "mesh_watertight",
)
_LENGTH_TO_MM = {"mm": 1.0, "in": 25.4}


def hard_checks(
    ctx: ModelContext,
    tolerances: GateTolerances | Mapping[str, float] | None = None,
) -> list[Check]:
    """Run error-level geometry checks; unavailable checks are error-level skips.

    ``tolerances`` may be a :class:`GateTolerances` or a mapping of its fields.
    Length, area, and volume thresholds use millimetres regardless of the
    project's declared units.
    """
    config, config_error = _normalize_tolerances(tolerances)
    checks: list[Check] = []
    if ctx.shape is None:
        checks.append(
            _check(
                "shape_present",
                "fail",
                "The model did not produce a B-rep shape.",
                expected="a non-empty B-rep shape",
                actual="no shape",
            )
        )
        if config_error is not None:
            checks.append(_tolerance_error(config_error))
        checks.extend(_unavailable_geometry_checks("ctx.shape is None"))
        return checks

    checks.append(
        _check(
            "shape_present",
            "pass",
            "A shape object is available for the hard checks.",
            expected="a B-rep shape object",
            actual=type(ctx.shape).__name__,
        )
    )
    if config_error is not None:
        checks.append(_tolerance_error(config_error))

    unit_scale = _LENGTH_TO_MM.get(ctx.units) if isinstance(ctx.units, str) else None
    if unit_scale is None:
        checks.append(
            _check(
                "gate_units",
                "fail",
                "The configured geometric thresholds cannot be interpreted for this unit system.",
                expected="ctx.units is 'mm' or 'in'",
                actual=repr(ctx.units),
            )
        )

    checks.append(_brep_validity_check(ctx.shape))
    solid_check, multiple_solids_check = _solid_count_checks(ctx.shape, ctx.kind)
    checks.append(solid_check)
    checks.append(_closed_shell_check(ctx.shape))
    if multiple_solids_check is not None:
        checks.append(multiple_solids_check)

    unavailable_reason = config_error or (
        f"ctx.units={ctx.units!r} is unsupported; thresholds are declared in mm"
        if unit_scale is None
        else None
    )
    if unavailable_reason is not None:
        checks.extend(_unavailable_tolerance_checks(unavailable_reason))
        return checks

    assert config is not None
    assert unit_scale is not None
    checks.append(_edge_quality_check(ctx.shape, config, unit_scale))
    checks.append(_face_quality_check(ctx.shape, config, unit_scale))
    checks.append(_step_volume_check(ctx, config, unit_scale))
    checks.append(_mesh_watertight_check(ctx.shape, config, unit_scale))
    return checks


def _normalize_tolerances(
    tolerances: GateTolerances | Mapping[str, float] | None,
) -> tuple[GateTolerances | None, str | None]:
    if tolerances is None:
        candidate = GateTolerances()
    elif isinstance(tolerances, GateTolerances):
        candidate = tolerances
    elif isinstance(tolerances, Mapping):
        names = {item.name for item in fields(GateTolerances)}
        unknown = sorted((name for name in tolerances if name not in names), key=repr)
        if unknown:
            return None, f"unknown tolerance field(s): {', '.join(map(repr, unknown))}"
        candidate = GateTolerances(**dict(tolerances))
    else:
        return None, "tolerances must be GateTolerances, a field mapping, or None"

    positive = {"mesh_deflection_mm", "mesh_angular_deflection_rad"}
    values: dict[str, float] = {}
    for item in fields(GateTolerances):
        raw = getattr(candidate, item.name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None, f"{item.name} must be a finite number"
        try:
            value = float(raw)
        except (OverflowError, ValueError):
            return None, f"{item.name} must be a finite number"
        if not isfinite(value):
            return None, f"{item.name} must be finite"
        if item.name in positive and value <= 0:
            return None, f"{item.name} must be greater than zero"
        if item.name not in positive and value < 0:
            return None, f"{item.name} must be zero or greater"
        values[item.name] = value
    return GateTolerances(**values), None


def _tolerance_error(reason: str) -> Check:
    return _check(
        "gate_tolerances",
        "fail",
        "The geometric gate tolerances are invalid; threshold-based checks cannot run.",
        expected="finite, non-negative values with positive mesh deflections",
        actual=reason,
    )


def _unavailable_geometry_checks(reason: str) -> list[Check]:
    return [
        _skip(check_id, f"not run because {reason}")
        for check_id in _GEOMETRY_CHECK_IDS
    ]


def _unavailable_tolerance_checks(reason: str) -> list[Check]:
    return [
        _skip(check_id, f"not run because {reason}")
        for check_id in (
            "edge_quality",
            "face_quality",
            "step_volume_drift",
            "mesh_watertight",
        )
    ]


def _skip(check_id: str, reason: str, expected: str | None = None) -> Check:
    return _check(
        check_id,
        "skip",
        f"The {check_id.replace('_', ' ')} check could not run: {reason}.",
        expected=expected,
        actual=reason,
    )


def _check(
    check_id: str,
    status: str,
    message: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
    level: str = "error",
    heuristic: bool | None = None,
    assumptions: str | None = None,
) -> Check:
    return Check(
        id=check_id,
        suite="hard",
        level=level,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        message=message,
        expected=expected,
        actual=actual,
        heuristic=heuristic,
        assumptions=assumptions,
    )


def _brep_validity_check(shape: Any) -> Check:
    try:
        from OCP.BRepCheck import BRepCheck_Analyzer

        analyzer_valid = bool(BRepCheck_Analyzer(shape.wrapped).IsValid())
        shape_valid = bool(shape.is_valid)  # build123d exposes this as a property.
    except Exception as exc:
        reason = _exception_text(exc)
        return _skip(
            "brep_valid",
            f"BRepCheck_Analyzer or Shape.is_valid raised {reason}",
            expected="BRepCheck_Analyzer.IsValid() and Shape.is_valid are both true",
        )

    valid = analyzer_valid and shape_valid
    return _check(
        "brep_valid",
        "pass" if valid else "fail",
        "BRepCheck_Analyzer and Shape.is_valid both report a valid B-rep."
        if valid
        else "BRepCheck_Analyzer or Shape.is_valid reports an invalid B-rep.",
        expected="BRepCheck_Analyzer.IsValid()=true; Shape.is_valid=true",
        actual=(
            f"BRepCheck_Analyzer.IsValid()={str(analyzer_valid).lower()}; "
            f"Shape.is_valid={str(shape_valid).lower()}"
        ),
    )


def _solid_count_checks(shape: Any, kind: str) -> tuple[Check, Check | None]:
    if not isinstance(kind, str) or kind not in {"part", "assembly"}:
        return (
            _skip(
                "solid_count",
                f"no 3D body-count policy is defined for kind={kind!r}",
                expected="kind is 'part' or 'assembly'",
            ),
            None,
        )
    expected = "exactly 1 solid for a part" if kind == "part" else "at least 1 solid for an assembly"
    try:
        solids = list(shape.solids())
    except Exception as exc:
        reason = _exception_text(exc)
        return (
            _skip("solid_count", f"solid enumeration raised {reason}", expected=expected),
            None,
        )

    count = len(solids)
    passed = count == 1 if kind == "part" else count >= 1
    count_check = _check(
        "solid_count",
        "pass" if passed else "fail",
        f"The {kind} contains {count} solid(s); "
        + ("the body-count policy is satisfied." if passed else "the body-count policy is not satisfied."),
        expected=expected,
        actual=f"{count} solid(s)",
    )
    if count <= 1:
        return count_check, None

    warning = _check(
        "multiple_solids",
        "warn",
        f"The {kind} contains {count} solids; their intended relationship has not been checked.",
        expected="review the body count and specify any required component relationships",
        actual=f"{count} solids",
        level="warn",
        heuristic=True,
        assumptions="Multiple solids can be intentional in an assembly; count alone does not establish intent or clearance.",
    )
    return count_check, warning


def _closed_shell_check(shape: Any) -> Check:
    try:
        from OCP.BRep import BRep_Tool

        solids = list(shape.solids())
        shells = list(shape.shells())
    except Exception as exc:
        reason = _exception_text(exc)
        return _skip(
            "closed_shells",
            f"solid or shell enumeration raised {reason}",
            expected="at least one shell and every shell closed",
        )

    if not shells:
        if solids:
            return _check(
                "closed_shells",
                "fail",
                "No shell topology was found for a shape containing solids.",
                expected="at least one shell and every shell closed",
                actual=f"solids={len(solids)}; shells=0",
            )
        return _skip(
            "closed_shells",
            "the shape contains no shells to inspect",
            expected="at least one shell and every shell closed",
        )

    closed: list[bool] = []
    try:
        for shell in shells:
            closed.append(bool(BRep_Tool.IsClosed_s(shell.wrapped)))
    except Exception as exc:
        reason = _exception_text(exc)
        return _skip(
            "closed_shells",
            f"BRep_Tool.IsClosed_s could not inspect every shell: {reason}",
            expected="every shell closed",
        )

    open_indices = [index + 1 for index, is_closed in enumerate(closed) if not is_closed]
    all_closed = not open_indices
    return _check(
        "closed_shells",
        "pass" if all_closed else "fail",
        "Every shell is closed."
        if all_closed
        else f"Found {len(open_indices)} open shell(s).",
        expected="every shell closed",
        actual=(
            f"shells={len(shells)}; closed={len(shells) - len(open_indices)}; "
            f"open={len(open_indices)}; open_indices={open_indices}"
        ),
    )


def _edge_quality_check(shape: Any, tolerance: GateTolerances, unit_scale: float) -> Check:
    threshold = tolerance.min_edge_length_mm
    try:
        from OCP.BRep import BRep_Tool
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.TopoDS import TopoDS

        # build123d Shape.edges() filters out edges marked degenerate by OCCT.
        # entities() exposes the unique underlying edges without that filter.
        edges = list(shape.entities("Edge"))
        if not edges:
            return _skip(
                "edge_quality",
                "the shape contains no edges to measure",
                expected=f"no degenerate edges; every edge length > {_fmt(threshold)} mm",
            )

        lengths: list[float] = []
        degenerate_count = 0
        for raw_edge in edges:
            edge = TopoDS.Edge(raw_edge)
            degenerate_count += int(bool(BRep_Tool.Degenerated_s(edge)))
            properties = GProp_GProps()
            BRepGProp.LinearProperties_s(edge, properties)
            length = float(properties.Mass()) * unit_scale
            if not isfinite(length) or length < 0:
                raise ValueError("an edge length was negative or not finite")
            lengths.append(length)
    except Exception as exc:
        reason = _exception_text(exc)
        return _skip(
            "edge_quality",
            f"edge enumeration or measurement raised {reason}",
            expected=f"no degenerate edges; every edge length > {_fmt(threshold)} mm",
        )

    small_count = sum(length <= threshold for length in lengths)
    passed = degenerate_count == 0 and small_count == 0
    return _check(
        "edge_quality",
        "pass" if passed else "fail",
        f"All {len(lengths)} edges are non-degenerate and above the configured threshold."
        if passed
        else f"Found {degenerate_count} degenerate edge(s) and {small_count} edge(s) at or below the configured threshold.",
        expected=f"no degenerate edges; each edge length > {_fmt(threshold)} mm",
        actual=(
            f"edges={len(lengths)}; degenerate_edges={degenerate_count}; "
            f"edges_at_or_below_threshold={small_count}; "
            f"minimum_edge_length={_fmt(min(lengths))} mm; "
            f"configured_threshold={_fmt(threshold)} mm"
        ),
        heuristic=True,
        assumptions="The configurable small-edge threshold is a numerical topology heuristic, not a manufacturing limit.",
    )


def _face_quality_check(shape: Any, tolerance: GateTolerances, unit_scale: float) -> Check:
    area_threshold = tolerance.min_face_area_mm2
    width_threshold = tolerance.min_face_width_mm
    expected = (
        f"every face area > {_fmt(area_threshold)} mm² and "
        f"2 × area / summed edge lengths > {_fmt(width_threshold)} mm"
    )
    try:
        faces = list(shape.faces())
        if not faces:
            return _skip(
                "face_quality",
                "the shape contains no faces to measure",
                expected=expected,
            )

        areas: list[float] = []
        widths: list[float] = []
        for face in faces:
            area = float(face.area) * unit_scale**2
            if not isfinite(area) or area < 0:
                raise ValueError("a face area was negative or not finite")
            areas.append(area)
            if area == 0:
                widths.append(0.0)
                continue
            lengths = [float(edge.length) * unit_scale for edge in face.edges()]
            if not lengths or any(not isfinite(length) or length < 0 for length in lengths):
                raise ValueError("a face has no measurable finite edge lengths")
            perimeter = sum(lengths)
            if not isfinite(perimeter) or perimeter <= 0:
                raise ValueError("a face's summed edge lengths are not positive and finite")
            width = 2 * area / perimeter
            if not isfinite(width):
                raise ValueError("a face width proxy was not finite")
            widths.append(width)
    except Exception as exc:
        reason = _exception_text(exc)
        return _skip(
            "face_quality",
            f"face enumeration or measurement raised {reason}",
            expected=expected,
        )

    degenerate_count = sum(area == 0 for area in areas)
    small_area_count = sum(0 < area <= area_threshold for area in areas)
    sliver_count = sum(area > 0 and width <= width_threshold for area, width in zip(areas, widths))
    passed = degenerate_count == 0 and small_area_count == 0 and sliver_count == 0
    return _check(
        "face_quality",
        "pass" if passed else "fail",
        f"All {len(areas)} faces exceed the configured area and sliver-width thresholds."
        if passed
        else (
            f"Found {degenerate_count} zero-area face(s), {small_area_count} small-area face(s), "
            f"and {sliver_count} face(s) at or below the sliver-width threshold; counts can overlap."
        ),
        expected=expected,
        actual=(
            f"faces={len(areas)}; zero_area_faces={degenerate_count}; "
            f"small_area_faces={small_area_count}; sliver_faces={sliver_count}; "
            f"minimum_face_area={_fmt(min(areas))} mm²; "
            f"minimum_face_width_proxy={_fmt(min(widths))} mm; "
            f"configured_area_threshold={_fmt(area_threshold)} mm²; "
            f"configured_width_threshold={_fmt(width_threshold)} mm"
        ),
        heuristic=True,
        assumptions=(
            "2 × area / the sum of distinct topological edge lengths is a sliver proxy; "
            "it depends on face trimming and is not a manufacturing limit."
        ),
    )


def _step_volume_check(ctx: ModelContext, tolerance: GateTolerances, unit_scale: float) -> Check:
    threshold_abs = tolerance.step_volume_abs_mm3
    threshold_rel = tolerance.step_volume_rel
    expected = (
        f"positive STEP volume; absolute drift <= max({_fmt(threshold_abs)} mm³, "
        f"{_fmt(threshold_rel)} × source volume)"
    )
    try:
        from build123d import Unit, export_step

        source_volume = fsum(float(solid.volume) for solid in ctx.shape.solids()) * unit_scale**3
        if not isfinite(source_volume):
            raise ValueError("the source volume was not finite")
        if source_volume <= 0:
            return _skip(
                "step_volume_drift",
                f"source volume is {_fmt(source_volume)} mm³; a positive volume is required for comparison",
                expected=expected,
            )

        job_dir = Path(ctx.job_dir)
        job_dir.mkdir(exist_ok=True)
        unit = Unit.IN if ctx.units == "in" else Unit.MM
        with tempfile.TemporaryDirectory(prefix="aiad-gate-", dir=job_dir) as temporary:
            step_path = Path(temporary) / "roundtrip.step"
            exported = export_step(ctx.shape, step_path, unit=unit)
            if not exported:
                return _skip("step_volume_drift", "export_step returned false", expected=expected)
            if not step_path.is_file() or step_path.stat().st_size == 0:
                return _skip(
                    "step_volume_drift",
                    "export_step did not create a non-empty STEP file",
                    expected=expected,
                )
            roundtrip_shape = _import_step_shape(step_path)
            # build123d's STEP importer returns geometry in mm even when the
            # exported document declared inch coordinates.
            roundtrip_volume = fsum(float(solid.volume) for solid in roundtrip_shape.solids())
            if not isfinite(roundtrip_volume):
                raise ValueError("the re-imported STEP volume was not finite")

        drift = abs(roundtrip_volume - source_volume)
        threshold = max(threshold_abs, threshold_rel * source_volume)
        relative_value = drift / source_volume
        if not all(isfinite(value) for value in (drift, threshold, relative_value)):
            raise ValueError("the STEP drift or comparison threshold was not finite")
        relative_drift = _fmt(relative_value)
        passed = roundtrip_volume > 0 and drift <= threshold
    except Exception as exc:
        reason = _exception_text(exc)
        return _skip(
            "step_volume_drift",
            f"STEP export, re-import, or volume measurement raised {reason}",
            expected=expected,
        )

    expected = (
        f"positive STEP volume; absolute drift <= max({_fmt(threshold_abs)} mm³, "
        f"{_fmt(threshold_rel)} × {_fmt(source_volume)} mm³) = {_fmt(threshold)} mm³"
    )
    return _check(
        "step_volume_drift",
        "pass" if passed else "fail",
        "STEP re-import volume drift is within the configured threshold."
        if passed
        else "STEP re-import volume is not positive or its drift exceeds the configured threshold.",
        expected=expected,
        actual=(
            f"source_volume={_fmt(source_volume)} mm³; "
            f"step_volume={_fmt(roundtrip_volume)} mm³; "
            f"absolute_drift={_fmt(drift)} mm³; relative_drift={relative_drift}; "
            f"configured_absolute_threshold={_fmt(threshold_abs)} mm³; "
            f"configured_relative_threshold={_fmt(threshold_rel)}"
        ),
    )


def _import_step_shape(path: Path) -> Any:
    """Isolated to make the actual STEP import boundary fault-injectable in tests."""
    from build123d import import_step

    return import_step(path)


def _mesh_watertight_check(shape: Any, tolerance: GateTolerances, unit_scale: float) -> Check:
    try:
        vertices, triangles = _fresh_mesh_arrays(
            shape,
            tolerance.mesh_deflection_mm / unit_scale,
            tolerance.mesh_angular_deflection_rad,
        )
    except Exception as exc:
        reason = _exception_text(exc)
        return _skip(
            "mesh_watertight",
            f"shape tessellation raised {reason}; "
            f"configured_deflection={_fmt(tolerance.mesh_deflection_mm)} mm; "
            f"configured_angular_deflection={_fmt(tolerance.mesh_angular_deflection_rad)} rad",
            expected=(
                "no degenerate or duplicate triangles; paired, oppositely oriented edges; "
                "cyclic vertex links; "
                f"weld tolerance={_fmt(tolerance.mesh_vertex_weld_mm)} mm"
            ),
        )
    check = _check_mesh_arrays(vertices, triangles, tolerance, unit_scale)
    check.actual = (
        f"{check.actual}; configured_deflection={_fmt(tolerance.mesh_deflection_mm)} mm; "
        f"configured_angular_deflection={_fmt(tolerance.mesh_angular_deflection_rad)} rad"
    )
    return check


def _fresh_mesh_arrays(shape: Any, linear_deflection: float, angular_deflection: float) -> tuple[list, list]:
    """Mesh private native topology with absolute deflection and no cached mesh."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    copier = BRepBuilderAPI_Copy(shape.wrapped, False, False)
    if not copier.IsDone():
        raise ValueError("native topology could not be copied for meshing")
    copied = copier.Shape()
    BRepTools.Clean_s(copied)
    mesher = BRepMesh_IncrementalMesh(copied, linear_deflection, False, angular_deflection, False)
    if not mesher.IsDone():
        raise ValueError("absolute native meshing did not complete")

    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    explorer = TopExp_Explorer(copied, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location)
        if mesh is None:
            raise ValueError("a face has no triangulation after native meshing")
        offset = len(vertices)
        transform = location.Transformation()
        for index in range(1, mesh.NbNodes() + 1):
            point = mesh.Node(index).Transformed(transform)
            vertices.append((point.X(), point.Y(), point.Z()))
        for triangle in mesh.Triangles():
            indices = (triangle.Value(1), triangle.Value(2), triangle.Value(3))
            if face.Orientation() == TopAbs_REVERSED:
                indices = (indices[0], indices[2], indices[1])
            triangles.append(tuple(index + offset - 1 for index in indices))
        explorer.Next()
    return vertices, triangles


def _check_mesh_arrays(
    vertices: Any,
    triangles: Any,
    tolerance: GateTolerances,
    unit_scale: float = 1.0,
) -> Check:
    """Check triangle topology, welding each vertex to a nearby representative."""
    expected = (
        "no degenerate or duplicate triangles; every edge has degree 2 with opposite winding; "
        "each used vertex has one connected cyclic link; "
        f"weld tolerance={_fmt(tolerance.mesh_vertex_weld_mm)} mm; "
        f"triangle area threshold={_fmt(tolerance.mesh_triangle_area_mm2)} mm²"
    )
    try:
        source_vertices = list(vertices)
        source_triangles = list(triangles)
        if not source_vertices or not source_triangles:
            return _skip("mesh_watertight", "tessellation produced no vertices or triangles", expected)

        points = [_point_in_mm(point, unit_scale) for point in source_vertices]
        welded_points, vertex_map = _weld_vertices(points, tolerance.mesh_vertex_weld_mm)
        edge_degrees: Counter[tuple[int, int]] = Counter()
        edge_directions: Counter[tuple[int, int]] = Counter()
        triangle_counts: Counter[tuple[int, int, int]] = Counter()
        links: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
        degenerate_triangles = 0
        minimum_triangle_area = float("inf")

        for triangle_number, triangle in enumerate(source_triangles, start=1):
            if len(triangle) != 3:
                raise ValueError(f"triangle {triangle_number} does not have three indices")
            if any(isinstance(index, bool) for index in triangle):
                raise ValueError(f"triangle {triangle_number} contains a boolean index")
            raw_indices = tuple(integer_index(index) for index in triangle)
            if any(index < 0 or index >= len(vertex_map) for index in raw_indices):
                raise ValueError(f"triangle {triangle_number} references an out-of-range vertex")
            indices = tuple(vertex_map[index] for index in raw_indices)
            if len(set(indices)) != 3:
                minimum_triangle_area = 0.0
                degenerate_triangles += 1
                continue

            area = _triangle_area(*(welded_points[index] for index in indices))
            if not isfinite(area):
                raise ValueError(f"triangle {triangle_number} has a non-finite area")
            minimum_triangle_area = min(minimum_triangle_area, area)
            if area <= tolerance.mesh_triangle_area_mm2:
                degenerate_triangles += 1
                continue

            triangle_counts[tuple(sorted(indices))] += 1
            for start, end in ((indices[0], indices[1]), (indices[1], indices[2]), (indices[2], indices[0])):
                edge = tuple(sorted((start, end)))
                edge_degrees[edge] += 1
                edge_directions[edge] += 1 if start < end else -1
            for vertex, first, second in (
                (indices[0], indices[1], indices[2]),
                (indices[1], indices[2], indices[0]),
                (indices[2], indices[0], indices[1]),
            ):
                links[vertex][first].add(second)
                links[vertex][second].add(first)

        duplicate_triangles = sum(count - 1 for count in triangle_counts.values())
        winding_conflicts = sum(
            degree == 2 and edge_directions[edge] != 0 for edge, degree in edge_degrees.items()
        )
        nonmanifold_vertices = sum(not _is_single_cycle(link) for link in links.values())

        if not edge_degrees:
            boundaries = 0
            nonmanifold = 0
            other_bad = 0
            bad_edges = 0
            min_degree = 0
            max_degree = 0
        else:
            degrees = list(edge_degrees.values())
            boundaries = sum(degree == 1 for degree in degrees)
            nonmanifold = sum(degree > 2 for degree in degrees)
            other_bad = sum(degree < 1 for degree in degrees)
            bad_edges = sum(degree != 2 for degree in degrees)
            min_degree = min(degrees)
            max_degree = max(degrees)
    except Exception as exc:
        reason = _exception_text(exc)
        return _skip("mesh_watertight", f"mesh topology measurement raised {reason}", expected)

    passed = (
        bool(edge_degrees)
        and degenerate_triangles == 0
        and duplicate_triangles == 0
        and bad_edges == 0
        and winding_conflicts == 0
        and nonmanifold_vertices == 0
    )
    return _check(
        "mesh_watertight",
        "pass" if passed else "fail",
        "The welded mesh has no degenerate or duplicate triangles, has paired oriented edges, and has cyclic vertex links."
        if passed
        else "The welded mesh failed at least one triangle, edge, winding, or vertex-link check.",
        expected=expected,
        actual=(
            f"input_vertices={len(points)}; welded_vertices={len(welded_points)}; "
            f"triangles={len(source_triangles)}; degenerate_triangles={degenerate_triangles}; "
            f"duplicate_triangles={duplicate_triangles}; winding_conflicts={winding_conflicts}; "
            f"nonmanifold_vertices={nonmanifold_vertices}; "
            f"undirected_edges={len(edge_degrees)}; boundary_edges={boundaries}; "
            f"nonmanifold_edges={nonmanifold}; other_non_2_degree_edges={other_bad}; "
            f"edge_degree_range={min_degree}..{max_degree}; "
            f"weld_tolerance={_fmt(tolerance.mesh_vertex_weld_mm)} mm; "
            f"minimum_triangle_area={_fmt(minimum_triangle_area)} mm²; "
            f"triangle_area_threshold={_fmt(tolerance.mesh_triangle_area_mm2)} mm²"
        ),
    )


def _is_single_cycle(link: dict[int, set[int]]) -> bool:
    """A closed 2-manifold vertex has one connected cycle of neighbors."""
    if not link or any(len(neighbors) != 2 for neighbors in link.values()):
        return False
    pending = [next(iter(link))]
    visited: set[int] = set()
    while pending:
        vertex = pending.pop()
        if vertex not in visited:
            visited.add(vertex)
            pending.extend(link[vertex] - visited)
    return len(visited) == len(link)


def _point_in_mm(point: Any, unit_scale: float) -> tuple[float, float, float]:
    if all(hasattr(point, axis) for axis in ("X", "Y", "Z")):
        coordinates = (point.X, point.Y, point.Z)
    elif all(hasattr(point, axis) for axis in ("x", "y", "z")):
        coordinates = (point.x, point.y, point.z)
    else:
        coordinates = tuple(point)
    if len(coordinates) != 3:
        raise ValueError("a tessellated vertex does not have three coordinates")
    result = tuple(float(value) * unit_scale for value in coordinates)
    if not all(isfinite(value) for value in result):
        raise ValueError("a tessellated vertex coordinate was not finite")
    return result  # type: ignore[return-value]


def _weld_vertices(
    points: list[tuple[float, float, float]], tolerance_mm: float
) -> tuple[list[tuple[float, float, float]], list[int]]:
    if tolerance_mm == 0:
        representatives: list[tuple[float, float, float]] = []
        exact: dict[tuple[float, float, float], int] = {}
        vertex_map: list[int] = []
        for point in points:
            index = exact.get(point)
            if index is None:
                index = len(representatives)
                exact[point] = index
                representatives.append(point)
            vertex_map.append(index)
        return representatives, vertex_map

    buckets: dict[tuple[int, int, int], list[int]] = {}
    representatives = []
    vertex_map = []
    for point in points:
        cell = tuple(floor(coordinate / tolerance_mm) for coordinate in point)
        candidates: set[int] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    candidates.update(buckets.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()))

        nearest: int | None = None
        nearest_distance = float("inf")
        for candidate in sorted(candidates):
            representative = representatives[candidate]
            distance = dist(point, representative)
            if distance <= tolerance_mm and distance < nearest_distance:
                nearest = candidate
                nearest_distance = distance

        if nearest is None:
            nearest = len(representatives)
            representatives.append(point)
            buckets.setdefault(cell, []).append(nearest)
        vertex_map.append(nearest)
    return representatives, vertex_map


def _triangle_area(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
    third: tuple[float, float, float],
) -> float:
    ab = tuple(second[index] - first[index] for index in range(3))
    ac = tuple(third[index] - first[index] for index in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return 0.5 * hypot(*cross)


def _exception_text(exc: Exception) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _fmt(value: float) -> str:
    # Preserve enough digits to reconstruct the actual comparison value.
    return repr(float(value)).removesuffix(".0")
