"""A minimal, testable safety boundary for patch-and-test tasks.

This module deliberately has no LLM dependency. It is the small deterministic
core that later Agent tools will call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path


class RunnerError(ValueError):
    """Raised when a request violates the runner's input contract."""


@dataclass(frozen=True)
class RunResult:
    """JSON-friendly result of one isolated patch run."""

    success: bool
    patch_applied: bool
    tests_passed: bool
    timed_out: bool
    exit_code: int | None
    test_command: list[str]
    output: str
    diff: str
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


_MAX_COMMAND_ARGS = 32
_ALLOWED_TEST_PROGRAMS = {"pytest", "python", "python3"}
_FORBIDDEN_COMMAND_TOKENS = {";", "&&", "||", "|", ">", "<", "`"}


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n... <output truncated> ...\n"
    available = max(0, limit - len(marker))
    head = available // 2
    tail = available - head
    return text[:head] + marker + text[-tail:]


def _validate_test_command(command: Sequence[str]) -> list[str]:
    if not command:
        raise RunnerError("TEST_COMMAND_EMPTY")
    if len(command) > _MAX_COMMAND_ARGS:
        raise RunnerError("TEST_COMMAND_TOO_LONG")

    normalized = [str(part) for part in command]
    program = Path(normalized[0]).name
    if program not in _ALLOWED_TEST_PROGRAMS:
        raise RunnerError("TEST_PROGRAM_NOT_ALLOWED")
    if any("\x00" in part for part in normalized):
        raise RunnerError("TEST_COMMAND_NUL")
    if any(token in part for part in normalized for token in _FORBIDDEN_COMMAND_TOKENS):
        raise RunnerError("TEST_SHELL_SYNTAX_NOT_ALLOWED")
    return normalized


def _validate_repository(repo_root: Path, max_repo_bytes: int) -> Path:
    try:
        resolved = repo_root.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise RunnerError("REPOSITORY_NOT_FOUND") from exc
    if not resolved.is_dir():
        raise RunnerError("REPOSITORY_NOT_DIRECTORY")
    if not (resolved / ".git").exists():
        raise RunnerError("GIT_REPOSITORY_REQUIRED")

    total_bytes = 0
    for root, dirs, filenames in os.walk(resolved, followlinks=False):
        root_path = Path(root)
        for name in [*dirs, *filenames]:
            path = root_path / name
            if path.is_symlink():
                raise RunnerError("SYMLINK_NOT_ALLOWED")
            if path.is_file():
                total_bytes += path.stat().st_size
                if total_bytes > max_repo_bytes:
                    raise RunnerError("REPOSITORY_TOO_LARGE")
    return resolved


def _run_process(
    command: Sequence[str],
    cwd: Path,
    *,
    input_text: str | None = None,
    timeout_seconds: float,
) -> tuple[int | None, bool, str]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        output = "".join(
            value if isinstance(value, str) else value.decode("utf-8", errors="replace")
            for value in (exc.stdout or "", exc.stderr or "")
        )
        return None, True, output
    output = completed.stdout + completed.stderr
    return completed.returncode, False, output


def run_patch_task(
    repo_root: Path,
    patch_text: str,
    test_command: Sequence[str] = ("pytest", "-q"),
    *,
    timeout_seconds: float = 30.0,
    max_output_chars: int = 20_000,
    max_repo_bytes: int = 200_000_000,
) -> RunResult:
    """Apply a patch and run tests in a disposable copy of ``repo_root``.

    The function never writes to the original repository. The test command is
    executed without a shell and only a small allowlist of interpreters is
    accepted. The returned result is safe to serialize as JSON.
    """

    if timeout_seconds <= 0:
        raise RunnerError("TIMEOUT_MUST_BE_POSITIVE")
    if max_output_chars < 256:
        raise RunnerError("OUTPUT_LIMIT_TOO_SMALL")
    if not isinstance(patch_text, str) or not patch_text.strip():
        raise RunnerError("PATCH_EMPTY")

    resolved_repo = _validate_repository(Path(repo_root), max_repo_bytes)
    command = _validate_test_command(test_command)

    with tempfile.TemporaryDirectory(prefix="repoevo-safe-run-") as temporary_dir:
        isolated_repo = Path(temporary_dir) / resolved_repo.name
        shutil.copytree(resolved_repo, isolated_repo, symlinks=False)

        check_code, check_timeout, check_output = _run_process(
            ["git", "apply", "--check", "--whitespace=nowarn", "-"],
            isolated_repo,
            input_text=patch_text,
            timeout_seconds=min(timeout_seconds, 10.0),
        )
        if check_timeout:
            return RunResult(
                success=False,
                patch_applied=False,
                tests_passed=False,
                timed_out=True,
                exit_code=None,
                test_command=command,
                output=_truncate(check_output, max_output_chars),
                diff="",
                error_code="PATCH_CHECK_TIMEOUT",
            )
        if check_code != 0:
            return RunResult(
                success=False,
                patch_applied=False,
                tests_passed=False,
                timed_out=False,
                exit_code=check_code,
                test_command=command,
                output=_truncate(check_output, max_output_chars),
                diff="",
                error_code="PATCH_INVALID",
            )

        apply_code, apply_timeout, apply_output = _run_process(
            ["git", "apply", "--whitespace=nowarn", "-"],
            isolated_repo,
            input_text=patch_text,
            timeout_seconds=min(timeout_seconds, 10.0),
        )
        if apply_timeout or apply_code != 0:
            return RunResult(
                success=False,
                patch_applied=False,
                tests_passed=False,
                timed_out=apply_timeout,
                exit_code=apply_code,
                test_command=command,
                output=_truncate(apply_output, max_output_chars),
                diff="",
                error_code="PATCH_APPLY_TIMEOUT" if apply_timeout else "PATCH_APPLY_FAILED",
            )

        test_code, test_timeout, test_output = _run_process(
            command,
            isolated_repo,
            timeout_seconds=timeout_seconds,
        )
        diff_code, _, diff_output = _run_process(
            ["git", "diff", "--no-ext-diff", "--binary"],
            isolated_repo,
            timeout_seconds=min(timeout_seconds, 10.0),
        )
        if diff_code != 0:
            diff_output = ""

        return RunResult(
            success=(not test_timeout and test_code == 0),
            patch_applied=True,
            tests_passed=(not test_timeout and test_code == 0),
            timed_out=test_timeout,
            exit_code=test_code,
            test_command=command,
            output=_truncate(test_output, max_output_chars),
            diff=diff_output,
            error_code="TEST_TIMEOUT" if test_timeout else None,
        )
