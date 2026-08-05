"""Load public benchmark metadata and locally private evaluation assets."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_BENCHMARK_ROOT = PROJECT_ROOT / "benchmarks"
PRIVATE_BENCHMARK_ENV = "REPOEVO_PRIVATE_BENCHMARK_ROOT"


class PrivateBenchmarkUnavailable(RuntimeError):
    """Raised when a full evaluation is requested without the local private pack."""


def private_benchmark_root() -> Path:
    configured = os.getenv(PRIVATE_BENCHMARK_ENV)
    return Path(configured).expanduser() if configured else PUBLIC_BENCHMARK_ROOT / "private"


def load_public_manifest() -> dict[str, Any]:
    path = PUBLIC_BENCHMARK_ROOT / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _require_private_root() -> Path:
    root = private_benchmark_root()
    required = [root / "manifest.json", root / "tasks", root / "hidden"]
    missing = [str(path.name) for path in required if not path.exists()]
    if missing:
        raise PrivateBenchmarkUnavailable(
            "PRIVATE_BENCHMARKS_REQUIRED: full evaluation assets are local-only. "
            f"Set {PRIVATE_BENCHMARK_ENV} to a directory containing manifest.json, tasks, and hidden."
        )
    return root


def load_private_manifest() -> dict[str, Any]:
    root = _require_private_root()
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def load_private_task(task_id: str) -> dict[str, Any]:
    root = _require_private_root()
    path = root / "tasks" / f"{task_id}.json"
    if not path.is_file():
        raise PrivateBenchmarkUnavailable(f"PRIVATE_BENCHMARK_TASK_MISSING:{task_id}")
    task = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "task_id",
        "repository",
        "request",
        "acceptance_conditions",
        "bug_patch",
        "reference_patch",
    }
    missing = sorted(required.difference(task))
    if missing:
        raise PrivateBenchmarkUnavailable(
            f"PRIVATE_BENCHMARK_TASK_INCOMPLETE:{task_id}:{','.join(missing)}"
        )
    return task


def private_hidden_test_path(task_id: str) -> Path:
    root = _require_private_root()
    path = root / "hidden" / f"{task_id}.py"
    if not path.is_file():
        raise PrivateBenchmarkUnavailable(f"PRIVATE_HIDDEN_TEST_MISSING:{task_id}")
    return path
