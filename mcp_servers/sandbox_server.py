"""Sandbox MCP server with fixed test profiles and no generic command tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from repoevo.tool_layer import run_repository_tests


def _result(result: Any) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "error_code": result.error_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "limits": result.limits,
    }


def make_sandbox_server(repo_root: Path) -> FastMCP:
    mcp = FastMCP("Sandbox MCP")

    @mcp.tool(name="run_target_tests", description="Run the fixed public pytest profile in the sandbox.")
    def run_target_tests(profile_id: str = "public") -> dict[str, Any]:
        if profile_id != "public":
            return {"ok": False, "error_code": "TEST_PROFILE_NOT_ALLOWED"}
        return _result(run_repository_tests(repo_root))

    @mcp.tool(name="run_regression", description="Run the fixed regression pytest profile in the sandbox.")
    def run_regression(profile_id: str = "regression") -> dict[str, Any]:
        if profile_id != "regression":
            return {"ok": False, "error_code": "TEST_PROFILE_NOT_ALLOWED"}
        return _result(run_repository_tests(repo_root))

    return mcp
