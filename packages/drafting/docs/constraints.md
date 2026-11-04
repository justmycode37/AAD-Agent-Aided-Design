# Named 2D constraints

`aiad_drafting.constraints` provides an independent 2D sketch API. It does not
import `build123d`, the engine, or `model2d`.

```python
from aiad_drafting.constraints import ConstraintSketch

sketch = ConstraintSketch()
sketch.add_point("A", 0, 0)
sketch.add_point("B", 40, 0)
sketch.add_point("C", 40, 25)
sketch.add_point("D", 0, 25)
sketch.add_line("bottom", "A", "B")
sketch.add_line("right", "B", "C")
sketch.add_line("top", "D", "C")
sketch.add_line("left", "A", "D")
sketch.add_constraint("base", "fixed", ["bottom"])
sketch.add_constraint("height", "distance", ["right"], value=25)
sketch.add_constraint("opposite_edges", "parallel", ["bottom", "top"])
sketch.add_constraint("side_edges", "parallel", ["right", "left"])
sketch.add_constraint("square_corner", "perpendicular", ["bottom", "right"])

report = sketch.solve()
print(report.status, report.dof, report.points)
```

Names are unique across points, lines, circles, and constraints. Points and
circles are mutable objects in readable registries (`sketch.points`,
`sketch.lines`, `sketch.circles`); constraint records are in
`sketch.constraints`. Lines reference two point names. Circles reference a
center point name and have a positive radius. Coordinates, lengths, and radii
use the sketch's native linear unit; the API does not assign a unit system.

## Constraint entities

`entities` is an ordered list or tuple of names. Unsupported combinations,
unknown names, invalid values, and degenerate references raise `ConstraintError`
with the constraint name. A zero-length line is rejected when created or when
it becomes numerically degenerate during a solve.

The numerical degeneracy guard treats a line as collapsed below the larger of
`1e-12` times its seed length and eight floating-point ulps at its endpoint
coordinates. Circle radii stay above the larger of `1e-12` times the seed
radius and eight ulps at that radius. These guards only protect numeric
geometry; they are not manufacturing limits.

| Kind | Supported entities | Value or options |
|---|---|---|
| `coincident` | Two distinct points | None; equates both coordinates |
| `distance` | Two distinct points, or one line | Required positive distance in native linear units |
| `angle` | Two distinct lines | Required directed angle in degrees, from the first line to the second, in `[0, 360]` |
| `parallel` | Two distinct lines | None; accepts same or opposite endpoint directions |
| `perpendicular` | Two distinct lines | None |
| `tangent` | One circle and one line, in either order | None; the line is infinite, and the seeded side selects the tangency branch |
| `tangent` | Two distinct circles | `mode="external"` (default) or `mode="internal"` |
| `equal` | Two distinct lines, or two distinct circles | None; equal line lengths or circle radii |
| `symmetric` | Two points and an axis line | None; points mirror across the infinite line |
| `symmetric` | Two points and a center point | None; the center is the midpoint of the point pair |
| `fixed` | One point, line, or circle | None; snapshots its current coordinates, and its radius when fixing a circle |

For `distance`, a point pair cannot start at the same coordinates; use
`coincident` for zero separation. Circle-to-circle tangency also requires
distinct seed centers so the solver has a defined direction. Internal
tangency uses the initial radius ordering as its branch; when radii start
equal, the first listed circle is treated as the larger one. A coincident pair
of equal circles is not reported as a valid internal tangency.
Internal tangency also rejects centers or radius differences that fall below
the solver's relative numeric degeneracy guard.

No point-to-line distance, line-to-line fixed angle value, fixed single
coordinate, or tangent arc constraint is implemented. To constrain a line's
angle to a datum, add a reference line and use `angle`. Unknown or unsupported
cases fail instead of being skipped.

## Solve reports and edits

`solve(tolerance=1e-6, inplace=True)` returns a `SolveReport` with the proposed
point coordinates, circle radii, named constraint residuals and statuses,
numerical rank, degrees of freedom, and free-variable names. The tolerance is
absolute: linear units for length-like residuals and degrees for angular
residuals. `report.to_dict()` returns JSON-safe lists, dictionaries, booleans,
integers, strings, and floats.

- `solved`: all constraints are satisfied and numerical DOF is zero.
- `underconstrained`: all constraints are satisfied; `dof` and
  `free_variables` describe the remaining local freedom. A free variable is a
  named coordinate or radius with a component in the Jacobian nullspace; a
  rigid motion can therefore list several coordinates for only a few DOF.
- `conflicting`: one or more later constraints conflict with earlier accepted
  constraints. Names are listed in `conflicting_constraints`; conflicting
  constraints are excluded from the proposed geometry and their residuals are
  still reported. `ok` is false and the live sketch is unchanged.
- `failed`: the numeric optimizer did not converge. The live sketch is
  unchanged.

Consistent constraints whose Jacobian adds no numerical rank are marked
`redundant` by name. `rank` comes from the numerical Jacobian of accepted
constraints; `dof` is the number of point-coordinate and radius variables minus
that rank. Constraints are tried in insertion order, making conflict
attribution deterministic and preserving earlier named constraints.
Per-constraint statuses are `satisfied`, `redundant`, `conflicting`, or
`not_solved` when optimization fails before the constraint is reached.

Successful underconstrained solves update the live sketch by default. Pass
`inplace=False` to inspect a proposed solution without changing registries.
Inconsistent and failed solves never mutate the sketch even when `inplace=True`.
Distance and angle targets can be edited through the returned constraint record
or `set_constraint_value(name, value)` before solving again. Fixed constraints
keep their original snapshot after later geometry edits.

The solver uses deterministic starting geometry and SciPy's
`scipy.optimize.least_squares`; it does not randomize or impose geometric
maximums. It is a local nonlinear solver, so singular or poorly seeded geometry
may remain underconstrained, fail to converge, or select a different valid
branch than intended. Inspect `ok`, statuses, residuals, and DOF before using a
solution downstream.
