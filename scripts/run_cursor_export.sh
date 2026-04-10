#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
EXPORTER="$SCRIPT_DIR/export_cursor_chat_deltas.py"
ENV_FILE="${CURSOR_EXPORT_ENV_FILE:-$BASE_DIR/.env}"
LOCK_FILE="${CURSOR_EXPORT_LOCK_FILE:-/tmp/cursor-chat-export.lock}"
PYTHON_BIN="${CURSOR_EXPORT_PYTHON_BIN:-python3}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file not found: $ENV_FILE" >&2
  exit 11
fi

# Load and export all variables defined in the env file.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

for var_name in CURSOR_PROJECTS_ROOT CURSOR_EXPORT_OUTPUT_ROOT CURSOR_EXPORT_STATE_DIR; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "Missing required environment variable: $var_name" >&2
    exit 10
  fi
  if [[ "${!var_name}" != /* ]]; then
    echo "Environment variable must be an absolute path: $var_name=${!var_name}" >&2
    exit 11
  fi
  if [[ ! -d "${!var_name}" ]]; then
    echo "Configured path does not exist or is not a directory: $var_name=${!var_name}" >&2
    exit 11
  fi
done

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python binary not found: $PYTHON_BIN" >&2
  exit 11
fi

mkdir -p "$CURSOR_EXPORT_OUTPUT_ROOT/logs"
LOG_FILE="$CURSOR_EXPORT_OUTPUT_ROOT/logs/export.log"

{
  echo "[$(date --iso-8601=seconds)] starting export"
  flock -n "$LOCK_FILE" "$PYTHON_BIN" "$EXPORTER" "$@"
  rc=$?
  echo "[$(date --iso-8601=seconds)] finished export rc=$rc"
  exit $rc
} >>"$LOG_FILE" 2>&1
