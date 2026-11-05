"""The added CAD capabilities through real MCP calls and the sandboxed worker."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .test_smoke import call, initialize

WAIT_S = 90
pytestmark = pytest.mark.usefixtures("engine")
BLOCK = "from aad import *\nwith part('x'):\n    with feature('a'):\n        Box(20, 10, 5)\n"
ASSEMBLY = """
from aad import *
asm = assembly('stack')
for level in range(3):
    asm.add(f'plate{level}', Box(20, 20, 2), at=Pos(0, 0, level * 5), part_number='PL-1')
asm.add('pin', Cylinder(1, 12), at=Pos(8, 8, 5), part_number='PN-1')
result = asm
"""
SHEET = """
from aad import *
from aad_drafting import sheet2d
plate = sheet2d('plate')
plate.rectangle('outline', 40, 20, origin=(0, 0))
plate.circle('hole', (10, 10), 2)
"""


def ok(result: dict[str, Any]) -> dict[str, Any]:
    envelope = result["structuredContent"]
    assert envelope["ok"], envelope
    return envelope


def built(mcp: Any, name: str, source: str, **new: Any) -> dict[str, Any]:
    ok(call(mcp, "aad_new", name=name, **new))
    return ok(call(mcp, "aad_build", project=name, source=source, wait_s=WAIT_S))


def test_new_measurements(mcp_process: Any) -> None:
    initialize(mcp_process)
    built(mcp_process, "m", BLOCK, material="pla", units="mm")

    def measure(**arguments: Any) -> dict[str, Any]:
        return ok(call(mcp_process, "aad_measure", project="m", **arguments))["measurement"]

    assert measure(kind="mass")["value"] == pytest.approx(1000 * 1e-3 * 1.24)
    assert measure(kind="bbox")["value"] == pytest.approx([20, 10, 5])
    assert measure(kind="center_of_mass")["value"] == pytest.approx([0, 0, 0], abs=1e-9)
    principal = measure(kind="principal_inertia")
    assert sorted(principal["value"]) == pytest.approx([10416.6667, 35416.6667, 41666.6667], rel=1e-5)
    thickness = measure(kind="thickness")
    assert thickness["value"] == pytest.approx(5)
    assert "not_a_proven" in thickness["definition"]
    edge = "edge(kind='line', parallel_xyz='x', nth=0)"
    assert measure(kind="length", a=edge)["value"] == pytest.approx(20)


def test_mass_without_a_material_is_refused(mcp_process: Any) -> None:
    initialize(mcp_process)
    built(mcp_process, "nomat", BLOCK)
    result = call(mcp_process, "aad_measure", project="nomat", kind="mass")["structuredContent"]
    assert not result["ok"] and "material" in result["failure"]["message"]


def test_exchange_round_trips_through_asset(mcp_process: Any) -> None:
    initialize(mcp_process)
    built(mcp_process, "x", BLOCK)
    formats = ["step", "iges", "obj", "ply", "viewer_html"]
    files = ok(call(mcp_process, "aad_export", project="x", formats=formats, allow_unverified=True))["artifacts"]
    for fmt in formats:
        assert Path(files[fmt]).stat().st_size > 0
    assert Path(files["viewer_html"]).read_text(encoding="utf-8").lstrip().lower().startswith("<!doctype html")
    assert "\nf " in Path(files["obj"]).read_text(encoding="ascii")

    step = Path(files["step"]).name
    assert ok(call(mcp_process, "aad_import", project="x", path=files["step"]))["imported"]["solids"] == 1
    ok(call(mcp_process, "aad_import", project="x", path=files["iges"]))
    edited = ok(call(mcp_process, "aad_build", project="x", wait_s=WAIT_S,
                     source=f"from aad import *\nresult = asset({step!r}) - Cylinder(1, 50)\n"))
    assert edited["metrics"]["volume"] == pytest.approx(1000 - 3.141592653589793 * 5, rel=1e-6)
    iges = Path(files["iges"]).name
    ok(call(mcp_process, "aad_build", project="x", wait_s=WAIT_S,
            source=f"from aad import *\nassert len(asset({iges!r}).faces()) == 6\nresult = Box(1, 1, 1)\n"))


def test_missing_asset_is_a_clear_failure(mcp_process: Any) -> None:
    initialize(mcp_process)
    built(mcp_process, "noasset", BLOCK)
    result = call(mcp_process, "aad_build", project="noasset", wait_s=WAIT_S,
                  source="from aad import *\nresult = asset('nope.step')\n")["structuredContent"]
    assert not result["ok"] and "aad_import" in result["failure"]["message"]


def test_assembly_keeps_its_components_across_operations(mcp_process: Any) -> None:
    initialize(mcp_process)
    envelope = built(mcp_process, "a", ASSEMBLY, kind="assembly", material="pla", units="mm")
    assert envelope["metrics"]["solids"] == 4
    rows = ok(call(mcp_process, "aad_inspect", project="a", what="bom"))["bom"]
    assert [(row["part_number"], row["quantity"]) for row in rows] == [("PL-1", 3), ("PN-1", 1)]
    mass = ok(call(mcp_process, "aad_measure", project="a", kind="mass"))["measurement"]
    assert mass["value"] == pytest.approx(sum(r["unit_volume"] * r["quantity"] for r in rows) * 1e-3 * 1.24)
    checks = ok(call(mcp_process, "aad_check", project="a", suites=["interference"]))["checks"]
    assert any(check["status"] == "fail" for check in checks), "the pin passes through two plates"
    drawing = ok(call(mcp_process, "aad_drawing", project="a", formats=["svg"], bom=True, notes=["Deburr"],
                      revisions=[{"revision": "A", "date": "2026-11-05", "description": "First", "by": "AAD"}]))
    assert "2 BOM rows" in drawing["summary"] and "1 notes" in drawing["summary"]


def test_sheet_builds_checks_shows_and_exports(mcp_process: Any) -> None:
    initialize(mcp_process)
    envelope = built(mcp_process, "s", SHEET, kind="2d", units="mm")
    assert envelope["status"] == "draft"
    assert envelope["metrics"]["bbox"][:2] == pytest.approx([40, 20])
    assert envelope["metrics"]["area"] == pytest.approx(40 * 20 - 3.141592653589793 * 4)
    assert ok(call(mcp_process, "aad_show", project="s"))["scene"]["format"] == "svg-1"
    assert ok(call(mcp_process, "aad_check", project="s"))["checks_summary"]["fail"] == 0
    assert call(mcp_process, "aad_render", project="s")["content"][-1]["type"] == "image"
    files = ok(call(mcp_process, "aad_export", project="s", formats=["dxf", "svg", "pdf"], allow_unverified=True))["artifacts"]
    assert set(files) == {"dxf", "svg", "pdf"}
    imported = ok(call(mcp_process, "aad_import", project="s", path=files["dxf"]))["imported"]
    assert imported["format"] == "dxf" and imported["entities"] >= 2
    refused = call(mcp_process, "aad_export", project="s", formats=["step"], allow_unverified=True)["structuredContent"]
    assert not refused["ok"] and "Extrude" in refused["failure"]["message"]


def test_open_cut_contour_fails_the_build_without_moving_head(mcp_process: Any) -> None:
    initialize(mcp_process)
    ok(call(mcp_process, "aad_new", name="open", kind="2d", units="mm"))
    source = "from aad_drafting import sheet2d\nsheet2d('p').line('l', (0, 0), (5, 5))\n"
    result = call(mcp_process, "aad_build", project="open", source=source, wait_s=WAIT_S)["structuredContent"]
    assert not result["ok"] and "closed_contours" in result["summary"]
    assert result.get("head_rev") is None


@pytest.mark.parametrize(("width", "expected_status"), [(80, "pass"), (999, "fail")])
def test_2d_bbox_requirements_use_measured_sheet_geometry(mcp_process: Any, width: int, expected_status: str) -> None:
    initialize(mcp_process)
    spec = "requirements:\n  - id: size\n    bbox: {x: 80, y: 40}\n"
    ok(call(mcp_process, "aad_new", name="sized", kind="2d", units="mm", spec=spec))
    source = f"from aad_drafting import sheet2d\nsheet2d('p').rectangle('outline', {width}, 40)\n"
    result = call(mcp_process, "aad_build", project="sized", source=source, wait_s=WAIT_S)["structuredContent"]
    check = next(item for item in result["checks"] if item["id"] == "size")
    assert check["status"] == expected_status
    assert result["ok"] is (expected_status == "pass")


def test_2d_hole_mismatch_fails_and_unsupported_type_skips(mcp_process: Any) -> None:
    initialize(mcp_process)
    spec = "requirements:\n  - id: holes\n    holes: {count: 2, diameter: 5}\n  - id: mass\n    mass_g: {max: 1}\n"
    ok(call(mcp_process, "aad_new", name="holes", kind="2d", units="mm", spec=spec))
    source = "from aad_drafting import sheet2d\ns=sheet2d('p'); s.rectangle('outline', 80, 40); s.circle('h1', (10,10), 2.5)\n"
    result = call(mcp_process, "aad_build", project="holes", source=source, wait_s=WAIT_S)["structuredContent"]
    checks = {item["id"]: item for item in result["checks"]}
    assert not result["ok"] and checks["holes"]["status"] == "fail"
    assert checks["mass"]["status"] == "skip" and "no supported 2D meaning" in checks["mass"]["message"]


@pytest.mark.parametrize("source", [
    "from aad_drafting import sheet2d\nsheet2d('p')\n",
    "from aad_drafting import sheet2d\nsheet2d('p').line('l', (0, 0), (5, 5), layer='score')\n",
])
def test_sheet_without_cut_geometry_is_refused(mcp_process: Any, source: str) -> None:
    initialize(mcp_process)
    ok(call(mcp_process, "aad_new", name="empty", kind="2d", units="mm"))
    result = call(mcp_process, "aad_build", project="empty", source=source, wait_s=WAIT_S)["structuredContent"]
    assert not result["ok"] and "no cut geometry" in result["failure"]["message"]


def test_material_from_the_model_gives_mass(mcp_process: Any) -> None:
    initialize(mcp_process)
    built(mcp_process, "meta", "from aad import *\nmeta(material='pla')\nresult = Box(10, 10, 10)\n")
    assert ok(call(mcp_process, "aad_measure", project="meta", kind="mass"))["measurement"]["value"] == pytest.approx(1.24)


def test_alias_import_of_asset_is_staged_and_inch_project_keeps_size(mcp_process: Any) -> None:
    initialize(mcp_process)
    built(mcp_process, "inch", "from aad import *\nmeta(units='in')\nresult = Box(1, 1, 1)\n", units="in")
    step = ok(call(mcp_process, "aad_export", project="inch", formats=["step", "iges"], allow_unverified=True))["artifacts"]
    ok(call(mcp_process, "aad_import", project="inch", path=step["step"]))
    name = Path(step["step"]).name
    edited = ok(call(mcp_process, "aad_build", project="inch", wait_s=WAIT_S,
                     source=f"from aad import *\nfrom aad import asset as load\nmeta(units='in')\nresult = load({name!r})\n"))
    assert edited["metrics"]["bbox"] == pytest.approx([1, 1, 1], abs=1e-6)
    assert not list(Path(step["step"]).parents[1].glob(".aad/revs/*/assets"))
