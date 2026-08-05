"""Machine-checkable MCP tool contracts.

The contract is deliberately smaller than a general shell API: every server
has a narrow purpose, fixed arguments, and explicit error semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REQUIRED_TOOLS: dict[str, set[str]] = {
    "repository": {"list_tree", "read_file", "search_text"},
    "workspace": {"get_diff", "apply_patch"},
    "sandbox": {"run_target_tests", "run_regression"},
    "git": {"get_history", "prepare_commit"},
}


async def validate_contracts(servers: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for server_name, required in REQUIRED_TOOLS.items():
        server = servers.get(server_name)
        if server is None:
            errors.append(f"SERVER_MISSING:{server_name}")
            continue
        tools = await server.get_tools()
        actual = set(tools)
        for tool_name in sorted(required - actual):
            errors.append(f"TOOL_MISSING:{server_name}.{tool_name}")
        for tool_name in sorted(required & actual):
            schema = tools[tool_name].parameters
            properties = schema.get("properties", {})
            if "command" in properties:
                errors.append(f"GENERIC_COMMAND_FORBIDDEN:{server_name}.{tool_name}")
    return errors
