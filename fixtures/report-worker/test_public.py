import pytest

from report_worker.worker import (
    deduplicate,
    normalize_status,
    parse_records,
    retry_delay,
    stable_json,
    summarize,
)


CSV = "job_id,status,duration_ms\nj1,ok,10\nj2,error,20\nj1,done,30\n"


def test_parse_records_and_statuses() -> None:
    records = parse_records(CSV)
    assert records[0] == {"job_id": "j1", "status": "succeeded", "duration_ms": 10}
    assert normalize_status(" retry ") == "retrying"


def test_parse_validation() -> None:
    with pytest.raises(ValueError):
        parse_records("job_id,status,duration_ms\nj1,ok,-1\n")


def test_deduplicate_keeps_first_record() -> None:
    records = parse_records(CSV)
    assert deduplicate(records)[0]["duration_ms"] == 10


def test_retry_delay_is_exponential_and_capped() -> None:
    assert retry_delay(3) == 8
    assert retry_delay(10) == 30


def test_summary_and_stable_json() -> None:
    records = deduplicate(parse_records(CSV))
    assert summarize(records) == {"failed": 1, "succeeded": 1}
    assert stable_json(records).startswith('[{"duration_ms":10')
