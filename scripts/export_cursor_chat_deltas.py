#!/usr/bin/env python3
import argparse
import json
import os
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
    "CURSOR_TRANSCRIPTS_ROOT",
    "CURSOR_EXPORT_OUTPUT_ROOT",
    "CURSOR_EXPORT_STATE_DIR",
)


@dataclass
class Config:
    transcripts_root: Path
    output_root: Path
    state_dir: Path
    date_str: str
    dry_run: bool
    verbose: bool
    bootstrap_mode: str


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


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
    return parser.parse_args()


def resolve_config(args: argparse.Namespace) -> Config:
    for var_name in REQUIRED_ENV_VARS:
        if os.environ.get(var_name, "").strip() == "":
            eprint(f"Missing required environment variable: {var_name}")
            raise SystemExit(EXIT_MISSING_ENV)

    transcripts_root = require_env_path("CURSOR_TRANSCRIPTS_ROOT")
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

    return Config(
        transcripts_root=transcripts_root,
        output_root=output_root,
        state_dir=state_dir,
        date_str=date_str,
        dry_run=args.dry_run,
        verbose=args.verbose,
        bootstrap_mode=args.bootstrap_mode,
    )


def iter_transcript_files(transcripts_root: Path) -> List[Path]:
    return sorted(transcripts_root.rglob("*.jsonl"))


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
    chats_dir = day_dir / "chats"
    if not cfg.dry_run:
        chats_dir.mkdir(parents=True, exist_ok=True)
    return day_dir, chats_dir


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


def bootstrap_offsets(cfg: Config, files: List[Path], offsets_path: Path, mode: str) -> Dict[str, int]:
    offsets: Dict[str, int] = {}
    for file_path in files:
        offsets[str(file_path)] = file_path.stat().st_size if mode == "baseline" else 0
    if not cfg.dry_run:
        atomic_write_json(offsets_path, offsets)
    return offsets


def main() -> int:
    args = parse_args()
    cfg = resolve_config(args)
    offsets_path = cfg.state_dir / "offsets.json"
    files = iter_transcript_files(cfg.transcripts_root)
    day_dir, chats_dir = ensure_output_paths(cfg)
    combined_path = day_dir / "combined.md"

    if cfg.verbose:
        print(f"Discovered {len(files)} transcript files")

    if not offsets_path.exists():
        offsets = bootstrap_offsets(cfg, files, offsets_path, cfg.bootstrap_mode)
        summary = {
            "time": datetime.now().isoformat(),
            "bootstrap_mode": cfg.bootstrap_mode,
            "file_count": len(files),
            "exported_entries": 0,
            "bootstrap_only": cfg.bootstrap_mode == "baseline",
        }
        write_run_summary(cfg, summary)
        if cfg.bootstrap_mode == "baseline":
            print("Initialized baseline offsets. No exports written on first run.")
            return EXIT_OK
    else:
        offsets = parse_offsets_payload(read_json_file(offsets_path, {}))

    exported_entries = 0
    parse_errors = 0

    for file_path in files:
        key = str(file_path)
        start_offset = offsets.get(key, 0)
        lines, next_offset = read_new_lines(file_path, start_offset)
        offsets[key] = next_offset
        if not lines:
            continue

        chat_id = file_path.stem
        per_chat_path = chats_dir / f"{chat_id}.md"
        combined_lines: List[str] = [f"\n## Chat {chat_id}\n"]

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
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        eprint(f"Unhandled runtime error: {exc}")
        raise SystemExit(EXIT_RUNTIME)
