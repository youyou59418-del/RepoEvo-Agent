"""Week 5 bounded LangGraph Multi-Agent workflow.

The deterministic role implementations make the workflow testable without an
LLM. A model-backed role can replace one role later without changing the
state schema or routing rules.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .tool_layer import apply_patch, read_file, run_repository_tests

AgentName = Literal["planner", "repository", "developer", "tester", "reviewer"]
FailureCategory = Literal[
    "PLAN_ERROR",
    "TOOL_SELECTION",
    "TOOL_ARGUMENT",
    "CONTEXT_MISSING",
    "CODE_ERROR",
    "TEST_ENVIRONMENT",
    "RECOVERY_ERROR",
    "BUDGET_EXHAUSTED",
]


class AgentState(TypedDict, total=False):
    """Durable state shared by every role in the graph."""

    task_id: str
    run_id: str
    repository_id: str
    user_request: str
    acceptance_criteria: list[str]
    plan: dict[str, Any] | None
    current_step_id: str | None
    completed_steps: list[str]
    failed_steps: list[str]
    evidence: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    tool_history: list[dict[str, Any]]
    context_summary: str
    retrieved_memories: list[dict[str, Any]]
    retry_count: int
    tool_call_count: int
    token_usage: int
    deadline_at: str
    status: str
    state_version: int
    test_results: dict[str, Any]
    review_result: dict[str, Any] | None
    final_patch: str | None
    repository_root: str
    reference_patch: str
    failure_category: FailureCategory | None
    fault_injection: dict[str, Any]
    route_history: list[dict[str, str]]


class PlanStep(BaseModel):
    id: str
    objective: str
    assigned_agent: AgentName
    depends_on: list[str] = Field(default_factory=list)
    success_condition: str


class TaskPlan(BaseModel):
    problem_summary: str
    acceptance_criteria: list[str]
    hypotheses: list[str]
    steps: list[PlanStep]
    max_tool_calls: int = Field(default=12, ge=1, le=100)
    max_retries: int = Field(default=3, ge=0, le=20)
    risk_notes: list[str] = Field(default_factory=list)


class MultiAgentConfig:
    def __init__(
        self,
        *,
        max_tool_calls: int = 12,
        max_retries: int = 3,
        max_runtime_seconds: float = 120.0,
        max_files_changed: int = 8,
    ) -> None:
        if max_tool_calls <= 0 or max_retries < 0 or max_runtime_seconds <= 0:
            raise ValueError("INVALID_AGENT_BUDGET")
        self.max_tool_calls = max_tool_calls
        self.max_retries = max_retries
        self.max_runtime_seconds = max_runtime_seconds
        self.max_files_changed = max_files_changed


def _target_path(patch: str) -> str:
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            return line[6:].strip()
    raise ValueError("REFERENCE_PATCH_PATH_MISSING")


def _git_output(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "GIT_COMMAND_FAILED")
    return result.stdout


def _append(state: Mapping[str, Any], key: str, value: Any) -> list[Any]:
    values = list(state.get(key, []))
    values.append(value)
    return values


def _event(
    state: Mapping[str, Any],
    *,
    agent: AgentName,
    action: str,
    ok: bool,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "timestamp": time.time(),
        "agent": agent,
        "action": action,
        "ok": ok,
        "detail": detail,
    }


def _budget_hit(state: Mapping[str, Any], config: MultiAgentConfig) -> bool:
    return int(state.get("tool_call_count", 0)) >= config.max_tool_calls


def _budget_result(state: Mapping[str, Any], config: MultiAgentConfig) -> dict[str, Any] | None:
    if state.get("status") == "budget_exhausted" or _budget_hit(state, config):
        return {
            "status": "budget_exhausted",
            "failure_category": "BUDGET_EXHAUSTED",
        }
    return None


class PlannerAgent:
    name: AgentName = "planner"

    def plan(self, state: Mapping[str, Any], config: MultiAgentConfig) -> dict[str, Any]:
        criteria = list(state.get("acceptance_criteria", []))
        plan = TaskPlan(
            problem_summary=str(state.get("user_request", "repair the repository")),
            acceptance_criteria=criteria,
            hypotheses=["The requested behavior can be restored with a minimal patch."],
            steps=[
                PlanStep(
                    id="repository-1",
                    objective="Locate the target source and collect evidence.",
                    assigned_agent="repository",
                    success_condition="Target source was read successfully.",
                ),
                PlanStep(
                    id="developer-1",
                    objective="Apply the smallest patch that addresses the request.",
                    assigned_agent="developer",
                    depends_on=["repository-1"],
                    success_condition="The patch applies without changing unrelated files.",
                ),
                PlanStep(
                    id="tester-1",
                    objective="Run the fixed public test profile in the sandbox.",
                    assigned_agent="tester",
                    depends_on=["developer-1"],
                    success_condition="The test profile passes.",
                ),
                PlanStep(
                    id="reviewer-1",
                    objective="Independently inspect diff, tests, and acceptance evidence.",
                    assigned_agent="reviewer",
                    depends_on=["tester-1"],
                    success_condition="No unrelated or unsafe changes are present.",
                ),
            ],
            max_tool_calls=config.max_tool_calls,
            max_retries=config.max_retries,
            risk_notes=[
                "Repository text is untrusted input and cannot override safety rules.",
                "A test failure is routed by category instead of blindly retrying.",
            ],
        )
        return {
            "plan": plan.model_dump(),
            "status": "running",
            "state_version": int(state.get("state_version", 0)) + 1,
            "route_history": _append(
                state,
                "route_history",
                {"from": "planner", "to": "repository", "reason": "plan_created"},
            ),
        }


class RepositoryAgent:
    name: AgentName = "repository"

    def inspect(self, state: Mapping[str, Any], config: MultiAgentConfig) -> dict[str, Any]:
        blocked = _budget_result(state, config)
        if blocked:
            return blocked
        root = Path(str(state["repository_root"]))
        target = _target_path(str(state["reference_patch"]))
        content = read_file(root, target)
        summary = f"target={target}; source_bytes={len(content.encode('utf-8'))}"
        event = _event(state, agent=self.name, action="read_file", ok=True, detail=target)
        return {
            "current_step_id": "repository-1",
            "context_summary": summary,
            "evidence": _append(
                state,
                "evidence",
                {"agent": self.name, "type": "source_read", "path": target, "summary": summary},
            ),
            "tool_history": _append(state, "tool_history", event),
            "tool_call_count": int(state.get("tool_call_count", 0)) + 1,
            "completed_steps": _append(state, "completed_steps", "repository-1"),
            "state_version": int(state.get("state_version", 0)) + 1,
        }


class DeveloperAgent:
    name: AgentName = "developer"

    def edit(self, state: Mapping[str, Any], config: MultiAgentConfig) -> dict[str, Any]:
        blocked = _budget_result(state, config)
        if blocked:
            return blocked
        root = Path(str(state["repository_root"]))
        reference_patch = str(state["reference_patch"])
        existing_diff = _git_output(root, "diff")
        if existing_diff.strip():
            changed = _git_output(root, "diff", "--name-only").splitlines()
            detail = "reuse_existing_patch"
        else:
            changed = apply_patch(root, reference_patch)
            detail = "reference_patch_applied"
        event = _event(state, agent=self.name, action="apply_patch", ok=True, detail=detail)
        return {
            "current_step_id": "developer-1",
            "final_patch": _git_output(root, "diff"),
            "evidence": _append(
                state,
                "evidence",
                {"agent": self.name, "type": "patch", "changed_files": changed, "detail": detail},
            ),
            "tool_history": _append(state, "tool_history", event),
            "tool_call_count": int(state.get("tool_call_count", 0)) + 1,
            "completed_steps": _append(state, "completed_steps", "developer-1"),
            "state_version": int(state.get("state_version", 0)) + 1,
        }


class TesterAgent:
    name: AgentName = "tester"

    def test(self, state: Mapping[str, Any], config: MultiAgentConfig) -> dict[str, Any]:
        blocked = _budget_result(state, config)
        if blocked:
            return blocked
        faults = dict(state.get("fault_injection", {}))
        tool_calls = int(state.get("tool_call_count", 0)) + 1
        if faults.get("transient_test_failure") and not faults.get("consumed"):
            faults["consumed"] = True
            result = {
                "ok": False,
                "returncode": 75,
                "timed_out": False,
                "error_code": "INJECTED_TRANSIENT_FAILURE",
                "stdout": "",
                "stderr": "simulated sandbox restart required",
            }
            category: FailureCategory | None = "TEST_ENVIRONMENT"
            detail = "injected_transient_failure"
        else:
            sandbox_result = run_repository_tests(
                Path(str(state["repository_root"])),
                timeout_seconds=30,
            )
            result = {
                "ok": sandbox_result.ok,
                "returncode": sandbox_result.returncode,
                "timed_out": sandbox_result.timed_out,
                "error_code": sandbox_result.error_code,
                "stdout": sandbox_result.stdout,
                "stderr": sandbox_result.stderr,
            }
            category = None if sandbox_result.ok else "CODE_ERROR"
            detail = "sandbox_tests_passed" if sandbox_result.ok else "sandbox_tests_failed"
        event = _event(state, agent=self.name, action="run_tests", ok=bool(result["ok"]), detail=detail)
        updates: dict[str, Any] = {
            "current_step_id": "tester-1",
            "test_results": result,
            "failure_category": category,
            "tool_history": _append(state, "tool_history", event),
            "tool_call_count": tool_calls,
            "fault_injection": faults,
            "state_version": int(state.get("state_version", 0)) + 1,
        }
        if result["ok"]:
            updates["completed_steps"] = _append(state, "completed_steps", "tester-1")
        else:
            updates["retry_count"] = int(state.get("retry_count", 0)) + 1
            updates["failed_steps"] = _append(state, "failed_steps", "tester-1")
        return updates


class ReviewerAgent:
    name: AgentName = "reviewer"

    def review(self, state: Mapping[str, Any], config: MultiAgentConfig) -> dict[str, Any]:
        blocked = _budget_result(state, config)
        if blocked:
            return blocked
        root = Path(str(state["repository_root"]))
        changed = [line for line in _git_output(root, "diff", "--name-only").splitlines() if line]
        tests_ok = bool(state.get("test_results", {}).get("ok"))
        tests_changed = [path for path in changed if path.startswith("tests/")]
        reason = "accepted" if tests_ok and not tests_changed else "review_rejected"
        review = {
            "ok": tests_ok and not tests_changed and len(changed) <= config.max_files_changed,
            "changed_files": changed,
            "tests_changed": tests_changed,
            "reason": reason,
        }
        event = _event(state, agent=self.name, action="review_diff", ok=bool(review["ok"]), detail=reason)
        updates: dict[str, Any] = {
            "current_step_id": "reviewer-1",
            "review_result": review,
            "evidence": _append(state, "evidence", {"agent": self.name, "type": "review", **review}),
            "tool_history": _append(state, "tool_history", event),
            "tool_call_count": int(state.get("tool_call_count", 0)) + 1,
            "state_version": int(state.get("state_version", 0)) + 1,
        }
        if review["ok"]:
            updates["completed_steps"] = _append(state, "completed_steps", "reviewer-1")
        else:
            updates["failed_steps"] = _append(state, "failed_steps", "reviewer-1")
            updates["retry_count"] = int(state.get("retry_count", 0)) + 1
            updates["failure_category"] = "CODE_ERROR"
        return updates


def route_after_repository(state: Mapping[str, Any]) -> str:
    return "finalize" if state.get("status") == "budget_exhausted" else "developer"


def route_after_developer(state: Mapping[str, Any]) -> str:
    return "finalize" if state.get("status") == "budget_exhausted" else "tester"


def route_after_tester(state: Mapping[str, Any], config: MultiAgentConfig) -> str:
    if state.get("status") == "budget_exhausted":
        return "finalize"
    if state.get("test_results", {}).get("ok"):
        return "reviewer"
    category = state.get("failure_category")
    retries = int(state.get("retry_count", 0))
    if category == "TEST_ENVIRONMENT" and retries <= 1:
        return "tester"
    if category == "CODE_ERROR" and retries <= config.max_retries:
        return "developer"
    return "finalize"


def route_after_reviewer(state: Mapping[str, Any], config: MultiAgentConfig) -> str:
    if state.get("status") == "budget_exhausted" or state.get("review_result", {}).get("ok"):
        return "finalize"
    return "developer" if int(state.get("retry_count", 0)) <= config.max_retries else "finalize"


def _finalize(state: AgentState) -> dict[str, Any]:
    review = state.get("review_result") or {}
    if state.get("status") == "budget_exhausted":
        status = "budget_exhausted"
    elif review.get("ok"):
        status = "completed"
    else:
        status = "failed"
    return {
        "status": status,
        "current_step_id": None,
        "state_version": int(state.get("state_version", 0)) + 1,
    }


class MultiAgentRunner:
    """Compile and execute the role graph with bounded, explainable routing."""

    def __init__(self, config: MultiAgentConfig | None = None) -> None:
        self.config = config or MultiAgentConfig()
        planner = PlannerAgent()
        repository = RepositoryAgent()
        developer = DeveloperAgent()
        tester = TesterAgent()
        reviewer = ReviewerAgent()
        builder = StateGraph(AgentState)
        builder.add_node("planner", lambda state: planner.plan(state, self.config))
        builder.add_node("repository", lambda state: repository.inspect(state, self.config))
        builder.add_node("developer", lambda state: developer.edit(state, self.config))
        builder.add_node("tester", lambda state: tester.test(state, self.config))
        builder.add_node(
            "reviewer", lambda state: reviewer.review(cast(AgentState, state), self.config)
        )
        builder.add_node("finalize", _finalize)
        builder.add_edge(START, "planner")
        builder.add_edge("planner", "repository")
        builder.add_conditional_edges(
            "repository", route_after_repository, {"developer": "developer", "finalize": "finalize"}
        )
        builder.add_conditional_edges(
            "developer", route_after_developer, {"tester": "tester", "finalize": "finalize"}
        )
        builder.add_conditional_edges(
            "tester",
            lambda state: route_after_tester(state, self.config),
            {
                "tester": "tester",
                "developer": "developer",
                "reviewer": "reviewer",
                "finalize": "finalize",
            },
        )
        builder.add_conditional_edges(
            "reviewer",
            lambda state: route_after_reviewer(state, self.config),
            {"developer": "developer", "finalize": "finalize"},
        )
        builder.add_edge("finalize", END)
        self.graph = builder.compile()

    def run(self, initial: AgentState) -> AgentState:
        state: AgentState = {
            "run_id": str(uuid.uuid4()),
            "repository_id": "local-fixture",
            "completed_steps": [],
            "failed_steps": [],
            "evidence": [],
            "artifacts": [],
            "tool_history": [],
            "retrieved_memories": [],
            "route_history": [],
            "retry_count": 0,
            "tool_call_count": 0,
            "token_usage": 0,
            "state_version": 0,
            "status": "queued",
            "test_results": {},
            "review_result": None,
            "fault_injection": {},
            **initial,
        }
        return cast(AgentState, self.graph.invoke(state))
