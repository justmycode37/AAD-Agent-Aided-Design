"""Lazy, conservative B-rep measurements used by the spec evaluator.

This module deliberately imports build123d/OCCT only inside measurement
functions so importing :mod:`aad_quality` does not load the CAD kernel.
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


def symmetry_exact_polyhedral_match(shape: Any, plane: str, mm_per_native_unit: float) -> bool:
    """Compare planar polygon boundaries after reflection without a B-rep tolerance.

    A zero boolean difference can hide sub-resolution changes because OCCT uses
    geometric tolerances. This stricter path only proves equality for valid
    planar, straight-edged B-reps: each face plane and its exact edge endpoints
    must match the reflected face set. Curved or otherwise unsupported topology
    remains unknown.
    """
    _required_shape(shape)
    try:
        from build123d import Shape
        from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Plane
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf

        raw = _raw(shape)
        if not _solids(shape) or not BRepCheck_Analyzer(raw).IsValid():
            raise UnsupportedGeometry("exact mirror comparison requires valid volumetric B-rep solids")
        normal = {"YZ": (1.0, 0.0, 0.0), "XZ": (0.0, 1.0, 0.0), "XY": (0.0, 0.0, 1.0)}[plane]
        transform = gp_Trsf()
        transform.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*normal)))
        reflected = BRepBuilderAPI_Transform(raw, transform, True).Shape()
        if reflected.IsNull() or not BRepCheck_Analyzer(reflected).IsValid():
            raise UnsupportedGeometry("reflected B-rep is not valid")

        def number(value: float) -> float:
            result = float(value)
            if not math.isfinite(result):
                raise UnsupportedGeometry("exact mirror comparison found a non-finite value")
            return 0.0 if result == 0.0 else result

        def length(value: float) -> float:
            return number(float(value) * mm_per_native_unit)

        def point(vertex: Any) -> tuple[float, float, float]:
            center = vertex.center()
            return (length(center.X), length(center.Y), length(center.Z))

        def signature(candidate: Any) -> tuple[Any, ...]:
            faces = Shape.cast(candidate).faces()
            face_signatures = []
            for face in faces:
                surface = BRepAdaptor_Surface(face.wrapped, True)
                if surface.GetType() != GeomAbs_Plane:
                    raise UnsupportedGeometry("exact mirror comparison currently requires planar faces")
                plane_surface = surface.Plane()
                direction = _xyz(plane_surface.Axis().Direction())
                magnitude = math.sqrt(sum(value * value for value in direction))
                if not math.isfinite(magnitude) or magnitude == 0:
                    raise UnsupportedGeometry("planar face has an invalid normal")
                unit_normal = tuple(value / magnitude for value in direction)
                first_nonzero = next((value for value in unit_normal if abs(value) > 1e-14), None)
                if first_nonzero is None:
                    raise UnsupportedGeometry("planar face has an invalid normal")
                if first_nonzero < 0:
                    unit_normal = tuple(-value for value in unit_normal)
                origin = _xyz(plane_surface.Location())
                offset = sum(unit_normal[index] * origin[index] for index in range(3))
                edges = []
                for edge in face.edges():
                    if BRepAdaptor_Curve(edge.wrapped).GetType() != GeomAbs_Line:
                        raise UnsupportedGeometry("exact mirror comparison currently requires straight edges")
                    endpoints = sorted(point(vertex) for vertex in edge.vertices())
                    if len(endpoints) != 2:
                        raise UnsupportedGeometry("straight face edge does not have two finite endpoints")
                    edges.append(tuple(endpoints))
                if not edges:
                    raise UnsupportedGeometry("planar face has no straight boundary edges")
                face_signatures.append((
                    tuple(number(value) for value in unit_normal),
                    length(offset),
                    tuple(sorted(edges)),
                ))
            return tuple(sorted(face_signatures))

        return signature(raw) == signature(reflected)
    except UnsupportedGeometry:
        raise
    except Exception as exc:
        raise UnsupportedGeometry(f"exact polyhedral mirror comparison failed: {exc}") from exc


def mirror_separation_mm(shape: Any, plane: str, factor: float) -> float:
    """A disjoint mirror's minimum distance is a lower bound on deviation."""
    from build123d import Shape, Plane, mirror
    clean = Shape.cast(_raw(shape))
    return distance_mm(clean, mirror(clean, about=getattr(Plane, plane)), factor)


