"""Lazy, conservative B-rep measurements used by the spec evaluator.

This module deliberately imports build123d/OCCT only inside measurement
functions so importing :mod:`aiad_quality` does not load the CAD kernel.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any


class GeometryUnavailable(RuntimeError):
    """The model or requested geometry evidence is not available."""


class UnsupportedGeometry(RuntimeError):
    """The available B-rep cannot support this measurement conservatively."""


class GeometryOverlap(RuntimeError):
    def __init__(self, volume_mm3: float):
        self.volume_mm3=volume_mm3
        super().__init__('solids overlap')


@dataclass(frozen=True)
class HoleCylinder:
    """One analytically recognized inward-facing full cylindrical wall."""

    axis: tuple[float, float, float]
    point: tuple[float, float, float]
    radius_mm: float
    axial_min_mm: float
    axial_max_mm: float


def _raw(shape: Any) -> Any:
    raw = getattr(shape, "wrapped", None)
    return shape if raw is None else raw


def _xyz(vector: Any) -> tuple[float, float, float]:
    return float(vector.X()), float(vector.Y()), float(vector.Z())


def _required_shape(shape: Any) -> Any:
    if shape is None:
        raise GeometryUnavailable("model shape is missing")
    if not hasattr(shape, "wrapped") and not hasattr(shape, "solids"):
        raise UnsupportedGeometry("model shape does not expose a supported B-rep")
    return _raw(shape)


def bbox_mm(shape: Any, mm_per_native_unit: float) -> dict[str, float]:
    """Return X/Y/Z extents derived from the model's bounding box."""
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        box=Bnd_Box()
        BRepBndLib.AddOptimal_s(_required_shape(shape),box,False,False)
        low_x,low_y,low_z=_xyz(box.CornerMin())
        high_x,high_y,high_z=_xyz(box.CornerMax())
        dimensions=dict(zip('xyz',(high_x-low_x,high_y-low_y,high_z-low_z),strict=True))
    except Exception as exc:
        raise UnsupportedGeometry(f"bounding box is unavailable: {exc}") from exc
    if any(not math.isfinite(value) or value < 0 for value in dimensions.values()):
        raise UnsupportedGeometry("bounding box returned invalid dimensions")
    return {axis: value * mm_per_native_unit for axis, value in dimensions.items()}


def _solids(shape: Any) -> list[Any]:
    _required_shape(shape)
    try:
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopExp import TopExp_Explorer
        from build123d import Shape

        explorer = TopExp_Explorer(_raw(shape), TopAbs_SOLID)
        solids = []
        while explorer.More():
            solids.append(Shape.cast(explorer.Current()).wrapped)
            explorer.Next()
        return solids
    except ModuleNotFoundError as exc:
        raise UnsupportedGeometry("OCCT solid topology is unavailable") from exc
    except Exception as exc:
        raise UnsupportedGeometry(f"could not enumerate model solids: {exc}") from exc


def solid_count(shape: Any) -> int:
    """Count B-rep solids, without consulting cached context metrics."""
    return len(_solids(shape))


def volume_mm3(shape: Any, mm_per_native_unit: float) -> float:
    """Calculate B-rep volume and convert it to mm³."""
    _required_shape(shape)
    if not _solids(shape):
        raise UnsupportedGeometry('volume requires volumetric solids')
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(_raw(shape), props)
        volume = float(props.Mass())
    except ModuleNotFoundError as exc:
        raise UnsupportedGeometry("OCCT volume measurement is unavailable") from exc
    except Exception as exc:
        raise UnsupportedGeometry(f"could not measure model volume: {exc}") from exc
    if not math.isfinite(volume) or volume < 0:
        raise UnsupportedGeometry("volume measurement was not finite and non-negative")
    return volume * mm_per_native_unit**3


