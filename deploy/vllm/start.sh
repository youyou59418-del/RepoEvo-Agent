#!/usr/bin/env bash
set -euo pipefail

: "${VLLM_MODEL_PATH:?Set VLLM_MODEL_PATH to a local model directory}"
: "${VLLM_API_KEY:?Set VLLM_API_KEY in the private environment}"
VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-8000}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-8192}"
VLLM_GENERATION_CONFIG="${VLLM_GENERATION_CONFIG:-vllm}"

if [[ "${REPOEVO_VLLM_DRY_RUN:-0}" == "1" ]]; then
  command -v vllm >/dev/null
  command -v nvidia-smi >/dev/null
  echo "vllm_config_valid=true host=${VLLM_HOST} port=${VLLM_PORT} max_model_len=${VLLM_MAX_MODEL_LEN} generation_config=${VLLM_GENERATION_CONFIG}"
  exit 0
fi

command -v vllm >/dev/null
nvidia-smi >/dev/null
exec vllm serve "${VLLM_MODEL_PATH}" \
  --host "${VLLM_HOST}" \
  --port "${VLLM_PORT}" \
  --api-key "${VLLM_API_KEY}" \
  --max-model-len "${VLLM_MAX_MODEL_LEN}" \
  --generation-config "${VLLM_GENERATION_CONFIG}"
