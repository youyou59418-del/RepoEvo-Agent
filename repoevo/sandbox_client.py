"""Small standard-library client for the local RepoEvo sandbox gateway."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SandboxClientError(RuntimeError):
    """Raised when the gateway cannot accept or return a valid request."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str
    error_code: str | None
    command: list[str]
    files: list[str]
    limits: dict[str, object]

    @classmethod
    def from_payload(cls, payload: object) -> SandboxResult:
        if not isinstance(payload, dict):
            raise SandboxClientError("RESPONSE_INVALID")
        required = ["ok", "returncode", "timed_out", "stdout", "stderr", "command", "files"]
        if any(key not in payload for key in required):
            raise SandboxClientError("RESPONSE_FIELDS_MISSING")
        if not isinstance(payload["ok"], bool):
            raise SandboxClientError("RESPONSE_INVALID")
        if not isinstance(payload["timed_out"], bool):
            raise SandboxClientError("RESPONSE_INVALID")
        if not isinstance(payload["stdout"], str) or not isinstance(payload["stderr"], str):
            raise SandboxClientError("RESPONSE_INVALID")
        if not isinstance(payload["command"], list) or not all(
            isinstance(part, str) for part in payload["command"]
        ):
            raise SandboxClientError("RESPONSE_INVALID")
        if not isinstance(payload["files"], list) or not all(
            isinstance(path, str) for path in payload["files"]
        ):
            raise SandboxClientError("RESPONSE_INVALID")
        return cls(
            ok=payload["ok"],
            returncode=payload["returncode"] if isinstance(payload["returncode"], int) else None,
            timed_out=payload["timed_out"],
            stdout=payload["stdout"],
            stderr=payload["stderr"],
            error_code=payload.get("error_code")
            if isinstance(payload.get("error_code"), str)
            else None,
            command=list(payload["command"]),
            files=list(payload["files"]),
            limits=dict(payload.get("limits", {}))
            if isinstance(payload.get("limits"), dict)
            else {},
        )


def _build_files(files: Mapping[str, str]) -> list[dict[str, str]]:
    if not isinstance(files, Mapping) or not files:
        raise SandboxClientError("FILES_REQUIRED")
    entries: list[dict[str, str]] = []
    for path, content in sorted(files.items()):
        if not isinstance(path, str) or not isinstance(content, str):
            raise SandboxClientError("FILES_INVALID")
        entries.append({"path": path, "content": content})
    return entries


def run_in_sandbox(
    files: Mapping[str, str],
    command: Sequence[str] = ("pytest", "-q"),
    *,
    timeout_seconds: float = 30.0,
    base_url: str | None = None,
    request_timeout_seconds: float | None = None,
) -> SandboxResult:
    """Run source files through the localhost/SSH-tunneled sandbox API."""

    if not isinstance(command, Sequence) or isinstance(command, (str, bytes)):
        raise SandboxClientError("COMMAND_INVALID")
    command_list = [str(part) for part in command]
    if not command_list:
        raise SandboxClientError("COMMAND_INVALID")
    payload = {
        "files": _build_files(files),
        "command": command_list,
        "timeout_seconds": timeout_seconds,
    }
    gateway_url = (
        base_url or os.environ.get("REPOEVO_SANDBOX_URL", "http://127.0.0.1:19090")
    ).rstrip("/")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{gateway_url}/v1/sandbox/run",
        data=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        method="POST",
    )
    timeout = request_timeout_seconds or timeout_seconds + 10.0
    try:
        with urlopen(request, timeout=timeout) as response:
            response_payload: Any = json.loads(response.read())
    except HTTPError as exc:
        try:
            error_payload = json.loads(exc.read())
        except json.JSONDecodeError:
            error_payload = {}
        code = error_payload.get("error_code", "GATEWAY_HTTP_ERROR")
        raise SandboxClientError(code, f"sandbox gateway returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise SandboxClientError("GATEWAY_UNREACHABLE", str(exc)) from exc
    return SandboxResult.from_payload(response_payload)
