"""Week 10 deterministic chaos scenarios for runtime and context safety."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .context import ArtifactStore, ContextAssembler, ContextItem
from .runtime import InMemoryTaskQueue, SQLiteCheckpointStore, StateConflict, TaskRuntime


def _result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"scenario": name, "passed": passed, "detail": detail}


def run_runtime_chaos_suite(root: Path | None = None) -> dict[str, Any]:
    """Run failure scenarios without external services or a GPU."""

    with tempfile.TemporaryDirectory(prefix="repoevo-chaos-") as directory:
        base = Path(directory) if root is None else root
        base.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, Any]] = []

        store_path = base / "restart.db"
        queue = InMemoryTaskQueue()
        runtime = TaskRuntime(SQLiteCheckpointStore(store_path), queue)
        run_id = runtime.create_run("chaos-restart", {"step": "planner"})
        runtime.claim_next("worker-a")
        runtime.checkpoint(run_id, {"status": "running", "step": "developer"}, 1)
        recovered = TaskRuntime(SQLiteCheckpointStore(store_path), queue).store.load_run(run_id)
        results.append(
            _result(
                "worker_restart",
                recovered is not None and recovered["state"]["step"] == "developer",
                "checkpoint restored after a new runtime instance",
            )
        )

        duplicate_queue = InMemoryTaskQueue()
        duplicate_runtime = TaskRuntime(SQLiteCheckpointStore(base / "duplicate.db"), duplicate_queue)
        duplicate_id = duplicate_runtime.create_run("chaos-duplicate", {})
        duplicate_queue.enqueue(duplicate_id)
        first = duplicate_runtime.claim_next("worker-a")
        second = duplicate_runtime.claim_next("worker-a")
        results.append(
            _result(
                "duplicate_delivery",
                first is not None and second is None,
                "a second queue delivery cannot claim a running task",
            )
        )

        stale_runtime = TaskRuntime(SQLiteCheckpointStore(base / "stale.db"), InMemoryTaskQueue())
        stale_id = stale_runtime.create_run("chaos-stale", {})
        stale_runtime.claim_next("worker-a")
        stale_runtime.checkpoint(stale_id, {"status": "running", "step": "one"}, 1)
        try:
            stale_runtime.checkpoint(stale_id, {"status": "running", "step": "stale"}, 1)
        except StateConflict:
            stale_passed = True
        else:
            stale_passed = False
        results.append(_result("stale_checkpoint", stale_passed, "optimistic version conflict was rejected"))

        artifact_store = ArtifactStore(base / "artifacts")
        context = ContextAssembler(artifact_store, max_chars=2000).assemble(
            [ContextItem(kind="tool_output", source="chaos", content="output\n" * 10000)]
        )
        results.append(
            _result(
                "oversized_tool_output",
                context.compressed and bool(context.artifact_refs) and context.char_count <= 2000,
                "full output moved to Artifact and only a bounded preview remained",
            )
        )
        return {
            "scenario_count": len(results),
            "passed_count": sum(item["passed"] for item in results),
            "all_passed": all(item["passed"] for item in results),
            "scenarios": results,
        }