def _faces(shape: Any) -> list[Any]:
    _required_shape(shape)
    try:
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from build123d import Shape
        explorer=TopExp_Explorer(_raw(shape),TopAbs_FACE)
        result=[]
        while explorer.More():
            result.append(Shape.cast(explorer.Current()).wrapped)
            explorer.Next()
        return result
    except Exception as exc:
        raise UnsupportedGeometry(f'could not enumerate native B-rep faces: {exc}') from exc


def inward_full_cylinders(shape: Any, mm_per_native_unit: float, *, cylindrical_only: bool = False) -> list[HoleCylinder]:
    """Find full, inward-facing analytic cylindrical walls.

    A wall is accepted only when its OCCT face orientation points into the
    cylinder and its parameter range spans a full revolution. This excludes
    ordinary external bosses and partial cylindrical fillets/slots.
    """
    _required_shape(shape)
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepTools import BRepTools
        from OCP.GeomAbs import GeomAbs_Cylinder,GeomAbs_Plane
        from OCP.TopAbs import TopAbs_REVERSED
    except ModuleNotFoundError as exc:
        raise UnsupportedGeometry("OCCT analytic surface inspection is unavailable") from exc

    cylinders: list[HoleCylinder] = []
    for face in _faces(shape):
        try:
            raw_face = _raw(face)
            surface = BRepAdaptor_Surface(raw_face, True)
            if surface.GetType()==GeomAbs_Plane:
                continue
            if surface.GetType() != GeomAbs_Cylinder:
                if cylindrical_only:
                    continue
                raise UnsupportedGeometry('nonplanar, noncylindrical faces may hide unrecognized holes')
            cylinder = surface.Cylinder()
            # Reflection changes the surface handedness as well as the face
            # orientation. Reading Orientation alone swaps bosses and bores.
            if (raw_face.Orientation() == TopAbs_REVERSED) != cylinder.Direct():
                continue
            u_min, u_max, v_min, v_max = BRepTools.UVBounds_s(raw_face)
            if abs(float(u_max) - float(u_min)) < 2.0 * math.pi - 1e-6:
                if cylindrical_only:
                    continue
                raise UnsupportedGeometry("partial inward cylindrical face cannot be safely classified as a full hole")
            cylinder = surface.Cylinder()
            original_direction = _xyz(cylinder.Axis().Direction())
            norm = math.sqrt(sum(component * component for component in original_direction))
            if norm == 0 or not math.isfinite(norm):
                continue
            original_axis = tuple(component / norm for component in original_direction)
            axis = original_axis
            # Give parallel and antiparallel axes a stable common direction.
            first_nonzero = next((component for component in axis if abs(component) > 1e-12), 1.0)
            if first_nonzero < 0:
                axis = tuple(-component for component in axis)
            axis_sign = 1.0 if sum(axis[i] * original_axis[i] for i in range(3)) >= 0 else -1.0
            origin = _xyz(cylinder.Location())
            projection = sum(origin[i] * axis[i] for i in range(3))
            point = tuple((origin[i] - projection * axis[i]) * mm_per_native_unit for i in range(3))
            radius = float(cylinder.Radius()) * mm_per_native_unit
            if not math.isfinite(radius) or radius <= 0:
                continue
            axial_first = (projection + float(v_min) * axis_sign) * mm_per_native_unit
            axial_second = (projection + float(v_max) * axis_sign) * mm_per_native_unit
            axial_min, axial_max = sorted((axial_first, axial_second))
            if not math.isfinite(axial_min) or not math.isfinite(axial_max):
                raise UnsupportedGeometry("cylindrical face has invalid axial bounds")
            cylinders.append(HoleCylinder(axis, point, radius, axial_min, axial_max))
        except Exception as exc:
            # Never count a partial set of surfaces: an unreadable face could
            # hide another hole and create a false passing count.
            raise UnsupportedGeometry(f"could not inspect a B-rep face for a hole: {exc}") from exc
    return cylinders


