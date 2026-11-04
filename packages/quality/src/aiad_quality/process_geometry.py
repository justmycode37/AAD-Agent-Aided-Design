"""Finite B-rep screens used by the process evaluator.

All build123d and OCCT imports live inside functions so importing
``aiad_quality`` does not load the geometry kernel.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from ._checks import finite, mm_per_unit


class GeometryUnavailable(ValueError):
    """The requested screen could not get bounded evidence from this shape."""


@dataclass(frozen=True)
class SurfaceSample:
    face_index: int
    face: Any
    point: tuple[float, float, float]
    normal: tuple[float, float, float]


@dataclass(frozen=True)
class Mouth:
    point: tuple[float, float, float]
    outward: tuple[float, float, float]
    plane_area: float
    plane_face_index: int


@dataclass(frozen=True)
class CylinderPatch:
    face_index: int
    face: Any
    radius_mm: float
    radius_raw: float
    axis_origin: tuple[float, float, float]
    axis: tuple[float, float, float]
    v_range: tuple[float, float]
    angular_span: float
    radial_alignment: float
    inward: bool
    mouths: tuple[Mouth, ...]
    corner_plane_normals: tuple[tuple[float, float, float], ...]
    radial_direction: tuple[float, float, float]


@dataclass(frozen=True)
class WallResult:
    minimum_mm: float
    face_index: int
    samples_hit: int
    samples_missed: int
    sample_limit_reached: bool


@dataclass(frozen=True)
class OverhangResult:
    minimum_downward_slope_deg: float | None
    violating_samples: int
    downward_samples: int
    total_samples: int
    sample_limit_reached: bool
    face_index: int | None


def _xyz(value: Any) -> tuple[float, float, float]:
    components = []
    for axis in ("X", "Y", "Z"):
        component = getattr(value, axis)
        components.append(finite(component() if callable(component) else component))
    return (components[0], components[1], components[2])


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(length) or length <= 1e-14:
        raise GeometryUnavailable("a B-rep direction was zero or non-finite")
    return tuple(value / length for value in vector)  # type: ignore[return-value]


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add_scaled(
    point: tuple[float, float, float], vector: tuple[float, float, float], amount: float
) -> tuple[float, float, float]:
    return tuple(point[i] + vector[i] * amount for i in range(3))  # type: ignore[return-value]


def _cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _shape_faces(shape: Any) -> list[Any]:
    try:
        faces = list(shape.faces())
    except Exception as exc:  # kernel wrappers vary while S1 is landing
        raise GeometryUnavailable(f"shape does not expose B-rep faces: {exc}") from exc
    if not faces:
        raise GeometryUnavailable("shape has no recognized B-rep faces")
    return faces


def _surface_samples_for_face(face: Any, face_index: int, tolerance_raw: float) -> list[SurfaceSample]:
    """Return at most nine interior samples on one trimmed face."""
    from build123d import Vector
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_State
    from OCP.gp import gp_Pnt

    try:
        u0, u1, v0, v1 = (finite(x) for x in BRepTools.UVBounds_s(face.wrapped))
        surface = BRepAdaptor_Surface(face.wrapped, True)
    except Exception as exc:
        raise GeometryUnavailable(f"could not read trimmed surface bounds: {exc}") from exc
    if not all(math.isfinite(x) for x in (u0, u1, v0, v1)) or u1 <= u0 or v1 <= v0:
        raise GeometryUnavailable("a face has non-finite or empty parameter bounds")

    fractions = (0.25, 0.5, 0.75)
    samples: list[SurfaceSample] = []
    seen: set[tuple[int, int, int]] = set()
    classify_tolerance = max(tolerance_raw * 0.1, 1e-8)
    for fu in fractions:
        for fv in fractions:
            try:
                point = surface.Value(u0 + (u1 - u0) * fu, v0 + (v1 - v0) * fv)
                xyz = _xyz(point)
                state = BRepClass_FaceClassifier(face.wrapped, gp_Pnt(*xyz), classify_tolerance).State()
                if state != TopAbs_State.TopAbs_IN:
                    continue
                key = tuple(round(coordinate / max(classify_tolerance, 1e-8)) for coordinate in xyz)
                if key in seen:
                    continue
                normal = _unit(_xyz(face.normal_at(Vector(*xyz))))
                seen.add(key)
                samples.append(SurfaceSample(face_index, face, xyz, normal))
            except Exception as exc:
                raise GeometryUnavailable(f'surface sample query failed on face {face_index}: {exc}') from exc
            if len(samples) >= 9:
                break
        if len(samples) >= 9:
            break

    if not samples:
        try:
            center = _xyz(face.center())
            state = BRepClass_FaceClassifier(
                face.wrapped, gp_Pnt(*center), classify_tolerance
            ).State()
            if state == TopAbs_State.TopAbs_IN:
                normal = _unit(_xyz(face.normal_at(Vector(*center))))
                samples.append(SurfaceSample(face_index, face, center, normal))
        except Exception as exc:
            raise GeometryUnavailable(f'fallback surface query failed on face {face_index}: {exc}') from exc
    return samples


def surface_samples(
    shape: Any,
    *,
    units: str,
    tolerance_mm: float,
    max_faces: int = 128,
    max_samples: int = 512,
) -> tuple[list[SurfaceSample], bool]:
    """Sample finite points on a bounded prefix of trimmed B-rep faces."""
    scale = mm_per_unit(units)
    tolerance_raw = finite(tolerance_mm) / scale
    faces = _shape_faces(shape)
    samples: list[SurfaceSample] = []
    truncated = len(faces) > max_faces
    for face_index, face in enumerate(faces[:max_faces]):
        if len(samples) >= max_samples:
            truncated = True
            break
        face_samples = _surface_samples_for_face(face, face_index, tolerance_raw)
        if not face_samples:
            truncated = True
        remaining = max_samples - len(samples)
        samples.extend(face_samples[:remaining])
        if len(face_samples) > remaining:
            truncated = True
    if not samples:
        raise GeometryUnavailable('no trimmed face yielded a classified finite normal sample')
    return samples, truncated


def _bounding_corners(shape: Any) -> list[tuple[float, float, float]]:
    try:
        bbox = shape.bounding_box()
        low, high = _xyz(bbox.min), _xyz(bbox.max)
    except Exception as exc:
        raise GeometryUnavailable(f"shape does not expose a finite B-rep bounding box: {exc}") from exc
    # Vertex extrema miss curved surfaces. An enclosing AABB gives a conservative
    # build-plane bound for arbitrary directions and a complete ray-length bound.
    return [(x,y,z) for x in (low[0],high[0]) for y in (low[1],high[1]) for z in (low[2],high[2])]


def _support_projection(shape: Any, direction: tuple[float,float,float]) -> float:
    """Bound the actual B-rep in a frame whose Z axis is the build direction."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf
    try:
        transform=gp_Trsf()
        transform.SetTransformation(gp_Ax3(gp_Pnt(0,0,0),gp_Dir(*direction)))
        rotated=BRepBuilderAPI_Transform(shape.wrapped,transform,True).Shape()
        bounds=Bnd_Box()
        BRepBndLib.AddOptimal_s(rotated,bounds,False,False)
        return finite(bounds.CornerMin().Z())
    except Exception as exc:
        raise GeometryUnavailable(f'could not bound the B-rep in the build frame: {exc}') from exc


