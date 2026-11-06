"""Runs one post-build operation in a fresh, killable kernel process."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aad_render.render_limits import (
    MAX_PREPARED_RENDER_BYTES,
    MAX_RENDER_PAYLOAD_BYTES,
    MAX_RENDER_RESULT_BYTES,
    MAX_WORKER_STDERR_BYTES,
    MAX_WORKER_STDOUT_BYTES,
)

from ..ops import PREPARED_RENDER_FILENAME, RESULT_MARKER, _render_result

DEFAULT_TIMEOUT_S = 120.0


def _package_roots() -> list[Path]:
    """Editable installs import from each package's src dir, which Python must be able to list."""
    roots = []
    for name in ("aad_platform", "aad_render", "aad_drafting", "aad_quality"):
        spec = importlib.util.find_spec(name)
        if spec is not None and spec.origin is not None:
            roots.append(Path(spec.origin).parents[1])
            if name == "aad_quality":
                from aad_quality.catalog import _guidance_root

                # Editable installs keep playbooks outside src; workers need the
                # same read-only guidance that is bundled into release wheels.
                roots.append(Path(str(_guidance_root())))
                # The catalog checks this marker to locate editable guidance.
                # Linux mount namespaces also need the file for is_file(), while
                # macOS already grants file-read-metadata outside read subpaths.
                marker = Path(spec.origin).parents[2] / "pyproject.toml"
                if marker.is_file():
                    roots.append(marker)
    return roots


