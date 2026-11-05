from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.benchmark.audit_process import unevaluated_task_audit
from evals.benchmark.pilot import _cost_estimate, _metric_rows, main
from evals.benchmark.corpus.loader import load_corpus
from evals.benchmark.oracles import CheckObservation, StageAudit, TaskAudit, evaluate_stage, evaluate_task, request_pass_counts
from evals.benchmark.selection import load_selection


def test_frozen_pilot_selection_is_20_tasks_and_24_stages() -> None:
    corpus, manifest = load_selection()
    selected = set(manifest["run_order"])
    task_by_id = {task["id"]: task for task in corpus["tasks"]}
    assert len(selected) == 20
    assert manifest["stage_count"] == 24
    assert {task_by_id[task_id]["category"] for task_id in selected} == {
        "part",
        "edit_chain",
        "assembly",
        "2d_profile",
        "drawing",
        "clarification",
        "infeasible",
    }
    assert all(
        len(manifest["strata"][category]["selected_task_ids"]) == quota
        for category, quota in manifest["quotas"].items()
    )
    assert "oracle support" in manifest["rule"]
    assert "outcome" in manifest["rule"]


def test_cost_estimate_is_a_hard_budget_ceiling() -> None:
    estimate = _cost_estimate(2.0, 10.0, provider="openai", model="explicit-model")
    assert estimate["maximum_billable_token_budget"] == 240_000
    assert estimate["maximum_cost_usd_from_declared_token_prices"] == 2.4
    assert estimate["maximum_generation_calls"] == 640
    assert estimate["maximum_input_token_count_api_calls"] == 640
    assert estimate["maximum_provider_api_requests"] == 1_280
    assert estimate["maximum_agent_mcp_tool_calls"] == 1_920
    assert estimate["task_budgets"] == {
        "max_tokens": 12_000,
        "max_generation_calls": 32,
        "max_mcp_tool_calls": 96,
        "max_wall_seconds": 300.0,
        "max_output_tokens_per_generation": 2_048,
        "aad_build_timeout_seconds": 120,
        "repetitions": 1,
    }


def test_daemon_temp_path_stays_short_for_long_task_homes(tmp_path) -> None:
    import shutil

    from evals.benchmark.pilot import _session_environment

    long_home = tmp_path / ("a" * 90)
    for component in ("task-output", "artifacts", "a-long-selected-task-name", "home"):
        long_home /= component
    long_home.mkdir(parents=True)
    environment = _session_environment(long_home)
    temporary_directory = environment.temporary_directory
    try:
        assert temporary_directory.is_dir()
        assert temporary_directory.parent == Path("/tmp")
        socket_path = temporary_directory / "aadd-0123456789abcdef.sock"
        assert len(str(socket_path).encode()) < 100
        assert environment.env["TMPDIR"] == str(temporary_directory)
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)


def test_paid_cost_only_command_exits_without_creating_output_or_running(monkeypatch, tmp_path, capsys) -> None:
    import evals.benchmark.pilot as pilot

    def forbidden_run(*args, **kwargs):
        raise AssertionError("cost-only invocation must not launch the pilot")

    monkeypatch.setattr(pilot, "run_pilot", forbidden_run)
    output = tmp_path / "must-not-exist"
    result = main([
        "--provider", "openai",
        "--model", "explicit-model",
        "--input-usd-per-million", "2",
        "--output-usd-per-million", "10",
        "--output-dir", str(output),
    ])
    rendered = capsys.readouterr().out
    assert result == 0
    assert '"maximum_cost_usd_from_declared_token_prices": 2.4' in rendered
    assert "No provider request was sent" in rendered
    assert not output.exists()


