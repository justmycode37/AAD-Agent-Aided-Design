"""Native BREP measurements. Import/call this inside a trusted kernel worker."""

from __future__ import annotations

from dataclasses import asdict, fields
import json
import math
from typing import Any

from .types import Entity, Failure, ModelContext

_KINDS = {
    "distance", "clearance", "angle", "radius", "area", "volume", "inertia", "mass", "thickness",
    "length", "bbox", "center_of_mass", "principal_inertia",
}


class MeasurementError(ValueError):
    """Structured failure, also exposing fields used by the platform adapter."""

    def __init__(self, failure: Failure):
        self.failure = failure
        for item in fields(Failure):
            setattr(self, item.name, getattr(failure, item.name))
        super().__init__(failure.message)


def measure(ctx: ModelContext, kind: str, a: str | None = None, b: str | None = None) -> dict[str, Any]:
    """Measure native geometry in its declared coordinates, never cached metrics.

    Selectors resolve to exactly one entity through the engine resolver. Whole
    volume/inertia sum independent bodies, including overlapping bodies; they
    do not compute a material union. See measure.md for precise definitions.
    """
    try:
        if not isinstance(kind, str) or kind not in _KINDS:
            raise _error(f"Unsupported measurement kind: {kind!r}.")
        if (not isinstance(getattr(ctx, "units", None), str) or ctx.units not in {"mm", "in"}
                or not isinstance(getattr(ctx, "kind", None), str) or ctx.kind not in {"part", "assembly"}):
            raise _error("Native 3D measurement requires declared units mm/in and a 3D context.")
        for selector in (a, b):
            if selector is not None and (not isinstance(selector, str) or not selector.strip()):
                raise _error("Measurement selectors must be nonempty text.")
        if kind in {"distance", "clearance", "angle"} and (a is None or b is None):
            raise _error(f"{kind} requires both a and b selectors.")
        if kind == "radius" and a is None:
            raise _error("radius requires an a selector.")
        if kind in {"radius", "area"} and b is not None:
            raise _error(f"{kind} does not accept a b selector.")
        if kind == "length" and a is None:
            raise _error("length requires an a selector for an edge.")
        if kind in {"length", "bbox"} and b is not None:
            raise _error(f"{kind} does not accept a b selector.")
        if kind in {"volume", "inertia", "mass", "thickness", "center_of_mass", "principal_inertia"} and (a is not None or b is not None):
            raise _error(f"{kind} does not support selected-entity arguments in this implementation.")
        root = _native_root(ctx)
        if kind == "mass":
            if not ctx.material:
                raise _error("Mass measurement requires a declared material and a sourced density.")
            raise _error(f"Sourced material-density integration is not implemented for {ctx.material!r}; no mass was computed.")
        if kind == "thickness":
            raise _error("Wall thickness requires a defined material path or wall pair; this operation is not implemented. Use distance between specified faces for their local separation.")

        entities: list[Entity] = []
        selected = []
        for selector in (a, b):
            if selector is not None:
                entity, topology = _resolve(ctx, selector)
                entities.append(entity)
                selected.append(topology)
        result: dict[str, Any] = {
            "kind": kind, "scope": "selected_entities" if selected else "whole_shape",
            "entities": [asdict(entity) for entity in entities],
        }
        if kind in {"distance", "clearance"}:
            from OCP.BRepExtrema import BRepExtrema_DistShapeShape

            query = BRepExtrema_DistShapeShape()
            query.LoadS1(selected[0].wrapped)
            query.LoadS2(selected[1].wrapped)
            query.Perform()
            if not query.IsDone() or query.NbSolution() < 1:
                raise _error("The native distance query returned no completed solution.", code="KERNEL_OTHER", phase="kernel", selector=a)
            result.update(value=_number(query.Value(), nonnegative=True), unit=ctx.units,
                          definition="minimum_distance_between_selected_entities" if kind == "distance" else "minimum_gap_between_selected_entities",
                          closest_points={"a": _point(query.PointOnShape1(1)), "b": _point(query.PointOnShape2(1))},
                          witness="one_native_minimum")
        elif kind == "angle":
            value, definition = _angle(entities, selected, a)
            result.update(value=value, unit="deg", definition=definition)
        elif kind == "length":
            if entities[0].kind != "edge":
                raise _error("length requires an edge, not a face or vertex.", selector=a)
            result.update(value=_number(selected[0].length, nonnegative=True), unit=ctx.units, definition="edge_arc_length")
        elif kind == "bbox":
            box = (selected[0] if selected else root).bounding_box()
            result.update(
                value=[_number(box.size.X), _number(box.size.Y), _number(box.size.Z)],
                min=_point_xyz(box.min), max=_point_xyz(box.max), unit=ctx.units, definition="axis_aligned_bounds",
            )
        elif kind == "radius":
            result.update(value=_radius(entities[0], selected[0], a), unit=ctx.units, definition="constant_analytic_radius")
        elif kind == "area":
            from OCP.BRepGProp import BRepGProp
            from OCP.GProp import GProp_GProps

            if entities and entities[0].kind != "face":
                raise _error("Selected area requires a face, not an edge or vertex.", selector=a)
            target = selected[0] if selected else root
            _valid(target)
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(target.wrapped, props, False, False)
            result.update(value=_number(props.Mass(), nonnegative=True), unit=f"{ctx.units}^2",
                          definition="trimmed_face_area" if selected else "sum_of_native_surface_areas")
        else:
            props, count = _body_properties(root)
            result.update(aggregation="sum_of_native_solids", solid_count=count)
            if kind == "volume":
                result.update(value=_number(props.Mass(), positive=True), unit=f"{ctx.units}^3", definition="sum_of_solid_volumes")
            elif kind == "center_of_mass":
                result.update(value=_point(props.CentreOfMass()), unit=ctx.units, definition="uniform_density_centroid_of_solids")
            elif kind == "principal_inertia":
                principal = props.PrincipalProperties()
                moments = principal.Moments()
                axes = [principal.FirstAxisOfInertia(), principal.SecondAxisOfInertia(), principal.ThirdAxisOfInertia()]
                result.update(
                    value=[_number(moments[0]), _number(moments[1]), _number(moments[2])],
                    axes=[[_number(v.X()), _number(v.Y()), _number(v.Z())] for v in axes],
                    unit=f"{ctx.units}^5", definition="principal_volume_moments_about_centroid",
                    reference_point=_point(props.CentreOfMass()),
                )
            else:
                matrix = props.MatrixOfInertia()
                result.update(value=[[_number(matrix.Value(i, j)) for j in range(1, 4)] for i in range(1, 4)],
                              unit=f"{ctx.units}^5", definition="central_volume_inertia",
                              reference_point=_point(props.CentreOfMass()), axes="world_xyz")
        # The platform's serializer replaces non-finite floats with null; fail
        # here instead of silently returning an unusable measurement.
        json.dumps(result, allow_nan=False)
        return result
    except MeasurementError:
        raise
    except Exception as exc:
        raise _error(f"Native {kind} measurement could not be completed: {type(exc).__name__}: {exc}",
                     code="KERNEL_OTHER", phase="kernel", selector=a, kernel_message=str(exc)) from exc


