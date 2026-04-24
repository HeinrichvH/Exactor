#!/usr/bin/env python3
"""
Memory store worker for Exactor.

Invoked by the memory.store hook on PreCompact/SessionEnd. Reads the Claude
Code hook payload from stdin, runs a Goose recipe (Mistral) to extract
memories from the conversation transcript, appends them to a JSONL log, and
writes the extracted memories JSON to stdout — where Exactor pipes it to the
configured adapter (hippocampus, Notion, etc.).

Fail-open throughout: any single step failing is logged to stderr and the
script exits 0 so Exactor doesn't treat it as a hook error.

Requirements:
  - goose on PATH
  - MISTRAL_API_KEY in env or ~/.vibe/.env
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
EXTRACTOR_RECIPE = SCRIPT_DIR / "goose-home" / "extractor" / "recipe.yaml"

# Goose turns budget for the extractor. Extraction is a pure read+synthesis
# task — 25 turns is generous for reading a transcript and outputting JSON.
_MAX_TURNS = 25


def _load_mistral_key() -> str | None:
    key = os.environ.get("MISTRAL_API_KEY")
    if key:
        return key
    dotenv = Path.home() / ".vibe" / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if line.startswith("MISTRAL_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "exactor"


def _last_json_object(text: str) -> dict | None:
    """Extract the last JSON object from combined goose stdout/stderr."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _run_extractor(transcript_path: str, hook_event: str, project_cwd: str) -> dict | None:
    argv = [
        "goose", "run",
        "--recipe", str(EXTRACTOR_RECIPE),
        "--no-session",
        "--max-turns", str(_MAX_TURNS),
        "--params", f"transcript_path={transcript_path}",
        "--params", f"hook_event={hook_event}",
        "--params", f"project_cwd={project_cwd}",
    ]

    env = os.environ.copy()
    key = _load_mistral_key()
    if key:
        env["MISTRAL_API_KEY"] = key

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        print("[store] goose extractor timed out after 90s", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("[store] goose not found on PATH", file=sys.stderr)
        return None

    combined = result.stdout + "\n" + result.stderr
    parsed = _last_json_object(combined)
    if parsed is None:
        print(
            f"[store] goose returned no JSON (exit {result.returncode})",
            file=sys.stderr,
        )
    return parsed


def _append_to_log(entry: dict) -> None:
    log_dir = _data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "memories.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(f"[store] invalid JSON payload: {e}", file=sys.stderr)
        return 0

    transcript_path = payload.get("transcript_path", "")
    session_id = payload.get("session_id", "")
    hook_event = payload.get("hook_event_name", "unknown")
    project_cwd = os.getcwd()

    if not transcript_path:
        print("[store] no transcript_path in payload — nothing to extract", file=sys.stderr)
        return 0

    if not Path(transcript_path).exists():
        print(f"[store] transcript not found: {transcript_path}", file=sys.stderr)
        return 0

    extracted = _run_extractor(transcript_path, hook_event, project_cwd)
    if not extracted:
        return 0

    memories = extracted.get("memories", [])
    if not memories:
        print("[store] no memories extracted from this session", file=sys.stderr)
        return 0

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "event": hook_event,
        "project_cwd": project_cwd,
        "session_summary": extracted.get("session_summary", ""),
        "memories": memories,
    }

    try:
        _append_to_log(entry)
    except OSError as e:
        print(f"[store] failed to write JSONL log: {e}", file=sys.stderr)
        # Still output to stdout so the adapter gets a chance to persist it.

    # Emit to stdout for the adapter (Exactor pipes this to adapter stdin).
    print(json.dumps(entry, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
