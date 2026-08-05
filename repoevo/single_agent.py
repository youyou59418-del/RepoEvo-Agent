"""Minimal LangGraph single-Agent baseline for RepoEvo-Agent."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .model_adapter import AgentDecision, DecisionModel
from .sandbox_client import SandboxClientError
from .tool_layer import (
    ToolError,
    apply_patch,
    list_files,
    read_file,
    replace_text,
    run_repository_tests,
)


class BaselineState(TypedDict, total=False):
    """The intentionally small state carried through one baseline run."""

    task_id: str
    request: str
    acceptance_criteria: list[str]
    repository_root: str
    messages: list[dict[str, str]]
    tool_history: list[dict[str, Any]]
    repository_files: list[str]
    observed_files: dict[str, str]
    changed_files: list[str]
    test_results: dict[str, Any]
    tool_call_count: int
    retry_count: int
    status: str
    model_mode: str
    pending_decision: dict[str, Any]
    last_tool_result: dict[str, Any]
    final_message: str
    final_diff: str
    deadline_monotonic: float


@dataclass(frozen=True)
class AgentConfig:
    max_tool_calls: int = 12
    max_runtime_seconds: float = 120.0
    max_files_changed: int = 8
    test_timeout_seconds: float = 30.0


def _truncate(value: str, limit: int = 8_000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n... <truncated> ..."


def _diff(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        shell=False,
    )
    return result.stdout if result.returncode == 0 else ""


class SingleAgent:
    """One model node plus one bounded tool node connected by LangGraph."""

    def __init__(self, model: DecisionModel, config: AgentConfig | None = None) -> None:
        self.model = model
        self.config = config or AgentConfig()
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        builder = StateGraph(BaselineState)
        builder.add_node("agent", self._agent_node)
        builder.add_node("tools", self._tool_node)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges(
            "agent",
            self._route_after_agent,
            {"tools": "tools", "finish": END},
        )
        builder.add_edge("tools", "agent")
        return builder.compile()

    def run(self, initial_state: BaselineState) -> BaselineState:
        state: BaselineState = {
            "messages": [],
            "tool_history": [],
            "repository_files": [],
            "observed_files": {},
            "changed_files": [],
            "test_results": {},
            "tool_call_count": 0,
            "retry_count": 0,
            "status": "running",
            "model_mode": self.model.mode,
            "deadline_monotonic": time.monotonic() + self.config.max_runtime_seconds,
            **initial_state,
        }
        result = self.graph.invoke(state)
        if not isinstance(result, dict):
            raise TypeError("LANGGRAPH_STATE_INVALID")
        final = dict(result)
        repository_root = Path(str(final["repository_root"]))
        final["final_diff"] = _diff(repository_root)
        return final  # type: ignore[return-value]

    def _budget_exhausted(self, state: Mapping[str, Any]) -> str | None:
        if time.monotonic() >= float(state.get("deadline_monotonic", 0)):
            return "TIME_BUDGET_EXHAUSTED"
        if int(state.get("tool_call_count", 0)) >= self.config.max_tool_calls:
            return "TOOL_CALL_BUDGET_EXHAUSTED"
        return None

    @staticmethod
    def _with_message(
        state: Mapping[str, Any],
        role: str,
        content: str,
    ) -> list[dict[str, str]]:
        messages = [dict(item) for item in state.get("messages", [])]
        messages.append({"role": role, "content": _truncate(content)})
        return messages[-32:]

    def _agent_node(self, state: BaselineState) -> dict[str, Any]:
        exhausted = self._budget_exhausted(state)
        if exhausted:
            evidence = state.get("test_results", {})
            if evidence.get("ok") is True:
                return {
                    "status": "completed",
                    "final_message": "Public tests passed at the tool-call limit.",
                    "pending_decision": {
                        "action": "finish",
                        "reason": "Public tests passed at the tool-call limit.",
                    },
                }
            return {
                "status": "budget_exhausted",
                "final_message": exhausted,
                "pending_decision": {"action": "finish", "reason": exhausted},
            }

        decision = self.model.decide(state)
        decision_dict = decision.model_dump()
        messages = self._with_message(
            state,
            "agent",
            f"action={decision.action}; reason={decision.reason}; path={decision.path or ''}",
        )
        if decision.action == "finish":
            evidence = state.get("test_results", {})
            status = "completed" if evidence.get("ok") is True else "finished_without_evidence"
            return {
                "pending_decision": decision_dict,
                "messages": messages,
                "status": status,
                "final_message": decision.summary or decision.reason,
            }
        return {"pending_decision": decision_dict, "messages": messages}

    def _route_after_agent(self, state: BaselineState) -> str:
        decision = state.get("pending_decision", {})
        if decision.get("action") == "finish":
            return "finish"
        if self._budget_exhausted(state):
            return "finish"
        return "tools"

    def _tool_node(self, state: BaselineState) -> dict[str, Any]:
        decision = AgentDecision.model_validate(state.get("pending_decision", {}))
        repository_root = Path(str(state["repository_root"]))
        count = int(state.get("tool_call_count", 0)) + 1
        result: dict[str, Any]
        changed_files = list(state.get("changed_files", []))
        test_results = dict(state.get("test_results", {}))
        repository_files = list(state.get("repository_files", []))
        observed_files = dict(state.get("observed_files", {}))

        try:
            if decision.action == "list_files":
                repository_files = list_files(repository_root)
                result = {"ok": True, "files": repository_files}
            elif decision.action == "read_file":
                if not decision.path:
                    raise ToolError("PATH_REQUIRED")
                content = read_file(repository_root, decision.path)
                observed_files[decision.path] = _truncate(content, limit=4_000)
                # Four recent files are enough for this bounded baseline while
                # keeping the model prompt safely within its context window.
                while len(observed_files) > 4:
                    oldest_path = next(iter(observed_files))
                    del observed_files[oldest_path]
                result = {
                    "ok": True,
                    "path": decision.path,
                    "content": observed_files[decision.path],
                }
            elif decision.action == "apply_patch":
                if not decision.patch:
                    raise ToolError("PATCH_REQUIRED")
                changed = apply_patch(
                    repository_root,
                    decision.patch,
                    target_path=decision.path,
                )
                changed_files = sorted(set(changed_files).union(changed))
                if len(changed_files) > self.config.max_files_changed:
                    raise ToolError("MAX_FILES_CHANGED_EXCEEDED")
                result = {"ok": True, "changed_files": changed}
            elif decision.action == "replace_text":
                if not decision.path:
                    raise ToolError("PATH_REQUIRED")
                if not decision.old_text:
                    raise ToolError("OLD_TEXT_REQUIRED")
                if decision.new_text is None:
                    raise ToolError("NEW_TEXT_INVALID")
                changed = replace_text(
                    repository_root,
                    decision.path,
                    decision.old_text,
                    decision.new_text,
                )
                changed_files = sorted(set(changed_files).union(changed))
                if len(changed_files) > self.config.max_files_changed:
                    raise ToolError("MAX_FILES_CHANGED_EXCEEDED")
                result = {"ok": True, "changed_files": changed}
            elif decision.action == "run_tests":
                sandbox_result = run_repository_tests(
                    repository_root,
                    decision.command,
                    timeout_seconds=self.config.test_timeout_seconds,
                )
                test_results = {
                    "ok": sandbox_result.ok,
                    "returncode": sandbox_result.returncode,
                    "timed_out": sandbox_result.timed_out,
                    "stdout": _truncate(sandbox_result.stdout),
                    "stderr": _truncate(sandbox_result.stderr),
                    "error_code": sandbox_result.error_code,
                }
                result = dict(test_results)
            else:
                raise ToolError("ACTION_NOT_TOOL")
        except (SandboxClientError, ToolError, OSError, subprocess.SubprocessError) as exc:
            result = {"ok": False, "error_code": str(exc)}

        history_item = {
            "action": decision.action,
            "ok": result.get("ok") is True,
            "tool_call_index": count,
            **{key: value for key, value in result.items() if key != "content"},
        }
        if decision.path and decision.action in {"read_file", "replace_text", "apply_patch"}:
            history_item.setdefault("path", decision.path)
        history = [dict(item) for item in state.get("tool_history", [])]
        history.append(history_item)
        message = f"tool={decision.action}; result={result.get('ok')}; error={result.get('error_code', '')}"
        return {
            "tool_call_count": count,
            "tool_history": history,
            "last_tool_result": result,
            "test_results": test_results,
            "repository_files": repository_files,
            "observed_files": observed_files,
            "changed_files": changed_files,
            "messages": self._with_message(state, "tool", message),
        }
