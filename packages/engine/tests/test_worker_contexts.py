"""Operations that build123d validates against the name of the active context."""

import pytest

from aiad.engine.jobs import run_build
from aiad.engine.types import BuildRequest

SOURCE = "from build123d import *\nwith BuildPart() as bp:\n    Box(10, 10, 10)\n    {operation}\nresult = bp.part\n"


@pytest.mark.parametrize(
    "operation",
    [
        "fillet(bp.edges().filter_by(Axis.Z), radius=1)",
        "chamfer(bp.edges().filter_by(Axis.Z), length=1)",
        "offset(amount=-1, openings=bp.faces().sort_by(Axis.Z)[-1])",
        "mirror(about=Plane.YZ)",
        "split(bisect_by=Plane.XY)",
    ],
)
def test_operations_inside_a_build_part_context_run_in_the_worker(tmp_path, operation):
    """Regression: the tracking subclass's name made build123d refuse fillet and chamfer."""
    source = SOURCE.format(operation=operation)
    result = run_build(BuildRequest(source=source, project_dir=tmp_path, job_dir=tmp_path / "job", timeout_s=120))
    assert result.ok, result.failure
    assert (tmp_path / "job" / "part.brep").stat().st_size > 0
