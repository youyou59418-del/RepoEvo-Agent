"""Git MCP server exposing read-only history and approval-shaped commit intent."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fastmcp import FastMCP


def make_git_server(repo_root: Path) -> FastMCP:
    mcp = FastMCP("Git MCP")

    @mcp.tool(name="get_history", description="Read a bounded local Git history.")
    def get_history(limit: int = 5) -> dict[str, Any]:
        if limit <= 0 or limit > 20:
            return {"ok": False, "error_code": "HISTORY_LIMIT_INVALID"}
        result = subprocess.run(
            ["git", "log", f"-{limit}", "--oneline"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            shell=False,
        )
        return {
            "ok": result.returncode == 0,
            "error_code": None if result.returncode == 0 else "GIT_HISTORY_FAILED",
            "commits": result.stdout.splitlines(),
        }

    @mcp.tool(name="prepare_commit", description="Prepare a commit intent without creating a remote side effect.")
    def prepare_commit(message: str) -> dict[str, Any]:
        cleaned = message.strip()
        if not cleaned or len(cleaned) > 120:
            return {"ok": False, "error_code": "COMMIT_MESSAGE_INVALID"}
        diff = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            shell=False,
        )
        if diff.returncode != 0:
            return {"ok": False, "error_code": "DIFF_FAILED"}
        return {
            "ok": True,
            "approval_required": True,
            "message": cleaned,
            "changed_files": diff.stdout.splitlines(),
        }

    return mcp