def _run_sandboxed(request: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    """Blocking post-build operation; only the final VTK renderer runs outside the OS sandbox."""
    try:
        from aad.engine import sandbox
    except ImportError as exc:
        return _crash("WORKER_CRASH", "crash", f"The AAD engine is not installed: {exc}. Run aad doctor.")

    if request.get("op") == "render":
        if os.environ.get("AAD_REMOTE"):
            return _crash("SANDBOX_DENIED", "sandbox", "rendering needs a local graphics worker that remote mode does not grant")
        if request.get("kind", "part") != "2d":
            return _run_mesh_render(request, timeout_s, sandbox)

    return _run_worker(request, timeout_s, sandbox)


def _run_worker(request: dict[str, Any], timeout_s: float, sandbox: Any) -> dict[str, Any]:
    """Run an operation under the unchanged OS policy, with the usual timeout and kill behavior."""
    out = Path(request["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    reads = [*(Path(path) for path in request.get("read_paths", [])), *_package_roots()]
    argv = [sys.executable, "-m", "aad_platform.ops"]
    try:
        process = sandbox.popen(argv, out, read_paths=reads)
    except (sandbox.SandboxUnavailable, OSError) as exc:
        # Never fall back to an unsandboxed run.
        return _crash("SANDBOX_DENIED", "sandbox", f"The OS sandbox could not start this operation: {exc}")
    result = _communicate_result(
        process,
        json.dumps(request, default=str).encode(),
        timeout_s,
        stdout_limit=MAX_PREPARED_RENDER_BYTES + 1024 if request.get("op") == "render_prepare" else MAX_WORKER_STDOUT_BYTES,
        stderr_limit=MAX_WORKER_STDERR_BYTES,
        failure_phase="render" if request.get("op") in {"render", "render_prepare"} else "crash",
    )
    if not result.get("ok") and request.get("op") in {"render", "render_prepare"}:
        return _as_render_failure(result)
    return result


def _run_mesh_render(request: dict[str, Any], timeout_s: float, sandbox: Any) -> dict[str, Any]:
    """Parse the B-rep under the OS sandbox, then give a clean VTK worker render data only."""
    deadline = time.monotonic() + timeout_s
    prepare_request = {**request, "op": "render_prepare"}
    prepared_result = _run_worker(prepare_request, timeout_s, sandbox)
    out = Path(request["out_dir"])
    payload_path = out / PREPARED_RENDER_FILENAME
    if not prepared_result.get("ok"):
        _discard_prepared_render_payload(payload_path)
        return _as_render_failure(prepared_result)
    prepared = prepared_result.get("prepared_render")
    if (
        not isinstance(prepared, dict)
        or set(prepared) != {"version", "render", "inspection", "render_payload_file"}
        or prepared.get("version") != 1
        or prepared.get("render_payload_file") != PREPARED_RENDER_FILENAME
        or not isinstance(prepared.get("render"), dict)
        or not isinstance(prepared["render"].get("request"), dict)
    ):
        _discard_prepared_render_payload(payload_path)
        return _crash("WORKER_CRASH", "render", "The sandboxed worker returned no prepared mesh payload.")
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _crash("TIMEOUT", "timeout", f"The operation did not finish within {timeout_s:.0f} s.")
        input_data = _read_prepared_render_payload(payload_path)
    except (OSError, ValueError) as exc:
        return _crash("INVALID_RENDER_PAYLOAD", "render", f"Could not read the sandboxed render payload: {exc}")
    finally:
        _discard_prepared_render_payload(payload_path)

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return _crash("TIMEOUT", "timeout", f"The operation did not finish within {timeout_s:.0f} s.")
    argv = [sys.executable, "-m", "aad_platform.render_mesh", str(out)]
    try:
        process = subprocess.Popen(
            argv,
            cwd=out,
            env=sandbox.environment(out),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        return _crash("WORKER_CRASH", "render", f"The mesh renderer could not start: {exc}")
    rendered_result = _communicate_result(
        process,
        input_data,
        remaining,
        stdout_limit=MAX_RENDER_RESULT_BYTES + len(RESULT_MARKER) + 1,
        stderr_limit=MAX_WORKER_STDERR_BYTES,
        failure_phase="render",
    )
    if not rendered_result.get("ok"):
        return _as_render_failure(rendered_result)
    rendered = rendered_result.get("rendered")
    if not isinstance(rendered, dict):
        return _crash("WORKER_CRASH", "render", "The mesh renderer returned no image metadata.")
    try:
        from aad_render import finish_render

        paths = finish_render(prepared, rendered, out)
        return {"ok": True, **_render_result(paths)}
    except Exception as exc:
        return _crash("RENDER_FAILED", "render", f"The render report could not be completed: {exc}")


def _read_prepared_render_payload(path: Path) -> bytes:
    """Read the sandboxed mesh sidecar once, with a fixed cap and no link following."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("safe render payload reads require O_NOFOLLOW support")
    fd = os.open(path, os.O_RDONLY | nofollow)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RENDER_PAYLOAD_BYTES:
            raise ValueError(f"render payload is not a regular file within the {MAX_RENDER_PAYLOAD_BYTES}-byte limit")
        data = bytearray()
        while len(data) <= MAX_RENDER_PAYLOAD_BYTES:
            chunk = os.read(fd, min(64 * 1024, MAX_RENDER_PAYLOAD_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_RENDER_PAYLOAD_BYTES:
            raise ValueError(f"render payload exceeds the {MAX_RENDER_PAYLOAD_BYTES}-byte limit")
        return bytes(data)
    finally:
        os.close(fd)


def _discard_prepared_render_payload(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _as_render_failure(result: dict[str, Any]) -> dict[str, Any]:
    """Keep input, sandbox and timeout failures distinct from renderer failures."""
    failure = result.get("failure")
    if not isinstance(failure, dict):
        return _crash("WORKER_CRASH", "render", "The render worker failed without a failure report.")
    if failure.get("phase") not in {"input", "sandbox", "timeout"}:
        failure = {**failure, "phase": "render"}
    return {**result, "failure": failure}


def _communicate_result(
    process: subprocess.Popen[bytes],
    input_data: bytes,
    timeout_s: float,
    *,
    stdout_limit: int = MAX_WORKER_STDOUT_BYTES,
    stderr_limit: int = MAX_WORKER_STDERR_BYTES,
    failure_phase: str = "crash",
) -> dict[str, Any]:
    try:
        if all(getattr(process, name, None) is not None for name in ("stdin", "stdout", "stderr")):
            stdout, stderr = _bounded_communicate(
                process,
                input_data,
                timeout_s,
                stdout_limit=stdout_limit,
                stderr_limit=stderr_limit,
            )
        else:
            # Lightweight process fakes in unit tests keep the historical interface.
            stdout, stderr = process.communicate(input_data, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill(process)
        _reap(process)
        return _crash("TIMEOUT", "timeout", f"The operation did not finish within {timeout_s:.0f} s.")
    except _WorkerOutputLimit as exc:
        _kill(process)
        _reap(process)
        return _crash("WORKER_OUTPUT_LIMIT", failure_phase, str(exc))
    except (OSError, ValueError) as exc:
        _kill(process)
        _reap(process)
        return _crash("WORKER_CRASH", failure_phase, f"Could not read worker output: {exc}")
    marker_at = stdout.rfind(RESULT_MARKER)
    while marker_at > 0 and stdout[marker_at - 1] != 10:
        marker_at = stdout.rfind(RESULT_MARKER, 0, marker_at)
    if marker_at >= 0:
        line_start = marker_at + len(RESULT_MARKER)
        line_end = stdout.find(b"\n", line_start)
        if line_end < 0:
            line_end = len(stdout)
        try:
            result = json.loads(stdout[line_start:line_end], parse_constant=_reject_json_constant)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            pass
    detail = stderr.decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
    return _crash("WORKER_CRASH", failure_phase, f"The worker exited with code {process.returncode}: {detail[0]}")


class _WorkerOutputLimit(Exception):
    pass


def _bounded_communicate(
    process: subprocess.Popen[bytes],
    input_data: bytes,
    timeout_s: float,
    *,
    stdout_limit: int,
    stderr_limit: int,
) -> tuple[bytes, bytes]:
    """Stream subprocess pipes with fixed memory bounds and a shared deadline."""
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    buffers = {"stdout": (process.stdout, stdout, stdout_limit), "stderr": (process.stderr, stderr, stderr_limit)}
    deadline = time.monotonic() + timeout_s
    stdin_fd = process.stdin.fileno()
    remaining_input = memoryview(input_data)
    try:
        os.set_blocking(stdin_fd, False)
        for name, (stream, _buffer, _limit) in buffers.items():
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        if remaining_input:
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout_s)
            events = selector.select(min(remaining, 0.5))
            for key, _mask in events:
                if key.data == "stdin":
                    try:
                        written = os.write(stdin_fd, remaining_input[:64 * 1024])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        remaining_input = memoryview(b"")
                        selector.unregister(process.stdin)
                        process.stdin.close()
                        continue
                    remaining_input = remaining_input[written:]
                    if not remaining_input:
                        selector.unregister(process.stdin)
                        process.stdin.close()
                    continue

                stream, buffer, limit = buffers[key.data]
                available = limit - len(buffer)
                chunk = os.read(stream.fileno(), min(64 * 1024, available + 1))
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                if len(chunk) > available:
                    raise _WorkerOutputLimit(f"worker {key.data} exceeded its {limit}-byte output limit")
                buffer.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_s)
        process.wait(timeout=remaining)
        return bytes(stdout), bytes(stderr)
    finally:
        selector.close()


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number {token} is not allowed")


def _reap(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(process, name, None)
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


async def run_op(request: dict[str, Any], *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    return await asyncio.to_thread(_run_sandboxed, request, timeout_s)


def _kill(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _crash(code: str, phase: str, message: str) -> dict[str, Any]:
    return {"ok": False, "failure": {"code": code, "phase": phase, "message": message}}


def job_request(project: Any, rev: int, op: str, args: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    meta = project.meta()
    read_paths = [str(project.path / ".aad" / "revs"), *_extra_reads(args)]
    if op == "check":
        read_paths.extend(_spec_reference_reads(project, rev, args))
    return {
        "op": op,
        "project_dir": str(project.path),
        "project": project.name,
        "rev": rev,
        "kind": meta.get("kind", "part"),
        "units": meta.get("units", "mm"),
        "material": meta.get("material"),
        "process": meta.get("process"),
        "args": args,
        "out_dir": str(out_dir),
        "read_paths": list(dict.fromkeys(read_paths)),
    }


def _extra_reads(args: dict[str, Any]) -> list[str]:
    """Operations read one explicitly selected file, and nothing else outside revisions."""
    path = args.get("path")
    reference = args.get("reference_path")
    reads = [str(path)] if path else []
    if reference:
        try:
            resolved = Path(reference).expanduser().resolve(strict=True)
            if resolved.is_file():
                reads.append(str(resolved))
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
    return reads


def _spec_reference_reads(project: Any, rev: int, args: dict[str, Any]) -> list[str]:
    # An empty list means the default suites, which include spec.
    suites = args.get("suites")
    if suites and "spec" not in suites:
        return []
    spec_path = project.path / ".aad" / "revs" / f"{rev:06d}" / "spec.yaml"
    try:
        if spec_path.stat().st_size > 1_048_576:
            return []
        from aad_quality.compare import resolve_project_reference
        from aad_quality.schema import load_spec

        document = load_spec(spec_path.read_bytes())
    except Exception:
        return []
    requirements = document.get("requirements")
    if not isinstance(requirements, list):
        return []
    result: set[str] = set()
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        body = requirement.get("matches_reference")
        if not isinstance(body, dict):
            continue
        try:
            result.add(str(resolve_project_reference(project.path, body.get("path"))))
        except Exception:
            # The evaluator emits a visible skip; unsafe paths never enter the sandbox allowlist.
            continue
    return sorted(result)
