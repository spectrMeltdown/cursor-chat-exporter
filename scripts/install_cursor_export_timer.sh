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
PYTHON_BIN="${CURSOR_EXPORT_PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python binary not found: $PYTHON_BIN" >&2
  exit 11
fi
"$PYTHON_BIN" -c '
from pathlib import Path
import sys
base, dst, src = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
dst.write_text(src.read_text().replace("@@REPO_ROOT@@", str(base)))
' "$BASE_DIR" "$SYSTEMD_USER_DIR/cursor-chat-export.service" "$SERVICE_SRC"
cp "$TIMER_SRC" "$SYSTEMD_USER_DIR/cursor-chat-export.timer"

systemctl --user daemon-reload
systemctl --user enable --now cursor-chat-export.timer
systemctl --user status cursor-chat-export.timer --no-pager

echo "Installed systemd user timer."
