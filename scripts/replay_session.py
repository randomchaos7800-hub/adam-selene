#!/usr/bin/env python3
"""Replay a session as a readable trace.

Usage:
  python3 scripts/replay_session.py                    # list recent sessions
  python3 scripts/replay_session.py <session_id>       # replay specific session
  python3 scripts/replay_session.py --last             # replay most recent session
  python3 scripts/replay_session.py --errors           # show only error events
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Determine sessions directory from settings or environment
SMARTAGENT_ROOT = Path(__file__).parent.parent
_settings_path = SMARTAGENT_ROOT / "config" / "settings.json"
_settings = json.loads(_settings_path.read_text()) if _settings_path.exists() else {}
_memory_dir = os.environ.get(
    "SMARTAGENT_MEMORY_DIR",
    _settings.get("memory_dir", str(Path.home() / "smartagent-memory"))
)
SESSIONS_DIR = Path(_memory_dir) / "sessions"
INDEX_FILE = SESSIONS_DIR / "index.json"

EVENT_COLORS = {
    "session_start": "\033[32m",   # green
    "session_end":   "\033[32m",
    "user_message":  "\033[36m",   # cyan
    "model_call":    "\033[33m",   # yellow
    "model_response":"\033[33m",
    "tool_call":     "\033[35m",   # magenta
    "tool_result":   "\033[35m",
    "shell_exec":    "\033[31m",   # red
    "error":         "\033[31m",
    "extraction":    "\033[34m",   # blue
}
RESET = "\033[0m"


def list_sessions(limit: int = 20) -> None:
    if not INDEX_FILE.exists():
        print("No sessions recorded yet.")
        return
    index = json.loads(INDEX_FILE.read_text())
    sessions = sorted(index.values(), key=lambda x: x["started_at"], reverse=True)[:limit]
    print(f"\n{'SESSION ID':<14} {'STARTED':<26} {'INTERFACE':<12} {'STATUS'}")
    print("-" * 70)
    for s in sessions:
        ts = s["started_at"][:19].replace("T", " ")
        print(f"{s['session_id']:<14} {ts:<26} {s.get('interface', '?'):<12} {s['status']}")
    print()


def find_session_file(session_id: str) -> Path | None:
    for f in SESSIONS_DIR.glob(f"*_{session_id}.jsonl"):
        return f
    return None


def replay(session_id: str, errors_only: bool = False) -> None:
    filepath = find_session_file(session_id)
    if not filepath:
        print(f"Session file not found for: {session_id}")
        return

    events = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not events:
        print(f"No events in session {session_id}")
        return

    print(f"\n{'='*70}")
    print(f"Session: {session_id}  ({len(events)} events)")
    print(f"{'='*70}\n")

    for ev in events:
        etype = ev.get("event", "unknown")
        if errors_only and etype not in ("error", "shell_exec"):
            continue

        ts = ev.get("ts", "")[:19].replace("T", " ")
        color = EVENT_COLORS.get(etype, "")

        if etype == "session_start":
            print(f"{color}[{ts}] SESSION START  interface={ev.get('interface')} user={ev.get('user_id')}{RESET}")

        elif etype == "user_message":
            print(f"{color}[{ts}] USER  ({ev.get('length', 0)} chars){RESET}")
            print(f"         {ev.get('preview', '')[:100]}")

        elif etype == "model_call":
            print(f"{color}[{ts}] MODEL CALL  model={ev.get('model')} msgs={ev.get('messages_count')} max_tok={ev.get('max_tokens')}{RESET}")

        elif etype == "model_response":
            latency = ev.get("latency_ms", 0)
            lat_str = f" {latency}ms" if latency else ""
            print(f"{color}[{ts}] MODEL RESPONSE  stop={ev.get('stop_reason')}{lat_str} ({ev.get('length', 0)} chars){RESET}")
            print(f"         {ev.get('preview', '')[:100]}")

        elif etype == "tool_call":
            inp = ev.get("input", {})
            inp_str = ", ".join(f"{k}={v!r}" for k, v in list(inp.items())[:3])
            print(f"{color}[{ts}] TOOL CALL  {ev.get('tool')}({inp_str}){RESET}")

        elif etype == "tool_result":
            ok = "+" if ev.get("success") else "x"
            print(f"{color}[{ts}] TOOL RESULT {ok} {ev.get('tool')}  {ev.get('preview', '')[:80]}{RESET}")

        elif etype == "shell_exec":
            blocked = "BLOCKED" if ev.get("blocked") else f"exit={ev.get('exit_code', '?')}"
            color = "\033[31m" if ev.get("blocked") else "\033[33m"
            print(f"{color}[{ts}] SHELL [{blocked}]  {ev.get('command', '')[:100]}{RESET}")

        elif etype == "error":
            print(f"{color}[{ts}] ERROR  {ev.get('error', '')[:120]}{RESET}")
            if ev.get("context"):
                print(f"         context: {ev.get('context', '')[:80]}")

        elif etype == "extraction":
            print(f"{color}[{ts}] EXTRACTION  facts={ev.get('facts_saved')} entities={ev.get('entities_created')}{RESET}")

        elif etype == "session_end":
            print(f"{color}[{ts}] SESSION END  tools={ev.get('tool_count')} errors={ev.get('error_count')}{RESET}")

    print()


def main() -> None:
    args = sys.argv[1:]

    if not args:
        list_sessions()
        return

    if "--errors" in args:
        args = [a for a in args if a != "--errors"]
        errors_only = True
    else:
        errors_only = False

    if "--last" in args or not args:
        if not INDEX_FILE.exists():
            print("No sessions yet.")
            return
        index = json.loads(INDEX_FILE.read_text())
        if not index:
            print("No sessions yet.")
            return
        latest = sorted(index.values(), key=lambda x: x["started_at"], reverse=True)[0]
        replay(latest["session_id"], errors_only=errors_only)
        return

    replay(args[0], errors_only=errors_only)


if __name__ == "__main__":
    main()
