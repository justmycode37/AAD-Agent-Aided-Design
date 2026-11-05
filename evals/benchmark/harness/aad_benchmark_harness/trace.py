from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .security import safe_observation, validate_external_output_path


class JsonlTraceWriter:
    """Append-only, exclusive-create trace writer for host-owned trial output."""

    origin = "live"

    def __init__(self, path: Path, solver_write_roots: tuple[Path, ...]):
        self._path = validate_external_output_path(path, solver_write_roots)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        self._fd = os.open(self._path, flags, 0o600)
        self._sequence = 0
        self._closed = False

    @property
    def path(self) -> Path:
        return self._path

    def write_event(self, event: Mapping[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("trace writer is closed")
        safe = safe_observation(dict(event))
        payload = {**safe, "sequence": self._sequence}
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        view = memoryview(encoded)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]
        os.fsync(self._fd)
        self._sequence += 1

    def close(self) -> None:
        if not self._closed:
            os.fsync(self._fd)
            os.close(self._fd)
            self._closed = True


class InMemoryTraceWriter:
    """Test-only trace sink; its output is explicitly marked as a mock."""

    origin = "mock"

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._closed = False

    @property
    def path(self) -> None:
        return None

    def write_event(self, event: Mapping[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("trace writer is closed")
        self.events.append(safe_observation(dict(event)))

    def close(self) -> None:
        self._closed = True
