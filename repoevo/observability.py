"""Week 11 structured events, trace IDs, and Prometheus metrics."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class Observability:
    def __init__(self, log_path: Path | None = None) -> None:
        self.log_path = log_path
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry = CollectorRegistry()
        self.task_counter = Counter(
            "repoevo_tasks_total",
            "Task lifecycle events",
            ["event", "status"],
            registry=self.registry,
        )
        self.tool_counter = Counter(
            "repoevo_tool_calls_total", "Tool calls", ["tool", "ok"], registry=self.registry
        )
        self.task_duration = Histogram(
            "repoevo_task_duration_seconds", "Task duration", registry=self.registry
        )
        self.queue_gauge = Gauge(
            "repoevo_queue_depth", "Current queue depth", registry=self.registry
        )

    def emit(
        self,
        event_type: str,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "timestamp": time.time(),
            "trace_id": trace_id or str(uuid.uuid4()),
            "run_id": run_id,
            "event_type": event_type,
            "payload": payload or {},
        }
        if self.log_path is not None:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def record_task(self, event: str, status: str) -> None:
        self.task_counter.labels(event=event, status=status).inc()

    def record_tool(self, tool: str, ok: bool) -> None:
        self.tool_counter.labels(tool=tool, ok=str(ok).lower()).inc()

    def record_duration(self, seconds: float) -> None:
        self.task_duration.observe(max(0.0, seconds))

    def set_queue_depth(self, depth: int) -> None:
        self.queue_gauge.set(max(0, depth))

    def metrics(self) -> bytes:
        return generate_latest(self.registry)
