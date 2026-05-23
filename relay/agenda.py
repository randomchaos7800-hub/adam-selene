"""Agenda — persistent topic queue for autonomous research.

Two item sources:
  - conversation: added via add_to_agenda() tool during chat
  - self: generated during heartbeat when agenda is empty

Items expire after 7 days unless refreshed.

Storage: {memory_root}/agenda.json
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from relay import config
from relay.fs_utils import read_json_file, update_json_file, write_json_file

logger = logging.getLogger(__name__)

EXPIRY_DAYS = 7


def _agenda_file() -> Path:
    return config.memory_root() / "agenda.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=EXPIRY_DAYS)).isoformat()


def _load() -> list[dict]:
    f = _agenda_file()
    try:
        return read_json_file(f, [])
    except Exception as e:
        logger.error(f"agenda load error: {e}")
        return []


def _save(items: list[dict]) -> None:
    f = _agenda_file()
    write_json_file(f, items)


def _prune_expired(items: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    kept = []
    for item in items:
        try:
            exp = datetime.fromisoformat(item.get("expires_at", ""))
            if exp > now:
                kept.append(item)
            else:
                logger.info(f"Agenda: expired '{item['topic']}'")
        except Exception:
            kept.append(item)  # malformed expiry -> keep
    return kept


class Agenda:
    """Simple CRUD interface for the research agenda."""

    def add(self, topic: str, context: str = "", priority: int = 2, source: str = "conversation") -> dict:
        """Add a new item. priority: 1=high, 2=medium, 3=low."""
        def _add(items: list[dict]) -> dict:
            items[:] = _prune_expired(items)
            topic_lower = topic.lower()
            for item in items:
                if item["status"] == "pending" and topic_lower in item["topic"].lower():
                    logger.info(f"Agenda: skipping duplicate '{topic}'")
                    return {"added": False, "reason": "similar item already pending", "existing": item}

            new_item = {
                "id": str(uuid.uuid4())[:8],
                "topic": topic,
                "context": context,
                "source": source,
                "priority": priority,
                "status": "pending",
                "created_at": _now(),
                "expires_at": _expiry(),
                "researched_at": None,
            }
            items.append(new_item)
            return {"added": True, "item": new_item}

        result = update_json_file(_agenda_file(), [], _add)
        if result.get("added"):
            logger.info(f"Agenda: added [{source}] '{topic}' (priority {priority})")
        return result

    def next(self) -> dict | None:
        """Return the highest-priority pending item. Conversation items first, then self-generated."""
        def _next(items: list[dict]) -> dict | None:
            items[:] = _prune_expired(items)
            pending = [i for i in items if i["status"] == "pending"]
            if not pending:
                return None

            def sort_key(item):
                source_order = 0 if item["source"] == "conversation" else 1
                return (source_order, item["priority"], item["created_at"])

            pending.sort(key=sort_key)
            return pending[0]

        return update_json_file(_agenda_file(), [], _next)

    def mark_researched(self, item_id: str) -> None:
        def _mark(items: list[dict]) -> None:
            for item in items:
                if item["id"] == item_id:
                    item["status"] = "researched"
                    item["researched_at"] = _now()
                    break

        update_json_file(_agenda_file(), [], _mark)
        logger.info(f"Agenda: marked {item_id} as researched")

    def dismiss(self, item_id: str) -> None:
        def _dismiss(items: list[dict]) -> None:
            for item in items:
                if item["id"] == item_id:
                    item["status"] = "dismissed"
                    break

        update_json_file(_agenda_file(), [], _dismiss)

    def list(self, status: str | None = None) -> list[dict]:
        def _list(items: list[dict]) -> list[dict]:
            items[:] = _prune_expired(items)
            if status:
                return [i for i in items if i["status"] == status]
            return list(items)

        return update_json_file(_agenda_file(), [], _list)

    def pending_count(self) -> int:
        def _count(items: list[dict]) -> int:
            items[:] = _prune_expired(items)
            return sum(1 for i in items if i["status"] == "pending")

        return update_json_file(_agenda_file(), [], _count)


# Module-level singleton
_agenda = Agenda()


def get_agenda() -> Agenda:
    return _agenda
