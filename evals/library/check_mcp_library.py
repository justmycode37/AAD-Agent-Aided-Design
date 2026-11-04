#!/usr/bin/env python3
"""Actual MCP/daemon library integration; deterministic, not an agent eval."""
import asyncio
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE = Path(__file__).resolve().parent


def fingerprints():
    import aiad_quality.catalog
    import aiad_platform
    roots = {"quality": Path(aiad_quality.__file__).parent, "platform": Path(aiad_platform.__file__).parent,
             "guidance": Path(str(aiad_quality.catalog._guidance_root()))}
    return {f"{name}/{path.relative_to(root)}": hashlib.sha256(path.read_bytes()).hexdigest()
            for name, root in roots.items() for path in sorted(root.rglob("*"))
            if path.is_file() and (path.suffix == ".py" or name == "guidance")}


async def exercise(state):
    env = {**os.environ, "AIAD_HOME": str(state), "AIAD_IDLE_TIMEOUT_S": "30", "AIAD_NO_BROWSER": "1"}
    server = StdioServerParameters(command=sys.executable, args=["-m", "aiad_platform.cli", "mcp"], env=env)
    records = []
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for kind in ("part", "material", "playbook", "api"):
                args = {"action": "search", "kind": kind}
                result = await session.call_tool("aiad_library", args)
                envelope = result.structured_content
                assert envelope["ok"], envelope
                listing = envelope["library"]
                assert listing["total"] > 0 and 0 < len(listing["items"]) <= 5
                records.append({"args": args, "ok": True, "total": listing["total"]})
                args = {"action": "get", "kind": kind, "id": listing["items"][0]["id"]}
                result = await session.call_tool("aiad_library", args)
                envelope = result.structured_content
                assert envelope["ok"] and envelope["library"]["found"], envelope
                records.append({"args": args, "ok": True, "returned_id": envelope["library"]["item"]["id"]})
            result = await session.call_tool("aiad_library", {"action": "get", "kind": "material", "id": "missing"})
            assert result.structured_content["ok"] and result.structured_content["library"]["found"] is False
            result = await session.call_tool("aiad_library", {"action": "get", "id": "../secret"})
            assert result.structured_content["ok"] is False, result
            assert result.structured_content["failure"]["code"] == "BAD_INPUT", result
    return records


def main():
    output = BASE / "mcp-evidence.json"
    output.unlink(missing_ok=True)
    before = fingerprints()
    with tempfile.TemporaryDirectory(prefix="aiad-lib-", dir="/tmp") as folder:
        state = Path(folder)
        try:
            records = asyncio.run(exercise(state))
        finally:
            info = state / "run/aiadd.json"
            if info.is_file():
                pid = json.loads(info.read_text())["pid"]
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
    import aiad_quality.catalog
    catalog = Path(aiad_quality.catalog.__file__)
    if before != fingerprints():
        raise SystemExit("Sources changed during MCP exercise; no completed evidence retained")
    data = {"source_sha256": before, "complete": True, "passed": True, "scope": "deterministic MCP integration; no model-quality or timing estimate",
            "reproduce": "UV_PROJECT_ENVIRONMENT=packages/quality/.workspace-venv uv run --locked --all-packages --all-extras python evals/library/check_mcp_library.py",
            "versions": {name: version(name) for name in ("aiad", "aiad-quality", "mcp")},
            "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
            "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "unknown_id_returned_not_found": True, "path_id_rejected_as_bad_input": True, "records": records}
    output.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
