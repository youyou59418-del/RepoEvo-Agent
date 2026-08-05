"""Claim and execute one queued RepoEvo task using the persistent SQLite fallback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repoevo.runtime import (
    PostgresCheckpointStore,
    RedisTaskQueue,
    SQLiteCheckpointStore,
    SQLiteTaskQueue,
    TaskRuntime,
)
from repoevo.settings import RepoEvoSettings
from repoevo.worker import AgentWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RepoEvo Worker once or continuously.")
    parser.add_argument("--forever", action="store_true", help="Keep polling the queue after startup.")
    parser.add_argument("--idle-sleep", type=float, default=0.5, help="Seconds to sleep when the queue is empty.")
    args = parser.parse_args()
    settings = RepoEvoSettings()
    root = settings.repoevo_data_root
    store = (
        PostgresCheckpointStore(settings.checkpoint_dsn)
        if settings.checkpoint_dsn
        else SQLiteCheckpointStore(root / "task_runs.db")
    )
    queue = RedisTaskQueue(settings.redis_url) if settings.redis_url else SQLiteTaskQueue(root / "task_runs.db")
    runtime = TaskRuntime(store, queue)
    worker = AgentWorker(runtime, worker_id=settings.worker_id)
    if args.forever:
        worker.run_forever(idle_sleep_seconds=args.idle_sleep)
    else:
        print(json.dumps(worker.run_once(), ensure_ascii=False))


if __name__ == "__main__":
    main()
