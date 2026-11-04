"""Exact answers read from the B-rep, not the render mesh.

An image shows where to look; these functions say what is there. Numbers come from OCCT's
surfaces, curves and mass-property algorithms, independently of the render resolution.
OCP is imported on first use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

TANGENT_DEG = 1.0  # Below this angle between face normals an edge is smooth, not a crease.
RAY_REACH_FACTOR = 8.0


def _xyz(value: Any) -> list[float]:
    return [_clean(value.X()), _clean(value.Y()), _clean(value.Z())]


def _clean(value: float) -> float:
    """Round off float noise so reports repeat exactly and -0.0 never appears."""
    rounded = round(float(value), 9)
    return rounded + 0.0


@dataclass(slots=True)
class Topology:
    """Index lookup for one shape's faces and edges, in the order selectors count them."""

    shape: Any
    faces: list[Any]
    edges: list[Any]
    _face_map: Any
    _edge_map: Any
    _edge_faces: Any
    _classes: dict[int, tuple[str, float | None, list[int]]] = field(default_factory=dict)

    @classmethod
    def of(cls, shape: Any) -> Topology:
        from OCP.TopAbs import TopAbs_ShapeEnum
        from OCP.TopExp import TopExp
        from OCP.collections import (
            IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as AncestorMap,
            IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap,
        )

        faces = [face.wrapped for face in shape.faces()]
        edges = [edge.wrapped for edge in shape.edges()]
        face_map = ShapeMap()
        for face in faces:
            face_map.Add(face)
        edge_map = ShapeMap()
        for edge in edges:
            edge_map.Add(edge)
        ancestors = AncestorMap()
        TopExp.MapShapesAndAncestors_s(
            shape.wrapped, TopAbs_ShapeEnum.TopAbs_EDGE, TopAbs_ShapeEnum.TopAbs_FACE, ancestors
        )
        return cls(shape.wrapped, faces, edges, face_map, edge_map, ancestors)

    def face_index(self, face: Any) -> int | None:
        found = self._face_map.FindIndex(face)
        return found - 1 if found > 0 else None

    def edge_index(self, edge: Any) -> int | None:
        found = self._edge_map.FindIndex(edge)
        return found - 1 if found > 0 else None

    def faces_of_edge(self, edge: Any) -> list[Any]:
        from OCP.TopoDS import TopoDS

        if not self._edge_faces.Contains(edge):
            return []
        distinct: list[Any] = []
        for ancestor in self._edge_faces.FindFromKey(edge):
            face = TopoDS.Face(ancestor)
            if not any(face.IsSame(known) for known in distinct):
                distinct.append(face)
        return distinct


