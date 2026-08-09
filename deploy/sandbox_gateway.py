"""Local Docker execution plane for RepoEvo-Agent.

The gateway accepts source files as JSON content and executes an allowlisted
test command inside a disposable, heavily restricted Docker container. It
never accepts a host path or a host shell command from the caller.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from threading import BoundedSemaphore

HOST = os.environ.get("REPOEVO_SANDBOX_HOST", "127.0.0.1")
PORT = int(os.environ.get("REPOEVO_SANDBOX_PORT", "19090"))
IMAGE = os.environ.get("REPOEVO_SANDBOX_IMAGE", "repoevo-sandbox:py311")
VERSION = "0.2"

MAX_REQUEST_BYTES = 5 * 1024 * 1024
MAX_FILES = 64
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_FILE_BYTES = 4 * 1024 * 1024
MAX_COMMAND_ARGS = 32
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_CONCURRENT_RUNS = 2

ALLOWED_PROGRAMS = {"pytest", "python", "python3"}
FORBIDDEN_SHELL_TOKENS = {";", "&&", "||", "|", ">", "<", "`"}
RUN_SEMAPHORE = BoundedSemaphore(MAX_CONCURRENT_RUNS)


class RequestError(ValueError):
    """Raised when a request violates the gateway contract."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _text(value: object, code: str) -> str:
    if not isinstance(value, str):
        raise RequestError(code)
    if "\x00" in value:
        raise RequestError(f"{code}_NUL")
    return value