def test_selected_original_request_oracles_reject_wrong_geometry_and_responses(tmp_path) -> None:
    pytest.importorskip("build123d")
    from build123d import Box, export_brep

    corpus, selection = load_selection()
    task_by_id = {task["id"]: task for task in corpus["tasks"]}
    wrong_brep = tmp_path / "one-millimetre-cube.brep"
    assert export_brep(Box(1, 1, 1), str(wrong_brep))
    wrong_sheet = tmp_path / "empty-sheet.json"
    wrong_sheet.write_text(
        json.dumps({"units": "mm", "points": {}, "entities": {}, "layers": {}}),
        encoding="utf-8",
    )
    wrong_drawing = tmp_path / "wrong-drawing.svg"
    wrong_drawing.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<rect x="0" y="0" width="1" height="1"/></svg>',
        encoding="utf-8",
    )

    failing_types: set[str] = set()
    observed_types: set[str] = set()
    for task_id in selection["run_order"]:
        task = task_by_id[task_id]
        evidence = {}
        for stage in task["stages"]:
            types = {check["type"] for check in stage["oracle"]["checks"]}
            row = {
                "brep_path": str(wrong_brep) if types - {"dxf_profile", "clarification"} else None,
                "sheet_json_path": str(wrong_sheet) if "dxf_profile" in types else None,
                "drawing_svg_path": str(wrong_drawing) if "drawing_dimensions" in types else None,
                "response_text": "",
            }
            evidence[stage["id"]] = row
        audit = evaluate_task(task, evidence)
        assert audit.verdict == "bad", task_id
        for stage in audit.stages:
            assert stage.verdict == "bad", f"{task_id}/{stage.stage_id}"
            for check in stage.checks:
                observed_types.add(check.check_type)
                assert check.status != "unsupported", f"{task_id}/{stage.stage_id}/{check.check_id}: {check.reason}"
                if check.status == "fail":
                    failing_types.add(check.check_type)

    assert observed_types == {
        "bbox_mm",
        "solids",
        "point_material",
        "cylindrical_bores",
        "rotational_pattern",
        "linear_pattern",
        "component_layout",
        "dxf_profile",
        "drawing_dimensions",
        "clarification",
    }
    assert failing_types == observed_types


def test_bounded_positive_geometry_witnesses_remain_indeterminate(tmp_path) -> None:
    pytest.importorskip("build123d")
    from build123d import export_brep

    from evals.benchmark.corpus.references import build_reference

    corpus, _ = load_selection()
    task = next(task for task in corpus["tasks"] if task["id"] == "part_plate_001")
    stage = task["stages"][0]
    reference_shape = build_reference(task, stage["id"])
    reference_brep = tmp_path / "part-plate-reference-control.brep"
    assert export_brep(reference_shape, str(reference_brep))
    audit = evaluate_stage(
        task["id"],
        stage,
        brep_path=reference_brep,
    )
    assert all(check.status == "pass" for check in audit.checks)
    assert audit.declared_coverage == "bounded"
    assert audit.verdict == "indeterminate"


def test_unsupported_scope_never_counts_as_a_pass(tmp_path) -> None:
    pytest.importorskip("build123d")
    from build123d import Box, export_brep

    corpus = load_corpus()
    task = next(task for task in corpus["tasks"] if task["id"] == "part_fan_carrier_001")
    unsupported_stage = next(
        stage for stage in task["stages"]
        if any(check["type"] == "clearance_mm" for check in stage["oracle"]["checks"])
    )
    check = next(check for check in unsupported_stage["oracle"]["checks"] if check["type"] == "clearance_mm")
    wrong_brep = tmp_path / "one-millimetre-cube.brep"
    assert export_brep(Box(1, 1, 1), str(wrong_brep))
    audit = evaluate_task(task, {unsupported_stage["id"]: {"brep_path": str(wrong_brep)}})
    measured = next(
        item
        for stage in audit.stages
        for item in stage.checks
        if item.check_id == check["id"]
    )
    assert measured.status == "unsupported"
    counts = request_pass_counts([TaskAudit(
        task_id=task["id"],
        verdict="indeterminate",
        coverage="bounded",
        stages=(StageAudit(
            task_id=task["id"],
            stage_id=unsupported_stage["id"],
            expected_outcome="artifact",
            declared_coverage="bounded",
            effective_coverage="bounded",
            verdict="indeterminate",
            checks=(measured,),
        ),),
    )])
    assert counts["unsupported"] == 1
    assert counts["passed"] == 0
    assert counts["denominator"] == 1
    assert counts["rate"] == 0


def test_metric_report_preserves_raw_denominators_and_uncertainty_scope() -> None:
    corpus, selection = load_selection()
    task_by_id = {task["id"]: task for task in corpus["tasks"]}
    tasks = [task_by_id[task_id] for task_id in selection["run_order"]]
    records = [
        {
            "task_id": task["id"],
            "category": task["category"],
            "origin": "scripted_control",
            "trial": None,
            "first_build_attempts": [],
            "audit": unevaluated_task_audit(task, "test control").as_dict(),
        }
        for task in tasks
    ]

    metrics = _metric_rows(records, tasks, selection, arm_id="scripted-control")
    assert metrics["request_pass_rate"]["denominator"] == 20
    assert metrics["request_check_witnesses"]["expected_declared_checks"] == 98
    assert metrics["request_check_witnesses"]["unsupported"] == 98
    assert metrics["first_pass_build_validity"]["expected_artifact_stage_denominator"] == 21
    assert metrics["first_pass_build_validity"]["uncertainty_95pct"]["expected_cells"] == 17
    assert metrics["clarification_infeasible_disposition"]["uncertainty_95pct"]["expected_cells"] == 3
    assert metrics["false_verified"]["host_manifest_validated"] is False
    assert metrics["false_verified"]["inference"]["upper"] is None