def overhang_screen(
    shape: Any,
    *,
    units: str,
    build_direction: tuple[float, float, float],
    min_downward_slope_deg: float,
    tolerance_mm: float,
    max_faces: int = 128,
    max_samples: int = 512,
) -> OverhangResult:
    """Screen sampled downward normals against a build-plate slope threshold."""
    scale = mm_per_unit(units)
    direction = _unit(build_direction)
    tolerance_raw = finite(tolerance_mm) / scale
    lowest_projection = _support_projection(shape,direction)
    samples, truncated = surface_samples(
        shape,
        units=units,
        tolerance_mm=tolerance_mm,
        max_faces=max_faces,
        max_samples=max_samples,
    )
    downward: list[tuple[float, int]] = []
    violating = 0
    threshold = finite(min_downward_slope_deg)
    for sample in samples:
        projection = _dot(sample.point, direction)
        normal_dot = max(-1.0, min(1.0, _dot(sample.normal, direction)))
        if normal_dot >= -1e-6 or projection <= lowest_projection + tolerance_raw:
            continue
        # Angle of the downward-facing surface above the horizontal build
        # plane: 0 degrees is a horizontal underside; 90 is a vertical wall.
        slope = math.degrees(math.acos(max(0.0, min(1.0, -normal_dot))))
        slope = finite(slope)
        downward.append((slope, sample.face_index))
        if slope + 1e-9 < threshold:
            violating += 1
    if not downward:
        return OverhangResult(None, 0, 0, len(samples), truncated, None)
    minimum, face_index = min(downward, key=lambda item: item[0])
    return OverhangResult(
        finite(minimum), violating, len(downward), len(samples), truncated, face_index
    )


