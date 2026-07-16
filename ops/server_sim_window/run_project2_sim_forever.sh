#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/opt/apps/project2"
OUTPUT_ROOT="/opt/apps/project2-sim-data"
ENV_FILE="${PROJECT_ROOT}/.env.simulation"
LIVE_LOG_PATH="${PROJECT_ROOT}/tmp/server/live_runtime.jsonl"
LOCK_PATH="${OUTPUT_ROOT}/rwusd_sim_daemon.lock"

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

exec 9>"${LOCK_PATH}"
if ! flock -n 9; then
  echo "RWUSD simulation daemon already running: ${LOCK_PATH}" >&2
  exit 1
fi

./.venv/bin/python scripts/run_server_sim_daemon.py \
  --env-file "${ENV_FILE}" \
  --live-log-path "${LIVE_LOG_PATH}" \
  --output-root "${OUTPUT_ROOT}" \
  --max-loops 100000 \
  --restart-delay-seconds 2 \
  --failure-backoff-seconds 10 \
  --failure-backoff-multiplier 2 \
  --max-failure-backoff-seconds 120
