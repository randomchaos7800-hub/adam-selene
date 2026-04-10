"""Session replay logging — AgentOps pattern, native implementation.

Every significant event in an agent session is written as a JSONL line to
<memory_root>/sessions/YYYY-MM-DD_<session_id>.jsonl

Event types: session_start, user_message, model_call, tool_call, tool_result,
             model_response, extraction, error, session_end

Use replay_session.py to reconstruct any session as a readable trace.
"""

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from relay import config

logger = logging.getLogger(__name__)

SESSIONS_DIR = config.memory_root() / "sessions"
INDEX_FILE = SESSIONS_DIR / "index.json"

_local = threading.local()  # thread-local current session


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_file(session_id: str, started_at: str) -> Path:
    date = started_at[:10]
    return SESSIONS_DIR / f"{date}_{session_id}.jsonl"


# --- Session lifecycle ---

def start_session(user_id: str = None, interface: str = "unknown") -> str:
    """Open a new session. Returns session_id. Call at conversation start."""
    if user_id is None:
        user_id = config.owner_user_id()
    session_id = str(uuid.uuid4())[:12]
    started_at = _now_iso()
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    _local.session_id = session_id
    _local.started_at = started_at
    _local.tool_count = 0
    _local.error_count = 0
    _local.interfaces = [interface]

    _write(session_id, started_at, {
        "event": "session_start",
        "user_id": user_id,
        "interface": interface,
    })
    _index_upsert(session_id, started_at, user_id, interface)
    return session_id


def end_session(session_id: str = None, started_at: str = None) -> None:
    """Close a session."""
    sid = session_id or getattr(_local, "session_id", None)
    sat = started_at or getattr(_local, "started_at", None)
    if not sid or not sat:
        return
    _write(sid, sat, {
        "event": "session_end",
        "tool_count": getattr(_local, "tool_count", 0),
        "error_count": getattr(_local, "error_count", 0),
    })
    _index_upsert(sid, sat, status="ended")


def current_session() -> tuple[str | None, str | None]:
    """Return (session_id, started_at) for the current thread."""
    return getattr(_local, "session_id", None), getattr(_local, "started_at", None)


# --- Event emitters ---

def log_user_message(text: str, interface: str = "") -> None:
    sid, sat = current_session()
    if not sid:
        return
    _write(sid, sat, {
        "event": "user_message",
        "interface": interface,
        "length": len(text),
        "preview": text[:120],
    })


def log_model_call(model: str, messages_count: int, max_tokens: int) -> None:
    sid, sat = current_session()
    if not sid:
        return
    _write(sid, sat, {
        "event": "model_call",
        "model": model,
        "messages_count": messages_count,
        "max_tokens": max_tokens,
    })


def log_model_response(text: str, stop_reason: str, latency_ms: int = 0) -> None:
    sid, sat = current_session()
    if not sid:
        return
    _write(sid, sat, {
        "event": "model_response",
        "stop_reason": stop_reason,
        "length": len(text),
        "preview": text[:120],
        "latency_ms": latency_ms,
    })


def log_tool_call(tool_name: str, tool_input: dict) -> None:
    sid, sat = current_session()
    if not sid:
        return
    _local.tool_count = getattr(_local, "tool_count", 0) + 1
    REDACTED_TOOLS = {"vault_set", "vault_get", "store_credential", "read_credential"}
    REDACTED_KEYS = {"value", "data", "credentials", "api_key", "token", "secret", "password"}
    safe_input = {}
    for k, v in (tool_input or {}).items():
        if tool_name in REDACTED_TOOLS and k in REDACTED_KEYS:
            safe_input[k] = "[REDACTED]"
        else:
            safe_input[k] = str(v)[:200]
    _write(sid, sat, {
        "event": "tool_call",
        "tool": tool_name,
        "input": safe_input,
    })


def log_tool_result(tool_name: str, result_preview: str, success: bool = True) -> None:
    sid, sat = current_session()
    if not sid:
        return
    _write(sid, sat, {
        "event": "tool_result",
        "tool": tool_name,
        "success": success,
        "preview": str(result_preview)[:200],
    })


def log_shell_exec(command: str, blocked: bool, exit_code: int | None = None) -> None:
    """Always log shell executions for audit."""
    sid, sat = current_session()
    if not sid:
        # Log to a standalone audit file even outside a session
        _write_audit({"event": "shell_exec", "command": command[:300], "blocked": blocked, "exit_code": exit_code})
        return
    _write(sid, sat, {
        "event": "shell_exec",
        "command": command[:300],
        "blocked": blocked,
        "exit_code": exit_code,
    })


def log_error(error: str, context: str = "") -> None:
    sid, sat = current_session()
    if not sid:
        return
    _local.error_count = getattr(_local, "error_count", 0) + 1
    _write(sid, sat, {
        "event": "error",
        "error": str(error)[:400],
        "context": context,
    })


def log_extraction(facts_saved: int, entities_created: int) -> None:
    sid, sat = current_session()
    if not sid:
        return
    _write(sid, sat, {
        "event": "extraction",
        "facts_saved": facts_saved,
        "entities_created": entities_created,
    })


# --- Internal writers ---

def _write(session_id: str, started_at: str, data: dict) -> None:
    filepath = _session_file(session_id, started_at)
    line = json.dumps({"ts": _now_iso(), "session_id": session_id, **data})
    try:
        with open(filepath, "a") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.debug(f"session_log write failed: {e}")


def _write_audit(data: dict) -> None:
    audit_file = SESSIONS_DIR / "shell_audit.jsonl"
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": _now_iso(), **data})
    try:
        with open(audit_file, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _index_upsert(session_id: str, started_at: str, user_id: str = "", interface: str = "", status: str = "active") -> None:
    try:
        index = {}
        if INDEX_FILE.exists():
            index = json.loads(INDEX_FILE.read_text())
        entry = index.get(session_id, {
            "session_id": session_id,
            "started_at": started_at,
            "user_id": user_id,
            "interface": interface,
            "status": "active",
        })
        entry["status"] = status
        if status == "ended":
            entry["ended_at"] = _now_iso()
        index[session_id] = entry
        # Keep last 200 sessions
        if len(index) > 200:
            oldest = sorted(index.values(), key=lambda x: x["started_at"])[:len(index) - 200]
            for e in oldest:
                index.pop(e["session_id"], None)
        INDEX_FILE.write_text(json.dumps(index, indent=2))
    except Exception as e:
        logger.debug(f"session index update failed: {e}")
