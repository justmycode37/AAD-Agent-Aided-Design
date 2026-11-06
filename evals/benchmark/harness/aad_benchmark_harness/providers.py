from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .security import safe_observation, safe_tool_text, to_plain
from .types import (
    FinishReceipt,
    InputTokenEstimate,
    ProviderToolCall,
    ProviderTurn,
    PublicStageProjection,
    SpendConsent,
    ToolInvocationResult,
    ToolSpec,
    UsageReceipt,
)

FINISH_TOOL_NAME = "benchmark_finish"
FINISH_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "claims_requirements_met": {"type": "boolean"},
        "summary": {"type": "string"},
    },
    "required": ["artifacts", "claims_requirements_met", "summary"],
    "additionalProperties": False,
}


def stage_prompt(stage: PublicStageProjection) -> str:
    assets = json.dumps(
        [dict(asset) for asset in stage.input_assets],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return (
        f"Benchmark task {stage.task_id}: {stage.title}\n"
        f"Task kind: {stage.kind}; stage: {stage.stage_id}\n"
        f"Declared public assets staged for this task: {assets}\n\n"
        "Public user request (verbatim):\n"
        f"{stage.request}"
    )


def _get(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _nonnegative_int(value: Any, field_name: str, issues: list[str], *, required: bool) -> int | None:
    if value is None:
        if required:
            issues.append(f"missing_usage_field:{field_name}")
        return None
    if type(value) is not int or value < 0:
        issues.append(f"invalid_usage_field:{field_name}")
        return None
    return value


def _validate_detail_counts(
    details: Mapping[str, Any],
    input_tokens: int,
    output_tokens: int,
    issues: list[str],
) -> None:
    """Validate detail counts as subsets; never add breakdowns to totals."""
    input_roots = {"input_tokens_details", "input_token_details", "input_details"}
    output_roots = {"output_tokens_details", "output_token_details", "output_details"}

    def visit(value: Any, direction: str | None, path: str) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                normalized = str(key).casefold()
                next_direction = direction
                if normalized in input_roots:
                    next_direction = "input"
                elif normalized in output_roots:
                    next_direction = "output"
                elif "input" in normalized or "cache" in normalized:
                    next_direction = "input"
                elif "output" in normalized or "reasoning" in normalized or "thinking" in normalized:
                    next_direction = "output"
                if isinstance(nested, Mapping):
                    visit(nested, next_direction, f"{path}.{key}")
                elif isinstance(nested, (list, tuple)):
                    visit(nested, next_direction, f"{path}.{key}")
                elif "token" in normalized and nested is not None:
                    count = _nonnegative_int(nested, f"{path}.{key}", issues, required=False)
                    aggregate = input_tokens if next_direction == "input" else output_tokens if next_direction == "output" else None
                    if count is not None and aggregate is not None and count > aggregate:
                        issues.append(f"usage_detail_exceeds_aggregate:{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, nested in enumerate(value):
                visit(nested, direction, f"{path}[{index}]")

    visit(details, None, "usage")


def parse_openai_usage(usage_value: Any) -> UsageReceipt | None:
    raw = to_plain(usage_value)
    if not isinstance(raw, Mapping):
        return None
    issues: list[str] = []
    input_tokens = _nonnegative_int(raw.get("input_tokens"), "input_tokens", issues, required=True)
    output_tokens = _nonnegative_int(raw.get("output_tokens"), "output_tokens", issues, required=True)
    reported_total = _nonnegative_int(raw.get("total_tokens"), "total_tokens", issues, required=False)
    if input_tokens is None or output_tokens is None:
        return UsageReceipt(
            input_tokens=0,
            output_tokens=0,
            provider_reported_input_tokens=0,
            provider_reported_total_tokens=reported_total,
            details=dict(raw),
            validation_issues=tuple(issues),
            provider_fields=dict(raw),
        )
    expected_total = input_tokens + output_tokens
    if reported_total is not None and reported_total != expected_total:
        issues.append("provider_total_tokens_mismatch")
    details = {
        key: value
        for key, value in raw.items()
        if key not in {"input_tokens", "output_tokens", "total_tokens"}
    }
    _validate_detail_counts(details, input_tokens, output_tokens, issues)
    return UsageReceipt(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        provider_reported_input_tokens=input_tokens,
        provider_reported_total_tokens=reported_total,
        details=details,
        validation_issues=tuple(issues),
        provider_fields=dict(raw),
    )


def parse_anthropic_usage(usage_value: Any, *, cache_control_sent: bool = False) -> UsageReceipt | None:
    raw = to_plain(usage_value)
    if not isinstance(raw, Mapping):
        return None
    issues: list[str] = []
    uncached_input = _nonnegative_int(raw.get("input_tokens"), "input_tokens", issues, required=True)
    output_tokens = _nonnegative_int(raw.get("output_tokens"), "output_tokens", issues, required=True)
    cache_creation = _nonnegative_int(
        raw.get("cache_creation_input_tokens"),
        "cache_creation_input_tokens",
        issues,
        required=cache_control_sent,
    )
    cache_read = _nonnegative_int(
        raw.get("cache_read_input_tokens"),
        "cache_read_input_tokens",
        issues,
        required=cache_control_sent,
    )
    # This adapter never sends cache_control. With no cache breakpoint, optional
    # absent cache fields mean no cached input was used; any supplied values are
    # still included in the canonical input total.
    if cache_creation is None:
        cache_creation = 0
    if cache_read is None:
        cache_read = 0
    if uncached_input is None or output_tokens is None:
        return UsageReceipt(
            input_tokens=0,
            output_tokens=0,
            provider_reported_input_tokens=0,
            provider_reported_total_tokens=None,
            details=dict(raw),
            validation_issues=tuple(issues),
            provider_fields=dict(raw),
        )

    cache_creation_details = raw.get("cache_creation")
    if isinstance(cache_creation_details, Mapping):
        nested_values = [
            value
            for key, value in cache_creation_details.items()
            if "input_tokens" in str(key).casefold()
        ]
        if nested_values:
            parsed_nested = [
                _nonnegative_int(value, f"cache_creation.{i}", issues, required=True)
                for i, value in enumerate(nested_values)
            ]
            if all(value is not None for value in parsed_nested) and sum(parsed_nested) != cache_creation:
                issues.append("cache_creation_detail_sum_mismatch")

    total_input = uncached_input + cache_creation + cache_read
    details = {
        key: value
        for key, value in raw.items()
        if key not in {"input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"}
    }
    _validate_detail_counts(details, total_input, output_tokens, issues)
    return UsageReceipt(
        input_tokens=total_input,
        output_tokens=output_tokens,
        provider_reported_input_tokens=uncached_input,
        provider_reported_total_tokens=None,
        details={
            **details,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "cache_breakdown_absent_means_zero": (
                "cache_control was not sent" if not cache_control_sent else None
            ),
        },
        validation_issues=tuple(issues),
        provider_fields=dict(raw),
    )


def finish_receipt_from_arguments(value: Any) -> tuple[FinishReceipt | None, str | None]:
    if not isinstance(value, Mapping):
        return None, "finish_argument_error_proxy:arguments_not_object"
    if set(value) != {"artifacts", "claims_requirements_met", "summary"}:
        return None, "finish_argument_error_proxy:fields_mismatch"
    artifacts = value["artifacts"]
    claim = value["claims_requirements_met"]
    summary = value["summary"]
    if (
        not isinstance(artifacts, list)
        or any(not isinstance(item, str) for item in artifacts)
        or type(claim) is not bool
        or not isinstance(summary, str)
    ):
        return None, "finish_argument_error_proxy:field_type_mismatch"
    return FinishReceipt(
        tuple(safe_tool_text(item) for item in artifacts),
        claim,
        safe_tool_text(summary),
    ), None


def _finish_tool_spec() -> ToolSpec:
    return ToolSpec(
        name=FINISH_TOOL_NAME,
        description=(
            "Submit the model's claim for this stage and end the trial. This receipt is not evidence."
        ),
        input_schema=FINISH_TOOL_SCHEMA,
    )


def _provider_tool_specs(tools: Sequence[ToolSpec]) -> tuple[ToolSpec, ...]:
    return tuple(tools) + (_finish_tool_spec(),)


def _require_spend_consent(spend_consent: SpendConsent | None) -> None:
    """Guard every path that creates a provider adapter, including direct use."""
    if not isinstance(spend_consent, SpendConsent) or not spend_consent.confirmed:
        raise PermissionError("provider adapter creation requires explicit --confirm-spend consent")


class OpenAIResponsesAdapter:
    family = "openai"
    origin = "custom"

    def __init__(self, client: Any, model: str, *, spend_consent: SpendConsent):
        _require_spend_consent(spend_consent)
        if not isinstance(model, str) or not model.strip():
            raise ValueError("OpenAI Responses model name must be explicit")
        self.client = client
        self.model = model
        self._instructions = ""
        self._input: list[Any] = []
        self._tools: list[dict[str, Any]] = []
        self._specs: tuple[ToolSpec, ...] = ()

    def initialize(
        self, stage: PublicStageProjection, instructions: str, tools: Sequence[ToolSpec]
    ) -> None:
        self._instructions = instructions
        self._input = [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": stage_prompt(stage)}],
            }
        ]
        self._specs = _provider_tool_specs(tools)
        self._tools = [
            {
                "type": "function",
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_schema,
            }
            for spec in self._specs
        ]

    def advance_stage(self, stage: PublicStageProjection) -> None:
        """Append the next stage request while retaining prior task context."""
        self._input.append({
            "role": "user",
            "content": [{"type": "input_text", "text": stage_prompt(stage)}],
        })

    def tool_catalog_payload(self) -> Any:
        return self._tools

    async def count_prompt_tokens(self) -> InputTokenEstimate:
        response = await self.client.responses.input_tokens.count(
            model=self.model,
            instructions=self._instructions,
            input=self._input,
            tools=self._tools,
            tool_choice="auto",
            parallel_tool_calls=True,
        )
        raw = to_plain(response)
        count = _get(response, "input_tokens")
        if type(count) is not int or count < 0:
            return InputTokenEstimate(None, "openai.responses.input_tokens.count", True, raw, "invalid_or_missing_input_tokens")
        return InputTokenEstimate(
            count,
            "openai.responses.input_tokens.count",
            True,
            safe_observation(raw),
        )

    async def complete(self, max_output_tokens: int) -> ProviderTurn:
        response = await self.client.responses.create(
            model=self.model,
            instructions=self._instructions,
            input=self._input,
            tools=self._tools,
            tool_choice="auto",
            parallel_tool_calls=True,
            max_output_tokens=max_output_tokens,
            store=False,
        )
        output_items = _get(response, "output", default=()) or ()
        # Keep the provider-native response items in history: function-call and
        # message output items are valid input items for the next Responses turn.
        self._input.extend(output_items)
        raw = to_plain(response)
        usage = parse_openai_usage(_get(response, "usage"))
        usage_problem = None if usage is not None and usage.valid else "missing_or_invalid_provider_usage"
        calls: list[ProviderToolCall] = []
        text_parts: list[str] = []
        for item in output_items:
            item_type = _get(item, "type")
            if item_type == "function_call":
                raw_arguments = _get(item, "arguments")
                try:
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                except (TypeError, json.JSONDecodeError):
                    arguments = None
                calls.append(
                    ProviderToolCall(
                        call_id=str(_get(item, "call_id", default="")),
                        name=str(_get(item, "name", default="")),
                        arguments=arguments,
                        raw_arguments=raw_arguments if isinstance(raw_arguments, str) else None,
                    )
                )
            elif item_type == "message":
                for block in _get(item, "content", default=()) or ():
                    if _get(block, "type") == "output_text":
                        text_parts.append(str(_get(block, "text", default="")))
        output_text = _get(response, "output_text")
        if not text_parts and isinstance(output_text, str):
            text_parts.append(output_text)
        status = _get(response, "status")
        incomplete = _get(response, "incomplete_details")
        stop_reason = str(_get(incomplete, "reason", default=status)) if incomplete else (str(status) if status else None)
        return ProviderTurn(
            text="\n".join(part for part in text_parts if part),
            tool_calls=tuple(calls),
            raw_response=safe_observation(raw),
            usage=usage,
            usage_problem=usage_problem,
            stop_reason=stop_reason,
            history_items=output_items,
        )

    def add_tool_results(
        self, turn: ProviderTurn, results: Sequence[ToolInvocationResult]
    ) -> None:
        for result in results:
            blocks: list[dict[str, Any]] = []
            if result.observation.is_error:
                blocks.append(
                    {"type": "input_text", "text": "MCP tool returned an error (isError=true)."}
                )
            for block in result.observation.blocks:
                if block.type == "text":
                    blocks.append({"type": "input_text", "text": block.text or ""})
                elif block.type == "image" and block.mime_type and block.data_base64:
                    blocks.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:{block.mime_type};base64,{block.data_base64}",
                        }
                    )
            if result.observation.structured_content is not None:
                blocks.append(
                    {
                        "type": "input_text",
                        "text": "structuredContent: "
                        + json.dumps(
                            result.observation.structured_content,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                    }
                )
            if not blocks:
                blocks.append({"type": "input_text", "text": "(empty MCP tool result)"})
            self._input.append(
                {
                    "type": "function_call_output",
                    "call_id": result.call_id,
                    "output": blocks,
                }
            )

    async def aclose(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result


class AnthropicMessagesAdapter:
    family = "anthropic"
    origin = "custom"

    def __init__(self, client: Any, model: str, *, spend_consent: SpendConsent):
        _require_spend_consent(spend_consent)
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Anthropic Messages model name must be explicit")
        self.client = client
        self.model = model
        self._system = ""
        self._messages: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []
        self._specs: tuple[ToolSpec, ...] = ()

    def initialize(
        self, stage: PublicStageProjection, instructions: str, tools: Sequence[ToolSpec]
    ) -> None:
        self._system = instructions
        self._messages = [{"role": "user", "content": stage_prompt(stage)}]
        self._specs = _provider_tool_specs(tools)
        self._tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for spec in self._specs
        ]

    def advance_stage(self, stage: PublicStageProjection) -> None:
        """Append the next stage request after the preceding tool result."""
        prompt = {"type": "text", "text": stage_prompt(stage)}
        if self._messages and self._messages[-1].get("role") == "user" and isinstance(self._messages[-1].get("content"), list):
            self._messages[-1]["content"].append(prompt)
        else:
            self._messages.append({"role": "user", "content": [prompt]})

    def tool_catalog_payload(self) -> Any:
        return self._tools

    async def count_prompt_tokens(self) -> InputTokenEstimate:
        response = await self.client.messages.count_tokens(
            model=self.model,
            system=self._system,
            messages=self._messages,
            tools=self._tools,
            tool_choice={"type": "auto"},
        )
        raw = to_plain(response)
        count = _get(response, "input_tokens")
        if type(count) is not int or count < 0:
            return InputTokenEstimate(None, "anthropic.messages.count_tokens", False, raw, "invalid_or_missing_input_tokens")
        # Anthropic documents this endpoint as an estimate. The shared
        # configured margin is added by the loop before every generation call.
        return InputTokenEstimate(
            count,
            "anthropic.messages.count_tokens",
            False,
            safe_observation(raw),
        )

    async def complete(self, max_output_tokens: int) -> ProviderTurn:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_output_tokens,
            system=self._system,
            messages=self._messages,
            tools=self._tools,
            tool_choice={"type": "auto"},
        )
        content = _get(response, "content", default=()) or ()
        # Keep all returned blocks, including any thinking signatures, intact in
        # history so the SDK can send the exact assistant turn back on tool use.
        self._messages.append({"role": "assistant", "content": content})
        raw = to_plain(response)
        usage = parse_anthropic_usage(_get(response, "usage"), cache_control_sent=False)
        usage_problem = None if usage is not None and usage.valid else "missing_or_invalid_provider_usage"
        calls: list[ProviderToolCall] = []
        text_parts: list[str] = []
        for block in content:
            block_type = _get(block, "type")
            if block_type == "tool_use":
                calls.append(
                    ProviderToolCall(
                        call_id=str(_get(block, "id", default="")),
                        name=str(_get(block, "name", default="")),
                        arguments=_get(block, "input"),
                    )
                )
            elif block_type == "text":
                text_parts.append(str(_get(block, "text", default="")))
        return ProviderTurn(
            text="\n".join(part for part in text_parts if part),
            tool_calls=tuple(calls),
            raw_response=safe_observation(raw),
            usage=usage,
            usage_problem=usage_problem,
            stop_reason=str(_get(response, "stop_reason")) if _get(response, "stop_reason") else None,
            history_items=content,
        )

    def add_tool_results(
        self, turn: ProviderTurn, results: Sequence[ToolInvocationResult]
    ) -> None:
        content: list[dict[str, Any]] = []
        for result in results:
            result_content: list[dict[str, Any]] = []
            for block in result.observation.blocks:
                if block.type == "text":
                    result_content.append({"type": "text", "text": block.text or ""})
                elif block.type == "image" and block.mime_type and block.data_base64:
                    result_content.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.mime_type,
                                "data": block.data_base64,
                            },
                        }
                    )
            if result.observation.structured_content is not None:
                result_content.append(
                    {
                        "type": "text",
                        "text": "structuredContent: "
                        + json.dumps(
                            result.observation.structured_content,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                    }
                )
            if not result_content:
                result_content.append({"type": "text", "text": "(empty MCP tool result)"})
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": result.call_id,
                "content": result_content,
            }
            if result.observation.is_error:
                block["is_error"] = True
            content.append(block)
        if content:
            # Anthropic requires tool_result blocks first in this user message.
            self._messages.append({"role": "user", "content": content})

    async def aclose(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result


def create_provider_adapter(family: str, model: str, *, spend_consent: SpendConsent):
    """Create a live SDK adapter only after explicit spend consent was provided."""
    _require_spend_consent(spend_consent)
    if not model or not model.strip():
        raise ValueError("provider model name is required; no default model is selected")
    if family == "openai":
        from openai import AsyncOpenAI

        adapter = OpenAIResponsesAdapter(
            AsyncOpenAI(max_retries=0), model, spend_consent=spend_consent
        )
        adapter.origin = "live"
        return adapter
    if family == "anthropic":
        from anthropic import AsyncAnthropic

        adapter = AnthropicMessagesAdapter(
            AsyncAnthropic(max_retries=0), model, spend_consent=spend_consent
        )
        adapter.origin = "live"
        return adapter
    raise ValueError("provider must be explicitly set to openai or anthropic")
