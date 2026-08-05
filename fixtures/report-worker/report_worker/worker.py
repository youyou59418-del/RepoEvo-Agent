from __future__ import annotations

import csv
import json
from collections import Counter
from io import StringIO

STATUS_MAP = {"ok": "succeeded", "done": "succeeded", "error": "failed", "retry": "retrying"}


def parse_records(text: str) -> list[dict[str, str | int]]:
    if not text.strip():
        return []
    rows = csv.DictReader(StringIO(text))
    required = {"job_id", "status", "duration_ms"}
    if not rows.fieldnames or not required.issubset(rows.fieldnames):
        raise ValueError("missing required columns")
    records: list[dict[str, str | int]] = []
    for row in rows:
        duration = int(row["duration_ms"] or "0")
        if duration < 0:
            raise ValueError("duration cannot be negative")
        records.append(
            {"job_id": row["job_id"], "status": normalize_status(row["status"]), "duration_ms": duration}
        )
    return records


def normalize_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized not in STATUS_MAP:
        raise ValueError("unknown status")
    return STATUS_MAP[normalized]


def deduplicate(records: list[dict[str, str | int]]) -> list[dict[str, str | int]]:
    seen: set[str] = set()
    result: list[dict[str, str | int]] = []
    for record in records:
        job_id = str(record["job_id"])
        if job_id not in seen:
            seen.add(job_id)
            result.append(record)
    return result


def retry_delay(attempt: int, base: int = 2, cap: int = 30) -> int:
    if attempt < 0 or base < 1 or cap < 1:
        raise ValueError("invalid retry configuration")
    return min(cap, base**attempt)


def summarize(records: list[dict[str, str | int]]) -> dict[str, int]:
    counts = Counter(str(record["status"]) for record in records)
    return {key: counts[key] for key in sorted(counts)}


def stable_json(records: list[dict[str, str | int]]) -> str:
    return json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