def _resolve_selector(ctx: Any, selector: str) -> Any:
    """Resolve a selector through S1's trusted resolver; never evaluate code."""
    _, shape = _resolve_selector_entity(ctx, selector)
    return shape


def _resolve_selector_entity(ctx: Any, selector: str) -> tuple[Any, Any]:
    """Return a selected entity and its live B-rep shape from S1's resolver."""
    try:
        from importlib import import_module

        module = import_module("aad.engine.select")
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
        return found, found
    binding = getattr(module, 'entity_shape', None)
    if binding is None:
        raise UnsupportedGeometry("S1 resolver returned metadata without the live entity_shape binding API")
    try:
        return found, binding(ctx, found)
    except Exception as exc:
        raise UnsupportedGeometry(f"native selector binding failed: {exc}") from exc


def selector_position_mm(ctx: Any, selector: str, mm_per_native_unit: float) -> tuple[float, float, float]:
    """Measure the center of one selected feature entity and return XYZ in mm."""
    from build123d import CenterOf, Shape

    entity, topology = _resolve_selector_entity(ctx, selector)
    selected = Shape.cast(_raw(topology))
    center = selected.center() if getattr(entity, "kind", None) == "vertex" else selected.center(CenterOf.MASS)
    point = tuple(float(value) * mm_per_native_unit for value in (center.X, center.Y, center.Z))
    if any(not math.isfinite(value) for value in point):
        raise UnsupportedGeometry("selected entity center is not finite")
    return point


