from __future__ import annotations

import subprocess
from pathlib import Path

from repoevo.multi_agent import (
    AgentState,
    MultiAgentConfig,
    MultiAgentRunner,
    TaskPlan,
    route_after_tester,
)
from repoevo.sandbox_client import SandboxResult

PATCH = """diff --git a/app.py b/app.py
index 3d3f3d3..a8e9f3a 100644
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""


def init_git(root: Path) -> None:
    for args in [
        ["git", "init", "-q"],
        ["git", "config", "user.name", "Week5 Test"],
        ["git", "config", "user.email", "week5@test"],
        ["git", "add", "."],
        ["git", "commit", "-qm", "baseline"],
    ]:
        result = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def passing_tests(*args: object, **kwargs: object) -> SandboxResult:
    return SandboxResult(
        ok=True,
        returncode=0,
        timed_out=False,
        stdout="1 passed",
        stderr="",
        error_code=None,
        command=["pytest", "-q"],
        files=["app.py"],
        limits={},
    )


def test_planner_creates_independent_role_plan() -> None:
    from repoevo.multi_agent import PlannerAgent

    plan = PlannerAgent().plan(
        {"user_request": "repair", "acceptance_criteria": ["tests pass"]},
        MultiAgentConfig(),
    )["plan"]
    parsed = TaskPlan.model_validate(plan)
    assert [step.assigned_agent for step in parsed.steps] == [
        "repository",
        "developer",
        "tester",
        "reviewer",
    ]
    assert parsed.steps[1].depends_on == ["repository-1"]


def test_test_environment_failure_routes_to_tester_once() -> None:
    state: AgentState = {
        "test_results": {"ok": False},
        "failure_category": "TEST_ENVIRONMENT",
        "retry_count": 1,
        "status": "running",
    }
    assert route_after_tester(state, MultiAgentConfig()) == "tester"


def test_multi_agent_completes_and_records_route(monkeypatch: object, tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)
    monkeypatch.setattr("repoevo.multi_agent.run_repository_tests", passing_tests)
    state = MultiAgentRunner().run(
        {
            "task_id": "week5-001",
            "user_request": "set the value to two",
            "acceptance_criteria": ["VALUE == 2"],
            "repository_root": str(tmp_path),
            "reference_patch": PATCH,
        }
    )
    assert state["status"] == "completed"
    assert state["retry_count"] == 0
    assert state["completed_steps"] == ["repository-1", "developer-1", "tester-1", "reviewer-1"]
    assert [item["to"] for item in state["route_history"]] == ["repository"]
    assert state["review_result"]["ok"] is True


def test_transient_failure_is_retried_without_duplicate_patch(monkeypatch: object, tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)
    monkeypatch.setattr("repoevo.multi_agent.run_repository_tests", passing_tests)
    state = MultiAgentRunner().run(
        {
            "task_id": "week5-retry",
            "user_request": "set the value to two",
            "acceptance_criteria": ["VALUE == 2"],
            "repository_root": str(tmp_path),
            "reference_patch": PATCH,
            "fault_injection": {"transient_test_failure": True},
        }
    )
    assert state["status"] == "completed"
    assert state["retry_count"] == 1
    assert state["tool_call_count"] == 5
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
