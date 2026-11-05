from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from .mcp_bridge import tool_spec_from_mcp
from .providers import (
    FINISH_TOOL_NAME,
    create_provider_adapter,
    finish_receipt_from_arguments,
)
from .security import safe_exception_message, safe_observation, safe_tool_text, sha256_json
from .trace import JsonlTraceWriter
from .types import (
    FinishReceipt,
    InputTokenEstimate,
    IsolationPreflight,
    MCPBridge,
    MCPObservation,
    NativeVerifiedClaim,
    ObservationBlock,
    ProviderAdapter,
    PublicStageProjection,
    RunConfig,
    SpendConsent,
    ToolInvocationResult,
    ToolSpec,
    TraceSink,
    TrialResult,
    TurnRecord,
)

_APP_ONLY_NAMES = {
    "aad_scene_read",
    "aad_set_param",
    "aad_pick_info",
    "aad_file_read",
}
_FORBIDDEN_TOOL_NAMES = {
    "aad_import",
    "bash",
    "shell",
    "terminal",
    "python",
    "browser",
    "web_search",
    "web_fetch",
    "read_file",
    "write_file",
    "list_directory",
    "execute_command",
    "run_command",
    "call_oracle",
    "oracle_check",
    "load_corpus",
    "read_corpus",
    "read_oracle",
}
_FORBIDDEN_NAME_FRAGMENTS = (
    "oracle",
    "hidden_check",
    "corpus_read",
    "corpus_lookup",
    "browser",
    "browse_web",
    "web_search",
    "web_fetch",
    "web_",
    "open_url",
    "fetch_url",
    "visit_url",
    "load_url",
    "navigate_url",
    "http_request",
    "http_",
    "network_request",
    "selenium",
    "playwright",
    "filesystem",
    "file_system",
    "host_filesystem",
    "host_fs",
    "fs_read",
    "fs_write",
    "read_fs",
    "write_fs",
    "file_read",
    "file_write",
    "read_path",
    "write_path",
    "path_read",
    "path_write",
    "path_list",
    "directory",
    "dir_list",
    "list_dir",
    "find_files",
    "create_file",
    "edit_file",
    "copy_file",
    "move_file",
    "remove_file",
    "exec_command",
    "command_exec",
    "run_cmd",
    "shell",
    "bash",
    "terminal",
    "execute_shell",
    "run_shell",
    "execute_command",
    "run_command",
    "read_file",
    "write_file",
    "delete_file",
    "open_file",
    "list_files",
    "list_directory",
    "list_dir",
    "dir_list",
    "provider_secret",
    "credential",
)
_FORBIDDEN_DESCRIPTION_PHRASES = (
    "hidden oracle",
    "oracle check",
    "hidden check",
    "read the benchmark corpus",
    "host filesystem",
    "host file system",
    "arbitrary local files",
    "reads arbitrary files",
    "writes arbitrary files",
    "runs shell commands",
    "run shell commands",
    "execute shell commands",
    "execute arbitrary commands",
    "api key",
    "access token",
    "authorization header",
    "password",
    "credential",
    "web browser",
    "browser automation",
    "web browsing",
    "open a url",
    "visit a url",
    "navigate to a website",
    "network access",
    "http request",
    "read files from the host",
    "write files to the host",
    "local filesystem",
    "local file system",
)
_CAD_PREFIXES = (
    "aad_",
    "cad_",
    "build123d_",
    "agentcad_",
    "geometry_",
    "sketch_",
    "drawing_",
    "part_",
    "solid_",
    "shape_",
)
_SUPPORTED_IMAGE_MIME = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}
_SENSITIVE_SCHEMA_KEY_PARTS = (
    "apikey",
    "authorization",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "clientsecret",
    "secret",
    "password",
    "credential",
    "bearer",
)
_SENSITIVE_SCHEMA_TEXT = (
    "api key",
    "access token",
    "authorization header",
    "bearer token",
    "password",
    "credential",
)


