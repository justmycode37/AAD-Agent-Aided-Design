"""Locate the canonical CAD viewer without silently degrading to the legacy stub."""

from __future__ import annotations

import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
STUB = _HERE / "stub_viewer.html"


def viewer_path() -> Path:
    """Use an explicit override, the packaged runtime or a workspace build."""
    if override := os.environ.get("AAD_VIEWER_HTML"):
        path = Path(override).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"AAD_VIEWER_HTML does not exist: {path}")
        return path
    candidates = [_HERE / "aad-viewer.html"]
    if len(_HERE.parents) > 4:
        candidates.append(_HERE.parents[4] / "viewer" / "dist" / "aad-viewer.html")
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "The full AAD viewer is missing from this Python environment. "
        "Update/reinstall AAD, or run `npm run build` in the current repository's viewer directory. "
        "The legacy stub is not a replacement for the CAD viewer."
    )


def load_html() -> str:
    return viewer_path().read_text(encoding="utf-8")