def _safe_relative_path(value: object) -> str:
    path_text = _text(value, "FILE_PATH_INVALID")
    if not path_text or "\\" in path_text:
        raise RequestError("FILE_PATH_INVALID")
    path = PurePosixPath(path_text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RequestError("FILE_PATH_INVALID")
    if path.parts[0] == ".git":
        raise RequestError("FILE_PATH_RESERVED")
    normalized = "/".join(path.parts)
    if normalized != path_text:
        raise RequestError("FILE_PATH_INVALID")
    return normalized


def _validate_command(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RequestError("COMMAND_INVALID")
    if len(value) > MAX_COMMAND_ARGS:
        raise RequestError("COMMAND_TOO_LONG")
    command = [_text(part, "COMMAND_ARGUMENT_INVALID") for part in value]
    if not command[0] or Path(command[0]).name not in ALLOWED_PROGRAMS:
        raise RequestError("COMMAND_PROGRAM_NOT_ALLOWED")
    if any(token in part for part in command for token in FORBIDDEN_SHELL_TOKENS):
        raise RequestError("COMMAND_SHELL_SYNTAX_NOT_ALLOWED")
    return command


def _validate_timeout(value: object) -> float:
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestError("TIMEOUT_INVALID")
    timeout = float(value)
    if not 0.1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise RequestError("TIMEOUT_OUT_OF_RANGE")
    return timeout


def _write_files(value: object, workspace: Path) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RequestError("FILES_INVALID")
    if len(value) > MAX_FILES:
        raise RequestError("TOO_MANY_FILES")

    total_bytes = 0
    written: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise RequestError("FILE_ENTRY_INVALID")
        relative = _safe_relative_path(item.get("path"))
        content = _text(item.get("content"), "FILE_CONTENT_INVALID")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise RequestError("FILE_TOO_LARGE")
        total_bytes += len(encoded)
        if total_bytes > MAX_TOTAL_FILE_BYTES:
            raise RequestError("FILES_TOO_LARGE")
        if relative in written:
            raise RequestError("DUPLICATE_FILE_PATH")
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        written.append(relative)
    return written


def _docker_limits() -> dict[str, object]:
    return {
        "network": "none",
        "rootfs": "read-only",
        "user": "10001:10001",
        "capabilities": "drop-all",
        "no_new_privileges": True,
        "pids": 64,
        "memory": "256m",
        "cpus": 1,
        "tmpfs": "/tmp:rw,noexec,nosuid,size=64m",
    }


def _docker_command(
    command: Sequence[str], *, workspace: Path | None, container_name: str
) -> list[str]:
    docker = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--pull",
        "never",
        "--user",
        "10001:10001",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "256m",
        "--cpus",
        "1",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
    ]
    if workspace is not None:
        docker.extend(
            [
                "--mount",
                f"type=bind,source={workspace},destination=/workspace,readonly",
                "--workdir",
                "/workspace",
            ]
        )
    docker.extend(
        [
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTEST_ADDOPTS=-p no:cacheprovider",
            "--env",
            "PYTHONUNBUFFERED=1",
            IMAGE,
            *command,
        ]
    )
    return docker


def _kill_container(container_name: str) -> None:
    subprocess.run(
        ["docker", "rm", "--force", container_name],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _run_container(
    command: Sequence[str], *, workspace: Path | None, timeout_seconds: float
) -> dict[str, object]:
    container_name = f"repoevo-sandbox-{uuid.uuid4().hex[:16]}"
    try:
        completed = subprocess.run(
            _docker_command(command, workspace=workspace, container_name=container_name),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _kill_container(container_name)
        return {
            "ok": False,
            "returncode": None,
            "timed_out": True,
            "stdout": exc.stdout if isinstance(exc.stdout, str) else "",
            "stderr": exc.stderr if isinstance(exc.stderr, str) else "",
            "error_code": "EXECUTION_TIMEOUT",
        }
    error_code = None
    if completed.returncode != 0:
        error_code = (
            "SANDBOX_IMAGE_NOT_FOUND"
            if "Unable to find image" in completed.stderr or "No such image" in completed.stderr
            else "COMMAND_FAILED"
        )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "timed_out": False,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "error_code": error_code,
    }


def docker_probe() -> dict[str, object]:
    """Run a fixed security probe without accepting caller-controlled code."""

    script = r"""
set -eu
printf 'uid=%s\n' "$(id -u)"
grep -E '^(CapEff|NoNewPrivs):' /proc/self/status
if python -c "import socket; socket.create_connection(('1.1.1.1', 80), 1)" 2>/dev/null; then
    echo 'network=ALLOWED'
else
    echo 'network=BLOCKED'
fi
if touch /blocked 2>/dev/null; then
    echo 'rootfs=WRITABLE'
else
    echo 'rootfs=READ_ONLY'
fi
printf 'pids.max='
cat /sys/fs/cgroup/pids.max
printf 'memory.max='
cat /sys/fs/cgroup/memory.max
"""
    return {
        **_run_container(["sh", "-c", script], workspace=None, timeout_seconds=30.0),
        "limits": _docker_limits(),
    }


def execute_request(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise RequestError("JSON_OBJECT_REQUIRED")
    command = _validate_command(payload.get("command"))
    timeout_seconds = _validate_timeout(payload.get("timeout_seconds"))
    if not RUN_SEMAPHORE.acquire(blocking=False):
        raise RequestError("SANDBOX_BUSY")
    try:
        with tempfile.TemporaryDirectory(prefix="repoevo-sandbox-request-") as temporary:
            workspace = Path(temporary)
            # The container is UID 10001: permit traversal but bind only read-only files.
            workspace.chmod(0o755)
            files = _write_files(payload.get("files"), workspace)
            return {
                **_run_container(command, workspace=workspace, timeout_seconds=timeout_seconds),
                "service": "repoevo-sandbox",
                "version": VERSION,
                "command": command,
                "files": files,
                "limits": _docker_limits(),
            }
    finally:
        RUN_SEMAPHORE.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "RepoEvoSandbox/0.2"

    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> object:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RequestError("CONTENT_LENGTH_INVALID") from exc
        if content_length <= 0:
            raise RequestError("REQUEST_BODY_REQUIRED")
        if content_length > MAX_REQUEST_BYTES:
            raise RequestError("REQUEST_TOO_LARGE")
        try:
            return json.loads(self.rfile.read(content_length))
        except json.JSONDecodeError as exc:
            raise RequestError("JSON_INVALID") from exc

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send(
                200,
                {
                    "ok": True,
                    "service": "repoevo-sandbox",
                    "version": VERSION,
                    "capabilities": ["probe", "run"],
                    "limits": _docker_limits(),
                },
            )
            return
        self._send(404, {"ok": False, "error_code": "NOT_FOUND"})

    def do_POST(self) -> None:
        if self.path == "/v1/sandbox/probe":
            try:
                result = docker_probe()
            except (OSError, subprocess.SubprocessError) as exc:
                self._send(
                    503, {"ok": False, "error_code": "DOCKER_UNAVAILABLE", "detail": str(exc)}
                )
                return
            self._send(200, result)
            return
        if self.path != "/v1/sandbox/run":
            self._send(404, {"ok": False, "error_code": "NOT_FOUND"})
            return
        try:
            result = execute_request(self._read_json())
        except RequestError as exc:
            self._send(
                429 if exc.code == "SANDBOX_BUSY" else 400, {"ok": False, "error_code": exc.code}
            )
            return
        except (OSError, subprocess.SubprocessError) as exc:
            self._send(503, {"ok": False, "error_code": "DOCKER_UNAVAILABLE", "detail": str(exc)})
            return
        self._send(200, result)

    def log_message(self, format: str, *args: object) -> None:
        print("[sandbox] " + format % args)


if __name__ == "__main__":
    print(f"RepoEvo sandbox gateway listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
