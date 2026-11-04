"""Content-addressed scene store and chunked reads (CONTRACTS section 7)."""

from __future__ import annotations

import base64
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .. import paths

SCENE_ID_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK_BYTES = 128 * 1024


def scene_path(scene_id: str) -> Path:
    if not SCENE_ID_RE.match(scene_id or ""):
        raise ValueError(f"invalid scene_id {scene_id!r}")
    return paths.cache_dir() / "scenes" / f"{scene_id}.gz"


def publish(scene_id: str, source: Path) -> Path:
    """Copy a revision's scene into the store so reads survive a daemon restart."""
    target = scene_path(scene_id)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        shutil.copyfile(source, tmp)
        os.replace(tmp, target)
    return target


def read_chunk(scene_id: str, offset: int, length: int | None = None) -> dict[str, Any]:
    target = scene_path(scene_id)
    if not target.exists():
        raise FileNotFoundError(f"unknown scene {scene_id}")
    return read_file_chunk(target, offset, length)


def read_file_chunk(path: Path, offset: int, length: int | None = None) -> dict[str, Any]:
    total = path.stat().st_size
    if offset < 0 or offset > total:
        raise ValueError(f"offset {offset} is outside 0..{total}")
    size = min(CHUNK_BYTES, length if length and length > 0 else CHUNK_BYTES)
    with path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read(size)
    return {
        "data_b64": base64.b64encode(data).decode("ascii"),
        "offset": offset,
        "byte_count": len(data),
        "total_bytes": total,
        "has_more": offset + len(data) < total,
    }
