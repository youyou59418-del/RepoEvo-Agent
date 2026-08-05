from __future__ import annotations

import json
from typing import Self
from unittest.mock import patch

import pytest

from repoevo.sandbox_client import SandboxClientError, SandboxResult, run_in_sandbox


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def result_payload() -> dict[str, object]:
    return {
        "ok": True,
        "returncode": 0,
        "timed_out": False,
        "stdout": "1 passed\n",
        "stderr": "",
        "error_code": None,
        "command": ["pytest", "-q"],
        "files": ["test_ok.py"],
        "limits": {"network": "none"},
    }


def test_client_sends_sorted_files_and_parses_result() -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        body = request.data  # type: ignore[attr-defined]
        captured["payload"] = json.loads(body)
        return FakeResponse(result_payload())

    with patch("repoevo.sandbox_client.urlopen", fake_urlopen):
        result = run_in_sandbox({"z.py": "", "a.py": ""})

    assert isinstance(result, SandboxResult)
    assert result.ok is True
    assert [item["path"] for item in captured["payload"]["files"]] == ["a.py", "z.py"]  # type: ignore[index]
    assert captured["payload"]["command"] == ["pytest", "-q"]  # type: ignore[index]


def test_client_rejects_empty_files() -> None:
    with pytest.raises(SandboxClientError, match="FILES_REQUIRED"):
        run_in_sandbox({})


def test_result_rejects_malformed_payload() -> None:
    with pytest.raises(SandboxClientError, match="RESPONSE_FIELDS_MISSING"):
        SandboxResult.from_payload({})
