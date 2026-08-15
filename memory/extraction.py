"""LLM-powered fact extraction from conversations.

Runs after conversations to extract durable facts and add them to memory.
V3: Mem0-inspired two-stage extraction -- extract then compare against existing facts
    to decide ADD/UPDATE/supersede/NONE per fact. Prevents duplicates, handles contradictions.
Native OpenRouter with GLM-4.7-flash.
"""

import json
import logging
import os
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from openai import OpenAI

from memory import storage

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT_PATH = Path(__file__).parent.parent / "config" / "extraction.md"
SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"

# Stage 2 comparison prompt (Mem0 pattern)
COMPARISON_PROMPT = """You are a memory reconciliation engine.

Compare newly extracted facts against existing stored facts for each entity.
For each new fact, decide: ADD, UPDATE, or NONE.

- ADD: Genuinely new information not captured in memory
- UPDATE: Contradicts or supersedes an existing fact -- provide the fact_id of the fact being replaced
- NONE: Already captured in memory (identical or near-identical to an existing fact)

Existing stored facts:
{existing_facts}

Newly extracted facts:
{new_facts}

Return valid JSON only:
{{
  "decisions": [
    {{
      "index": 0,
      "operation": "ADD|UPDATE|NONE",
      "supersedes_id": "fact-xxxxxxxx or null",
      "reason": "one-line explanation"
    }}
  ]
}}
"""


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text())
    return {}


def load_extraction_prompt() -> str:
    if EXTRACTION_PROMPT_PATH.exists():
        return EXTRACTION_PROMPT_PATH.read_text()
    raise FileNotFoundError("Extraction prompt not found at config/extraction.md")


def get_entities_list() -> str:
    entities = storage.load_entities()
    if not entities:
        return "No known entities yet."
    lines = []
    for name, data in entities.items():
        aliases = data.get("aliases", [])
        alias_str = f" (aliases: {', '.join(aliases)})" if aliases else ""
        lines.append(f"- {name} [{data['category']}]{alias_str}")
    return "\n".join(lines)


def _ensure_entity(name: str, category: str) -> None:
    """Create entity if it doesn't exist. No-op if already present."""
    try:
        storage.add_entity(name, category)
    except ValueError:
        pass  # already exists


# Below this ratio, two fact strings aren't similar enough to trust an
# LLM's NONE ("already captured, near-identical") verdict — see
# _verify_decision(). Deliberately the same technique (difflib
# SequenceMatcher) heartbeat.py's own Tier 1 compaction already uses for
# near-duplicate detection, applied here as a write-time backstop instead
# of only a nightly one.
_NONE_VERDICT_MIN_SIMILARITY = 0.75


def _verify_decision(fact: dict, decision: dict, existing_facts_by_entity: dict[str, list]) -> dict:
    """Deterministic sanity check on a Stage 2 LLM decision before it's
    trusted.

    Two independent LLM-agnostic checks — the LLM identifies semantic
    matches (what it's good at), but freshness/dedup correctness is
    re-derived in plain code rather than taken purely on the LLM's word
    (current research on agent memory pipelines flags LLM-judged
    dedup/conflict-resolution as the specific weak point in this style of
    two-stage extraction, even for the reference implementation this
    pipeline is modeled on):

      NONE verdict ("this duplicates an existing fact, skip it") — verified
      against the entity's existing facts via string similarity. If
      nothing existing is actually close, the LLM's dedup claim doesn't
      hold up; downgrade to ADD rather than silently losing a fact that
      isn't really a duplicate.

      UPDATE verdict with a supersedes_id — verified that the referenced
      fact_id actually exists among the entity's current facts.
      storage.supersede_fact() already no-ops safely if it doesn't, but
      that failure was previously silent; here it's surfaced and the
      decision downgrades to ADD instead of silently dropping the
      supersession.

    Both failure modes downgrade to ADD rather than NONE/skip — the safer
    direction when uncertain is an extra fact in memory, not a lost one.
    """
    operation = decision.get("operation", "ADD")
    entity = fact.get("entity")
    content = (fact.get("content") or "").strip()

    if operation == "NONE" and content:
        candidates = existing_facts_by_entity.get(entity, [])
        best_ratio = 0.0
        for candidate in candidates:
            candidate_text = candidate.get("fact", candidate.get("content", ""))
            ratio = SequenceMatcher(None, content, candidate_text).ratio()
            best_ratio = max(best_ratio, ratio)
        if best_ratio < _NONE_VERDICT_MIN_SIMILARITY:
            logger.info(
                f"Overriding NONE verdict for '{content[:50]}' — best match to existing "
                f"facts was only {best_ratio:.2f} similarity, below {_NONE_VERDICT_MIN_SIMILARITY} threshold"
            )
            return {**decision, "operation": "ADD", "supersedes_id": None, "reason": "verification: not actually a near-duplicate"}

    if operation == "UPDATE":
        supersedes_id = decision.get("supersedes_id")
        candidates = existing_facts_by_entity.get(entity, [])
        known_ids = {c.get("id") for c in candidates}
        if supersedes_id and supersedes_id not in known_ids:
            logger.warning(
                f"UPDATE verdict for '{content[:50]}' references unknown fact_id "
                f"'{supersedes_id}' for entity '{entity}' — downgrading to ADD"
            )
            return {**decision, "operation": "ADD", "supersedes_id": None, "reason": "verification: supersedes_id not found"}

    return decision


