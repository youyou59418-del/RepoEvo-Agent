"""Week 11 FastAPI task control plane and SSE event stream."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field

from repoevo.context import ArtifactStore
from repoevo.observability import Observability
from repoevo.runtime import (
    PostgresCheckpointStore,
    RedisTaskQueue,
    RuntimeTransitionError,
    SQLiteCheckpointStore,
    SQLiteTaskQueue,
    TaskRuntime,
)
from repoevo.settings import RepoEvoSettings


class TaskCreateRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)
    initial_state: dict[str, Any] = Field(default_factory=dict)


class ServiceContainer:
    def __init__(self) -> None:
        settings = RepoEvoSettings()
        root = settings.repoevo_data_root
        self.store = (
            PostgresCheckpointStore(settings.checkpoint_dsn)
            if settings.checkpoint_dsn
            else SQLiteCheckpointStore(root / "task_runs.db")
        )
        redis_url = settings.redis_url
        self.queue = RedisTaskQueue(redis_url) if redis_url else SQLiteTaskQueue(root / "task_runs.db")
        self.runtime = TaskRuntime(self.store, self.queue)
        self.artifacts = ArtifactStore(settings.repoevo_artifact_root)
        self.observability = Observability(root / "events.jsonl")


container = ServiceContainer()
app = FastAPI(title="RepoEvo Agent API", version="0.1.0")


def _cors_origins() -> list[str]:
    """Return only explicitly configured browser origins for the local console."""

    configured = os.environ.get(
        "REPOEVO_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def _record_or_404(run_id: str) -> dict[str, Any]:
    record = container.store.load_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="RUN_NOT_FOUND")
    return record


def _transition(run_id: str, action: str) -> dict[str, Any]:
    try:
        result = getattr(container.runtime, action)(run_id)
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    container.observability.record_task(action, str(result.get("status", "unknown")))
    container.observability.emit(action, run_id=run_id, payload=result)
    container.observability.set_queue_depth(len(container.queue))
    return result


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "repoevo-api", "version": app.version}


@app.post("/api/tasks", status_code=201)
def create_task(request: TaskCreateRequest) -> dict[str, Any]:
    run_id = container.runtime.create_run(request.task_id, request.initial_state)
    container.observability.record_task("create", "queued")
    container.observability.emit("task_created", run_id=run_id, payload={"task_id": request.task_id})
    container.observability.set_queue_depth(len(container.queue))
    return {"run_id": run_id, "status": "queued", "state_version": 0}


@app.get("/api/tasks/{run_id}")
def get_task(run_id: str) -> dict[str, Any]:
    return _record_or_404(run_id)


def _event_stream(run_id: str, follow: bool) -> Iterator[str]:
    sent = 0
    deadline = time.monotonic() + 30
    while True:
        events = container.store.get_events(run_id)
        for event in events[sent:]:
            yield f"event: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        sent = len(events)
        record = _record_or_404(run_id)
        if not follow or record["status"] in {"completed", "failed", "cancelled"}:
            break
        if time.monotonic() >= deadline:
            yield "event: timeout\ndata: {}\n\n"
            break
        time.sleep(0.25)


@app.get("/api/tasks/{run_id}/events")
def task_events(run_id: str, follow: bool = Query(default=False)) -> StreamingResponse:
    _record_or_404(run_id)
    return StreamingResponse(
        _event_stream(run_id, follow),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/tasks/{run_id}/pause")
def pause_task(run_id: str) -> dict[str, Any]:
    _record_or_404(run_id)
    return _transition(run_id, "pause")


@app.post("/api/tasks/{run_id}/resume")
def resume_task(run_id: str) -> dict[str, Any]:
    _record_or_404(run_id)
    return _transition(run_id, "resume")


@app.post("/api/tasks/{run_id}/cancel")
def cancel_task(run_id: str) -> dict[str, Any]:
    _record_or_404(run_id)
    return _transition(run_id, "cancel")


@app.post("/api/tasks/{run_id}/approve")
def approve_task(run_id: str) -> dict[str, Any]:
    _record_or_404(run_id)
    try:
        result = container.runtime.approve(run_id)
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    container.observability.record_task("approve", str(result.get("status", "unknown")))
    container.observability.emit("approved", run_id=run_id, payload=result)
    return result


@app.get("/api/tasks/{run_id}/artifacts/{artifact_id}")
def get_artifact(run_id: str, artifact_id: str) -> FileResponse:
    _record_or_404(run_id)
    matches = list(container.artifacts.root.glob(f"{artifact_id}-*"))
    if len(matches) != 1 or not matches[0].is_file():
        raise HTTPException(status_code=404, detail="ARTIFACT_NOT_FOUND")
    return FileResponse(matches[0])


@app.get("/metrics")
def metrics() -> Response:
    container.observability.set_queue_depth(len(container.queue))
    return Response(content=container.observability.metrics(), media_type=CONTENT_TYPE_LATEST)
