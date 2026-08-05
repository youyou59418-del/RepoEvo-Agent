from __future__ import annotations

import asyncio
from pathlib import Path

from mcp_servers import build_servers, validate_contracts


def test_four_mcp_servers_match_contracts(tmp_path: Path) -> None:
    servers = build_servers(tmp_path)
    errors = asyncio.run(validate_contracts(servers))
    assert errors == []
    assert set(servers) == {"repository", "workspace", "sandbox", "git"}


def test_repository_tool_has_safe_argument_schema(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    servers = build_servers(tmp_path)

    async def inspect() -> tuple[dict, dict]:
        tools = await servers["repository"].get_tools()
        return tools["read_file"].parameters, tools["search_text"].parameters

    read_schema, search_schema = asyncio.run(inspect())
    assert "path" in read_schema["properties"]
    assert search_schema["properties"]["limit"]["maximum"] == 100


def test_workspace_contract_calls_checked_patch(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    # The contract test only verifies the tool surface; the Week 3 tool tests
    # cover the actual Git patch operation and path validation.
    servers = build_servers(tmp_path)

    async def inspect() -> dict:
        tools = await servers["workspace"].get_tools()
        return tools["apply_patch"].parameters

    schema = asyncio.run(inspect())
    assert set(schema["properties"]) == {"patch_text"}
