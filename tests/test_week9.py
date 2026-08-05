from __future__ import annotations

from pathlib import Path

import pytest

from repoevo.memory import FailureAnalyzer, MemoryGate, MemoryRecord, MemoryRetriever, MemoryStore


def test_failure_analyzer_creates_candidate_not_published() -> None:
    analyzer = FailureAnalyzer()
    record = analyzer.propose(
        {
            "status": "failed",
            "failure_category": "CODE_ERROR",
            "user_request": "fix totals",
            "repository_id": "order-service",
        },
        source_run_id="run-001",
    )
    assert record.validation_status == "candidate"
    assert "CODE_ERROR" in record.content


def test_memory_gate_blocks_missing_evidence(tmp_path: Path) -> None:
    record = MemoryRecord(
        memory_type="procedural",
        content="Check schema and API tests together.",
        source_run_id="run-002",
        repository_scope="orders",
    )
    verified = MemoryGate().verify(record, {"hidden_test_ok": True})
    assert verified.validation_status == "candidate"
    with pytest.raises(ValueError, match="REQUIRES_VERIFIED"):
        MemoryGate().publish(verified)


def test_verified_memory_can_publish_and_retrieve_by_scope(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.jsonl")
    candidate = MemoryRecord(
        memory_type="procedural",
        content="Check schema and API tests together before changing response structure.",
        source_run_id="run-003",
        repository_scope="orders",
    )
    gate = MemoryGate()
    verified = gate.verify(
        candidate,
        {
            "hidden_test_ok": True,
            "review_ok": True,
            "acceptance_covered": True,
            "negative_transfer_checked": True,
        },
    )
    store.save(gate.publish(verified))
    store.save(
        MemoryRecord(
            memory_type="semantic",
            content="Inventory uses reservations.",
            source_run_id="run-004",
            repository_scope="inventory",
            validation_status="published",
            confidence=0.9,
        )
    )
    hits = MemoryRetriever(store).retrieve("schema API response", repository_scope="orders")
    assert len(hits) == 1
    assert hits[0].validation_status == "published"
    assert MemoryRetriever(store).retrieve("reservations", repository_scope="orders") == []
