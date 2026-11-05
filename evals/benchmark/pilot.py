"""Outcome-blind 20-task AAD pilot with real MCP stdio and an optional fake model.

The cost-accounted live path prints a token-price estimate and exits before
launching a provider unless ``--confirm-spend`` is present. The shared provider
factory independently enforces an explicit spend-consent object. The offline
smoke uses a scripted in-process provider and a real AAD stdio adapter/daemon.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Mapping, Sequence

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .audit_process import AuditWorkerError, audit_task_in_process, unevaluated_task_audit
from .corpus.loader import load_public_projection
from .harness.aad_benchmark_harness.loop import filter_native_tools
from .harness.aad_benchmark_harness.mcp_bridge import ClientSessionBridge
from .harness.aad_benchmark_harness.security import safe_exception_message, safe_observation, sha256_json
from .harness.aad_benchmark_harness.task_loop import TaskTrialResult, run_agent_task_loop
from .harness.aad_benchmark_harness.types import (
    InputTokenEstimate,
    IsolationPreflight,
    ObservationBlock,
    ProviderToolCall,
    ProviderTurn,
    PublicStageProjection,
    RunConfig,
    SpendConsent,
    ToolInvocationResult,
    ToolSpec,
    UsageReceipt,
)
from .oracles import TaskAudit, request_pass_counts
from .selection import SELECTION_PATH, load_selection
from .statistics import AuditObservation, MetricObservation, false_verified_summary, task_cluster_bootstrap


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_TOKEN_BUDGET = 12_000
TASK_GENERATION_CALL_LIMIT = 32
TASK_MCP_TOOL_CALL_LIMIT = 96
TASK_WALL_SECONDS = 300.0
MAX_OUTPUT_TOKENS = 2_048
TOKEN_COUNT_API_CALLS_PER_GENERATION = 1
PILOT_MODEL_CALL_CAP = 120.0
PILOT_TIMEOUT_NOTE = "The pilot caps aad_build timeout_s at 120 seconds so timed-out solver descendants can finish before the task deadline."
BOOTSTRAP_SEED = 20261105
BOOTSTRAP_RESAMPLES = 10_000
_WORKER_ENV_KEYS = frozenset({
    "HOME", "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
    "PATH", "LANG", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE",
    "MPLCONFIGDIR", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
})
_BACKEND_SOURCE_FILES = (
    "packages/engine/src/aad/engine/sandbox.py",
    "packages/engine/src/aad/engine/jobs.py",
    "packages/platform/src/aad_platform/adapter.py",
    "packages/platform/src/aad_platform/daemon/ops_client.py",
    "packages/platform/src/aad_platform/daemon/handlers.py",
    "packages/platform/src/aad_platform/tools.py",
    "evals/benchmark/harness/aad_benchmark_harness/loop.py",
    "evals/benchmark/harness/aad_benchmark_harness/task_loop.py",
    "evals/benchmark/harness/aad_benchmark_harness/providers.py",
    "evals/benchmark/harness/aad_benchmark_harness/types.py",
    "evals/benchmark/harness/aad_benchmark_harness/cli.py",
    "evals/benchmark/oracles.py",
    "evals/benchmark/audit_process.py",
    "evals/benchmark/audit_worker.py",
    "evals/benchmark/selection.py",
    "evals/benchmark/pilot-selection.json",
    "evals/benchmark/statistics.py",
    "evals/benchmark/corpus/tasks.json",
    "evals/benchmark/corpus/public.json",
    "evals/benchmark/pilot.py",
)


def _field(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _structured(result: Any) -> dict[str, Any]:
    value = _field(result, "structuredContent", "structured_content", default={})
    return dict(value) if isinstance(value, Mapping) else {}


def _tool_catalog_digest(tools: Sequence[ToolSpec]) -> str:
    return sha256_json([
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "metadata": tool.metadata,
        }
        for tool in tools
    ])


def _response_text(result: Any) -> str:
    stages = getattr(result, "stage_results", None)
    if not isinstance(stages, Sequence):
        stages = (result,)
    chunks = []
    for stage in stages:
        for record in stage.turn_records:
            if record.text.strip():
                chunks.append(record.text.strip())
    return "\n".join(chunks)


def _model_script(stage: PublicStageProjection) -> str:
    if stage.kind == "2d":
        return (
            "from aad_drafting import sheet2d\n"
            "sheet = sheet2d('offline-smoke', units='mm')\n"
            "sheet.rectangle('outline', 10, 10, origin=(-5, -5))\n"
            "result = sheet\n"
        )
    if stage.kind == "assembly":
        return (
            "from aad import *\n"
            "asm = assembly('offline-smoke')\n"
            "asm.add('base', Box(10, 10, 2))\n"
            "asm.add('upright', Box(2, 10, 8), at=Pos(6, 0, 5))\n"
            "result = asm\n"
        )
    if stage.stage_id == "edit_1":
        return "from aad import *\nresult = Box(12, 10, 10)\n"
    return "from aad import *\nresult = Box(10, 10, 10)\n"


_CLARIFICATION_TEXT = {
    "clarify_pipe_adapter_004": (
        "Which pipe standard (for example NPS and schedule) or measured outside diameter should I use? "
        "Please provide the mating-port dimensions and insertion depth, operating pressure and temperature range, "
        "seal type and permitted leakage, and the material, process, units, and tolerances."
    ),
    "clarify_electronics_box_002": (
        "Please provide the board length, width, thickness, and hole pattern; connector locations and clearance "
        "envelopes; lid or open-top preference and sealing requirement; material, process, wall limits; and units and tolerances."
    ),
    "infeasible_envelope_005": (
        "The minimum cartridge length is 109.9 mm, which exceeds the maximum available span of 105.1 mm, "
        "so the requirements are incompatible and cannot fit together."
    ),
}


class ScriptedFakeProvider:
    """Small deterministic model double used only by offline controls."""

    family = "openai"
    origin = "mock"

    def __init__(self, task_id: str, model: str):
        self.task_id = task_id
        self.model = model
        self.stage: PublicStageProjection | None = None
        self.step = "new"
        self.project: str | None = None
        self.head_rev: int | None = None
        self.serial = 0
        self.exposed_tools: tuple[ToolSpec, ...] = ()
        self.results: list[dict[str, Any]] = []

    def initialize(self, stage: PublicStageProjection, instructions: str, tools: Sequence[ToolSpec]) -> None:
        self.stage = stage
        self.exposed_tools = tuple(tools)
        self.step = "answer" if stage.kind == "clarification" else ("build" if self.project else "new")

    def advance_stage(self, stage: PublicStageProjection) -> None:
        self.stage = stage
        self.step = "answer" if stage.kind == "clarification" else ("build" if self.project else "new")

    async def count_prompt_tokens(self) -> InputTokenEstimate:
        return InputTokenEstimate(256, "scripted_fake_no_provider_request", False, {"origin": "mock"})

    async def complete(self, max_output_tokens: int) -> ProviderTurn:
        if self.stage is None:
            raise RuntimeError("scripted model was not initialized")
        self.serial += 1
        text = ""
        if self.step == "answer":
            text = _CLARIFICATION_TEXT[self.task_id]
            calls = (self._call("benchmark_finish", {
                "artifacts": [],
                "claims_requirements_met": False,
                "summary": "Offline scripted control response; not an agent result.",
            }),)
        elif self.step == "new":
            project_name = f"pilot-{self.task_id}"
            kind = {"drawing": "part"}.get(self.stage.kind, self.stage.kind)
            calls = (self._call("aad_new", {
                "name": project_name,
                "kind": kind,
                "units": "mm",
                "source": _model_script(self.stage),
            }),)
        elif self.step == "build":
            arguments: dict[str, Any] = {
                "project": self.project,
                "source": _model_script(self.stage),
                "wait_s": 30,
                "timeout_s": 60,
            }
            if self.head_rev is not None:
                arguments["base_rev"] = self.head_rev
            calls = (self._call("aad_build", arguments),)
        elif self.step == "drawing":
            calls = (self._call("aad_drawing", {
                "project": self.project,
                "rev": self.head_rev,
                "sheet": "A4",
                "views": [
                    {"name": "front", "label": "Front"},
                    {"name": "top", "label": "Top"},
                    {"name": "right", "label": "Right"},
                ],
                "dimensions": "auto",
                "style": "asme",
                "formats": ["svg"],
            }),)
        else:
            calls = (self._call("benchmark_finish", {
                "artifacts": [],
                "claims_requirements_met": False,
                "summary": "Offline scripted transport smoke; intentionally generic geometry, no quality claim.",
            }),)
        receipt = UsageReceipt(
            input_tokens=100,
            output_tokens=20,
            provider_reported_input_tokens=100,
            provider_reported_total_tokens=120,
            details={"origin": "scripted_fake"},
            provider_fields={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        )
        return ProviderTurn(
            text=text,
            tool_calls=calls,
            raw_response={"origin": "scripted_fake", "turn": self.serial, "step": self.step},
            usage=receipt,
            usage_problem=None,
            stop_reason="tool_use",
        )

    def _call(self, name: str, arguments: dict[str, Any]) -> ProviderToolCall:
        return ProviderToolCall(
            call_id=f"script-{self.serial}-{name}",
            name=name,
            arguments=arguments,
            raw_arguments=json.dumps(arguments, sort_keys=True),
        )

    def add_tool_results(self, turn: ProviderTurn, results: Sequence[ToolInvocationResult]) -> None:
        for result in results:
            structured = result.observation.structured_content
            summary = dict(structured) if isinstance(structured, Mapping) else {}
            self.results.append({"tool": result.name, "is_error": result.observation.is_error, "structured": safe_observation(summary)})
            if result.name == "aad_new" and summary.get("ok") is True:
                self.project = str(summary.get("project", "")) or self.project
                self.step = "build"
            elif result.name == "aad_new":
                self.step = "finish"
            elif result.name == "aad_build":
                if summary.get("ok") is True and type(summary.get("rev")) is int:
                    self.head_rev = summary["rev"]
                    self.step = "drawing" if self.stage and self.stage.kind == "drawing" else "finish"
                else:
                    self.step = "finish"
            elif result.name == "aad_drawing":
                self.step = "finish"

    def tool_catalog_payload(self) -> Any:
        return [{"name": tool.name, "input_schema": tool.input_schema} for tool in self.exposed_tools]

    async def aclose(self) -> None:
        return None


class RecordingBridge:
    """Record only host metadata while forwarding the real MCP stdio session."""

    origin = "live"

    def __init__(self, session: Any):
        self._bridge = ClientSessionBridge(session)
        self.stage_id: str | None = None
        self.catalog_sha256: str | None = None
        self.calls: list[dict[str, Any]] = []
        self.build_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.drawing_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.daemon_stopped = False
        self.wall_seconds = 0.0

    def set_stage(self, stage: PublicStageProjection) -> None:
        self.stage_id = stage.stage_id

    async def list_tools(self) -> Sequence[ToolSpec]:
        tools = tuple(await self._bridge.list_tools())
        adjusted = []
        for tool in tools:
            if tool.name != "aad_build":
                adjusted.append(tool)
                continue
            schema = json.loads(json.dumps(tool.input_schema))
            timeout = schema.get("properties", {}).get("timeout_s")
            if isinstance(timeout, dict):
                timeout["maximum"] = min(float(timeout.get("maximum", PILOT_MODEL_CALL_CAP)), PILOT_MODEL_CALL_CAP)
            description = f"{tool.description} {PILOT_TIMEOUT_NOTE}"
            adjusted.append(ToolSpec(tool.name, description, schema, tool.metadata))
        tools = tuple(adjusted)
        self.catalog_sha256 = _tool_catalog_digest(tools)
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        timeout = arguments.get("timeout_s", 60)
        if (
            name == "aad_build"
            and isinstance(timeout, (int, float))
            and not isinstance(timeout, bool)
            and timeout > PILOT_MODEL_CALL_CAP
        ):
            return {
                "isError": True,
                "content": [{"type": "text", "text": PILOT_TIMEOUT_NOTE}],
                "structuredContent": {
                    "ok": False,
                    "project": str(arguments.get("project", "")),
                    "status": "failed",
                    "summary": PILOT_TIMEOUT_NOTE,
                },
            }
        started = time.monotonic()
        result = await self._bridge.call_tool(name, arguments)
        elapsed = max(0.0, time.monotonic() - started)
        self.wall_seconds += elapsed
        structured = _structured(result)
        is_error = _field(result, "isError", "is_error", default=False) is True
        record = {
            "stage_id": self.stage_id,
            "tool": name,
            "arguments_sha256": sha256_json(arguments),
            "action": arguments.get("action") if name == "aad_history" else None,
            "job": arguments.get("job") if name == "aad_history" else structured.get("job"),
            "is_error": is_error,
            "wall_seconds": elapsed,
            "status": structured.get("status"),
            "project": structured.get("project"),
            "rev": structured.get("rev"),
            "head_rev": structured.get("head_rev"),
            "ok": structured.get("ok"),
            "artifacts": safe_observation(structured.get("artifacts", {})),
        }
        self.calls.append(record)
        if name == "aad_build":
            self.build_calls[self.stage_id or ""].append(record)
        elif name == "aad_drawing":
            self.drawing_calls[self.stage_id or ""].append(record)
        return result


@dataclass(frozen=True)
class SessionEnvironment:
    home: Path
    env: Mapping[str, str]
    temporary_directory: Path


def _session_environment(home: Path, *, marker: str | None = None) -> SessionEnvironment:
    home = home.resolve()
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    # A task's private HOME can be long enough that AAD's fallback Unix socket
    # path would also exceed macOS's sockaddr limit if it used that HOME as
    # TMPDIR. Give each daemon a short, private temp root; solver jobs still
    # receive their own sandbox-local TMPDIR from engine.sandbox.environment.
    temporary_directory = Path(tempfile.mkdtemp(prefix="aadp-", dir="/tmp"))
    env = {
        "HOME": str(home),
        "AAD_HOME": str(home / ".aad"),
        "TMPDIR": str(temporary_directory),
        "PATH": os.defpath,
        "LANG": "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if marker is not None:
        env["AAD_BENCHMARK_ENV_SENTINEL"] = marker
    return SessionEnvironment(home, env, temporary_directory)


def _stop_daemon(env: Mapping[str, str]) -> bool:
    completed = subprocess.run(
        [sys.executable, "-m", "aad_platform.cli", "daemon", "stop", "--json"],
        cwd=REPO_ROOT,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=12,
        check=False,
    )
    if completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False
    return payload.get("ok") is True and payload.get("stopped") is True


@asynccontextmanager
async def aad_stdio_session(home: Path, *, marker: str | None = None):
    """Launch the packaged AAD MCP stdio adapter with a minimal environment."""
    environment = _session_environment(home, marker=marker)
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aad_platform.cli", "mcp"],
        env=dict(environment.env),
        cwd=str(REPO_ROOT),
    )
    bridge: RecordingBridge | None = None
    try:
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                bridge = RecordingBridge(session)
                yield bridge
    finally:
        stopped = await asyncio.to_thread(_stop_daemon, environment.env)
        if bridge is not None:
            bridge.daemon_stopped = stopped
        if stopped:
            shutil.rmtree(environment.temporary_directory, ignore_errors=True)


def _source_fingerprints() -> tuple[str, dict[str, str]]:
    files = {}
    for relative in _BACKEND_SOURCE_FILES:
        path = REPO_ROOT / relative
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    dirty = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    worktree_digest = hashlib.sha256(dirty + json.dumps(files, sort_keys=True).encode()).hexdigest()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout.strip()
    return f"{head}+worktree.{worktree_digest[:16]}", files


def _sandbox_policy_record(probe_root: Path) -> dict[str, Any]:
    from aad.engine import sandbox

    job = probe_root / "policy-job"
    job.mkdir(parents=True, exist_ok=True)
    env = sandbox.environment(job)
    code = "\n".join((inspect.getsource(sandbox.environment), inspect.getsource(sandbox.popen), inspect.getsource(sandbox.command)))
    return {
        "environment_allowlist_match": set(env) == _WORKER_ENV_KEYS,
        "worker_environment_keys": sorted(env),
        "sandbox_source_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "macos_sandbox_exec_present": bool(subprocess.run(
            ["/usr/bin/which", "sandbox-exec"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode == 0) if sys.platform == "darwin" else None,
    }


def _backend_environment_digest(
    source_fingerprints: Mapping[str, str],
    policy: Mapping[str, Any],
    catalog_sha256: str,
) -> str:
    versions = {}
    for package in ("aad-design", "aad-engine", "aad-drafting", "mcp"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return sha256_json({
        "platform": platform.platform(),
        "python": platform.python_version(),
        "packages": versions,
        "backend_source_sha256": dict(source_fingerprints),
        "sandbox_policy": dict(policy),
        "mcp_tool_catalog_sha256": catalog_sha256,
        "mcp_command": [sys.executable, "-m", "aad_platform.cli", "mcp"],
        "task_isolation": "fresh HOME, AAD_HOME and project root per task",
        "audit_process": "fresh Python -I -B subprocess with explicit host artifact paths and timeout",
    })


def _probe_source(probe: str, *, read_path: Path, write_path: Path, network_port: int) -> str:
    if probe == "private_read_sentinel_denied":
        body = (
            f"try:\n    open({json.dumps(str(read_path))}, 'r', encoding='utf-8').read()\n"
            "    raise RuntimeError('preflight_private_read_allowed')\n"
            "except OSError:\n    pass\n"
        )
    elif probe == "write_outside_solver_root_denied":
        body = (
            f"try:\n    open({json.dumps(str(write_path))}, 'w', encoding='utf-8').write('probe')\n"
            "    raise RuntimeError('preflight_outside_write_allowed')\n"
            "except OSError:\n    pass\n"
        )
    elif probe == "network_probe_denied":
        body = (
            "import socket\n"
            "probe_socket = socket.socket()\n"
            "probe_socket.settimeout(1.0)\n"
            f"try:\n    probe_socket.connect(('127.0.0.1', {network_port}))\n"
            "    raise RuntimeError('preflight_network_allowed')\n"
            "except OSError:\n    pass\n"
            "finally:\n    probe_socket.close()\n"
        )
    elif probe == "provider_credentials_absent_from_solver":
        body = (
            "import os\n"
            "if 'AAD_BENCHMARK_ENV_SENTINEL' in os.environ:\n"
            "    raise RuntimeError('preflight_environment_inherited')\n"
        )
    else:
        raise ValueError(f"unknown isolation probe {probe}")
    return "from build123d import Box\n" + body + "result = Box(10, 10, 2)\n"


async def run_sandbox_preflight(
    home: Path,
    *,
    backend_revision: str,
    worker_policy: Mapping[str, Any],
) -> tuple[IsolationPreflight, dict[str, Any], str]:
    marker = "aad-pilot-sentinel-" + uuid.uuid4().hex
    host_root = home / "host-probes"
    host_root.mkdir(parents=True, exist_ok=True)
    read_path = host_root / "private-read.txt"
    write_path = host_root / "outside-write.txt"
    read_path.write_text("private sentinel for AAD benchmark isolation probe", encoding="utf-8")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.15)
    port = listener.getsockname()[1]
    network_connected = threading.Event()

    def accept_probe() -> None:
        try:
            connection, _ = listener.accept()
        except (TimeoutError, OSError):
            return
        network_connected.set()
        connection.close()

    network_thread = threading.Thread(target=accept_probe, daemon=True)
    probes = {
        "private_read_sentinel_denied": False,
        "network_probe_denied": False,
        "write_outside_solver_root_denied": False,
        "provider_credentials_absent_from_solver": False,
        "aad_import_excluded": False,
    }
    tool_catalog_sha256 = "0" * 64
    probe_records: list[dict[str, Any]] = []
    try:
        async with aad_stdio_session(home, marker=marker) as bridge:
            tools = tuple(await bridge.list_tools())
            tool_catalog_sha256 = bridge.catalog_sha256 or _tool_catalog_digest(tools)
            safe_names = {tool.name for tool in filter_native_tools(tools)}
            probes["aad_import_excluded"] = "aad_import" not in safe_names
            required = {"aad_new", "aad_build", "aad_drawing"}
            catalog_ok = required <= safe_names and probes["aad_import_excluded"]
            created = await bridge.call_tool("aad_new", {
                "name": "pilot-isolation-probe",
                "kind": "part",
                "units": "mm",
                "source": "from build123d import Box\nresult = Box(10, 10, 2)\n",
            })
            project = _structured(created).get("project")
            new_ok = _structured(created).get("ok") is True and isinstance(project, str) and bool(project)
            if new_ok and catalog_ok:
                for name in (
                    "private_read_sentinel_denied",
                    "network_probe_denied",
                    "write_outside_solver_root_denied",
                    "provider_credentials_absent_from_solver",
                ):
                    if name == "network_probe_denied":
                        network_thread.start()
                    result = await bridge.call_tool("aad_build", {
                        "project": project,
                        "source": _probe_source(name, read_path=read_path, write_path=write_path, network_port=port),
                        "wait_s": 40,
                        "timeout_s": 30,
                    })
                    envelope = _structured(result)
                    good_build = _field(result, "isError", "is_error", default=False) is not True and envelope.get("ok") is True and envelope.get("status") in {"draft", "verified"}
                    if name == "private_read_sentinel_denied":
                        probes[name] = good_build
                    elif name == "write_outside_solver_root_denied":
                        probes[name] = good_build and not write_path.exists()
                    elif name == "network_probe_denied":
                        network_thread.join(timeout=0.25)
                        probes[name] = good_build and not network_connected.is_set()
                    else:
                        probes[name] = good_build and bool(worker_policy.get("environment_allowlist_match"))
                    probe_records.append({"probe": name, "passed": probes[name], "build_status": envelope.get("status"), "failure": safe_observation(envelope.get("failure"))})
        if network_thread.is_alive():
            network_thread.join(timeout=0.2)
    finally:
        listener.close()
        write_path.unlink(missing_ok=True)
    evidence = {
        "backend_revision": backend_revision,
        "probe_results": probes,
        "tool_catalog_sha256": tool_catalog_sha256,
        "worker_policy": dict(worker_policy),
        "probe_builds": probe_records,
        "sentinel_value_sha256": hashlib.sha256(marker.encode()).hexdigest(),
    }
    backend_environment = _backend_environment_digest(
        {path: hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest() for path in _BACKEND_SOURCE_FILES},
        worker_policy,
        tool_catalog_sha256,
    )
    preflight = IsolationPreflight(
        backend_revision=backend_revision,
        backend_environment=backend_environment,
        probe_results=probes,
        evidence_digest=sha256_json(evidence),
        passed=all(probes.values()),
    )
    return preflight, evidence, tool_catalog_sha256


def _task_config(
    *,
    provider: str,
    model: str,
    backend_revision: str,
    backend_environment: str,
    trace_path: Path,
    solver_root: Path,
    task_order_seed: int,
) -> RunConfig:
    return RunConfig.from_mapping({
        "provider": provider,
        "model": model,
        "backend_name": "aad-mcp-stdio",
        "backend_revision": backend_revision,
        "backend_environment": backend_environment,
        "token_budget": TASK_TOKEN_BUDGET,
        "model_call_limit": TASK_GENERATION_CALL_LIMIT,
        "tool_call_limit": TASK_MCP_TOOL_CALL_LIMIT,
        "wall_deadline_seconds": TASK_WALL_SECONDS,
        "max_output_tokens_per_call": MAX_OUTPUT_TOKENS,
        "input_token_safety_margin_ratio": 0.1,
        "input_token_safety_margin_tokens": 64,
        "repetition": 1,
        "task_order_seed": task_order_seed,
        "ablation_arm": "aad-default-v1",
        "trace_path": str(trace_path),
        "solver_write_roots": [str(solver_root)],
        "provider_supports_image_input": False,
        "include_tool_images": False,
    })


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        target.write("\n")


def _prepare_output_dir(value: str) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    output = requested.resolve(strict=False)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    (output / "raw" / "traces").mkdir(parents=True, mode=0o700)
    (output / "raw" / "tasks").mkdir(parents=True, mode=0o700)
    (output / "artifacts").mkdir(mode=0o700)
    return output


def _stage_projections(public_task: Mapping[str, Any]) -> tuple[PublicStageProjection, ...]:
    return tuple(
        PublicStageProjection.from_task_projection(public_task, str(stage["id"]))
        for stage in public_task["stages"]
    )


def _project_name(value: Any) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        return None
    return value


def _revision_dir(home: Path, project: str, revision: int) -> Path | None:
    if type(revision) is not int or revision < 1:
        return None
    root = (home / "AAD").resolve(strict=False)
    candidate = (root / project / ".aad" / "revs" / f"{revision:06d}").resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _existing_file(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        if path.is_file():
            return str(path.resolve(strict=True))
    except OSError:
        return None
    return None


def _revision_for_project(home: Path, project: str, calls: Sequence[Mapping[str, Any]]) -> int | None:
    for call in reversed(calls):
        if _project_name(call.get("project")) != project:
            continue
        revision = call.get("rev")
        if type(revision) is int:
            return revision
    project_root = (home / "AAD" / project).resolve(strict=False)
    try:
        project_root.relative_to((home / "AAD").resolve(strict=False))
        metadata = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    revision = metadata.get("head_rev") if isinstance(metadata, Mapping) else None
    return revision if type(revision) is int else None


def _artifact_evidence(
    task: Mapping[str, Any],
    home: Path,
    calls: Sequence[Mapping[str, Any]],
    trial: TaskTrialResult | None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    evidence: dict[str, dict[str, Any]] = {}
    first_builds: list[dict[str, Any]] = []
    stage_results = {stage.stage_id: stage for stage in trial.stage_results} if trial else {}
    for stage in task["stages"]:
        stage_id = str(stage["id"])
        oracle_types = {str(check["type"]) for check in stage["oracle"]["checks"]}
        stage_calls = [call for call in calls if call.get("stage_id") == stage_id]
        build_calls = [call for call in stage_calls if call.get("tool") == "aad_build"]
        if stage["expected_outcome"] == "artifact":
            first = build_calls[0] if build_calls else None
            terminal = first
            if first and first.get("status") == "running" and isinstance(first.get("job"), str):
                first_index = next(index for index, call in enumerate(stage_calls) if call is first)
                job_id = first["job"]
                terminal = next((
                    call for call in stage_calls[first_index + 1:]
                    if call.get("tool") == "aad_history"
                    and call.get("action") == "job"
                    and call.get("job") == job_id
                    and call.get("status") != "running"
                ), first)
            project = _project_name(terminal.get("project")) if terminal else None
            revision = terminal.get("rev") if terminal else None
            native_name = "sheet.json" if "dxf_profile" in oracle_types else "part.brep"
            native_path = _existing_file(_revision_dir(home, project, revision) / native_name) if project and type(revision) is int else None
            first_builds.append({
                "stage_id": stage_id,
                "attempted": first is not None,
                "initial_status": first.get("status") if first else None,
                "ok": terminal.get("ok") is True if terminal else False,
                "status": terminal.get("status") if terminal else None,
                "revision": revision if type(revision) is int else None,
                "required_native_artifact": native_name,
                "artifact_present": native_path is not None,
                "valid": bool(
                    first
                    and first.get("ok") is True
                    and first.get("status") in {"draft", "verified"}
                    and type(revision) is int
                    and native_path is not None
                ),
            })

        relevant = [call for call in stage_calls if call.get("tool") in {"aad_new", "aad_build", "aad_history", "aad_drawing"}]
        project_candidates = [_project_name(call.get("project")) for call in relevant]
        if not project_candidates:
            project_candidates = [_project_name(call.get("project")) for call in calls]
        project_candidates = [value for value in project_candidates if value is not None]
        project = project_candidates[-1] if project_candidates else None
        revision = _revision_for_project(home, project, relevant) if project else None
        revision_dir = _revision_dir(home, project, revision) if project and revision else None

        row: dict[str, Any] = {
            "brep_path": None,
            "sheet_json_path": None,
            "drawing_svg_path": None,
            "response_text": _response_text(stage_results[stage_id]) if stage_id in stage_results else "",
        }
        if "dxf_profile" in oracle_types:
            row["sheet_json_path"] = _existing_file(revision_dir / "sheet.json") if revision_dir else None
        if any(value not in {"clarification", "dxf_profile"} for value in oracle_types):
            row["brep_path"] = _existing_file(revision_dir / "part.brep") if revision_dir else None
            # Failed AAD hard-gate candidates remain useful original-request
            # evidence, but only when the daemon returned a path inside this task.
            if row["brep_path"] is None:
                for call in reversed(stage_calls):
                    artifacts = call.get("artifacts")
                    raw = artifacts.get("part.brep") if isinstance(artifacts, Mapping) else None
                    if isinstance(raw, str) and Path(raw).is_absolute():
                        row["brep_path"] = _existing_file(Path(raw))
                        if row["brep_path"] is not None:
                            break
        if "drawing_dimensions" in oracle_types:
            for call in reversed(stage_calls):
                artifacts = call.get("artifacts")
                if not isinstance(artifacts, Mapping):
                    continue
                candidate = next(
                    (
                        value for name, value in artifacts.items()
                        if isinstance(value, str) and (str(name).casefold() == "svg" or value.casefold().endswith(".svg"))
                    ),
                    None,
                )
                if isinstance(candidate, str) and Path(candidate).is_absolute():
                    row["drawing_svg_path"] = _existing_file(Path(candidate))
                    if row["drawing_svg_path"] is not None:
                        break
        evidence[stage_id] = row
    return evidence, first_builds


def _audit_from_dict(value: Mapping[str, Any]) -> TaskAudit:
    from .oracles import CheckObservation, StageAudit

    stages = []
    for stage in value.get("stages", []):
        checks = tuple(
            CheckObservation(
                check_id=str(check["check_id"]),
                check_type=str(check["check_type"]),
                status=str(check["status"]),
                coverage=str(check["coverage"]),
                reason=str(check["reason"]),
                expected=check.get("expected"),
                actual=check.get("actual"),
            )
            for check in stage.get("checks", [])
        )
        stages.append(StageAudit(
            task_id=str(stage["task_id"]),
            stage_id=str(stage["stage_id"]),
            expected_outcome=str(stage["expected_outcome"]),
            declared_coverage=str(stage["declared_coverage"]),
            effective_coverage=str(stage["effective_coverage"]),
            verdict=str(stage["verdict"]),
            checks=checks,
        ))
    return TaskAudit(
        task_id=str(value["task_id"]),
        verdict=str(value["verdict"]),
        coverage=str(value["coverage"]),
        stages=tuple(stages),
    )


def _false_verified_task_observation(
    record: Mapping[str, Any],
    *,
    task_id: str,
    arm_id: str,
) -> AuditObservation:
    """Join each native verification claim to its own stage audit.

    The predeclared sampling unit remains one task. A task is bad only when a
    stage that itself claimed native verification fails its matching audit;
    a later, unverified edit failure cannot retroactively falsify an earlier
    stage's verified claim.
    """
    audit = record.get("audit")
    audit = audit if isinstance(audit, Mapping) else {}
    task_verdict = str(audit.get("verdict", "indeterminate"))
    task_coverage = str(audit.get("coverage", "bounded"))
    trial = record.get("trial")

    if not isinstance(trial, Mapping):
        native_verified: bool | None = None
        verdict, coverage = task_verdict, task_coverage
    else:
        stage_audits = {
            str(stage.get("stage_id")): stage
            for stage in audit.get("stages", [])
            if isinstance(stage, Mapping) and stage.get("stage_id") is not None
        }
        verified_stage_ids = []
        for stage_result in trial.get("stage_results", []):
            if not isinstance(stage_result, Mapping):
                continue
            claims = stage_result.get("native_verified_claims", [])
            if isinstance(claims, list) and claims:
                verified_stage_ids.append(str(stage_result.get("stage_id", "")))

        if verified_stage_ids:
            native_verified = True
            stage_verdicts = []
            stage_coverages = []
            for stage_id in verified_stage_ids:
                stage_audit = stage_audits.get(stage_id)
                if stage_audit is None:
                    stage_verdicts.append("indeterminate")
                    stage_coverages.append("bounded")
                    continue
                stage_verdict = str(stage_audit.get("verdict", "indeterminate"))
                stage_coverage = str(stage_audit.get("effective_coverage", stage_audit.get("coverage", "bounded")))
                if stage_verdict == "good" and stage_coverage != "complete":
                    stage_verdict = "indeterminate"
                stage_verdicts.append(stage_verdict)
                stage_coverages.append(stage_coverage)
            verdict = (
                "bad" if "bad" in stage_verdicts
                else "indeterminate" if "indeterminate" in stage_verdicts
                else "good"
            )
            coverage = "complete" if stage_coverages and all(item == "complete" for item in stage_coverages) else "bounded"
        elif trial.get("completed") is True:
            native_verified = False
            verdict, coverage = task_verdict, task_coverage
        else:
            native_verified = None
            verdict, coverage = task_verdict, task_coverage

    return AuditObservation(
        task_id=task_id,
        repetition=1,
        native_verified=native_verified,
        verdict=verdict,
        coverage=coverage,
        origin=str(record.get("origin", "scripted_control")),
        arm_id=arm_id,
    )


async def _run_task(
    task: Mapping[str, Any],
    public_task: Mapping[str, Any],
    *,
    output: Path,
    preflight: IsolationPreflight,
    expected_catalog_sha256: str,
    backend_revision: str,
    backend_environment: str,
    provider_family: str,
    model: str,
    task_order_seed: int,
    scripted: bool,
    spend_consent: SpendConsent | None,
) -> dict[str, Any]:
    task_id = str(task["id"])
    home = output / "artifacts" / task_id
    home.mkdir(mode=0o700)
    solver_root = home / "AAD"
    trace_path = output / "raw" / "traces" / f"{task_id}.jsonl"
    stages = _stage_projections(public_task)
    config = _task_config(
        provider=provider_family,
        model=model,
        backend_revision=backend_revision,
        backend_environment=backend_environment,
        trace_path=trace_path,
        solver_root=solver_root,
        task_order_seed=task_order_seed,
    )
    provider = ScriptedFakeProvider(task_id, model) if scripted else None
    trial: TaskTrialResult | None = None
    task_error: str | None = None
    bridge: RecordingBridge | None = None
    initial_catalog_sha256: str | None = None
    try:
        async with aad_stdio_session(home) as active_bridge:
            bridge = active_bridge
            await bridge.list_tools()
            initial_catalog_sha256 = bridge.catalog_sha256
            if initial_catalog_sha256 != expected_catalog_sha256:
                raise RuntimeError("MCP tool catalog changed after the isolation preflight")
            trial = await run_agent_task_loop(
                config,
                stages,
                bridge,
                preflight,
                provider=provider,
                spend_consent=spend_consent,
            )
            if bridge.catalog_sha256 != expected_catalog_sha256:
                raise RuntimeError("MCP tool catalog changed during the task")
    except Exception as error:
        task_error = safe_exception_message(error)

    calls = list(bridge.calls) if bridge is not None else []
    evidence, first_builds = _artifact_evidence(task, home, calls, trial)
    audit_error = None
    try:
        audit = audit_task_in_process(
            task_id,
            evidence,
            allowed_roots=(home,),
            timeout_seconds=90.0,
        )
    except Exception as error:
        audit_error = safe_exception_message(error)
        audit = unevaluated_task_audit(task, "independent audit unavailable: " + audit_error).as_dict()

    record = {
        "task_id": task_id,
        "category": task["category"],
        "origin": "scripted_control" if scripted else "real_agent",
        "task_error": task_error,
        "audit_error": audit_error,
        "daemon_stopped": bool(bridge is not None and bridge.daemon_stopped),
        "tool_catalog_sha256": initial_catalog_sha256,
        "expected_tool_catalog_sha256": expected_catalog_sha256,
        "trial": trial.as_dict() if trial else None,
        "tool_calls": calls,
        "first_build_attempts": first_builds,
        "stage_evidence": evidence,
        "audit": audit,
    }
    _write_json(output / "raw" / "tasks" / f"{task_id}.json", record)
    return record


def _metric_rows(
    records: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
    selection: Mapping[str, Any],
    *,
    arm_id: str,
) -> dict[str, Any]:
    task_by_id = {str(task["id"]): task for task in tasks}
    record_by_id = {str(record["task_id"]): record for record in records}
    task_ids = [str(task_id) for task_id in selection["run_order"]]
    expected = [(task_id, 1) for task_id in task_ids]
    strata = {task_id: str(task_by_id[task_id]["category"]) for task_id in task_ids}
    populations = {
        name: int(row["population_n"])
        for name, row in selection["strata"].items()
    }
    audits = [_audit_from_dict(record_by_id[task_id]["audit"]) for task_id in task_ids]
    check_counts = request_pass_counts(audits)
    verdict_counts = {
        verdict: sum(record_by_id[task_id]["audit"].get("verdict") == verdict for task_id in task_ids)
        for verdict in ("good", "bad", "indeterminate")
    }
    strict_rows = []
    witness_rows = []
    expected_checks = 0
    for task_id in task_ids:
        audit = record_by_id[task_id]["audit"]
        checks = [check for stage in audit.get("stages", []) for check in stage.get("checks", [])]
        expected_n = sum(len(stage["oracle"]["checks"]) for stage in task_by_id[task_id]["stages"])
        expected_checks += expected_n
        passed = sum(check.get("status") == "pass" for check in checks)
        task_value = passed / expected_n if expected_n and len(checks) == expected_n else None
        witness_rows.append(MetricObservation(task_id, 1, task_value, arm_id))
        strict_rows.append(MetricObservation(
            task_id,
            1,
            1.0 if audit.get("verdict") == "good" else 0.0,
            arm_id,
        ))
    witness_uncertainty = task_cluster_bootstrap(
        witness_rows,
        expected=expected,
        strata=strata,
        population_counts=populations,
        seed=BOOTSTRAP_SEED,
        resamples=BOOTSTRAP_RESAMPLES,
    )
    confirmed_pass_uncertainty = task_cluster_bootstrap(
        strict_rows,
        expected=expected,
        strata=strata,
        population_counts=populations,
        seed=BOOTSTRAP_SEED,
        resamples=BOOTSTRAP_RESAMPLES,
    )

    artifact_tasks = [task for task in tasks if task["id"] in set(task_ids)]
    build_observations = []
    build_strata: dict[str, str] = {}
    expected_builds = 0
    valid_builds = 0
    attempted_builds = 0
    for task_id in task_ids:
        task = task_by_id[task_id]
        artifact_stages = [stage for stage in task["stages"] if stage["expected_outcome"] == "artifact"]
        if not artifact_stages:
            continue
        expected_builds += len(artifact_stages)
        stage_rows = {
            str(row.get("stage_id")): row
            for row in record_by_id[task_id].get("first_build_attempts", [])
            if isinstance(row, Mapping) and isinstance(row.get("stage_id"), str)
        }
        valid = sum(stage_rows.get(stage["id"], {}).get("valid") is True for stage in artifact_stages)
        attempted = sum(stage_rows.get(stage["id"], {}).get("attempted") is True for stage in artifact_stages)
        valid_builds += valid
        attempted_builds += attempted
        build_observations.append(MetricObservation(
            task_id,
            1,
            valid / len(artifact_stages),
            arm_id,
        ))
        build_strata[task_id] = str(task["category"])
    build_populations = {
        name: count for name, count in populations.items()
        if name in set(build_strata.values())
    }
    build_uncertainty = task_cluster_bootstrap(
        build_observations,
        expected=[(task_id, 1) for task_id in build_strata],
        strata=build_strata,
        population_counts=build_populations,
        seed=BOOTSTRAP_SEED,
        resamples=BOOTSTRAP_RESAMPLES,
    )

    edit_task_ids = [task_id for task_id in task_ids if task_by_id[task_id]["category"] == "edit_chain"]
    edit_values: dict[str, float | None] = {}
    edit_details: dict[str, Any] = {}
    for task_id in edit_task_ids:
        task = task_by_id[task_id]
        audit = record_by_id[task_id]["audit"]
        stages = {str(stage["stage_id"]): stage for stage in audit.get("stages", [])}
        final_stage = task["stages"][-1]
        previous_stage = task["stages"][-2]
        preserve_ids = list(final_stage["oracle"].get("preserve_check_ids", []))
        previous_checks = {check["check_id"]: check for check in stages.get(previous_stage["id"], {}).get("checks", [])}
        final_checks = {check["check_id"]: check for check in stages.get(final_stage["id"], {}).get("checks", [])}
        denominator = 0
        retained = 0
        source_pass = 0
        source_failed = 0
        source_unknown = 0
        final_failed = 0
        final_unknown = 0
        for check_id in preserve_ids:
            before = previous_checks.get(check_id)
            after = final_checks.get(check_id)
            if before is None or after is None:
                source_unknown += int(before is None)
                final_unknown += int(after is None)
                continue
            if before["status"] == "pass":
                denominator += 1
                source_pass += 1
                if after["status"] == "pass":
                    retained += 1
                elif after["status"] == "unsupported":
                    final_unknown += 1
                else:
                    final_failed += 1
            elif before["status"] == "unsupported":
                source_unknown += 1
            else:
                source_failed += 1
        value = retained / denominator if denominator else None
        edit_values[task_id] = value
        edit_details[task_id] = {
            "declared_preserve_checks": len(preserve_ids),
            "source_pass_denominator": denominator,
            "source_pass": source_pass,
            "source_failed": source_failed,
            "source_unsupported_or_missing": source_unknown,
            "retained": retained,
            "edited_failed": final_failed,
            "edited_unsupported_or_missing": final_unknown,
            "retention_rate": value,
        }
    edit_strata = {task_id: "edit_chain" for task_id in edit_task_ids}
    edit_observations = [MetricObservation(task_id, 1, edit_values[task_id], arm_id) for task_id in edit_task_ids]
    edit_uncertainty = task_cluster_bootstrap(
        edit_observations,
        expected=[(task_id, 1) for task_id in edit_task_ids],
        strata=edit_strata,
        population_counts={"edit_chain": populations["edit_chain"]},
        seed=BOOTSTRAP_SEED,
        resamples=BOOTSTRAP_RESAMPLES,
    )
    edit_declared = sum(item["declared_preserve_checks"] for item in edit_details.values())
    edit_source_pass = sum(item["source_pass_denominator"] for item in edit_details.values())
    edit_retained = sum(item["retained"] for item in edit_details.values())

    clarification_ids = [task_id for task_id in task_ids if task_by_id[task_id]["category"] in {"clarification", "infeasible"}]
    disposition = {
        "expected": len(clarification_ids),
        "good": sum(record_by_id[task_id]["audit"].get("verdict") == "good" for task_id in clarification_ids),
        "bad": sum(record_by_id[task_id]["audit"].get("verdict") == "bad" for task_id in clarification_ids),
        "indeterminate": sum(record_by_id[task_id]["audit"].get("verdict") == "indeterminate" for task_id in clarification_ids),
        "task_ids": clarification_ids,
    }
    disposition["pass_rate"] = disposition["good"] / disposition["expected"] if disposition["expected"] else None
    disposition_strata = {
        task_id: str(task_by_id[task_id]["category"])
        for task_id in clarification_ids
    }
    disposition_observations = [
        MetricObservation(
            task_id,
            1,
            1.0 if record_by_id[task_id]["audit"].get("verdict") == "good" else 0.0,
            arm_id,
        )
        for task_id in clarification_ids
    ]
    disposition_populations = {
        name: populations[name]
        for name in populations
        if name in set(disposition_strata.values())
    }
    disposition["uncertainty_95pct"] = task_cluster_bootstrap(
        disposition_observations,
        expected=[(task_id, 1) for task_id in clarification_ids],
        strata=disposition_strata,
        population_counts=disposition_populations,
        seed=BOOTSTRAP_SEED,
        resamples=BOOTSTRAP_RESAMPLES,
    )
    disposition["uncertainty_estimand"] = (
        "category-population-weighted mean of each selected clarification/infeasible task's good indicator"
    )

    false_verified_points = [
        _false_verified_task_observation(
            record_by_id[task_id],
            task_id=task_id,
            arm_id=arm_id,
        )
        for task_id in task_ids
    ]
    false_verified = false_verified_summary(
        false_verified_points,
        selected=[(task_id, 1) for task_id in task_ids],
        arm_id=arm_id,
        sampling_plan_sha256=hashlib.sha256(SELECTION_PATH.read_bytes()).hexdigest(),
        sampling_assumptions=(
            "One predeclared task per hash-ranked category sample; task is the independent unit. "
            "Inference is conditional on a native verified claim and complete eligible audit."
        ),
        host_manifest_validated=False,
    )

    expected_artifact_tasks = sum(task["category"] not in {"clarification", "infeasible"} for task in artifact_tasks)
    return {
        "request_pass_rate": {
            "passes": verdict_counts["good"],
            "denominator": len(task_ids),
            "rate": verdict_counts["good"] / len(task_ids) if task_ids else None,
            "confirmed_full_pass_lower_bound": True,
            "true_request_correctness_estimable": all(
                record_by_id[task_id]["audit"].get("coverage") == "complete"
                for task_id in task_ids
            ),
            "bad": verdict_counts["bad"],
            "indeterminate": verdict_counts["indeterminate"],
            "note": (
                "The raw rate is good tasks / all selected tasks. Only complete, all-pass task audits count as passes. Bounded positive witnesses and unsupported checks remain indeterminate/nonpasses; this is a confirmed-pass lower bound, not an estimate of unresolved tasks' true correctness. The bootstrap estimate weights category-specific task rates by corpus population and may differ from the raw sample rate."
            ),
            "uncertainty_estimand": "category-population-weighted mean of complete, confirmed-pass task indicators",
            "task_cluster_bootstrap_95pct": confirmed_pass_uncertainty,
        },
        "request_check_witnesses": {
            **check_counts,
            "expected_declared_checks": expected_checks,
            "raw_denominator_matches_expected": check_counts["denominator"] == expected_checks,
            "unsupported_are_nonpasses": True,
            "uncertainty_estimand": "category-population-weighted mean of each task's passed-check fraction",
            "task_cluster_bootstrap_95pct": witness_uncertainty,
        },
        "first_pass_build_validity": {
            "valid_first_builds": valid_builds,
            "first_build_attempts": attempted_builds,
            "expected_artifact_stage_denominator": expected_builds,
            "rate": valid_builds / expected_builds if expected_builds else None,
            "unattempted_counted_as_nonpasses": True,
            "uncertainty_95pct": build_uncertainty,
            "uncertainty_estimand": "category-population-weighted mean of each artifact-eligible task's valid first-build fraction",
            "definition": "first aad_build call in each artifact-expected stage succeeds with draft/verified status, returns a revision, and saves the required BRep or Sheet2D JSON artifact",
        },
        "edit_retention": {
            "retained_preserve_checks": edit_retained,
            "base_pass_denominator": edit_source_pass,
            "rate": edit_retained / edit_source_pass if edit_source_pass else None,
            "declared_preserve_checks": edit_declared,
            "per_task": edit_details,
            "uncertainty_95pct": edit_uncertainty,
            "uncertainty_estimand": "mean task-level edit-retention rate within the edit-chain stratum; raw rate is pooled retained checks / pooled base-pass checks",
            "definition": "among explicitly preserved checks that passed on the immediately preceding stage, the fraction that also passes on the edited stage; unsupported or missing evidence never enters the numerator",
        },
        "clarification_infeasible_disposition": disposition,
        "false_verified": false_verified,
        "expected_task_count": len(task_ids),
        "expected_artifact_task_count": expected_artifact_tasks,
        "audit_task_verdicts": verdict_counts,
    }


def _cost_estimate(input_rate: float, output_rate: float, *, provider: str, model: str) -> dict[str, Any]:
    max_tasks = 20
    max_generation_calls = max_tasks * TASK_GENERATION_CALL_LIMIT
    max_token_count_calls = max_generation_calls * TOKEN_COUNT_API_CALLS_PER_GENERATION
    max_provider_calls = max_generation_calls + max_token_count_calls
    total_tokens = max_tasks * TASK_TOKEN_BUDGET
    estimate = total_tokens * max(input_rate, output_rate) / 1_000_000
    maximum_output_tokens = max_generation_calls * MAX_OUTPUT_TOKENS
    return {
        "provider": provider,
        "model": model,
        "price_source": "caller_supplied",
        "prices_are_absolute_spend_bounds": False,
        "pricing_input_usd_per_million": input_rate,
        "pricing_output_usd_per_million": output_rate,
        "configured_token_budget_total": total_tokens,
        "estimated_cost_usd_at_configured_token_budget": estimate,
        "estimate_method": "configured token budget priced entirely at the higher caller-supplied input/output rate",
        "token_budget_semantics": "pre-call reservation target, not a hard billable-token ceiling; a provider-reported overage can exceed it before the loop stops later calls",
        "maximum_generation_calls": max_generation_calls,
        "maximum_input_token_count_api_calls": max_token_count_calls,
        "maximum_provider_api_requests": max_provider_calls,
        "maximum_agent_mcp_tool_calls": max_tasks * TASK_MCP_TOOL_CALL_LIMIT,
        "max_output_tokens_per_generation": MAX_OUTPUT_TOKENS,
        "maximum_output_tokens_across_generations": maximum_output_tokens,
        "worst_case_usage_note": (
            f"The runner sends max_output_tokens/max_tokens={MAX_OUTPUT_TOKENS} on every generation; if the provider honors it, generated output is bounded by {maximum_output_tokens} tokens. "
            "Prompt input token estimates can undercount, and these adapters have no provider-side input-token cap, so this harness does not establish a finite worst-case total-token or spend bound."
        ),
        "task_budgets": {
            "max_tokens": TASK_TOKEN_BUDGET,
            "max_generation_calls": TASK_GENERATION_CALL_LIMIT,
            "max_mcp_tool_calls": TASK_MCP_TOOL_CALL_LIMIT,
            "max_wall_seconds": TASK_WALL_SECONDS,
            "max_output_tokens_per_generation": MAX_OUTPUT_TOKENS,
            "aad_build_timeout_seconds": int(PILOT_MODEL_CALL_CAP),
            "repetitions": 1,
        },
        "estimate_limitations": "This is an estimate at the configured reservation budget using caller-supplied token rates, not a maximum spend guarantee. It excludes provider request fees, taxes, discounts, cached-token tiers, and other charges.",
    }


def _positive_rate(value: float | None, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        raise ValueError(f"{label} must be a finite, nonnegative USD-per-million-token rate")
    return float(value)


def _public_map(value: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(task["id"]): task for task in value["tasks"]}


async def run_pilot(
    args: argparse.Namespace,
    *,
    selection: Mapping[str, Any],
    corpus: Mapping[str, Any],
    public: Mapping[str, Any],
    output: Path,
    cost_estimate: Mapping[str, Any] | None,
    spend_consent: SpendConsent | None = None,
) -> dict[str, Any]:
    scripted = bool(args.offline_smoke)
    if not scripted and not isinstance(spend_consent, SpendConsent):
        raise PermissionError("live pilot requires explicit --confirm-spend consent")
    if scripted and spend_consent is not None:
        raise ValueError("offline scripted control must not carry live spend consent")
    started_wall = time.monotonic()
    task_by_id = {str(task["id"]): task for task in corpus["tasks"]}
    public_by_id = _public_map(public)
    selected_ids = [str(task_id) for task_id in selection["run_order"]]
    selected_tasks = [task_by_id[task_id] for task_id in selected_ids]
    asset_count = sum(
        len(stage["input_assets"])
        for task_id in selected_ids
        for stage in public_by_id[task_id]["stages"]
    )
    if asset_count:
        raise RuntimeError(
            "the frozen pilot selection now contains public assets; stage those assets before running this version"
        )

    backend_revision, source_fingerprints = _source_fingerprints()
    preflight_home = output / "artifacts" / "_isolation-preflight"
    preflight_home.mkdir(mode=0o700)
    policy = _sandbox_policy_record(preflight_home)
    preflight_started = time.monotonic()
    preflight, preflight_evidence, catalog_sha256 = await run_sandbox_preflight(
        preflight_home,
        backend_revision=backend_revision,
        worker_policy=policy,
    )
    preflight_wall = max(0.0, time.monotonic() - preflight_started)
    preflight_doc = {
        **preflight.as_dict(),
        "wall_seconds": preflight_wall,
        "evidence": preflight_evidence,
        "required_probes": sorted(IsolationPreflight.REQUIRED_PROBES),
        "additional_probe_results": {
            name: value for name, value in preflight.probe_results.items()
            if name not in IsolationPreflight.REQUIRED_PROBES
        },
        "provider_credentials_note": "The probe used a generic environment sentinel to verify the worker allowlist. It did not inspect or enumerate provider credentials.",
    }
    _write_json(output / "preflight.json", preflight_doc)
    if not preflight.passed:
        raise RuntimeError("AAD isolation preflight failed; no provider adapter was created")
    if preflight_evidence.get("backend_revision") != backend_revision:
        raise RuntimeError("isolation evidence is not bound to the recorded backend revision")

    backend_environment = preflight.backend_environment
    provider_family = "openai" if scripted else str(args.provider)
    model = "scripted-offline-control" if scripted else str(args.model)
    arm_id = f"{provider_family}/{model}/aad-default-v1"
    if scripted:
        arm_id = "scripted-control/mock/aad-default-v1"
    task_records: list[dict[str, Any]] = []
    stop_reason: str | None = None
    task_order_seed = int.from_bytes(hashlib.sha256(str(selection["seed"]).encode("utf-8")).digest()[:4], "big")
    for task in selected_tasks:
        task_id = str(task["id"])
        record = await _run_task(
            task,
            public_by_id[task_id],
            output=output,
            preflight=preflight,
            expected_catalog_sha256=catalog_sha256,
            backend_revision=backend_revision,
            backend_environment=backend_environment,
            provider_family=provider_family,
            model=model,
            task_order_seed=task_order_seed,
            scripted=scripted,
            spend_consent=spend_consent,
        )
        task_records.append(record)
        if not record["daemon_stopped"]:
            stop_reason = "AAD daemon teardown was not confirmed; subsequent tasks were not launched"
            break

    metric_records = list(task_records)
    completed_ids = {str(record["task_id"]) for record in task_records}
    for task in selected_tasks:
        task_id = str(task["id"])
        if task_id in completed_ids:
            continue
        reason = stop_reason or "task was not launched after an earlier pilot interruption"
        missing_record = {
            "task_id": task_id,
            "category": task["category"],
            "origin": "scripted_control" if scripted else "real_agent",
            "task_error": reason,
            "audit_error": None,
            "daemon_stopped": False,
            "tool_catalog_sha256": None,
            "expected_tool_catalog_sha256": catalog_sha256,
            "trial": None,
            "tool_calls": [],
            "first_build_attempts": [],
            "stage_evidence": {
                str(stage["id"]): {
                    "brep_path": None,
                    "sheet_json_path": None,
                    "drawing_svg_path": None,
                    "response_text": "",
                }
                for stage in task["stages"]
            },
            "audit": unevaluated_task_audit(task, reason).as_dict(),
            "executed": False,
        }
        metric_records.append(missing_record)
        _write_json(output / "raw" / "tasks" / f"{task_id}.json", missing_record)

    metrics = _metric_rows(metric_records, selected_tasks, selection, arm_id=arm_id)
    total_model_calls = sum(
        stage.get("model_calls_attempted", 0)
        for record in task_records
        for stage in (record.get("trial") or {}).get("stage_results", [])
    )
    total_usage_calls = sum(
        stage.get("model_calls_with_usage", 0)
        for record in task_records
        for stage in (record.get("trial") or {}).get("stage_results", [])
    )
    total_input_tokens = sum(
        stage.get("provider_input_tokens_known", 0)
        for record in task_records
        for stage in (record.get("trial") or {}).get("stage_results", [])
    )
    total_output_tokens = sum(
        stage.get("provider_output_tokens_known", 0)
        for record in task_records
        for stage in (record.get("trial") or {}).get("stage_results", [])
    )
    total_count_calls = sum(
        stage.get("input_token_count_calls", 0)
        for record in task_records
        for stage in (record.get("trial") or {}).get("stage_results", [])
    )
    missing_usage_turns = sum(
        len(stage.get("missing_usage_turns", []))
        for record in task_records
        for stage in (record.get("trial") or {}).get("stage_results", [])
    )
    task_wall = sum(
        (record.get("trial") or {}).get("wall_seconds", 0.0)
        for record in task_records
    )
    tool_wall = sum(
        call.get("wall_seconds", 0.0)
        for record in task_records
        for call in record.get("tool_calls", [])
    )
    observed_usage_cost = None
    if not scripted and missing_usage_turns == 0 and cost_estimate is not None:
        observed_usage_cost = (
            total_input_tokens * float(cost_estimate["pricing_input_usd_per_million"])
            + total_output_tokens * float(cost_estimate["pricing_output_usd_per_million"])
        ) / 1_000_000

    selection_sha256 = hashlib.sha256(SELECTION_PATH.read_bytes()).hexdigest()
    report = {
        "schema_version": 1,
        "report_type": "aad-benchmark-pilot",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "selection_id": selection["selection_id"],
        "sampling_plan_sha256": selection_sha256,
        "sampling_rule": selection["rule"],
        "sampling_assumptions": "Within each task-category stratum, the selected hash-ranked sample is treated as exchangeable with that stratum for descriptive cluster-bootstrap intervals. This small pilot is not a release gate or proof of representativeness.",
        "corpus_sha256": selection["corpus_sha256"],
        "selected_task_ids": selected_ids,
        "selected_task_count": len(selected_ids),
        "selected_stage_count": selection["stage_count"],
        "strata": {
            name: {
                "population_n": row["population_n"],
                "selected_n": len(row["selected_task_ids"]),
                "selected_task_ids": row["selected_task_ids"],
            }
            for name, row in selection["strata"].items()
        },
        "origin": "scripted_control" if scripted else "real_agent",
        "provider": provider_family,
        "model": model,
        "backend": "AAD MCP over stdio with real AAD daemon",
        "backend_revision": backend_revision,
        "backend_environment_sha256": backend_environment,
        "backend_source_sha256": source_fingerprints,
        "mcp_tool_catalog_sha256": catalog_sha256,
        "isolation_preflight": preflight_doc,
        "cost_estimate": dict(cost_estimate) if cost_estimate else {
            "provider_spend_usd": 0,
            "provider_calls": 0,
            "maximum_provider_api_requests": 0,
            "note": "Scripted model; no provider SDK or provider API call is used.",
        },
        "metrics": metrics,
        "usage_and_time": {
            "usage_origin": "scripted_synthetic" if scripted else "provider_reported",
            "repetitions_per_task": 1,
            "repeat_based_resource_uncertainty": "not estimated: one repetition per task; token and wall values are observed run totals, not repeated-run intervals",
            "model_generation_calls_attempted": total_model_calls,
            "model_generation_calls_with_usage": total_usage_calls,
            "input_token_count_operations": total_count_calls,
            "provider_api_request_upper_bound_observed": 0 if scripted else total_model_calls + total_count_calls,
            "scripted_model_operation_count": total_model_calls + total_count_calls if scripted else None,
            "provider_input_tokens_known": total_input_tokens,
            "provider_output_tokens_known": total_output_tokens,
            "provider_total_tokens_known": total_input_tokens + total_output_tokens,
            "missing_usage_turns": missing_usage_turns,
            "modeled_usage_cost_usd": observed_usage_cost,
            "modeled_usage_cost_is_invoice": False,
            "agent_mcp_tool_calls": sum(
                stage.get("tool_calls_attempted", 0)
                for record in task_records
                for stage in (record.get("trial") or {}).get("stage_results", [])
            ),
            "agent_wall_seconds_sum": task_wall,
            "mcp_tool_wall_seconds_sum": tool_wall,
            "pilot_wall_seconds_including_preflight_audit_and_reporting": max(0.0, time.monotonic() - started_wall),
        },
        "scheduled_task_count": len(selected_ids),
        "completed_task_count": sum(
            (record.get("trial") or {}).get("completed") is True
            for record in task_records
        ),
        "executed_task_count": len(task_records),
        "task_records": [
            {
                "task_id": record["task_id"],
                "category": record["category"],
                "origin": record["origin"],
                "executed": record.get("executed", True),
                "trial_completed": (record.get("trial") or {}).get("completed"),
                "task_error": record.get("task_error"),
                "audit_error": record.get("audit_error"),
                "daemon_stopped": record.get("daemon_stopped"),
                "audit_verdict": record["audit"].get("verdict"),
                "audit_coverage": record["audit"].get("coverage"),
            }
            for record in metric_records
        ],
        "stop_reason": stop_reason,
        "release_gate": "not evaluated; a 20-task pilot is not the release gate",
        "limitations": [
            "Scripted offline controls are transport and harness smoke tests; they are not real-agent quality results.",
            "All artifact oracles remain bounded; strict request passes require complete coverage, and unresolved checks do not pass.",
            "False-verified inference is conditional on native verified claims and is unknown for an empty verified denominator or scripted controls.",
            "No plain-build123d comparison arm is included because the repository has no matched plain-build123d MCP runner, tool catalog, isolation launcher or artifact/report path.",
        ],
    }
    if scripted:
        report["usage_and_time"]["provider_api_requests"] = 0
    else:
        report["usage_and_time"]["provider_api_requests"] = total_model_calls + total_count_calls
    _write_json(output / "manifest.json", {
        "schema_version": 1,
        "selection_id": selection["selection_id"],
        "sampling_plan_sha256": selection_sha256,
        "backend_revision": backend_revision,
        "backend_environment_sha256": backend_environment,
        "mcp_tool_catalog_sha256": catalog_sha256,
        "task_order_seed": task_order_seed,
        "origin": "scripted_control" if scripted else "real_agent",
        "provider": provider_family,
        "model": model,
        "arm_id": arm_id,
        "expected_task_repetitions": [[task_id, 1] for task_id in selected_ids],
        "preflight_evidence_sha256": preflight.evidence_digest,
        "task_records": [
            {"task_id": record["task_id"], "executed": record.get("executed", True)}
            for record in metric_records
        ],
        "stop_reason": stop_reason,
    })
    _write_json(output / "report.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aad-benchmark-pilot",
        description=(
            "Run the frozen 20-task pilot. Live provider calls are impossible without --confirm-spend; "
            "--offline-smoke uses a scripted fake model and real local AAD MCP/daemon."
        ),
    )
    parser.add_argument("--output-dir", required=True, help="New, empty host-owned directory for traces, artifacts and reports")
    parser.add_argument("--offline-smoke", action="store_true", help="Run a scripted no-provider control over real local MCP stdio and AAD daemon")
    parser.add_argument("--provider", choices=("openai", "anthropic"), help="Provider for a paid pilot; there is no default")
    parser.add_argument("--model", help="Explicit provider model name; there is no default")
    parser.add_argument("--input-usd-per-million", type=float, help="Caller-supplied input price used for the configured-budget cost estimate")
    parser.add_argument("--output-usd-per-million", type=float, help="Caller-supplied output price used for the configured-budget cost estimate")
    parser.add_argument("--confirm-spend", action="store_true", help="Explicitly authorize provider calls after the cost estimate is printed")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        spend_consent: SpendConsent | None = None
        corpus, selection = load_selection()
        public = load_public_projection()
        from .corpus.loader import public_projection

        if public != public_projection(corpus):
            raise ValueError("checked-in public projection does not match the validated task corpus")
        selected = set(selection["run_order"])
        if selected - set(_public_map(public)):
            raise ValueError("frozen selection includes a task missing from public projection")
        if args.offline_smoke:
            if args.confirm_spend or any(value is not None for value in (args.provider, args.model, args.input_usd_per_million, args.output_usd_per_million)):
                raise ValueError("--offline-smoke cannot be combined with provider settings or --confirm-spend")
            cost_estimate = None
            print(json.dumps({
                "mode": "offline_smoke",
                "provider_spend_usd": 0,
                "provider_api_requests": 0,
                "selected_tasks": len(selection["run_order"]),
                "backend": "real local AAD MCP stdio and daemon; scripted model",
            }, sort_keys=True))
        else:
            if not args.provider or not isinstance(args.model, str) or not args.model.strip():
                raise ValueError("a paid pilot requires explicit --provider and --model values")
            if args.input_usd_per_million is None or args.output_usd_per_million is None:
                raise ValueError("a paid pilot requires explicit input and output USD-per-million token prices")
            input_rate = _positive_rate(args.input_usd_per_million, "--input-usd-per-million")
            output_rate = _positive_rate(args.output_usd_per_million, "--output-usd-per-million")
            cost_estimate = _cost_estimate(input_rate, output_rate, provider=args.provider, model=args.model)
            print(json.dumps({"mode": "paid_pilot_cost_estimate", **cost_estimate}, sort_keys=True))
            if not args.confirm_spend:
                print("No provider request was sent. Re-run with --confirm-spend only if this estimate and its unbounded-input limitation are acceptable.")
                return 0
            spend_consent = SpendConsent.from_cli_flag(args.confirm_spend)

        output = _prepare_output_dir(args.output_dir)
        report = asyncio.run(run_pilot(
            args,
            selection=selection,
            corpus=corpus,
            public=public,
            output=output,
            cost_estimate=cost_estimate,
            spend_consent=spend_consent,
        ))
        print(json.dumps({
            "output_dir": str(output),
            "report": str(output / "report.json"),
            "manifest": str(output / "manifest.json"),
            "completed_tasks": report["completed_task_count"],
            "request_pass_rate": report["metrics"]["request_pass_rate"],
            "false_verified": {
                "n_verified": report["metrics"]["false_verified"]["n_verified"],
                "bad": report["metrics"]["false_verified"]["bad"],
                "blockers": report["metrics"]["false_verified"]["blockers"],
            },
            "release_gate": report["release_gate"],
        }, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"pilot_error": safe_exception_message(error)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
