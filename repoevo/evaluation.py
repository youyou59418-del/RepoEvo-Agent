"""Helpers for the Week 10 50-case evaluation and ablation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def build_evaluation_cases(
    template_task_ids: Iterable[str], count: int = 50
) -> list[dict[str, Any]]:
    templates = list(template_task_ids)
    if not templates or count <= 0:
        raise ValueError("EVALUATION_CASES_INVALID")
    return [
        {
            "case_id": f"case-{index + 1:03d}",
            "template_task_id": templates[index % len(templates)],
            "seed": index,
        }
        for index in range(count)
    ]


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    successful = sum(row.get("success") is True for row in rows)
    durations = [float(row["duration_ms"]) for row in rows if "duration_ms" in row]
    calls = [int(row["tool_call_count"]) for row in rows if "tool_call_count" in row]
    return {
        "case_count": len(rows),
        "success_count": successful,
        "task_success_rate": successful / len(rows) if rows else 0.0,
        "average_duration_ms": sum(durations) / len(durations) if durations else None,
        "average_tool_calls": sum(calls) / len(calls) if calls else None,
    }
