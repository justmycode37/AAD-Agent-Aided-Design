"""Locates the single-file viewer HTML that S3 builds, or the M0 stub."""

from __future__ import annotations

import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
STUB = _HERE / "stub_viewer.html"


def viewer_path() -> Path:
    """The first viewer that exists: override, packaged, workspace build, then the stub."""
    candidates = []
    if override := os.environ.get("AIAD_VIEWER_HTML"):
        candidates.append(Path(override).expanduser())
    candidates.append(_HERE / "aiad-viewer.html")
    # packages/platform/src/aiad_platform/viewer -> repository root
    if len(_HERE.parents) > 4:
        candidates.append(_HERE.parents[4] / "viewer" / "dist" / "aiad-viewer.html")
    return next((path for path in candidates if path.is_file()), STUB)


def load_html() -> str:
    return viewer_path().read_text(encoding="utf-8")
