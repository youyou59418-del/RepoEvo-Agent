"""Week 7 checkpoint, queue, lifecycle, and idempotency runtime.

SQLite and an in-memory queue are the zero-service fallback used by the
current AutoDL instance. PostgreSQL and Redis adapters implement the same
protocol for the production deployment described in the guide.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, cast


class StateConflict(RuntimeError):
    """Raised when a stale worker tries to overwrite a newer checkpoint."""


class RuntimeTransitionError(ValueError):
    """Raised when a lifecycle transition is not allowed."""


def idempotency_key(run_id: str, step_id: str, tool_name: str, arguments: Mapping[str, Any]) -> str:
    normalized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{run_id}|{step_id}|{tool_name}|{normalized}".encode()).hexdigest()


class CheckpointStore(Protocol):
    def create_run(self, run_id: str, task_id: str, state: Mapping[str, Any]) -> None: ...

    def load_run(self, run_id: str) -> dict[str, Any] | None: ...

    def save_state(self, run_id: str, state: Mapping[str, Any], expected_version: int) -> int: ...

    def append_event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> None: ...

    def get_events(self, run_id: str) -> list[dict[str, Any]]: ...

    def get_idempotency(self, run_id: str, key: str) -> dict[str, Any] | None: ...

    def put_idempotency(self, run_id: str, key: str, response: Mapping[str, Any]) -> None: ...


def _record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "task_id": row["task_id"],
        "status": row["status"],
        "state_version": int(row["state_version"]),
        "worker_id": row["worker_id"],
        "state": json.loads(row["payload"]),
    }


class SQLiteCheckpointStore:
    """Durable local checkpoint store with optimistic state versions."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state_version INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    run_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    response TEXT NOT NULL,
                    PRIMARY KEY (run_id, key)
                );
                """
            )

    def create_run(self, run_id: str, task_id: str, state: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(state), ensure_ascii=False, sort_keys=True)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO task_runs(run_id, task_id, status, payload) VALUES (?, ?, ?, ?)",
                (run_id, task_id, str(state.get("status", "queued")), payload),
            )
            connection.execute(
                "INSERT INTO task_events(run_id, event_type, payload) VALUES (?, ?, ?)",
                (run_id, "created", json.dumps({"task_id": task_id})),
            )

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM task_runs WHERE run_id = ?", (run_id,)).fetchone()
        return _record(row) if row else None

    def save_state(self, run_id: str, state: Mapping[str, Any], expected_version: int) -> int:
        payload = json.dumps(dict(state), ensure_ascii=False, sort_keys=True)
        status = str(state.get("status", "running"))
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE task_runs
                SET status = ?, payload = ?, state_version = state_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE run_id = ? AND state_version = ?
                """,
                (status, payload, run_id, expected_version),
            )
            if updated.rowcount != 1:
                raise StateConflict(f"CHECKPOINT_VERSION_CONFLICT:{run_id}:{expected_version}")
            version = expected_version + 1
            connection.execute(
                "INSERT INTO task_events(run_id, event_type, payload) VALUES (?, ?, ?)",
                (run_id, "checkpoint", json.dumps({"state_version": version, "status": status})),
            )
        return version

    def append_event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO task_events(run_id, event_type, payload) VALUES (?, ?, ?)",
                (run_id, event_type, json.dumps(dict(payload), ensure_ascii=False)),
            )

    def get_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_type, payload, created_at FROM task_events WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [
            {"event_type": row["event_type"], "payload": json.loads(row["payload"]), "created_at": row["created_at"]}
            for row in rows
        ]

    def get_idempotency(self, run_id: str, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT response FROM idempotency_keys WHERE run_id = ? AND key = ?",
                (run_id, key),
            ).fetchone()
        return json.loads(row["response"]) if row else None

    def put_idempotency(self, run_id: str, key: str, response: Mapping[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO idempotency_keys(run_id, key, response) VALUES (?, ?, ?)",
                (run_id, key, json.dumps(dict(response), ensure_ascii=False)),
            )


class PostgresCheckpointStore:
    """PostgreSQL implementation for deployment with CHECKPOINT_DSN."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("CHECKPOINT_DSN_REQUIRED")
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PSYCOPG_NOT_INSTALLED") from exc
        self._psycopg = psycopg
        self.dsn = dsn
        self._initialize()

    def _connect(self) -> Any:
        return self._psycopg.connect(self.dsn)

    def _initialize(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS task_runs (
                        run_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        state_version BIGINT NOT NULL DEFAULT 0,
                        worker_id TEXT,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    CREATE TABLE IF NOT EXISTS task_events (
                        id BIGSERIAL PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    CREATE TABLE IF NOT EXISTS idempotency_keys (
                        run_id TEXT NOT NULL,
                        key TEXT NOT NULL,
                        response JSONB NOT NULL,
                        PRIMARY KEY (run_id, key)
                    );
                    """
                )

    def create_run(self, run_id: str, task_id: str, state: Mapping[str, Any]) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO task_runs(run_id, task_id, status, payload) VALUES (%s, %s, %s, %s)",
                    (run_id, task_id, str(state.get("status", "queued")), json.dumps(dict(state))),
                )
                cursor.execute(
                    "INSERT INTO task_events(run_id, event_type, payload) VALUES (%s, %s, %s)",
                    (run_id, "created", json.dumps({"task_id": task_id})),
                )

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT run_id, task_id, status, state_version, worker_id, payload FROM task_runs WHERE run_id=%s",
                    (run_id,),
                )
                row = cursor.fetchone()
        if not row:
            return None
        return {
            "run_id": row[0],
            "task_id": row[1],
            "status": row[2],
            "state_version": int(row[3]),
            "worker_id": row[4],
            "state": row[5] if isinstance(row[5], dict) else json.loads(row[5]),
        }

    def save_state(self, run_id: str, state: Mapping[str, Any], expected_version: int) -> int:
        with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE task_runs SET status=%s, payload=%s, state_version=state_version+1,
                        updated_at=now() WHERE run_id=%s AND state_version=%s
                    """,
                    (str(state.get("status", "running")), json.dumps(dict(state)), run_id, expected_version),
                )
                if cursor.rowcount != 1:
                    raise StateConflict(f"CHECKPOINT_VERSION_CONFLICT:{run_id}:{expected_version}")
        return expected_version + 1

    def append_event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO task_events(run_id, event_type, payload) VALUES (%s, %s, %s)",
                    (run_id, event_type, json.dumps(dict(payload))),
                )

    def get_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT event_type, payload, created_at FROM task_events WHERE run_id=%s ORDER BY id",
                    (run_id,),
                )
                rows = cursor.fetchall()
        return [
            {"event_type": row[0], "payload": row[1], "created_at": row[2].isoformat()}
            for row in rows
        ]

    def get_idempotency(self, run_id: str, key: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT response FROM idempotency_keys WHERE run_id=%s AND key=%s", (run_id, key)
                )
                row = cursor.fetchone()
        return row[0] if row else None

    def put_idempotency(self, run_id: str, key: str, response: Mapping[str, Any]) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO idempotency_keys(run_id, key, response) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (run_id, key, json.dumps(dict(response))),
                )


class TaskQueue(Protocol):
    def enqueue(self, run_id: str) -> None: ...

    def claim(self, timeout_seconds: int = 0) -> str | None: ...

    def discard(self, run_id: str) -> None: ...

    def __len__(self) -> int: ...


class InMemoryTaskQueue:
    def __init__(self) -> None:
        self._items: deque[str] = deque()
        self._lock = threading.Lock()

    def enqueue(self, run_id: str) -> None:
        with self._lock:
            self._items.append(run_id)

    def claim(self, timeout_seconds: int = 0) -> str | None:
        del timeout_seconds
        with self._lock:
            return self._items.popleft() if self._items else None

    def discard(self, run_id: str) -> None:
        with self._lock:
            self._items = deque(item for item in self._items if item != run_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class SQLiteTaskQueue:
    """Cross-process queue fallback for hosts without a Redis service."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    enqueued_at REAL NOT NULL
                )
                """
            )

    def enqueue(self, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO task_queue(run_id, enqueued_at) VALUES (?, ?)",
                (run_id, time.time()),
            )

    def claim(self, timeout_seconds: int = 0) -> str | None:
        deadline = time.monotonic() + max(0, timeout_seconds)
        while True:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT id, run_id FROM task_queue ORDER BY id LIMIT 1"
                ).fetchone()
                if row is None:
                    connection.commit()
                else:
                    connection.execute("DELETE FROM task_queue WHERE id = ?", (row[0],))
                    connection.commit()
                    return str(row[1])
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def discard(self, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM task_queue WHERE run_id = ?", (run_id,))

    def __len__(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM task_queue").fetchone()
        return int(row[0]) if row else 0


class RedisTaskQueue:
    """Redis list adapter; duplicate deliveries remain safe through idempotency."""

    def __init__(self, url: str, key: str = "repoevo:task_queue") -> None:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("REDIS_NOT_INSTALLED") from exc
        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.key = key

    def enqueue(self, run_id: str) -> None:
        self.client.rpush(self.key, run_id)

    def claim(self, timeout_seconds: int = 0) -> str | None:
        item = cast(list[Any] | None, self.client.blpop([self.key], timeout=timeout_seconds))
        return str(item[1]) if item else None

    def discard(self, run_id: str) -> None:
        self.client.lrem(self.key, 0, run_id)

    def __len__(self) -> int:
        return cast(int, self.client.llen(self.key))


class TaskRuntime:
    """Lifecycle API shared by HTTP workers and command-line demos."""

    def __init__(self, store: CheckpointStore, queue: TaskQueue) -> None:
        self.store = store
        self.queue = queue

    def create_run(self, task_id: str, initial_state: Mapping[str, Any]) -> str:
        run_id = str(uuid.uuid4())
        state = {"status": "queued", "state_version": 0, **dict(initial_state)}
        self.store.create_run(run_id, task_id, state)
        self.queue.enqueue(run_id)
        return run_id

    def _command(
        self,
        run_id: str,
        *,
        command: str,
        target_status: str,
        allowed: set[str],
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = arguments or {}
        key = idempotency_key(run_id, command, command, args)
        previous = self.store.get_idempotency(run_id, key)
        if previous is not None:
            return previous
        record = self.store.load_run(run_id)
        if record is None:
            raise RuntimeTransitionError("RUN_NOT_FOUND")
        if record["status"] not in allowed:
            raise RuntimeTransitionError(f"INVALID_TRANSITION:{record['status']}->{target_status}")
        state = dict(record["state"])
        state["status"] = target_status
        version = self.store.save_state(run_id, state, int(record["state_version"]))
        response = {"run_id": run_id, "status": target_status, "state_version": version}
        self.store.append_event(run_id, command, response)
        self.store.put_idempotency(run_id, key, response)
        if command in {"pause", "cancel"}:
            self.queue.discard(run_id)
        elif command == "resume":
            self.queue.enqueue(run_id)
        return response

    def pause(self, run_id: str) -> dict[str, Any]:
        return self._command(run_id, command="pause", target_status="paused", allowed={"queued", "running"})

    def resume(self, run_id: str) -> dict[str, Any]:
        return self._command(run_id, command="resume", target_status="queued", allowed={"paused"})

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._command(
            run_id,
            command="cancel",
            target_status="cancelled",
            allowed={"queued", "running", "paused"},
        )

    def approve(self, run_id: str) -> dict[str, Any]:
        """Record an approval without changing the lifecycle status."""
        key = idempotency_key(run_id, "approval", "approve", {})
        previous = self.store.get_idempotency(run_id, key)
        if previous is not None:
            return previous
        record = self.store.load_run(run_id)
        if record is None:
            raise RuntimeTransitionError("RUN_NOT_FOUND")
        if record["status"] not in {"queued", "running", "paused"}:
            raise RuntimeTransitionError(f"INVALID_APPROVAL_STATUS:{record['status']}")
        state = dict(record["state"])
        state["approval"] = "approved"
        version = self.store.save_state(run_id, state, int(record["state_version"]))
        response = {"run_id": run_id, "status": record["status"], "approved": True, "state_version": version}
        self.store.append_event(run_id, "approved", response)
        self.store.put_idempotency(run_id, key, response)
        return response

    def claim_next(self, worker_id: str) -> dict[str, Any] | None:
        while True:
            run_id = self.queue.claim()
            if run_id is None:
                return None
            record = self.store.load_run(run_id)
            if record is None or record["status"] != "queued":
                continue
            state = dict(record["state"])
            state.update({"status": "running", "worker_id": worker_id})
            version = self.store.save_state(run_id, state, int(record["state_version"]))
            self.store.append_event(run_id, "claimed", {"worker_id": worker_id, "state_version": version})
            record["state"] = state
            record["status"] = "running"
            record["state_version"] = version
            return record

    def checkpoint(self, run_id: str, state: Mapping[str, Any], expected_version: int) -> int:
        return self.store.save_state(run_id, state, expected_version)

    def complete(self, run_id: str, expected_version: int, result: Mapping[str, Any]) -> int:
        record = self.store.load_run(run_id)
        if record is None:
            raise RuntimeTransitionError("RUN_NOT_FOUND")
        state = dict(record["state"])
        state.update({"status": "completed", "result": dict(result)})
        return self.store.save_state(run_id, state, expected_version)

    def execute_once(
        self,
        worker_id: str,
        step: Callable[[dict[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        record = self.claim_next(worker_id)
        if record is None:
            return None
        next_state = dict(record["state"])
        next_state.update(step(dict(next_state)))
        version = self.checkpoint(record["run_id"], next_state, int(record["state_version"]))
        return {"run_id": record["run_id"], "state_version": version, "state": next_state}