def closed_shell_tunnels(shape: Any) -> int:
    """Count handles on valid closed analytic shells using Euler characteristic.

    Count unique edges once; seams are included and degenerate pole edges
    are collapsed. Each inner wire reduces the face's Euler contribution.
    This establishes through-tunnel coverage, not blind-pocket dimensions.
    """
    from build123d import Shape, GeomType
    from OCP.BRep import BRep_Tool
    from OCP.BRepCheck import BRepCheck_Analyzer, BRepCheck_Shell, BRepCheck_NoError
    clean = Shape.cast(_raw(shape))
    solids = list(clean.solids())
    if not solids or not BRepCheck_Analyzer(clean.wrapped).IsValid():
        raise UnsupportedGeometry('through-hole coverage requires valid volumetric solids')
    allowed = {GeomType.PLANE, GeomType.CYLINDER, GeomType.CONE, GeomType.SPHERE, GeomType.TORUS}
    total = 0
    from OCP.BRepClass3d import BRepClass3d
    for solid in solids:
        outer = BRepClass3d.OuterShell_s(solid.wrapped)
        if outer.IsNull():
            raise UnsupportedGeometry('solid has no recognized exterior shell')
        for shell in [Shape.cast(outer)]:
            faces = list(shell.faces())
            if not faces or any(face.geom_type not in allowed for face in faces):
                raise UnsupportedGeometry('nonanalytic face topology has no established through-tunnel coverage')
            if BRepCheck_Shell(shell.wrapped).Closed() != BRepCheck_NoError:
                raise UnsupportedGeometry('open shell cannot establish a through-tunnel count')
            vertices = len(shell.vertices())
            edges = sum(not BRep_Tool.Degenerated_s(edge.wrapped) for edge in shell.edges())
            face_chi = sum(1 - len(face.inner_wires()) for face in faces)
            chi = vertices - edges + face_chi
            if chi > 2 or chi % 2:
                raise UnsupportedGeometry('shell cell decomposition has an unsupported Euler characteristic')
            total += (2 - chi) // 2
    return total


def require_round_planar_openings(shape: Any) -> None:
    """Refuse a general opening count if planar inner loops are not circular.

    This is a conservative coverage check, not a polygonal-hole recognizer.
    Native wrappers discard any instance-level method overrides.
    """
    from build123d import Shape, GeomType
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    if not _solids(shape):
        raise UnsupportedGeometry("3D hole counts require volumetric solids; 2D contours need a separate evaluator")
    try:
        bounds = Bnd_Box()
        BRepBndLib.AddOptimal_s(_raw(shape), bounds, False, False)
        low, high = _xyz(bounds.CornerMin()), _xyz(bounds.CornerMax())
        for face in Shape.cast(_raw(shape)).faces():
            if face.geom_type != GeomType.PLANE:
                continue
            for wire in face.inner_wires():
                edges = list(wire.edges())
                if not edges or any(edge.geom_type != GeomType.CIRCLE for edge in edges):
                    raise UnsupportedGeometry("noncircular planar inner boundaries are present; the complete opening count is not established")
            plane = BRepAdaptor_Surface(face.wrapped, True).Plane()
            normal, origin = _xyz(plane.Axis().Direction()), _xyz(plane.Location())
            outer_support = any(
                abs(normal[axis]) == 1
                and all(normal[j] == 0 for j in range(3) if j != axis)
                and origin[axis] in (low[axis], high[axis])
                for axis in range(3)
            )
            if not outer_support and any(edge.geom_type != GeomType.CIRCLE for edge in face.edges()):
                # Polygonal tunnels can straddle exterior face junctions and
                # leave no inner wire on any one face. Unclassified interior
                # planar boundaries must therefore withhold a general count.
                raise UnsupportedGeometry("noncircular planar features outside the outer support planes are not classified as openings or bosses")
    except UnsupportedGeometry:
        raise
    except Exception as exc:
        raise UnsupportedGeometry(f"could not enumerate planar opening boundaries: {exc}") from exc


