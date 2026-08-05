from __future__ import annotations

from pathlib import Path

from repoevo.chaos import run_runtime_chaos_suite
from repoevo.evaluation import build_evaluation_cases, summarize_rows


def test_evaluation_suite_has_50_cases_from_known_templates() -> None:
    cases = build_evaluation_cases(["order-001", "inventory-001", "report-001"])
    assert len(cases) == 50
    assert cases[0]["case_id"] == "case-001"
    assert cases[-1]["case_id"] == "case-050"
    assert len({case["template_task_id"] for case in cases}) == 3


def test_summary_reports_real_rows() -> None:
    summary = summarize_rows(
        [
            {"success": True, "duration_ms": 10, "tool_call_count": 3},
            {"success": False, "duration_ms": 20, "tool_call_count": 5},
        ]
    )
    assert summary["case_count"] == 2
    assert summary["success_count"] == 1
    assert summary["task_success_rate"] == 0.5


def test_runtime_chaos_suite_passes(tmp_path: Path) -> None:
    summary = run_runtime_chaos_suite(tmp_path)
    assert summary["all_passed"] is True
    assert summary["passed_count"] == summary["scenario_count"] == 4