def shape_facts(shape: Any) -> dict[str, Any]:
    """Size and mass properties of the whole shape."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    low = box.CornerMin()
    high = box.CornerMax()
    area = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, area)
    volume = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, volume)
    return {
        "bbox": {
            "min": _xyz(low),
            "max": _xyz(high),
            "size": [_clean(high.X() - low.X()), _clean(high.Y() - low.Y()), _clean(high.Z() - low.Z())],
        },
        "area": _clean(area.Mass()),
        "volume": _clean(volume.Mass()),
        "center_of_mass": _xyz(volume.CentreOfMass()),
    }


def box_of(shapes: list[Any]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Tight world box around OCCT shapes."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    for shape in shapes:
        BRepBndLib.AddOptimal_s(shape, box, False, False)
    low, high = box.CornerMin(), box.CornerMax()
    return (low.X(), low.Y(), low.Z()), (high.X(), high.Y(), high.Z())


def _outward_normal(adaptor: Any, face: Any, u: float, v: float) -> tuple[float, float, float] | None:
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.TopAbs import TopAbs_Orientation

    props = BRepLProp_SLProps(adaptor, u, v, 1, 1.0e-9)
    if not props.IsNormalDefined():
        return None
    normal = props.Normal()
    sign = -1.0 if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED else 1.0
    return (sign * normal.X(), sign * normal.Y(), sign * normal.Z())


def face_facts(face: Any) -> dict[str, Any]:
    """Surface type and defining dimensions of one face."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_SurfaceType as Kind
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    adaptor = BRepAdaptor_Surface(face)
    kind = adaptor.GetType()
    u0, u1 = adaptor.FirstUParameter(), adaptor.LastUParameter()
    v0, v1 = adaptor.FirstVParameter(), adaptor.LastVParameter()
    center = props.CentreOfMass()
    facts: dict[str, Any] = {
        "type": _surface_name(kind),
        "area": _clean(props.Mass()),
        "centroid": _xyz(center),
    }
    low, high = box_of([face])
    facts["bbox"] = {"min": [_clean(c) for c in low], "max": [_clean(c) for c in high]}
    middle = _outward_normal(adaptor, face, (u0 + u1) * 0.5, (v0 + v1) * 0.5) if _finite(u0, u1, v0, v1) else None
    if kind == Kind.GeomAbs_Plane:
        if middle is not None:
            facts["normal"] = [_clean(c) for c in middle]
            facts["plane_offset"] = _clean(sum(n * c for n, c in zip(middle, (center.X(), center.Y(), center.Z()))))
    elif kind == Kind.GeomAbs_Cylinder:
        cylinder = adaptor.Cylinder()
        axis = cylinder.Axis()
        facts.update(_round_facts(axis, cylinder.Radius(), adaptor, face, u0, u1, v0, v1))
    elif kind == Kind.GeomAbs_Cone:
        cone = adaptor.Cone()
        facts["semi_angle_deg"] = _clean(math.degrees(cone.SemiAngle()))
        facts["reference_radius"] = _clean(cone.RefRadius())
        facts["apex"] = _xyz(cone.Apex())
        facts["axis_direction"] = _xyz(cone.Axis().Direction())
    elif kind == Kind.GeomAbs_Sphere:
        sphere = adaptor.Sphere()
        facts["radius"] = _clean(sphere.Radius())
        facts["center"] = _xyz(sphere.Location())
    elif kind == Kind.GeomAbs_Torus:
        torus = adaptor.Torus()
        facts["major_radius"] = _clean(torus.MajorRadius())
        facts["minor_radius"] = _clean(torus.MinorRadius())
        facts["center"] = _xyz(torus.Location())
        facts["axis_direction"] = _xyz(torus.Axis().Direction())
    return facts


def _finite(*values: float) -> bool:
    return all(math.isfinite(value) and abs(value) < 1.0e50 for value in values)


def _round_facts(axis: Any, radius: float, adaptor: Any, face: Any, u0: float, u1: float, v0: float, v1: float) -> dict[str, Any]:
    location, direction = axis.Location(), axis.Direction()
    middle_v = (v0 + v1) * 0.5
    point_on_axis = [location.X() + direction.X() * middle_v, location.Y() + direction.Y() * middle_v, location.Z() + direction.Z() * middle_v]
    surface_point = adaptor.Value((u0 + u1) * 0.5, middle_v)
    normal = _outward_normal(adaptor, face, (u0 + u1) * 0.5, middle_v)
    radial = (surface_point.X() - point_on_axis[0], surface_point.Y() - point_on_axis[1], surface_point.Z() - point_on_axis[2])
    return {
        "radius": _clean(radius),
        "diameter": _clean(radius * 2.0),
        "axis_point": [_clean(c) for c in point_on_axis],
        "axis_direction": _xyz(direction),
        "axial_length": _clean(v1 - v0),
        "sweep_deg": _clean(math.degrees(u1 - u0)),
        # An outward normal pointing at the axis means material surrounds the surface: a hole.
        "internal": bool(normal is not None and sum(n * r for n, r in zip(normal, radial)) < 0.0),
    }


def _surface_name(kind: Any) -> str:
    from OCP.GeomAbs import GeomAbs_SurfaceType as Kind

    return {
        Kind.GeomAbs_Plane: "plane",
        Kind.GeomAbs_Cylinder: "cylinder",
        Kind.GeomAbs_Cone: "cone",
        Kind.GeomAbs_Sphere: "sphere",
        Kind.GeomAbs_Torus: "torus",
        Kind.GeomAbs_BezierSurface: "bezier",
        Kind.GeomAbs_BSplineSurface: "bspline",
        Kind.GeomAbs_SurfaceOfRevolution: "revolution",
        Kind.GeomAbs_SurfaceOfExtrusion: "extrusion",
        Kind.GeomAbs_OffsetSurface: "offset",
    }.get(kind, "other")


