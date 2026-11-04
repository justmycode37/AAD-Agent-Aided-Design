"""Measure heavyweight imports during installation, outside MCP startup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

MODULES = (
    "build123d",
    "OCP",
    "aiad.engine.jobs",
    "aiad_render",
    "aiad_drafting",
    "aiad_quality",
    "vtk",
)
REQUIRED_MODULES = frozenset({"build123d", "OCP"})
IMPORT_TIMEOUT_S = 180.0
TOTAL_TIMEOUT_S = 120.0

_IMPORT_SCRIPT = r"""
import importlib, json, sys, time
name = sys.argv[1]
started = time.perf_counter()
try:
    importlib.import_module(name)
except ModuleNotFoundError as exc:
    missing = exc.name == name or name.startswith((exc.name or "") + ".")
    print(json.dumps({"status": "missing" if missing else "fail", "seconds": time.perf_counter() - started, "error": str(exc)}))
    raise SystemExit(0 if missing else 1)
except Exception as exc:
    print(json.dumps({"status": "fail", "seconds": time.perf_counter() - started, "error": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(1)
print(json.dumps({"status": "pass", "seconds": time.perf_counter() - started}))
"""


def _import_once(module: str, *, timeout_s: float = IMPORT_TIMEOUT_S) -> dict[str, Any]:
    env = os.environ.copy()
    env["AIAD_NO_BROWSER"] = "1"
    started = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, "-c", _IMPORT_SCRIPT, module],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "seconds": time.perf_counter() - started, "error": "import timed out", "timed_out": True}
    except OSError as exc:
        return {"status": "fail", "seconds": time.perf_counter() - started, "error": str(exc)}
    output = result.stdout.strip().splitlines()
    if output:
        try:
            value = json.loads(output[-1])
            if isinstance(value, dict) and "status" in value:
                value["seconds"] = float(value.get("seconds", time.perf_counter() - started))
                return value
        except (ValueError, TypeError):
            pass
    detail = result.stderr.strip() or result.stdout.strip() or f"import exited with status {result.returncode}"
    return {"status": "fail", "seconds": time.perf_counter() - started, "error": detail[-1200:]}


def run_prewarm(progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Import each package twice in separate subprocesses and report both timings."""
    modules: list[dict[str, Any]] = []
    deadline = time.monotonic() + TOTAL_TIMEOUT_S
    for name in MODULES:
        if time.monotonic() >= deadline:
            return {"ok": False, "modules": modules, "error": f"Pre-warm exceeded its {TOTAL_TIMEOUT_S:.0f} s total budget."}
        passes: list[dict[str, Any]] = []
        for pass_name in ("first", "second"):
            if progress is not None:
                progress(f"Pre-warming {name} ({pass_name} pass)…")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result = {"status": "fail", "seconds": 0.0, "error": "pre-warm time budget exhausted", "timed_out": True}
            else:
                result = _import_once(name, timeout_s=min(IMPORT_TIMEOUT_S, remaining))
            passes.append(result)
            if progress is not None:
                seconds = result.get("seconds", 0.0)
                detail = result.get("error")
                progress(f"{name} {pass_name}: {result['status']} in {seconds:.2f} s" + (f" ({detail})" if detail else ""))
            if result.get("timed_out"):
                if len(passes) == 1:
                    passes.append({"status": "skip", "seconds": 0.0, "error": "First import timed out; second pass skipped."})
                break
        statuses = {result["status"] for result in passes}
        if "fail" in statuses or ("missing" in statuses and name in REQUIRED_MODULES):
            status = "fail"
        elif "missing" in statuses:
            status = "missing"
        else:
            status = "pass"
        modules.append(
            {
                "name": name,
                "status": status,
                "first_s": round(float(passes[0].get("seconds", 0.0)), 3),
                "second_s": round(float(passes[1].get("seconds", 0.0)), 3),
                "first": passes[0],
                "second": passes[1],
            }
        )
    return {"ok": all(module["status"] != "fail" for module in modules), "modules": modules}
