#!/usr/bin/env python3
"""Reject stale constructor/MCP evidence; reproduce missing local artifacts first."""
import hashlib
import json
from pathlib import Path

import check_mcp_library
import probe_parts

BASE = Path(__file__).resolve().parent
parts = json.loads((BASE / "parts-evidence.json").read_text())
assert parts["complete"] and parts["passed"]
assert parts["source_sha256"] == probe_parts.fingerprints(), "Stale constructor evidence"
assert {row["id"] for row in parts["records"]} == set(probe_parts.EXPECTED)
for row in parts["records"]:
    path = (BASE / row["artifact"]).resolve()
    assert path.is_relative_to((BASE / ".runs" / parts["run_id"]).resolve())
    assert row["passed"] and path.is_file(), "Reproduce local constructor artifacts first"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["artifact_sha256"]
mcp = json.loads((BASE / "mcp-evidence.json").read_text())
assert mcp["complete"] and mcp["passed"]
assert mcp["source_sha256"] == check_mcp_library.fingerprints(), "Stale MCP source/guidance evidence"
assert mcp["probe_sha256"] == hashlib.sha256((BASE / "check_mcp_library.py").read_bytes()).hexdigest()
assert mcp["unknown_id_returned_not_found"] and mcp["path_id_rejected_as_bad_input"]
print("PASS: source/guidance fingerprints, run-scoped BREP artifacts and MCP observations agree")
