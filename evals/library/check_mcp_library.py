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
    import aad_quality.catalog
    import aad_platform
    roots = {"quality": Path(aad_quality.__file__).parent, "platform": Path(aad_platform.__file__).parent,
             "guidance": Path(str(aad_quality.catalog._guidance_root()))}
    hashes = {f"{name}/{path.relative_to(root)}": hashlib.sha256(path.read_bytes()).hexdigest()
            for name, root in roots.items() for path in sorted(root.rglob("*"))
            if path.is_file() and (path.suffix == ".py" or name == "guidance")}
    for name in ("evidence.py", "check_evidence.py"):
        hashes[f"validation/{name}"] = hashlib.sha256((BASE / name).read_bytes()).hexdigest()
    return hashes


async def exercise(state):
    env = {**os.environ, "AAD_HOME": str(state), "AAD_IDLE_TIMEOUT_S": "30", "AAD_NO_BROWSER": "1"}
    server = StdioServerParameters(command=sys.executable, args=["-m", "aad_platform.cli", "mcp"], env=env)
    records = []
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for kind in ("part", "material", "playbook", "api"):
                args = {"action": "search", "kind": kind}
                result = await session.call_tool("aad_library", args)
                envelope = result.structured_content
                requested_id = _search_response(envelope,kind)
                records.append({"args":args,"response":envelope})
                args = {"action":"get","kind":kind,"id":requested_id}
                result = await session.call_tool("aad_library",args)
                records.append({"args":args,"response":result.structured_content})
            for args in ({"action":"get","kind":"material","id":"missing"},
                         {"action":"get","id":"../secret"}):
                result = await session.call_tool("aad_library",args)
                records.append({"args":args,"response":result.structured_content})
    validate_mcp_records(records)
    return records


def main():
    output = BASE / "mcp-evidence.json"
    output.unlink(missing_ok=True)
    before = fingerprints()
    with tempfile.TemporaryDirectory(prefix="aad-lib-", dir="/tmp") as folder:
        state = Path(folder)
        try:
            records = asyncio.run(exercise(state))
        finally:
            info = state / "run/aadd.json"
            if info.is_file():
                pid = json.loads(info.read_text())["pid"]
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
    import aad_quality.catalog
    catalog = Path(aad_quality.catalog.__file__)
    if before != fingerprints():
        raise SystemExit("Sources changed during MCP exercise; no completed evidence retained")
    data = {"source_sha256": before, "complete": True, "passed": True, "scope": "deterministic MCP integration; no model-quality or timing estimate",
            "reproduce": "UV_PROJECT_ENVIRONMENT=packages/quality/.workspace-venv uv run --locked --all-packages --all-extras python evals/library/check_mcp_library.py",
            "versions": {name: version(name) for name in ("aad-design", "aad-quality", "mcp")},
            "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
            "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "records": records}
    output.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    print(json.dumps(data, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