def is_app_only_tool(tool: ToolSpec) -> bool:
    if tool.name.casefold() in _APP_ONLY_NAMES:
        return True
    metadata = tool.metadata
    ui = metadata.get("ui") if isinstance(metadata, Mapping) else None
    visibility = ui.get("visibility") if isinstance(ui, Mapping) else None
    if isinstance(visibility, Sequence) and not isinstance(visibility, (str, bytes)):
        if any(isinstance(item, str) and item.casefold() == "app" for item in visibility):
            return True
    # Some wrappers flatten MCP metadata or retain the contract's warning in
    # the description. Both are treated as app-only signals.
    if isinstance(metadata, Mapping):
        flat_visibility = metadata.get("visibility")
        if (
            isinstance(flat_visibility, str) and flat_visibility.casefold() == "app"
        ) or (
            isinstance(flat_visibility, list)
            and any(isinstance(item, str) and item.casefold() == "app" for item in flat_visibility)
        ):
            return True
    return "internal viewer tool. do not call." in tool.description.casefold()


def is_forbidden_tool(tool: ToolSpec) -> bool:
    normalized = tool.name.casefold().replace("-", "_")
    if normalized in _FORBIDDEN_TOOL_NAMES or any(fragment in normalized for fragment in _FORBIDDEN_NAME_FRAGMENTS):
        return True
    description = tool.description.casefold()
    return any(phrase in description for phrase in _FORBIDDEN_DESCRIPTION_PHRASES)


def _schema_requests_credentials(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "", str(raw_key).casefold())
            if any(part in normalized for part in _SENSITIVE_SCHEMA_KEY_PARTS):
                return True
            if _schema_requests_credentials(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_schema_requests_credentials(nested) for nested in value)
    elif isinstance(value, str):
        normalized = value.casefold().replace("_", " ").replace("-", " ")
        return any(phrase in normalized for phrase in _SENSITIVE_SCHEMA_TEXT)
    return False


def filter_native_tools(
    tools: Sequence[ToolSpec], allowlist: Sequence[str] = ()
) -> tuple[ToolSpec, ...]:
    """Expose only backend CAD tools; always remove app-only and host tools."""
    allowed_names = set(allowlist)
    result: list[ToolSpec] = []
    seen: set[str] = set()
    for raw_tool in tools:
        tool = raw_tool if isinstance(raw_tool, ToolSpec) else tool_spec_from_mcp(raw_tool)
        if tool.name == FINISH_TOOL_NAME or tool.name in seen:
            continue
        if (
            is_app_only_tool(tool)
            or is_forbidden_tool(tool)
            or _schema_requests_credentials(tool.input_schema)
        ):
            continue
        normalized = tool.name.casefold()
        trusted_allowlisted = tool.name in allowed_names
        recognizable_cad_tool = normalized.startswith(_CAD_PREFIXES)
        if not trusted_allowlisted and not recognizable_cad_tool:
            continue
        safe_description = safe_tool_text(tool.description)
        safe_schema = safe_observation(tool.input_schema)
        if not isinstance(safe_schema, Mapping):
            continue
        result.append(
            ToolSpec(
                name=tool.name,
                description=safe_description,
                input_schema=dict(safe_schema),
            )
        )
        seen.add(tool.name)
    return tuple(result)


