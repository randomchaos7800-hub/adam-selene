"""Weekly synthesis -- rewrites summaries from accumulated facts.

Run manually or via cron on a schedule (e.g., Sundays).
V2: updated schema support, uses cost-efficient models.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from openai import OpenAI

from memory import storage

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"
SYNTHESIS_PROMPT_PATH = Path(__file__).parent.parent / "config" / "synthesis.md"


def load_settings() -> dict:
    """Load settings from config file."""
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text())
    return {}


def load_synthesis_prompt() -> str:
    """Load the synthesis prompt template."""
    if SYNTHESIS_PROMPT_PATH.exists():
        return SYNTHESIS_PROMPT_PATH.read_text()

    return """Rewrite this entity summary based on current active facts.

Entity: {name}
Category: {category}

Current facts:
{facts}

Previous summary:
{previous_summary}

Write a new summary that:
- Can be read in 30 seconds
- Captures current state, not history
- Highlights what's most relevant RIGHT NOW
- Uses natural language, not bullet dumps
- Is concise but complete

Output ONLY the summary text, no preamble."""


class Synthesizer:
    """Rewrites entity summaries from accumulated facts."""

    def __init__(self):
        settings = load_settings()
        or_cfg = settings.get("openrouter", {})
        self.model = or_cfg.get("model", "google/gemini-flash-2.0")
        base_url = or_cfg.get("base_url", "https://openrouter.ai/v1")
        self.archive_after_days = settings.get("synthesis", {}).get("archive_after_days", 90)

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not set")
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def synthesize_entity(self, entity_name: str) -> Optional[str]:
        """Rewrite summary for a single entity."""
        entity_data = storage.read_entity(entity_name)
        if not entity_data:
            logger.warning(f"Entity not found: {entity_name}")
            return None

        facts = entity_data.get("recent_facts", [])
        if not facts:
            logger.info(f"No facts for {entity_name}, skipping")
            return None

        facts_text = "\n".join([
            f"- [{f.get('category', f.get('type', ''))}] "
            f"{f.get('fact', f.get('content', ''))} "
            f"({f.get('timestamp', f.get('extracted', ''))[:10]})"
            for f in facts
        ])

        prompt_template = load_synthesis_prompt()
        prompt = prompt_template.format(
            name=entity_name,
            category=entity_data["category"],
            facts=facts_text,
            previous_summary=entity_data.get("summary", "(No previous summary)")
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            new_summary = response.choices[0].message.content.strip()

            word_count = len(new_summary.split())
            if word_count > 150:
                logger.warning(f"Summary for {entity_name} too long ({word_count} words), truncating")
                words = new_summary.split()[:150]
                new_summary = " ".join(words) + "..."

            return new_summary

        except Exception as e:
            logger.error(f"Synthesis error for {entity_name}: {e}")
            return None

    def update_entity_summary(self, entity_name: str, new_summary: str) -> bool:
        """Update an entity's summary file."""
        entities = storage.load_entities()
        if entity_name not in entities:
            return False

        entity_path = storage.get_memory_path() / entities[entity_name]["path"]
        summary_file = entity_path / "summary.md"

        full_summary = f"# {entity_name.replace('_', ' ').title()}\n\n{new_summary}\n"
        summary_file.write_text(full_summary)

        logger.info(f"Updated summary for {entity_name}")
        return True

    def archive_old_facts(self, entity_name: str) -> int:
        """Mark facts older than threshold as historical."""
        entities = storage.load_entities()
        if entity_name not in entities:
            return 0

        entity_path = storage.get_memory_path() / entities[entity_name]["path"]
        facts_file = entity_path / "facts.json"

        if not facts_file.exists():
            return 0

        facts_data = json.loads(facts_file.read_text())
        cutoff = datetime.now() - timedelta(days=self.archive_after_days)
        archived = 0

        for fact in facts_data.get("facts", []):
            if not fact.get("active", True):
                continue
            if fact.get("status", "active") != "active":
                continue

            timestamp = fact.get("timestamp", fact.get("extracted", ""))
            if timestamp:
                try:
                    fact_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if fact_date.replace(tzinfo=None) < cutoff:
                        fact["active"] = False
                        fact["context"] = "historical"
                        fact["archived"] = datetime.now().isoformat()
                        archived += 1
                except ValueError:
                    pass

        if archived > 0:
            facts_file.write_text(json.dumps(facts_data, indent=2))
            logger.info(f"Archived {archived} facts for {entity_name}")

        return archived


def run() -> dict:
    """Run weekly synthesis on all entities."""
    synthesizer = Synthesizer()
    entities = storage.load_entities()

    results = {
        "synthesized": [],
        "archived_facts": 0,
        "errors": [],
    }

    for entity_name in entities:
        try:
            new_summary = synthesizer.synthesize_entity(entity_name)
            if new_summary:
                synthesizer.update_entity_summary(entity_name, new_summary)
                results["synthesized"].append(entity_name)
        except Exception as e:
            results["errors"].append(f"{entity_name}: {e}")

        archived = synthesizer.archive_old_facts(entity_name)
        results["archived_facts"] += archived

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    print(json.dumps(result, indent=2))