def unique_hole_axes(
    cylinders: list[HoleCylinder],
    *,
    diameter_mm: tuple[float | None, float | None] | None = None,
    diameter_resolution_mm: float = 0.0,
    shape: Any = None,
    mm_per_native_unit: float = 1.0,
    through: bool = True,
) -> list[tuple[float, float, float]]:
    """Cluster recognized cylindrical walls by coaxial centerline."""
    selected = cylinders
    if not selected:
        return []
    scale = max(1.0, max(cylinder.radius_mm for cylinder in selected))
    line_tol = max(1e-7, scale * 1e-9)
    axis_tol = 1e-8
    groups: list[tuple[tuple[float, float, float], list[HoleCylinder]]] = []
    for cylinder in selected:
        match: list[HoleCylinder] | None = None
        for axis, group in groups:
            if max(abs(axis[i] - cylinder.axis[i]) for i in range(3)) > axis_tol:
                continue
            if math.dist(group[0].point, cylinder.point) <= line_tol:
                match = group
                break
        if match is None:
            groups.append((cylinder.axis, [cylinder]))
        else:
            match.append(cylinder)
    hole_points: list[tuple[float, float, float]] = []
    interval_tol = line_tol
    for _, group in groups:
        ordered = sorted(group, key=lambda cylinder: cylinder.axial_min_mm)
        components: list[list[HoleCylinder]] = []
        upper = -math.inf
        for cylinder in ordered:
            if not components or cylinder.axial_min_mm > upper + interval_tol:
                components.append([])
                upper = cylinder.axial_max_mm
            components[-1].append(cylinder)
            upper = max(upper, cylinder.axial_max_mm)
        for component in components:
            matching = component
            if diameter_mm is not None:
                lower, upper_d = diameter_mm
                matching = [c for c in component
                    if (lower is None or 2*c.radius_mm >= lower-diameter_resolution_mm)
                    and (upper_d is None or 2*c.radius_mm <= upper_d+diameter_resolution_mm)]
                if not matching:
                    continue
            if shape is not None and not _bore_channel_clear(
                shape, component, matching, mm_per_native_unit, through=through
            ):
                continue
            hole_points.append(component[0].point)
    return hole_points


def _bore_channel_clear(shape: Any, group: list[HoleCylinder], matching: list[HoleCylinder],
                        factor: float, *, through: bool) -> bool:
    """Test the requested bore radius, including caps/waists, out to exterior.

    A point just past a wall can still be inside a sealed chamber or can jump
    across a thin cap. A finite cylindrical solid crossing the entire model
    bounds cannot miss either. The radial inset is OCCT's native 1e-7 length
    resolution; this is native B-rep evidence, not a subresolution guarantee.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from itertools import product

    bounds = Bnd_Box()
    BRepBndLib.AddOptimal_s(_raw(shape), bounds, False, False)
    low, high = _xyz(bounds.CornerMin()), _xyz(bounds.CornerMax())
    axis, point = group[0].axis, group[0].point
    projections = [sum(corner[i]*axis[i] for i in range(3))*factor
                   for corner in product(*[(low[i], high[i]) for i in range(3)])]
    margin = max(factor, max(projections)-min(projections))
    outside_low, outside_high = min(projections)-margin, max(projections)+margin

    def clear(first: float, last: float, radius_mm: float) -> bool:
        radius = radius_mm/factor - 1e-7
        if radius <= 1e-7:
            raise UnsupportedGeometry('bore radius is below native channel-classification resolution')
        direction = axis if last > first else tuple(-x for x in axis)
        start = tuple((point[i]+axis[i]*first)/factor for i in range(3))
        builder = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*start), gp_Dir(*direction)), radius, abs(last-first)/factor)
        builder.Build()
        if not builder.IsDone():
            raise UnsupportedGeometry('OCCT could not construct the axial channel probe')
        return distance_mm(builder.Solid(), _raw(shape), factor) > factor*1e-9

    radius = min(c.radius_mm for c in matching)
    if through:
        return clear(outside_low, outside_high, radius)
    # A blind bore must have a matching-radius path to at least one exterior
    # side. Probe from its interior, not from a smaller coaxial pilot's end.
    return any(clear((c.axial_min_mm+c.axial_max_mm)/2, outside, c.radius_mm)
               for c in matching for outside in (outside_low, outside_high))


def distance_mm(first: Any, second: Any, mm_per_native_unit: float) -> float:
    """Return OCCT's minimum distance between two B-rep shapes/entities."""
    try:
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape

        distance = BRepExtrema_DistShapeShape(_raw(first), _raw(second))
        distance.Perform()
        if not distance.IsDone():
            raise UnsupportedGeometry("OCCT distance calculation did not complete")
        result = float(distance.Value()) * mm_per_native_unit
    except ModuleNotFoundError as exc:
        raise UnsupportedGeometry("OCCT distance calculation is unavailable") from exc
    except UnsupportedGeometry:
        raise
    except Exception as exc:
        raise UnsupportedGeometry(f"OCCT distance calculation failed: {exc}") from exc
    if not math.isfinite(result) or result < 0:
        raise UnsupportedGeometry("distance result was invalid")
    return result


