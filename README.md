# cursor-chat-exporter

Incremental exporter for Cursor agent transcript JSONL files.

This project reads Cursor transcript files, tracks byte offsets per file, and writes readable Markdown exports for new chat content only (delta export).

> Development status: This project is currently under active development and may change.

## What It Does

- Scans `CURSOR_TRANSCRIPTS_ROOT` recursively for `*.jsonl` transcript files.
- Reads only newly appended content using per-file byte offsets.
- Extracts text messages and writes:
  - Per-chat Markdown: `daily/YYYY-MM-DD/chats/<chat_id>.md`
  - Combined daily Markdown: `daily/YYYY-MM-DD/combined.md`
- Writes run metadata and state:
  - `CURSOR_EXPORT_STATE_DIR/offsets.json`
  - `CURSOR_EXPORT_STATE_DIR/last_run.json`
- Logs execution to:
  - `CURSOR_EXPORT_OUTPUT_ROOT/logs/export.log`
- Prevents overlapping runs using a lock file.

## Requirements

- Python 3 (`python3` on Linux/macOS, `python` on Windows by default)
- One of:
  - Linux or macOS shell environment with `bash` and `flock`
  - Windows with Windows PowerShell 5.1+
- Optional scheduling:
  - `systemd --user` (recommended on Linux), or
  - `cron` (Linux/macOS), or
  - Windows Task Scheduler

## Configuration

The project uses a `.env` file at repository root by default.

1. Copy the template:

```bash
cp .env.template .env
```

2. Edit `.env` and set absolute paths:

```dotenv
CURSOR_TRANSCRIPTS_ROOT=/absolute/path/to/.cursor/projects/<project>/agent-transcripts
CURSOR_EXPORT_OUTPUT_ROOT=/absolute/path/to/cursor-chat-exports
CURSOR_EXPORT_STATE_DIR=/absolute/path/to/.local/state/cursor-chat-export

# Optional
CURSOR_EXPORT_PYTHON_BIN=python3
CURSOR_EXPORT_LOCK_FILE=/tmp/cursor-chat-export.lock
```

3. Ensure required directories already exist:

```bash
mkdir -p "$CURSOR_EXPORT_OUTPUT_ROOT"
mkdir -p "$CURSOR_EXPORT_STATE_DIR"
```

You can override env file location with `CURSOR_EXPORT_ENV_FILE`.

## Bootstrap Modes

On the first run (no `offsets.json` yet), choose behavior with `--bootstrap-mode`:

- `baseline` (default): initialize offsets to current file sizes, export nothing on first run.
- `backfill`: start from offset `0`, export historical transcript content on first run.

## Manual Usage

Run exporter directly through the wrapper:

```bash
./scripts/run_cursor_export.sh --bootstrap-mode baseline --verbose
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_cursor_export.ps1 --bootstrap-mode baseline --verbose
```

Dry run (no writes to outputs/state):

```bash
./scripts/run_cursor_export.sh --dry-run --verbose
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_cursor_export.ps1 --dry-run --verbose
```

Run with backfill:

```bash
./scripts/run_cursor_export.sh --bootstrap-mode backfill --verbose
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_cursor_export.ps1 --bootstrap-mode backfill --verbose
```

## Scheduling

### Option A: systemd user timer (recommended)

Install timer/service:

```bash
./scripts/install_cursor_export_timer.sh
```

Defaults:
- Runs daily at `00:05`.
- The install script writes your real repository path into the user unit (see `EnvironmentFile` / `WorkingDirectory` / `ExecStart` in `~/.config/systemd/user/cursor-chat-export.service`).

Useful commands:

```bash
systemctl --user status cursor-chat-export.timer
systemctl --user list-timers | grep cursor-chat-export
journalctl --user -u cursor-chat-export.service --no-pager
```

### Option B: cron

Install cron entry:

```bash
./scripts/install_cursor_export_cron.sh
```

Defaults:
- Schedule: `5 0 * * *` (daily at 00:05)
- Uses repo `.env` path by passing `CURSOR_EXPORT_ENV_FILE`

Override schedule at install time:

```bash
CURSOR_EXPORT_CRON_SCHEDULE="*/30 * * * *" ./scripts/install_cursor_export_cron.sh
```

### Option C: Windows Task Scheduler

Install/update a daily scheduled task (default `00:05`):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_cursor_export_task.ps1
```

Optional environment overrides before install:

```powershell
$env:CURSOR_EXPORT_ENV_FILE = "C:\path\to\cursor-chat-exporter\.env"
$env:CURSOR_EXPORT_TASK_NAME = "cursor-chat-export"
$env:CURSOR_EXPORT_TASK_TIME = "00:05"
powershell -ExecutionPolicy Bypass -File .\scripts\install_cursor_export_task.ps1
```

## Output Layout

Given:

- `CURSOR_EXPORT_OUTPUT_ROOT=/path/exports`
- Date = `2026-04-09`

You will see:

```text
/path/exports/
  logs/
    export.log
  daily/
    2026-04-09/
      combined.md
      chats/
        <chat_id>.md
```

State files live in `CURSOR_EXPORT_STATE_DIR`:

```text
offsets.json
last_run.json
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `10` | Missing required environment variable |
| `11` | Invalid or missing path, lock busy, or wrapper configuration error |
| `12` | Invalid `offsets.json` (not a JSON object, bad offset values, or negative offsets) |
| `20` | Runtime error (including invalid `--date` format, I/O failures, uncaught exceptions) |

The Python exporter uses `10`, `11`, `12`, and `20`. Shell and PowerShell wrappers also use `11` for several validation failures (paths, missing `.env`, missing Python, lock held).

## Consistency and duplicates

- **Crash mid-run:** Markdown may be appended before `offsets.json` is rewritten at the end of a successful run. If the process stops early, the next run may **export the same transcript lines again** (duplicate entries in `combined.md` / per-chat files). Delete or edit those files if needed, or restore `offsets.json` from backup.
- **Truncated transcript files:** If a `.jsonl` shrinks below the stored byte offset, the exporter resets to offset `0` and may **re-export** older content.
- **Large catch-up:** Very large new tails are read in one pass; unusually huge single-shot growth can use significant memory.

## Linux wrapper logging

`run_cursor_export.sh` appends **all** exporter stdout/stderr to `CURSOR_EXPORT_OUTPUT_ROOT/logs/export.log`. Interactive runs do not print errors to the terminal; use `tail -f` on that log to debug.

## Troubleshooting

- `Environment file not found`: create `.env` or set `CURSOR_EXPORT_ENV_FILE`.
- `Missing required environment variable`: ensure all required keys are present in `.env`.
- `Configured path does not exist`: create directories and use absolute paths.
- `Invalid offsets.json`: fix or remove `CURSOR_EXPORT_STATE_DIR/offsets.json` (removal triggers a new bootstrap on next run; use `--bootstrap-mode` as needed).
- No output on first run: expected if using `--bootstrap-mode baseline`.
- Repeated/overlapping runs: check lock file configuration and scheduler frequency.
- On Linux/macOS the wrapper uses `flock`; on Windows the PowerShell runner uses an exclusive lock file handle. Both prevent overlapping runs.

