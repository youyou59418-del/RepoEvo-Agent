"""Run 50-case single-vs-multi ablation plus runtime Chaos scenarios."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repoevo.benchmark_access import (
    load_private_task,
    load_public_manifest,
    private_hidden_test_path,
)
from repoevo.chaos import run_runtime_chaos_suite
from repoevo.evaluation import build_evaluation_cases, summarize_rows
from repoevo.model_adapter import OfflineRepairModel
from repoevo.multi_agent import AgentState, MultiAgentConfig, MultiAgentRunner
from repoevo.single_agent import AgentConfig, BaselineState, SingleAgent
from repoevo.tool_layer import apply_patch, run_repository_tests

ROOT = Path(__file__).resolve().parents[1]


def run_git(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "GIT_COMMAND_FAILED")


def init_workspace(root: Path) -> None:
    run_git(root, "init", "-q")
    run_git(root, "config", "user.name", "RepoEvo Week10")
    run_git(root, "config", "user.email", "week10@localhost")
    run_git(root, "add", ".")
    run_git(root, "commit", "-qm", "baseline")


def run_case(task: dict[str, Any], case: dict[str, Any], architecture: str) -> dict[str, Any]:
    task = load_private_task(str(task["task_id"]))
    fixture = ROOT / "fixtures" / str(task["repository"])
    hidden_test = private_hidden_test_path(str(task["task_id"]))
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"repoevo-week10-{case['case_id']}-") as directory:
        workspace = Path(directory)
        shutil.copytree(
            fixture,
            workspace,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
        init_workspace(workspace)
        apply_patch(workspace, str(task["bug_patch"]))
        run_git(workspace, "add", ".")
        run_git(workspace, "commit", "-qm", "injected task bug")
        if architecture == "single_agent":
            initial: BaselineState = {
                "task_id": str(task["task_id"]),
                "request": str(task["request"]),
                "acceptance_criteria": list(task["acceptance_conditions"]),
                "repository_root": str(workspace),
            }
            state = SingleAgent(
                OfflineRepairModel(str(task["reference_patch"])),
                AgentConfig(max_tool_calls=8, max_runtime_seconds=120, max_files_changed=8),
            ).run(initial)
            status = state.get("status")
            public_ok = dict(state.get("test_results", {})).get("ok") is True
            tool_calls = int(state.get("tool_call_count", 0))
        else:
            initial_multi: AgentState = {
                "task_id": str(task["task_id"]),
                "user_request": str(task["request"]),
                "acceptance_criteria": list(task["acceptance_conditions"]),
                "repository_root": str(workspace),
                "reference_patch": str(task["reference_patch"]),
            }
            state_multi = MultiAgentRunner(MultiAgentConfig()).run(initial_multi)
            status = state_multi.get("status")
            public_ok = dict(state_multi.get("test_results", {})).get("ok") is True
            tool_calls = int(state_multi.get("tool_call_count", 0))
        hidden_target = workspace / "tests" / "test_hidden.py"
        hidden_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hidden_test, hidden_target)
        hidden = run_repository_tests(workspace)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "case_id": case["case_id"],
            "template_task_id": case["template_task_id"],
            "architecture": architecture,
            "success": status == "completed" and public_ok and hidden.ok,
            "status": status,
            "tool_call_count": tool_calls,
            "duration_ms": elapsed_ms,
            "public_ok": public_ok,
            "hidden_ok": hidden.ok,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-count", type=int, default=50)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".artifacts" / "week10")
    args = parser.parse_args()
    manifest = load_public_manifest()
    cases = build_evaluation_cases(manifest["tasks"], args.case_count)
    rows: list[dict[str, Any]] = []
    for case in cases:
        task: dict[str, Any] = {"task_id": case["template_task_id"]}
        rows.append(run_case(task, case, "single_agent"))
        rows.append(run_case(task, case, "multi_agent"))
    single = [row for row in rows if row["architecture"] == "single_agent"]
    multi = [row for row in rows if row["architecture"] == "multi_agent"]
    ablation = {
        "single_agent": summarize_rows(single),
        "multi_agent": summarize_rows(multi),
        "success_rate_delta_multi_minus_single": summarize_rows(multi)["task_success_rate"]
        - summarize_rows(single)["task_success_rate"],
    }
    chaos = run_runtime_chaos_suite(args.output_dir / "chaos")
    summary = {
        "schema_version": 1,
        "case_count": args.case_count,
        "template_task_count": len(manifest["tasks"]),
        "note": "50 evaluation cases cycle 20 real task templates; they are not claimed as 50 unique repositories.",
        "ablation": ablation,
        "chaos": chaos,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "cases.jsonl").write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8"
    )
    (args.output_dir / "runs.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
