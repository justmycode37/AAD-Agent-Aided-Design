"""Opt-in per-part geometry reuse inside a build worker.

The persistent project cache is never opened by model code. The coordinator
stages validated candidates into the already-writable job directory and only
publishes worker-produced candidates after the worker has exited. A cache hit
supplies geometry only; the full build gate always runs again.
"""

from __future__ import annotations

import hashlib
import json
import marshal
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import CodeType
from typing import Any, Callable

@dataclass
class _PartCache:
    payload: dict[str, Any]
    exchange_dir: Path
    enabled: bool
    seeds: dict[str, dict[str, Any]]
    shapes: dict[str, Any] = field(default_factory=dict)
    calls: int = 0
    persistent_hits: int = 0
    memory_hits: int = 0
    misses: int = 0
    rejected_entries: int = 0
    uncacheable: int = 0

    def metrics(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "calls": self.calls,
            "persistent_hits": self.persistent_hits,
            "memory_hits": self.memory_hits,
            "misses": self.misses,
            "rejected_entries": self.rejected_entries,
            "uncacheable": self.uncacheable,
        }


_active: _PartCache | None = None


def configure_part_cache(payload: dict[str, Any], exchange_dir: Path, *, enabled: bool) -> None:
    """Set up one worker's cache exchange, with no persistent-cache access."""
    global _active
    seeds = payload.get("part_cache_seeds", {})
    if type(seeds) is not dict:
        seeds = {}
    _active = _PartCache(
        payload=payload,
        exchange_dir=Path(exchange_dir),
        enabled=bool(enabled),
        seeds=seeds,
    )


def part_cache_metrics() -> dict[str, Any]:
    """Return counters for the current worker build."""
    if _active is None:
        return {"enabled": False, "calls": 0, "persistent_hits": 0, "memory_hits": 0,
                "misses": 0, "rejected_entries": 0, "uncacheable": 0}
    return _active.metrics()


def cached_part(key_inputs: Any, builder_fn: Callable[[], Any]) -> Any:
    """Return one deterministic part, reusing geometry for identical inputs.

    ``key_inputs`` must describe every geometry-affecting input read by
    ``builder_fn``. Model source, current applied parameters, project inputs,
    kernel versions and the complete engine/check pipeline are added by AAD.
    Builders with randomness, time, hidden state or unbound file reads must
    not use this helper.
    """
    if not callable(builder_fn):
        raise TypeError("cached_part builder_fn must be callable")
    cache = _active
    if cache is None:
        return builder_fn()
    cache.calls += 1
    if not cache.enabled:
        cache.misses += 1
        return builder_fn()

    builder_identity = _builder_identity(builder_fn)
    if builder_identity is None:
        cache.uncacheable += 1
        return builder_fn()

    try:
        from .identity import cache_identity

        record = _identity_record(cache, key_inputs, builder_identity)
        key, record = cache_identity(**record)
    except (TypeError, ValueError, OverflowError):
        # An unsupported key or model parameter is a safe cache miss. The
        # ordinary builder remains responsible for the model's behavior.
        cache.uncacheable += 1
        return builder_fn()

    if key in cache.shapes:
        cache.memory_hits += 1
        return _fresh_location(cache.shapes[key])

    seed = cache.seeds.get(key)
    if seed is not None:
        try:
            if not _same_record(record, seed.get("input_record")):
                raise ValueError("staged cache identity differs from the requested identity")
            path = _seed_path(cache.exchange_dir, key)
            if _hash_file(path) != (seed.get("byte_length"), seed.get("sha256")):
                raise ValueError("staged cache bytes differ from their validated manifest")
            from build123d import import_brep

            shape = import_brep(path)
            if shape is None or getattr(shape, "is_null", False):
                raise ValueError("staged BREP did not load as a shape")
            cache.shapes[key] = shape
            cache.persistent_hits += 1
            return _fresh_location(shape)
        except Exception as exc:
            from .memory import is_memory_exhaustion

            if is_memory_exhaustion(exc):
                raise
            cache.rejected_entries += 1

    cache.misses += 1
    shape = builder_fn()
    try:
        _write_candidate(cache, key, record, shape)
    except Exception as exc:
        from .memory import is_memory_exhaustion

        # Cache storage is optional. A cache-write error cannot discard a part
        # that the model already built successfully.
        if is_memory_exhaustion(exc):
            cache.uncacheable += 1
        else:
            cache.uncacheable += 1
    try:
        from build123d import Shape

        if isinstance(shape, Shape):
            cache.shapes[key] = shape
    except Exception:
        pass
    return _fresh_location(shape)


