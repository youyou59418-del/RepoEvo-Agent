from __future__ import annotations

import json
from pathlib import Path

import pytest

from repoevo.benchmark_access import (
    PRIVATE_BENCHMARK_ENV,
    PrivateBenchmarkUnavailable,
    load_private_task,
    private_hidden_test_path,
)


def test_private_benchmark_requires_a_complete_local_pack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(PRIVATE_BENCHMARK_ENV, str(tmp_path / "missing"))

    with pytest.raises(PrivateBenchmarkUnavailable, match="PRIVATE_BENCHMARKS_REQUIRED"):
        load_private_task("order-001")


def test_private_benchmark_reads_task_and_hidden_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_root = tmp_path / "private"
    (private_root / "tasks").mkdir(parents=True)
    (private_root / "hidden").mkdir()
    (private_root / "manifest.json").write_text("{}", encoding="utf-8")
    (private_root / "tasks" / "demo-001.json").write_text(
        json.dumps(
            {
                "task_id": "demo-001",
                "repository": "demo",
                "request": "修复示例",
                "acceptance_conditions": [],
                "bug_patch": "patch",
                "reference_patch": "reference",
            }
        ),
        encoding="utf-8",
    )
    (private_root / "hidden" / "demo-001.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    monkeypatch.setenv(PRIVATE_BENCHMARK_ENV, str(private_root))

    assert load_private_task("demo-001")["reference_patch"] == "reference"
    assert private_hidden_test_path("demo-001").name == "demo-001.py"