def hole_axis_position_mm(
    ctx: Any,
    selector: str,
    target_mm: tuple[float, float, float],
    mm_per_native_unit: float,
) -> tuple[tuple[float, float, float], float]:
    """Measure a target's shortest distance to a selected inward cylindrical hole axis."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_REVERSED, TopAbs_FACE

    entity, topology = _resolve_selector_entity(ctx, selector)
    raw_face = _raw(topology)
    if getattr(entity, "kind", None) != "face" or raw_face.ShapeType() != TopAbs_FACE:
        raise UnsupportedGeometry("hole position requires a face selector")
    surface = BRepAdaptor_Surface(raw_face, True)
    if surface.GetType() != GeomAbs_Cylinder:
        raise UnsupportedGeometry("hole position selector must resolve to a cylindrical face")
    cylinder = surface.Cylinder()
    if (raw_face.Orientation() == TopAbs_REVERSED) != cylinder.Direct():
        raise UnsupportedGeometry("hole position selector is not an inward-facing bore")
    u_min, u_max, _, _ = BRepTools.UVBounds_s(raw_face)
    if abs(float(u_max) - float(u_min)) < 2.0 * math.pi - 1e-6:
        raise UnsupportedGeometry("hole position selector is not a full-revolution cylindrical wall")
    direction_raw = _xyz(cylinder.Axis().Direction())
    magnitude = math.hypot(*direction_raw)
    if not math.isfinite(magnitude) or magnitude == 0:
        raise UnsupportedGeometry("hole axis direction is invalid")
    direction = tuple(value / magnitude for value in direction_raw)
    origin = tuple(value * mm_per_native_unit for value in _xyz(cylinder.Location()))
    delta = tuple(target_mm[index] - origin[index] for index in range(3))
    along = sum(delta[index] * direction[index] for index in range(3))
    closest = tuple(origin[index] + along * direction[index] for index in range(3))
    distance = math.dist(target_mm, closest)
    if not math.isfinite(distance):
        raise UnsupportedGeometry("hole centerline distance is not finite")
    return closest, distance


def _fit_operand(ctx: Any, name_or_selector: str) -> Any:
    selector = name_or_selector.lstrip()
    if selector.startswith(("face(", "edge(", "vertex(")):
        return _resolve_selector(ctx, name_or_selector)
    shape = getattr(ctx, "shape", None)
    assembly = (getattr(ctx, "meta", None) or {}).get("assembly")
    names = assembly.get("components") if isinstance(assembly, dict) else None
    if not isinstance(names, list) or shape is None:
        raise UnsupportedGeometry(f"named body {name_or_selector!r} is not available; use an assembly component name or selector")
    try:
        children = list(shape)
    except Exception as exc:
        raise UnsupportedGeometry(f"assembly bodies could not be enumerated: {exc}") from exc
    if len(names) != len(children):
        raise UnsupportedGeometry("assembly component names do not match the stored body geometry")
    matches = [child for name, child in zip(names, children, strict=True) if name == name_or_selector]
    if len(matches) != 1:
        raise UnsupportedGeometry(f"named body {name_or_selector!r} resolved to {len(matches)} assembly components")
    return matches[0]


def _solid_overlap_mm3(first: Any, second: Any, mm_per_native_unit: float) -> float:
    """Return exact common B-rep solid volume, or unknown if its boolean is incomplete."""
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        common = BRepAlgoAPI_Common(_raw(first), _raw(second))
        common.Build()
        if not common.IsDone():
            raise UnsupportedGeometry("OCCT common-volume boolean did not complete")
        result_shape = common.Shape()
        if result_shape.IsNull():
            volume = 0.0
        else:
            from OCP.TopAbs import TopAbs_SOLID
            from OCP.TopExp import TopExp_Explorer

            solids = TopExp_Explorer(result_shape, TopAbs_SOLID)
            if not solids.More():
                volume = 0.0
            else:
                if not BRepCheck_Analyzer(result_shape).IsValid():
                    raise UnsupportedGeometry("OCCT common-volume result is invalid")
                properties = GProp_GProps()
                BRepGProp.VolumeProperties_s(result_shape, properties)
                volume = float(properties.Mass()) * mm_per_native_unit**3
    except ModuleNotFoundError as exc:
        raise UnsupportedGeometry("OCCT common-volume operations are unavailable") from exc
    except UnsupportedGeometry:
        raise
    except Exception as exc:
        raise UnsupportedGeometry(f"OCCT common-volume boolean failed: {exc}") from exc
    if not math.isfinite(volume) or volume < 0:
        raise UnsupportedGeometry("common-volume result is not finite and non-negative")
    return volume


def fit_clearance_mm(ctx: Any, a: str, b: str, mm_per_native_unit: float) -> tuple[float | None, float]:
    """Measure pair clearance; positive solid overlap is reported separately."""
    first, second = _fit_operand(ctx, a), _fit_operand(ctx, b)
    first_solids, second_solids = _solids(first), _solids(second)
    overlap = _solid_overlap_mm3(first, second, mm_per_native_unit) if first_solids and second_solids else 0.0
    if overlap > 0:
        return None, overlap
    return distance_mm(first, second, mm_per_native_unit), overlap


def fit_interference_mm3(ctx: Any, a: str, b: str, mm_per_native_unit: float) -> float:
    """Measure the B-rep common volume of two named solid bodies."""
    first, second = _fit_operand(ctx, a), _fit_operand(ctx, b)
    if not _solids(first) or not _solids(second):
        raise UnsupportedGeometry("interference volume requires two named bodies or selectors that each resolve to solids")
    return _solid_overlap_mm3(first, second, mm_per_native_unit)


def _unoriented_angle_degrees(first_entity: Any, first: Any, second_entity: Any, second: Any) -> float:
    """Measure the smaller angle between two planar faces or two straight edges."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Plane

    entities = (first_entity, second_entity)
    shapes = (first, second)
    directions = []
    if all(getattr(entity, "kind", None) == "face" for entity in entities):
        for shape in shapes:
            surface = BRepAdaptor_Surface(_raw(shape), True)
            if surface.GetType() != GeomAbs_Plane:
                raise UnsupportedGeometry("angle between faces requires two planar faces")
            directions.append(_xyz(surface.Plane().Axis().Direction()))
    elif all(getattr(entity, "kind", None) == "edge" for entity in entities):
        for shape in shapes:
            curve = BRepAdaptor_Curve(_raw(shape))
            if curve.GetType() != GeomAbs_Line:
                raise UnsupportedGeometry("angle between edges requires two straight edges")
            directions.append(_xyz(curve.Line().Direction()))
    else:
        raise UnsupportedGeometry("angle requires two planar faces or two straight edges of the same kind")
    dot = sum(directions[0][index] * directions[1][index] for index in range(3))
    value = math.degrees(math.acos(min(1.0, max(0.0, abs(dot)))))
    if not math.isfinite(value):
        raise UnsupportedGeometry("angle result is not finite")
    return value


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
    first_entity, first = _resolve_selector_entity(ctx, selector_a) if kind == "angle" else (None, _resolve_selector(ctx, selector_a))
    if kind == "distance":
        second = _resolve_selector(ctx, measure["b"])
        return distance_mm(first, second, mm_per_native_unit), 1
    if kind == "angle":
        second_entity, second = _resolve_selector_entity(ctx, measure["b"])
        return _unoriented_angle_degrees(first_entity, first, second_entity, second), 0
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
