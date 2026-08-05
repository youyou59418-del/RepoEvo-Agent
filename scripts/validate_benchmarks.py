from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

from repoevo.benchmark_access import (
    load_private_task,
    load_public_manifest,
    private_hidden_test_path,
)


def run(
    command: list[str], cwd: Path, *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


def init_repo(root: Path) -> None:
    for command in [
        ["git", "init", "-q"],
        ["git", "config", "user.name", "RepoEvo Validator"],
        ["git", "config", "user.email", "validator@localhost"],
        ["git", "add", "."],
        ["git", "commit", "-qm", "baseline"],
    ]:
        result = run(command, root)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())


def apply_patch(root: Path, patch_text: str) -> None:
    result = run(["git", "apply", "--whitespace=nowarn"], root, input_text=patch_text)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())


def run_tests(root: Path, hidden_test: Path) -> subprocess.CompletedProcess[str]:
    for cache in list(root.rglob("__pycache__")):
        if cache.is_dir():
            shutil.rmtree(cache)
    pytest_cache = root / ".pytest_cache"
    if pytest_cache.exists():
        shutil.rmtree(pytest_cache)
    test_path = root / "tests" / "test_hidden.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(hidden_test, test_path)
    environment = os.environ.copy()
    # Make the temporary repository the only project import location. This
    # prevents an identically named package from another fixture affecting a
    # later task in the same validation run.
    environment["PYTHONPATH"] = str(root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--import-mode=importlib"],
        cwd=root,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def main() -> None:
    manifest = load_public_manifest()
    failures: list[str] = []
    buggy_failures = 0

    for task_id in manifest["tasks"]:
        task = load_private_task(str(task_id))
        fixture = ROOT / "fixtures" / task["repository"]
        hidden_test = private_hidden_test_path(str(task_id))

        with tempfile.TemporaryDirectory(prefix=f"repoevo-{task_id}-") as temporary:
            workspace = Path(temporary)
            shutil.copytree(
                fixture,
                workspace,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
            )
            init_repo(workspace)

            apply_patch(workspace, task["bug_patch"])
            buggy_result = run_tests(workspace, hidden_test)
            if buggy_result.returncode == 0:
                failures.append(f"{task_id}: injected bug did not fail hidden/public tests")
            else:
                buggy_failures += 1

            apply_patch(workspace, task["reference_patch"])
            repaired_result = run_tests(workspace, hidden_test)
            if repaired_result.returncode:
                details = (repaired_result.stdout + repaired_result.stderr).strip()
                failures.append(f"{task_id}: reference repair failed\n{details}")

    if failures:
        raise SystemExit("\n\n".join(failures))
    print(f"validated={len(manifest['tasks'])}")
    print(f"buggy_hidden_or_public_failures={buggy_failures}")
    print("reference_repairs=all_pass")


if __name__ == "__main__":
    main()
