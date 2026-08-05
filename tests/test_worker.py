from __future__ import annotations

from pathlib import Path

from repoevo.runtime import SQLiteCheckpointStore, SQLiteTaskQueue, TaskRuntime
from repoevo.worker import AgentWorker


def test_sqlite_queue_is_visible_across_runtime_instances(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    store = SQLiteCheckpointStore(db)
    producer = TaskRuntime(store, SQLiteTaskQueue(db))
    run_id = producer.create_run("cross-process", {})
    consumer = TaskRuntime(SQLiteCheckpointStore(db), SQLiteTaskQueue(db))
    claimed = consumer.claim_next("worker-b")
    assert claimed is not None
    assert claimed["run_id"] == run_id
    assert claimed["status"] == "running"


def test_sqlite_queue_discard_removes_all_matching_entries(tmp_path: Path) -> None:
    queue = SQLiteTaskQueue(tmp_path / "queue.db")
    queue.enqueue("stale")
    queue.enqueue("ready")
    queue.enqueue("stale")

    queue.discard("stale")

    assert queue.claim() == "ready"
    assert queue.claim() is None


def test_worker_records_invalid_task_input_in_checkpoint(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    runtime = TaskRuntime(SQLiteCheckpointStore(db), SQLiteTaskQueue(db))
    run_id = runtime.create_run("invalid-worker-task", {})
    result = AgentWorker(runtime, worker_id="worker-test").run_once()
    assert result is not None
    assert result["run_id"] == run_id
    assert result["status"] == "failed"
    record = runtime.store.load_run(run_id)
    assert record is not None
    assert "WORKER_INPUT_MISSING" in record["state"]["worker_error"]
