"""Named, independent 2D sketch constraints solved with SciPy least squares.

The module has no build123d or ``model2d`` dependency. NumPy and SciPy are
loaded only when :meth:`ConstraintSketch.solve` is called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, degrees, hypot, isfinite, radians, sin, ulp
from numbers import Real
from typing import Any

__all__ = [
    "Circle",
    "Constraint",
    "ConstraintError",
    "ConstraintResult",
    "ConstraintSketch",
    "Line",
    "Point",
    "SolveReport",
]


class ConstraintError(ValueError):
    """A named sketch constraint has invalid or unsupported input."""


@dataclass
class Point:
    """A mutable named 2D point, in the sketch's native linear units."""

    name: str
    x: float
    y: float


@dataclass(frozen=True)
class Line:
    """A named segment joining two entries in ``ConstraintSketch.points``."""

    name: str
    a: str
    b: str


@dataclass
class Circle:
    """A mutable named circle whose center is a named sketch point."""

    name: str
    center: str
    radius: float


@dataclass
class Constraint:
    """A named constraint specification.

    ``value`` can be edited between solves for distance and angle parameters.
    ``snapshot`` and ``branch`` are solver-owned data used by fixed and
    tangency constraints.
    """

    name: str
    kind: str
    entities: tuple[str, ...]
    value: float | None = None
    options: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict, repr=False)
    branch: int | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ConstraintResult:
    """Residual and outcome for one named constraint."""

    residuals: tuple[float, ...]
    status: str

    @property
    def residual(self) -> float:
        """Euclidean norm of this constraint's residual components."""

        return hypot(*self.residuals) if self.residuals else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "residual": float(self.residual),
            "residuals": [float(value) for value in self.residuals],
            "status": self.status,
        }