def _builder_identity(builder_fn: Callable[[], Any]) -> str | None:
    """Hash stable function bytecode, excluding its per-job source filename."""
    code = getattr(builder_fn, "__code__", None)
    if not isinstance(code, CodeType):
        call = getattr(type(builder_fn), "__call__", None)
        code = getattr(call, "__code__", None)
    if not isinstance(code, CodeType):
        return None

    def without_runtime_paths(value: CodeType) -> CodeType:
        constants = tuple(
            without_runtime_paths(item) if isinstance(item, CodeType) else item
            for item in value.co_consts
        )
        return value.replace(co_filename="<cached-part-builder>", co_consts=constants)

    try:
        return hashlib.sha256(marshal.dumps(without_runtime_paths(code))).hexdigest()
    except (TypeError, ValueError):
        return None


def _identity_record(cache: _PartCache, key_inputs: Any, builder_identity: str) -> dict[str, Any]:
    from aad._runtime import current_params
    from aad.modeling import context_metadata
    from .gate import GateTolerances

    metadata = context_metadata(cache.payload)
    return {
        "source": cache.payload["source"],
        "applied_params": {
            "model": current_params(),
            "part": {"builder_code_sha256": builder_identity, "inputs": key_inputs},
        },
        "spec": cache.payload.get("spec", {}),
        "kind": cache.payload.get("kind", "part"),
        "units": metadata.get("units", cache.payload.get("units", "mm")),
        "material": metadata.get("material", cache.payload.get("material")),
        "process": metadata.get("process", cache.payload.get("process")),
        "kernel_versions": cache.payload.get("part_cache_kernel_versions", {}),
        "tolerances": asdict(GateTolerances()),
        "asset_sha256": cache.payload.get("part_cache_asset_sha256", {}),
        "pipeline_identity": cache.payload.get("part_cache_pipeline_identity", ""),
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _same_record(first: Any, second: Any) -> bool:
    try:
        return _canonical_json(first) == _canonical_json(second)
    except (TypeError, ValueError, OverflowError):
        return False


def _seed_path(exchange_dir: Path, key: str) -> Path:
    root = exchange_dir / "seed"
    directory = root / key
    for path in (exchange_dir, root, directory):
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError("staged cache path contains a symlink or non-directory")
    return directory / "part.brep"


def _file_fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _hash_file(path: Path) -> tuple[int, str]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size <= 0:
        raise ValueError("staged cache payload is not a non-empty private regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if _file_fingerprint(opened) != _file_fingerprint(before):
            raise ValueError("staged cache payload changed while opening")
        digest, length = hashlib.sha256(), 0
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                length += len(chunk)
        if length != opened.st_size or _file_fingerprint(os.fstat(fd)) != _file_fingerprint(opened):
            raise ValueError("staged cache payload changed while reading")
        return length, digest.hexdigest()
    finally:
        os.close(fd)


def _write_candidate(cache: _PartCache, key: str, record: dict[str, Any], shape: Any) -> None:
    from build123d import Shape, export_brep

    if not isinstance(shape, Shape):
        return
    pending = cache.exchange_dir / "pending"
    pending.mkdir(mode=0o700, exist_ok=True)
    entry = pending / key
    entry.mkdir(mode=0o700, exist_ok=False)
    path = entry / "part.brep"
    if not export_brep(shape, path) or not path.is_file() or path.stat().st_size <= 0:
        return
    with (entry / "input_record.json").open("xb") as stream:
        stream.write(_canonical_json(record))
        stream.flush()
        os.fsync(stream.fileno())


def _fresh_location(shape: Any) -> Any:
    """Give each caller a separate wrapper while sharing immutable topology."""
    try:
        from build123d import Location

        moved = getattr(shape, "moved", None)
        return moved(Location()) if callable(moved) else shape
    except Exception:
        return shape
