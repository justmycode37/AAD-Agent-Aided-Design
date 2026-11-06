from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from evals.benchmark.audit_process import unevaluated_task_audit
from evals.benchmark.pilot import (
    _artifact_evidence,
    _cost_estimate,
    _false_verified_task_observation,
    _metric_rows,
    _report_provider_name,
    main,
    run_pilot,
)
from evals.benchmark.corpus.loader import load_corpus
from evals.benchmark.oracles import StageAudit, TaskAudit, evaluate_stage, evaluate_task, request_pass_counts
from evals.benchmark.selection import load_selection
from evals.benchmark.harness.aad_benchmark_harness.types import SpendConsent
from evals.benchmark.statistics import false_verified_summary


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


def test_cost_estimate_labels_configured_budget_and_caller_supplied_prices() -> None:
    estimate = _cost_estimate(2.0, 10.0, provider="openai", model="explicit-model")
    assert estimate["configured_token_budget_total"] == 240_000
    assert estimate["estimated_cost_usd_at_configured_token_budget"] == 2.4
    assert estimate["price_source"] == "caller_supplied"
    assert estimate["prices_are_absolute_spend_bounds"] is False
    assert estimate["maximum_generation_calls"] == 640
    assert estimate["maximum_input_token_count_api_calls"] == 640
    assert estimate["maximum_provider_api_requests"] == 1_280
    assert estimate["maximum_agent_mcp_tool_calls"] == 1_920
    assert estimate["maximum_output_tokens_across_generations"] == 640 * 2_048
    assert estimate["task_budgets"] == {
        "max_tokens": 12_000,
        "max_generation_calls": 32,
        "max_mcp_tool_calls": 96,
        "max_wall_seconds": 300.0,
        "max_output_tokens_per_generation": 2_048,
        "aad_build_timeout_seconds": 120,
        "repetitions": 1,
    }


def test_scripted_report_names_the_provider_as_scripted() -> None:
    assert _report_provider_name("openai", scripted=True) == "scripted"
    assert _report_provider_name("anthropic", scripted=False) == "anthropic"


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
    assert '"estimated_cost_usd_at_configured_token_budget": 2.4' in rendered
    assert "No provider request was sent" in rendered
    assert not output.exists()


def test_pilot_cli_passes_consent_object_only_after_confirmation(monkeypatch, tmp_path, capsys) -> None:
    import evals.benchmark.pilot as pilot

    captured = {}

    async def fake_run_pilot(args, **kwargs):
        captured.update(kwargs)
        return {
            "completed_task_count": 0,
            "metrics": {
                "request_pass_rate": {"rate": None},
                "false_verified": {"n_verified": 0, "bad": 0, "blockers": ["scripted test"]},
            },
            "release_gate": "not evaluated",
        }

    monkeypatch.setattr(pilot, "run_pilot", fake_run_pilot)
    output = tmp_path / "confirmed-pilot"
    assert main([
        "--provider", "openai",
        "--model", "explicit-model",
        "--input-usd-per-million", "2",
        "--output-usd-per-million", "10",
        "--output-dir", str(output),
        "--confirm-spend",
    ]) == 0
    assert isinstance(captured["spend_consent"], SpendConsent)
    assert captured["spend_consent"].confirmed is True
    rendered = capsys.readouterr().out
    assert '"mode": "paid_pilot_cost_estimate"' in rendered
    assert '"price_source": "caller_supplied"' in rendered