def _project_asset(project_dir: Any, asset_name: str) -> Path:
    if not isinstance(asset_name, str) or not asset_name.strip():
        raise UnsupportedGeometry("clearance asset path is empty")
    if project_dir is None:
        raise UnsupportedGeometry("project directory is unavailable")
    try:
        root = Path(project_dir).expanduser().resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise UnsupportedGeometry(f"project directory is unavailable: {exc}") from exc
    raw_path = Path(asset_name)
    if raw_path.is_absolute():
        raise UnsupportedGeometry("clearance asset must be project-relative")
    try:
        candidate = (root / raw_path).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise UnsupportedGeometry(f"project-relative STEP asset is unavailable: {exc}") from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise UnsupportedGeometry("clearance asset resolves outside the project directory") from exc
    if candidate.suffix.lower() not in {".step", ".stp"}:
        raise UnsupportedGeometry("clearance asset must be a STEP or STP file")
    if not candidate.is_file():
        raise UnsupportedGeometry("clearance asset is not a file")
    return candidate


def step_asset_distance_mm(shape: Any, project_dir: Any, asset_name: str, mm_per_native_unit: float) -> float:
    """Load a project-contained STEP asset and measure minimum B-rep clearance."""
    _required_shape(shape)
    path = _project_asset(project_dir, asset_name)
    try:
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader

        reader = STEPControl_Reader()
        status = reader.ReadFile(str(path))
        if status != IFSelect_RetDone:
            raise UnsupportedGeometry("STEP asset could not be read by OCCT")
        # Keep transferred STEP coordinates in the same native length unit as
        # the current model before applying the shared native-to-mm scale.
        reader.SetSystemLengthUnit(mm_per_native_unit)
        roots = reader.TransferRoots()
        if not roots:
            raise UnsupportedGeometry("STEP asset contains no transferable roots")
        asset = reader.OneShape()
        if asset.IsNull():
            raise UnsupportedGeometry("STEP asset has no B-rep geometry")
    except ModuleNotFoundError as exc:
        raise UnsupportedGeometry("OCCT STEP reader is unavailable") from exc
    except UnsupportedGeometry:
        raise
    except Exception as exc:
        raise UnsupportedGeometry(f"could not load STEP asset: {exc}") from exc
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        common=BRepAlgoAPI_Common(_raw(shape),asset)
        common.Build()
        if not common.IsDone():
            raise UnsupportedGeometry('clearance overlap boolean did not complete')
        from OCP.BRepCheck import BRepCheck_Analyzer
        if not BRepCheck_Analyzer(common.Shape()).IsValid():
            raise UnsupportedGeometry('clearance overlap boolean produced invalid topology')
        props=GProp_GProps()
        BRepGProp.VolumeProperties_s(common.Shape(),props)
        volume=float(props.Mass())*mm_per_native_unit**3
        if not math.isfinite(volume) or volume<0:
            raise UnsupportedGeometry('overlap volume is not finite and nonnegative')
        if volume>0:
            raise GeometryOverlap(volume)
    except (GeometryOverlap,UnsupportedGeometry):
        raise
    except Exception as exc:
        raise UnsupportedGeometry(f'clearance overlap query failed: {exc}') from exc
    return distance_mm(_raw(shape), asset, mm_per_native_unit)


