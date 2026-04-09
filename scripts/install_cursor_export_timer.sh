#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_SRC="$BASE_DIR/deploy/systemd/cursor-chat-export.service"
TIMER_SRC="$BASE_DIR/deploy/systemd/cursor-chat-export.timer"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENV_FILE="${CURSOR_EXPORT_ENV_FILE:-$BASE_DIR/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file not found: $ENV_FILE" >&2
  exit 11
fi

mkdir -p "$SYSTEMD_USER_DIR"
cp "$SERVICE_SRC" "$SYSTEMD_USER_DIR/cursor-chat-export.service"
cp "$TIMER_SRC" "$SYSTEMD_USER_DIR/cursor-chat-export.timer"

systemctl --user daemon-reload
systemctl --user enable --now cursor-chat-export.timer
systemctl --user status cursor-chat-export.timer --no-pager

echo "Installed systemd user timer."