def test_direct_live_pilot_entry_requires_consent_before_preflight(tmp_path) -> None:
    from types import SimpleNamespace

    args = SimpleNamespace(offline_smoke=False)
    with pytest.raises(PermissionError, match="explicit --confirm-spend"):
        asyncio.run(run_pilot(
            args,
            selection={},
            corpus={},
            public={},
            output=tmp_path,
            cost_estimate=None,
        ))


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
        expected_task_verdict = "indeterminate" if task["category"] in {"clarification", "infeasible"} else "bad"
        assert audit.verdict == expected_task_verdict, task_id
        for stage in audit.stages:
            assert stage.verdict == expected_task_verdict, f"{task_id}/{stage.stage_id}"
            for check in stage.checks:
                observed_types.add(check.check_type)
                if check.check_type == "clarification":
                    assert check.status == "unsupported"
                else:
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
    assert failing_types == observed_types - {"clarification"}
    assert all(
        check.status == "unsupported"
        for task_id in selection["run_order"]
        if task_by_id[task_id]["category"] in {"clarification", "infeasible"}
        for stage in evaluate_task(task_by_id[task_id], {
            candidate["id"]: {"response_text": ""}
            for candidate in task_by_id[task_id]["stages"]
        }).stages
        for check in stage.checks
    )


@pytest.mark.parametrize(("task_id", "response"), [
    (
        "clarify_electronics_box_002",
        "Do not ask about board length, width, thickness, or hole pattern; connector locations and clearances; lid preference and sealing; material, process, wall limits, units, or tolerances.",
    ),
    (
        "infeasible_envelope_005",
        "The 109.9 mm cartridge fits within the 105.1 mm span.",
    ),
    (
        "infeasible_envelope_005",
        "The 109.9 mm cartridge exceeds the 105.1 mm span, but it fits within that span.",
    ),
])
def test_negated_or_denying_clarification_responses_are_not_successes(task_id, response) -> None:
    corpus, _ = load_selection()
    task = next(task for task in corpus["tasks"] if task["id"] == task_id)
    evidence = {stage["id"]: {"response_text": response} for stage in task["stages"]}
    audit = evaluate_task(task, evidence)

    assert audit.verdict == "indeterminate"
    assert audit.stages[0].checks[0].status == "unsupported"
    counts = request_pass_counts([audit])
    assert counts["passed"] == 0
    assert counts["unsupported"] == 1
    assert counts["denominator"] == 1


def test_bore_oracle_checks_both_openings_cross_section_and_request_tolerance(tmp_path) -> None:
    pytest.importorskip("build123d")
    from build123d import Box, Cylinder, Pos, export_brep

    check = {
        "id": "through_bore",
        "type": "cylindrical_bores",
        "bores": [{
            "axis_mm": [0.0, 0.0, 1.0],
            "center_mm": [0.0, 0.0, 0.0],
            "center_tolerance_mm": 0.05,
            "diameter_mm": [3.95, 4.05],
            "depth_mm": [9.9, 10.1],
            "through": True,
        }],
    }
    stage = {
        "id": "bore-stage",
        "expected_outcome": "artifact",
        "oracle": {"coverage": "bounded", "checks": [check]},
    }

    open_path = tmp_path / "open-through-bore.brep"
    assert export_brep(Box(20, 20, 10) - Cylinder(2, 10), str(open_path))
    open_audit = evaluate_stage("bore-control", stage, brep_path=open_path)
    assert open_audit.checks[0].status == "pass"

    capped_path = tmp_path / "capped-through-bore.brep"
    assert export_brep(Box(20, 20, 10) - Cylinder(2, 9), str(capped_path))
    capped_audit = evaluate_stage("bore-control", stage, brep_path=capped_path)
    assert capped_audit.checks[0].status == "fail"

    offset_path = tmp_path / "out-of-tolerance-bore.brep"
    assert export_brep(Box(20, 20, 10) - (Pos(0.075, 0, 0) * Cylinder(2, 10)), str(offset_path))
    offset_audit = evaluate_stage("bore-control", stage, brep_path=offset_path)
    assert offset_audit.checks[0].status == "fail"

    missing_tolerance = {
        **check,
        "bores": [{key: value for key, value in check["bores"][0].items() if key != "center_tolerance_mm"}],
    }
    unsupported_stage = {**stage, "oracle": {"coverage": "bounded", "checks": [missing_tolerance]}}
    unsupported = evaluate_stage("bore-control", unsupported_stage, brep_path=open_path)
    assert unsupported.checks[0].status == "unsupported"


