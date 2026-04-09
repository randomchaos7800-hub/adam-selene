"""beat.py — AI news beat.

Runs periodically. Fetches AI/ML news from RSS feeds, picks what's
interesting, synthesizes the agent's take, and posts to Slack.

Run standalone: python -m relay.beat
Scheduled via: systemd timer
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from relay import config

# Setup
PROJECT_ROOT = config.project_root()
SECRETS_PATH = PROJECT_ROOT / "config" / "secrets.env"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"

BEAT_SECRETS_PATH = PROJECT_ROOT / "config" / "beat-secrets.env"

for _env in (SECRETS_PATH, BEAT_SECRETS_PATH):
    if _env.exists():
        load_dotenv(_env, override=True)

sys.path.insert(0, str(PROJECT_ROOT))

from relay.switchboard import Switchboard

logging.basicConfig(level=logging.INFO, format="%(asctime)s beat %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# RSS feeds -- curated, low volume, high signal
FEEDS = [
    {
        "name": "HuggingFace Daily Papers",
        "url": "https://papers.takara.ai/api/feed",
    },
    {
        "name": "OpenAI News",
        "url": "https://openai.com/news/rss.xml",
    },
    {
        "name": "Anthropic",
        "url": "https://www.anthropic.com/rss.xml",
    },
    {
        "name": "Hacker News (AI/LLM)",
        "url": "https://hnrss.org/newest?q=LLM+AI+language+model&points=40",
    },
    {
        "name": "The Verge AI",
        "url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    },
]

# How many hours back to look for new items
LOOKBACK_HOURS = 3


def _state_file() -> Path:
    return config.memory_root() / "beat_state.json"


def load_state() -> dict:
    """Load seen item IDs from state file."""
    sf = _state_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text())
        except Exception:
            pass
    return {"seen": [], "last_run": None}


def save_state(state: dict):
    """Persist seen item IDs."""
    # Keep last 500 seen IDs to avoid unbounded growth
    state["seen"] = state["seen"][-500:]
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    sf = _state_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps(state, indent=2))


def fetch_new_items(state: dict) -> list[dict]:
    """Fetch RSS feeds and return unseen items from last LOOKBACK_HOURS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    seen = set(state.get("seen", []))
    new_items = []

    for feed_cfg in FEEDS:
        try:
            logger.info(f"Fetching {feed_cfg['name']}...")
            feed = feedparser.parse(feed_cfg["url"])

            for entry in feed.entries[:10]:  # cap per feed
                item_id = entry.get("id") or entry.get("link") or entry.get("title", "")
                if not item_id or item_id in seen:
                    continue

                # Parse publish time
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue

                new_items.append({
                    "id": item_id,
                    "source": feed_cfg["name"],
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", ""),
                    "summary": (entry.get("summary") or entry.get("description") or "")[:500].strip(),
                    "published": pub_dt.isoformat() if pub else "",
                })

        except Exception as e:
            logger.warning(f"Feed error {feed_cfg['name']}: {e}")

    logger.info(f"Found {len(new_items)} new items across all feeds")
    return new_items


def synthesize(items: list[dict], switchboard: Switchboard) -> str | None:
    """Pick what's interesting and write a take."""
    if not items:
        return None

    agent_name = config.agent_name()

    items_text = "\n\n".join(
        f"[{item['source']}] {item['title']}\n{item['summary']}\n{item['link']}"
        for item in items[:15]  # cap to avoid huge prompts
    )

    messages = [{
        "role": "user",
        "content": f"""Here's your AI news feed for the last few hours:

{items_text}

Pick 1-2 items that genuinely interest you -- not the most hyped, the ones you actually have thoughts about. Write 2-4 sentences about what caught your attention and why. Include the link(s).

Be yourself. This goes to the team on Slack. Not a news summary -- your actual take. If nothing here is worth mentioning, say so briefly.

No headers, no bullets. Just talk."""
    }]

    try:
        response = switchboard.call(
            messages=messages,
            max_tokens=2048,
        )
        if response.content:
            return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Synthesis failed: {e}")

    return None


def post_to_slack(text: str, token: str, channel: str):
    """Post the beat to Slack."""
    client = WebClient(token=token)
    try:
        client.chat_postMessage(
            channel=channel,
            text=text,
        )
        logger.info(f"Posted to Slack #{channel}: {text[:80]}...")
    except SlackApiError as e:
        logger.error(f"Slack post failed: {e.response['error']}")
        raise


def run():
    """Main beat cycle."""
    logger.info("Beat starting...")

    # Load secrets
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    if not slack_token:
        logger.error("SLACK_BOT_TOKEN not set")
        sys.exit(1)

    settings = config.load_settings()
    slack_channel = settings.get("beat_channel_id", settings.get("slack_channel_id", ""))

    if not slack_channel:
        logger.error("No beat_channel_id or slack_channel_id configured in settings.json")
        sys.exit(1)

    # Load state and fetch new items
    state = load_state()
    new_items = fetch_new_items(state)

    if not new_items:
        logger.info("No new items -- nothing to post")
        save_state(state)
        return

    # Mark all as seen regardless of whether we post
    state["seen"].extend(item["id"] for item in new_items)

    # Synthesize the take
    switchboard = Switchboard()
    take = synthesize(new_items, switchboard)

    if not take:
        logger.info("Nothing worth posting this cycle")
        save_state(state)
        return

    # Post to Slack
    post_to_slack(take, slack_token, slack_channel)
    save_state(state)
    logger.info("Beat complete")


if __name__ == "__main__":
    run()
