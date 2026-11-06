"""Compressed metadata storage for immutable project revisions."""

from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
from typing import Any

COMPRESSED_META_NAME = "meta.json.gz"
LEGACY_META_NAME = "meta.json"


def read_revision_meta(rev_dir: Path) -> dict[str, Any]:
    """Read current compressed metadata or the uncompressed legacy form."""
    compressed = rev_dir / COMPRESSED_META_NAME
    if compressed.is_file():
        with gzip.open(compressed, "rt", encoding="utf-8") as stream:
            value = json.load(stream)
    else:
        with (rev_dir / LEGACY_META_NAME).open("rt", encoding="utf-8") as stream:
            value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Revision metadata must be a JSON object")
    return value


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
