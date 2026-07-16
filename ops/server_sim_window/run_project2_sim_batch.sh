#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/opt/apps/project2"
OUTPUT_ROOT="/opt/apps/project2-sim-data"
ENV_FILE="${PROJECT_ROOT}/.env.simulation"
LIVE_LOG_PATH="${PROJECT_ROOT}/tmp/server/live_runtime.jsonl"

cd "${PROJECT_ROOT}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "missing env file: ${ENV_FILE}" >&2
  exit 1
fi

if [[ ! -f "${LIVE_LOG_PATH}" ]]; then
  echo "missing live log path: ${LIVE_LOG_PATH}" >&2
  exit 1
fi

mkdir -p "${OUTPUT_ROOT}"

python3 scripts/run_server_sim_batch.py \
  --env-file "${ENV_FILE}" \
  --live-log-path "${LIVE_LOG_PATH}" \
  --output-root "${OUTPUT_ROOT}" \
  --max-loops 100
