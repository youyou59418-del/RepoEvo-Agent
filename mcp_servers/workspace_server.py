"""Workspace MCP server for isolated, checked Git changes."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from repoevo.tool_layer import ToolError, apply_patch, snapshot_text_files


def make_workspace_server(repo_root: Path) -> FastMCP:
    mcp = FastMCP("Workspace MCP")

    @mcp.tool(name="get_diff", description="Return the current isolated Git diff.")
    def get_diff() -> dict[str, Any]:
        result = subprocess.run(
            ["git", "diff", "--binary"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            shell=False,
        )
        if result.returncode != 0:
            return {"ok": False, "error_code": "DIFF_FAILED", "stderr": result.stderr}
        return {"ok": True, "diff": result.stdout}

    @mcp.tool(
        name="apply_patch", description="Apply one checked unified diff in the isolated workspace."
    )
    def apply_workspace_patch(patch_text: str) -> dict[str, Any]:
        try:
            return {"ok": True, "changed_files": apply_patch(repo_root, patch_text)}
        except ToolError as exc:
            return {"ok": False, "error_code": str(exc)}

    @mcp.tool(
        name="workspace_snapshot", description="Return safe text-file contents for the workspace."
    )
    def workspace_snapshot() -> dict[str, Any]:
        try:
            files = snapshot_text_files(repo_root)
            return {"ok": True, "file_count": len(files), "files": files}
        except ToolError as exc:
            return {"ok": False, "error_code": str(exc)}

    return mcp