def _single_solid(shape: Any) -> bool:
    try:
        solids = list(shape.solids())
    except Exception as exc:
        raise GeometryUnavailable('could not determine solid count for wall rays') from exc
    return len(solids) == 1


def wall_thickness_screen(
    shape: Any,
    *,
    units: str,
    tolerance_mm: float,
    max_faces: int = 128,
    max_samples: int = 512,
) -> WallResult:
    """Cast finite inward-normal rays and measure their first B-rep exit."""
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

    if not _single_solid(shape):
        raise GeometryUnavailable("wall rays need one attributable solid; a multi-solid shape was supplied")
    solid = list(shape.solids())[0].wrapped
    scale = mm_per_unit(units)
    tolerance_mm = finite(tolerance_mm)
    tolerance_raw = tolerance_mm / scale
    samples, truncated = surface_samples(
        shape,
        units=units,
        tolerance_mm=tolerance_mm,
        max_faces=max_faces,
        max_samples=max_samples,
    )
    positions = _bounding_corners(shape)
    mins = [min(point[i] for point in positions) for i in range(3)]
    maxs = [max(point[i] for point in positions) for i in range(3)]
    diagonal = math.sqrt(sum((maxs[i] - mins[i]) ** 2 for i in range(3)))
    ray_limit = finite(diagonal + 2.0 * tolerance_raw)
    if ray_limit <= 0.0:
        raise GeometryUnavailable("shape has no positive finite bounding diagonal")

    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(shape.wrapped, max(tolerance_raw * 0.1, 1e-8))
    best: float | None = None
    best_face = -1
    hits = 0
    misses = 0
    offset = max(tolerance_raw * 2.0, diagonal * 1e-8, 1e-8)
    for sample in samples:
        inward = tuple(-component for component in sample.normal)
        start = _add_scaled(sample.point, inward, offset)
        try:
            if BRepClass3d_SolidClassifier(solid, gp_Pnt(*start), min(tolerance_raw*0.1,1e-7)).State() != TopAbs_IN:
                # The offset can jump entirely across a very thin wall. Such a
                # ray is unknown; it must not measure unrelated stock beyond it.
                misses += 1
                continue
            line = gp_Lin(gp_Pnt(*start), gp_Dir(*inward))
            intersector.Perform(line, max(offset * 0.05, 1e-9), ray_limit)
            if not intersector.IsDone():
                misses += 1
                continue
            distances = [
                finite(intersector.WParameter(index)) + offset
                for index in range(1, intersector.NbPnt() + 1)
                if finite(intersector.WParameter(index)) > offset * 0.05
            ]
        except Exception:
            misses += 1
            continue
        if not distances:
            misses += 1
            continue
        thickness_mm = finite(min(distances) * scale)
        if thickness_mm <= 0.0:
            misses += 1
            continue
        hits += 1
        if best is None or thickness_mm < best:
            best = thickness_mm
            best_face = sample.face_index

    if best is None:
        raise GeometryUnavailable("no sampled inward-normal ray found an opposing B-rep boundary")
    return WallResult(best, best_face, hits, misses, truncated)


def _same_topology(a: Any, b: Any, *, strict: bool = False) -> bool:
    try:
        return bool(a.wrapped.IsSame(b.wrapped))
    except Exception as exc:
        if strict:
            raise GeometryUnavailable(f'topology identity query failed: {exc}') from exc
        return False


