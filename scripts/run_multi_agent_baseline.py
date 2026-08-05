"""Run the deterministic Week 5 Multi-Agent workflow on all benchmark tasks."""

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
from repoevo.multi_agent import AgentState, MultiAgentConfig, MultiAgentRunner
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
    run_git(root, "config", "user.name", "RepoEvo Multi-Agent")
    run_git(root, "config", "user.email", "multi-agent@localhost")
    run_git(root, "add", ".")
    run_git(root, "commit", "-qm", "baseline")


def run_one(task: dict[str, Any], *, retry_demo: bool = False) -> dict[str, Any]:
    task = load_private_task(str(task["task_id"]))
    fixture = ROOT / "fixtures" / str(task["repository"])
    hidden_test = private_hidden_test_path(str(task["task_id"]))
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"repoevo-multi-{task['task_id']}-") as directory:
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
        initial: AgentState = {
            "task_id": str(task["task_id"]),
            "user_request": str(task["request"]),
            "acceptance_criteria": list(task["acceptance_conditions"]),
            "repository_root": str(workspace),
            "reference_patch": str(task["reference_patch"]),
            "fault_injection": {"transient_test_failure": retry_demo},
        }
        state = MultiAgentRunner(MultiAgentConfig()).run(initial)
        hidden_target = workspace / "tests" / "test_hidden.py"
        hidden_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hidden_test, hidden_target)
        hidden = run_repository_tests(workspace)
        public = dict(state.get("test_results", {}))
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        success = state.get("status") == "completed" and public.get("ok") is True and hidden.ok
        return {
            "task_id": task["task_id"],
            "model_mode": "deterministic_role_harness",
            "success": success,
            "status": state.get("status"),
            "duration_ms": elapsed_ms,
            "tool_call_count": state.get("tool_call_count", 0),
            "retry_count": state.get("retry_count", 0),
            "failure_category": state.get("failure_category"),
            "public_test": public,
            "hidden_test": {"ok": hidden.ok, "error_code": hidden.error_code},
            "completed_steps": state.get("completed_steps", []),
            "route_history": state.get("route_history", []),
            "tool_history": state.get("tool_history", []),
            "review_result": state.get("review_result"),
        }


def evaluate() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = load_public_manifest()
    rows: list[dict[str, Any]] = []
    for task_id in manifest["tasks"]:
        task: dict[str, Any] = {"task_id": task_id}
        rows.append(run_one(task))
    successful = sum(row.get("success") is True for row in rows)
    retry_runs = sum(row.get("retry_count", 0) > 0 for row in rows)
    summary = {
        "schema_version": 1,
        "architecture": "langgraph_multi_agent",
        "model_mode": "deterministic_role_harness",
        "note": "Engineering workflow baseline; not an LLM capability score.",
        "task_count": len(rows),
        "run_count": len(rows),
        "success_count": successful,
        "task_success_rate": successful / len(rows) if rows else 0.0,
        "retry_run_count": retry_runs,
        "average_tool_calls": (
            sum(int(row["tool_call_count"]) for row in rows) / len(rows) if rows else None
        ),
        "average_duration_ms": (
            sum(float(row["duration_ms"]) for row in rows) / len(rows) if rows else None
        ),
    }
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".artifacts" / "multi_agent")
    args = parser.parse_args()
    rows, summary = evaluate()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "runs.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
