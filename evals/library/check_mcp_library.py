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
from evidence import _search_response, validate_mcp_records

BASE = Path(__file__).resolve().parent


def fingerprints():
    import aiad_quality.catalog
    import aiad_platform
    roots = {"quality": Path(aiad_quality.__file__).parent, "platform": Path(aiad_platform.__file__).parent,
             "guidance": Path(str(aiad_quality.catalog._guidance_root()))}
    hashes = {f"{name}/{path.relative_to(root)}": hashlib.sha256(path.read_bytes()).hexdigest()
            for name, root in roots.items() for path in sorted(root.rglob("*"))
            if path.is_file() and (path.suffix == ".py" or name == "guidance")}
    for name in ("evidence.py", "check_evidence.py"):
        hashes[f"validation/{name}"] = hashlib.sha256((BASE / name).read_bytes()).hexdigest()
    return hashes


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
                requested_id = _search_response(envelope,kind)
                records.append({"args":args,"response":envelope})
                args = {"action":"get","kind":kind,"id":requested_id}
                result = await session.call_tool("aiad_library",args)
                records.append({"args":args,"response":result.structured_content})
            for args in ({"action":"get","kind":"material","id":"missing"},
                         {"action":"get","id":"../secret"}):
                result = await session.call_tool("aiad_library",args)
                records.append({"args":args,"response":result.structured_content})
    validate_mcp_records(records)
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
            "records": records}
    output.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    print(json.dumps(data, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
