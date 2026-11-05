"""Fast checks for the offline example's reporting/CLI; no full CAD rebuild in CI."""

from __future__ import annotations

import importlib.util
import json
import sys
from types import ModuleType
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "fixtures/examples/seamaster"


@pytest.fixture
def example(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "seamaster_example", EXAMPLE / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert list(tmp_path.iterdir()) == []  # Import must not construct/export the watch.
    return module


@pytest.fixture
def observed_checks():
    return json.loads((EXAMPLE / "reference/inspection.json").read_text())


def test_reference_evidence_passes_selected_checks(example, observed_checks):
    assert example.validation_failures(observed_checks) == []


@pytest.mark.parametrize(
    "bad_value", [None, float("nan"), float("inf"), -1, True, 0.01]
)
def test_selected_checks_fail_closed(example, observed_checks, bad_value):
    observed_checks["exact_intersections"][0]["intersection_mm3"] = bad_value
    assert example.validation_failures(observed_checks)


def test_missing_results_and_invalid_parts_are_failures(example, observed_checks):
    observed_checks["mesh_sweeps"] = []
    observed_checks["invalid_parts"] = ["Midcase"]
    errors = example.validation_failures(observed_checks)
    assert any("mesh_sweeps" in error for error in errors)
    assert any("Midcase" in error for error in errors)


def test_cli_help_does_not_build(example, monkeypatch, capsys):
    build = Mock(side_effect=AssertionError("Help must not build geometry"))
    monkeypatch.setattr(example, "build_watch", build)
    with pytest.raises(SystemExit) as stopped:
        example.main(["--help"])
    assert stopped.value.code == 0
    assert "--output-dir" in capsys.readouterr().out
    build.assert_not_called()


def test_failed_build_is_nonzero_unless_explicitly_allowed(
    example, monkeypatch, tmp_path
):
    build = Mock(return_value={"selected_check_failures": ["Invalid test component"]})
    monkeypatch.setattr(example, "build_watch", build)
    assert example.main(["--output-dir", str(tmp_path)]) == 1
    build.assert_called_with(tmp_path)
    assert example.main(["--allow-failed-checks"]) == 0


@pytest.mark.parametrize("raises", [False, True])
def test_color_adapter_is_restored_even_on_failure(
    example, monkeypatch, tmp_path, raises
):
    # Rendering packages are optional in the isolated engine test environment.
    tessellate = ModuleType("ocp_tessellate")
    convert = ModuleType("ocp_tessellate.convert")
    tessellate.convert = convert
    renderer = ModuleType("aad_render")
    monkeypatch.setitem(sys.modules, "ocp_tessellate", tessellate)
    monkeypatch.setitem(sys.modules, "ocp_tessellate.convert", convert)
    monkeypatch.setitem(sys.modules, "aad_render", renderer)

    original = Mock(return_value="native group")
    monkeypatch.setattr(convert, "to_ocpgroup", original, raising=False)

    def fake_make_scene(ctx, output):
        assert (
            convert.to_ocpgroup(names=["Blue dial__part-1", "Unspecified"])
            == "native group"
        )
        assert original.call_args.kwargs["colors"] == ["#07346a", "#929ba5"]
        if raises:
            raise RuntimeError("Tessellation failed")
        return "manifest"

    monkeypatch.setattr(renderer, "make_scene", fake_make_scene, raising=False)
    if raises:
        with pytest.raises(RuntimeError, match="Tessellation failed"):
            example.make_colored_scene(None, tmp_path, {"Blue dial": "#07346a"})
    else:
        assert (
            example.make_colored_scene(None, tmp_path, {"Blue dial": "#07346a"})
            == "manifest"
        )
    assert convert.to_ocpgroup is original


def test_named_views_preserve_dial_and_caseback_visibility(example):
    # Prepared recipes are independent: the base hide list will not carry over.
    groups = {
        "dial": ["Blue dial"],
        "crystal": ["Sapphire crystal"],
        "case": ["Steel case", "Caseback sapphire"],
    }
    parts = [
        {"name": name, "path": "/Group/" + name}
        for name in ["Blue dial", "Sapphire crystal", "Steel case", "Caseback sapphire"]
    ]
    recipes = {r["id"]: r["presentation"] for r in example.prepare_views(groups, parts)}
    assert "/Group/Blue dial" in recipes["dial"]["isolate"]
    assert "/Group/Sapphire crystal" not in recipes["dial"]["isolate"]
    assert "Caseback sapphire" in recipes["rear"]["hide"]
