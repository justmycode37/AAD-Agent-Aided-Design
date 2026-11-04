"""Optional native rendering in a separate, bounded OS-sandboxed process.

The parent imports no kernel here. The child reloads only the BREP, never the
model source or model-authored modules. Its result cannot promote build status.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from . import sandbox
from .types import BuildResult, Check

_MARKER = b"AIAD_PREVIEW_RESULT:"
_TIMEOUT_S = 10.0  # Optional-work budget, not a renderer capability limit.
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from aiad.engine._preview import main; main()"
)


def add_preview(result: BuildResult, *, timeout_s: float) -> None:
    """Add private PNGs or a warning while retaining all geometry and checks."""
    from .jobs import _canonical_artifact, _check_from_wire, _decode, _drain_killed_worker, _kill_worker

    artifacts: dict[str, str] = {}
    check = _warning("render_failed", "Agent rendering did not complete; the built BREP artifact was retained.")
    stdout, stderr = b"", b""
    process = None
    try:
        budget_s = min(_TIMEOUT_S, timeout_s)
        if budget_s <= 0:
            check = _warning("render_timeout", "No build-time budget remained for the optional preview; the built BREP artifact was retained.")
        else:
            job = Path(result.job_dir).resolve(strict=True)
            brep = _canonical_artifact(Path(result.artifacts["part.brep"]), job)
            if brep is None or result.context is None:
                raise ValueError("preview requires a private BREP and model context")
            # Created after the model exits. No model output is imported as
            # Python, and no pre-existing model-owned output directory is used.
            preview_job = Path(tempfile.mkdtemp(prefix="preview-", dir=job)).resolve()
            context = asdict(result.context)
            context.update(shape=None, sheet=None, job_dir=str(preview_job))
            payload = json.dumps({"context": context, "brep": str(brep)}, default=str, allow_nan=False).encode() + b"\n"
            process = sandbox.popen(
                [sys.executable, "-I", "-B", "-c", _BOOTSTRAP, str(_SOURCE_ROOT)],
                preview_job, read_paths=(brep,),
            )
            try:
                stdout, stderr = process.communicate(input=payload, timeout=budget_s)
            except subprocess.TimeoutExpired:
                _kill_worker(process)
                stdout, stderr = _drain_killed_worker(process)
                check = _warning("render_timeout", f"The optional preview exceeded its {budget_s:g} second budget and was killed; the built BREP artifact was retained.")
            else:
                if process.returncode != 0:
                    check = _warning("render_failed", f"The optional preview worker exited with status {process.returncode}; no exit cause was inferred. The built BREP artifact was retained.")
                else:
                    wire = _response(stdout)
                    check = _check_from_wire(wire["check"])
                    if check.suite != "hard" or check.level != "warn" or check.id not in {"render_generated", "render_unavailable", "render_failed"}:
                        raise ValueError("preview returned an unsupported check")
                    if not isinstance(wire["artifacts"], dict):
                        raise ValueError("preview artifacts must be an object")
                    for name, value in wire["artifacts"].items():
                        if not isinstance(name, str) or not isinstance(value, str):
                            raise ValueError("preview artifact names and paths must be text")
                        path = _canonical_artifact(Path(value), preview_job)
                        if path is None or path.suffix.lower() != ".png":
                            raise ValueError("preview artifact is not a private PNG")
                        with path.open("rb") as stream:
                            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                                raise ValueError("preview artifact has no PNG signature")
                        artifacts[f"render_{path.name}"] = str(path)
                    if check.id == "render_generated" and (check.status != "pass" or not artifacts):
                        raise ValueError("preview success requires at least one private PNG")
                    if check.id != "render_generated" and check.status not in {"warn", "skip"}:
                        raise ValueError("preview failure cannot report success")
                    if check.id != "render_generated" and artifacts:
                        raise ValueError("preview failure cannot publish artifacts")
    except Exception as exc:
        artifacts = {}
        check = _warning("render_failed", "Agent rendering failed; the built BREP artifact was retained.", actual=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            if process is not None and process.poll() is None:
                _kill_worker(process)
                stdout, stderr = _drain_killed_worker(process)
        except Exception as exc:
            artifacts = {}
            check = _warning("render_failed", "Optional preview cleanup failed; the built BREP artifact was retained.", actual=f"{type(exc).__name__}: {exc}")

    # Replace the internal not-requested placeholder; do not erase unrelated
    # validation checks or change status/requirements_proven/failure.
    retained = []
    for previous in result.checks:
        if (previous.id, previous.suite, previous.level, previous.status) == ("render_not_requested", "hard", "warn", "skip"):
            result.checks_summary[previous.status] = max(0, result.checks_summary.get(previous.status, 0) - 1)
        else:
            retained.append(previous)
    result.checks = retained + [check]
    result.checks_summary[check.status] = result.checks_summary.get(check.status, 0) + 1
    result.artifacts.update(artifacts)
    if stdout:
        result.stdout += "\n[preview worker]\n" + _decode(stdout)[-32768:]
    if stderr:
        result.stderr += "\n[preview worker]\n" + _decode(stderr)[-32768:]
    if result.scene is not None:
        result.scene.checks_summary = dict(result.checks_summary)


def _warning(identifier: str, message: str, *, actual: str | None = None) -> Check:
    return Check(id=identifier, suite="hard", level="warn", status="warn", message=message, actual=actual)


def _response(stdout: bytes) -> dict:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_MARKER):
            wire = json.loads(line[len(_MARKER):])
            if isinstance(wire, dict) and isinstance(wire.get("check"), dict):
                return wire
    raise ValueError("preview worker returned no valid result")


def main() -> None:
    """Trusted child entry point: deserialize geometry, then render it."""
    import contextlib
    from ._worker import _optional_render
    from .jobs import _context_from_wire

    # Keep native fd diagnostics in stderr and protocol output on stdout.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            payload = json.loads(sys.stdin.buffer.readline())
            context = _context_from_wire(payload["context"])
            from build123d import import_brep

            context.shape = import_brep(payload["brep"])
            artifacts, check = _optional_render(context, True)
        except Exception as exc:
            artifacts = {}
            check = _warning("render_failed", "Agent rendering failed; the built BREP artifact was retained.", actual=f"{type(exc).__name__}: {exc}")
    sys.stdout.buffer.write(b"\n" + _MARKER + json.dumps({"check": asdict(check), "artifacts": artifacts}).encode() + b"\n")
    sys.stdout.buffer.flush()
