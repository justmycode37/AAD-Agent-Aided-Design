"""Measured cylindrical features for drawing callouts and axial station tables.

Values come from OCCT surfaces. Face indices are transient provenance for the
current drawing, never persistent selectors. A partial cylindrical surface is
reported as a radius, so a roundover cannot masquerade as a complete bore.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .geometry import wrapped_shape


@dataclass(frozen=True)
class CylindricalFeature:
    id: str
    radius: float
    axis: tuple[float, float, float]
    axis_origin: tuple[float, float, float]
    station_min: float
    station_max: float
    interior: bool
    full_circle: bool
    face_indices: tuple[int, ...]
    units: str

    @property
    def length(self) -> float:
        return self.station_max - self.station_min

    @property
    def dimension_value(self) -> float:
        return self.radius * (2 if self.full_circle else 1)

    def dimension(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": "cylinder_diameter" if self.full_circle else "cylinder_radius",
            "value": self.dimension_value,
            "unit": self.units,
            "display": f"{'DIA' if self.full_circle else 'R'} {self.dimension_value:g}",
            "axis": list(self.axis),
            "axis_origin": list(self.axis_origin),
            "station_min": self.station_min,
            "station_max": self.station_max,
            "cylindrical_length": self.length,
            "interior": self.interior,
            "source": {
                "type": "OCCT cylindrical surface",
                "face_indices": list(self.face_indices),
                "measurement": "2 * surface.radius" if self.full_circle else "surface.radius",
                "stations": "axis.Location dot unit_axis + signed surface.VParameter",
                "length": "station_max - station_min (cylindrical surface only)",
            },
        }


def _dot(a, b):
    return sum(x*y for x,y in zip(a,b))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _coverage(intervals):
    merged = []
    for first,last in sorted(intervals):
        if merged and first <= merged[-1][1] + 1e-8:
            merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
        else:
            merged.append((first,last))
    return sum(b-a for a,b in merged)


def measure_cylinders(shape: Any, units: str = "mm") -> list[CylindricalFeature]:
    """Measure complete and partial cylindrical walls on every axis.

    Adjacent axial stages remain separate. Seam-split patches with the same
    station interval are merged by angular union, rather than counting faces
    as holes. Interior means the face orientation points into the cylindrical
    void; no unproven THRU, blind-depth, thread or counterbore label is added.
    """
    if units not in {"mm", "in"}:
        raise ValueError("Feature units must be mm or in.")
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    groups = {}
    explorer = TopExp_Explorer(wrapped_shape(shape), TopAbs_FACE)
    face_index = 0
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            origin = tuple(cylinder.Location().Coord())
            raw_axis = tuple(cylinder.Axis().Direction().Coord())
            main = max(range(3), key=lambda i: abs(raw_axis[i]))
            sign = 1.0 if raw_axis[main] > 0 else -1.0
            axis = tuple(v*sign for v in raw_axis)
            axis_origin = tuple(v-axis[i]*_dot(origin,axis) for i,v in enumerate(origin))
            first_v, last_v = surface.FirstVParameter(), surface.LastVParameter()
            first_u, last_u = surface.FirstUParameter(), surface.LastUParameter()
            radius = cylinder.Radius()
            values = (*origin,*axis,radius,first_v,last_v,first_u,last_u)
            if not all(math.isfinite(v) for v in values):
                raise ValueError("Cannot dimension an unbounded cylindrical surface.")
            stations = sorted(_dot(origin,axis) + sign*v for v in (first_v,last_v))
            interior = face.Orientation() == TopAbs_REVERSED
            key = tuple(round(v,7) for v in (*axis,*axis_origin,radius,*stations)) + (interior,)
            group = groups.setdefault(key, {"axis":axis,"origin":axis_origin,"radius":radius,
                                           "stations":stations,"interior":interior,"faces":[],"angles":[]})
            group["faces"].append(face_index)
            # Express each patch in one canonical polar frame, including seams
            # and oppositely oriented surface axes. This prevents duplicate
            # patches from manufacturing a fictitious 360-degree diameter.
            hint = (1,0,0) if abs(axis[0]) < 0.8 else (0,1,0)
            basis = _cross(axis,hint)
            norm = math.sqrt(_dot(basis,basis))
            basis = tuple(v/norm for v in basis)
            vertical = _cross(axis,basis)
            start = surface.Value(first_u,(first_v+last_v)/2)
            delta = tuple(v-origin[i] for i,v in enumerate(start.Coord()))
            angle = math.atan2(_dot(delta,vertical), _dot(delta,basis))
            span = min(2*math.pi, abs(last_u-first_u))
            if sign < 0:
                angle -= span
            angle %= 2*math.pi
            if angle+span <= 2*math.pi:
                group["angles"].append((angle,angle+span))
            else:
                group["angles"].extend(((angle,2*math.pi),(0,angle+span-2*math.pi)))
        face_index += 1
        explorer.Next()
    features = []
    for index, (_, group) in enumerate(sorted(groups.items())):
        first,last = group["stations"]
        features.append(CylindricalFeature(
            id=f"cylinder.{index+1}", radius=float(group["radius"]), axis=group["axis"],
            axis_origin=group["origin"], station_min=first, station_max=last,
            interior=group["interior"], full_circle=_coverage(group["angles"]) >= 2*math.pi-1e-6,
            face_indices=tuple(group["faces"]), units=units))
    return features
