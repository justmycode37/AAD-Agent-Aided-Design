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


def _import_once(module: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["AIAD_NO_BROWSER"] = "1"
    started = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, "-c", _IMPORT_SCRIPT, module],
            capture_output=True,
            text=True,
            timeout=IMPORT_TIMEOUT_S,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "seconds": time.perf_counter() - started, "error": "import timed out"}
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
    for name in MODULES:
        passes: list[dict[str, Any]] = []
        for pass_name in ("first", "second"):
            if progress is not None:
                progress(f"Pre-warming {name} ({pass_name} pass)…")
            result = _import_once(name)
            passes.append(result)
            if progress is not None:
                seconds = result.get("seconds", 0.0)
                detail = result.get("error")
                progress(f"{name} {pass_name}: {result['status']} in {seconds:.2f} s" + (f" ({detail})" if detail else ""))
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
