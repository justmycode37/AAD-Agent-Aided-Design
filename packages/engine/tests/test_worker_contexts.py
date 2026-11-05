"""Operations that build123d validates against the name of the active context."""

import pytest

from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest

SOURCE = "from build123d import *\nwith BuildPart() as bp:\n    Box(10, 10, 10)\n    {operation}\nresult = bp.part\n"


def test_named_part_publishes_nested_sketch_and_tracks_extrusion(tmp_path):
    from build123d import import_brep

    source = '''from aad import *
with part("block"):
    with feature("stock"):
        with BuildSketch():
            Rectangle(10, 8)
        extrude(amount=4)
'''
    result = run_build(BuildRequest(source=source, project_dir=tmp_path, job_dir=tmp_path / "job"))
    assert result.ok, result.failure
    actual = import_brep(str(tmp_path / "job" / "part.brep"))
    assert actual.is_valid
    assert actual.volume == pytest.approx(320, abs=1e-6)
    assert result.context.part_name == "block"
    stock, = result.context.features
    assert stock.name == "stock"
    assert len(stock.faces) == len(actual.faces()) == 6


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


NESTED_MODELS = [
    ('sketch_and_line', '''with BuildPart() as bp:
    with BuildSketch():
        with BuildLine():
            Polyline((0,0),(10,0),(10,10),(0,10),close=True)
        make_face()
    extrude(amount=10)
''', 'bp'),
    ('additive_part', '''with BuildPart() as bp:
    Box(10,10,10)
    with BuildPart():
        with Locations((10,0,0)):
            Box(10,10,10)
''', 'bp'),
    ('subtractive_part', '''with BuildPart() as bp:
    Box(10,10,10)
    with BuildPart(mode=Mode.SUBTRACT):
        Box(2,2,2)
''', 'bp'),
    ('nested_private', '''with BuildPart() as bp:
    Box(10,10,10)
    with BuildPart(mode=Mode.PRIVATE):
        Box(2,2,2)
''', 'bp'),
    ('function_private', '''def helper():
    with BuildPart() as local:
        Box(2,2,2)
    return local.part
with BuildPart() as bp:
    Box(10,10,10)
    helper()
''', 'bp'),
    ('late_private', '''with BuildPart() as bp:
    Box(10,10,10)
with BuildPart(mode=Mode.PRIVATE):
    Box(2,2,2)
''', 'bp'),
    ('unentered_helper', '''with BuildPart() as bp:
    Box(10,10,10)
helper = BuildPart()
helper.part = Box(2,2,2)
''', 'bp'),
    ('failed_helper', '''with BuildPart() as bp:
    Box(10,10,10)
try:
    with BuildPart():
        Box(2,2,2)
        raise ValueError('discard this helper')
except ValueError:
    pass
''', 'bp'),
    ('delayed_root', '''with BuildPart() as bp:
    Box(10,10,10)
    last = BuildPart()
with last:
    Box(2,2,2)
''', 'last'),
    ('explicit_private', '''with BuildPart() as bp:
    Box(10,10,10)
    with BuildPart(mode=Mode.PRIVATE) as helper:
        Box(2,2,2)
result = helper.part
''', 'helper'),
]


NESTED_MODELS.extend([
    ('failed_reentered_exit', """with BuildPart() as earlier:
    Box(2,2,2)
with BuildPart() as bp:
    Box(10,10,10)
def fail_exit():
    raise ValueError('discard failed exit')
bp._exit_extras = fail_exit
try:
    with bp:
        pass
except ValueError:
    pass
""", 'earlier'),
    ('constructed_early_completed_last', """last = BuildPart()
with BuildPart() as bp:
    Box(10,10,10)
with last:
    Box(2,2,2)
""", 'last'),
    ('reentered_root_completed_last', """with BuildPart() as bp:
    Box(10,10,10)
with BuildPart():
    Box(2,2,2)
with bp:
    pass
""", 'bp'),
])


@pytest.mark.parametrize('name,body,expected_builder', NESTED_MODELS, ids=[row[0] for row in NESTED_MODELS])
def test_nested_models_preserve_plain_build123d_geometry(tmp_path, name, body, expected_builder):
    """The exported native artifact must match plain build123d, not a helper or cached metric."""
    from build123d import import_brep
    source = 'from build123d import *\n' + body
    plain = {}
    exec(compile(source, 'plain_context_control.py', 'exec'), plain)
    expected = plain[expected_builder].part
    assert expected is not None and expected.is_valid
    result = run_build(BuildRequest(source=source, project_dir=tmp_path, job_dir=tmp_path / 'job', timeout_s=120))
    assert result.ok, result.failure
    actual = import_brep(str(tmp_path / 'job' / 'part.brep'))
    assert actual.volume == pytest.approx(expected.volume, abs=1e-6)
    assert [len(actual.faces()), len(actual.edges()), len(actual.solids())] == [
        len(expected.faces()), len(expected.edges()), len(expected.solids())]