def _get(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _error_observation(message: str) -> MCPObservation:
    safe_message = safe_tool_text(message)
    raw = {"isError": True, "content": [{"type": "text", "text": safe_message}]}
    return MCPObservation(
        is_error=True,
        blocks=(ObservationBlock(type="text", text=safe_message),),
        structured_content=None,
        raw_safe=safe_observation(raw),
        raw_digest=sha256_json(raw),
    )


def normalize_mcp_observation(value: Any, *, include_images: bool) -> MCPObservation:
    plain = safe_observation(value)
    raw_digest = sha256_json(to_plain_for_hash(value))
    content = _get(value, "content", default=()) or ()
    structured = _get(value, "structuredContent", "structured_content", default=None)
    is_error = _get(value, "isError", "is_error", default=False) is True
    blocks: list[ObservationBlock] = []
    omitted_images = 0
    for block in content:
        kind = _get(block, "type")
        if kind == "text":
            text = _get(block, "text", default="")
            if isinstance(text, str):
                blocks.append(ObservationBlock(type="text", text=safe_tool_text(text)))
        elif kind == "image":
            mime = _get(block, "mimeType", "mime_type")
            data = _get(block, "data")
            if (
                include_images
                and isinstance(mime, str)
                and mime.casefold() in _SUPPORTED_IMAGE_MIME
                and isinstance(data, str)
                and _valid_base64_image(data)
            ):
                blocks.append(
                    ObservationBlock(type="image", mime_type=mime.casefold(), data_base64=data)
                )
            else:
                omitted_images += 1
                reason = "disabled by run config" if not include_images else "unsupported or invalid image content"
                blocks.append(
                    ObservationBlock(type="text", text=f"[MCP image omitted: {reason}; mesh data is never exposed]")
                )
    safe_structured = safe_observation(structured) if structured is not None else None
    return MCPObservation(
        is_error=is_error,
        blocks=tuple(blocks),
        structured_content=safe_structured,
        raw_safe=plain,
        raw_digest=raw_digest,
        image_content_omitted=omitted_images,
    )


def to_plain_for_hash(value: Any) -> Any:
    # Import locally to avoid widening the public API around SDK internals.
    from .security import to_plain

    return to_plain(value)


def _valid_base64_image(value: str) -> bool:
    if not value:
        return False
    try:
        import base64

        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return False
    return bool(decoded)


def _native_verified_claim(
    turn_number: int, call_id: str, tool_name: str, observation: MCPObservation
) -> NativeVerifiedClaim | None:
    if observation.is_error:
        return None
    structured = observation.structured_content
    if not isinstance(structured, Mapping) or structured.get("status") != "verified":
        return None
    project = structured.get("project")
    revision = structured.get("rev")
    return NativeVerifiedClaim(
        turn=turn_number,
        tool_call_id=call_id,
        tool_name=safe_tool_text(tool_name),
        status="verified",
        project=safe_tool_text(project) if isinstance(project, str) else None,
        revision=revision if type(revision) is int else None,
    )


def _reservation(estimate: InputTokenEstimate, config: RunConfig) -> int:
    if type(estimate.tokens) is not int or estimate.tokens < 0:
        raise ValueError("cannot reserve an unknown input-token count")
    return math.ceil(estimate.tokens * (1 + config.input_token_safety_margin_ratio)) + config.input_token_safety_margin_tokens


def _is_json_object(value: Any) -> bool:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        return False
    try:
        json.dumps(value, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _backend_origin(backend: Any) -> str:
    origin = getattr(backend, "origin", None)
    if isinstance(origin, str) and origin in {"live", "mock", "custom"}:
        return origin
    # Official MCP ClientSession instances do not expose a benchmark-origin
    # field; their SDK module path is a sufficient live marker here.
    if type(backend).__module__.startswith("mcp."):
        return "live"
    return "custom"


async def run_agent_loop(
    config: RunConfig,
    stage: PublicStageProjection,
    backend: MCPBridge,
    isolation_preflight: IsolationPreflight,
    *,
    provider: ProviderAdapter | None = None,
    spend_consent: SpendConsent | None = None,
    trace_sink: TraceSink | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    initialize_provider: bool = True,
    next_stage: PublicStageProjection | None = None,
) -> TrialResult:
    """Run one trial. No aggregate score or release claim is produced here."""
    if not isinstance(stage, PublicStageProjection):
        raise TypeError("stage must be a validated PublicStageProjection")
    if not initialize_provider and provider is None:
        raise ValueError("a continuing stage must reuse an existing provider adapter")
    if next_stage is not None and (
        not isinstance(next_stage, PublicStageProjection) or next_stage.task_id != stage.task_id
    ):
        raise ValueError("next_stage must be a validated stage from the same task")
    # Re-copy and revalidate nested asset dictionaries so a caller cannot
    # mutate a previously validated projection before or during a trial.
    stage = PublicStageProjection(
        task_id=stage.task_id,
        title=stage.title,
        kind=stage.kind,
        stage_id=stage.stage_id,
        request=stage.request,
        input_assets=stage.input_assets,
    )
    if not isinstance(isolation_preflight, IsolationPreflight):
        raise TypeError("isolation_preflight must be a validated IsolationPreflight")
    isolation_preflight = IsolationPreflight(
        backend_revision=isolation_preflight.backend_revision,
        backend_environment=isolation_preflight.backend_environment,
        probe_results=dict(isolation_preflight.probe_results),
        evidence_digest=isolation_preflight.evidence_digest,
        passed=isolation_preflight.passed,
    )
    owned_provider = provider is None
    provider_adapter = provider
    backend_origin = _backend_origin(backend)
    sink = (
        trace_sink
        if trace_sink is not None
        else JsonlTraceWriter(config.trace_path, config.solver_write_roots)
    )
    trace_path = str(sink.path) if sink.path else None
    started = monotonic()
    deadline = started + config.wall_deadline_seconds
    stage_hash = sha256_json(stage.as_dict())
    instruction_hash = sha256_json({"system_instructions": config.system_instructions})

    model_calls_attempted = 0
    model_calls_with_usage = 0
    input_count_calls = 0
    tool_calls_attempted = 0
    tool_name_proxies = 0
    tool_argument_proxies = 0
    known_input_tokens = 0
    known_output_tokens = 0
    missing_usage_turns: list[int] = []
    turn_records: list[TurnRecord] = []
    native_claims: list[NativeVerifiedClaim] = []
    finish_receipt: FinishReceipt | None = None
    catalog_hash: str | None = None
    state = "ineligible"
    stop_reason = "not_started"
    eligibility_reasons: list[str] = []
    completed = False
    trial_finished_event_written = False
    terminal_wall_seconds: float | None = None

    def result() -> TrialResult:
        nonlocal terminal_wall_seconds
        if terminal_wall_seconds is None:
            terminal_wall_seconds = max(0.0, monotonic() - started)
        origin = getattr(provider_adapter, "origin", "custom") if provider_adapter else "custom"
        if origin != "live":
            reasons = set(eligibility_reasons)
            reasons.add(f"provider_origin_{origin}")
        else:
            reasons = set(eligibility_reasons)
        if sink.origin != "live":
            reasons.add(f"trace_origin_{sink.origin}")
        if backend_origin != "live":
            reasons.add(f"backend_origin_{backend_origin}")
        if missing_usage_turns:
            reasons.add("provider_usage_missing_or_invalid")
        provisional_eligible = not reasons
        # Live labels are diagnostic declarations. Only the separate host
        # manifest validator can authenticate a sampled real-agent outcome.
        reasons.add("host_manifest_validation_required")
        total = None if missing_usage_turns else known_input_tokens + known_output_tokens
        return TrialResult(
            task_id=stage.task_id,
            stage_id=stage.stage_id,
            provider=config.provider,
            model=config.model,
            backend_name=config.backend_name,
            backend_revision=config.backend_revision,
            backend_environment=config.backend_environment,
            repetition=config.repetition,
            task_order_seed=config.task_order_seed,
            ablation_arm=config.ablation_arm,
            wall_seconds=terminal_wall_seconds,
            trial_origin=origin,
            backend_origin=backend_origin,
            state=state,
            sample_eligible=False,
            provisional_eligible=provisional_eligible,
            eligibility_reasons=tuple(sorted(reasons)),
            completed=completed,
            stop_reason=stop_reason,
            model_calls_attempted=model_calls_attempted,
            model_calls_with_usage=model_calls_with_usage,
            provider_input_tokens_known=known_input_tokens,
            provider_output_tokens_known=known_output_tokens,
            provider_total_tokens=total,
            missing_usage_turns=tuple(missing_usage_turns),
            input_token_count_calls=input_count_calls,
            tool_calls_attempted=tool_calls_attempted,
            tool_name_error_proxies=tool_name_proxies,
            tool_argument_error_proxies=tool_argument_proxies,
            turn_records=tuple(turn_records),
            model_claims=finish_receipt,
            native_verified_claims=tuple(native_claims),
            catalog_hash=catalog_hash,
            instruction_hash=instruction_hash,
            public_stage_hash=stage_hash,
            trace_path=trace_path,
        )

    def write_trial_finished() -> None:
        nonlocal trial_finished_event_written
        if trial_finished_event_written:
            return
        sink.write_event({"event": "trial_finished", "result": result().as_dict()})
        trial_finished_event_written = True

    try:
        sink.write_event(
            {
                "event": "trial_started",
                "trial_metadata": config.public_metadata(),
                "backend_origin": backend_origin,
                "public_stage": stage.as_dict(),
                "public_stage_hash": stage_hash,
                "instruction_hash": instruction_hash,
                "system_instructions": config.system_instructions,
                "isolation_preflight": isolation_preflight.as_dict(),
            }
        )
        eligibility_reasons.extend(isolation_preflight.failure_reasons(config))
        if eligibility_reasons:
            state = "ineligible"
            stop_reason = eligibility_reasons[0]
            return result()
        if (
            provider_adapter is not None
            and getattr(provider_adapter, "requires_spend_consent", False)
            and not isinstance(spend_consent, SpendConsent)
        ):
            state = "ineligible"
            stop_reason = "explicit_spend_consent_required"
            eligibility_reasons.append("explicit_spend_consent_required")
            return result()
        if config.include_tool_images and not config.provider_supports_image_input:
            state = "ineligible"
            stop_reason = "provider_image_input_capability_not_declared"
            eligibility_reasons.append("provider_image_input_capability_not_declared")
            return result()

        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                state = "deadline_exhausted"
                stop_reason = "wall_deadline_reached_before_backend_tool_catalog"
                return result()
            async with asyncio.timeout(remaining):
                listed = await backend.list_tools()
            raw_tools = _get(listed, "tools", default=listed)
            if not isinstance(raw_tools, Sequence) or isinstance(raw_tools, (str, bytes)):
                raise TypeError("MCP list_tools result is not a tool sequence")
            native_tools = filter_native_tools(raw_tools, config.native_tool_allowlist)
        except TimeoutError:
            state = "timed_out"
            stop_reason = "wall_deadline_during_backend_tool_catalog"
            eligibility_reasons.append("backend_tool_catalog_timeout")
            sink.write_event({"event": "backend_catalog_timeout"})
            return result()
        except Exception as error:
            state = "ineligible"
            stop_reason = "backend_tool_catalog_error"
            eligibility_reasons.append("backend_tool_catalog_unavailable")
            sink.write_event(
                {"event": "backend_catalog_error", "error": safe_exception_message(error)}
            )
            return result()
        if not native_tools:
            state = "ineligible"
            stop_reason = "no_safe_native_cad_tools"
            eligibility_reasons.append("no_safe_native_cad_tools")
            sink.write_event({"event": "backend_catalog_empty_after_safety_filter"})
            return result()

        try:
            if provider_adapter is None:
                # The factory is called only after the external isolation report has
                # matched this backend revision/environment and passed every probe.
                provider_adapter = create_provider_adapter(
                    config.provider,
                    config.model,
                    spend_consent=spend_consent,
                )
            if provider_adapter.family != config.provider or provider_adapter.model != config.model:
                state = "ineligible"
                stop_reason = "provider_configuration_mismatch"
                eligibility_reasons.append("provider_adapter_configuration_mismatch")
                return result()
            if initialize_provider:
                provider_adapter.initialize(stage, config.system_instructions, native_tools)
            elif not callable(getattr(provider_adapter, "advance_stage", None)):
                state = "ineligible"
                stop_reason = "provider_adapter_cannot_continue_task_stages"
                eligibility_reasons.append("provider_adapter_cannot_continue_task_stages")
                return result()
        except Exception as error:
            state = "provider_error"
            stop_reason = "provider_adapter_initialization_error"
            eligibility_reasons.append("provider_adapter_initialization_failed")
            sink.write_event(
                {"event": "provider_adapter_initialization_error", "error": safe_exception_message(error)}
            )
            return result()
        catalog_hash = sha256_json(provider_adapter.tool_catalog_payload())
        sink.write_event(
            {
                "event": "tool_catalog_ready",
                "native_tool_names": [tool.name for tool in native_tools],
                "catalog_hash": catalog_hash,
                "common_finish_tool": FINISH_TOOL_NAME,
            }
        )

        while True:
            next_turn = model_calls_attempted + 1
            remaining = deadline - monotonic()
            if remaining <= 0:
                state = "deadline_exhausted"
                stop_reason = "wall_deadline_reached_before_token_count"
                break
            if model_calls_attempted >= config.model_call_limit:
                state = "budget_exhausted"
                stop_reason = "model_call_limit_reached"
                break

            input_count_calls += 1
            try:
                async with asyncio.timeout(remaining):
                    estimate = await provider_adapter.count_prompt_tokens()
            except TimeoutError:
                state = "timed_out"
                stop_reason = "wall_deadline_during_token_count"
                eligibility_reasons.append("provider_input_token_count_timeout")
                sink.write_event(
                    {"event": "token_count_timeout", "turn": next_turn}
                )
                break
            except Exception as error:
                state = "provider_error"
                stop_reason = "provider_input_token_count_error"
                eligibility_reasons.append("provider_input_token_count_failed")
                sink.write_event(
                    {
                        "event": "token_count_error",
                        "turn": next_turn,
                        "error": safe_exception_message(error),
                    }
                )
                break
            sink.write_event(
                {
                    "event": "input_tokens_reserved",
                    "turn": next_turn,
                    "estimate": estimate,
                }
            )
            if estimate.tokens is None or type(estimate.tokens) is not int or estimate.tokens < 0:
                state = "ineligible"
                stop_reason = "provider_input_token_count_unknown"
                eligibility_reasons.append("provider_input_token_count_unknown")
                break

            input_reservation = _reservation(estimate, config)
            output_reservation = config.max_output_tokens_per_call
            needed = input_reservation + output_reservation
            used = known_input_tokens + known_output_tokens
            if used + needed > config.token_budget:
                state = "budget_exhausted"
                stop_reason = "token_budget_would_be_exceeded_by_next_call"
                sink.write_event(
                    {
                        "event": "budget_stop",
                        "turn": next_turn,
                        "known_tokens_used": used,
                        "input_reservation_tokens": input_reservation,
                        "output_reservation_tokens": output_reservation,
                        "token_budget": config.token_budget,
                    }
                )
                break

            remaining = deadline - monotonic()
            if remaining <= 0:
                state = "deadline_exhausted"
                stop_reason = "wall_deadline_reached_before_generation"
                break

            model_calls_attempted += 1
            try:
                async with asyncio.timeout(remaining):
                    turn = await provider_adapter.complete(config.max_output_tokens_per_call)
            except TimeoutError:
                missing_usage_turns.append(next_turn)
                eligibility_reasons.append("provider_generation_timeout_usage_unknown")
                turn_records.append(
                    TurnRecord(
                        turn=next_turn,
                        provider_call_attempted=True,
                        input_token_estimate=estimate.tokens,
                        input_token_estimate_method=estimate.method,
                        input_reservation_tokens=input_reservation,
                        output_reservation_tokens=output_reservation,
                        usage=None,
                        usage_problem="provider_generation_timeout_usage_unknown",
                        text="",
                        stop_reason=None,
                        tool_names=(),
                        raw_response_digest=None,
                    )
                )
                state = "timed_out"
                stop_reason = "wall_deadline_during_provider_generation"
                sink.write_event(
                    {"event": "provider_generation_timeout", "turn": next_turn}
                )
                break
            except Exception as error:
                missing_usage_turns.append(next_turn)
                eligibility_reasons.append("provider_generation_failed_usage_unknown")
                turn_records.append(
                    TurnRecord(
                        turn=next_turn,
                        provider_call_attempted=True,
                        input_token_estimate=estimate.tokens,
                        input_token_estimate_method=estimate.method,
                        input_reservation_tokens=input_reservation,
                        output_reservation_tokens=output_reservation,
                        usage=None,
                        usage_problem="provider_generation_failed_usage_unknown",
                        text="",
                        stop_reason=None,
                        tool_names=(),
                        raw_response_digest=None,
                    )
                )
                state = "provider_error"
                stop_reason = "provider_generation_error"
                sink.write_event(
                    {
                        "event": "provider_generation_error",
                        "turn": next_turn,
                        "error": safe_exception_message(error),
                    }
                )
                break

            raw_digest = sha256_json(turn.raw_response)
            valid_usage = turn.usage is not None and turn.usage.valid and turn.usage_problem is None
            turn_text = safe_tool_text(turn.text)
            turn_records.append(
                TurnRecord(
                    turn=next_turn,
                    provider_call_attempted=True,
                    input_token_estimate=estimate.tokens,
                    input_token_estimate_method=estimate.method,
                    input_reservation_tokens=input_reservation,
                    output_reservation_tokens=output_reservation,
                    usage=turn.usage if valid_usage else None,
                    usage_problem=None if valid_usage else turn.usage_problem or "missing_or_invalid_provider_usage",
                    text=turn_text,
                    stop_reason=turn.stop_reason,
                    tool_names=tuple(safe_tool_text(call.name) for call in turn.tool_calls),
                    raw_response_digest=raw_digest,
                )
            )
            sink.write_event(
                {
                    "event": "provider_turn",
                    "turn": next_turn,
                    "response": turn.raw_response,
                    "response_sha256": raw_digest,
                    "usage": turn.usage,
                    "usage_problem": turn.usage_problem,
                    "stop_reason": turn.stop_reason,
                    "tool_calls": [
                        {
                            "call_id": call.call_id,
                            "name": call.name,
                            "arguments": call.arguments,
                            "raw_arguments": call.raw_arguments,
                        }
                        for call in turn.tool_calls
                    ],
                }
            )
            if not valid_usage:
                missing_usage_turns.append(next_turn)
                eligibility_reasons.append("provider_usage_missing_or_invalid")
                state = "ineligible"
                stop_reason = "provider_usage_unknown"
                break

            assert turn.usage is not None
            model_calls_with_usage += 1
            known_input_tokens += turn.usage.input_tokens
            known_output_tokens += turn.usage.output_tokens
            if (
                turn.usage.input_tokens > input_reservation
                or turn.usage.output_tokens > output_reservation
                or known_input_tokens + known_output_tokens > config.token_budget
            ):
                eligibility_reasons.append("provider_usage_exceeded_pre_call_reservation")
                state = "ineligible"
                stop_reason = "provider_usage_exceeded_pre_call_reservation"
                break

            if monotonic() >= deadline:
                state = "deadline_exhausted"
                stop_reason = "provider_response_arrived_after_wall_deadline"
                break

            if not turn.tool_calls:
                state = "incomplete"
                stop_reason = "assistant_ended_without_benchmark_finish"
                if turn.stop_reason in {"max_output_tokens", "max_tokens", "length"}:
                    stop_reason = "provider_output_limit_without_benchmark_finish"
                break

            results: list[ToolInvocationResult] = []
            finished = False
            terminal = False
            for call in turn.tool_calls:
                if not call.call_id:
                    eligibility_reasons.append("provider_tool_call_missing_id")
                    state = "provider_error"
                    stop_reason = "provider_tool_call_missing_id"
                    terminal = True
                    break

                if call.name == FINISH_TOOL_NAME:
                    finish_receipt, finish_error = finish_receipt_from_arguments(call.arguments)
                    if finish_receipt is not None:
                        sink.write_event(
                            {
                                "event": "model_finish_claim",
                                "turn": next_turn,
                                "tool_call_id": call.call_id,
                                "receipt": finish_receipt.as_dict(),
                                "receipt_is_evidence": False,
                            }
                        )
                        state = "finished"
                        stop_reason = "benchmark_finish_called"
                        completed = True
                        finished = True
                        terminal = True
                        if next_stage is not None:
                            advance_stage = getattr(provider_adapter, "advance_stage", None)
                            if not callable(advance_stage):
                                state = "provider_error"
                                stop_reason = "provider_adapter_cannot_continue_task_stages"
                                eligibility_reasons.append("provider_adapter_cannot_continue_task_stages")
                                completed = False
                            else:
                                continuation = {
                                    "status": "stage_complete",
                                    "next_stage_id": next_stage.stage_id,
                                    "finish_receipt_is_evidence": False,
                                }
                                continuation_observation = MCPObservation(
                                    is_error=False,
                                    blocks=(ObservationBlock(type="text", text="Stage finished. Continue with the next public request."),),
                                    structured_content=continuation,
                                    raw_safe=continuation,
                                    raw_digest=sha256_json(continuation),
                                )
                                results.append(ToolInvocationResult(
                                    call_id=call.call_id,
                                    name=call.name,
                                    observation=continuation_observation,
                                ))
                                try:
                                    provider_adapter.add_tool_results(turn, results)
                                    advance_stage(next_stage)
                                    sink.write_event({
                                        "event": "task_stage_continuation_ready",
                                        "from_stage_id": stage.stage_id,
                                        "to_stage_id": next_stage.stage_id,
                                    })
                                except Exception as error:
                                    state = "provider_error"
                                    stop_reason = "provider_stage_continuation_failed"
                                    eligibility_reasons.append("provider_stage_continuation_failed")
                                    completed = False
                                    sink.write_event({
                                        "event": "task_stage_continuation_error",
                                        "error": safe_exception_message(error),
                                    })
                        break
                    tool_argument_proxies += 1
                    assert finish_error is not None
                    results.append(
                        ToolInvocationResult(
                            call_id=call.call_id,
                            name=call.name,
                            observation=_error_observation(
                                "benchmark_finish receipt rejected: " + finish_error
                            ),
                            proxy_label=finish_error,
                        )
                    )
                    sink.write_event(
                        {
                            "event": "tool_argument_error_proxy",
                            "turn": next_turn,
                            "tool_call_id": call.call_id,
                            "tool_name": call.name,
                            "proxy": finish_error,
                        }
                    )
                    continue

                if call.name not in {tool.name for tool in native_tools}:
                    tool_name_proxies += 1
                    proxy = "tool_name_error_proxy:tool_not_exposed"
                    results.append(
                        ToolInvocationResult(
                            call_id=call.call_id,
                            name=call.name,
                            observation=_error_observation("Tool is not available in this benchmark trial."),
                            proxy_label=proxy,
                        )
                    )
                    sink.write_event(
                        {
                            "event": "tool_name_error_proxy",
                            "turn": next_turn,
                            "tool_call_id": call.call_id,
                            "tool_name": call.name,
                            "proxy": proxy,
                        }
                    )
                    continue

                if not _is_json_object(call.arguments):
                    tool_argument_proxies += 1
                    proxy = "tool_argument_error_proxy:arguments_not_object"
                    results.append(
                        ToolInvocationResult(
                            call_id=call.call_id,
                            name=call.name,
                            observation=_error_observation("Tool arguments must be a JSON object."),
                            proxy_label=proxy,
                        )
                    )
                    sink.write_event(
                        {
                            "event": "tool_argument_error_proxy",
                            "turn": next_turn,
                            "tool_call_id": call.call_id,
                            "tool_name": call.name,
                            "proxy": proxy,
                        }
                    )
                    continue

                if tool_calls_attempted >= config.tool_call_limit:
                    state = "budget_exhausted"
                    stop_reason = "tool_call_limit_reached"
                    terminal = True
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    state = "deadline_exhausted"
                    stop_reason = "wall_deadline_reached_before_mcp_tool_call"
                    terminal = True
                    break

                tool_calls_attempted += 1
                try:
                    async with asyncio.timeout(remaining):
                        raw_result = await backend.call_tool(call.name, dict(call.arguments))
                except TimeoutError:
                    eligibility_reasons.append("mcp_tool_timeout_result_unknown")
                    state = "timed_out"
                    stop_reason = "wall_deadline_during_mcp_tool_call"
                    sink.write_event(
                        {
                            "event": "mcp_tool_timeout",
                            "turn": next_turn,
                            "tool_call_id": call.call_id,
                            "tool_name": call.name,
                        }
                    )
                    terminal = True
                    break
                except Exception as error:
                    observation = _error_observation(
                        "MCP tool call failed: " + safe_exception_message(error)
                    )
                    results.append(
                        ToolInvocationResult(call.call_id, call.name, observation)
                    )
                    sink.write_event(
                        {
                            "event": "mcp_tool_error",
                            "turn": next_turn,
                            "tool_call_id": call.call_id,
                            "tool_name": call.name,
                            "observation": observation.raw_safe,
                        }
                    )
                    continue

                observation = normalize_mcp_observation(
                    raw_result, include_images=config.include_tool_images
                )
                results.append(ToolInvocationResult(call.call_id, call.name, observation))
                native_claim = _native_verified_claim(
                    next_turn, call.call_id, call.name, observation
                )
                if native_claim is not None:
                    native_claims.append(native_claim)
                    sink.write_event(
                        {
                            "event": "native_server_verified_claim",
                            "turn": next_turn,
                            "claim": native_claim,
                            "claim_is_independent_evidence": False,
                        }
                    )
                sink.write_event(
                    {
                        "event": "mcp_tool_observation",
                        "turn": next_turn,
                        "tool_call_id": call.call_id,
                        "tool_name": call.name,
                        "is_error": observation.is_error,
                        "raw_observation_sha256": observation.raw_digest,
                        "image_content_omitted": observation.image_content_omitted,
                        "observation": observation.raw_safe,
                    }
                )

            if finished or terminal:
                break
            provider_adapter.add_tool_results(turn, results)

        if not state:
            state = "incomplete"
            stop_reason = "loop_stopped_without_terminal_state"
        return result()
    finally:
        if owned_provider and provider_adapter is not None:
            try:
                await provider_adapter.aclose()
            except Exception as error:
                sink.write_event(
                    {"event": "provider_close_error", "error": safe_exception_message(error)}
                )
        try:
            write_trial_finished()
        finally:
            sink.close()