def _load_existing_facts_for_entities(entity_names: list[str]) -> dict[str, list]:
    """Load active facts for a list of entities, keyed by entity name."""
    result = {}
    for name in entity_names:
        entity_data = storage.read_entity(name)
        if entity_data and entity_data.get("recent_facts"):
            result[name] = entity_data["recent_facts"]
    return result


class Extractor:
    """Extracts facts from conversations using OpenRouter GLM model."""

    def __init__(self):
        settings = load_settings()
        or_cfg = settings.get("openrouter", {})
        self.model = settings.get("models", {}).get("extraction") or or_cfg.get("model", "z-ai/glm-4.7-flash")
        base_url = or_cfg.get("base_url", "https://openrouter.ai/api/v1")

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not set")
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        logger.info(f"Extraction using OpenRouter with model: {self.model}")

    def extract(self, conversation_text: str) -> dict:
        """Stage 1: Extract raw facts from conversation."""
        prompt_template = load_extraction_prompt()
        entities_list = get_entities_list()
        today = datetime.now().strftime("%Y-%m-%d")
        prompt = (
            prompt_template
            .replace("{entities_list}", entities_list)
            .replace("{today}", today)
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=2048,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Extract facts from this conversation:\n\n{conversation_text}"}
                ]
            )
        except Exception as e:
            logger.error(f"Extraction API error: {e}")
            return {"facts": [], "new_entities": [], "timeline_entry": f"Extraction failed: {e}"}

        response_text = response.choices[0].message.content or ""
        return self._parse_json(response_text, "extraction")

    def compare_against_memory(self, new_facts: list[dict], existing_facts_by_entity: dict) -> list[dict]:
        """Stage 2 (Mem0 pattern): Compare extracted facts against existing memory.

        Returns a list of decision dicts with keys: index, operation, supersedes_id, reason.
        Falls back to all-ADD if the comparison call fails.
        """
        if not existing_facts_by_entity:
            # No existing facts to compare against -- everything is ADD
            return [{"index": i, "operation": "ADD", "supersedes_id": None, "reason": "no prior facts"} for i in range(len(new_facts))]

        # Format existing facts for the prompt
        existing_lines = []
        for entity_name, facts in existing_facts_by_entity.items():
            existing_lines.append(f"\nEntity: {entity_name}")
            for f in facts:
                fid = f.get("id", "?")
                text = f.get("fact", f.get("content", ""))
                ftype = f.get("category", f.get("type", ""))
                existing_lines.append(f"  [{fid}] ({ftype}) {text}")

        new_lines = []
        for i, fact in enumerate(new_facts):
            new_lines.append(f"[{i}] entity={fact.get('entity')} type={fact.get('type')} content={fact.get('content')}")

        prompt = COMPARISON_PROMPT.format(
            existing_facts="\n".join(existing_lines),
            new_facts="\n".join(new_lines),
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=1024,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            response_text = response.choices[0].message.content or ""
            parsed = self._parse_json(response_text, "comparison")
            return parsed.get("decisions", [])
        except Exception as e:
            logger.warning(f"Stage 2 comparison failed (falling back to ADD-all): {e}")
            return [{"index": i, "operation": "ADD", "supersedes_id": None, "reason": "comparison unavailable"} for i in range(len(new_facts))]

    def _parse_json(self, text: str, stage: str) -> dict:
        try:
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                json_str = text.split("```")[1].split("```")[0]
            else:
                json_str = text
            return json.loads(json_str.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse {stage} response: {e}")
            logger.error(f"Response was: {text[:200]}")
            return {"facts": [], "new_entities": [], "timeline_entry": f"Parse error in {stage}"}


def extract_to_memory(conversation_text: str, provenance: str = storage.DEFAULT_PROVENANCE) -> dict:
    """Extract facts via two-stage pipeline and save to memory.

    provenance is stamped on every fact this call saves — see
    memory.storage.VALID_PROVENANCE. Defaults to "owner_stated" (the
    primary conversation-extraction path, unchanged from before this
    parameter existed). Callers extracting from a less-trusted source
    (e.g. relay/irc_memory.py's extract_irc_learnings(), which runs this
    same pipeline over public-channel content from arbitrary third
    parties) should pass "tool_derived" instead.
    """
    try:
        extractor = Extractor()
    except Exception as e:
        logger.error(f"Extractor init failed: {e}")
        return {"success": False, "error": str(e)}

    # Stage 1: Extract raw facts
    try:
        result = extractor.extract(conversation_text)
    except Exception as e:
        logger.error(f"Stage 1 extraction failed: {e}")
        return {"success": False, "error": str(e)}

    new_facts = result.get("facts", [])
    new_entities = result.get("new_entities", [])

    if not new_facts and not new_entities:
        logger.info("No facts extracted from conversation")
        if result.get("timeline_entry"):
            today = datetime.now().strftime("%Y-%m-%d")
            storage.append_timeline(today, result["timeline_entry"])
        return {"success": True, "facts_saved": 0, "entities_created": 0}

    # Create new entities first (so entity resolution works for their facts)
    entities_created = 0
    entity_category_map: dict[str, str] = {}  # name -> category for new entities
    for entity_data in new_entities:
        name = entity_data.get("name")
        category = entity_data.get("category", "concepts")
        if name and category:
            _ensure_entity(name, category)
            entity_category_map[name] = category
            entities_created += 1
            logger.info(f"Entity created/confirmed: {name} [{category}]")

    # Determine which entity names appear in extracted facts
    entity_names_in_facts = list({f.get("entity") for f in new_facts if f.get("entity")})

    # Stage 2: Load existing facts and compare (Mem0 pattern)
    existing_facts = _load_existing_facts_for_entities(entity_names_in_facts)
    decisions = extractor.compare_against_memory(new_facts, existing_facts)

    # Build decision lookup by index
    decision_by_index = {d.get("index", i): d for i, d in enumerate(decisions)}

    # Save facts based on decisions
    facts_saved = 0
    skipped = 0
    superseded_count = 0

    for i, fact in enumerate(new_facts):
        entity = fact.get("entity")
        content = fact.get("content")
        fact_type = fact.get("type", "general")
        supersedes_hint = fact.get("supersedes")  # hint from Stage 1

        if not entity or not content:
            continue

        decision = _verify_decision(fact, decision_by_index.get(i, {}), existing_facts)
        operation = decision.get("operation", "ADD")

        if operation == "NONE":
            logger.debug(f"Skipping fact (NONE): {content[:60]}")
            skipped += 1
            continue

        # Ensure entity exists before writing
        known_entities = storage.load_entities()
        if entity not in known_entities:
            # Entity not in known list and not in new_entities -- try to infer category
            category = entity_category_map.get(entity, "concepts")
            _ensure_entity(entity, category)

        # Save the fact
        try:
            new_fact_id = storage.add_fact(entity, fact_type, content, provenance=provenance)
            facts_saved += 1
        except Exception as e:
            logger.warning(f"Failed to save fact for {entity}: {e}")
            continue

        # Handle supersession: Stage 2 decision takes priority, Stage 1 hint as fallback
        supersede_id = decision.get("supersedes_id") if operation == "UPDATE" else None

        if not supersede_id and supersedes_hint:
            # Stage 2 didn't pin a specific ID -- use Stage 1's hint to search
            search_term = supersedes_hint[:40]
            old_facts = storage.search_facts(search_term)
            for old in old_facts:
                if old.get("entity") == entity:
                    supersede_id = old["fact"].get("id")
                    break

        if supersede_id:
            ok = storage.supersede_fact(entity, supersede_id, new_fact_id)
            if ok:
                superseded_count += 1
                logger.info(f"Superseded fact {supersede_id} -> {new_fact_id} for {entity}")

    # Save timeline entry
    if result.get("timeline_entry"):
        today = datetime.now().strftime("%Y-%m-%d")
        storage.append_timeline(today, result["timeline_entry"])

    logger.info(
        f"Extraction complete: {facts_saved} saved, {skipped} skipped (NONE), "
        f"{superseded_count} superseded, {entities_created} entities"
    )

    return {
        "success": True,
        "facts_saved": facts_saved,
        "entities_created": entities_created,
        "superseded_count": superseded_count,
        "skipped_count": skipped,
        "timeline_entry": result.get("timeline_entry"),
    }


def run(conversation_text: str, provenance: str = storage.DEFAULT_PROVENANCE) -> dict:
    """Run extraction on conversation text. Alias for extract_to_memory."""
    return extract_to_memory(conversation_text, provenance=provenance)
