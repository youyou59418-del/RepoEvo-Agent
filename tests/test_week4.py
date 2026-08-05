from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repoevo.model_adapter import (
    AgentDecision,
    LLMSettings,
    ModelConfigError,
    OfflineRepairModel,
    OpenAICompatibleDecisionModel,
    ReplaceTextDecision,
    build_prompt_context,
    create_llm,
)
from repoevo.sandbox_client import SandboxResult
from repoevo.single_agent import AgentConfig, BaselineState, SingleAgent

PATCH = """diff --git a/app.py b/app.py
index 3d3f3d3..a8e9f3a 100644
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""


def test_missing_model_configuration_is_explicit() -> None:
    with pytest.raises(ModelConfigError, match="LLM_NOT_CONFIGURED"):
        create_llm(LLMSettings(llm_base_url=None, llm_api_key=None, llm_model=None))


def test_offline_model_has_one_action_per_turn() -> None:
    model = OfflineRepairModel(PATCH)
    state: BaselineState = {"tool_history": [], "test_results": {}}
    assert model.decide(state).action == "read_file"
    state["tool_history"] = [{"action": "read_file", "ok": True}]
    assert model.decide(state).action == "apply_patch"
    state["tool_history"].append({"action": "apply_patch", "ok": True})
    assert model.decide(state).action == "run_tests"
    state["test_results"] = {"ok": True}
    assert model.decide(state).action == "finish"


def test_empty_model_test_command_uses_the_fixed_safe_profile() -> None:
    decision = AgentDecision(action="run_tests", command=[])
    assert decision.command == ["pytest", "-q"]


def test_model_response_budget_has_a_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)
    monkeypatch.delenv("LLM_REPLACE_MAX_TOKENS", raising=False)
    settings = LLMSettings(
        _env_file=None,
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
    )
    assert settings.llm_max_tokens == 512
    assert settings.llm_replace_max_tokens == 384


def test_model_context_keeps_evidence_without_duplicate_chatter() -> None:
    context = build_prompt_context(
        {
            "task_id": "context-001",
            "request": "repair the file",
            "repository_root": "/private/omitted",
            "messages": [{"role": "agent", "content": "duplicated chatter"}],
            "observed_files": {"app.py": "X" * 7_000},
            "last_tool_result": {"ok": True, "content": "X" * 7_000},
            "test_results": {"ok": False, "stdout": "Y" * 3_000},
            "tool_history": [{"action": "read_file", "ok": True}] * 10,
        }
    )

    assert "repository_root" not in context
    assert "messages" not in context
    assert "content" not in context["last_tool_result"]
    assert len(context["observed_files"]["app.py"]) < 3_100
    assert len(context["test_results"]["stdout"]) < 2_100
    assert len(context["tool_history"]) == 8


def test_model_uses_exact_replacement_schema_after_reading_source() -> None:
    class StructuredResponse:
        def __init__(self, value: object) -> None:
            self.value = value

        def invoke(self, messages: object) -> object:
            return self.value

    class FakeLLM:
        def bind(self, **kwargs: object) -> FakeLLM:
            return self

        def with_structured_output(self, schema: object) -> StructuredResponse:
            if schema is ReplaceTextDecision:
                return StructuredResponse(
                    ReplaceTextDecision(
                        path="app.py",
                        old_text="VALUE = 1",
                        new_text="VALUE = 2",
                    )
                )
            return StructuredResponse(AgentDecision(action="finish"))

    decision = OpenAICompatibleDecisionModel(llm=FakeLLM()).decide(
        {
            "request": "set the value to two",
            "observed_files": {"app.py": "VALUE = 1\n"},
            "tool_history": [{"action": "read_file", "ok": True}],
            "changed_files": [],
            "test_results": {},
        }
    )
    assert decision.action == "replace_text"
    assert decision.old_text == "VALUE = 1"
    assert decision.new_text == "VALUE = 2"


def test_model_forces_test_then_finish_after_a_successful_edit() -> None:
    class FakeLLM:
        def with_structured_output(self, schema: object) -> object:
            return object()

    model = OpenAICompatibleDecisionModel(llm=FakeLLM())
    after_edit = model.decide(
        {
            "tool_history": [{"action": "replace_text", "ok": True}],
            "changed_files": ["app.py"],
            "test_results": {},
        }
    )
    assert after_edit.action == "run_tests"

    after_test = model.decide(
        {
            "tool_history": [{"action": "run_tests", "ok": True}],
            "test_results": {"ok": True},
        }
    )
    assert after_test.action == "finish"


def test_model_refreshes_a_single_edited_file_after_a_failed_test() -> None:
    class FakeLLM:
        def with_structured_output(self, schema: object) -> object:
            return object()

    decision = OpenAICompatibleDecisionModel(llm=FakeLLM()).decide(
        {
            "tool_history": [{"action": "run_tests", "ok": False}],
            "changed_files": ["app.py"],
            "test_results": {"ok": False},
        }
    )
    assert decision.action == "read_file"
    assert decision.path == "app.py"


def init_git(root: Path) -> None:
    for args in [
        ["git", "init", "-q"],
        ["git", "config", "user.name", "Week4 Test"],
        ["git", "config", "user.email", "week4@test"],
        ["git", "add", "."],
        ["git", "commit", "-qm", "baseline"],
    ]:
        result = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_langgraph_single_agent_completes_bounded_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)

    def fake_tests(*args: object, **kwargs: object) -> SandboxResult:
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

    monkeypatch.setattr("repoevo.single_agent.run_repository_tests", fake_tests)
    agent = SingleAgent(OfflineRepairModel(PATCH), AgentConfig(max_tool_calls=8))
    state = agent.run(
        {
            "task_id": "unit-001",
            "request": "set the value to two",
            "acceptance_criteria": ["VALUE == 2"],
            "repository_root": str(tmp_path),
        }
    )
    assert state["status"] == "completed"
    assert state["tool_call_count"] == 3
    assert state["changed_files"] == ["app.py"]
    assert "VALUE = 2" in (tmp_path / "app.py").read_text(encoding="utf-8")
    assert [item["action"] for item in state["tool_history"]] == [
        "read_file",
        "apply_patch",
        "run_tests",
    ]


def test_langgraph_single_agent_applies_an_exact_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)

    def fake_tests(*args: object, **kwargs: object) -> SandboxResult:
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

    monkeypatch.setattr("repoevo.single_agent.run_repository_tests", fake_tests)

    class ReplacementModel:
        mode = "scripted_replacement"

        def decide(self, state: BaselineState) -> AgentDecision:
            history = state.get("tool_history", [])
            if not history:
                return AgentDecision(
                    action="replace_text",
                    path="app.py",
                    old_text="VALUE = 1",
                    new_text="VALUE = 2",
                )
            if len(history) == 1:
                return AgentDecision(action="run_tests")
            return AgentDecision(action="finish")

    state = SingleAgent(ReplacementModel()).run(
        {
            "task_id": "replacement-001",
            "request": "set the value to two",
            "acceptance_criteria": ["VALUE == 2"],
            "repository_root": str(tmp_path),
        }
    )
    assert state["status"] == "completed"
    assert state["changed_files"] == ["app.py"]
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
def test_agent_stops_at_tool_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)
    monkeypatch.setattr(
        "repoevo.single_agent.run_repository_tests",
        lambda *args, **kwargs: pytest.fail("test tool must not run after budget"),
    )
    agent = SingleAgent(OfflineRepairModel(PATCH), AgentConfig(max_tool_calls=1))
    state = agent.run(
        {
            "task_id": "budget-001",
            "request": "set the value to two",
            "acceptance_criteria": ["VALUE == 2"],
            "repository_root": str(tmp_path),
        }
    )
    assert state["status"] == "budget_exhausted"
    assert state["tool_call_count"] == 1


def test_agent_accepts_passing_tests_at_the_tool_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)

    def fake_tests(*args: object, **kwargs: object) -> SandboxResult:
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

    monkeypatch.setattr("repoevo.single_agent.run_repository_tests", fake_tests)

    class TestOnlyModel:
        mode = "scripted_test_only"

        def decide(self, state: BaselineState) -> AgentDecision:
            return AgentDecision(action="run_tests")

    state = SingleAgent(TestOnlyModel(), AgentConfig(max_tool_calls=1)).run(
        {
            "task_id": "budget-test-001",
            "request": "run tests",
            "acceptance_criteria": ["tests pass"],
            "repository_root": str(tmp_path),
        }
    )
    assert state["status"] == "completed"
    assert state["tool_call_count"] == 1


def test_agent_can_list_repository_files_before_reading(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)

    class DiscoveryModel:
        mode = "scripted_discovery"

        def decide(self, state: BaselineState) -> AgentDecision:
            if not state.get("tool_history"):
                return AgentDecision(action="list_files", reason="Discover available files.")
            return AgentDecision(action="finish", reason="Discovery complete.")

    state = SingleAgent(DiscoveryModel()).run(
        {
            "task_id": "discovery-001",
            "request": "inspect the repository",
            "acceptance_criteria": [],
            "repository_root": str(tmp_path),
        }
    )
    assert state["tool_history"][0]["action"] == "list_files"
    assert state["last_tool_result"]["files"] == ["app.py"]


def test_agent_keeps_observed_files_across_multiple_reads(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "test_public.py").write_text("assert VALUE == 2\n", encoding="utf-8")
    init_git(tmp_path)

    class RememberingModel:
        mode = "scripted_memory"

        def decide(self, state: BaselineState) -> AgentDecision:
            history = state.get("tool_history", [])
            if not history:
                return AgentDecision(action="list_files")
            if len(history) == 1:
                return AgentDecision(action="read_file", path="app.py")
            if len(history) == 2:
                return AgentDecision(action="read_file", path="test_public.py")
            assert state["repository_files"] == ["app.py", "test_public.py"]
            assert state["observed_files"] == {
                "app.py": "VALUE = 1\n",
                "test_public.py": "assert VALUE == 2\n",
            }
            return AgentDecision(action="finish", reason="Inspection complete.")

    state = SingleAgent(RememberingModel()).run(
        {
            "task_id": "memory-001",
            "request": "inspect two files",
            "acceptance_criteria": [],
            "repository_root": str(tmp_path),
        }
    )
    assert state["tool_call_count"] == 3
