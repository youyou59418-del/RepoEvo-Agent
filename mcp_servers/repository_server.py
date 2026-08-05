"""Read-only Repository MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from repoevo.tool_layer import ToolError, list_files, read_file


def make_repository_server(repo_root: Path) -> FastMCP:
    mcp = FastMCP("Repository MCP")

    @mcp.tool(name="list_tree", description="List safe relative repository files.")
    def list_tree(limit: int = 64) -> dict[str, Any]:
        try:
            return {"ok": True, "files": list_files(repo_root, max_files=limit)}
        except ToolError as exc:
            return {"ok": False, "error_code": str(exc)}

    @mcp.tool(name="read_file", description="Read one safe UTF-8 repository file.")
    def read_repository_file(path: str) -> dict[str, Any]:
        try:
            return {"ok": True, "path": path, "content": read_file(repo_root, path)}
        except ToolError as exc:
            return {"ok": False, "error_code": str(exc)}

    @mcp.tool(name="search_text", description="Search text in safe UTF-8 repository files.")
    def search_text(query: str, limit: Annotated[int, Field(ge=1, le=100)] = 30) -> dict[str, Any]:
        if not query or len(query) > 200 or limit <= 0 or limit > 100:
            return {"ok": False, "error_code": "SEARCH_ARGUMENT_INVALID"}
        matches: list[dict[str, Any]] = []
        try:
            for path in list_files(repo_root, max_files=128):
                content = read_file(repo_root, path)
                if query in content:
                    matches.append({"path": path, "count": content.count(query)})
                    if len(matches) >= limit:
                        break
        except ToolError as exc:
            return {"ok": False, "error_code": str(exc)}
        return {"ok": True, "matches": matches}

    return mcp
