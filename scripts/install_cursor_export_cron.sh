#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$BASE_DIR/scripts/run_cursor_export.sh"
ENV_FILE="${CURSOR_EXPORT_ENV_FILE:-$BASE_DIR/.env}"
SCHEDULE="${CURSOR_EXPORT_CRON_SCHEDULE:-5 0 * * *}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file not found: $ENV_FILE" >&2
  exit 11
fi

CRON_CMD="CURSOR_EXPORT_ENV_FILE=\"$ENV_FILE\" \"$RUNNER\" --bootstrap-mode baseline"
NEW_LINE="$SCHEDULE $CRON_CMD"

TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT

if crontab -l >/dev/null 2>&1; then
  crontab -l | grep -Fv "$RUNNER" > "$TMP_FILE" || true
fi

echo "$NEW_LINE" >> "$TMP_FILE"
crontab "$TMP_FILE"

echo "Installed cron entry: $NEW_LINE"
