#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

EXIT_OK = 0
EXIT_MISSING_ENV = 10
EXIT_INVALID_PATH = 11
EXIT_INVALID_STATE = 12
EXIT_RUNTIME = 20

REQUIRED_ENV_VARS = (
    "CURSOR_PROJECTS_ROOT",
    "CURSOR_EXPORT_OUTPUT_ROOT",
    "CURSOR_EXPORT_STATE_DIR",
)


@dataclass
class Config:
    projects_root: Path
    output_root: Path
    state_dir: Path
    date_str: str
    dry_run: bool
    verbose: bool
    bootstrap_mode: str
    notify: bool
    notify_when: str


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def env_truthy(var_name: str) -> bool:
    value = os.environ.get(var_name, "").strip().lower()
    return value in ("1", "true", "yes", "on")


def require_env_path(env_name: str) -> Path:
    value = os.environ.get(env_name, "").strip()
    if not value:
        eprint(f"Missing required environment variable: {env_name}")
        raise SystemExit(EXIT_MISSING_ENV)
    path = Path(value).expanduser()
    if not path.is_absolute():
        eprint(f"Environment variable must be an absolute path: {env_name}={value}")
        raise SystemExit(EXIT_INVALID_PATH)
    if not path.exists():
        eprint(f"Configured path does not exist: {env_name}={path}")
        raise SystemExit(EXIT_INVALID_PATH)
    if not path.is_dir():
        eprint(f"Configured path is not a directory: {env_name}={path}")
        raise SystemExit(EXIT_INVALID_PATH)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export incremental text deltas from Cursor transcript JSONL files."
    )
    parser.add_argument("--date", help="Override output date (YYYY-MM-DD).")
    parser.add_argument("--dry-run", action="store_true", help="Do not write output/state.")
    parser.add_argument("--verbose", action="store_true", help="Print detailed run logs.")
    parser.add_argument(
        "--bootstrap-mode",
        choices=("baseline", "backfill"),
        default="baseline",
        help="First run behavior when no offsets state exists.",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send a desktop notification after success (subject to --notify-when).",
    )
    parser.add_argument(
        "--notify-when",
        choices=("new_data", "always"),
        default=None,
        help="When to notify: new_data (default) if new exports or baseline init; always on every success.",
    )
    return parser.parse_args()


def resolve_config(args: argparse.Namespace) -> Config:
    for var_name in REQUIRED_ENV_VARS:
        if os.environ.get(var_name, "").strip() == "":
            eprint(f"Missing required environment variable: {var_name}")
            raise SystemExit(EXIT_MISSING_ENV)

    projects_root = require_env_path("CURSOR_PROJECTS_ROOT")
    output_root = require_env_path("CURSOR_EXPORT_OUTPUT_ROOT")
    state_dir = require_env_path("CURSOR_EXPORT_STATE_DIR")

    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
            date_str = args.date
        except ValueError:
            eprint("Invalid --date format; expected YYYY-MM-DD.")
            raise SystemExit(EXIT_RUNTIME)
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    notify = bool(args.notify) or env_truthy("CURSOR_EXPORT_NOTIFY")
    if args.notify_when is not None:
        notify_when = args.notify_when
    else:
        raw = os.environ.get("CURSOR_EXPORT_NOTIFY_WHEN", "").strip().lower()
        notify_when = raw if raw in ("always", "new_data") else "new_data"

    return Config(
        projects_root=projects_root,
        output_root=output_root,
        state_dir=state_dir,
        date_str=date_str,
        dry_run=args.dry_run,
        verbose=args.verbose,
        bootstrap_mode=args.bootstrap_mode,
        notify=notify,
        notify_when=notify_when,
    )


def iter_transcript_files(projects_root: Path) -> List[Tuple[str, Path]]:
    files: List[Tuple[str, Path]] = []
    for project_dir in sorted(projects_root.iterdir()):
        if not project_dir.is_dir():
            continue
        transcript_dir = project_dir / "agent-transcripts"
        if not transcript_dir.is_dir():
            continue
        for transcript_file in sorted(transcript_dir.rglob("*.jsonl")):
            files.append((project_dir.name, transcript_file))
    return files


def read_json_file(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def coerce_nonnegative_int(value) -> int:
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, int):
        n = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError
        n = int(value)
    elif isinstance(value, str):
        n = int(value.strip(), 10)
    else:
        raise ValueError
    if n < 0:
        raise ValueError
    return n


def parse_offsets_payload(raw) -> Dict[str, int]:
    if not isinstance(raw, dict):
        eprint("Invalid offsets.json: root must be a JSON object.")
        raise SystemExit(EXIT_INVALID_STATE)
    result: Dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            eprint(f"Invalid offsets.json: non-string key {key!r}.")
            raise SystemExit(EXIT_INVALID_STATE)
        try:
            result[key] = coerce_nonnegative_int(value)
        except ValueError:
            eprint(f"Invalid offsets.json: bad offset for {key!r}: {value!r}.")
            raise SystemExit(EXIT_INVALID_STATE)
    return result