def edge_class(edge: Any, topology: Topology) -> tuple[str, float | None, list[int]]:
    """How an edge sits between its faces: sharp, tangent, seam, free or nonmanifold.

    Returns the class, the angle in degrees between the outward normals at the edge's midpoint,
    and the indexes of the adjoining faces.
    """
    from OCP.BRep import BRep_Tool

    known = topology.edge_index(edge)
    if known is not None and known in topology._classes:
        return topology._classes[known]
    result = _classify_edge(edge, topology, BRep_Tool)
    if known is not None:
        topology._classes[known] = result
    return result


def _classify_edge(edge: Any, topology: Topology, BRep_Tool: Any) -> tuple[str, float | None, list[int]]:
    faces = topology.faces_of_edge(edge)
    indexes = [index for index in (topology.face_index(face) for face in faces) if index is not None]
    if len(faces) == 1:
        return ("seam" if BRep_Tool.IsClosed_s(edge, faces[0]) else "free"), None, indexes
    if len(faces) != 2:
        return ("nonmanifold" if faces else "free"), None, indexes
    first = _normal_on_edge(edge, faces[0])
    second = _normal_on_edge(edge, faces[1])
    if first is None or second is None:
        return "sharp", None, indexes
    cosine = max(-1.0, min(1.0, sum(a * b for a, b in zip(first, second))))
    angle = math.degrees(math.acos(cosine))
    return ("tangent" if angle < TANGENT_DEG else "sharp"), _clean(angle), indexes


def _normal_on_edge(edge: Any, face: Any) -> tuple[float, float, float] | None:
    from OCP.BRepAdaptor import BRepAdaptor_Curve2d, BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType
    from OCP.TopAbs import TopAbs_Orientation

    try:
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() == GeomAbs_SurfaceType.GeomAbs_Plane:  # No need for a slow curve on the surface.
            plane = adaptor.Plane()
            axis = plane.Axis().Direction()
            sign = 1.0 if plane.Direct() else -1.0
            if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
                sign = -sign
            return (sign * axis.X(), sign * axis.Y(), sign * axis.Z())
        if adaptor.GetType() == GeomAbs_SurfaceType.GeomAbs_Cylinder:
            return _cylinder_normal(edge, face, adaptor)
        curve = BRepAdaptor_Curve2d(edge, face)
        uv = curve.Value((curve.FirstParameter() + curve.LastParameter()) * 0.5)
        return _outward_normal(adaptor, face, uv.X(), uv.Y())
    except Exception:
        return None


def _cylinder_normal(edge: Any, face: Any, adaptor: Any) -> tuple[float, float, float]:
    """Radial direction at the edge's midpoint; a cylinder's normal needs no surface parameters."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopAbs import TopAbs_Orientation

    curve = BRepAdaptor_Curve(edge)
    point = curve.Value((curve.FirstParameter() + curve.LastParameter()) * 0.5)
    cylinder = adaptor.Cylinder()
    origin, axis = cylinder.Axis().Location(), cylinder.Axis().Direction()
    relative = (point.X() - origin.X(), point.Y() - origin.Y(), point.Z() - origin.Z())
    along = relative[0] * axis.X() + relative[1] * axis.Y() + relative[2] * axis.Z()
    radial = (relative[0] - along * axis.X(), relative[1] - along * axis.Y(), relative[2] - along * axis.Z())
    length = math.sqrt(sum(c * c for c in radial)) or 1.0
    # An unreversed cylinder's surface normal points away from the axis; a left-handed one points in.
    sign = 1.0 if cylinder.Direct() else -1.0
    if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
        sign = -sign
    return (sign * radial[0] / length, sign * radial[1] / length, sign * radial[2] / length)


def edge_facts(edge: Any, topology: Topology | None = None) -> dict[str, Any]:
    """Curve type, length and ends of one edge, plus its class when the topology is known."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_CurveType as Kind
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    curve = BRepAdaptor_Curve(edge)
    kind = curve.GetType()
    first, last = curve.FirstParameter(), curve.LastParameter()
    facts: dict[str, Any] = {
        "type": _curve_name(kind),
        "length": _clean(props.Mass()),
        "start": _xyz(curve.Value(first)),
        "end": _xyz(curve.Value(last)),
    }
    if kind == Kind.GeomAbs_Line:
        facts["direction"] = _xyz(curve.Line().Direction())
    elif kind == Kind.GeomAbs_Circle:
        circle = curve.Circle()
        facts["radius"] = _clean(circle.Radius())
        facts["center"] = _xyz(circle.Location())
        facts["axis_direction"] = _xyz(circle.Axis().Direction())
        facts["sweep_deg"] = _clean(math.degrees(last - first))
    elif kind == Kind.GeomAbs_Ellipse:
        ellipse = curve.Ellipse()
        facts["major_radius"] = _clean(ellipse.MajorRadius())
        facts["minor_radius"] = _clean(ellipse.MinorRadius())
        facts["center"] = _xyz(ellipse.Location())
    if topology is not None:
        name, angle, faces = edge_class(edge, topology)
        facts["class"] = name
        facts["faces"] = [f"face:{index}" for index in faces]
        if angle is not None:
            facts["normal_angle_deg"] = angle
    return facts


