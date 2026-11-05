from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .loop import run_agent_loop
from .security import safe_exception_message
from .types import IsolationPreflight, PublicStageProjection, RunConfig, TrialResult


@dataclass(frozen=True)
class TrialLaunch:
    """Return value expected from the parent-owned backend launcher."""

    backend: Any
    isolation_preflight: IsolationPreflight
    close: Callable[[], Any] | None = None


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def _load_callable(specification: str) -> Callable[..., Any]:
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("launcher must use module:function form")
    module = importlib.import_module(module_name)
    launch = getattr(module, attribute, None)
    if not callable(launch):
        raise ValueError("launcher target is not callable")
    return launch


async def run_from_files(
    *,
    config_path: Path,
    public_projection_path: Path,
    stage_id: str,
    launcher_spec: str,
) -> TrialResult:
    config = RunConfig.from_mapping(_load_json(config_path))
    projection_data = _load_json(public_projection_path)
    stage = PublicStageProjection.from_task_projection(projection_data, stage_id)

    launch_function = _load_callable(launcher_spec)
    launched = launch_function(config)
    if inspect.isawaitable(launched):
        launched = await launched
    if not isinstance(launched, TrialLaunch):
        raise TypeError("launcher must return TrialLaunch(backend, isolation_preflight)")
    try:
        preflight = launched.isolation_preflight
        if not isinstance(preflight, IsolationPreflight):
            preflight = IsolationPreflight.from_mapping(preflight)
        return await run_agent_loop(config, stage, launched.backend, preflight)
    finally:
        if launched.close is not None:
            closed = launched.close()
            if inspect.isawaitable(closed):
                await closed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aiad-benchmark-harness",
        description="Run exactly one benchmark stage through an isolated MCP backend.",
    )
    parser.add_argument("--config", type=Path, required=True, help="Explicit per-trial JSON run config")
    parser.add_argument("--public-projection", type=Path, required=True, help="Public task projection JSON only")
    parser.add_argument("--stage", required=True, help="Stage id within the public projection")
    parser.add_argument(
        "--launcher",
        required=True,
        help="Parent-owned async launcher in module:function form; it must run isolation probes",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(
            run_from_files(
                config_path=args.config,
                public_projection_path=args.public_projection,
                stage_id=args.stage,
                launcher_spec=args.launcher,
            )
        )
    except Exception as error:
        print(
            json.dumps(
                {"state": "harness_error", "error": safe_exception_message(error)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result.as_dict(), sort_keys=True, ensure_ascii=False))
    return 0
