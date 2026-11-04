#!/usr/bin/env python3
"""Reproduce the build123d-mcp S5 teardown through its real MCP stdio API."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parents[2]
TASKS = REPO_ROOT / "evals" / "teardown" / "tasks"
OUTPUTS = BASE / "outputs"
RAW = OUTPUTS / "raw"
STEP = OUTPUTS / "step"
TEMP = OUTPUTS / "tmp"
SERVER = BASE / "upstream" / ".venv" / "bin" / "build123d-mcp"
PYTHON = BASE / "upstream" / ".venv" / "bin" / "python"
UPSTREAM_COMMIT = "48a7dba3a607ca4f1cab6a8ea798160fdb2c8162"
UPSTREAM_TAG = "v0.3.91"

TASK_FILES = {
    "plate": "plate.py",
    "edit_plate": "edit_plate.py",
    "blocked_counterbore": "blocked_counterbore.py",
}


def ensure_output_dirs() -> None:
    for path in (RAW, STEP, TEMP, BASE / ".cache" / "xdg", BASE / ".cache" / "mpl"):
        path.mkdir(parents=True, exist_ok=True)


def child_environment() -> dict[str, str]:
    # Do not forward unrelated host variables (including any credentials) to the
    # MCP server or its model-code worker. All temporary and graphics cache files
    # stay in this assigned sector directory.
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(TEMP),
        "XDG_CACHE_HOME": str(BASE / ".cache" / "xdg"),
        "MPLCONFIGDIR": str(BASE / ".cache" / "mpl"),
        "PYTHONUNBUFFERED": "1",
    }


@asynccontextmanager
async def open_session(experimental: bool = True):
    args = ["--experimental"] if experimental else []
    params = StdioServerParameters(
        command=str(SERVER),
        args=args,
        cwd=str(REPO_ROOT),
        env=child_environment(),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            started = time.perf_counter_ns()
            initialized = await session.initialize()
            init_ms = (time.perf_counter_ns() - started) / 1_000_000
            yield session, initialized, init_ms


def as_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "__dict__"):
        return value.__dict__
    return value


def text_content(reply: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in reply.get("content", []):
        if item.get("type") == "text":
            chunks.append(item.get("text", ""))
    return "\n".join(chunks)


def embedded_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{" and char != "[":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
            return value
        except json.JSONDecodeError:
            continue
    return None


async def call_tool(
    session: ClientSession,
    calls: list[dict[str, Any]],
    name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = arguments or {}
    started = time.perf_counter_ns()
    try:
        reply = await session.call_tool(name, args)
        serialized = as_jsonable(reply)
        error: str | None = None
    except Exception as exc:  # preserve a transport/server error in the trial record
        serialized = None
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    entry = {
        "tool": name,
        "arguments": args,
        "elapsed_ms": round(elapsed_ms, 3),
        "reply": serialized,
    }
    if error is not None:
        entry["call_error"] = error
    calls.append(entry)
    return entry


def source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_spec(task: str) -> dict[str, Any]:
    if task == "plate":
        return {
            "envelope_mm": {
                "x": [59.99, 60.01],
                "y": [39.99, 40.01],
                "z": [5.99, 6.01],
            },
            "solid": {"count": 1, "valid": True},
            "features": [
                {
                    "kind": "hole",
                    "count": 4,
                    "diameter_mm": 4.4,
                    "depth_mm": 6.0,
                    "through": True,
                }
            ],
        }
    if task == "edit_plate":
        return {
            "envelope_mm": {
                "x": [79.99, 80.01],
                "y": [39.99, 40.01],
                "z": [5.99, 6.01],
            },
            "solid": {"count": 1, "valid": True},
            "features": [
                {
                    "kind": "hole",
                    "count": 4,
                    "diameter_mm": 4.4,
                    "depth_mm": 6.0,
                    "through": True,
                }
            ],
        }
    return {
        "envelope_mm": {
            "x": [59.99, 60.01],
            "y": [39.99, 40.01],
            "z": [21.99, 22.01],
        },
        "solid": {"count": 1, "valid": True},
        "features": [
            {
                "kind": "hole",
                "count": 1,
                "diameter_mm": 4.4,
                # The recognizer reports the through-bore cylinder after the
                # counterbore (3 mm here); `through` records that it exits the
                # opposite plate face.
                "depth_mm": 3.0,
                "through": True,
                "counterbore": {"diameter_mm": 9.0, "depth_mm": 3.0},
            }
        ],
    }


async def inventory() -> dict[str, Any]:
    surfaces: dict[str, Any] = {}
    for mode, experimental in (("default", False), ("experimental", True)):
        async with open_session(experimental=experimental) as (session, initialized, init_ms):
            listed = await session.list_tools()
            tools = [as_jsonable(tool) for tool in listed.tools]
            surfaces[mode] = {
                "initialize_ms": round(init_ms, 3),
                "server_info": as_jsonable(initialized.server_info),
                "tools": tools,
            }
            if mode == "default":
                call_log: list[dict[str, Any]] = []
                version_reply = await call_tool(session, call_log, "version")
                surfaces[mode]["probe_calls"] = call_log
                surfaces[mode]["version_text"] = (
                    text_content(version_reply["reply"]) if version_reply["reply"] else None
                )
    path = OUTPUTS / "tool_inventory.json"
    path.write_text(json.dumps(surfaces, indent=2, sort_keys=True) + "\n")
    return surfaces


async def run_trial(task: str, repeat: int, run_id: str) -> dict[str, Any]:
    filename = TASK_FILES[task]
    task_path = TASKS / filename
    calls: list[dict[str, Any]] = []
    trial_started = time.perf_counter_ns()
    step_path = STEP / task / f"{task}-r{repeat}.step"
    step_path.parent.mkdir(parents=True, exist_ok=True)
    # A failed fresh export must never inherit an artifact from an earlier run.
    step_path.unlink(missing_ok=True)
    bank_path = STEP / task / f"{task}-r{repeat}.bank.step"
    bank_path.unlink(missing_ok=True)

    async with open_session(experimental=True) as (session, initialized, init_ms):
        if task == "plate":
            await call_tool(
                session,
                calls,
                "execute_file",
                {"path": str(task_path), "result_name": "result"},
            )
        elif task == "edit_plate":
            await call_tool(
                session,
                calls,
                "execute_file",
                {
                    "path": str(TASKS / "plate.py"),
                    "result_name": "result",
                    "snapshot": "before_edit",
                },
            )
            await call_tool(
                session,
                calls,
                "execute_file",
                {
                    "path": str(task_path),
                    "result_name": "result",
                    "snapshot": "after_edit",
                },
            )
            await call_tool(
                session,
                calls,
                "compare",
                {"a": "before_edit", "kind": "snapshot", "format": "json"},
            )
        else:
            await call_tool(
                session,
                calls,
                "execute_file",
                {"path": str(task_path), "result_name": "result"},
            )

        await call_tool(session, calls, "measure")
        await call_tool(session, calls, "find_holes")
        await call_tool(session, calls, "validate")
        if task == "blocked_counterbore":
            # First asks only about shape intent and the recognised counterbore.
            # A second, explicitly caller-authored point probe checks the occupied
            # screw approach at (0, 0, 19 mm); this is evidence of that point only,
            # not a general swept-tool access analysis.
            await call_tool(
                session,
                calls,
                "verify_spec",
                {"spec": json.dumps(verify_spec(task), separators=(",", ":"))},
            )
            access_spec = {
                "features": [
                    {"kind": "material_at_point", "point": [0, 0, 19], "expect": "void"}
                ]
            }
            await call_tool(
                session,
                calls,
                "verify_spec",
                {"spec": json.dumps(access_spec, separators=(",", ":"))},
            )
            # Preserve the MCP tool's exact call name by labelling this occurrence
            # after serialization below.
            calls[-1]["analysis_role"] = "verify_access_probe"
        else:
            await call_tool(
                session,
                calls,
                "verify_spec",
                {"spec": json.dumps(verify_spec(task), separators=(",", ":"))},
            )

        if task == "blocked_counterbore":
            await call_tool(session, calls, "export", {"filename": str(step_path), "format": "step"})
            # bank_candidate is the competitor's atomic written-STEP promotion
            # gate. It is run after the explicit point probe to check whether
            # that intent failure is connected to export promotion.
            await call_tool(session, calls, "bank_candidate", {"filename": str(bank_path)})
        else:
            await call_tool(session, calls, "export", {"filename": str(step_path), "format": "step"})

        await call_tool(session, calls, "import_cad_file", {"path": str(step_path), "name": "step_copy"})
        await call_tool(session, calls, "validate", {"object_name": "step_copy"})
        calls[-1]["analysis_role"] = "validate_step"
        await call_tool(session, calls, "measure", {"object_name": "step_copy"})
        calls[-1]["analysis_role"] = "measure_step"
        if task == "blocked_counterbore":
            await call_tool(
                session,
                calls,
                "verify_spec",
                {
                    "spec": json.dumps(verify_spec(task), separators=(",", ":")),
                    "object_name": "step_copy",
                },
            )
            calls[-1]["analysis_role"] = "verify_spec_step"
            access_spec = {
                "features": [
                    {"kind": "material_at_point", "point": [0, 0, 19], "expect": "void"}
                ]
            }
            await call_tool(
                session,
                calls,
                "verify_spec",
                {"spec": json.dumps(access_spec, separators=(",", ":")), "object_name": "step_copy"},
            )
            calls[-1]["analysis_role"] = "verify_access_probe_step"
        else:
            await call_tool(
                session,
                calls,
                "verify_spec",
                {
                    "spec": json.dumps(verify_spec(task), separators=(",", ":")),
                    "object_name": "step_copy",
                },
            )
            calls[-1]["analysis_role"] = "verify_spec_step"

        # The loop above labels a small number of calls by their role; make those
        # roles consistent for parsing while retaining the exact MCP tool name.
        for item in calls:
            if item.get("analysis_role") == "verify_access_probe":
                item["tool_role"] = item["analysis_role"]
            elif item.get("analysis_role") in ("verify_access_probe_step", "verify_spec_step"):
                item["tool_role"] = item["analysis_role"]
            elif item["tool"] == "validate" and item.get("arguments", {}).get("object_name") == "step_copy":
                item["tool_role"] = "validate_step"
            elif item["tool"] == "measure" and item.get("arguments", {}).get("object_name") == "step_copy":
                item["tool_role"] = "measure_step"
            elif item["tool"] == "verify_spec":
                item["tool_role"] = "verify_spec"
            else:
                item["tool_role"] = item["tool"]

    mcp_elapsed_ms = (time.perf_counter_ns() - trial_started) / 1_000_000
    independent_started = time.perf_counter_ns()
    independent = independent_step_check(step_path, task) if step_path.exists() else None
    independent_elapsed_ms = (time.perf_counter_ns() - independent_started) / 1_000_000
    summary = tool_summary_by_role(calls)
    summary["independent_step_check"] = independent
    summary["independent_step_check_ms"] = round(independent_elapsed_ms, 3)
    record = {
        "run_id": run_id,
        "task": task,
        "repeat": repeat,
        "task_source": str(task_path),
        "task_source_sha256": source_hash(task_path),
        "mcp_server": f"build123d-mcp {UPSTREAM_TAG}",
        "initialize_ms": round(init_ms, 3),
        "elapsed_total_ms": round(mcp_elapsed_ms, 3),
        "independent_step_check_ms": round(independent_elapsed_ms, 3),
        "calls": calls,
        "summary": summary,
        "step_path": str(step_path),
        "step_bytes": step_path.stat().st_size if step_path.exists() else None,
        "step_sha256": hashlib.sha256(step_path.read_bytes()).hexdigest() if step_path.exists() else None,
    }
    raw_path = RAW / f"{task}-r{repeat}.json"
    raw_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def call_text(entry: dict[str, Any]) -> str:
    reply = entry.get("reply")
    return text_content(reply) if isinstance(reply, dict) else ""


def tool_summary_by_role(calls: list[dict[str, Any]]) -> dict[str, Any]:
    by_role: dict[str, dict[str, Any]] = {}
    for item in calls:
        by_role[item.get("tool_role", item["tool"])] = item

    def report(role: str) -> Any:
        item = by_role.get(role)
        return embedded_json(call_text(item)) if item else None

    measure_report = report("measure")
    validate_report = report("validate")
    holes_report = report("find_holes")
    spec_report = report("verify_spec")
    step_spec_report = report("verify_spec_step")
    access_report = report("verify_access_probe")
    access_step_report = report("verify_access_probe_step")
    final_validate = report("validate_step")
    final_measure = report("measure_step")
    compare_report = report("compare")
    export_reply = call_text(by_role["export"]) if "export" in by_role else ""
    bank_reply = call_text(by_role["bank_candidate"]) if "bank_candidate" in by_role else ""

    holes = holes_report.get("holes", []) if isinstance(holes_report, dict) else []
    centers = sorted(
        [round(float(h["location"][0]), 3), round(float(h["location"][1]), 3)]
        for h in holes
        if h.get("location") and len(h["location"]) >= 2
    )
    return {
        "measure": measure_report,
        "validate": validate_report,
        "find_holes": holes_report,
        "verify_spec": spec_report,
        "verify_spec_step": step_spec_report,
        "verify_access_probe": access_report,
        "verify_access_probe_step": access_step_report,
        "validate_step": final_validate,
        "measure_step": final_measure,
        "compare_snapshot": compare_report,
        "export_text": export_reply,
        "bank_candidate_text": bank_reply,
        "observed_hole_count": holes_report.get("count") if isinstance(holes_report, dict) else None,
        "observed_hole_centers_xy_mm": centers,
        "mcp_call_timings_ms": [
            {
                "tool": item["tool"],
                "role": item.get("tool_role", item["tool"]),
                "elapsed_ms": item["elapsed_ms"],
            }
            for item in calls
        ],
    }


def independent_step_check(path: Path, task: str) -> dict[str, Any]:
    """Re-import an exported STEP outside the MCP server and inspect its B-rep."""
    from build123d import Vector, import_step
    from OCP.BRepCheck import BRepCheck_Analyzer

    shape = import_step(str(path))
    bbox = shape.bounding_box()
    actual_bbox = [round(bbox.size.X, 4), round(bbox.size.Y, 4), round(bbox.size.Z, 4)]
    expected_bbox = {
        "plate": [60.0, 40.0, 6.0],
        "edit_plate": [80.0, 40.0, 6.0],
        "blocked_counterbore": [60.0, 40.0, 22.0],
    }[task]
    # This uses the released drafting recognizer directly on a fresh STEP import,
    # outside the MCP process. It is a separate artifact check, not a second MCP
    # response and not a design-certification claim.
    from build123d_drafting import find_holes

    holes = list(find_holes(shape))
    expected_holes = 1 if task == "blocked_counterbore" else 4
    inside_obstruction: bool | None = None
    if task == "blocked_counterbore":
        inside_obstruction = bool(shape.is_inside(Vector(0, 0, 19)))
    return {
        "step_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "brepcheck_valid": bool(BRepCheck_Analyzer(shape.wrapped).IsValid()),
        "solid_count": len(shape.solids()),
        "bbox_mm": actual_bbox,
        "expected_bbox_mm": expected_bbox,
        "bbox_matches_task_dimensions": all(
            abs(actual - expected) <= 0.01 for actual, expected in zip(actual_bbox, expected_bbox)
        ),
        "volume_mm3": round(shape.volume, 4),
        "hole_count_recognized_after_direct_step_import": len(holes),
        "expected_hole_count": expected_holes,
        "hole_diameters_mm_after_direct_step_import": sorted(
            round(float(hole.diameter), 4) for hole in holes
        ),
        "center_0_0_z19_is_inside_solid": inside_obstruction,
    }


def environment_record() -> dict[str, Any]:
    packages = (
        "build123d-mcp",
        "build123d",
        "cadquery-ocp-novtk",
        "mcp",
        "bd_warehouse",
        "vtk",
        "quiddity",
        "augura",
    )
    distributions: dict[str, str] = {}
    for package in packages:
        try:
            distributions[package] = version(package)
        except PackageNotFoundError:
            distributions[package] = "not-installed"
    uv = shutil.which("uv")
    uv_version = None
    if uv:
        completed = subprocess.run([uv, "--version"], check=False, capture_output=True, text=True)
        uv_version = completed.stdout.strip() or completed.stderr.strip()
    lock_path = BASE / "upstream" / "uv.lock"
    return {
        "python": platform.python_version(),
        "python_executable": str(Path(sys.executable).resolve()),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "uv_version": uv_version,
        "upstream_tag": UPSTREAM_TAG,
        "upstream_commit": UPSTREAM_COMMIT,
        "source_pyproject_version": "0.3.91.dev0",
        "resolved_distributions": distributions,
        "resolved_uv_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.exists() else None,
    }


def summarize_inventory(surfaces: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for mode, data in surfaces.items():
        summary[mode] = {
            "tool_count": len(data["tools"]),
            "tool_names": sorted(item["name"] for item in data["tools"]),
            "experimental_only": sorted(
                item["name"]
                for item in data["tools"]
                if item["name"] in ("verify_spec", "suggest_spec")
            ),
        }
    return summary


def make_results(records: list[dict[str, Any]], surfaces: dict[str, Any], run_id: str) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    for task in TASK_FILES:
        samples = [r for r in records if r["task"] == task]
        successful = [r for r in samples if r.get("step_bytes")]
        tasks[task] = {
            "repetitions": [
                {
                    "repeat": r["repeat"],
                    "elapsed_total_ms": r["elapsed_total_ms"],
                    "initialize_ms": r["initialize_ms"],
                    "tool_timings_ms": r["summary"]["mcp_call_timings_ms"],
                    "step_path": str(Path(r["step_path"]).relative_to(BASE)),
                    "step_bytes": r["step_bytes"],
                    "step_sha256": r["step_sha256"],
                    "source_sha256": r["task_source_sha256"],
                    "mcp_call_count": len(r["calls"]),
                    "valid": r["summary"]["validate"],
                    "spec": r["summary"]["verify_spec"],
                    "step_reimport_valid": r["summary"]["validate_step"],
                    "step_reimport_measure": r["summary"]["measure_step"],
                    "step_reimport_spec": r["summary"]["verify_spec_step"],
                    "independent_step_check": r["summary"]["independent_step_check"],
                    "independent_step_check_ms": r["independent_step_check_ms"],
                    "access_probe": r["summary"]["verify_access_probe"],
                    "access_probe_after_step_reimport": r["summary"]["verify_access_probe_step"],
                    "snapshot_comparison": r["summary"]["compare_snapshot"],
                    "bank_candidate_text": r["summary"]["bank_candidate_text"],
                    "raw_reply_file": str(Path(RAW / f"{task}-r{r['repeat']}.json").relative_to(BASE)),
                }
                for r in samples
            ],
            "step_files_created": len(successful),
        }
    return {
        "run_id": run_id,
        "complete": True,
        "title": "build123d-mcp teardown: shared MCP task replies and structural checks",
        "generated_at_local": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "environment": environment_record(),
        "reproduction": {
            "checkout": "git clone --branch v0.3.91 --depth 1 https://github.com/pzfreo/build123d-mcp.git upstream",
            "install": "UV_CACHE_DIR=<build123d_mcp>/.cache uv sync --project upstream --python 3.12 --no-dev",
            "run": "upstream/.venv/bin/python reproduce.py --repetitions 3",
            "mcp_transport": "Python mcp.ClientSession + mcp.client.stdio.stdio_client; server launched as a subprocess over stdio with --experimental for task calls",
            "tasks": {
                task: {
                    "file": f"../tasks/{filename}",
                    "sha256": source_hash(TASKS / filename),
                }
                for task, filename in TASK_FILES.items()
            },
        },
        "tool_inventory": summarize_inventory(surfaces),
        "task_results": tasks,
        "interpretation_limits": [
            "These are deterministic tool exercises, not agent benchmark results.",
            "Kernel validity and STEP export validity do not prove design intent or manufacturability.",
            "The blocked-counterbore probe is one caller-authored point occupancy check, not swept-tool or screw-access analysis.",
            "Timings are single-machine whole MCP sequences; summarize.py reports sample SD across repeats, not model-quality variance.",
        ],
    }


async def main(repetitions: int) -> None:
    run_id = str(uuid.uuid4())
    (BASE / 'results.json').unlink(missing_ok=True)
    (BASE / 'audit.json').unlink(missing_ok=True)
    actual_commit = subprocess.check_output(['git', '-C', str(BASE/'upstream'), 'rev-parse', 'HEAD'], text=True).strip()
    if actual_commit != UPSTREAM_COMMIT:
        raise SystemExit('Wrong upstream commit: '+actual_commit)
    subprocess.run(['git','-C',str(BASE/'upstream'),'diff','--exit-code','HEAD','--'],check=True,capture_output=True)
    if not SERVER.exists():
        raise SystemExit(f"Missing server executable: {SERVER}. Install the pinned checkout first.")
    ensure_output_dirs()
    surfaces = await inventory()
    all_records: list[dict[str, Any]] = []
    for task in TASK_FILES:
        for repeat in range(1, repetitions + 1):
            record=await run_trial(task, repeat, run_id)
            if not record['step_bytes'] or any(
                call.get('call_error') or (call.get('reply') or {}).get('isError') for call in record['calls']
            ):
                raise SystemExit('A task had a transport/tool error or no fresh STEP; inspect raw replies')
            summary=record['summary']
            if not summary['verify_spec']['summary']['conforms']:
                raise SystemExit('The explicitly declared size/hole requirements did not pass')
            if record['task']=='blocked_counterbore' and summary['verify_access_probe']['summary']['conforms']:
                raise SystemExit('The negative access probe unexpectedly passed')
            all_records.append(record)
            print(f"saved {task} repetition {repeat}", flush=True)
    result = make_results(all_records, surfaces, run_id)
    result_path = BASE / "results.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (OUTPUTS / "all_trials.json").write_text(
        json.dumps(all_records, indent=2, sort_keys=True) + "\n"
    )
    print(result_path)


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    asyncio.run(main(args.repetitions))


if __name__ == "__main__":
    cli()