def atomic_write_json(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=True, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp_path, path)


def extract_text_from_content(content) -> List[str]:
    texts: List[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    texts.append(item["text"])
    elif isinstance(content, str):
        texts.append(content)
    return texts


def extract_line_text(line: str) -> Tuple[str, List[str]]:
    data = json.loads(line)
    role = str(data.get("role", "unknown"))
    message = data.get("message", {})
    texts: List[str] = []
    if isinstance(message, dict):
        texts.extend(extract_text_from_content(message.get("content")))
    return role, [t.strip() for t in texts if isinstance(t, str) and t.strip()]


def read_new_lines(path: Path, start_offset: int) -> Tuple[List[str], int]:
    if start_offset < 0:
        start_offset = 0
    file_size = path.stat().st_size
    if file_size < start_offset:
        start_offset = 0
    if file_size == start_offset:
        return [], start_offset

    with path.open("rb") as fh:
        fh.seek(start_offset)
        chunk = fh.read()

    lines = chunk.splitlines(keepends=True)
    processed_bytes = 0
    if lines:
        last = lines[-1]
        if not (last.endswith(b"\n") or last.endswith(b"\r")):
            lines = lines[:-1]
        processed_bytes = sum(len(part) for part in lines)

    decoded_lines: List[str] = []
    for raw in lines:
        decoded = raw.decode("utf-8", errors="replace").strip()
        if decoded:
            decoded_lines.append(decoded)
    return decoded_lines, start_offset + processed_bytes


def format_entry(role: str, text: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"- [{timestamp}] {role}: {text}\n"


def ensure_output_paths(cfg: Config) -> Tuple[Path, Path]:
    day_dir = cfg.output_root / "daily" / cfg.date_str
    projects_dir = day_dir / "projects"
    if not cfg.dry_run:
        projects_dir.mkdir(parents=True, exist_ok=True)
    return day_dir, projects_dir


def append_text(path: Path, text: str, dry_run: bool) -> None:
    if dry_run:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def write_run_summary(cfg: Config, summary: dict) -> None:
    if cfg.dry_run:
        return
    last_run_path = cfg.state_dir / "last_run.json"
    atomic_write_json(last_run_path, summary)


def _sanitize_notification_text(text: str, max_len: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1] + "…"


def should_send_notification(cfg: Config, summary: dict) -> bool:
    if not cfg.notify or cfg.dry_run:
        return False
    if cfg.notify_when == "always":
        return True
    if summary.get("exported_entries", 0) > 0:
        return True
    if summary.get("bootstrap_only"):
        return True
    return False


def notification_payload(summary: dict) -> Tuple[str, str]:
    title = "Cursor chat export"
    if summary.get("bootstrap_only"):
        body = "Initialized baseline offsets. No exports on first run."
        return title, body
    parts = [f"Date {summary.get('date', 'n/a')}"]
    parts.append(f"exported {summary.get('exported_entries', 0)} entries")
    parse_errors = summary.get("parse_errors", 0)
    if parse_errors:
        parts.append(f"{parse_errors} parse errors")
    return title, ". ".join(parts)


def _notify_freedesktop(title: str, body: str, verbose: bool) -> None:
    app = "Cursor Chat Export"
    notify_send = shutil.which("notify-send")
    if notify_send:
        completed = subprocess.run(
            [notify_send, "-a", app, "-t", "10000", title, body],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode == 0:
            return
        if verbose:
            err = (completed.stderr or completed.stdout or "").strip()
            eprint(f"notify-send failed (rc={completed.returncode}): {err}")

    gdbus = shutil.which("gdbus")
    if not gdbus:
        if verbose:
            eprint("Desktop notification skipped: notify-send and gdbus not found.")
        return

    completed = subprocess.run(
        [
            gdbus,
            "call",
            "--session",
            "--dest",
            "org.freedesktop.Notifications",
            "--object-path",
            "/org/freedesktop/Notifications",
            "--method",
            "org.freedesktop.Notifications.Notify",
            app,
            "0",
            "",
            title,
            body,
            "[]",
            "{}",
            "10000",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0 and verbose:
        err = (completed.stderr or completed.stdout or "").strip()
        eprint(f"gdbus notification failed (rc={completed.returncode}): {err}")


def _notify_windows(title: str, body: str, verbose: bool) -> None:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    ps_exe = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.isfile(ps_exe):
        ps_exe = "powershell.exe"
    env = os.environ.copy()
    env["CURSOR_CHAT_EXPORT_NOTIF_TITLE"] = title
    env["CURSOR_CHAT_EXPORT_NOTIF_BODY"] = body
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        "$n.Visible = $true; "
        "$n.ShowBalloonTip(8000, $env:CURSOR_CHAT_EXPORT_NOTIF_TITLE, "
        "$env:CURSOR_CHAT_EXPORT_NOTIF_BODY, [System.Windows.Forms.ToolTipIcon]::Info); "
        "Start-Sleep -Seconds 9; "
        "$n.Dispose()"
    )
    run_kw: Dict[str, object] = dict(
        args=[
            ps_exe,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flags:
            run_kw["creationflags"] = flags
    completed = subprocess.run(**run_kw)
    if completed.returncode != 0 and verbose:
        err = (completed.stderr or completed.stdout or "").strip()
        eprint(f"Windows notification failed (rc={completed.returncode}): {err}")


def send_desktop_notification(title: str, body: str, *, verbose: bool) -> None:
    title = _sanitize_notification_text(title, 120)
    body = _sanitize_notification_text(body, 400)
    try:
        if sys.platform == "win32":
            _notify_windows(title, body, verbose)
        else:
            _notify_freedesktop(title, body, verbose)
    except OSError as exc:
        if verbose:
            eprint(f"Desktop notification failed: {exc}")
    except subprocess.TimeoutExpired:
        if verbose:
            eprint("Desktop notification timed out.")


def maybe_notify(cfg: Config, summary: dict) -> None:
    if not should_send_notification(cfg, summary):
        return
    title, body = notification_payload(summary)
    send_desktop_notification(title, body, verbose=cfg.verbose)


def bootstrap_offsets(
    cfg: Config, entries: List[Tuple[str, Path]], offsets_path: Path, mode: str
) -> Dict[str, int]:
    offsets: Dict[str, int] = {}
    for _project_name, file_path in entries:
        offsets[str(file_path)] = file_path.stat().st_size if mode == "baseline" else 0
    if not cfg.dry_run:
        atomic_write_json(offsets_path, offsets)
    return offsets


def main() -> int:
    args = parse_args()
    cfg = resolve_config(args)
    offsets_path = cfg.state_dir / "offsets.json"
    files = iter_transcript_files(cfg.projects_root)
    day_dir, projects_dir = ensure_output_paths(cfg)
    combined_path = day_dir / "combined.md"

    if cfg.verbose:
        print(f"Discovered {len(files)} transcript files")

    if not offsets_path.exists():
        offsets = bootstrap_offsets(cfg, files, offsets_path, cfg.bootstrap_mode)
        summary = {
            "time": datetime.now().isoformat(),
            "date": cfg.date_str,
            "bootstrap_mode": cfg.bootstrap_mode,
            "file_count": len(files),
            "exported_entries": 0,
            "bootstrap_only": cfg.bootstrap_mode == "baseline",
        }
        write_run_summary(cfg, summary)
        if cfg.bootstrap_mode == "baseline":
            print("Initialized baseline offsets. No exports written on first run.")
            maybe_notify(cfg, summary)
            return EXIT_OK
    else:
        offsets = parse_offsets_payload(read_json_file(offsets_path, {}))

    exported_entries = 0
    parse_errors = 0

    for project_name, file_path in files:
        key = str(file_path)
        start_offset = offsets.get(key, 0)
        lines, next_offset = read_new_lines(file_path, start_offset)
        offsets[key] = next_offset
        if not lines:
            continue

        chat_id = file_path.stem
        per_project_chats_dir = projects_dir / project_name / "chats"
        if not cfg.dry_run:
            per_project_chats_dir.mkdir(parents=True, exist_ok=True)
        per_chat_path = per_project_chats_dir / f"{chat_id}.md"
        combined_lines: List[str] = [f"\n## Project {project_name} / Chat {chat_id}\n"]

        for line in lines:
            try:
                role, texts = extract_line_text(line)
            except Exception:
                parse_errors += 1
                continue
            for text in texts:
                entry = format_entry(role, text)
                append_text(per_chat_path, entry, cfg.dry_run)
                combined_lines.append(entry)
                exported_entries += 1

        if len(combined_lines) > 1:
            append_text(combined_path, "".join(combined_lines), cfg.dry_run)

    if not cfg.dry_run:
        atomic_write_json(offsets_path, offsets)

    summary = {
        "time": datetime.now().isoformat(),
        "date": cfg.date_str,
        "file_count": len(files),
        "exported_entries": exported_entries,
        "parse_errors": parse_errors,
        "dry_run": cfg.dry_run,
        "bootstrap_mode": cfg.bootstrap_mode,
    }
    write_run_summary(cfg, summary)
    print(
        f"Run complete: files={len(files)} exported_entries={exported_entries} parse_errors={parse_errors}"
    )
    maybe_notify(cfg, summary)
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        eprint(f"Unhandled runtime error: {exc}")
        raise SystemExit(EXIT_RUNTIME)
