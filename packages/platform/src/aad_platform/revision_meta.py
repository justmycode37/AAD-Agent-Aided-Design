"""Compressed metadata storage for immutable project revisions."""

from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
import stat
from typing import Any
import zlib

COMPRESSED_META_NAME = "meta.json.gz"
LEGACY_META_NAME = "meta.json"


class InvalidRevisionMetadata(Exception):
    """A revision's compressed or legacy inspection metadata cannot be read."""

    code = "INVALID_REVISION_METADATA"
    phase = "input"

    def __init__(self, filename: str, reason: str) -> None:
        self.filename = filename
        self.reason = reason
        super().__init__(f"Revision metadata file {filename} is invalid: {reason}")


def read_revision_meta(rev_dir: Path) -> dict[str, Any]:
    """Read compressed metadata or the legacy form, with compressed always taking precedence.

    If both files exist, a broken ``meta.json.gz`` is an error; it never falls back
    to a possibly stale ``meta.json`` copy.
    """
    compressed = rev_dir / COMPRESSED_META_NAME
    path = compressed
    try:
        try:
            compressed.lstat()
        except FileNotFoundError:
            path = rev_dir / LEGACY_META_NAME

        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise InvalidRevisionMetadata(path.name, "not a regular file")
        opener = gzip.open if path == compressed else open
        with opener(path, "rt", encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise InvalidRevisionMetadata(path.name, "metadata must be a JSON object")
        return value
    except InvalidRevisionMetadata:
        raise
    except (OSError, EOFError, UnicodeError, ValueError, zlib.error) as exc:
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
        raise InvalidRevisionMetadata(path.name, f"{type(exc).__name__}: {reason}") from exc


def write_revision_meta(job_dir: Path, value: dict[str, Any]) -> Path:
    """Write gzip-compressed JSON directly into a private build job directory."""
    target = job_dir / COMPRESSED_META_NAME
    temporary = job_dir / f".{COMPRESSED_META_NAME}.{os.getpid()}.tmp"
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8") as stream:
                    json.dump(value, stream, indent=2, default=str)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
