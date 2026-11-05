"""Exercise actual catalog routing, bounded lookup and installed-resource behavior."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys

import pytest

from aad_quality import library
from aad_quality.catalog import server_instruction_candidate, tool_description_candidates


def test_search_returns_bounded_summaries_and_get_loads_one_entry():
    search = library("search")
    assert len(search["items"]) == 5
    assert search["total"] > len(search["items"])
    assert search["truncated"] is True
    assert all(set(item) == {"id", "kind", "title", "summary"} for item in search["items"])
    for summary in search["items"]:
        result = library("get", kind=summary["kind"], id=summary["id"])
        assert result["found"] and result["item"]["id"] == summary["id"]
    assert len(json.dumps(search)) < 2500


def test_material_lookup_is_exact_and_defensive():
    result = library("get", kind="material", id="pla")
    assert result["found"]
    assert result["item"]["density_g_cm3"] == 1.24
    result["item"]["assumptions"]["porosity"] = "mutated"
    assert "mutated" not in library("get", kind="material", id="pla")["item"]["assumptions"]["porosity"]
    assert library("get", kind="material", id="PLA")["found"] is False


@pytest.mark.parametrize(("query", "material_id"), [
    ("ABS", "abs"), ("PETG", "petg"), ("nylon PA12", "pa12"),
    ("6061 aluminium", "aluminum_6061"), ("aluminium 7075", "aluminum_7075"),
    ("5052", "aluminum_5052"), ("stainless 316", "stainless_304_4301"),
])
def test_material_search_resolves_common_alias_terms(query, material_id):
    # 316 is deliberately not aliased; this query should not silently return 304.
    results = library("search", kind="material", query=query)
    ids = {item["id"] for item in results["items"]}
    if "316" in query:
        assert "stainless_304_4301" not in ids
    else:
        assert material_id in ids


def test_curved_modeling_recipes_are_discoverable_and_build_single_solids():
    import re
    from aad._runtime import configure_params
    from aad.modeling import configure_modeling
    from build123d import GeomType

    results = library("search", kind="playbook", query="curved freeform")
    assert any(item["id"] == "modeling.curved-freeform.v1" for item in results["items"])
    entry = library("get", kind="playbook", id="modeling.curved-freeform.v1")["item"]
    recipes = re.findall(r"```python\n(.*?)```", entry["text"], re.S)
    assert len(recipes) == 3
    for source in recipes:
        configure_params()
        configure_modeling(source)
        namespace = {}
        exec(compile(source, entry["id"], "exec"), namespace)
        shape = namespace["result"]
        assert shape.is_valid and len(shape.solids()) == 1
        assert any(face.geom_type == GeomType.BSPLINE for face in shape.faces())


def test_playbook_and_api_search_find_content_without_dumping_text():
    results = library("search", kind="playbook", query="counterbore")
    assert results["total"] > 0
    assert all(item["kind"] == "playbook" and "text" not in item for item in results["items"])
    entry = library("get", kind="playbook", id=results["items"][0]["id"])["item"]
    assert "counterbore" in entry["text"].casefold()
    assert entry["candidate_status"] == "unmeasured"
    assert library("search", kind="api", query="nominal mass")["total"] > 0
    assert library("search", query="zzzznotinthecatalogzzzz")["items"] == []


@pytest.mark.parametrize("args", [
    {"action": "execute"}, {"action": []}, {"action": "search", "kind": []},
    {"action": "search", "kind": "secret"}, {"action": "search", "query": 1},
    {"action": "search", "query": "x" * 513}, {"action": "search", "id": "pla"},
    {"action": "get"}, {"action": "get", "id": "../secrets"},
    {"action": "get", "id": "/etc/passwd"}, {"action": "get", "id": "pla", "query": "pla"},
])
def test_catalog_arguments_cannot_be_paths_or_code(args):
    with pytest.raises(ValueError):
        library(**args)


def test_all_catalog_lookups_are_kernel_free_in_fresh_process():
    result = subprocess.run([sys.executable, "-c", """
import sys
from aad_quality import library
for kind in ('part','material','playbook','api'):
    result=library('search',kind=kind)
    assert result['total']>0, kind
    for entry in result['items']:
        assert library('get',kind=kind,id=entry['id'])['found']
assert not any(n=='build123d' or n=='OCP' or n.startswith('OCP.') or n=='bd_warehouse' for n in sys.modules)
"""], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_repeated_public_calls_do_not_replace_the_function_with_a_module():
    result = subprocess.run([sys.executable, "-c", """
import aad_quality
for _ in range(3):
    assert callable(aad_quality.library)
    assert aad_quality.library('get',kind='material',id='pla')['found']
    assert aad_quality.library('search',kind='part')['total']>0
"""], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_description_candidates_cover_contract_without_claiming_improvement():
    candidates = tool_description_candidates()
    assert len(candidates["model_visible"]) == 13
    assert len(candidates["app_only"]) == 4
    for description in candidates["app_only"].values():
        assert description.startswith("Internal viewer tool. Do not call.")
    for group in ("model_visible", "app_only"):
        assert all("unmeasured" in desc.casefold() for desc in candidates[group].values())
    assert "unmeasured" in server_instruction_candidate().casefold()


def test_main_skill_is_served_as_the_guide_and_its_examples_build(tmp_path):
    from aad.engine.jobs import run_build
    from aad.engine.types import BuildRequest

    entry = library("get", kind="playbook", id="aad.guide.v1")["item"]
    assert entry["text"].startswith("# AAD guide")
    assert "aad.guide.v1" in [item["id"] for item in library("search", kind="playbook", query="guide")["items"]]
    blocks = [block.split("```", 1)[0] for block in entry["text"].split("```python\n")[1:]]
    model, _selectors, assembly, gears, sheet = blocks

    built = run_build(BuildRequest(source=model, job_dir=tmp_path / "job", material="steel_1018"))
    assert built.ok, built.failure
    assert built.metrics["solids"] == 1

    namespace = {}
    exec("from aad import *\n" + assembly, namespace)
    assert namespace["result"].bom()[0]["quantity"] == 3
    assert len(namespace["result"].compound().solids()) == 4 and namespace["result"].compound().is_valid
    if importlib.util.find_spec("aad_drafting"):
        exec(sheet, namespace)
        assert namespace["plate"].to_face().area > 0
    exec("from aad import *\n" + gears, namespace)
    assert namespace["pinion"].is_valid and not (namespace["pinion"] & namespace["wheel"])


def test_published_core_example_builds_the_requested_geometry():
    from aad_quality.catalog import _guidance_root
    text = _guidance_root().joinpath('playbooks/core-workflow.md').read_text(encoding='utf-8')
    source = '\n'.join(line[4:] for line in text.splitlines() if line.startswith('    '))
    assert 'result = bracket.part' in source
    from aad._runtime import configure_params
    configure_params({})  # params() is per build; another test may have run a model in this process.
    namespace = {}
    exec(compile(source, 'core-workflow.md', 'exec'), namespace)
    shape = namespace['result']
    assert shape.is_valid and len(shape.solids()) == 1
    box = shape.bounding_box().size
    assert (box.X, box.Y, box.Z) == pytest.approx((60, 40, 4), abs=1e-6)
    assert shape.volume == pytest.approx(60*40*4 - 4*3.141592653589793*2.2**2*4, abs=1e-6)