def material_state_at_point(shape: Any, point_native: tuple[float, float, float]) -> str:
    """Classify a point against all model solids; return solid/void/boundary."""
    try:
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON
        from OCP.gp import gp_Pnt
    except ModuleNotFoundError as exc:
        raise UnsupportedGeometry("OCCT point classification is unavailable") from exc
    solids = _solids(shape)
    if not solids:
        raise GeometryUnavailable("model has no solids to classify")
    point = gp_Pnt(*point_native)
    try:
        for solid in solids:
            classifier = BRepClass3d_SolidClassifier(_raw(solid), point, 1e-7)
            state = classifier.State()
            if state == TopAbs_ON:
                return "boundary"
            if state == TopAbs_IN:
                return "solid"
        return "void"
    except Exception as exc:
        raise UnsupportedGeometry(f"OCCT point classification failed: {exc}") from exc


def symmetry_difference_volume(shape: Any, plane: str, mm_per_native_unit: float) -> float:
    """Compute symmetric-difference B-rep volume after reflection in a plane."""
    _required_shape(shape)
    if not _solids(shape):
        raise UnsupportedGeometry('volume-based symmetry requires volumetric solids; surface symmetry is not established')
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf

        normal = {"YZ": (1.0, 0.0, 0.0), "XZ": (0.0, 1.0, 0.0), "XY": (0.0, 0.0, 1.0)}[plane]
        transform = gp_Trsf()
        transform.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*normal)))
        reflected = BRepBuilderAPI_Transform(_raw(shape), transform, True).Shape()
        if reflected.IsNull():
            raise UnsupportedGeometry("OCCT could not construct the reflected B-rep")
        first_only = BRepAlgoAPI_Cut(_raw(shape), reflected)
        first_only.Build()
        second_only = BRepAlgoAPI_Cut(reflected, _raw(shape))
        second_only.Build()
        if not first_only.IsDone() or not second_only.IsDone():
            raise UnsupportedGeometry("OCCT symmetric-difference booleans did not complete")
        properties = GProp_GProps()
        BRepGProp.VolumeProperties_s(first_only.Shape(), properties)
        first_volume = float(properties.Mass())
        properties = GProp_GProps()
        BRepGProp.VolumeProperties_s(second_only.Shape(), properties)
        second_volume = float(properties.Mass())
        result = (first_volume + second_volume) * mm_per_native_unit**3
    except ModuleNotFoundError as exc:
        raise UnsupportedGeometry("OCCT B-rep symmetry operations are unavailable") from exc
    except UnsupportedGeometry:
        raise
    except Exception as exc:
        raise UnsupportedGeometry(f"OCCT symmetry comparison failed: {exc}") from exc
    if not math.isfinite(result) or result < 0:
        raise UnsupportedGeometry("OCCT symmetry comparison returned invalid volume")
    return result


def mirror_separation_mm(shape: Any, plane: str, factor: float) -> float:
    """A disjoint mirror's minimum distance is a lower bound on deviation."""
    from build123d import Shape, Plane, mirror
    clean = Shape.cast(_raw(shape))
    return distance_mm(clean, mirror(clean, about=getattr(Plane, plane)), factor)


