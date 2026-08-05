"""Worker that connects the persistent runtime queue to the Multi-Agent graph."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .multi_agent import AgentState, MultiAgentConfig, MultiAgentRunner
from .runtime import TaskRuntime


class AgentWorker:
    def __init__(
        self,
        runtime: TaskRuntime,
        *,
        worker_id: str = "worker-1",
        runner_factory: Callable[[], MultiAgentRunner] | None = None,
    ) -> None:
        self.runtime = runtime
        self.worker_id = worker_id
        self.runner_factory = runner_factory or (lambda: MultiAgentRunner(MultiAgentConfig()))

    def run_once(self) -> dict[str, Any] | None:
        record = self.runtime.claim_next(self.worker_id)
        if record is None:
            return None
        state = dict(record["state"])
        try:
            required = ["repository_root", "reference_patch", "user_request"]
            missing = [key for key in required if not state.get(key)]
            if missing:
                raise ValueError(f"WORKER_INPUT_MISSING:{','.join(missing)}")
            initial: AgentState = {
                "task_id": str(state.get("task_id", record["task_id"])),
                "user_request": str(state["user_request"]),
                "acceptance_criteria": list(state.get("acceptance_criteria", [])),
                "repository_root": str(state["repository_root"]),
                "reference_patch": str(state["reference_patch"]),
            }
            result = self.runner_factory().run(initial)
            state["agent_result"] = dict(result)
            state["status"] = "completed" if result.get("status") == "completed" else "failed"
            event_type = "worker_completed" if state["status"] == "completed" else "worker_failed"
        except Exception as exc:  # noqa: BLE001 - worker boundary records task failure and continues
            state["status"] = "failed"
            state["worker_error"] = f"{type(exc).__name__}: {exc}"
            event_type = "worker_failed"
        version = self.runtime.checkpoint(record["run_id"], state, int(record["state_version"]))
        self.runtime.store.append_event(
            record["run_id"], event_type, {"worker_id": self.worker_id, "state_version": version}
        )
        return {"run_id": record["run_id"], "status": state["status"], "state_version": version}

    def run_forever(self, *, idle_sleep_seconds: float = 0.5) -> None:
        while True:
            result = self.run_once()
            if result is None:
                time.sleep(idle_sleep_seconds)
