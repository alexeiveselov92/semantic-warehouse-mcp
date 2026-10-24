"""warehouse-mcp — a read-only MCP server for SQL databases with their semantics (docs/DESIGN.md)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("warehouse-mcp")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0"
