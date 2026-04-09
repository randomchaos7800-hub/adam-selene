"""Session storage for the agent.

Persists conversations to SQLite so the agent never loses context
across process restarts, idle timeouts, or reconnections.

Three time horizons:
- Immediate: today's messages loaded into context
- Recent: queryable via review_own_conversations
- Long-term: extracted facts in the knowledge graph
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from relay import config

logger = logging.getLogger(__name__)

# Sessions DB lives alongside memory
DEFAULT_DB_PATH = config.memory_root() / "sessions.db"


class SessionStore:
    """SQLite-backed conversation persistence."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    session_date TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_user_date
                ON messages(user_id, session_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp
                ON messages(timestamp)
            """)

    def _connect(self) -> sqlite3.Connection:
        """Get a database connection."""
        return sqlite3.connect(str(self.db_path))

    def save_message(self, user_id: str, role: str, content: str) -> None:
        """Save a single message."""
        # Skip empty messages
        if not content or not content.strip():
            logger.warning(f"Attempted to save empty message for {user_id}, skipping")
            return

        now = datetime.now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (user_id, role, content, timestamp, session_date) VALUES (?, ?, ?, ?, ?)",
                (user_id, role, content, now.isoformat(), now.strftime("%Y-%m-%d"))
            )

    def save_exchange(self, user_id: str, user_message: str, assistant_response: str) -> None:
        """Save a user message + assistant response pair."""
        self.save_message(user_id, "user", user_message)
        self.save_message(user_id, "assistant", assistant_response)

    def get_today_messages(self, user_id: str) -> list[dict]:
        """Get all messages from today for a user."""
        today = datetime.now().strftime("%Y-%m-%d")
        return self._get_messages_for_date(user_id, today)

    # ~75K tokens at 4 chars/token — leaves 25% of 128K for output
    _CHAR_BUDGET = 300_000

    def get_session_snapshot(self, user_id: str, max_messages: int = 500) -> list[dict]:
        """Get recent messages for context injection.

        Loads all of today's messages up to the char budget.
        When budget is exceeded, uses stratified sampling:
        first 4 (session opener) + sampled middle + last 40 (recent).
        If today has no messages, loads last session's tail.
        """
        today_msgs = self.get_today_messages(user_id)

        if today_msgs:
            msgs = today_msgs[-max_messages:] if len(today_msgs) > max_messages else today_msgs
            total_chars = sum(len(m["content"]) for m in msgs)

            if total_chars <= self._CHAR_BUDGET:
                return msgs

            # Over budget — stratified sampling
            first = msgs[:4]
            last = msgs[-40:]
            middle = msgs[4:-40]

            if not middle:
                return first + last

            # Sample middle: keep every Nth to fill remaining budget
            remaining_budget = self._CHAR_BUDGET - sum(len(m["content"]) for m in first + last)
            sampled, chars = [], 0
            step = max(1, len(middle) // 80)  # aim for ~80 middle messages
            for m in middle[::step]:
                if chars + len(m["content"]) > remaining_budget:
                    break
                sampled.append(m)
                chars += len(m["content"])

            dropped = len(middle) - len(sampled)
            note = {
                "role": "user",
                "content": f"[Context note: {dropped} messages from earlier in this conversation were omitted to fit the context window.]",
                "timestamp": middle[0]["timestamp"]
            }
            return first + [note] + sampled + last

        # No messages today — load tail of most recent session
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT role, content, timestamp FROM messages
                   WHERE user_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (user_id, max_messages)
            ).fetchall()

        if not rows:
            return []

        return [
            {"role": r[0], "content": r[1], "timestamp": r[2]}
            for r in reversed(rows)
        ]

    def get_today_message_count(self, user_id: str) -> int:
        """Count of messages exchanged today. Used for incremental extraction trigger."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id = ? AND session_date = ?",
                (user_id, today)
            ).fetchone()
        return row[0] if row else 0

    def get_conversations_since(self, user_id: str, hours: int = 24) -> list[dict]:
        """Get all conversations from the last N hours.

        Used by heartbeat for reflection.
        """
        since = (datetime.now() - timedelta(hours=hours)).isoformat()

        with self._connect() as conn:
            rows = conn.execute(
                """SELECT role, content, timestamp FROM messages
                   WHERE user_id = ? AND timestamp > ?
                   ORDER BY timestamp ASC""",
                (user_id, since)
            ).fetchall()

        return [
            {"role": r[0], "content": r[1], "timestamp": r[2]}
            for r in rows
        ]

    def get_conversation_text(self, user_id: str, hours: int = 24) -> str:
        """Get conversations as formatted text for extraction/review."""
        messages = self.get_conversations_since(user_id, hours)

        lines = []
        for msg in messages:
            role = config.owner_name().upper() if msg["role"] == "user" else config.agent_name().upper()
            lines.append(f"{role}: {msg['content']}")

        return "\n\n".join(lines)

    def get_last_message_time(self, user_id: str) -> Optional[datetime]:
        """Get timestamp of the last message from a user."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT timestamp FROM messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
                (user_id,)
            ).fetchone()

        if row:
            return datetime.fromisoformat(row[0])
        return None

    def _get_messages_for_date(self, user_id: str, date: str) -> list[dict]:
        """Get all messages for a specific date."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT role, content, timestamp FROM messages
                   WHERE user_id = ? AND session_date = ?
                   ORDER BY timestamp ASC""",
                (user_id, date)
            ).fetchall()

        return [
            {"role": r[0], "content": r[1], "timestamp": r[2]}
            for r in rows
        ]

    def message_count(self, user_id: str) -> int:
        """Total messages stored for a user."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        return row[0] if row else 0

    def get_most_recent_user(self) -> Optional[str]:
        """Get the user_id with the most recent message."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM messages ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def list_sessions(self) -> list[str]:
        """Get all unique user_ids that have messages."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM messages ORDER BY user_id"
            ).fetchall()
        return [row[0] for row in rows]
