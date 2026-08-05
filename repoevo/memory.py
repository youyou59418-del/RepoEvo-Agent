"""Week 9 layered memory and failure-analysis gate."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryType = Literal["episodic", "semantic", "procedural"]
ValidationStatus = Literal["candidate", "verified", "published", "deprecated"]
FailureCategory = Literal[
    "PLAN_ERROR",
    "TOOL_SELECTION",
    "TOOL_ARGUMENT",
    "CONTEXT_MISSING",
    "CONTEXT_NOISE",
    "CODE_ERROR",
    "TEST_STRATEGY",
    "RECOVERY_ERROR",
    "MEMORY_INTERFERENCE",
    "BUDGET_EXHAUSTED",
]


class MemoryRecord(BaseModel):
    memory_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    memory_type: MemoryType
    content: str
    source_run_id: str
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = "candidate"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    repository_scope: str | None = None
    created_at: float = Field(default_factory=time.time)
    tags: list[str] = Field(default_factory=list)


class MemoryStore:
    """Append-only JSONL memory store suitable for local evaluation."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, record: MemoryRecord) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")

    def all(self) -> list[MemoryRecord]:
        if not self.path.exists():
            return []
        records: list[MemoryRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(MemoryRecord.model_validate_json(line))
        return records

    def replace(self, record: MemoryRecord) -> None:
        records = [item for item in self.all() if item.memory_id != record.memory_id]
        records.append(record)
        self.path.write_text(
            "".join(item.model_dump_json() + "\n" for item in records), encoding="utf-8"
        )


class FailureAnalyzer:
    """Turn a trace into a category and a candidate strategy, never a published fact."""

    def classify(self, state: Mapping[str, Any]) -> tuple[FailureCategory, str]:
        explicit = state.get("failure_category")
        if explicit in {
            "PLAN_ERROR",
            "TOOL_SELECTION",
            "TOOL_ARGUMENT",
            "CONTEXT_MISSING",
            "CONTEXT_NOISE",
            "CODE_ERROR",
            "TEST_STRATEGY",
            "RECOVERY_ERROR",
            "MEMORY_INTERFERENCE",
            "BUDGET_EXHAUSTED",
        }:
            return explicit, "The runtime supplied an explicit failure category."
        if state.get("status") == "budget_exhausted":
            return "BUDGET_EXHAUSTED", "The run stopped at its execution budget."
        test_results = state.get("test_results") or {}
        error_code = str(test_results.get("error_code", ""))
        if "TIMEOUT" in error_code or "ENVIRONMENT" in error_code:
            return "RECOVERY_ERROR", "The test environment failed before code evidence was collected."
        if test_results.get("ok") is False:
            return "CODE_ERROR", "The implementation did not satisfy the test evidence."
        review = state.get("review_result") or {}
        if review.get("ok") is False:
            return "CODE_ERROR", "The independent review rejected the resulting change."
        return "RECOVERY_ERROR", "The run ended without sufficient success evidence."

    def propose(
        self,
        state: Mapping[str, Any],
        *,
        source_run_id: str,
        evidence_artifact_ids: Iterable[str] = (),
    ) -> MemoryRecord:
        category, explanation = self.classify(state)
        request = str(state.get("user_request", "the task"))
        strategy = {
            "CODE_ERROR": "Read the target and acceptance tests again before making a second patch.",
            "CONTEXT_MISSING": "Use repository retrieval to collect the symbol and its tests before editing.",
            "RECOVERY_ERROR": "Retry only the failed infrastructure step and preserve the checkpoint.",
            "BUDGET_EXHAUSTED": "Reduce context and tool calls before retrying the task.",
        }.get(category, "Keep the failure category explicit and route to the responsible role.")
        return MemoryRecord(
            memory_type="episodic",
            content=f"Failure {category} for request '{request}': {explanation} Strategy: {strategy}",
            source_run_id=source_run_id,
            evidence_artifact_ids=list(evidence_artifact_ids),
            validation_status="candidate",
            confidence=0.2,
            repository_scope=str(state.get("repository_id")) if state.get("repository_id") else None,
            tags=["failure", category.lower()],
        )


class MemoryGate:
    """Require independent evidence before a candidate can affect future runs."""

    def verify(self, record: MemoryRecord, evaluation: Mapping[str, Any]) -> MemoryRecord:
        required = ("hidden_test_ok", "review_ok", "acceptance_covered", "negative_transfer_checked")
        passed = all(evaluation.get(key) is True for key in required)
        if not passed:
            return record.model_copy(update={"validation_status": "candidate", "confidence": 0.2})
        return record.model_copy(
            update={"validation_status": "verified", "confidence": min(0.95, max(record.confidence, 0.8))}
        )

    def publish(self, record: MemoryRecord) -> MemoryRecord:
        if record.validation_status != "verified":
            raise ValueError("MEMORY_PUBLISH_REQUIRES_VERIFIED")
        return record.model_copy(update={"validation_status": "published"})


class MemoryRetriever:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def retrieve(self, query: str, *, repository_scope: str | None = None, limit: int = 5) -> list[MemoryRecord]:
        if not query.strip() or limit <= 0:
            raise ValueError("MEMORY_QUERY_INVALID")
        query_tokens = set(re.findall(r"[a-z0-9_]+", query.lower()))
        scored: list[tuple[int, MemoryRecord]] = []
        for record in self.store.all():
            if record.validation_status not in {"verified", "published"}:
                continue
            if record.repository_scope not in {None, repository_scope}:
                continue
            text_tokens = set(re.findall(r"[a-z0-9_]+", record.content.lower()))
            score = len(query_tokens & text_tokens)
            if score:
                scored.append((score, record))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].confidence, pair[1].created_at))
        return [record for _, record in scored[:limit]]
