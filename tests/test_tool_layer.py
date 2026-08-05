from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repoevo.sandbox_client import SandboxResult
from repoevo.tool_layer import (
    ToolError,
    apply_patch,
    list_files,
    read_file,
    replace_text,
    run_repository_tests,
    snapshot_text_files,
)


def init_git(root: Path) -> None:
    for args in [
        ["git", "init", "-q"],
        ["git", "config", "user.name", "Tool Layer Test"],
        ["git", "config", "user.email", "tool-layer@test"],
        ["git", "add", "."],
        ["git", "commit", "-qm", "baseline"],
    ]:
        result = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def fake_result() -> SandboxResult:
    return SandboxResult(
        ok=True,
        returncode=0,
        timed_out=False,
        stdout="1 passed",
        stderr="",
        error_code=None,
        command=["pytest", "-q"],
        files=["test_ok.py"],
        limits={"network": "none"},
    )


def test_snapshot_skips_git_and_caches(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"binary")
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    assert list_files(tmp_path) == ["main.py"]
    assert snapshot_text_files(tmp_path) == {"main.py": "print('ok')\n"}


def test_read_file_rejects_traversal(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("ok", encoding="utf-8")
    with pytest.raises(ToolError, match="PATH_INVALID"):
        read_file(tmp_path, "../main.py")


def test_snapshot_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("ok", encoding="utf-8")
    (tmp_path / "link.py").symlink_to(target)
    with pytest.raises(ToolError, match="SYMLINK_NOT_ALLOWED"):
        list_files(tmp_path)
    with pytest.raises(ToolError, match="SYMLINK_NOT_ALLOWED"):
        read_file(tmp_path, "link.py")


def test_apply_patch_returns_a_machine_readable_error_with_diagnostic(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)

    invalid_patch = """diff --git a/main.py b/main.py
--- a/main.py
+++ b/main.py
@@ -1 +1 @@
-VALUE = 0
+VALUE = 2
"""
    with pytest.raises(ToolError) as captured:
        apply_patch(tmp_path, invalid_patch)

    assert str(captured.value).startswith("PATCH_INVALID:")


def test_apply_patch_wraps_a_target_bound_hunk(tmp_path: Path) -> None:
    target = tmp_path / "main.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)

    changed = apply_patch(
        tmp_path,
        """@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
""",
        target_path="main.py",
    )

    assert changed == ["main.py"]
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_apply_patch_preserves_blank_diff_context_lines(tmp_path: Path) -> None:
    target = tmp_path / "main.py"
    target.write_text(
        'from decimal import Decimal\n\ndef value() -> str:\n    return "old"\n\n',
        encoding="utf-8",
    )
    init_git(tmp_path)

    patch_text = (
        "diff --git a/main.py b/main.py\n"
        "--- a/main.py\n"
        "+++ b/main.py\n"
        "@@ -1,5 +1,5 @@\n"
        " from decimal import Decimal\n"
        " \n"
        " def value() -> str:\n"
        '-    return "old"\n'
        '+    return "new"\n'
        " \n"
    )
    changed = apply_patch(tmp_path, patch_text)

    assert changed == ["main.py"]
    assert target.read_text(encoding="utf-8").endswith('return "new"\n\n')


def test_replace_text_requires_one_exact_source_match(tmp_path: Path) -> None:
    target = tmp_path / "main.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)

    changed = replace_text(tmp_path, "main.py", "VALUE = 1", "VALUE = 2")

    assert changed == ["main.py"]
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"
    with pytest.raises(ToolError, match="OLD_TEXT_NOT_FOUND"):
        replace_text(tmp_path, "main.py", "VALUE = 1", "VALUE = 3")


def test_replace_text_rejects_an_ambiguous_source_match(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("VALUE = 1\nVALUE = 1\n", encoding="utf-8")
    init_git(tmp_path)

    with pytest.raises(ToolError, match="OLD_TEXT_NOT_UNIQUE"):
        replace_text(tmp_path, "main.py", "VALUE = 1", "VALUE = 2")


def test_run_repository_tests_sends_snapshot_to_sandbox(tmp_path: Path) -> None:
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    with patch("repoevo.tool_layer.run_in_sandbox", return_value=fake_result()) as mocked:
        result = run_repository_tests(tmp_path)
    assert result.ok is True
    payload = mocked.call_args.args[0]
    assert payload == {"test_ok.py": "def test_ok():\n    assert True\n"}
