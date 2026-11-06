"""Pinned SDK serialization over in-memory HTTP; no network or model calls."""

import asyncio
import json

import httpx2
import pytest

from evals.benchmark.harness.aad_benchmark_harness.providers import (
    AnthropicMessagesAdapter,
    OpenAIResponsesAdapter,
)
from evals.benchmark.harness.aad_benchmark_harness.types import (
    MCPObservation,
    ObservationBlock,
    PublicStageProjection,
    SpendConsent,
    ToolInvocationResult,
    ToolSpec,
)


@pytest.mark.parametrize("family", ["openai", "anthropic"])
def test_official_sdk_serializes_multiturn_tool_image_and_usage(family):
    requests = []

    def respond(request):
        assert request.url.host == "provider.invalid"
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        if family == "openai":
            if request.url.path.endswith("input_tokens"):
                return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
            return httpx2.Response(200, json={
                "id": "mock-response", "object": "response", "created_at": 0,
                "model": "offline-mock-model", "status": "completed",
                "output": [{"type": "function_call", "id": "mock-function", "call_id": "mock-call", "name": "cad_build", "arguments": "{}", "status": "completed"}],
                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            })
        if request.url.path.endswith("count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 100})
        return httpx2.Response(200, json={
            "id": "mock-message", "type": "message", "role": "assistant",
            "model": "offline-mock-model", "stop_reason": "tool_use", "stop_sequence": None,
            "content": [{"type": "tool_use", "id": "mock-call", "name": "cad_build", "input": {}}],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        })

    async def exercise():
        test_consent = SpendConsent._from_cli_flag(True)
        transport = httpx2.MockTransport(respond)
        http_client = httpx2.AsyncClient(transport=transport)
        if family == "openai":
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key="offline-mock-only", base_url="https://provider.invalid/v1", http_client=http_client, max_retries=0)
            adapter = OpenAIResponsesAdapter(
                client, "offline-mock-model", spend_consent=test_consent
            )
        else:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key="offline-mock-only", base_url="https://provider.invalid", http_client=http_client, max_retries=0)
            adapter = AnthropicMessagesAdapter(
                client, "offline-mock-model", spend_consent=test_consent
            )
        adapter.origin = "mock"
        try:
            adapter.initialize(
                PublicStageProjection("mock-task", "Mock title", "part", "initial", "Make a mock part."),
                "Mock instructions", [ToolSpec("cad_build", "Build CAD", {"type": "object"})],
            )
            count = await adapter.count_prompt_tokens()
            turn = await adapter.complete(128)
            assert count.tokens == 100
            assert turn.usage.valid and turn.usage.total_tokens == 120
            assert turn.tool_calls[0].call_id == "mock-call"
            image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jS5kAAAAASUVORK5CYII="
            adapter.add_tool_results(turn, [ToolInvocationResult(
                "mock-call", "cad_build", MCPObservation(
                    False, (ObservationBlock("image", mime_type="image/png", data_base64=image),),
                    {"status": "draft"}, {"origin": "mock"}, "a" * 64,
                ),
            )])
            adapter.advance_stage(PublicStageProjection(
                "mock-task",
                "Mock title",
                "part",
                "edit",
                "Now edit the mock part and preserve its bores.",
            ))
            assert (await adapter.count_prompt_tokens()).tokens == 100
        finally:
            await adapter.aclose()

    asyncio.run(exercise())
    assert len(requests) == 3
    assert all(payload["model"] == "offline-mock-model" for _, payload in requests)
    assert all(any(tool.get("name") == "benchmark_finish" for tool in payload["tools"]) for _, payload in requests)
    last = requests[-1][1]
    if family == "openai":
        assert requests[1][1]["max_output_tokens"] == 128
        assert last["input"][-2]["type"] == "function_call_output"
        assert last["input"][-2]["output"][0]["type"] == "input_image"
        assert last["input"][-1]["role"] == "user"
        assert "preserve its bores" in last["input"][-1]["content"][0]["text"]
        assert requests[1][1]["store"] is False
    else:
        assert requests[1][1]["max_tokens"] == 128
        assert last["messages"][-1]["content"][0]["type"] == "tool_result"
        assert last["messages"][-1]["content"][0]["content"][0]["type"] == "image"
        assert last["messages"][-1]["content"][1]["type"] == "text"
        assert "preserve its bores" in last["messages"][-1]["content"][1]["text"]
