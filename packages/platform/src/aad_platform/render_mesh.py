"""Render a JSON mesh payload without opening or parsing the source B-rep."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .ops import RESULT_MARKER, _classify, _plain


def main() -> int:
    """Read render-only data from stdin and write PNGs beneath the supplied output directory."""
    protocol = os.dup(1)
    os.dup2(2, 1)
    out = Path(sys.argv[1])
    request = json.load(sys.stdin)
    try:
        from aad_render import render_mesh

        rendered = render_mesh(request["render"], out)
        result = {"ok": True, "rendered": _plain(rendered)}
    except Exception as exc:
        result = {"ok": False, "failure": _classify(exc)}
    os.write(protocol, RESULT_MARKER + json.dumps(result, default=str).encode() + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
