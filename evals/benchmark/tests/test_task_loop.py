from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Sequence

import pytest

from evals.benchmark.harness.aad_benchmark_harness.task_loop import run_agent_task_loop
from evals.benchmark.harness.aad_benchmark_harness.types import (
    InputTokenEstimate,
    IsolationPreflight,
    ProviderToolCall,
    ProviderTurn,
    PublicStageProjection,
    RunConfig,
    ToolSpec,
    UsageReceipt,
)


PUBLIC_TASK = {
    "id": "task-loop-contract",
    "title": "Two-stage mock request",
    "kind": "part",
    "stages": [
        {"id": "initial", "request": "Build a starting part.", "input_assets": []},
        {"id": "edit", "request": "Edit it and preserve the starting features.", "input_assets": []},
    ],
}


def make_config(tmp_path: Path, **changes: Any) -> RunConfig:
    value: dict[str, Any] = {
        "provider": "openai",
        "model": "offline-contract-control",
        "backend_name": "mock-cad",
        "backend_revision": "mock-revision",
        "backend_environment": "mock-environment",
        "token_budget": 12_000,
        "model_call_limit": 8,
        "tool_call_limit": 12,
        "wall_deadline_seconds": 30,
        "max_output_tokens_per_call": 256,
        "input_token_safety_margin_ratio": 0.1,
        "input_token_safety_margin_tokens": 64,
        "repetition": 1,
        "task_order_seed": 11,
        "ablation_arm": "task-loop-contract",
        "trace_path": str(tmp_path / "trace.jsonl"),
        "solver_write_roots": [str(tmp_path / "solver")],
        "provider_supports_image_input": False,
        "include_tool_images": False,
    }
    value.update(changes)
    return RunConfig.from_mapping(value)


def make_preflight() -> IsolationPreflight:
    return IsolationPreflight.from_mapping({
        "backend_revision": "mock-revision",
        "backend_environment": "mock-environment",
        "probe_results": {
            "private_read_sentinel_denied": True,
            "network_probe_denied": True,
            "write_outside_solver_root_denied": True,
            "provider_credentials_absent_from_solver": True,
        },
        "evidence_digest": "a" * 64,
        "passed": True,
    })


class MockBackend:
    origin = "live"

    def __init__(self) -> None:
        self.list_calls = 0

    async def list_tools(self) -> Sequence[ToolSpec]:
        self.list_calls += 1
        return (ToolSpec(
            "aad_build",
            "Mock build tool",
            {"type": "object", "properties": {}, "required": []},
        ),)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        raise AssertionError("finish-only controls must not invoke the CAD backend")


class ContinuingFakeProvider:
    family = "openai"
    origin = "mock"

    def __init__(self) -> None:
        self.model = "offline-contract-control"
        self.stage: PublicStageProjection | None = None
        self.advances: list[str] = []
        self.complete_calls = 0

    def initialize(self, stage, instructions, tools) -> None:
        self.stage = stage

    def advance_stage(self, stage: PublicStageProjection) -> None:
        self.stage = stage
        self.advances.append(stage.stage_id)

    async def count_prompt_tokens(self) -> InputTokenEstimate:
        return InputTokenEstimate(10, "scripted-contract-estimate", True, {"origin": "mock"})

    async def complete(self, max_output_tokens: int) -> ProviderTurn:
        self.complete_calls += 1
        call = ProviderToolCall(
            call_id=f"finish-{self.complete_calls}",
            name="benchmark_finish",
            arguments={
                "artifacts": [],
                "claims_requirements_met": False,
                "summary": "Scripted task-loop contract control.",
            },
        )
        usage = UsageReceipt(
            input_tokens=10,
            output_tokens=5,
            provider_reported_input_tokens=10,
            provider_reported_total_tokens=15,
            details={"origin": "mock"},
            provider_fields={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        return ProviderTurn(
            text="",
            tool_calls=(call,),
            raw_response={"origin": "mock", "call": self.complete_calls},
            usage=usage,
            usage_problem=None,
            stop_reason="tool_use",
        )

    def add_tool_results(self, turn, results) -> None:
        return None

    def tool_catalog_payload(self) -> Any:
        return [{"name": "aad_build"}]

    async def aclose(self) -> None:
        return None


def test_edit_chain_reuses_one_provider_conversation_and_shared_task_budget(tmp_path) -> None:
    stages = tuple(
        PublicStageProjection.from_task_projection(PUBLIC_TASK, stage["id"])
        for stage in PUBLIC_TASK["stages"]
    )
    provider = ContinuingFakeProvider()
    result = asyncio.run(run_agent_task_loop(
        make_config(tmp_path),
        stages,
        MockBackend(),
        make_preflight(),
        provider=provider,
    ))

    assert result.completed is True
    assert len(result.stage_results) == 2
    assert provider.complete_calls == 2
    assert provider.advances == ["edit"]
    assert result.provider_total_tokens == 30
    assert result.as_dict()["model_calls_attempted"] == 2
    assert (tmp_path / "trace-1-initial.jsonl").is_file()
    assert (tmp_path / "trace-2-edit.jsonl").is_file()


def test_edit_chain_stops_before_spending_past_shared_call_budget(tmp_path) -> None:
    stages = tuple(
        PublicStageProjection.from_task_projection(PUBLIC_TASK, stage["id"])
        for stage in PUBLIC_TASK["stages"]
    )
    provider = ContinuingFakeProvider()
    result = asyncio.run(run_agent_task_loop(
        make_config(tmp_path, model_call_limit=1),
        stages,
        MockBackend(),
        make_preflight(),
        provider=provider,
    ))

    assert result.completed is False
    assert len(result.stage_results) == 1
    assert result.stop_reason == "task_model_call_limit_reached_before_next_stage"
    assert provider.complete_calls == 1
    assert provider.advances == ["edit"]


def test_task_route_cannot_create_a_live_adapter_without_consent(tmp_path) -> None:
    stages = tuple(
        PublicStageProjection.from_task_projection(PUBLIC_TASK, stage["id"])
        for stage in PUBLIC_TASK["stages"]
    )
    with pytest.raises(PermissionError, match="explicit --confirm-spend"):
        asyncio.run(run_agent_task_loop(
            make_config(tmp_path),
            stages,
            MockBackend(),
            make_preflight(),
        ))


def test_task_route_rejects_injected_live_provider_without_consent_marker(tmp_path) -> None:
    stages = tuple(
        PublicStageProjection.from_task_projection(PUBLIC_TASK, stage["id"])
        for stage in PUBLIC_TASK["stages"]
    )
    provider = ContinuingFakeProvider()
    provider.origin = "live"
    assert not hasattr(provider, "requires_spend_consent")
    backend = MockBackend()

    result = asyncio.run(run_agent_task_loop(
        make_config(tmp_path),
        stages,
        backend,
        make_preflight(),
        provider=provider,
    ))

    assert result.completed is False
    assert len(result.stage_results) == 1
    assert result.stop_reason == "explicit_spend_consent_required"
    assert result.stage_results[0].stop_reason == "explicit_spend_consent_required"
    assert provider.complete_calls == 0
    assert backend.list_calls == 0
