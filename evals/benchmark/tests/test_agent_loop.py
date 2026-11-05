"""Offline MCP/provider contract tests. Every trial below has mock origin."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import stat
import time
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import pytest

from evals.benchmark.harness.aad_benchmark_harness.artifacts import (
    UnsafeArtifactPath,
    stage_claimed_artifacts,
)
from evals.benchmark.harness.aad_benchmark_harness.loop import (
    filter_native_tools,
    normalize_mcp_observation,
    run_agent_loop,
)
from evals.benchmark.harness.aad_benchmark_harness.providers import (
    AnthropicMessagesAdapter,
    OpenAIResponsesAdapter,
    parse_anthropic_usage,
    parse_openai_usage,
)
from evals.benchmark.harness.aad_benchmark_harness.security import safe_observation, safe_tool_text
from evals.benchmark.harness.aad_benchmark_harness.trace import InMemoryTraceWriter, JsonlTraceWriter
from evals.benchmark.harness.aad_benchmark_harness.types import (
    InputTokenEstimate,
    IsolationPreflight,
    MCPObservation,
    ObservationBlock,
    ProviderToolCall,
    ProviderTurn,
    PublicStageProjection,
    RunConfig,
    ToolInvocationResult,
    ToolSpec,
    UsageReceipt,
)


PUBLIC_TASK = {
    "id": "mock-task-1",
    "title": "Public mock bracket",
    "kind": "part",
    "stages": [
        {
            "id": "stage-1",
            "request": "Make a mock bracket 40 mm wide.",
            "input_assets": [],
        }
    ],
}


def make_config(**changes: Any) -> RunConfig:
    value: dict[str, Any] = {
        "provider": "openai",
        "model": "offline-mock-model",
        "backend_name": "mock-cad",
        "backend_revision": "mock-revision",
        "backend_environment": "mock-environment-sha256",
        "token_budget": 12000,
        "model_call_limit": 4,
        "tool_call_limit": 8,
        "wall_deadline_seconds": 30,
        "max_output_tokens_per_call": 1024,
        "input_token_safety_margin_ratio": 0.1,
        "input_token_safety_margin_tokens": 64,
        "repetition": 1,
        "task_order_seed": 17,
        "ablation_arm": "offline-contract-test",
        "trace_path": "/mock-host-results/trace.jsonl",
        "solver_write_roots": ["/mock-solver-root"],
        "provider_supports_image_input": True,
        "include_tool_images": True,
    }
    value.update(changes)
    return RunConfig.from_mapping(value)


def make_stage() -> PublicStageProjection:
    return PublicStageProjection.from_task_projection(PUBLIC_TASK, "stage-1")


def make_preflight(**changes: Any) -> IsolationPreflight:
    value: dict[str, Any] = {
        "backend_revision": "mock-revision",
        "backend_environment": "mock-environment-sha256",
        "probe_results": {
            "private_read_sentinel_denied": True,
            "network_probe_denied": True,
            "write_outside_solver_root_denied": True,
            "provider_credentials_absent_from_solver": True,
        },
        "evidence_digest": "a" * 64,
        "passed": True,
    }
    value.update(changes)
    return IsolationPreflight.from_mapping(value)


def make_usage(input_tokens: int = 100, output_tokens: int = 20) -> UsageReceipt:
    return UsageReceipt(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        provider_reported_input_tokens=input_tokens,
        provider_reported_total_tokens=input_tokens + output_tokens,
        details={},
        provider_fields={"input_tokens": input_tokens, "output_tokens": output_tokens},
    )


def make_turn(*calls: ProviderToolCall, usage: UsageReceipt | None = None) -> ProviderTurn:
    return ProviderTurn(
        text="mock provider response",
        tool_calls=tuple(calls),
        raw_response={"origin": "mock", "content": "mock provider response"},
        usage=usage,
        usage_problem=None if usage else "mock_missing_usage",
        stop_reason="tool_use" if calls else "end_turn",
    )


class FakeProvider:
    origin = "mock"
    model = "offline-mock-model"

    def __init__(
        self,
        turns: Sequence[ProviderTurn],
        estimates: Sequence[int] = (100,),
        family: str = "openai",
    ):
        self.family = family
        self.turns = list(turns)
        self.estimates = list(estimates)
        self.count_calls = 0
        self.complete_calls = 0
        self.tool_results: list[Sequence[ToolInvocationResult]] = []
        self.exposed_tools: tuple[ToolSpec, ...] = ()

    def initialize(self, stage: PublicStageProjection, instructions: str, tools: Sequence[ToolSpec]) -> None:
        self.exposed_tools = tuple(tools)

    async def count_prompt_tokens(self) -> InputTokenEstimate:
        self.count_calls += 1
        if len(self.estimates) > 1:
            count = self.estimates.pop(0)
        else:
            count = self.estimates[0]
        return InputTokenEstimate(count, "mock.counter", False, {"origin": "mock"})

    async def complete(self, max_output_tokens: int) -> ProviderTurn:
        self.complete_calls += 1
        return self.turns.pop(0)

    def add_tool_results(self, turn: ProviderTurn, results: Sequence[ToolInvocationResult]) -> None:
        self.tool_results.append(tuple(results))

    def tool_catalog_payload(self) -> Any:
        return [{"name": tool.name, "input_schema": tool.input_schema} for tool in self.exposed_tools]

    async def aclose(self) -> None:
        return None


class FakeBackend:
    origin = "mock"

    def __init__(self, tools: Sequence[ToolSpec], result: Any = None):
        self.tools = tuple(tools)
        self.result = result or {
            "content": [{"type": "text", "text": "mock build result"}],
            "structuredContent": {"status": "draft"},
            "isError": False,
        }
        self.list_calls = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self):
        self.list_calls += 1
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        self.calls.append((name, arguments))
        return self.result


def safe_tool(name: str = "aad_build") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Build the requested CAD part.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )


def test_public_projection_rejects_hidden_oracle_fields() -> None:
    hidden = {**PUBLIC_TASK, "stages": [{**PUBLIC_TASK["stages"][0], "oracle": {"checks": []}}]}
    with pytest.raises(ValueError, match="private field"):
        PublicStageProjection.from_task_projection(hidden, "stage-1")


def test_public_projection_rejects_portable_path_traversal() -> None:
    task = {
        **PUBLIC_TASK,
        "stages": [
            {
                **PUBLIC_TASK["stages"][0],
                "input_assets": [{"name": "mock asset", "path": r"assets\..\private.step"}],
            }
        ],
    }
    with pytest.raises(ValueError, match="relative path"):
        PublicStageProjection.from_task_projection(task, "stage-1")


def test_run_loop_rechecks_mutable_public_asset_projection() -> None:
    stage = PublicStageProjection(
        task_id="mock-task-1",
        title="Public mock bracket",
        kind="part",
        stage_id="stage-1",
        request="Make a mock bracket.",
        input_assets=({"path": "assets/part.step"},),
    )
    stage.input_assets[0]["expected_outcome"] = {"volume_mm3": 1}
    with pytest.raises(ValueError, match="private field"):
        asyncio.run(
            run_agent_loop(
                make_config(),
                stage,
                FakeBackend([safe_tool()]),
                make_preflight(),
                provider=FakeProvider(()),
                trace_sink=InMemoryTraceWriter(),
            )
        )


def test_filter_removes_app_host_and_oracle_tools() -> None:
    tools = [
        safe_tool("aad_build"),
        safe_tool("aad_scene_read"),
        safe_tool("aad_fs_read"),
        safe_tool("aad_browser_open_url"),
        safe_tool("aad_fetch_url"),
        safe_tool("aad_exec_command"),
        safe_tool("aad_read_path"),
        safe_tool("shell"),
        safe_tool("benchmark_oracle"),
        safe_tool("read_file"),
        ToolSpec(
            name="aad_configure_export",
            description="Configure output options.",
            input_schema={
                "type": "object",
                "properties": {"apiKey": {"type": "string"}},
            },
        ),
        safe_tool("create_sketch"),
    ]
    filtered = filter_native_tools(tools, allowlist=("create_sketch",))
    assert [tool.name for tool in filtered] == ["aad_build", "create_sketch"]


def test_mock_observation_keeps_image_and_omits_scene_mesh() -> None:
    image = base64.b64encode(b"mock-png-data").decode("ascii")
    mesh = base64.b64encode(b"mesh-data-" * 600).decode("ascii")
    result = {
        "content": [
            {"type": "text", "text": "Rendered mock preview."},
            {"type": "image", "mimeType": "image/png", "data": image},
        ],
        "structuredContent": {"scene": {"scene_id": "scene-1", "data_b64": mesh}},
        "isError": False,
    }
    observation = normalize_mcp_observation(result, include_images=True)
    assert observation.blocks[1].type == "image"
    assert observation.blocks[1].data_base64 == image
    scene = observation.structured_content["scene"]
    assert scene["scene_id"] == "scene-1"
    assert scene["data_b64"]["omitted"] == "binary geometry payload"
    assert mesh not in json_text(observation.raw_safe)


def json_text(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True)


def test_auth_values_are_redacted_from_trace_values() -> None:
    safe = safe_observation(
        {
            "api_key": "sk-ant-very-secret-value-000000000000",
            "summary": "Authorization: Bearer abcdef1234567890",
        }
    )
    serialized = json_text(safe)
    assert "very-secret" not in serialized
    assert "abcdef1234567890" not in serialized
    assert "<redacted>" in serialized


def test_small_json_mesh_and_bearer_values_are_removed() -> None:
    text = safe_tool_text(
        '{"vertices":[[0,0,0],[1,0,0]],'
        '"note":"Authorization: Bearer private-value-123"}'
    )
    assert "[0,0,0]" not in text
    assert "private-value-123" not in text
    assert "omitted" in text


def test_host_trace_writer_is_private_exclusive_and_sanitized(tmp_path: Path) -> None:
    solver_root = tmp_path / "solver"
    solver_root.mkdir()
    trace_path = tmp_path / "host" / "trial.jsonl"
    writer = JsonlTraceWriter(trace_path, (solver_root,))
    writer.write_event(
        {
            "event": "mock_observation",
            "api_key": "mock-secret-do-not-log",
            "scene": {"vertices": [[0, 0, 0], [1, 0, 0]]},
        }
    )
    writer.close()
    serialized = trace_path.read_text(encoding="utf-8")
    assert "mock-secret-do-not-log" not in serialized
    assert '"sequence":0' in serialized
    assert stat.S_IMODE(trace_path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        JsonlTraceWriter(trace_path, (solver_root,))
    with pytest.raises(ValueError, match="outside"):
        JsonlTraceWriter(solver_root / "inside.jsonl", (solver_root,))


def test_provider_usage_subcounts_are_not_double_counted() -> None:
    openai_usage = parse_openai_usage(
        {
            "input_tokens": 100,
            "output_tokens": 40,
            "total_tokens": 140,
            "input_tokens_details": {"cached_tokens": 25, "image_tokens": 75},
            "output_tokens_details": {"reasoning_tokens": 12},
        }
    )
    assert openai_usage is not None and openai_usage.valid
    assert (openai_usage.input_tokens, openai_usage.output_tokens, openai_usage.total_tokens) == (100, 40, 140)

    anthropic_usage = parse_anthropic_usage(
        {
            "input_tokens": 30,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 20,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 30,
                "ephemeral_1h_input_tokens": 20,
            },
            "output_tokens": 40,
            "output_tokens_details": {"thinking_tokens": 12},
        }
    )
    assert anthropic_usage is not None and anthropic_usage.valid
    assert (anthropic_usage.input_tokens, anthropic_usage.output_tokens, anthropic_usage.total_tokens) == (100, 40, 140)


def test_invalid_provider_usage_detail_is_not_accepted() -> None:
    usage = parse_openai_usage(
        {
            "input_tokens": 8,
            "output_tokens": 4,
            "total_tokens": 12,
            "input_tokens_details": {"cached_tokens": 9},
        }
    )
    assert usage is not None
    assert not usage.valid
    assert "usage_detail_exceeds_aggregate:usage.input_tokens_details.cached_tokens" in usage.validation_issues


def test_preflight_failure_prevents_provider_and_backend_calls() -> None:
    provider = FakeProvider(())
    backend = FakeBackend([safe_tool()])
    trace = InMemoryTraceWriter()
    preflight = make_preflight(passed=False)
    result = asyncio.run(
        run_agent_loop(
            make_config(),
            make_stage(),
            backend,
            preflight,
            provider=provider,
            trace_sink=trace,
        )
    )
    assert result.state == "ineligible"
    assert provider.count_calls == 0
    assert provider.complete_calls == 0
    assert backend.list_calls == 0
    assert result.trial_origin == "mock"
    assert result.backend_origin == "mock"
    assert not result.sample_eligible


def test_image_capability_is_checked_before_provider_calls() -> None:
    provider = FakeProvider(())
    backend = FakeBackend([safe_tool()])
    result = asyncio.run(
        run_agent_loop(
            make_config(provider_supports_image_input=False, include_tool_images=True),
            make_stage(),
            backend,
            make_preflight(),
            provider=provider,
            trace_sink=InMemoryTraceWriter(),
        )
    )
    assert result.stop_reason == "provider_image_input_capability_not_declared"
    assert provider.count_calls == 0
    assert provider.complete_calls == 0


def test_budget_reserves_prompt_margin_and_full_output_before_generation() -> None:
    provider = FakeProvider((), estimates=(100,))
    backend = FakeBackend([safe_tool()])
    result = asyncio.run(
        run_agent_loop(
            make_config(token_budget=1190, max_output_tokens_per_call=1024),
            make_stage(),
            backend,
            make_preflight(),
            provider=provider,
            trace_sink=InMemoryTraceWriter(),
        )
    )
    # 100 input + 10% + 64 margin + the 1024 output cap exceeds 1190.
    assert result.state == "budget_exhausted"
    assert provider.count_calls == 1
    assert provider.complete_calls == 0
    assert result.model_calls_attempted == 0


@pytest.mark.parametrize("family", ["openai", "anthropic"])
def test_cumulative_budget_reservation_is_shared_by_provider_families(family: str) -> None:
    provider = FakeProvider(
        [
            make_turn(
                ProviderToolCall("call-1", "aad_build", {"source": "mock"}),
                usage=make_usage(600, 150),
            ),
            make_turn(usage=make_usage(300, 50)),
        ],
        estimates=(600, 300),
        family=family,
    )
    backend = FakeBackend([safe_tool()])
    result = asyncio.run(
        run_agent_loop(
            make_config(
                provider=family,
                token_budget=1200,
                max_output_tokens_per_call=200,
            ),
            make_stage(),
            backend,
            make_preflight(),
            provider=provider,
            trace_sink=InMemoryTraceWriter(),
        )
    )
    assert result.state == "budget_exhausted"
    assert result.model_calls_attempted == 1
    assert provider.complete_calls == 1
    assert result.tool_calls_attempted == 1


def test_missing_usage_stops_before_mcp_and_total_stays_unknown() -> None:
    provider = FakeProvider(
        [make_turn(ProviderToolCall("call-1", "aad_build", {}), usage=None)]
    )
    backend = FakeBackend([safe_tool()])
    result = asyncio.run(
        run_agent_loop(
            make_config(),
            make_stage(),
            backend,
            make_preflight(),
            provider=provider,
            trace_sink=InMemoryTraceWriter(),
        )
    )
    assert result.state == "ineligible"
    assert result.missing_usage_turns == (1,)
    assert result.provider_total_tokens is None
    assert result.tool_calls_attempted == 0
    assert backend.calls == []


def test_finish_claim_and_native_verified_claim_are_distinct_mock_records() -> None:
    provider = FakeProvider(
        [
            make_turn(ProviderToolCall("call-build", "aad_build", {"source": "mock"}), usage=make_usage()),
            make_turn(
                ProviderToolCall(
                    "call-finish",
                    "benchmark_finish",
                    {
                        "artifacts": ["exports/mock.step"],
                        "claims_requirements_met": True,
                        "summary": "Mock model claim.",
                    },
                ),
                usage=make_usage(120, 30),
            ),
        ],
        estimates=(100, 140),
    )
    backend = FakeBackend(
        [safe_tool()],
        result={
            "content": [{"type": "text", "text": "Mock server says verified."}],
            "structuredContent": {"status": "verified", "project": "p1", "rev": 7},
            "isError": False,
        },
    )
    trace = InMemoryTraceWriter()
    result = asyncio.run(
        run_agent_loop(
            make_config(),
            make_stage(),
            backend,
            make_preflight(),
            provider=provider,
            trace_sink=trace,
        )
    )
    assert result.state == "finished"
    assert result.completed
    assert result.model_claims is not None
    assert result.model_claims.claims_requirements_met is True
    assert result.native_verified_claims[0].status == "verified"
    assert result.native_verified_claims[0].revision == 7
    assert result.trial_origin == "mock"
    assert result.backend_origin == "mock"
    assert not result.sample_eligible
    assert backend.calls == [("aad_build", {"source": "mock"})]
    events = [event["event"] for event in trace.events]
    assert "model_finish_claim" in events
    assert "native_server_verified_claim" in events


def test_openai_responses_adapter_uses_offline_sdk_surface_and_image_outputs() -> None:
    class Responses:
        def __init__(self):
            self.count_args = None
            self.create_args = None
            self.input_tokens = self

        async def count(self, **kwargs):
            self.count_args = kwargs
            return SimpleNamespace(input_tokens=80)

        async def create(self, **kwargs):
            self.create_args = kwargs
            return SimpleNamespace(
                output=[
                    {
                        "type": "function_call",
                        "call_id": "openai-call-1",
                        "name": "aad_render",
                        "arguments": "{}",
                    }
                ],
                output_text="",
                status="completed",
                incomplete_details=None,
                usage={
                    "input_tokens": 90,
                    "output_tokens": 20,
                    "total_tokens": 110,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            )

    class Client:
        def __init__(self):
            self.responses = Responses()

    client = Client()
    adapter = OpenAIResponsesAdapter(client, "offline-mock-model")
    adapter.origin = "mock"
    adapter.initialize(make_stage(), "mock instructions", [safe_tool("aad_render")])
    estimate = asyncio.run(adapter.count_prompt_tokens())
    turn = asyncio.run(adapter.complete(1024))
    image = base64.b64encode(b"mock-png").decode("ascii")
    result = ToolInvocationResult(
        call_id="openai-call-1",
        name="aad_render",
        observation=MCPObservation(
            is_error=False,
            blocks=(ObservationBlock(type="image", mime_type="image/png", data_base64=image),),
            structured_content=None,
            raw_safe={"origin": "mock"},
            raw_digest="mock-digest",
        ),
    )
    adapter.add_tool_results(turn, [result])
    assert estimate.tokens == 80
    assert turn.usage is not None and turn.usage.total_tokens == 110
    assert client.responses.count_args["tools"]
    assert client.responses.create_args["store"] is False
    output = adapter._input[-1]
    assert output["type"] == "function_call_output"
    assert output["output"][0]["type"] == "input_image"
    assert output["output"][0]["image_url"].startswith("data:image/png;base64,")


def test_anthropic_messages_adapter_preserves_tool_result_order_and_images() -> None:
    class Messages:
        def __init__(self):
            self.count_args = None
            self.create_args = None

        async def count_tokens(self, **kwargs):
            self.count_args = kwargs
            return SimpleNamespace(input_tokens=80)

        async def create(self, **kwargs):
            self.create_args = kwargs
            return SimpleNamespace(
                content=[
                    {"type": "text", "text": "Rendering."},
                    {"type": "tool_use", "id": "anthropic-call-1", "name": "aad_render", "input": {}},
                ],
                stop_reason="tool_use",
                usage={
                    "input_tokens": 90,
                    "output_tokens": 20,
                    "output_tokens_details": {"thinking_tokens": 8},
                },
            )

    class Client:
        def __init__(self):
            self.messages = Messages()

    client = Client()
    adapter = AnthropicMessagesAdapter(client, "offline-mock-model")
    adapter.origin = "mock"
    adapter.initialize(make_stage(), "mock instructions", [safe_tool("aad_render")])
    estimate = asyncio.run(adapter.count_prompt_tokens())
    turn = asyncio.run(adapter.complete(1024))
    image = base64.b64encode(b"mock-png").decode("ascii")
    adapter.add_tool_results(
        turn,
        [
            ToolInvocationResult(
                call_id="anthropic-call-1",
                name="aad_render",
                observation=MCPObservation(
                    is_error=True,
                    blocks=(ObservationBlock(type="image", mime_type="image/png", data_base64=image),),
                    structured_content=None,
                    raw_safe={"origin": "mock"},
                    raw_digest="mock-digest",
                ),
            )
        ],
    )
    assert estimate.tokens == 80
    assert turn.usage is not None
    assert turn.usage.input_tokens == 90
    assert turn.usage.output_tokens == 20
    assert client.messages.count_args["tools"]
    assistant_message = adapter._messages[-2]
    result_message = adapter._messages[-1]
    assert assistant_message["role"] == "assistant"
    assert result_message["role"] == "user"
    tool_result = result_message["content"][0]
    assert tool_result["is_error"] is True
    assert tool_result["content"][0]["type"] == "image"
    assert tool_result["content"][0]["source"]["media_type"] == "image/png"


def test_artifact_stager_copies_only_safe_solver_relative_files(tmp_path: Path) -> None:
    solver_root = tmp_path / "solver"
    host_root = tmp_path / "host-artifacts"
    (solver_root / "exports").mkdir(parents=True)
    source = solver_root / "exports" / "part.step"
    source.write_bytes(b"mock artifact bytes")
    staged = stage_claimed_artifacts(
        ["exports/part.step"],
        solver_artifact_root=solver_root,
        host_artifact_dir=host_root,
        solver_write_roots=(solver_root,),
    )
    assert len(staged) == 1
    assert Path(staged[0].host_path).read_bytes() == b"mock artifact bytes"
    assert not Path(staged[0].host_path).is_relative_to(solver_root)

    with pytest.raises(UnsafeArtifactPath, match="traversing"):
        stage_claimed_artifacts(
            ["../outside.step"],
            solver_artifact_root=solver_root,
            host_artifact_dir=tmp_path / "host-artifacts-2",
            solver_write_roots=(solver_root,),
        )


def test_measurement_vectors_and_requested_image_evidence_survive_trace_sanitizing():
    image = base64.b64encode(b"mock-image-data" * 500).decode("ascii")
    payload = {
        "content": [{"type": "image", "mimeType": "image/png", "data": image}],
        "measurement": {"position": [1, 2, 3], "normal": [0, 0, 1]},
        "vertices": [[1, 2, 3]],
    }
    sanitized = safe_observation(payload)
    assert sanitized["content"][0]["data"] == image
    assert sanitized["measurement"] == payload["measurement"]
    assert sanitized["vertices"]["omitted"] == "large geometry array"


@pytest.mark.parametrize("link_kind", ["symlink", "directory_symlink", "hardlink"])
def test_artifact_stager_rejects_links(tmp_path: Path, link_kind: str):
    solver = tmp_path / "solver"
    solver.mkdir()
    private = tmp_path / "private.step"
    private.write_bytes(b"private sentinel")
    if link_kind == "symlink":
        (solver / "part.step").symlink_to(private)
        claim = "part.step"
    elif link_kind == "directory_symlink":
        (solver / "link").symlink_to(tmp_path, target_is_directory=True)
        claim = "link/private.step"
    else:
        os.link(private, solver / "part.step")
        claim = "part.step"
    with pytest.raises(UnsafeArtifactPath):
        stage_claimed_artifacts([claim], solver_artifact_root=solver,
                               host_artifact_dir=tmp_path / "host", solver_write_roots=(solver,))
    assert not list((tmp_path / "host").glob("*.step"))


def test_artifact_stager_rejects_leaf_swap_before_open(tmp_path: Path, monkeypatch):
    from evals.benchmark.harness.aad_benchmark_harness import artifacts

    solver = tmp_path / "solver"
    solver.mkdir()
    output = solver / "part.step"
    output.write_bytes(b"public output")
    private = tmp_path / "private.step"
    private.write_bytes(b"private sentinel")
    original_open = os.open

    def swap(path, flags, *args, **kwargs):
        if path == "part.step" and kwargs.get("dir_fd") is not None:
            output.unlink()
            output.symlink_to(private)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "open", swap)
    with pytest.raises(UnsafeArtifactPath):
        stage_claimed_artifacts(["part.step"], solver_artifact_root=solver,
                               host_artifact_dir=tmp_path / "host", solver_write_roots=(solver,))
    assert not list((tmp_path / "host").glob("*.step"))


def test_artifact_stager_bounds_bytes_and_requires_fresh_destination(tmp_path: Path):
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "part.step").write_bytes(b"12345")
    with pytest.raises(UnsafeArtifactPath, match="byte_limit"):
        stage_claimed_artifacts(["part.step"], solver_artifact_root=solver,
                               host_artifact_dir=tmp_path / "host", solver_write_roots=(solver,),
                               max_artifact_bytes=4)
    assert not list((tmp_path / "host").glob("*.step"))
    with pytest.raises(FileExistsError):
        stage_claimed_artifacts(["part.step"], solver_artifact_root=solver,
                               host_artifact_dir=tmp_path / "host", solver_write_roots=(solver,))


def test_late_blocking_finish_is_recorded_but_not_accepted():
    class BlockingProvider(FakeProvider):
        async def complete(self, max_output_tokens):
            time.sleep(0.04)
            return make_turn(ProviderToolCall("late", "benchmark_finish", {
                "artifacts": [], "claims_requirements_met": True, "summary": "mock late finish",
            }), usage=make_usage())

    result = asyncio.run(run_agent_loop(
        make_config(wall_deadline_seconds=0.01), make_stage(), FakeBackend([safe_tool()]),
        make_preflight(), provider=BlockingProvider([]), trace_sink=InMemoryTraceWriter(),
    ))
    assert result.state == "deadline_exhausted"
    assert result.completed is False and result.model_claims is None
    assert result.wall_seconds >= 0.04
    assert result.provider_total_tokens == 120


def test_live_labels_alone_never_authenticate_an_agent_sample():
    provider = FakeProvider([make_turn(ProviderToolCall("finish", "benchmark_finish", {
        "artifacts": [], "claims_requirements_met": False, "summary": "mock fixture",
    }), usage=make_usage())])
    backend, trace = FakeBackend([safe_tool()]), InMemoryTraceWriter()
    provider.origin = backend.origin = trace.origin = "live"
    result = asyncio.run(run_agent_loop(make_config(), make_stage(), backend, make_preflight(), provider=provider, trace_sink=trace))
    assert result.provisional_eligible is True
    assert result.sample_eligible is False
    assert "host_manifest_validation_required" in result.eligibility_reasons
    assert OpenAIResponsesAdapter(object(), "mock").origin == "custom"
    assert AnthropicMessagesAdapter(object(), "mock").origin == "custom"
