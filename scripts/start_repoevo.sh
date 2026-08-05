#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
cd "${ROOT_DIR}"
exec "${PYTHON_BIN}" -m uvicorn apps.api.main:app \
  --host "${API_HOST:-127.0.0.1}" \
  --port "${API_PORT:-8080}"
