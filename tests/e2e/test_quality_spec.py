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


@pytest.mark.usefixtures('engine')
def test_functional_breakdown_checks_labels_evidence_and_review_over_stdio(mcp_process: Any):
    initialize(mcp_process)
    spec = (
        "requirements:\n  - id: body_count\n    solids: 2\n"
        "subsystems:\n  - id: drive\n    purpose: Transmit motor torque\n"
        "    parts: [base, motor]\n    interfaces:\n      - id: mount\n"
        "        kind: mounting\n        between: [base, motor]\n"
        "        requirements: [body_count]\n    evidence: [body_count]\n"
        "    status: modeled\n"
    )
    created = call(mcp_process, 'aad_new', name='functional-drive', kind='assembly', spec=spec)
    assert created['structuredContent']['ok'], created
    source = (
        "from aad import assembly\nfrom build123d import Box, Pos\n"
        "asm = assembly('drive')\n"
        "asm.add('base', Box(10, 10, 2))\n"
        "asm.add('motor', Box(4, 4, 4), at=Pos(0, 0, 4))\n"
        "result = asm\n"
    )
    built = call(mcp_process, 'aad_build', project='functional-drive', source=source,
                 wait_s=WAIT_S)['structuredContent']
    assert built['ok'] and built['head_rev'] == 1, built

    checked = call(mcp_process, 'aad_check', project='functional-drive', suites=['spec'])['structuredContent']
    assert checked['ok'], checked
    assert checked['checks_summary']['pass'] >= 3, checked
    assert not checked.get('checks'), checked

    inspected = call(mcp_process, 'aad_inspect', project='functional-drive', what='summary')['structuredContent']
    breakdown = inspected['request_review']['functional_breakdown']
    assert breakdown['meaning_assessed'] is False
    assert breakdown['function_proven'] is False
    assert breakdown['subsystems'][0]['review_status'] == 'checks_passed'
    assert breakdown['subsystems'][0]['interfaces'][0]['review_status'] == 'checks_passed'
    assert 'meaning and function are not assessed' in inspected['summary']
