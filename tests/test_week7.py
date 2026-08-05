from __future__ import annotations

from pathlib import Path

import pytest

from repoevo.runtime import (
    InMemoryTaskQueue,
    SQLiteCheckpointStore,
    StateConflict,
    TaskRuntime,
    idempotency_key,
)


def build_runtime(tmp_path: Path) -> tuple[TaskRuntime, SQLiteCheckpointStore, InMemoryTaskQueue]:
    store = SQLiteCheckpointStore(tmp_path / "runtime.db")
    queue = InMemoryTaskQueue()
    return TaskRuntime(store, queue), store, queue


def test_checkpoint_survives_new_runtime_instance(tmp_path: Path) -> None:
    runtime, store, queue = build_runtime(tmp_path)
    run_id = runtime.create_run("task-001", {"step": "planner"})
    claimed = runtime.claim_next("worker-a")
    assert claimed is not None
    version = runtime.checkpoint(run_id, {"status": "running", "step": "developer"}, 1)
    assert version == 2

    restarted = TaskRuntime(SQLiteCheckpointStore(tmp_path / "runtime.db"), queue)
    loaded = restarted.store.load_run(run_id)
    assert loaded is not None
    assert loaded["state"]["step"] == "developer"
    assert loaded["state_version"] == 2
    assert len(store.get_events(run_id)) >= 3


def test_pause_resume_cancel_and_idempotency(tmp_path: Path) -> None:
    runtime, store, queue = build_runtime(tmp_path)
    run_id = runtime.create_run("task-002", {})
    assert len(queue) == 1
    first_pause = runtime.pause(run_id)
    second_pause = runtime.pause(run_id)
    assert first_pause == second_pause
    assert store.load_run(run_id)["status"] == "paused"
    assert len(queue) == 0
    resumed = runtime.resume(run_id)
    assert resumed["status"] == "queued"
    assert len(queue) == 1
    cancelled = runtime.cancel(run_id)
    assert cancelled["status"] == "cancelled"
    assert len(queue) == 0
    other_run = runtime.create_run("task-002b", {})
    runtime.cancel(other_run)
    assert len(queue) == 0
    with pytest.raises(ValueError, match="INVALID_TRANSITION"):
        runtime.resume(other_run)


def test_claim_next_skips_legacy_nonqueued_queue_entries(tmp_path: Path) -> None:
    runtime, _, queue = build_runtime(tmp_path)
    stale_run = runtime.create_run("task-stale", {})
    runtime.cancel(stale_run)
    queue.enqueue(stale_run)
    ready_run = runtime.create_run("task-ready", {})

    claimed = runtime.claim_next("worker-a")

    assert claimed is not None
    assert claimed["run_id"] == ready_run
    assert len(queue) == 0


def test_stale_checkpoint_is_rejected(tmp_path: Path) -> None:
    runtime, _, _ = build_runtime(tmp_path)
    run_id = runtime.create_run("task-003", {})
    runtime.claim_next("worker-a")
    runtime.checkpoint(run_id, {"status": "running", "step": "one"}, 1)
    with pytest.raises(StateConflict):
        runtime.checkpoint(run_id, {"status": "running", "step": "stale"}, 1)


def test_idempotency_key_normalizes_json() -> None:
    first = idempotency_key("run", "step", "tool", {"b": 2, "a": 1})
    second = idempotency_key("run", "step", "tool", {"a": 1, "b": 2})
    assert first == second