def _curve_name(kind: Any) -> str:
    from OCP.GeomAbs import GeomAbs_CurveType as Kind

    return {
        Kind.GeomAbs_Line: "line",
        Kind.GeomAbs_Circle: "circle",
        Kind.GeomAbs_Ellipse: "ellipse",
        Kind.GeomAbs_Hyperbola: "hyperbola",
        Kind.GeomAbs_Parabola: "parabola",
        Kind.GeomAbs_BezierCurve: "bezier",
        Kind.GeomAbs_BSplineCurve: "bspline",
    }.get(kind, "other")


def edge_polyline(edge: Any, linear: float, angular: float) -> list[tuple[float, float, float]] | None:
    """Points on the true curve, spaced so the chord error stays under ``linear``."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_TangentialDeflection

    try:
        curve = BRepAdaptor_Curve(edge)
        sampler = GCPnts_TangentialDeflection(curve, angular, linear, 2)
    except Exception:
        return None
    count = sampler.NbPoints()
    if count < 2:
        return None
    return [(p.X(), p.Y(), p.Z()) for p in (sampler.Value(index) for index in range(1, count + 1))]


def section_loops(cap_faces: list[Any]) -> list[dict[str, Any]]:
    """Exact outline of each cut region: area, centroid, and every loop's lines and arcs."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_Orientation, TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    regions = []
    for face in cap_faces:
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        outer = BRepTools.OuterWire_s(face)
        low, high = box_of([face])
        region: dict[str, Any] = {
            "area": _clean(props.Mass()),
            "centroid": _xyz(props.CentreOfMass()),
            "bbox": {"min": [_clean(c) for c in low], "max": [_clean(c) for c in high]},
            "loops": [],
        }
        explorer = TopExp_Explorer(face, TopAbs_ShapeEnum.TopAbs_WIRE)
        while explorer.More():
            wire = TopoDS.Wire(explorer.Current())
            segments = []
            walker = BRepTools_WireExplorer(wire, face)
            perimeter = 0.0
            while walker.More():
                edge = walker.Current()
                facts = edge_facts(edge)
                if edge.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
                    facts["start"], facts["end"] = facts["end"], facts["start"]
                    if "sweep_deg" in facts:
                        facts["sweep_deg"] = -facts["sweep_deg"]
                perimeter += facts["length"]
                if "direction" in facts:
                    del facts["direction"]
                segments.append(facts)
                walker.Next()
            region["loops"].append(
                {"outer": wire.IsSame(outer), "perimeter": _clean(perimeter), "segments": segments}
            )
            explorer.Next()
        region["loops"].sort(key=lambda loop: not loop["outer"])
        regions.append(region)
    return regions


