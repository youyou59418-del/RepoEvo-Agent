"""Safe repository tools used by later RepoEvo Agent graphs."""

from __future__ import annotations

import difflib
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from .sandbox_client import SandboxResult, run_in_sandbox


class ToolError(ValueError):
    """Raised when a repository tool request is unsafe or invalid."""


MAX_FILES = 128
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
SKIPPED_DIRECTORIES = {".git", "__pycache__", ".pytest_cache"}


def _patch_error(error_code: str, stderr: str) -> ToolError:
    """Keep a stable machine-readable code plus a compact repair hint."""

    detail = " ".join(stderr.splitlines()).strip()
    return ToolError(f"{error_code}: {detail[:240]}" if detail else error_code)


def _normalize_patch(
    root: Path,
    patch_text: str,
    *,
    target_path: str | None,
) -> str:
    """Accept a fenced diff or a target-bound hunk without broadening write scope."""

    normalized = patch_text
    lines = patch_text.splitlines(keepends=True)
    fence = chr(96) * 3
    if len(lines) >= 2 and lines[0].strip().startswith(fence) and lines[-1].strip() == fence:
        normalized = "".join(lines[1:-1])
        if normalized and not normalized.endswith("\n"):
            normalized += "\n"
    hunk = normalized.lstrip()
    if not hunk.startswith("@@"):
        return normalized
    if target_path is None:
        raise ToolError("PATCH_TARGET_REQUIRED")
    relative = _relative_path(target_path).as_posix()
    _safe_file(root, relative)
    return f"diff --git a/{relative} b/{relative}\n--- a/{relative}\n+++ b/{relative}\n{hunk}"


def _root(path: Path) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise ToolError("REPOSITORY_NOT_FOUND") from exc
    if not resolved.is_dir():
        raise ToolError("REPOSITORY_NOT_DIRECTORY")
    return resolved


def _relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ToolError("PATH_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ToolError("PATH_INVALID")
    normalized = "/".join(path.parts)
    if normalized != value:
        raise ToolError("PATH_INVALID")
    return path


def _safe_file(root: Path, relative: str) -> Path:
    path = _relative_path(relative)
    raw_candidate = root
    for part in path.parts:
        raw_candidate /= part
        if raw_candidate.is_symlink():
            raise ToolError("SYMLINK_NOT_ALLOWED")
    candidate = raw_candidate.resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ToolError("PATH_ESCAPE") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise ToolError("FILE_NOT_REGULAR")
    return candidate


def list_files(repo_root: Path, *, max_files: int = MAX_FILES) -> list[str]:
    """Return deterministic relative file paths without symlinks or caches."""

    root = _root(repo_root)
    if max_files <= 0:
        raise ToolError("MAX_FILES_INVALID")
    result: list[str] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(directories)
        for directory in list(directories):
            if directory in SKIPPED_DIRECTORIES:
                directories.remove(directory)
            elif (Path(current) / directory).is_symlink():
                raise ToolError("SYMLINK_NOT_ALLOWED")
        for filename in sorted(filenames):
            candidate = Path(current) / filename
            if candidate.is_symlink():
                raise ToolError("SYMLINK_NOT_ALLOWED")
            relative = candidate.relative_to(root).as_posix()
            if len(result) >= max_files:
                raise ToolError("TOO_MANY_FILES")
            result.append(relative)
    return result


def read_file(repo_root: Path, relative: str, *, max_bytes: int = MAX_FILE_BYTES) -> str:
    """Read one UTF-8 repository file after validating its relative path."""

    if max_bytes <= 0:
        raise ToolError("MAX_BYTES_INVALID")
    root = _root(repo_root)
    candidate = _safe_file(root, relative)
    if candidate.stat().st_size > max_bytes:
        raise ToolError("FILE_TOO_LARGE")
    try:
        return candidate.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError("BINARY_FILE_NOT_ALLOWED") from exc


def snapshot_text_files(
    repo_root: Path,
    *,
    max_files: int = MAX_FILES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> dict[str, str]:
    """Build the file-content payload sent to the Docker sandbox."""

    if max_total_bytes <= 0:
        raise ToolError("MAX_TOTAL_BYTES_INVALID")
    snapshot: dict[str, str] = {}
    total_bytes = 0
    for relative in list_files(repo_root, max_files=max_files):
        content = read_file(repo_root, relative)
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > max_total_bytes:
            raise ToolError("FILES_TOO_LARGE")
        snapshot[relative] = content
    return snapshot


def apply_patch(
    repo_root: Path,
    patch_text: str,
    *,
    target_path: str | None = None,
    timeout_seconds: float = 10.0,
) -> list[str]:
    """Validate and apply a unified diff inside an isolated Git workspace."""

    if not isinstance(patch_text, str) or not patch_text.strip():
        raise ToolError("PATCH_EMPTY")
    if timeout_seconds <= 0:
        raise ToolError("TIMEOUT_INVALID")
    root = _root(repo_root)
    if not (root / ".git").exists():
        raise ToolError("GIT_REPOSITORY_REQUIRED")
    normalized_patch = _normalize_patch(root, patch_text, target_path=target_path)
    for arguments, error_code in [
        (["git", "apply", "--check", "--whitespace=nowarn", "-"], "PATCH_INVALID"),
        (["git", "apply", "--whitespace=nowarn", "-"], "PATCH_APPLY_FAILED"),
    ]:
        try:
            result = subprocess.run(
                arguments,
                cwd=root,
                input=normalized_patch,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError("PATCH_TIMEOUT") from exc
        if result.returncode != 0:
            raise _patch_error(error_code, result.stderr)
    try:
        changed = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("DIFF_TIMEOUT") from exc
    if changed.returncode != 0:
        raise ToolError("DIFF_FAILED")
    return [line for line in changed.stdout.splitlines() if line]


def replace_text(
    repo_root: Path,
    relative: str,
    old_text: str,
    new_text: str,
) -> list[str]:
    """Apply one exact, uniquely matched source replacement through git apply."""

    if not isinstance(old_text, str) or not old_text:
        raise ToolError("OLD_TEXT_REQUIRED")
    if not isinstance(new_text, str):
        raise ToolError("NEW_TEXT_INVALID")
    root = _root(repo_root)
    path = _relative_path(relative).as_posix()
    content = read_file(root, path)
    occurrences = content.count(old_text)
    if occurrences == 0:
        raise ToolError("OLD_TEXT_NOT_FOUND")
    if occurrences != 1:
        raise ToolError("OLD_TEXT_NOT_UNIQUE")
    updated = content.replace(old_text, new_text, 1)
    if updated == content:
        raise ToolError("REPLACEMENT_NO_CHANGE")
    patch = f"diff --git a/{path} b/{path}\n" + "".join(
        difflib.unified_diff(
            content.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return apply_patch(root, patch)


def run_repository_tests(
    repo_root: Path,
    command: Sequence[str] = ("pytest", "-q"),
    *,
    timeout_seconds: float = 30.0,
    base_url: str | None = None,
) -> SandboxResult:
    """Run a repository snapshot in the local Docker execution plane."""

    files = snapshot_text_files(repo_root)
    return run_in_sandbox(
        files,
        command,
        timeout_seconds=timeout_seconds,
        base_url=base_url,
    )
