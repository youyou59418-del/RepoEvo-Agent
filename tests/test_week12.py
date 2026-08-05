from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_deployment_artifacts_exist() -> None:
    required = [
        ROOT / "apps" / "web" / "package.json",
        ROOT / "apps" / "web" / "app" / "page.tsx",
        ROOT / "deploy" / "docker-compose.yml",
        ROOT / "deploy" / "Dockerfile.api",
        ROOT / "deploy" / "Dockerfile.web",
        ROOT / "deploy" / "vllm" / "start.sh",
        ROOT / "uv.lock",
        ROOT / "apps" / "web" / "package-lock.json",
        ROOT / ".github" / "workflows" / "ci.yml",
        ROOT / "LICENSE",
        ROOT / "CHANGELOG.md",
    ]
    assert all(path.exists() for path in required)


def test_vllm_dry_run_does_not_require_secrets() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "deploy" / "vllm" / "check_env.py"), "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "env_present" in result.stdout
    assert "replace_me" not in result.stdout


def test_compose_contains_control_plane_services() -> None:
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "postgres:" in compose
    assert "redis:" in compose
    assert "api:" in compose
    assert "web:" in compose
    assert "POSTGRES_PASSWORD" in compose
    assert 'profiles: ["worker"]' in compose


def test_docker_build_context_excludes_private_benchmark_assets() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "benchmarks/private/" in dockerignore
    assert "scripts/build_benchmarks.py" in dockerignore
