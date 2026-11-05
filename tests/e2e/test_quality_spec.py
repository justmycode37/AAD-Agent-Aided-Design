"""S5 regression through the actual MCP adapter/daemon/worker over stdio."""
from typing import Any

import pytest

WAIT_S = 200


def initialize(mcp):
    mcp.request('initialize', {'protocolVersion':'2025-11-25', 'capabilities':{},
        'clientInfo':{'name':'quality-regression','version':'1'}})
    mcp.send({'method':'notifications/initialized'})


def call(mcp, tool, **arguments):
    return mcp.request('tools/call', {'name':tool,'arguments':arguments})


@pytest.mark.usefixtures('engine')
def test_an_84_mm_candidate_fails_the_80_mm_spec_over_stdio(mcp_process: Any):
    initialize(mcp_process)
    created = call(mcp_process, 'aad_new', name='quality-width', spec='requirements:\n  - id: width\n    bbox: {x: "80 ± 0.1"}\n')
    assert created['structuredContent']['ok'], created
    original = 'from build123d import Box\nresult = Box(80, 20, 6)\n'
    accepted = call(mcp_process, 'aad_build', project='quality-width', source=original,
        wait_s=WAIT_S)['structuredContent']
    assert accepted['ok'] and accepted['head_rev'] == 1, accepted
    patch = '--- a/model.py\n+++ b/model.py\n@@ -1,2 +1,2 @@\n from build123d import Box\n-result = Box(80, 20, 6)\n+result = Box(84, 20, 6)\n'
    built = call(mcp_process, 'aad_build', project='quality-width', base_rev=1,
        patch=patch, wait_s=WAIT_S)['structuredContent']
    assert built['status'] == 'failed', built
    width = next(c for c in built['checks'] if c['id'] == 'width')
    assert width['status'] == 'fail' and width['suite'] == 'spec', width
    assert '84 mm' in width['actual'] and '80 mm' in width['expected'], width
    history = call(mcp_process, 'aad_history', project='quality-width')['structuredContent']
    assert history['head_rev'] == 1, history
