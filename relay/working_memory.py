"""Working Memory — active investigation thread.

Tracks a single in-progress research thread across heartbeat cycles.
Each cycle advances the thread one step: query -> findings -> next_step.
When the thread reaches its goal (or max cycles), it's synthesized and archived.

Storage: {memory_root}/working_memory.json
"""

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from relay import config

logger = logging.getLogger(__name__)

MAX_CYCLES = 6  # max heartbeat cycles before forcing synthesis + close


def _working_memory_file() -> Path:
    return config.memory_root() / "working_memory.json"


def _failure_log_file() -> Path:
    return config.memory_root() / "failure_log.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    f = _working_memory_file()
    if not f.exists():
        return {"active_thread": None, "archived_threads": []}
    try:
        return json.loads(f.read_text())
    except Exception as e:
        logger.error(f"working_memory load error: {e}")
        return {"active_thread": None, "archived_threads": []}


def _save(data: dict) -> None:
    f = _working_memory_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2))


class WorkingThread:
    """A single multi-cycle investigation thread."""

    def __init__(self, data: dict):
        self._data = data

    @classmethod
    def start(cls, goal: str, title: str = "", first_query: str = "") -> "WorkingThread":
        data = {
            "id": str(uuid.uuid4())[:8],
            "title": title or goal[:80],
            "goal": goal,
            "started_at": _now(),
            "steps": [],
            "next_step": first_query or goal,
            "cycle_count": 0,
            "status": "active",
        }
        wm = _load()
        wm["active_thread"] = data
        _save(wm)
        logger.info(f"WorkingThread started: '{data['title']}'")
        return cls(data)

    @classmethod
    def load_active(cls) -> "WorkingThread | None":
        wm = _load()
        thread = wm.get("active_thread")
        if not thread or thread.get("status") != "active":
            return None

        # Auto-abandon stale threads (no heartbeat for >2h)
        last = thread.get("last_heartbeat") or thread.get("started_at", "")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - last_dt
                if age > timedelta(hours=2):
                    t = cls(thread)
                    t.abandon("stale: no heartbeat for >2h (likely process restart)")
                    logger.warning(f"Auto-abandoned stale WorkingThread '{thread.get('title', '?')}' (age {age})")
                    return None
            except Exception as e:
                logger.debug(f"Stale thread check failed: {e}")

        return cls(thread)

    @property
    def id(self) -> str:
        return self._data["id"]

    @property
    def title(self) -> str:
        return self._data["title"]

    @property
    def goal(self) -> str:
        return self._data["goal"]

    @property
    def next_step(self) -> str:
        return self._data["next_step"]

    @property
    def cycle_count(self) -> int:
        return self._data["cycle_count"]

    @property
    def steps(self) -> list[dict]:
        return self._data["steps"]

    def is_exhausted(self) -> bool:
        return self._data["cycle_count"] >= MAX_CYCLES

    def append_step(self, query: str, findings: str, next_step: str) -> None:
        self._data["steps"].append({
            "cycle": self._data["cycle_count"] + 1,
            "query": query,
            "findings": findings[:1200],  # cap stored per step
            "timestamp": _now(),
        })
        self._data["next_step"] = next_step
        self._data["cycle_count"] += 1
        self._data["last_heartbeat"] = _now()
        self._flush()
        logger.info(f"WorkingThread '{self.title}' -- cycle {self._data['cycle_count']}, next: '{next_step[:60]}'")

    def complete(self) -> None:
        self._data["status"] = "complete"
        self._data["completed_at"] = _now()
        self._flush()
        self._archive()
        logger.info(f"WorkingThread '{self.title}' completed after {self.cycle_count} cycles")

    def abandon(self, reason: str = "") -> None:
        self._data["status"] = "abandoned"
        self._data["abandoned_reason"] = reason
        self._data["completed_at"] = _now()
        self._flush()
        self._archive()
        logger.info(f"WorkingThread '{self.title}' abandoned: {reason}")

    def summary_for_prompt(self) -> str:
        """Compact summary of what's been found so far -- for feeding into next query."""
        if not self._data["steps"]:
            return f"Goal: {self.goal}\n\nNo steps completed yet."
        lines = [f"Goal: {self.goal}\n"]
        for step in self._data["steps"][-3:]:  # last 3 steps only
            lines.append(f"Cycle {step['cycle']} query: {step['query']}")
            lines.append(f"Found: {step['findings'][:300]}\n")
        return "\n".join(lines)

    def full_synthesis_text(self) -> str:
        """Full thread content for LIGHTHOUSE archival."""
        lines = [f"**Goal:** {self.goal}\n", f"**Cycles:** {self.cycle_count}\n\n---\n"]
        for step in self._data["steps"]:
            lines.append(f"### Cycle {step['cycle']}: {step['query']}\n")
            lines.append(step["findings"] + "\n")
        return "\n".join(lines)

    def _flush(self) -> None:
        wm = _load()
        wm["active_thread"] = self._data
        _save(wm)

    def _archive(self) -> None:
        wm = _load()
        wm["active_thread"] = None
        archived = wm.get("archived_threads", [])
        archived.insert(0, self._data)
        wm["archived_threads"] = archived[:20]  # keep last 20
        _save(wm)


def get_active_thread() -> WorkingThread | None:
    return WorkingThread.load_active()


def start_thread(goal: str, title: str = "", first_query: str = "") -> WorkingThread:
    return WorkingThread.start(goal=goal, title=title, first_query=first_query)


def read_status() -> dict:
    """Return current working memory state for tool output."""
    wm = _load()
    thread = wm.get("active_thread")
    archived = wm.get("archived_threads", [])

    if not thread or thread.get("status") != "active":
        return {
            "active_thread": None,
            "recent_completed": [
                {
                    "title": t["title"],
                    "goal": t["goal"],
                    "cycles": t["cycle_count"],
                    "status": t["status"],
                    "completed_at": t.get("completed_at", ""),
                }
                for t in archived[:3]
            ],
        }

    return {
        "active_thread": {
            "id": thread["id"],
            "title": thread["title"],
            "goal": thread["goal"],
            "cycle_count": thread["cycle_count"],
            "max_cycles": MAX_CYCLES,
            "next_step": thread["next_step"],
            "started_at": thread["started_at"],
            "steps_summary": [
                {"cycle": s["cycle"], "query": s["query"][:80]}
                for s in thread["steps"]
            ],
        },
        "recent_completed": [
            {"title": t["title"], "cycles": t["cycle_count"], "status": t["status"]}
            for t in archived[:3]
        ],
    }


# -- Failure Logging --

def _load_failures() -> list:
    f = _failure_log_file()
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text())
    except Exception as e:
        logger.error(f"failure_log load error: {e}")
        return []


def _save_failures(failures: list) -> None:
    f = _failure_log_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(failures, indent=2))


def log_failure(context: str, error: str, recovery: str = "") -> None:
    """Log a tool or investigation failure so it's visible on next turn."""
    failures = _load_failures()
    entry = {
        "timestamp": _now(),
        "context": context,  # what was happening
        "error": error,      # what went wrong
        "recovery": recovery, # what I'm doing about it
    }
    failures.append(entry)
    failures = failures[-20:]  # keep last 20
    _save_failures(failures)
    logger.warning(f"Failure logged: {context} -- {error}")


def read_failures() -> list:
    """Return recent failures for pre-flight check."""
    return _load_failures()


def clear_failures() -> None:
    """Clear the failure log after review."""
    _save_failures([])