def _resolve_selector(ctx: Any, selector: str) -> Any:
    """Resolve a selector through S1's trusted resolver; never evaluate code."""
    try:
        from importlib import import_module

        module = import_module("aiad.engine.select")
        resolver = getattr(module, "resolve")
    except (ModuleNotFoundError, AttributeError) as exc:
        raise UnsupportedGeometry("S1 selector resolver is not available") from exc
    try:
        found = resolver(ctx, selector, expect=1)
    except Exception as exc:
        raise UnsupportedGeometry(f"selector resolution failed: {exc}") from exc
    if found is None:
        raise UnsupportedGeometry("selector resolved no entity")
    if isinstance(found, (list, tuple)):
        if len(found) != 1:
            raise UnsupportedGeometry(f"selector resolved {len(found)} entities; exactly one is required")
        found = found[0]
    if hasattr(found, 'wrapped'):
        return found
    binding = getattr(module, 'entity_shape', None)
    if binding is None:
        raise UnsupportedGeometry("S1 resolver returned metadata without the live entity_shape binding API")
    try:
        return binding(ctx, found)
    except Exception as exc:
        raise UnsupportedGeometry(f"native selector binding failed: {exc}") from exc


def _read_numeric(entity: Any, name: str) -> float | None:
    value = getattr(entity, name, None)
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _entity_radius(entity: Any) -> float | None:
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface,BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Cylinder,GeomAbs_Circle,GeomAbs_Sphere
        from OCP.TopAbs import TopAbs_EDGE
        from build123d import Shape
        raw=Shape.cast(_raw(entity)).wrapped
        if raw.ShapeType()==TopAbs_EDGE:
            curve=BRepAdaptor_Curve(raw)
            return float(curve.Circle().Radius()) if curve.GetType()==GeomAbs_Circle else None
        surface = BRepAdaptor_Surface(raw, True)
        if surface.GetType() == GeomAbs_Cylinder:
            return float(surface.Cylinder().Radius())
        if surface.GetType() == GeomAbs_Sphere:
            return float(surface.Sphere().Radius())
    except Exception:
        return None
    return None


def selector_measurement(ctx: Any, measure: dict[str, Any], mm_per_native_unit: float) -> tuple[float, int]:
    """Measure supported selector types through the S1 resolver.

    Returns a value in canonical units and its dimensional power (0 for angle,
    1/2/3 for length/area/volume). Unsupported entity APIs raise explicitly.
    """
    kind = measure["kind"]
    selector_a = measure.get("a", measure.get("selector"))
    first = _resolve_selector(ctx, selector_a)
    if kind == "distance":
        second = _resolve_selector(ctx, measure["b"])
        return distance_mm(first, second, mm_per_native_unit), 1
    if kind == "angle":
        raise UnsupportedGeometry("angle measurement is not supported by the current resolver contract")
    property_name = {"radius": "radius", "length": "length", "area": "area", "volume": "volume"}.get(kind)
    if property_name is None:
        raise UnsupportedGeometry(f"custom measurement kind {kind!r} is unsupported")
    if kind=='radius':
        value=_entity_radius(first)
    elif kind=='volume':
        return volume_mm3(first,mm_per_native_unit),3
    else:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.TopAbs import TopAbs_EDGE,TopAbs_WIRE
        props=GProp_GProps()
        raw=_raw(first)
        if kind=='length':
            if raw.ShapeType() not in {TopAbs_EDGE,TopAbs_WIRE}:
                raise UnsupportedGeometry('length needs an edge or wire')
            BRepGProp.LinearProperties_s(raw,props)
        else:
            if not _faces(first):
                raise UnsupportedGeometry('area needs B-rep faces')
            BRepGProp.SurfaceProperties_s(raw,props)
        value=float(props.Mass())
    if value is None:
        raise UnsupportedGeometry(f"resolved entity does not expose a measurable {kind}")
    power = {"radius": 1, "length": 1, "area": 2, "volume": 3}[kind]
    value=value*mm_per_native_unit**power
    if not math.isfinite(value) or value<0:
        raise UnsupportedGeometry('selector measurement is non-finite or negative')
    return value,power
    return value * mm_per_native_unit**power, power
