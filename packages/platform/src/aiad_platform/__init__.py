"""AIAD platform: CLI, daemon and MCP adapter. Must never import the kernel."""

from importlib import metadata

try:
    __version__ = metadata.version("aiad")
except metadata.PackageNotFoundError:
    __version__ = "0.0.0+unknown"
