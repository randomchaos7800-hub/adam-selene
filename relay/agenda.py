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
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text())
    except Exception as e:
        logger.error(f"agenda load error: {e}")
        return []


def _save(items: list[dict]) -> None:
    f = _agenda_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(items, indent=2))


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
        items = _prune_expired(_load())

        # Deduplicate: don't add if similar topic already pending
        topic_lower = topic.lower()
        for item in items:
            if item["status"] == "pending" and topic_lower in item["topic"].lower():
                logger.info(f"Agenda: skipping duplicate '{topic}'")
                return {"added": False, "reason": "similar item already pending", "existing": item}

        new_item = {
            "id": str(uuid.uuid4())[:8],
            "topic": topic,
            "context": context,
            "source": source,  # conversation | self
            "priority": priority,
            "status": "pending",
            "created_at": _now(),
            "expires_at": _expiry(),
            "researched_at": None,
        }
        items.append(new_item)
        _save(items)
        logger.info(f"Agenda: added [{source}] '{topic}' (priority {priority})")
        return {"added": True, "item": new_item}

    def next(self) -> dict | None:
        """Return the highest-priority pending item. Conversation items first, then self-generated."""
        items = _prune_expired(_load())
        _save(items)

        pending = [i for i in items if i["status"] == "pending"]
        if not pending:
            return None

        # Sort: conversation before self, then by priority (1=highest), then oldest first
        def sort_key(item):
            source_order = 0 if item["source"] == "conversation" else 1
            return (source_order, item["priority"], item["created_at"])

        pending.sort(key=sort_key)
        return pending[0]

    def mark_researched(self, item_id: str) -> None:
        items = _load()
        for item in items:
            if item["id"] == item_id:
                item["status"] = "researched"
                item["researched_at"] = _now()
                break
        _save(items)
        logger.info(f"Agenda: marked {item_id} as researched")

    def dismiss(self, item_id: str) -> None:
        items = _load()
        for item in items:
            if item["id"] == item_id:
                item["status"] = "dismissed"
                break
        _save(items)

    def list(self, status: str | None = None) -> list[dict]:
        items = _prune_expired(_load())
        _save(items)
        if status:
            return [i for i in items if i["status"] == status]
        return items

    def pending_count(self) -> int:
        items = _prune_expired(_load())
        return sum(1 for i in items if i["status"] == "pending")


# Module-level singleton
_agenda = Agenda()


def get_agenda() -> Agenda:
    return _agenda
