from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from repoevo.safe_patch_runner import RunnerError, run_patch_task

GOOD_PATCH = """diff --git a/calc.py b/calc.py
index 8b2d2c8..e3a6b7c 100644
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(left: int, right: int) -> int:
-    return left - right
+    return left + right
"""

BAD_LOGIC_PATCH = GOOD_PATCH.replace("return left + right", "return left * right")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "calc.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )
    (repo / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "RepoEvo Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    original = (repo / "calc.py").read_text(encoding="utf-8")
    return repo, original


def test_successful_patch_is_applied_only_in_the_copy(tmp_path: Path) -> None:
    repo, original = _make_repo(tmp_path)

    result = run_patch_task(repo, GOOD_PATCH, test_command=(sys.executable, "-m", "pytest", "-q"))

    assert result.success is True
    assert result.patch_applied is True
    assert result.tests_passed is True
    assert result.timed_out is False
    assert "return left + right" in result.diff
    assert (repo / "calc.py").read_text(encoding="utf-8") == original


def test_failed_tests_do_not_modify_the_original_repository(tmp_path: Path) -> None:
    repo, original = _make_repo(tmp_path)

    result = run_patch_task(repo, BAD_LOGIC_PATCH, test_command=(sys.executable, "-m", "pytest", "-q"))

    assert result.success is False
    assert result.patch_applied is True
    assert result.tests_passed is False
    assert "1 failed" in result.output
    assert (repo / "calc.py").read_text(encoding="utf-8") == original


def test_invalid_patch_is_rejected_before_tests(tmp_path: Path) -> None:
    repo, original = _make_repo(tmp_path)
    invalid_patch = GOOD_PATCH.replace("calc.py", "missing.py")

    result = run_patch_task(repo, invalid_patch, test_command=(sys.executable, "-m", "pytest", "-q"))

    assert result.success is False
    assert result.patch_applied is False
    assert result.error_code == "PATCH_INVALID"
    assert (repo / "calc.py").read_text(encoding="utf-8") == original


def test_timeout_is_reported_and_original_is_untouched(tmp_path: Path) -> None:
    repo, original = _make_repo(tmp_path)

    result = run_patch_task(
        repo,
        GOOD_PATCH,
        test_command=(sys.executable, "-c", "import time\ntime.sleep(2)"),
        timeout_seconds=0.1,
    )

    assert result.success is False
    assert result.timed_out is True
    assert result.error_code == "TEST_TIMEOUT"
    assert (repo / "calc.py").read_text(encoding="utf-8") == original


def test_shell_syntax_and_unapproved_program_are_rejected(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    with pytest.raises(RunnerError, match="TEST_PROGRAM_NOT_ALLOWED"):
        run_patch_task(repo, GOOD_PATCH, test_command=("bash", "-lc", "pytest"))

    with pytest.raises(RunnerError, match="TEST_SHELL_SYNTAX_NOT_ALLOWED"):
        run_patch_task(repo, GOOD_PATCH, test_command=("pytest", "-q", "&&", "whoami"))



def test_empty_patch_is_rejected(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    with pytest.raises(RunnerError, match="PATCH_EMPTY"):
        run_patch_task(repo, "", test_command=(sys.executable, "-m", "pytest", "-q"))


def test_missing_repository_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RunnerError, match="REPOSITORY_NOT_FOUND"):
        run_patch_task(tmp_path / "does-not-exist", GOOD_PATCH)


def test_symlink_is_rejected(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    (repo / "outside-link").symlink_to(tmp_path / "outside-target")

    with pytest.raises(RunnerError, match="SYMLINK_NOT_ALLOWED"):
        run_patch_task(repo, GOOD_PATCH)


def test_repository_size_limit_is_enforced(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    with pytest.raises(RunnerError, match="REPOSITORY_TOO_LARGE"):
        run_patch_task(repo, GOOD_PATCH, max_repo_bytes=1)


def test_output_is_truncated_to_the_declared_limit(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    result = run_patch_task(
        repo,
        GOOD_PATCH,
        test_command=(sys.executable, "-c", "print('x' * 1000)"),
        max_output_chars=256,
    )

    assert result.success is True
    assert len(result.output) <= 256
    assert "output truncated" in result.output
