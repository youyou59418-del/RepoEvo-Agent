from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_evaluator() -> ModuleType:
    path = ROOT / "scripts" / "run_baseline_eval.py"
    spec = importlib.util.spec_from_file_location("repoevo_baseline_eval", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_summary_keeps_public_hidden_and_end_to_end_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _load_evaluator()

    def fake_run_one(task: dict[str, Any], repeat: int, model_mode: str) -> dict[str, Any]:
        assert task["task_id"] == "inventory-v2-001"
        assert model_mode == "openai_compatible"
        return {
            "task_id": task["task_id"],
            "repeat": repeat,
            "status": "completed" if repeat == 0 else "budget_exhausted",
            "success": repeat == 0,
            "public_test_passed": True,
            "hidden_test_passed": repeat == 0,
            "duration_ms": 10.0,
            "tool_call_count": 3,
        }

    monkeypatch.setattr(evaluator, "run_one", fake_run_one)
    rows, summary = evaluator.evaluate(2, "openai_compatible", ["inventory-v2-001"])

    assert len(rows) == 2
    assert summary["schema_version"] == 2
    assert summary["public_test_pass_count"] == 2
    assert summary["hidden_test_pass_count"] == 1
    assert summary["success_count"] == 1
    assert summary["end_to_end_success_rate"] == 0.5
    assert summary["selected_task_ids"] == ["inventory-v2-001"]
    assert "git_revision" in summary["run_metadata"]
    assert summary["run_metadata"]["agent_limits"]["max_tool_calls"] == 12
    assert summary["run_metadata"]["model"]["replace_max_tokens"] == 384


def test_unknown_task_id_is_rejected_before_execution() -> None:
    evaluator = _load_evaluator()
    with pytest.raises(ValueError, match="UNKNOWN_TASK_IDS:not-a-task"):
        evaluator.evaluate(1, "offline_oracle", ["not-a-task"])
