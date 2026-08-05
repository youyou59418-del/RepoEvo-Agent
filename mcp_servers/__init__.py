"""MCP server factories and their contract registry."""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from .contracts import validate_contracts
from .git_server import make_git_server
from .repository_server import make_repository_server
from .sandbox_server import make_sandbox_server
from .workspace_server import make_workspace_server


def build_servers(repo_root: Path) -> dict[str, FastMCP]:
    return {
        "repository": make_repository_server(repo_root),
        "workspace": make_workspace_server(repo_root),
        "sandbox": make_sandbox_server(repo_root),
        "git": make_git_server(repo_root),
    }


__all__ = ["build_servers", "validate_contracts"]