class Rays:
    """Exact line-against-B-rep intersection for one shape."""

    def __init__(self, shape: Any, topology: Topology, reach: float) -> None:
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

        self._intersector = IntCurvesFace_ShapeIntersector()
        self._intersector.Load(shape, 1.0e-7)
        self._topology = topology
        self._reach = reach * RAY_REACH_FACTOR
        low, high = box_of([shape])
        self._center = tuple((a + b) * 0.5 for a, b in zip(low, high))

    def hits(self, origin: tuple[float, ...], direction: tuple[float, ...], *, back: float = 0.0) -> list[dict[str, Any]]:
        """Crossings in order along the line, from ``back`` behind the origin onward."""
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

        line = gp_Lin(gp_Pnt(*origin), gp_Dir(*direction))
        # World-space probes may start much farther from the part than an image camera does.
        reach = self._reach + math.dist(origin, self._center)
        self._intersector.Perform(line, -back, reach)
        found = []
        for index in range(1, self._intersector.NbPnt() + 1):
            found.append(
                {
                    "t": self._intersector.WParameter(index),
                    "u": self._intersector.UParameter(index),
                    "v": self._intersector.VParameter(index),
                    "point": self._intersector.Pnt(index),
                    "face": self._intersector.Face(index),
                }
            )
        found.sort(key=lambda hit: hit["t"])
        return found

    def wall_at(self, point: tuple[float, ...], inward: tuple[float, ...], tolerance: float) -> dict[str, Any] | None:
        """Exact solid path from the surface near ``point`` along ``inward`` to where it exits."""
        hits = self.hits(point, inward, back=tolerance)
        entry = next((hit for hit in hits if abs(hit["t"]) <= tolerance), None)
        if entry is None:
            return None
        exits = [hit for hit in hits if hit["t"] > entry["t"] + 1.0e-7]
        if not exits:
            return None
        leaving = exits[0]
        return {
            "thickness": leaving["t"] - entry["t"],
            "entry": entry,
            "exit": leaving,
        }

    def describe(self, hit: dict[str, Any]) -> dict[str, Any]:
        """What the ray landed on: the face, the exact point and normal, and local curvature."""
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepLProp import BRepLProp_SLProps

        face = hit["face"]
        adaptor = BRepAdaptor_Surface(face)
        normal = _outward_normal(adaptor, face, hit["u"], hit["v"])
        props = BRepLProp_SLProps(adaptor, hit["u"], hit["v"], 2, 1.0e-9)
        result: dict[str, Any] = {
            "face_index": self._topology.face_index(face),
            "point": _xyz(hit["point"]),
            "surface": _surface_name(adaptor.GetType()),
        }
        if normal is not None:
            result["normal"] = [_clean(c) for c in normal]
        if props.IsCurvatureDefined():
            largest = max(abs(props.MinCurvature()), abs(props.MaxCurvature()))
            result["min_radius"] = _clean(1.0 / largest) if largest > 1.0e-9 else None
        return result


def distance(first: Any, second: Any) -> dict[str, Any] | None:
    """Exact minimum distance between two shapes, with the closest points."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    solver = BRepExtrema_DistShapeShape(first, second)
    if not solver.IsDone() or solver.NbSolution() < 1:
        return None
    return {
        "distance": _clean(solver.Value()),
        "point_a": _xyz(solver.PointOnShape1(1)),
        "point_b": _xyz(solver.PointOnShape2(1)),
    }


def direction_of(shape: Any) -> tuple[str, tuple[float, float, float]] | None:
    """The fixed direction of a planar face (its normal) or a straight edge (its tangent)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_CurveType, GeomAbs_SurfaceType
    from OCP.TopAbs import TopAbs_ShapeEnum, TopAbs_Orientation
    from OCP.TopoDS import TopoDS

    if shape.ShapeType() == TopAbs_ShapeEnum.TopAbs_FACE:
        face = TopoDS.Face(shape)
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
            return None
        axis = adaptor.Plane().Axis().Direction()
        sign = 1.0 if adaptor.Plane().Direct() else -1.0
        if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
            sign = -sign
        return "normal", (sign * axis.X(), sign * axis.Y(), sign * axis.Z())
    if shape.ShapeType() == TopAbs_ShapeEnum.TopAbs_EDGE:
        curve = BRepAdaptor_Curve(TopoDS.Edge(shape))
        if curve.GetType() != GeomAbs_CurveType.GeomAbs_Line:
            return None
        axis = curve.Line().Direction()
        return "tangent", (axis.X(), axis.Y(), axis.Z())
    return None


def point_shape(point: tuple[float, float, float]) -> Any:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Pnt

    return BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex()


def surface_adaptor(face: Any) -> Any:
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    return BRepAdaptor_Surface(face)


def largest_curvature(adaptor: Any, u: float, v: float) -> float:
    from OCP.BRepLProp import BRepLProp_SLProps

    props = BRepLProp_SLProps(adaptor, u, v, 2, 1.0e-9)
    if not props.IsCurvatureDefined():
        return 0.0
    return max(abs(props.MinCurvature()), abs(props.MaxCurvature()))


def is_inside(shape: Any, point: tuple[float, float, float], tolerance: float = 1.0e-7) -> bool | None:
    """Whether a point lies in solid material. None when the classifier cannot say."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_State

    try:
        classifier = BRepClass3d_SolidClassifier(shape)
        classifier.Perform(gp_Pnt(*point), tolerance)
        return classifier.State() == TopAbs_State.TopAbs_IN
    except Exception:
        return None
