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

from .. import config
from ..ops import (
    PREPARED_RENDER_FILENAME,
    REVIEW_PACKET_RENDER_FILENAME,
    RESULT_MARKER,
    _render_result,
    _write_private_file,
)

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
        from aad.engine import memory, sandbox
    except ImportError as exc:
        return _crash("WORKER_CRASH", "crash", f"The AAD engine is not installed: {exc}. Run aad doctor.")

    try:
        memory_limit_bytes = config.engine_memory_limit_bytes()
    except (memory.AddressSpaceUnavailable, TypeError, ValueError) as exc:
        return _crash(
            "SANDBOX_DENIED",
            "sandbox",
            f"The configured engine.memory policy could not be resolved: {exc}",
        )

    if request.get("op") == "render":
        if os.environ.get("AAD_REMOTE"):
            return _crash(
                "SANDBOX_DENIED",
                "sandbox",
                "aad_render, including review_packet, needs a local graphics worker; remote mode does not grant one.",
            )
        if request.get("kind", "part") != "2d":
            return _run_mesh_render(request, timeout_s, sandbox, memory, memory_limit_bytes)

    return _run_worker(request, timeout_s, sandbox, memory, memory_limit_bytes)


def _run_worker(
    request: dict[str, Any],
    timeout_s: float,
    sandbox: Any,
    memory: Any,
    memory_limit_bytes: int | None,
) -> dict[str, Any]:
    """Run a sandboxed operation with its configured per-process memory budget."""
    out = Path(request["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    reads = [*(Path(path) for path in request.get("read_paths", [])), *_package_roots()]
    argv = [sys.executable, "-m", "aad_platform.ops"]
    try:
        startup_limit = (
            memory.bootstrap_address_space_budget(memory_limit_bytes)
            if memory_limit_bytes is not None
            else None
        )
        process = sandbox.popen(argv, out, read_paths=reads, address_space=startup_limit)
    except (sandbox.SandboxUnavailable, memory.AddressSpaceUnavailable, OSError) as exc:
        # Never fall back to an unsandboxed run.
        return _crash("SANDBOX_DENIED", "sandbox", f"The operation's sandbox or memory limit could not start: {exc}")
    result = _communicate_result(
        process,
        json.dumps({**request, "memory_limit_bytes": memory_limit_bytes}, default=str).encode(),
        timeout_s,
        stdout_limit=MAX_PREPARED_RENDER_BYTES + 1024 if request.get("op") == "render_prepare" else MAX_WORKER_STDOUT_BYTES,
        stderr_limit=MAX_WORKER_STDERR_BYTES,
        failure_phase="render" if request.get("op") in {"render", "render_prepare"} else "crash",
        memory=memory,
    )
    if not result.get("ok") and request.get("op") in {"render", "render_prepare"}:
        return _as_render_failure(result)
    return result


def _run_mesh_render(
    request: dict[str, Any],
    timeout_s: float,
    sandbox: Any,
    memory: Any,
    memory_limit_bytes: int | None,
) -> dict[str, Any]:
    """Parse the B-rep under the OS sandbox, then give a clean VTK worker render data only."""
    deadline = time.monotonic() + timeout_s
    prepare_request = {**request, "op": "render_prepare"}
    prepared_result = _run_worker(prepare_request, timeout_s, sandbox, memory, memory_limit_bytes)
    out = Path(request["out_dir"])
    requested_review_packet = bool((request.get("args") or {}).get("review_packet"))
    payload_path = out / (REVIEW_PACKET_RENDER_FILENAME if requested_review_packet else PREPARED_RENDER_FILENAME)
    if not prepared_result.get("ok"):
        _discard_prepared_render_payload(payload_path)
        return _as_render_failure(prepared_result)
    prepared = prepared_result.get("prepared_review_packet")
    is_review_packet = prepared is not None
    if not is_review_packet:
        prepared = prepared_result.get("prepared_render")
    if is_review_packet:
        valid_prepared = (
            isinstance(prepared, dict)
            and set(prepared) == {"version", "render_payload_file", "case_ids", "manifest"}
            and prepared.get("version") == 1
            and prepared.get("render_payload_file") == REVIEW_PACKET_RENDER_FILENAME
            and isinstance(prepared.get("case_ids"), list)
            and isinstance(prepared.get("manifest"), dict)
        )
        payload_filename = REVIEW_PACKET_RENDER_FILENAME
    else:
        valid_prepared = (
            isinstance(prepared, dict)
            and set(prepared) == {"version", "render", "inspection", "render_payload_file"}
            and prepared.get("version") == 1
            and prepared.get("render_payload_file") == PREPARED_RENDER_FILENAME
            and isinstance(prepared.get("render"), dict)
            and isinstance(prepared["render"].get("request"), dict)
        )
        payload_filename = PREPARED_RENDER_FILENAME
    if not valid_prepared:
        _discard_prepared_render_payload(payload_path)
        return _crash("WORKER_CRASH", "render", "The sandboxed worker returned no prepared mesh payload.")
    payload_path = out / payload_filename
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
    if memory_limit_bytes is None:
        return _crash(
            "SANDBOX_DENIED",
            "sandbox",
            "The clean mesh renderer requires a supported engine.memory address-space policy.",
        )
    argv = [sys.executable, "-m", "aad_platform.render_mesh", str(out)]
    try:
        startup_limit = memory.bootstrap_address_space_budget(memory_limit_bytes)
        argv = memory._bootstrap_argv(argv, startup_limit, out)
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
    except (memory.AddressSpaceUnavailable, OSError) as exc:
        return _crash("SANDBOX_DENIED", "sandbox", f"The mesh renderer's memory limit could not start: {exc}")
    rendered_result = _communicate_result(
        process,
        input_data,
        remaining,
        stdout_limit=MAX_RENDER_RESULT_BYTES + len(RESULT_MARKER) + 1,
        stderr_limit=MAX_WORKER_STDERR_BYTES,
        failure_phase="render",
        memory=memory,
    )
    if not rendered_result.get("ok"):
        return _as_render_failure(rendered_result)
    rendered = rendered_result.get("rendered")
    if not isinstance(rendered, dict):
        return _crash("WORKER_CRASH", "render", "The mesh renderer returned no image metadata.")
    if is_review_packet:
        try:
            return {"ok": True, "review_packet": _finalize_review_packet(out, prepared, rendered)}
        except Exception as exc:
            return _crash("RENDER_FAILED", "render", f"The review packet could not be finalized: {exc}")
    try:
        from aad_render import finish_render

        paths = finish_render(prepared, rendered, out)
        return {"ok": True, **_render_result(paths)}
    except Exception as exc:
        return _crash("RENDER_FAILED", "render", f"The render report could not be completed: {exc}")


def _finalize_review_packet(out: Path, prepared: dict[str, Any], rendered: dict[str, Any]) -> dict[str, Any]:
    """Hash bounded packet files and write the external evaluator's manifest."""
    import hashlib

    from aad_render.render_limits import (
        MAX_REVIEW_PACKET_BYTES,
        MAX_REVIEW_PACKET_IMAGE_BYTES,
        MAX_REVIEW_PACKET_IMAGES,
        MAX_REVIEW_PACKET_MANIFEST_BYTES,
        MAX_REVIEW_PACKET_REQUEST_BYTES,
        json_size_limited,
    )

    if set(rendered) != {"cases", "image_count", "image_bytes"} or not isinstance(rendered.get("cases"), list):
        raise ValueError("mesh renderer returned malformed review packet metadata")
    case_ids = prepared.get("case_ids")
    manifest = prepared.get("manifest")
    if not isinstance(case_ids, list) or not isinstance(manifest, dict):
        raise ValueError("sandboxed worker returned malformed review packet metadata")
    view_rows = manifest.get("views")
    if not isinstance(view_rows, list) or [row.get("id") for row in view_rows if isinstance(row, dict)] != case_ids:
        raise ValueError("review packet manifest cases do not match its render batch")
    if len(case_ids) != len(rendered["cases"]):
        raise ValueError("mesh renderer returned an incomplete review packet")

    packet_dir = out / "review-packet"
    files = dict(manifest.get("files") or {})
    request_bytes = _read_review_packet_file(packet_dir / "request.txt", MAX_REVIEW_PACKET_REQUEST_BYTES)
    files["request.txt"] = {
        "sha256": hashlib.sha256(request_bytes).hexdigest(),
        "bytes": len(request_bytes),
        "content_type": "text/plain; charset=utf-8",
    }
    image_count = 0
    image_bytes = 0
    for expected, actual in zip(view_rows, rendered["cases"], strict=True):
        if not isinstance(actual, dict) or set(actual) != {"id", "images"} or actual.get("id") != expected["id"]:
            raise ValueError("mesh renderer returned a review image for the wrong case")
        expected_images = expected.get("images")
        if not isinstance(expected_images, list) or actual.get("images") != expected_images:
            raise ValueError("mesh renderer returned unexpected review image names")
        for name in expected_images:
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError("review packet contains an unsafe image name")
            data = _read_review_packet_file(packet_dir / name, MAX_REVIEW_PACKET_IMAGE_BYTES)
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(f"review packet image {name!r} is not a PNG")
            image_count += 1
            image_bytes += len(data)
            files[name] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "content_type": "image/png",
            }
    if image_count > MAX_REVIEW_PACKET_IMAGES or image_count != rendered.get("image_count"):
        raise ValueError("review packet image count is inconsistent or over limit")
    if image_bytes != rendered.get("image_bytes"):
        raise ValueError("review packet image byte count is inconsistent")

    finalized = {**manifest, "files": files}
    manifest_data = (json.dumps(finalized, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(manifest_data) > MAX_REVIEW_PACKET_MANIFEST_BYTES:
        raise ValueError("review packet manifest exceeds its byte limit")
    json_size_limited(finalized, MAX_REVIEW_PACKET_MANIFEST_BYTES, "review packet manifest")
    total_bytes = len(request_bytes) + image_bytes + len(manifest_data)
    if total_bytes > MAX_REVIEW_PACKET_BYTES:
        raise ValueError("review packet directory exceeds its aggregate byte limit")
    _write_private_file(packet_dir / "manifest.json", manifest_data, MAX_REVIEW_PACKET_MANIFEST_BYTES)
    return {
        "path": str(packet_dir),
        "manifest": str(packet_dir / "manifest.json"),
        "image_count": image_count,
        "bytes": total_bytes,
        "manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
    }


def _read_review_packet_file(path: Path, maximum: int) -> bytes:
    """Read one regular packet file without following symlinks or trusting its size."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("safe review packet reads require O_NOFOLLOW support")
    fd = os.open(path, os.O_RDONLY | nofollow)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError(f"review packet file {path.name!r} is not a regular file within its size limit")
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(fd, min(64 * 1024, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > maximum:
            raise ValueError(f"review packet file {path.name!r} exceeds its size limit")
        return bytes(data)
    finally:
        os.close(fd)


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
    """Keep sandbox and timeout failures distinct; report worker failures as render failures."""
    failure = result.get("failure")
    if not isinstance(failure, dict):
        return _crash("WORKER_CRASH", "render", "The render worker failed without a failure report.")
    if failure.get("phase") not in {"sandbox", "timeout", "oom"}:
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
    memory: Any | None = None,
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
    if memory is not None:
        if memory.has_setup_failure_marker(stdout) or memory.has_setup_failure_marker(stderr):
            detail = stderr.decode(errors="replace").strip().splitlines()[-1:] or ["no setup diagnostic"]
            return _crash("SANDBOX_DENIED", "sandbox", f"The configured engine.memory limit could not be installed; the operation did not run. {detail[0]}")
        if memory.is_memory_exhaustion_output(stderr):
            detail = stderr.decode(errors="replace").strip().splitlines()[-1:] or ["allocation failed during worker startup"]
            return _crash("OUT_OF_MEMORY", "oom", f"The operation worker could not allocate memory under engine.memory. {detail[0]}")
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