def test_enclosure_cavity_probe_rejects_a_center_pinhole(tmp_path) -> None:
    pytest.importorskip("build123d")
    from build123d import Align, Box, Cylinder, export_brep

    from evals.benchmark.corpus.references import build_reference

    corpus, _ = load_selection()
    task = next(task for task in corpus["tasks"] if task["id"] == "edit_enclosure_add_mounts_011")
    stage = task["stages"][0]

    pinhole_path = tmp_path / "solid-enclosure-with-pinhole.brep"
    pinhole_shape = Box(80, 60, 40, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Cylinder(
        1,
        40,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    assert export_brep(pinhole_shape, str(pinhole_path))
    pinhole_audit = evaluate_stage(task["id"], stage, brep_path=pinhole_path)
    cavity_check = next(check for check in pinhole_audit.checks if check.check_id == "cavity_void")
    assert cavity_check.status == "fail"
    assert cavity_check.actual["states"] == ["void", "solid", "solid", "solid", "solid"]

    reference_path = tmp_path / "open-enclosure-reference.brep"
    assert export_brep(build_reference(task, stage["id"]), str(reference_path))
    reference_audit = evaluate_stage(task["id"], stage, brep_path=reference_path)
    cavity_reference = next(check for check in reference_audit.checks if check.check_id == "cavity_void")
    assert cavity_reference.status == "pass"


def test_artifact_evidence_never_borrows_a_later_stage_revision(tmp_path) -> None:
    pytest.importorskip("build123d")
    from build123d import export_brep

    from evals.benchmark.corpus.references import build_reference

    corpus, _ = load_selection()
    task = next(task for task in corpus["tasks"] if task["id"] == "edit_enclosure_add_mounts_011")
    home = tmp_path / "task-home"
    project_root = home / "AAD" / "edit-project"
    revision_dir = project_root / ".aad" / "revs" / "000002"
    revision_dir.mkdir(parents=True)
    brep_path = revision_dir / "part.brep"
    assert export_brep(build_reference(task, "edit_1"), str(brep_path))
    (project_root / "project.json").write_text(json.dumps({"head_rev": 2}), encoding="utf-8")

    later_build = {
        "stage_id": "edit_1",
        "tool": "aad_build",
        "project": "edit-project",
        "rev": 2,
        "status": "verified",
        "ok": True,
    }
    initial_create_without_revision = {
        "stage_id": "initial",
        "tool": "aad_new",
        "project": "edit-project",
        "status": "ok",
        "ok": True,
    }

    for calls in ([later_build], [initial_create_without_revision, later_build]):
        evidence, first_builds = _artifact_evidence(task, home, calls, trial=None)
        audit = evaluate_task(task, evidence)

        assert evidence["initial"]["brep_path"] is None
        assert evidence["edit_1"]["brep_path"] == str(brep_path.resolve())
        assert first_builds[0]["attempted"] is False
        assert first_builds[1]["attempted"] is True
        assert audit.stages[0].verdict == "bad"
        assert audit.stages[1].verdict == "indeterminate"


@pytest.mark.parametrize(("task_id", "rotation_degrees"), [
    ("part_spoked_wheel_001", 45),
    ("part_lobe_wheel_001", 22.5),
])
def test_rotational_pattern_accepts_unspecified_phase(tmp_path, task_id, rotation_degrees) -> None:
    pytest.importorskip("build123d")
    from build123d import Rot, export_brep

    from evals.benchmark.corpus.references import build_reference

    corpus, _ = load_selection()
    task = next(task for task in corpus["tasks"] if task["id"] == task_id)
    stage = task["stages"][0]
    rotated_shape = Rot(Z=rotation_degrees) * build_reference(task, stage["id"])
    rotated_path = tmp_path / f"{task_id}-phase-{rotation_degrees}.brep"
    assert export_brep(rotated_shape, str(rotated_path))

    audit = evaluate_stage(task_id, stage, brep_path=rotated_path)
    assert audit.verdict == "indeterminate"
    assert all(check.status == "pass" for check in audit.checks), [
        (check.check_id, check.status, check.reason, check.actual)
        for check in audit.checks
    ]


def test_spoked_wheel_oracles_accept_unspecified_xy_translation(tmp_path) -> None:
    pytest.importorskip("build123d")
    from build123d import Pos, export_brep

    from evals.benchmark.corpus.references import build_reference

    corpus, _ = load_selection()
    task = next(task for task in corpus["tasks"] if task["id"] == "part_spoked_wheel_001")
    stage = task["stages"][0]
    assert "origin" not in stage["request"].lower()
    translated_path = tmp_path / "spoked-wheel-at-8-7.brep"
    translated_shape = Pos(8, 7, 0) * build_reference(task, stage["id"])
    assert export_brep(translated_shape, str(translated_path))

    audit = evaluate_stage(task["id"], stage, brep_path=translated_path)

    assert audit.verdict == "indeterminate"
    assert all(check.status == "pass" for check in audit.checks), [
        (check.check_id, check.status, check.reason, check.actual)
        for check in audit.checks
    ]


def test_heat_sink_linear_pattern_accepts_unfixed_axis_and_rejects_wrong_count(tmp_path) -> None:
    pytest.importorskip("build123d")
    from build123d import Align, Box, Pos, export_brep

    corpus, _ = load_selection()
    task = next(task for task in corpus["tasks"] if task["id"] == "edit_heat_sink_add_fins_017")

    def heat_sink_with_fins(count: int):
        shape = Box(80, 50, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
        pitch = 6.0
        for index in range(count):
            y = (index - (count - 1) / 2) * pitch
            shape += Pos(0, y, 6) * Box(
                80, 2, 24, align=(Align.CENTER, Align.CENTER, Align.MIN)
            )
        return shape

    for stage in task["stages"]:
        pattern = next(check for check in stage["oracle"]["checks"] if check["type"] == "linear_pattern")
        expected_count = len(pattern["feature_centers_mm"])
        valid_path = tmp_path / f"{stage['id']}-alternate-fin-axis.brep"
        assert export_brep(heat_sink_with_fins(expected_count), str(valid_path))
        valid_audit = evaluate_stage(task["id"], stage, brep_path=valid_path)
        assert all(check.status == "pass" for check in valid_audit.checks), [
            (check.check_id, check.status, check.reason, check.actual)
            for check in valid_audit.checks
        ]

        wrong_path = tmp_path / f"{stage['id']}-wrong-fin-count.brep"
        assert export_brep(heat_sink_with_fins(expected_count - 1), str(wrong_path))
        wrong_audit = evaluate_stage(task["id"], stage, brep_path=wrong_path)
        pattern_result = next(check for check in wrong_audit.checks if check.check_type == "linear_pattern")
        assert pattern_result.status == "fail"


def test_hex_flange_profile_accepts_unspecified_phase_and_rejects_wrong_size(tmp_path) -> None:
    import math

    corpus, _ = load_selection()
    task = next(task for task in corpus["tasks"] if task["id"] == "profile_hex_flange_009")
    stage = task["stages"][0]
    reference = task["reference"]["stages"][stage["id"]]

    def save_sheet(path: Path, *, rotation_degrees: float, polygon_scale: float = 1.0) -> None:
        angle = math.radians(rotation_degrees)
        cosine, sine = math.cos(angle), math.sin(angle)
        points: dict[str, dict[str, float]] = {}
        entities: dict[str, dict[str, object]] = {}

        def transformed(point, scale=1.0):
            x, y = float(point[0]) * scale, float(point[1]) * scale
            return (x * cosine - y * sine, x * sine + y * cosine)

        for contour_index, contour in enumerate(reference["contours"]):
            point_names = []
            for point_index, point in enumerate(contour):
                x, y = transformed(point, polygon_scale if contour_index == 0 else 1.0)
                name = f"contour-{contour_index}-{point_index}"
                points[name] = {"x": x, "y": y}
                point_names.append(name)
            entities[f"polyline-{contour_index}"] = {
                "kind": "polyline",
                "points": point_names,
                "closed": True,
                "layer": "CUT",
            }
        for circle_index, circle in enumerate(reference["circles"]):
            x, y = transformed(circle["center_mm"])
            name = f"circle-center-{circle_index}"
            points[name] = {"x": x, "y": y}
            entities[f"circle-{circle_index}"] = {
                "kind": "circle",
                "points": [name],
                "radius": float(circle["diameter_mm"]) / 2,
                "layer": "CUT",
            }
        path.write_text(
            json.dumps({
                "units": "mm",
                "points": points,
                "entities": entities,
                "layers": {"CUT": {"role": "cut"}},
            }),
            encoding="utf-8",
        )

    rotated_path = tmp_path / "phase-rotated-hex-flange.json"
    save_sheet(rotated_path, rotation_degrees=17.0)
    rotated_audit = evaluate_stage(task["id"], stage, sheet_json_path=rotated_path)
    assert rotated_audit.checks[0].status == "pass"

    wrong_path = tmp_path / "undersized-hex-flange.json"
    save_sheet(wrong_path, rotation_degrees=17.0, polygon_scale=0.8)
    wrong_audit = evaluate_stage(task["id"], stage, sheet_json_path=wrong_path)
    assert wrong_audit.checks[0].status == "fail"


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
    assert metrics["request_check_witnesses"]["expected_declared_checks"] == 94
    assert metrics["request_check_witnesses"]["unsupported"] == 94
    assert metrics["first_pass_build_validity"]["expected_artifact_stage_denominator"] == 21
    assert metrics["first_pass_build_validity"]["uncertainty_95pct"]["expected_cells"] == 17
    assert metrics["clarification_infeasible_disposition"]["uncertainty_95pct"]["expected_cells"] == 3
    assert metrics["false_verified"]["host_manifest_validated"] is False
    assert metrics["false_verified"]["inference"]["upper"] is None


def test_false_verified_metric_matches_claim_to_its_own_stage() -> None:
    record = {
        "origin": "real_agent",
        "trial": {
            "completed": True,
            "stage_results": [
                {"stage_id": "initial", "native_verified_claims": [{"status": "verified"}]},
                {"stage_id": "edit_1", "native_verified_claims": []},
            ],
        },
        "audit": {
            "verdict": "bad",
            "coverage": "bounded",
            "stages": [
                {"stage_id": "initial", "verdict": "good", "effective_coverage": "complete"},
                {"stage_id": "edit_1", "verdict": "bad", "effective_coverage": "bounded"},
            ],
        },
    }
    observation = _false_verified_task_observation(
        record,
        task_id="edit_plate_widen_001",
        arm_id="real/openai/model/aad-default-v1",
    )
    summary = false_verified_summary(
        [observation],
        selected=[("edit_plate_widen_001", 1)],
        arm_id=observation.arm_id,
        sampling_plan_sha256="a" * 64,
        sampling_assumptions="Stage claims are audited against their matching stage records.",
        host_manifest_validated=False,
    )

    assert summary["n_verified"] == 1
    assert summary["good"] == 1
    assert summary["bad"] == 0
    assert observation.verdict == "good"

    both_stages_claim = {
        **record,
        "trial": {
            **record["trial"],
            "stage_results": [
                {"stage_id": "initial", "native_verified_claims": [{"status": "verified"}]},
                {"stage_id": "edit_1", "native_verified_claims": [{"status": "verified"}]},
            ],
        },
    }
    bad_observation = _false_verified_task_observation(
        both_stages_claim,
        task_id="edit_plate_widen_001",
        arm_id="real/openai/model/aad-default-v1",
    )
    bad_summary = false_verified_summary(
        [bad_observation],
        selected=[("edit_plate_widen_001", 1)],
        arm_id=bad_observation.arm_id,
        sampling_plan_sha256="a" * 64,
        sampling_assumptions="Each claimed stage must pass its matching stage audit.",
        host_manifest_validated=False,
    )
    assert bad_summary["n_verified"] == 1
    assert bad_summary["bad"] == 1
    assert bad_summary["good"] == 0
