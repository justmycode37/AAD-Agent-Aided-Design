#!/usr/bin/env python3
"""Reaudit run-bound BREPs and validate recorded MCP claims, also under -O."""
import hashlib
from importlib.metadata import version
from pathlib import Path

import check_mcp_library
import probe_parts
from evidence import load_document, require, validate_mcp, validate_parts

BASE = Path(__file__).resolve().parent


def main():
    parts = load_document(BASE / "parts-evidence.json")
    require(parts.get("versions") == {p: version(p) for p in ("build123d", "cadquery-ocp-novtk", "bd_warehouse")}, "Constructor runtime versions disagree")
    validate_parts(parts, base=BASE, expected=probe_parts.EXPECTED,
                   source_sha256=probe_parts.fingerprints(), measure=probe_parts.measure_artifact)
    mcp = load_document(BASE / "mcp-evidence.json")
    require(mcp.get("versions") == {p: version(p) for p in ("aiad", "aiad-quality", "mcp")}, "MCP runtime versions disagree")
    validate_mcp(mcp, source_sha256=check_mcp_library.fingerprints(),
                 probe_sha256=hashlib.sha256((BASE / "check_mcp_library.py").read_bytes()).hexdigest())
    print("PASS: exact trial/call coverage, source/guidance fingerprints and fresh BREP audits agree")


if __name__ == "__main__":
    main()
