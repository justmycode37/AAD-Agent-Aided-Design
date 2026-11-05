"""Nested modeling and implicit output through the real MCP stdio path."""
from pathlib import Path

import pytest


@pytest.mark.parametrize('name,source', [
    ('nested-sketch', '''from build123d import *
with BuildPart() as bp:
    with BuildSketch():
        Rectangle(10,10)
    extrude(amount=10)
'''),
    ('private-helper', '''from build123d import *
with BuildPart() as bp:
    Box(10,10,10)
    with BuildPart(mode=Mode.PRIVATE):
        Box(2,2,2)
'''),
])
def test_nested_modeling_keeps_the_outer_part_over_stdio(mcp_process, aad_env, name, source):
    from build123d import import_brep
    mcp_process.request('initialize', {'protocolVersion':'2025-11-25','capabilities':{},
        'clientInfo':{'name':'builder-regression','version':'1'}})
    mcp_process.send({'method':'notifications/initialized'})
    def call(tool, **arguments):
        return mcp_process.request('tools/call', {'name':tool,'arguments':arguments})['structuredContent']
    created = call('aad_new', name=name)
    assert created['ok'], created
    built = call('aad_build', project=name, source=source, wait_s=200)
    assert built['ok'] and built['head_rev'] == 1, built
    # Read the immutable native artifact using the on-disk contract, rather
    # than treating the worker's own reported metrics as the test oracle.
    path = Path(aad_env['HOME']) / 'AAD' / name / '.aad/revs/000001/part.brep'
    native = import_brep(str(path))
    assert native.volume == pytest.approx(1000, abs=1e-6)
    assert len(native.solids()) == 1
