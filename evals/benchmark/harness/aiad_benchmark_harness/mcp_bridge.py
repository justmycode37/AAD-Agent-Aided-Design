from __future__ import annotations

from typing import Any, Mapping, Sequence

from .security import to_plain
from .types import ToolSpec


def _get(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def tool_spec_from_mcp(tool: Any) -> ToolSpec:
    name = _get(tool, "name")
    description = _get(tool, "description", default="") or ""
    input_schema = _get(tool, "inputSchema", "input_schema", default=None)
    metadata = _get(tool, "_meta", "meta", "metadata", default={}) or {}
    if not isinstance(name, str) or not name:
        raise ValueError("MCP tool is missing a name")
    if not isinstance(description, str):
        description = str(description)
    if not isinstance(input_schema, Mapping):
        raise ValueError(f"MCP tool {name!r} is missing its input schema")
    plain_metadata = to_plain(metadata)
    if not isinstance(plain_metadata, Mapping):
        plain_metadata = {}
    return ToolSpec(
        name=name,
        description=description,
        input_schema=dict(input_schema),
        metadata=dict(plain_metadata),
    )


class ClientSessionBridge:
    """Small duck-typed adapter for an official MCP ClientSession.

    The loop needs only ``list_tools`` and ``call_tool``. Passing a custom
    bridge with the same async methods avoids coupling backend launchers to a
    particular transport implementation.
    """

    def __init__(self, session: Any):
        self._session = session
        origin = getattr(session, "origin", None)
        if isinstance(origin, str) and origin in {"live", "mock", "custom"}:
            self.origin = origin
        elif type(session).__module__.startswith("mcp."):
            self.origin = "live"
        else:
            self.origin = "custom"

    async def list_tools(self) -> Sequence[ToolSpec]:
        result = await self._session.list_tools()
        tools = _get(result, "tools", default=None)
        if tools is None:
            raise TypeError("MCP ClientSession.list_tools() returned no tools field")
        return tuple(tool_spec_from_mcp(tool) for tool in tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._session.call_tool(name, arguments)
