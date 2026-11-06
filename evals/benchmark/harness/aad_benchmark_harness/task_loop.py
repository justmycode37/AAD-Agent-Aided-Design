"""Task-level composition for multi-stage requests and edit-retention trials."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from .loop import run_agent_loop
from .providers import create_provider_adapter
from .security import safe_exception_message
from .types import (
    IsolationPreflight,
    MCPBridge,
    ProviderAdapter,
    PublicStageProjection,
    RunConfig,
    SpendConsent,
    TrialResult,
)


@dataclass(frozen=True)
class TaskTrialResult:
    task_id: str
    expected_stage_count: int
    stage_results: tuple[TrialResult, ...]
    completed: bool
    stop_reason: str
    wall_seconds: float

    @property
    def provider_total_tokens(self) -> int | None:
        if any(stage.provider_total_tokens is None for stage in self.stage_results):
            return None
        return sum(stage.provider_total_tokens or 0 for stage in self.stage_results)

    def as_dict(self) -> dict[str, Any]:
        results = [stage.as_dict() for stage in self.stage_results]
        return {
            "task_id": self.task_id,
            "expected_stage_count": self.expected_stage_count,
            "completed_stage_count": len(self.stage_results),
            "completed": self.completed,
            "stop_reason": self.stop_reason,
            "wall_seconds": self.wall_seconds,
            "model_calls_attempted": sum(stage.model_calls_attempted for stage in self.stage_results),
            "model_calls_with_usage": sum(stage.model_calls_with_usage for stage in self.stage_results),
            "provider_input_tokens_known": sum(stage.provider_input_tokens_known for stage in self.stage_results),
            "provider_output_tokens_known": sum(stage.provider_output_tokens_known for stage in self.stage_results),
            "provider_total_tokens": self.provider_total_tokens,
            "input_token_count_calls": sum(stage.input_token_count_calls for stage in self.stage_results),
            "tool_calls_attempted": sum(stage.tool_calls_attempted for stage in self.stage_results),
            "native_verified_claim_count": sum(len(stage.native_verified_claims) for stage in self.stage_results),
            "stage_results": results,
        }


async def run_agent_task_loop(
    config: RunConfig,
    stages: Sequence[PublicStageProjection],
    backend: MCPBridge,
    isolation_preflight: IsolationPreflight,
    *,
    provider: ProviderAdapter | None = None,
    spend_consent: SpendConsent | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> TaskTrialResult:
    """Run all public stages with one conversation and task-wide budget accounting.

    An intermediate ``benchmark_finish`` is returned to the provider as a
    synthetic tool result, then the next public request is appended. The
    provider sees no oracle data. A stage that stops without a valid finish
    ends the task chain; unrun stages remain in the denominator as missing.
    """
    if not stages:
        raise ValueError("a task needs at least one public stage")
    if any(not isinstance(stage, PublicStageProjection) for stage in stages):
        raise TypeError("stages must contain validated PublicStageProjection values")
    task_ids = {stage.task_id for stage in stages}
    if len(task_ids) != 1:
        raise ValueError("all public stages must belong to one task")
    task_id = stages[0].task_id
    if not isinstance(isolation_preflight, IsolationPreflight):
        raise TypeError("isolation_preflight must be a validated IsolationPreflight")

    started = monotonic()
    deadline = started + config.wall_deadline_seconds
    provider_adapter = provider
    owns_provider = provider is None
    results: list[TrialResult] = []
    stop_reason = "all_stages_finished"

    try:
        for index, stage in enumerate(stages):
            consumed_tokens = sum(
                result.provider_input_tokens_known + result.provider_output_tokens_known
                for result in results
            )
            calls_used = sum(result.model_calls_attempted for result in results)
            tools_used = sum(result.tool_calls_attempted for result in results)
            remaining_tokens = config.token_budget - consumed_tokens
            remaining_calls = config.model_call_limit - calls_used
            remaining_tools = config.tool_call_limit - tools_used
            remaining_seconds = deadline - monotonic()
            if remaining_tokens <= 0:
                stop_reason = "task_token_budget_exhausted_before_next_stage"
                break
            if remaining_calls <= 0:
                stop_reason = "task_model_call_limit_reached_before_next_stage"
                break
            if remaining_tools <= 0:
                stop_reason = "task_tool_call_limit_reached_before_next_stage"
                break
            if remaining_seconds <= 0:
                stop_reason = "task_wall_deadline_reached_before_next_stage"
                break

            trace_name = config.trace_path.name
            suffix = config.trace_path.suffix or ".jsonl"
            stem = trace_name[:-len(config.trace_path.suffix)] if config.trace_path.suffix else trace_name
            stage_trace = config.trace_path.with_name(f"{stem}-{index + 1}-{stage.stage_id}{suffix}")
            stage_config = replace(
                config,
                token_budget=remaining_tokens,
                model_call_limit=remaining_calls,
                tool_call_limit=remaining_tools,
                wall_deadline_seconds=remaining_seconds,
                trace_path=stage_trace,
            )

            # Validate preflight before adapter construction; the shared
            # factory separately requires explicit spend consent on every path.
            if provider_adapter is None and not isolation_preflight.failure_reasons(stage_config):
                provider_adapter = create_provider_adapter(
                    config.provider,
                    config.model,
                    spend_consent=spend_consent,
                )
            next_stage = stages[index + 1] if index + 1 < len(stages) else None
            set_stage = getattr(backend, "set_stage", None)
            if callable(set_stage):
                set_stage(stage)
            result = await run_agent_loop(
                stage_config,
                stage,
                backend,
                isolation_preflight,
                provider=provider_adapter,
                spend_consent=spend_consent,
                initialize_provider=index == 0,
                next_stage=next_stage,
                monotonic=monotonic,
            )
            results.append(result)
            if not result.completed or result.state != "finished":
                stop_reason = result.stop_reason
                break
            if next_stage is not None and result.provider_total_tokens is None:
                stop_reason = "task_provider_usage_unknown_before_next_stage"
                break
    except Exception as error:
        stop_reason = "task_harness_error:" + safe_exception_message(error)
        raise
    finally:
        if owns_provider and provider_adapter is not None:
            try:
                await provider_adapter.aclose()
            except Exception:
                pass

    completed = len(results) == len(stages) and all(
        result.completed and result.state == "finished" for result in results
    )
    return TaskTrialResult(
        task_id=task_id,
        expected_stage_count=len(stages),
        stage_results=tuple(results),
        completed=completed,
        stop_reason=stop_reason,
        wall_seconds=max(0.0, monotonic() - started),
    )


__all__ = ["TaskTrialResult", "run_agent_task_loop"]