def _face_edge_lists(faces: list[Any]) -> list[list[Any]]:
    result: list[list[Any]] = []
    for face in faces:
        try:
            result.append(list(face.edges()))
        except Exception as exc:
            raise GeometryUnavailable(f'face-edge enumeration failed: {exc}') from exc
    return result


def cylindrical_surfaces(shape: Any, *, units: str, tolerance_mm: float, max_faces: int = 128) -> list[CylinderPatch]:
    """Recognize cylinder faces and their inward orientation and planar mouths."""
    from build123d import GeomType, Vector
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder

    scale = mm_per_unit(units)
    tolerance_raw = finite(tolerance_mm) / scale
    faces = _shape_faces(shape)
    if len(faces)>max_faces:
        raise GeometryUnavailable(f'cylinder enumeration exceeds the {max_faces}-face computation cap')
    face_edges = _face_edge_lists(faces)
    patches: list[CylinderPatch] = []
    for face_index, face in enumerate(faces):
        if getattr(face, "geom_type", None) != GeomType.CYLINDER:
            continue
        try:
            surface = BRepAdaptor_Surface(face.wrapped, True)
            if surface.GetType() != GeomAbs_Cylinder:
                continue
            cylinder = surface.Cylinder()
            radius_raw = finite(cylinder.Radius())
            radius_mm = finite(radius_raw * scale)
            axis = _unit(_xyz(cylinder.Axis().Direction()))
            axis_origin = _xyz(cylinder.Axis().Location())
            u0, u1, v0, v1 = (finite(x) for x in BRepTools.UVBounds_s(face.wrapped))
            angular_span = finite(abs(u1 - u0))
            # A full cylindrical face's center of mass lies on its axis, where
            # the radial direction is undefined. Evaluate the analytic surface.
            center = _xyz(surface.Value((u0+u1)/2.0, (v0+v1)/2.0))
            normal = _unit(_xyz(face.normal_at(Vector(*center))))
            axial = _dot(_sub(center, axis_origin), axis)
            axis_point = _add_scaled(axis_origin, axis, axial)
            radial = _unit(_sub(center, axis_point))
            radial_alignment = finite(_dot(normal, radial))
        except Exception as exc:
            raise GeometryUnavailable(f"could not classify cylindrical face {face_index}: {exc}") from exc

        mouths: list[Mouth] = []
        corner_normals: list[tuple[float, float, float]] = []
        for edge in face_edges[face_index]:
            if getattr(edge, "geom_type", None) == GeomType.LINE:
                for other_index, other in enumerate(faces):
                    if other_index == face_index or getattr(other, "geom_type", None) != GeomType.PLANE:
                        continue
                    if not any(_same_topology(edge, other_edge,strict=True) for other_edge in face_edges[other_index]):
                        continue
                    try:
                        other_normal = _unit(_xyz(other.normal_at(other.center())))
                        if abs(_dot(other_normal, axis)) < 0.15:
                            corner_normals.append(other_normal)
                    except Exception as exc:
                        raise GeometryUnavailable(f'corner adjacency query failed: {exc}') from exc

            if getattr(edge, "geom_type", None) != GeomType.CIRCLE:
                continue
            try:
                curve = BRepAdaptor_Curve(edge.wrapped)
                if curve.GetType() != GeomAbs_Circle:
                    continue
                circle = curve.Circle()
                if abs(finite(circle.Radius()) - radius_raw) > max(tolerance_raw, radius_raw * 1e-6):
                    continue
                circle_center = _xyz(circle.Location())
                # A true end ring is centered on the cylinder axis.
                delta = _sub(circle_center, axis_origin)
                circle_axial = _dot(delta, axis)
                axis_point_at_ring = _add_scaled(axis_origin, axis, circle_axial)
                if math.sqrt(sum(x * x for x in _sub(circle_center, axis_point_at_ring))) > max(
                    tolerance_raw * 2.0, 1e-7
                ):
                    continue
                for other_index, other in enumerate(faces):
                    if other_index == face_index or getattr(other, "geom_type", None) != GeomType.PLANE:
                        continue
                    if not any(_same_topology(edge, other_edge,strict=True) for other_edge in face_edges[other_index]):
                        continue
                    outward = _unit(_xyz(other.normal_at(other.center())))
                    if abs(_dot(outward, axis)) < 0.98:
                        continue
                    # A pocket/counterbore floor points into the cylinder,
                    # rather than out through its entry. It is not a mouth.
                    end_sign = 1.0 if circle_axial > (v0+v1)/2.0 else -1.0
                    if _dot(outward, axis)*end_sign < 0.98:
                        continue
                    mouth = Mouth(circle_center, outward, finite(other.area), other_index)
                    if not any(
                        existing.plane_face_index == mouth.plane_face_index
                        and math.dist(existing.point, mouth.point) <= max(tolerance_raw, 1e-7)
                        for existing in mouths
                    ):
                        mouths.append(mouth)
            except Exception as exc:
                raise GeometryUnavailable(f'circular mouth query failed on face {face_index}: {exc}') from exc

        unique_corner_normals: list[tuple[float, float, float]] = []
        for normal in corner_normals:
            if not any(abs(_dot(normal, existing)) > 0.98 for existing in unique_corner_normals):
                unique_corner_normals.append(normal)
        patches.append(
            CylinderPatch(
                face_index=face_index,
                face=face,
                radius_mm=radius_mm,
                radius_raw=radius_raw,
                axis_origin=axis_origin,
                axis=axis,
                v_range=(min(v0, v1), max(v0, v1)),
                angular_span=angular_span,
                radial_alignment=radial_alignment,
                inward=radial_alignment < -0.5,
                mouths=tuple(mouths),
                corner_plane_normals=tuple(unique_corner_normals),
                radial_direction=radial,
            )
        )
    return patches


