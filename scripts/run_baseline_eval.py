"""Repeatable Week 4 single-Agent baseline evaluator."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repoevo.benchmark_access import (
    load_private_task,
    load_public_manifest,
    private_hidden_test_path,
)
from repoevo.model_adapter import (
    LLMSettings,
    OfflineRepairModel,
    OpenAICompatibleDecisionModel,
)
from repoevo.single_agent import AgentConfig, BaselineState, SingleAgent
from repoevo.tool_layer import apply_patch, run_repository_tests

ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = AgentConfig(
    max_tool_calls=12,
    max_runtime_seconds=120,
    max_files_changed=8,
    test_timeout_seconds=30,
)


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
    run_git(root, "config", "user.name", "RepoEvo Baseline")
    run_git(root, "config", "user.email", "baseline@localhost")
    run_git(root, "add", ".")
    run_git(root, "commit", "-qm", "baseline")


def hidden_test_result(workspace: Path, hidden_test: Path) -> dict[str, Any]:
    target = workspace / "tests" / "test_hidden.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(hidden_test, target)
    result = run_repository_tests(workspace)
    return {
        "ok": result.ok,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "error_code": result.error_code,
    }


def run_one(
    task: dict[str, Any], repeat: int, model_mode: str = "offline_oracle"
) -> dict[str, Any]:
    task = load_private_task(str(task["task_id"]))
    fixture = ROOT / "fixtures" / str(task["repository"])
    hidden_test = private_hidden_test_path(str(task["task_id"]))
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"repoevo-baseline-{task['task_id']}-") as directory:
        workspace = Path(directory)
        shutil.copytree(
            fixture,
            workspace,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
        init_workspace(workspace)
        # The Agent starts from the deliberately broken task state. The
        # injected bug is evaluator setup, not an Agent tool call.
        apply_patch(workspace, str(task["bug_patch"]))
        run_git(workspace, "add", ".")
        run_git(workspace, "commit", "-qm", "injected task bug")
        if model_mode == "offline_oracle":
            model = OfflineRepairModel(str(task["reference_patch"]))
        elif model_mode == "openai_compatible":
            model = OpenAICompatibleDecisionModel(LLMSettings())
        else:
            raise ValueError(f"UNSUPPORTED_MODEL_MODE: {model_mode}")
        agent = SingleAgent(model, AGENT_CONFIG)
        initial: BaselineState = {
            "task_id": str(task["task_id"]),
            "request": str(task["request"]),
            "acceptance_criteria": list(task["acceptance_conditions"]),
            "repository_root": str(workspace),
        }
        state = agent.run(initial)
        hidden = hidden_test_result(workspace, hidden_test)
        public = dict(state.get("test_results", {}))
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        success = (
            state.get("status") == "completed"
            and public.get("ok") is True
            and hidden.get("ok") is True
        )
        return {
            "task_id": task["task_id"],
            "repository": task["repository"],
            "repeat": repeat,
            "seed": repeat,
            "model_mode": model_mode,
            "status": state.get("status"),
            "success": success,
            "public_test_passed": public.get("ok") is True,
            "hidden_test_passed": hidden.get("ok") is True,
            "duration_ms": elapsed_ms,
            "tool_call_count": state.get("tool_call_count", 0),
            "retry_count": state.get("retry_count", 0),
            "changed_files": state.get("changed_files", []),
            "public_test": public,
            "hidden_test": hidden,
            "final_message": state.get("final_message", ""),
            "final_diff": state.get("final_diff", ""),
            "tool_history": state.get("tool_history", []),
            "messages": state.get("messages", []),
        }


def _selected_task_ids(task_ids: list[str] | None) -> list[str]:
    manifest = load_public_manifest()
    available = [str(task_id) for task_id in manifest["tasks"]]
    if task_ids is None:
        return available
    requested = list(dict.fromkeys(task_ids))
    unknown = sorted(set(requested).difference(available))
    if unknown:
        raise ValueError(f"UNKNOWN_TASK_IDS:{','.join(unknown)}")
    requested_set = set(requested)
    return [task_id for task_id in available if task_id in requested_set]


def _git_revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        shell=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _run_metadata(model_mode: str) -> dict[str, Any]:
    manifest = ROOT / "benchmarks" / "manifest.json"
    metadata: dict[str, Any] = {
        "git_revision": _git_revision(),
        "benchmark_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "agent_limits": {
            "max_tool_calls": AGENT_CONFIG.max_tool_calls,
            "max_runtime_seconds": AGENT_CONFIG.max_runtime_seconds,
            "max_files_changed": AGENT_CONFIG.max_files_changed,
            "test_timeout_seconds": AGENT_CONFIG.test_timeout_seconds,
        },
    }
    if model_mode == "openai_compatible":
        settings = LLMSettings()
        metadata["model"] = {
            "id": settings.llm_model,
            "base_url": settings.llm_base_url,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
            "replace_max_tokens": min(
                settings.llm_replace_max_tokens,
                settings.llm_max_tokens,
            ),
        }
    return metadata


def evaluate(
    repetitions: int,
    model_mode: str = "offline_oracle",
    task_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected_task_ids = _selected_task_ids(task_ids)
    started_at = datetime.now(UTC).isoformat()
    rows: list[dict[str, Any]] = []
    for task_id in selected_task_ids:
        task: dict[str, Any] = {"task_id": task_id}
        for repeat in range(repetitions):
            try:
                row = run_one(task, repeat, model_mode)
            except Exception as exc:  # noqa: BLE001 - preserve model endpoint failures as rows
                row = {
                    "task_id": task_id,
                    "repeat": repeat,
                    "seed": repeat,
                    "model_mode": model_mode,
                    "success": False,
                    "public_test_passed": False,
                    "hidden_test_passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            rows.append(row)
    successful = sum(row.get("success") is True for row in rows)
    public_passed = sum(row.get("public_test_passed") is True for row in rows)
    hidden_passed = sum(row.get("hidden_test_passed") is True for row in rows)
    durations = [float(row["duration_ms"]) for row in rows if "duration_ms" in row]
    calls = [int(row["tool_call_count"]) for row in rows if "tool_call_count" in row]
    summary = {
        "schema_version": 2,
        "model_mode": model_mode,
        "note": (
            "This is an engineering smoke baseline, not an LLM capability score."
            if model_mode == "offline_oracle"
            else "Real OpenAI-compatible model run. Preserve all failures and traces."
        ),
        "task_count": len(selected_task_ids),
        "selected_task_ids": selected_task_ids,
        "repetitions": repetitions,
        "run_count": len(rows),
        "success_count": successful,
        "task_success_rate": successful / len(rows) if rows else 0.0,
        "end_to_end_success_rate": successful / len(rows) if rows else 0.0,
        "public_test_pass_count": public_passed,
        "public_test_pass_rate": public_passed / len(rows) if rows else 0.0,
        "hidden_test_pass_count": hidden_passed,
        "hidden_test_pass_rate": hidden_passed / len(rows) if rows else 0.0,
        "status_counts": dict(Counter(str(row.get("status", "error")) for row in rows)),
        "error_count": sum("error" in row for row in rows),
        "average_duration_ms": sum(durations) / len(durations) if durations else None,
        "average_tool_calls": sum(calls) / len(calls) if calls else None,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "run_metadata": _run_metadata(model_mode),
    }
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--model-mode",
        choices=["offline_oracle", "openai_compatible"],
        default="offline_oracle",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
        help="Evaluate only this task ID; repeat the option to select several tasks.",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".artifacts" / "baseline")
    args = parser.parse_args()
    if args.repetitions <= 0:
        raise SystemExit("--repetitions must be positive")
    rows, summary = evaluate(args.repetitions, args.model_mode, args.task_ids)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "runs.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