@dataclass
class SolveReport:
    """JSON-safe result from :meth:`ConstraintSketch.solve`."""

    ok: bool
    status: str
    dof: int
    rank: int
    points: dict[str, tuple[float, float]]
    radii: dict[str, float]
    constraints: dict[str, ConstraintResult]
    conflicting_constraints: list[str] = field(default_factory=list)
    redundant_constraints: list[str] = field(default_factory=list)
    free_variables: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary containing only JSON-native values."""

        return {
            "ok": bool(self.ok),
            "status": self.status,
            "dof": int(self.dof),
            "rank": int(self.rank),
            "points": {
                name: [float(x), float(y)] for name, (x, y) in self.points.items()
            },
            "radii": {name: float(radius) for name, radius in self.radii.items()},
            "constraints": {
                name: result.to_dict() for name, result in self.constraints.items()
            },
            "conflicting_constraints": list(self.conflicting_constraints),
            "redundant_constraints": list(self.redundant_constraints),
            "free_variables": list(self.free_variables),
            "message": self.message,
        }


class _VariableLayout:
    """Represent all point coordinates and radii as scaled solver variables."""

    def __init__(self, sketch: ConstraintSketch, scale: float, np: Any) -> None:
        self.scale = scale
        self.base_points = {
            name: (float(point.x), float(point.y))
            for name, point in sketch.points.items()
        }
        self.base_radii = {
            name: float(circle.radius) for name, circle in sketch.circles.items()
        }
        self.point_slots: dict[str, tuple[int, int]] = {}
        self.radius_slots: dict[str, int] = {}
        self.labels: list[str] = []
        initial: list[float] = []
        lower: list[float] = []
        upper: list[float] = []
        self.radius_floors: dict[str, float] = {}

        for name in sketch.points:
            x_slot = len(initial)
            self.point_slots[name] = (x_slot, x_slot + 1)
            self.labels.extend((f"{name}.x", f"{name}.y"))
            initial.extend((0.0, 0.0))
            lower.extend((-np.inf, -np.inf))
            upper.extend((np.inf, np.inf))

        for name, circle in sketch.circles.items():
            slot = len(initial)
            self.radius_slots[name] = slot
            self.labels.append(f"{name}.radius")
            initial.append(0.0)
            floor = max(float(circle.radius) * 1e-12, ulp(float(circle.radius)) * 8.0)
            if floor >= circle.radius:
                raise ConstraintError(
                    f"circle {name!r} radius is too close to floating-point zero "
                    "for a stable solve"
                )
            self.radius_floors[name] = floor
            lower.append((floor - float(circle.radius)) / scale)
            upper.append(np.inf)

        self.initial = np.asarray(initial, dtype=float)
        self.lower = np.asarray(lower, dtype=float)
        self.upper = np.asarray(upper, dtype=float)
        self.variable_count = len(initial)

    def point(self, vector: Any, name: str) -> tuple[float, float]:
        x_slot, y_slot = self.point_slots[name]
        base_x, base_y = self.base_points[name]
        return (
            base_x + float(vector[x_slot]) * self.scale,
            base_y + float(vector[y_slot]) * self.scale,
        )

    def radius(self, vector: Any, name: str) -> float:
        return self.base_radii[name] + float(vector[self.radius_slots[name]]) * self.scale

    def points(self, vector: Any) -> dict[str, tuple[float, float]]:
        return {name: self.point(vector, name) for name in self.point_slots}

    def radii(self, vector: Any) -> dict[str, float]:
        return {name: self.radius(vector, name) for name in self.radius_slots}


class ConstraintSketch:
    """Mutable sketch registries plus a deterministic named constraint solver.

    Entity and constraint names share one namespace. Registries are insertion
    ordered dictionaries and their objects are readable and mutable. Constraint
    coordinates and radii are the only solver variables.
    """

    def __init__(self) -> None:
        self.points: dict[str, Point] = {}
        self.lines: dict[str, Line] = {}
        self.circles: dict[str, Circle] = {}
        self.constraints: dict[str, Constraint] = {}

    def add_point(self, name: str, x: float, y: float) -> Point:
        """Add and return a point with finite native-unit coordinates."""

        self._check_new_name(name, "point")
        point = Point(
            name,
            _finite_number(x, f"point {name!r} x"),
            _finite_number(y, f"point {name!r} y"),
        )
        self.points[name] = point
        return point

    def add_line(self, name: str, a: str, b: str) -> Line:
        """Add and return a segment referencing two existing point names."""

        self._check_new_name(name, "line")
        if not isinstance(a, str) or a not in self.points:
            raise ValueError(f"line {name!r} references unknown point {a!r}")
        if not isinstance(b, str) or b not in self.points:
            raise ValueError(f"line {name!r} references unknown point {b!r}")
        if a == b:
            raise ValueError(f"line {name!r} is degenerate: both endpoints are {a!r}")
        pa, pb = self.points[a], self.points[b]
        if hypot(pb.x - pa.x, pb.y - pa.y) == 0.0:
            raise ValueError(f"line {name!r} is degenerate: its endpoints coincide")
        line = Line(name, a, b)
        self.lines[name] = line
        return line

    def add_circle(self, name: str, center: str, radius: float) -> Circle:
        """Add and return a circle referencing an existing center point."""

        self._check_new_name(name, "circle")
        if not isinstance(center, str) or center not in self.points:
            raise ValueError(f"circle {name!r} references unknown point {center!r}")
        circle_radius = _finite_number(radius, f"circle {name!r} radius")
        if circle_radius <= 0.0:
            raise ValueError(f"circle {name!r} radius must be positive")
        circle = Circle(name, center, circle_radius)
        self.circles[name] = circle
        return circle

    def add_constraint(
        self,
        name: str,
        kind: str,
        entities: tuple[str, ...] | list[str],
        value: float | None = None,
        **options: Any,
    ) -> Constraint:
        """Add a validated named constraint and return its editable record.

        The supported entity combinations are listed in ``docs/constraints.md``.
        """

        self._check_new_name(name, "constraint")
        if not isinstance(kind, str) or not kind.strip():
            raise ConstraintError(f"constraint {name!r}: kind must be a non-empty string")
        if not isinstance(entities, (tuple, list)):
            raise ConstraintError(
                f"constraint {name!r}: entities must be an ordered list or tuple of names"
            )
        names = tuple(entities)

        constraint = Constraint(
            name=name,
            kind=kind.strip().lower(),
            entities=names,
            value=value,
            options=dict(options),
        )
        self._validate_constraint(constraint)
        if constraint.kind == "fixed":
            constraint.snapshot = self._snapshot(constraint.entities[0])
        if constraint.kind == "tangent":
            self._set_tangent_branch(constraint)
        self.constraints[name] = constraint
        return constraint

    def set_constraint_value(self, name: str, value: float) -> Constraint:
        """Set a distance or angle target, validating it before it is kept."""

        if name not in self.constraints:
            raise ConstraintError(f"unknown constraint {name!r}")
        constraint = self.constraints[name]
        old_value = constraint.value
        constraint.value = value  # type: ignore[assignment]
        try:
            self._validate_constraint(constraint)
        except Exception:
            constraint.value = old_value
            raise
        return constraint

    def solve(self, tolerance: float = 1e-6, inplace: bool = True) -> SolveReport:
        """Solve named constraints with ``scipy.optimize.least_squares``.

        Constraints are considered in insertion order. A consistent constraint
        is retained; an incompatible later one is named as conflicting and is
        excluded from the proposed geometry. Conflicting or failed solves never
        update the live sketch, even when ``inplace`` is true. A successful but
        underconstrained solution is applied when ``inplace`` is true.

        ``tolerance`` is an absolute residual tolerance: native linear units for
        distance/coincidence/fixed/tangency and degrees for angular constraints.
        """

        solve_tolerance = _finite_number(tolerance, "solve tolerance")
        if solve_tolerance <= 0.0:
            raise ValueError("solve tolerance must be positive")
        if not isinstance(inplace, bool):
            raise ValueError("inplace must be a bool")

        self._validate_sketch()
        scale = self._geometry_scale()

        try:
            import numpy as np
            from scipy.optimize import least_squares
        except ImportError as exc:  # pragma: no cover - packaging failure path
            raise RuntimeError(
                "ConstraintSketch.solve requires NumPy and SciPy; install the "
                "aiad-drafting dependencies"
            ) from exc

        layout = _VariableLayout(self, scale, np)
        accepted: list[Constraint] = []
        redundant: list[str] = []
        conflicting: list[str] = []
        conflict_reasons: list[str] = []
        current = layout.initial.copy()
        current_jac = np.zeros((0, layout.variable_count), dtype=float)

        for constraint in self.constraints.values():
            trial_constraints = [*accepted, constraint]
            if layout.variable_count == 0:
                trial = None
                trial_vector = current
                trial_jac = np.zeros((0, 0), dtype=float)
                solver_success = True
                solver_message = ""
            else:
                try:
                    trial = self._fit(
                        least_squares,
                        np,
                        layout,
                        trial_constraints,
                        current,
                        scale,
                    )
                except Exception as exc:
                    return self._failure_report(
                        np,
                        layout,
                        current,
                        accepted,
                        redundant,
                        conflicting,
                        current_jac,
                        f"solver failed while adding constraint {constraint.name!r}: {exc}",
                    )
                trial_vector = trial.x
                trial_jac = np.asarray(trial.jac, dtype=float)
                solver_success = bool(trial.success)
                solver_message = str(trial.message)

            if not solver_success:
                return self._failure_report(
                    np,
                    layout,
                    current,
                    accepted,
                    redundant,
                    conflicting,
                    current_jac,
                    f"solver did not converge while adding constraint "
                    f"{constraint.name!r}: {solver_message}",
                )

            self._preserve_unused_line_endpoints(layout, trial_vector, trial_constraints)
            invalid_reason = self._invalid_solution_reason(
                layout, trial_vector, scale, trial_constraints
            )
            if invalid_reason is not None:
                return self._failure_report(
                    np, layout, current, accepted, redundant, conflicting, current_jac,
                    f"solver could not find a nondegenerate trial while adding "
                    f"constraint {constraint.name!r}: {invalid_reason}",
                )

            trial_residuals = {
                item.name: self._constraint_residuals(item, layout, trial_vector)
                for item in trial_constraints
            }
            feasible = all(
                _norm(residuals) <= solve_tolerance
                for residuals in trial_residuals.values()
            )
            if feasible:
                rank_before, _ = _rank_and_free(
                    current_jac, layout.variable_count, layout.labels, np
                )
                rank_after, _ = _rank_and_free(
                    trial_jac, layout.variable_count, layout.labels, np
                )
                if rank_after == rank_before:
                    redundant.append(constraint.name)
                accepted.append(constraint)
                current = trial_vector.copy()
                current_jac = trial_jac
            else:
                conflicting.append(constraint.name)
                conflict_reasons.append(
                    f"{constraint.name}: cannot satisfy it together with the "
                    "earlier accepted constraints"
                )

        rank, free_variables = _rank_and_free(
            current_jac, layout.variable_count, layout.labels, np
        )
        dof = layout.variable_count - rank
        constraint_results = self._constraint_results(
            layout,
            current,
            accepted,
            redundant,
            conflicting,
            self.constraints.values(),
        )
        if conflicting:
            status = "conflicting"
            message = "; ".join(conflict_reasons)
            ok = False
        elif dof:
            status = "underconstrained"
            message = f"Solved with {dof} remaining degree(s) of freedom."
            ok = True
        else:
            status = "solved"
            message = "All constraints are satisfied and the sketch is fully constrained."
            ok = True

        report = SolveReport(
            ok=ok,
            status=status,
            dof=dof,
            rank=rank,
            points=layout.points(current),
            radii=layout.radii(current),
            constraints=constraint_results,
            conflicting_constraints=conflicting,
            redundant_constraints=redundant,
            free_variables=free_variables,
            message=message,
        )
        if inplace and report.ok:
            for name, (x, y) in report.points.items():
                self.points[name].x = x
                self.points[name].y = y
            for name, radius in report.radii.items():
                self.circles[name].radius = radius
        return report

    def _check_new_name(self, name: str, kind: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{kind} name must be a non-empty string")
        if name in self.points or name in self.lines or name in self.circles or name in self.constraints:
            raise ValueError(f"name {name!r} is already used by another sketch object")

    def _entity_kind(self, name: str) -> str | None:
        if name in self.points:
            return "point"
        if name in self.lines:
            return "line"
        if name in self.circles:
            return "circle"
        return None

    def _validate_constraint(self, constraint: Constraint) -> None:
        name = constraint.name

        def fail(message: str) -> None:
            raise ConstraintError(f"constraint {name!r}: {message}")

        if not isinstance(constraint.kind, str):
            fail("kind must be a string")
        constraint.kind = constraint.kind.strip().lower()
        supported = {
            "coincident",
            "distance",
            "angle",
            "parallel",
            "perpendicular",
            "tangent",
            "equal",
            "symmetric",
            "fixed",
        }
        if constraint.kind not in supported:
            fail(f"unsupported kind {constraint.kind!r}")
        if not isinstance(constraint.entities, (tuple, list)):
            fail("entities must be a sequence of entity names")
        entities = tuple(constraint.entities)
        constraint.entities = entities
        if not all(isinstance(entity, str) for entity in entities):
            fail("every entity reference must be a string name")
        kinds: list[str] = []
        for entity in entities:
            entity_kind = self._entity_kind(entity)
            if entity_kind is None:
                fail(f"unknown entity {entity!r}")
            kinds.append(entity_kind)

        kind = constraint.kind
        allowed_options = {"mode"} if kind == "tangent" else set()
        if not isinstance(constraint.options, dict):
            fail("options must be a mapping")
        unknown_options = set(constraint.options) - allowed_options
        if unknown_options:
            fail(f"unsupported option(s): {', '.join(sorted(unknown_options))}")

        needs_value = kind in {"distance", "angle"}
        if needs_value:
            if constraint.value is None:
                fail("value is required")
            constraint.value = _finite_number(constraint.value, f"constraint {name!r} value")
        elif constraint.value is not None:
            fail("value is not used by this kind")

        if kind == "coincident":
            self._require_signature(constraint, kinds, ("point", "point"), fail)
            self._require_distinct(constraint, fail)
        elif kind == "distance":
            if kinds == ["point", "point"]:
                self._require_distinct(constraint, fail)
                if constraint.value is not None and constraint.value <= 0.0:
                    fail("point-to-point distance must be positive; use coincident for zero separation")
                first = self.points[entities[0]]
                second = self.points[entities[1]]
                if hypot(second.x - first.x, second.y - first.y) == 0.0:
                    fail("the seed points coincide; separate them before constraining a positive distance")
            elif kinds == ["line"]:
                if constraint.value is not None and constraint.value <= 0.0:
                    fail("line length must be positive")
                self._require_usable_lines((entities[0],), fail)
            else:
                fail("supported entities are two distinct points or one line")
        elif kind == "angle":
            self._require_signature(constraint, kinds, ("line", "line"), fail)
            self._require_distinct(constraint, fail)
            self._require_usable_lines(entities, fail)
            if constraint.value is not None and not 0.0 <= constraint.value <= 360.0:
                fail("angle value must be between 0 and 360 degrees")
        elif kind in {"parallel", "perpendicular"}:
            self._require_signature(constraint, kinds, ("line", "line"), fail)
            self._require_distinct(constraint, fail)
            self._require_usable_lines(entities, fail)
        elif kind == "tangent":
            circle_line = len(kinds) == 2 and set(kinds) == {"circle", "line"}
            circle_circle = kinds == ["circle", "circle"]
            if not circle_line and not circle_circle:
                fail("supported entities are one circle and one line, or two distinct circles")
            self._require_distinct(constraint, fail)
            if circle_line:
                if "mode" in constraint.options:
                    fail("mode applies only to circle-to-circle tangency")
                line_name = entities[kinds.index("line")]
                self._require_usable_lines((line_name,), fail)
                circle = self.circles[entities[kinds.index("circle")]]
                segment = self.lines[line_name]
                if circle.center in {segment.a, segment.b}:
                    fail("a circle centered on a line endpoint cannot be tangent to that line")
            else:
                mode = constraint.options.get("mode", "external")
                if not isinstance(mode, str) or mode not in {"external", "internal"}:
                    fail("tangent mode must be 'external' or 'internal'")
                center_a = self.points[self.circles[entities[0]].center]
                center_b = self.points[self.circles[entities[1]].center]
                if hypot(center_b.x - center_a.x, center_b.y - center_a.y) == 0.0:
                    fail("seed circle centers coincide; offset them to define a tangency direction")
        elif kind == "equal":
            if kinds not in (["line", "line"], ["circle", "circle"]):
                fail("supported entities are two distinct lines or two distinct circles")
            self._require_distinct(constraint, fail)
            if kinds == ["line", "line"]:
                self._require_usable_lines(entities, fail)
        elif kind == "symmetric":
            if len(kinds) != 3 or kinds[0:2] != ["point", "point"] or kinds[2] not in {"line", "point"}:
                fail("supported entities are two points and an axis line, or two points and a center point")
            if len(set(entities)) != len(entities):
                fail("the two points and axis/center must be distinct named entities")
            if kinds[2] == "line":
                self._require_usable_lines((entities[2],), fail)
        elif kind == "fixed":
            if len(kinds) != 1:
                fail("fixed accepts exactly one point, line, or circle")
            if kinds[0] == "line":
                self._require_usable_lines(entities, fail)
            if not constraint.snapshot:
                # A user-edited constraint record may not discard its original anchor.
                if name in self.constraints:
                    fail("fixed snapshot is missing")

    @staticmethod
    def _require_signature(
        constraint: Constraint,
        kinds: list[str],
        expected: tuple[str, ...],
        fail: Any,
    ) -> None:
        if tuple(kinds) != expected:
            fail(f"supported entities are {', '.join(expected)}")

    @staticmethod
    def _require_distinct(constraint: Constraint, fail: Any) -> None:
        if len(set(constraint.entities)) != len(constraint.entities):
            fail("entity references must be distinct")

    def _require_usable_lines(self, names: tuple[str, ...], fail: Any) -> None:
        for line_name in names:
            line = self.lines[line_name]
            a, b = self.points[line.a], self.points[line.b]
            if hypot(b.x - a.x, b.y - a.y) == 0.0:
                fail(f"line {line_name!r} is degenerate because its endpoints coincide")

    def _snapshot(self, entity_name: str) -> dict[str, Any]:
        entity_kind = self._entity_kind(entity_name)
        if entity_kind == "point":
            point = self.points[entity_name]
            return {"point": (float(point.x), float(point.y))}
        if entity_kind == "line":
            line = self.lines[entity_name]
            a, b = self.points[line.a], self.points[line.b]
            return {
                "a": (float(a.x), float(a.y)),
                "b": (float(b.x), float(b.y)),
            }
        if entity_kind == "circle":
            circle = self.circles[entity_name]
            center = self.points[circle.center]
            return {
                "center": (float(center.x), float(center.y)),
                "radius": float(circle.radius),
            }
        raise ConstraintError(f"fixed entity {entity_name!r} is unknown")

    def _set_tangent_branch(self, constraint: Constraint) -> None:
        kinds = [self._entity_kind(name) for name in constraint.entities]
        if len(kinds) == 2 and set(kinds) == {"circle", "line"}:
            circle_name = constraint.entities[kinds.index("circle")]
            line_name = constraint.entities[kinds.index("line")]
            center = self.points[self.circles[circle_name].center]
            signed = _signed_line_distance(self, line_name, (center.x, center.y))
            constraint.branch = 1 if signed >= 0.0 else -1
        elif kinds == ["circle", "circle"] and constraint.options.get("mode", "external") == "internal":
            first, second = (self.circles[name] for name in constraint.entities)
            constraint.branch = 1 if first.radius >= second.radius else -1

    def _validate_sketch(self) -> None:
        for name, point in self.points.items():
            if not isfinite(float(point.x)) or not isfinite(float(point.y)):
                referenced = self._constraints_referencing_point(name)
                if referenced:
                    raise ConstraintError(
                        f"constraint(s) {', '.join(repr(item) for item in referenced)}: "
                        f"point {name!r} has non-finite coordinates"
                    )
                raise ValueError(f"point {name!r} has non-finite coordinates")
        for name, line in self.lines.items():
            if line.a not in self.points or line.b not in self.points or line.a == line.b:
                raise ValueError(f"line {name!r} has invalid point references")
        for name, circle in self.circles.items():
            if circle.center not in self.points:
                raise ValueError(f"circle {name!r} references unknown point {circle.center!r}")
            if not isfinite(float(circle.radius)) or circle.radius <= 0.0:
                referenced = [c.name for c in self.constraints.values() if name in c.entities]
                owner = ", ".join(repr(item) for item in referenced) or f"circle {name!r}"
                raise ConstraintError(f"{owner}: circle radius must be finite and positive")

        for constraint in self.constraints.values():
            self._validate_constraint(constraint)
            if constraint.kind == "fixed" and not constraint.snapshot:
                raise ConstraintError(f"constraint {constraint.name!r}: fixed snapshot is missing")
            if constraint.kind == "tangent" and constraint.branch is None:
                self._set_tangent_branch(constraint)

        for line in self.lines.values():
            a, b = self.points[line.a], self.points[line.b]
            length = hypot(b.x - a.x, b.y - a.y)
            epsilon = _line_epsilon(a.x, a.y, b.x, b.y, length)
            if length <= epsilon:
                referenced = [c.name for c in self.constraints.values() if line.name in c.entities]
                owner = (
                    "constraint(s) " + ", ".join(repr(item) for item in referenced)
                    if referenced
                    else f"line {line.name!r}"
                )
                raise ConstraintError(
                    f"{owner}: line {line.name!r} is degenerate at the current seed coordinates"
                )

    def _constraints_referencing_point(self, point_name: str) -> list[str]:
        related = {point_name}
        related.update(
            line.name
            for line in self.lines.values()
            if point_name in {line.a, line.b}
        )
        related.update(
            circle.name
            for circle in self.circles.values()
            if circle.center == point_name
        )
        return [
            constraint.name
            for constraint in self.constraints.values()
            if any(entity in related for entity in constraint.entities)
        ]

    def _geometry_scale(self) -> float:
        values: list[float] = []
        if self.points:
            xs = [float(point.x) for point in self.points.values()]
            ys = [float(point.y) for point in self.points.values()]
            values.extend((max(xs) - min(xs), max(ys) - min(ys)))
        for line in self.lines.values():
            a, b = self.points[line.a], self.points[line.b]
            values.append(hypot(b.x - a.x, b.y - a.y))
        values.extend(float(circle.radius) for circle in self.circles.values())
        values.extend(
            float(constraint.value)
            for constraint in self.constraints.values()
            if constraint.kind == "distance" and constraint.value is not None
        )
        scale = max(values, default=1.0)
        return scale if scale > 0.0 else 1.0

    def _fit(
        self,
        least_squares: Any,
        np: Any,
        layout: _VariableLayout,
        constraints: list[Constraint],
        start: Any,
        scale: float,
    ) -> Any:
        angular_kinds = {"angle", "parallel", "perpendicular"}

        def objective(vector: Any) -> Any:
            components: list[float] = []
            for constraint in constraints:
                divisor = 180.0 if constraint.kind in angular_kinds else scale
                components.extend(
                    value / divisor
                    for value in self._constraint_residuals(constraint, layout, vector)
                )
            result = np.asarray(components, dtype=float)
            if not np.all(np.isfinite(result)):
                raise FloatingPointError("constraint residual became non-finite")
            return result

        return least_squares(
            objective,
            start,
            jac="3-point",
            bounds=(layout.lower, layout.upper),
            method="trf",
            # Coordinates/radii already share the geometry scale in layout.
            # Jacobian scaling can give an unconstrained coordinate an enormous
            # step and flatten a normal polygon before later constraints run.
            x_scale=1.0,
            ftol=1e-12,
            xtol=1e-12,
            gtol=1e-12,
        )

    def _constraint_residuals(
        self, constraint: Constraint, layout: _VariableLayout, vector: Any
    ) -> tuple[float, ...]:
        kind = constraint.kind
        entities = constraint.entities
        point = lambda name: layout.point(vector, name)
        line = lambda name: _line_points(self, point, name)
        circle_radius = lambda name: layout.radius(vector, name)

        if kind == "coincident":
            ax, ay = point(entities[0])
            bx, by = point(entities[1])
            return bx - ax, by - ay

        if kind == "distance":
            if self._entity_kind(entities[0]) == "line":
                a, b = line(entities[0])
                return (hypot(b[0] - a[0], b[1] - a[1]) - float(constraint.value),)
            a, b = point(entities[0]), point(entities[1])
            return (hypot(b[0] - a[0], b[1] - a[1]) - float(constraint.value),)

        if kind == "angle":
            first_a, first_b = line(entities[0])
            second_a, second_b = line(entities[1])
            first_angle = degrees(atan2(first_b[1] - first_a[1], first_b[0] - first_a[0]))
            second_angle = degrees(atan2(second_b[1] - second_a[1], second_b[0] - second_a[0]))
            delta = (second_angle - first_angle) % 360.0
            residual = (delta - float(constraint.value) + 180.0) % 360.0 - 180.0
            return (residual,)

        if kind in {"parallel", "perpendicular"}:
            a0, a1 = line(entities[0])
            b0, b1 = line(entities[1])
            ax, ay = a1[0] - a0[0], a1[1] - a0[1]
            bx, by = b1[0] - b0[0], b1[1] - b0[1]
            delta = atan2(ax * by - ay * bx, ax * bx + ay * by)
            if kind == "parallel":
                error = 0.5 * atan2(sin(2.0 * delta), cos(2.0 * delta))
            else:
                error = 0.5 * atan2(
                    sin(2.0 * (delta - radians(90.0))),
                    cos(2.0 * (delta - radians(90.0))),
                )
            return (degrees(error),)

        if kind == "tangent":
            kinds = [self._entity_kind(name) for name in entities]
            if set(kinds) == {"circle", "line"}:
                circle_name = entities[kinds.index("circle")]
                line_name = entities[kinds.index("line")]
                center_name = self.circles[circle_name].center
                center = point(center_name)
                line_a, line_b = line(line_name)
                signed = _signed_distance_to_points(line_a, line_b, center)
                branch = constraint.branch or 1
                return (branch * signed - circle_radius(circle_name),)

            circle_a, circle_b = entities
            center_a = point(self.circles[circle_a].center)
            center_b = point(self.circles[circle_b].center)
            center_distance = hypot(center_b[0] - center_a[0], center_b[1] - center_a[1])
            radius_a, radius_b = circle_radius(circle_a), circle_radius(circle_b)
            if constraint.options.get("mode", "external") == "internal":
                branch = constraint.branch or 1
                target = branch * (radius_a - radius_b)
            else:
                target = radius_a + radius_b
            return (center_distance - target,)

        if kind == "equal":
            if self._entity_kind(entities[0]) == "line":
                a0, a1 = line(entities[0])
                b0, b1 = line(entities[1])
                return (
                    hypot(a1[0] - a0[0], a1[1] - a0[1])
                    - hypot(b1[0] - b0[0], b1[1] - b0[1]),
                )
            return (circle_radius(entities[0]) - circle_radius(entities[1]),)

        if kind == "symmetric":
            first_name, second_name, axis_name = entities
            first, second = point(first_name), point(second_name)
            if self._entity_kind(axis_name) == "point":
                center = point(axis_name)
                return (
                    (first[0] + second[0]) / 2.0 - center[0],
                    (first[1] + second[1]) / 2.0 - center[1],
                )
            axis_a, axis_b = line(axis_name)
            dx, dy = axis_b[0] - axis_a[0], axis_b[1] - axis_a[1]
            length = hypot(dx, dy)
            midpoint = ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
            pair = (second[0] - first[0], second[1] - first[1])
            return (
                _signed_distance_to_points(axis_a, axis_b, midpoint),
                (pair[0] * dx + pair[1] * dy) / length,
            )

        if kind == "fixed":
            entity_name = entities[0]
            entity_kind = self._entity_kind(entity_name)
            snapshot = constraint.snapshot
            if entity_kind == "point":
                current = point(entity_name)
                target = snapshot["point"]
                return current[0] - target[0], current[1] - target[1]
            if entity_kind == "line":
                line_entity = self.lines[entity_name]
                a, b = point(line_entity.a), point(line_entity.b)
                target_a, target_b = snapshot["a"], snapshot["b"]
                return (
                    a[0] - target_a[0],
                    a[1] - target_a[1],
                    b[0] - target_b[0],
                    b[1] - target_b[1],
                )
            circle_entity = self.circles[entity_name]
            center = point(circle_entity.center)
            target_center = snapshot["center"]
            return (
                center[0] - target_center[0],
                center[1] - target_center[1],
                circle_radius(entity_name) - snapshot["radius"],
            )

        raise ConstraintError(f"constraint {constraint.name!r}: unsupported kind {kind!r}")

    def _preserve_unused_line_endpoints(self, layout, vector, constraints):
        """Carry a wholly free endpoint with its moving neighbor.

        An optimizer has no residual for a point absent from every constraint,
        so it can leave that point exactly where another endpoint must move.
        Preserve the seed edge vector using only these unreferenced points;
        named constraint residuals and Jacobian rank remain unchanged.
        """
        used = set()
        for constraint in constraints:
            for name in constraint.entities:
                kind = self._entity_kind(name)
                if kind == "point":
                    used.add(name)
                elif kind == "line":
                    used.update((self.lines[name].a, self.lines[name].b))
                elif kind == "circle":
                    used.add(self.circles[name].center)
        for _ in range(len(self.lines) + 1):
            changed = False
            for line in self.lines.values():
                a, b = layout.point(vector, line.a), layout.point(vector, line.b)
                base_a, base_b = layout.base_points[line.a], layout.base_points[line.b]
                seed_length = hypot(base_b[0] - base_a[0], base_b[1] - base_a[1])
                if hypot(b[0] - a[0], b[1] - a[1]) > _line_epsilon(*base_a, *base_b, seed_length):
                    continue
                for free, anchor in ((line.b, line.a), (line.a, line.b)):
                    if free in used:
                        continue
                    current = layout.point(vector, anchor)
                    base_free, base_anchor = layout.base_points[free], layout.base_points[anchor]
                    slots = layout.point_slots[free]
                    for axis, slot in enumerate(slots):
                        target = current[axis] + base_free[axis] - base_anchor[axis]
                        vector[slot] = (target - base_free[axis]) / layout.scale
                    changed = True
                    break
            if not changed:
                break

    def _invalid_solution_reason(
        self,
        layout: _VariableLayout,
        vector: Any,
        scale: float,
        constraints: list[Constraint],
    ) -> str | None:
        for line_entity in self.lines.values():
            a = layout.point(vector, line_entity.a)
            b = layout.point(vector, line_entity.b)
            length = hypot(b[0] - a[0], b[1] - a[1])
            base_a, base_b = layout.base_points[line_entity.a], layout.base_points[line_entity.b]
            base_length = hypot(base_b[0] - base_a[0], base_b[1] - base_a[1])
            epsilon = _line_epsilon(*base_a, *base_b, base_length)
            if length <= epsilon:
                return f"line {line_entity.name!r} would collapse to zero length"

        for constraint in constraints:
            if constraint.kind != "tangent" or constraint.options.get("mode", "external") != "internal":
                continue
            if [self._entity_kind(name) for name in constraint.entities] != ["circle", "circle"]:
                continue
            first, second = constraint.entities
            center_a = layout.point(vector, self.circles[first].center)
            center_b = layout.point(vector, self.circles[second].center)
            distance = hypot(center_b[0] - center_a[0], center_b[1] - center_a[1])
            radius_difference = abs(layout.radius(vector, first) - layout.radius(vector, second))
            epsilon = max(scale * 1e-12, ulp(scale) * 8.0)
            if distance <= epsilon or radius_difference <= epsilon:
                return (
                    f"internal tangency {constraint.name!r} would make the circles "
                    "coincident rather than tangent at one point"
                )
        return None

    def _constraint_results(
        self,
        layout: _VariableLayout,
        vector: Any,
        accepted: list[Constraint],
        redundant: list[str],
        conflicting: list[str],
        all_constraints: Any,
    ) -> dict[str, ConstraintResult]:
        accepted_names = {item.name for item in accepted}
        redundant_names = set(redundant)
        conflicting_names = set(conflicting)
        results: dict[str, ConstraintResult] = {}
        for constraint in all_constraints:
            if constraint.name in conflicting_names:
                status = "conflicting"
            elif constraint.name in redundant_names:
                status = "redundant"
            elif constraint.name in accepted_names:
                status = "satisfied"
            else:
                status = "not_solved"
            residuals = self._constraint_residuals(constraint, layout, vector)
            results[constraint.name] = ConstraintResult(
                residuals=tuple(float(value) for value in residuals),
                status=status,
            )
        return results

    def _failure_report(
        self,
        np: Any,
        layout: _VariableLayout,
        vector: Any,
        accepted: list[Constraint],
        redundant: list[str],
        conflicting: list[str],
        jacobian: Any,
        message: str,
    ) -> SolveReport:
        rank, free_variables = _rank_and_free(
            jacobian, layout.variable_count, layout.labels, np
        )
        results = self._constraint_results(
            layout,
            vector,
            accepted,
            redundant,
            conflicting,
            self.constraints.values(),
        )
        return SolveReport(
            ok=False,
            status="failed",
            dof=layout.variable_count - rank,
            rank=rank,
            points=layout.points(vector),
            radii=layout.radii(vector),
            constraints=results,
            conflicting_constraints=list(conflicting),
            redundant_constraints=list(redundant),
            free_variables=free_variables,
            message=message,
        )


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a real number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _line_points(
    sketch: ConstraintSketch, point: Any, line_name: str
) -> tuple[tuple[float, float], tuple[float, float]]:
    line = sketch.lines[line_name]
    return point(line.a), point(line.b)


def _signed_distance_to_points(
    start: tuple[float, float],
    end: tuple[float, float],
    point: tuple[float, float],
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = hypot(dx, dy)
    if length == 0.0:
        return 0.0
    return (dx * (point[1] - start[1]) - dy * (point[0] - start[0])) / length


def _signed_line_distance(
    sketch: ConstraintSketch, line_name: str, point: tuple[float, float]
) -> float:
    line = sketch.lines[line_name]
    a, b = sketch.points[line.a], sketch.points[line.b]
    return _signed_distance_to_points((a.x, a.y), (b.x, b.y), point)


def _line_epsilon(ax: float, ay: float, bx: float, by: float, length: float) -> float:
    coordinate_resolution = max(ulp(ax), ulp(ay), ulp(bx), ulp(by))
    return max(length * 1e-12, coordinate_resolution * 8.0)


def _norm(values: tuple[float, ...]) -> float:
    return hypot(*values) if values else 0.0


def _rank_and_free(
    jacobian: Any, variable_count: int, labels: list[str], np: Any
) -> tuple[int, list[str]]:
    if variable_count == 0:
        return 0, []
    matrix = np.asarray(jacobian, dtype=float)
    if matrix.ndim != 2:
        matrix = matrix.reshape((-1, variable_count))
    if matrix.shape[1] != variable_count:
        matrix = matrix.reshape((-1, variable_count))
    _, singular_values, right_vectors = np.linalg.svd(matrix, full_matrices=True)
    if singular_values.size == 0 or singular_values[0] == 0.0:
        rank = 0
    else:
        threshold = (
            max(matrix.shape)
            * (np.finfo(float).eps ** 0.5)
            * float(singular_values[0])
        )
        rank = int(np.count_nonzero(singular_values > threshold))
    nullspace = right_vectors[rank:, :]
    if nullspace.size == 0:
        return rank, []
    participation = np.sqrt(np.sum(nullspace * nullspace, axis=0))
    return rank, [label for label, amount in zip(labels, participation) if amount > 1e-6]