def approach_probe_collision_mm3(
    shape: Any,
    *,
    mouth: Mouth,
    cylinder: CylinderPatch,
    probe_radius_mm: float,
    outside_length_mm: float,
    units: str,
    end_tolerance_mm: float,
) -> float:
    """Intersect an explicit finite coaxial cylinder probe with the B-rep."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    scale = mm_per_unit(units)
    radius_raw = finite(probe_radius_mm) / scale
    outside_raw = finite(outside_length_mm) / scale
    end_raw = finite(end_tolerance_mm) / scale
    depth_raw = abs(cylinder.v_range[1] - cylinder.v_range[0])
    length_raw = finite(outside_raw + depth_raw - end_raw)
    if radius_raw <= 0.0 or outside_raw <= 0.0 or length_raw <= 0.0:
        raise GeometryUnavailable("approach probe radius or length is not positive")
    inward = tuple(-component for component in mouth.outward)
    start = _add_scaled(mouth.point, mouth.outward, outside_raw)
    probe = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*start), gp_Dir(*inward)), radius_raw, length_raw
    )
    probe.Build()
    if not probe.IsDone():
        raise GeometryUnavailable("OCCT could not build the coaxial swept probe")
    common = BRepAlgoAPI_Common(shape.wrapped, probe.Solid())
    common.Build()
    if not common.IsDone():
        raise GeometryUnavailable("OCCT could not intersect the swept probe with the B-rep")
    common_shape = common.Shape()
    if common_shape.IsNull():
        return 0.0
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(common_shape, props)
    return finite(max(0.0, props.Mass()) * scale**3)


def through_cylindrical_features(
    shape: Any, *, units: str, tolerance_mm: float, max_faces: int = 128
) -> list[CylinderPatch]:
    """Return inward circular cuts with broad planar faces at both ends."""
    scale = mm_per_unit(units)
    tol = finite(tolerance_mm) / scale
    result: list[CylinderPatch] = []
    for cylinder in cylindrical_surfaces(shape, units=units, tolerance_mm=tolerance_mm,max_faces=max_faces):
        if not cylinder.inward or cylinder.angular_span < 2*math.pi-1e-5:
            continue
        circle_area = math.pi * cylinder.radius_raw**2
        broad = [mouth for mouth in cylinder.mouths if mouth.plane_area >= 1.5 * circle_area]
        opposite = any(_dot(a.outward, b.outward) < -0.98 for i, a in enumerate(broad) for b in broad[i + 1 :])
        if opposite and abs(cylinder.v_range[1] - cylinder.v_range[0]) > tol:
            result.append(cylinder)
    return result


def bend_surface_pairs(
    shape: Any, *, units: str, tolerance_mm: float, max_faces: int = 128
) -> list[tuple[CylinderPatch, CylinderPatch]]:
    """Recognize co-axial partial cylinder pairs with overlapping axial spans."""
    scale = mm_per_unit(units)
    tolerance_raw = finite(tolerance_mm) / scale
    if not _single_solid(shape):
        raise GeometryUnavailable('bend recognition requires one connected attributable solid')
    solid_faces=list(shape.solids()[0].faces())
    if any(not any(_same_topology(face,member,strict=True) for member in solid_faces) for face in _shape_faces(shape)):
        raise GeometryUnavailable('shape contains faces outside its one solid')
    cylinders = cylindrical_surfaces(shape, units=units, tolerance_mm=tolerance_mm,max_faces=max_faces)
    pairs: list[tuple[CylinderPatch, CylinderPatch]] = []
    for index, first in enumerate(cylinders):
        if not (0.05 < first.angular_span < 2.0 * math.pi - 0.15):
            continue
        for second in cylinders[index + 1 :]:
            if not (0.05 < second.angular_span < 2.0 * math.pi - 0.15):
                continue
            if abs(first.angular_span - second.angular_span) > 1e-6:
                continue
            if first.inward==second.inward or _dot(first.radial_direction,second.radial_direction)<1-1e-8:
                continue
            inner,outer=sorted((first,second),key=lambda patch:patch.radius_raw)
            if not inner.inward or outer.inward:
                continue
            shared_ends={mouth.plane_face_index for mouth in first.mouths}&{mouth.plane_face_index for mouth in second.mouths}
            if len(shared_ends)<2:
                continue
            ends=[mouth for mouth in first.mouths if mouth.plane_face_index in shared_ends]
            if not any(_dot(a.outward,b.outward)<-0.98 for i,a in enumerate(ends) for b in ends[i+1:]):
                continue
            if abs(_dot(first.axis, second.axis)) < 0.999:
                continue
            offset = _sub(second.axis_origin, first.axis_origin)
            perpendicular = _cross(offset, first.axis)
            if math.sqrt(_dot(perpendicular, perpendicular)) > max(tolerance_raw * 2.0, 1e-6):
                continue
            if abs(first.radius_raw - second.radius_raw) <= tolerance_raw:
                continue
            # Compare axial intervals after projecting both cylinder endpoints
            # into the first cylinder's direction, allowing opposite axes.
            first_ends = [
                _add_scaled(first.axis_origin, first.axis, first.v_range[0]),
                _add_scaled(first.axis_origin, first.axis, first.v_range[1]),
            ]
            second_ends = [
                _add_scaled(second.axis_origin, second.axis, second.v_range[0]),
                _add_scaled(second.axis_origin, second.axis, second.v_range[1]),
            ]
            a0, a1 = sorted(_dot(point, first.axis) for point in first_ends)
            b0, b1 = sorted(_dot(point, first.axis) for point in second_ends)
            overlap = min(a1, b1) - max(a0, b0)
            if overlap <= max(tolerance_raw, 1e-6):
                continue
            pairs.append((first, second))
    return pairs


def feature_for_face(ctx: Any, shape: Any, face_index: int) -> str | None:
    """Return a feature only when the context links the exact B-rep face."""
    try:
        target = list(shape.faces())[face_index]
    except Exception:
        return None
    explicit = getattr(target, "feature", None) or getattr(target, "feature_name", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    for feature in getattr(ctx, "features", ()) or ():
        name = getattr(feature, "name", None)
        if not isinstance(name, str) or not name:
            continue
        refs = getattr(feature, "faces", ()) or ()
        for ref in refs:
            candidate = getattr(ref, "face", ref)
            if _same_topology(target, candidate):
                return name
    return None