def _native_root(ctx: ModelContext) -> Any:
    shape = getattr(ctx, "shape", None)
    wrapped = getattr(shape, "wrapped", None)
    if wrapped is None or wrapped.IsNull():
        raise _error("Measurement requires live native 3D geometry in a kernel worker.")
    import build123d as bd

    # Fresh wrappers avoid overridden Python volume/bbox/solids methods on the
    # supplied wrapper. This is not isolation from model interpreter mutation;
    # the caller must load the revision in a separate trusted worker.
    return bd.Shape.cast(wrapped)


def _resolve(ctx: ModelContext, selector: str) -> tuple[Entity, Any]:
    try:
        from .select import resolve, entity_shape
    except ModuleNotFoundError as exc:
        if exc.name != "aad.engine.select":
            raise
        raise _error("Semantic measurement requires aad.engine.select; the selector package is not installed.", selector=selector) from exc
    try:
        entity, = resolve(ctx, selector, expect=1)
        return entity, entity_shape(ctx, entity)
    except Exception as exc:
        failure = getattr(exc, "failure", None)
        if isinstance(failure, Failure):
            raise MeasurementError(failure) from exc
        raise


def _angle(entities: list[Entity], shapes: list[Any], selector: str) -> tuple[float, str]:
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Plane

    vectors = []
    if all(entity.kind == "face" for entity in entities):
        for shape in shapes:
            surface = BRepAdaptor_Surface(shape.wrapped)
            if surface.GetType() != GeomAbs_Plane:
                raise _error(f"Face angle requires planes; native type is {surface.GetType()}.", selector=selector)
            vectors.append(surface.Plane().Axis().Direction())
        definition = "unoriented_planes"
    elif all(entity.kind == "edge" for entity in entities):
        for shape in shapes:
            curve = BRepAdaptor_Curve(shape.wrapped)
            if curve.GetType() != GeomAbs_Line:
                raise _error(f"Edge angle requires straight lines; native type is {curve.GetType()}.", selector=selector)
            vectors.append(curve.Line().Direction())
        definition = "unoriented_lines"
    else:
        raise _error("Angle requires two planar faces or two straight edges of the same kind.", selector=selector)
    dot = sum(getattr(vectors[0], axis)() * getattr(vectors[1], axis)() for axis in ("X", "Y", "Z"))
    dot = _number(dot)
    return math.degrees(math.acos(min(1.0, max(0.0, abs(dot))))), definition


def _radius(entity: Entity, shape: Any, selector: str) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Sphere

    if entity.kind == "edge":
        curve = BRepAdaptor_Curve(shape.wrapped)
        if curve.GetType() == GeomAbs_Circle:
            return _number(curve.Circle().Radius(), positive=True)
        actual = str(curve.GetType())
    elif entity.kind == "face":
        surface = BRepAdaptor_Surface(shape.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            return _number(surface.Cylinder().Radius(), positive=True)
        if surface.GetType() == GeomAbs_Sphere:
            return _number(surface.Sphere().Radius(), positive=True)
        actual = str(surface.GetType())
    else:
        actual = entity.kind
    raise _error(f"One constant radius requires a circular edge, cylindrical face or spherical face; selected native type is {actual}.", selector=selector)


def _valid(shape: Any) -> None:
    from OCP.BRepCheck import BRepCheck_Analyzer

    if not BRepCheck_Analyzer(shape.wrapped).IsValid():
        raise _error("The measured native geometry did not pass BREP validity.", code="INVALID_SOLID", phase="validate")


def _body_properties(root: Any) -> tuple[Any, int]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    _valid(root)
    solids = list(root.solids())
    if not solids:
        raise _error("Volume and inertia require at least one native solid.", code="INVALID_SOLID", phase="validate")
    aggregate = GProp_GProps()
    for index, solid in enumerate(solids):
        _valid(solid)
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid.wrapped, props, True, False, False)
        volume = float(props.Mass())
        if not math.isfinite(volume) or volume <= 0:
            raise _error(f"Native body index {index} has nonpositive or non-finite signed volume {volume!r}; no aggregate was computed.",
                         code="INVALID_SOLID", phase="validate")
        aggregate.Add(props)
    _number(aggregate.Mass(), positive=True)
    return aggregate, len(solids)


def _point_xyz(value: Any) -> list[float]:
    return [_number(value.X), _number(value.Y), _number(value.Z)]


def _point(value: Any) -> list[float]:
    return [_number(getattr(value, axis)()) for axis in ("X", "Y", "Z")]


def _number(value: Any, *, nonnegative: bool = False, positive: bool = False) -> float:
    number = float(value)
    if not math.isfinite(number) or (nonnegative and number < 0) or (positive and number <= 0):
        raise ValueError(f"Native measurement is outside its finite {'positive' if positive else 'nonnegative' if nonnegative else 'numeric'} domain: {number!r}")
    return number


def _error(message: str, *, code: str = "BAD_INPUT", phase: str = "input", selector: str | None = None,
           kernel_message: str | None = None) -> MeasurementError:
    return MeasurementError(Failure(code=code, phase=phase, message=message, selector=selector,
                                    kernel_message=kernel_message, feature="unknown"))
