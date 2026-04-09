"""Nightly memory consolidation -- the agent's REM sleep pass.

Runs on a schedule (e.g., 3:00 AM) after any extraction passes.

Four-phase pass:
  1. Replay      -- collect what was active across all memory layers in the last 24h
  2. Decay       -- score all active facts with exponential decay; archive below threshold
                   (never delete -- archive is archaeological reference, not trash)
  3. Patterns    -- cross-layer signal detection; promote insights to MEMORY.md
  4. Resolve     -- detect and auto-resolve fact contradictions (keep newer, log what changed)

Output:
  <memory_root>/consolidation/YYYY-MM-DD.json  -- structured run log
  LIGHTHOUSE reasoning entry                    -- breadcrumb so the agent has overnight context
"""

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


def _get_memory_root() -> Path:
    """Get memory root from settings, avoiding circular imports."""
    settings_path = Path(__file__).parent.parent / "config" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        return Path(settings.get("memory_path", "~/smartagent-memory")).expanduser()
    return Path.home() / "smartagent-memory"


def _get_lighthouse_root() -> Path:
    """Get LIGHTHOUSE root from settings."""
    settings_path = Path(__file__).parent.parent / "config" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        return Path(settings.get("lighthouse_path", "~/SmartAgent/LIGHTHOUSE")).expanduser()
    return Path(__file__).parent.parent / "LIGHTHOUSE"


MEMORY_ROOT = _get_memory_root()
CONSOLIDATION_DIR = MEMORY_ROOT / "consolidation"
LIGHTHOUSE_ROOT = _get_lighthouse_root()
SESSIONS_DB = MEMORY_ROOT / "sessions.db"

# Decay rate per category -- how fast relevance fades day-over-day.
# Higher = faster decay. Milestones and relationships are durable; status is transient.
CATEGORY_DECAY_RATES: dict[str, float] = {
    "status": 0.94,        # ~37 days to 0.10 without boost
    "constraint": 0.97,    # ~74 days to 0.10 without boost
    "preference": 0.97,
    "decision": 0.98,      # ~112 days to 0.10 without boost
    "milestone": 0.992,    # ~285 days to 0.10 without boost
    "relationship": 0.992,
}
DEFAULT_DECAY_RATE = 0.97

# Archive when score drops below this AND fact is older than ARCHIVE_MIN_AGE_DAYS.
ARCHIVE_THRESHOLD = 0.10
ARCHIVE_MIN_AGE_DAYS = 45

# Recent reference boost: if the entity was mentioned in conversations in the
# last N days, multiply the decay score by this factor (capped at 1.0).
REFERENCE_BOOST = 1.6
REFERENCE_BOOST_WINDOW_DAYS = 7

# Only run contradiction checks for categories where contradictions are meaningful.
CONTRADICTION_CATEGORIES = {"status", "preference", "constraint", "decision"}

# Max facts per entity/category to send to the LLM for contradiction check.
MAX_FACTS_FOR_CONTRADICTION_CHECK = 6


def _now_str() -> str:
    return datetime.now().isoformat()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_settings() -> dict:
    settings_path = Path(__file__).parent.parent / "config" / "settings.json"
    if settings_path.exists():
        return json.loads(settings_path.read_text())
    return {}


def _make_client() -> tuple[OpenAI, str]:
    settings = _load_settings()
    or_cfg = settings.get("openrouter", {})
    base_url = or_cfg.get("base_url", "https://openrouter.ai/v1")
    model = or_cfg.get("model", "google/gemini-flash-2.0")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    return OpenAI(base_url=base_url, api_key=api_key), model


def _load_entities() -> dict:
    entities_file = MEMORY_ROOT / "entities.json"
    if not entities_file.exists():
        return {}
    return json.loads(entities_file.read_text())


def _load_facts(entity_name: str, entities: dict) -> Optional[tuple[Path, dict]]:
    """Return (facts_file_path, facts_data) or None if missing."""
    if entity_name not in entities:
        return None
    facts_file = MEMORY_ROOT / entities[entity_name]["path"] / "facts.json"
    if not facts_file.exists():
        return None
    return facts_file, json.loads(facts_file.read_text())


def _strip_json_fence(raw: str) -> str:
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


class ConsolidationPass:
    """Full nightly consolidation -- runs after extraction passes."""

    def __init__(self):
        self.client, self.model = _make_client()
        self.today = _today()
        self.now = datetime.now()
        self.entities = _load_entities()
        self.report: dict = {
            "date": self.today,
            "phases": {},
            "facts_scored": 0,
            "facts_archived": 0,
            "contradictions_found": 0,
            "contradictions_resolved": 0,
            "insights_promoted": 0,
            "patterns": [],
            "errors": [],
        }

    def run(self) -> dict:
        logger.info(f"Consolidation pass starting -- {self.today}")

        # Phase 1: Replay -- gather cross-layer signals
        hot_entities = self._phase_replay()
        self.report["phases"]["replay"] = {
            "hot_entities": list(hot_entities.keys()),
            "total_signals": sum(hot_entities.values()),
        }
        logger.info(f"Replay: {len(hot_entities)} hot entities across layers")

        # Phase 2: Decay scoring + archival
        recent_entities = self._get_recent_entities(days=REFERENCE_BOOST_WINDOW_DAYS)
        self._phase_decay(recent_entities)

        # Phase 3: Pattern detection -- cross-layer insights -> MEMORY.md
        if hot_entities:
            self._phase_patterns(hot_entities)
        else:
            self.report["phases"]["patterns"] = {"skipped": "no hot entities"}

        # Phase 4: Contradiction resolution -- only for hot entities (cost control)
        self._phase_resolve_contradictions(set(hot_entities.keys()))

        # Write outputs
        self._write_log()
        self._write_breadcrumb()

        logger.info(
            f"Consolidation complete -- "
            f"{self.report['facts_archived']} archived, "
            f"{self.report['contradictions_resolved']} resolved, "
            f"{self.report['insights_promoted']} insights promoted"
        )
        return self.report

    # -- Phase 1: Replay -------------------------------------------------------

    def _phase_replay(self) -> dict[str, int]:
        """Count how many memory layers each entity appeared in during the last 24h.

        Returns entities with 2+ signals -- those that are cross-layer hot.
        """
        signals: dict[str, int] = {}
        since = self.now - timedelta(hours=24)

        # Source 1: New/updated LTM facts
        for entity_name, entity_data in self.entities.items():
            facts_file = MEMORY_ROOT / entity_data["path"] / "facts.json"
            if not facts_file.exists():
                continue
            facts_data = json.loads(facts_file.read_text())
            for fact in facts_data.get("facts", []):
                ts_str = fact.get("timestamp", fact.get("extracted", ""))
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
                    if ts >= since:
                        signals[entity_name] = signals.get(entity_name, 0) + 1
                        break  # one signal per entity per source
                except ValueError:
                    pass

        # Source 2: LIGHTHOUSE entries written in last 24h
        if LIGHTHOUSE_ROOT.exists():
            entity_names_lower = {
                e: e.replace("_", " ") for e in self.entities
            }
            for section_dir in LIGHTHOUSE_ROOT.iterdir():
                if not section_dir.is_dir():
                    continue
                for entry_file in section_dir.glob("*.md"):
                    try:
                        mtime = datetime.fromtimestamp(entry_file.stat().st_mtime)
                        if mtime < since:
                            continue
                        content = entry_file.read_text(encoding="utf-8", errors="ignore").lower()
                        for entity_name, display in entity_names_lower.items():
                            if entity_name in content or display in content:
                                signals[entity_name] = signals.get(entity_name, 0) + 1
                    except Exception:
                        pass

        # Source 3: Archived WM threads (check goal/title text for entity mentions)
        wm_file = MEMORY_ROOT / "working_memory.json"
        if wm_file.exists():
            try:
                wm_data = json.loads(wm_file.read_text())
                entity_names_lower = {
                    e: e.replace("_", " ") for e in self.entities
                }
                for thread in wm_data.get("archived_threads", []):
                    thread_text = (
                        thread.get("goal", "") + " " + thread.get("title", "")
                    ).lower()
                    for entity_name, display in entity_names_lower.items():
                        if entity_name in thread_text or display in thread_text:
                            signals[entity_name] = signals.get(entity_name, 0) + 1
            except Exception as e:
                logger.warning(f"WM replay error: {e}")

        # Only entities with cross-layer presence (2+ signals)
        return {k: v for k, v in signals.items() if v >= 2}

    # -- Phase 2: Decay scoring + archival -------------------------------------

    def _get_recent_entities(self, days: int) -> set[str]:
        """Entity names mentioned in conversations in the last N days."""
        recent: set[str] = set()
        if not SESSIONS_DB.exists():
            return recent
        try:
            since = (self.now - timedelta(days=days)).isoformat()
            conn = sqlite3.connect(str(SESSIONS_DB))
            rows = conn.execute(
                "SELECT content FROM messages WHERE timestamp > ?",
                (since,)
            ).fetchall()
            conn.close()

            all_content = " ".join(r[0] for r in rows if r[0]).lower()
            for entity_name in self.entities:
                display = entity_name.replace("_", " ")
                if entity_name in all_content or display in all_content:
                    recent.add(entity_name)
        except Exception as e:
            logger.warning(f"Recent entity lookup failed: {e}")
        return recent

    def _phase_decay(self, recent_entities: set[str]) -> None:
        """Score all active facts; archive those below the threshold."""
        total_scored = 0
        total_archived = 0

        for entity_name, entity_data in self.entities.items():
            result = _load_facts(entity_name, self.entities)
            if result is None:
                continue
            facts_file, facts_data = result
            modified = False
            has_boost = entity_name in recent_entities

            for fact in facts_data.get("facts", []):
                if not fact.get("active", True):
                    continue
                if fact.get("status", "active") != "active":
                    continue

                ts_str = fact.get("timestamp", fact.get("extracted", ""))
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    continue

                age_days = (self.now - ts).days
                category = fact.get("category", fact.get("type", "other"))
                rate = CATEGORY_DECAY_RATES.get(category, DEFAULT_DECAY_RATE)

                score = rate ** age_days
                if has_boost:
                    score = min(1.0, score * REFERENCE_BOOST)

                fact["decay_score"] = round(score, 4)
                fact["decay_scored_at"] = _now_str()
                total_scored += 1
                modified = True

                if score < ARCHIVE_THRESHOLD and age_days >= ARCHIVE_MIN_AGE_DAYS:
                    fact["active"] = False
                    fact["context"] = "archived"
                    fact["archived"] = _now_str()
                    fact["archive_reason"] = (
                        f"decay score={score:.3f} age={age_days}d category={category}"
                    )
                    total_archived += 1
                    logger.debug(
                        f"Archived {fact.get('id', '?')} from {entity_name} "
                        f"(score={score:.3f}, age={age_days}d)"
                    )

            if modified:
                facts_file.write_text(json.dumps(facts_data, indent=2))

        self.report["facts_scored"] = total_scored
        self.report["facts_archived"] = total_archived
        self.report["phases"]["decay"] = {
            "scored": total_scored,
            "archived": total_archived,
            "archive_threshold": ARCHIVE_THRESHOLD,
            "archive_min_age_days": ARCHIVE_MIN_AGE_DAYS,
        }
        logger.info(f"Decay: scored {total_scored}, archived {total_archived}")

    # -- Phase 3: Pattern detection --------------------------------------------

    def _phase_patterns(self, hot_entities: dict[str, int]) -> None:
        """Detect cross-layer patterns; promote genuine insights to MEMORY.md."""
        # Build a brief context block for each hot entity
        context_parts: list[str] = []
        for entity_name, signal_count in sorted(hot_entities.items(), key=lambda x: -x[1])[:10]:
            result = _load_facts(entity_name, self.entities)
            active_facts: list[str] = []
            if result:
                _, facts_data = result
                active = [
                    f for f in facts_data.get("facts", [])
                    if f.get("active", True) and f.get("status", "active") == "active"
                ]
                active_facts = [
                    f.get("fact", f.get("content", ""))
                    for f in sorted(
                        active,
                        key=lambda f: f.get("timestamp", f.get("extracted", "")),
                        reverse=True,
                    )[:5]
                ]

            fact_lines = "\n".join(f"  - {fact}" for fact in active_facts) if active_facts else "  (no recent facts)"
            context_parts.append(
                f"Entity: {entity_name} ({signal_count} cross-layer signals today)\n{fact_lines}"
            )

        memory_md = (MEMORY_ROOT / "MEMORY.md").read_text(encoding="utf-8") if (MEMORY_ROOT / "MEMORY.md").exists() else ""

        prompt = (
            "You are analyzing an AI assistant's memory system to find cross-cutting patterns.\n\n"
            "CROSS-LAYER HOT ENTITIES (appeared in LTM + LIGHTHOUSE + working memory today):\n"
            + "\n\n".join(context_parts)
            + "\n\nCURRENT MEMORY.md (tacit knowledge already captured -- DO NOT REPEAT these):\n"
            + memory_md[:3000]
            + "\n\nTASK: Identify 0-2 insights that:\n"
            "1. Are NOT already captured in MEMORY.md\n"
            "2. Represent a genuine cross-cutting pattern (not just one entity's facts)\n"
            "3. Would help the assistant understand how your owner thinks, works, or what matters to them\n"
            "4. Are specific enough to be actionable -- not generic observations\n\n"
            "Return a JSON array ONLY:\n"
            '[{"insight": "The specific tacit insight (1-3 sentences)", '
            '"entities": ["entity1", "entity2"], "confidence": "high|medium"}]\n\n'
            "If no genuine cross-cutting insight exists, return [].\n"
            "Do NOT invent insights. Quality over quantity."
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = _strip_json_fence((response.choices[0].message.content or "").strip())
            insights = json.loads(raw)
            if not isinstance(insights, list):
                insights = []

            insights = [i for i in insights if i.get("confidence") in ("high", "medium")]

            if insights:
                self._append_to_memory_md(insights)
                self.report["insights_promoted"] = len(insights)
                self.report["patterns"] = insights

            self.report["phases"]["patterns"] = {
                "hot_entities_analyzed": len(hot_entities),
                "insights_found": len(insights),
            }
            logger.info(f"Patterns: {len(insights)} insights promoted")

        except json.JSONDecodeError as e:
            logger.warning(f"Pattern detection JSON parse failed: {e}")
            self.report["errors"].append("patterns: JSON parse error")
        except Exception as e:
            logger.error(f"Pattern detection failed: {e}")
            self.report["errors"].append(f"patterns: {e}")

    def _append_to_memory_md(self, insights: list[dict]) -> None:
        memory_file = MEMORY_ROOT / "MEMORY.md"
        existing = memory_file.read_text(encoding="utf-8") if memory_file.exists() else "# How Your Owner Thinks\n\n"

        new_section = f"\n\n## Patterns -- {self.today}\n\n"
        for item in insights:
            insight = item.get("insight", "").strip()
            entities = ", ".join(item.get("entities", []))
            new_section += f"- {insight}"
            if entities:
                new_section += f" *(signals: {entities})*"
            new_section += "\n"

        memory_file.write_text(existing.rstrip() + new_section, encoding="utf-8")
        logger.info(f"Promoted {len(insights)} insights to MEMORY.md")

    # -- Phase 4: Contradiction resolution -------------------------------------

    def _phase_resolve_contradictions(self, hot_entity_names: set[str]) -> None:
        """Find and auto-resolve contradictions in hot entities' active facts.

        Only runs on hot entities (recently cross-layer active) to control LLM cost.
        Keeps the newer fact; marks the older one superseded.
        """
        contradictions_found = 0
        contradictions_resolved = 0
        contradiction_log: list[dict] = []

        for entity_name in hot_entity_names:
            result = _load_facts(entity_name, self.entities)
            if result is None:
                continue
            facts_file, facts_data = result

            active_facts = [
                f for f in facts_data.get("facts", [])
                if f.get("active", True) and f.get("status", "active") == "active"
            ]

            # Group by category
            by_category: dict[str, list] = {}
            for fact in active_facts:
                cat = fact.get("category", fact.get("type", "general"))
                by_category.setdefault(cat, []).append(fact)

            entity_modified = False
            for category, cat_facts in by_category.items():
                if category not in CONTRADICTION_CATEGORIES:
                    continue
                if len(cat_facts) < 3:
                    continue

                # Sort oldest first
                cat_facts_sorted = sorted(
                    cat_facts,
                    key=lambda f: f.get("timestamp", f.get("extracted", "")),
                )[:MAX_FACTS_FOR_CONTRADICTION_CHECK]

                facts_text = "\n".join(
                    f"[id={f.get('id', '?')} date={f.get('timestamp', '')[:10]}] "
                    f"{f.get('fact', f.get('content', ''))}"
                    for f in cat_facts_sorted
                )

                prompt = (
                    f"These are active '{category}' facts about entity '{entity_name}'.\n"
                    "Identify pairs that DIRECTLY CONTRADICT each other "
                    "(mutually exclusive statements, not just redundant or overlapping).\n\n"
                    f"Facts:\n{facts_text}\n\n"
                    "Return JSON ONLY:\n"
                    '{"contradictions": [{"keep_id": "id-to-keep", '
                    '"supersede_id": "id-to-supersede", "reason": "brief reason"}]}\n\n'
                    "keep_id should be the NEWER (more recent) fact.\n"
                    'If no direct contradictions: {"contradictions": []}'
                )

                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        max_tokens=400,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    raw = _strip_json_fence((response.choices[0].message.content or "").strip())
                    result_json = json.loads(raw)
                    found = result_json.get("contradictions", [])

                    if not found:
                        continue

                    contradictions_found += len(found)

                    for contradiction in found:
                        keep_id = contradiction.get("keep_id", "")
                        supersede_id = contradiction.get("supersede_id", "")
                        reason = contradiction.get("reason", "")

                        if not keep_id or not supersede_id or keep_id == supersede_id:
                            continue

                        for fact in facts_data["facts"]:
                            if fact.get("id") == supersede_id:
                                fact["status"] = "superseded"
                                fact["active"] = False
                                fact["supersededBy"] = keep_id
                                fact["superseded_reason"] = f"consolidation auto-resolve: {reason}"
                                fact["superseded_at"] = _now_str()
                                entity_modified = True
                                contradictions_resolved += 1
                                contradiction_log.append({
                                    "entity": entity_name,
                                    "category": category,
                                    "superseded_id": supersede_id,
                                    "kept_id": keep_id,
                                    "reason": reason,
                                })
                                logger.debug(
                                    f"Resolved contradiction in {entity_name}/{category}: "
                                    f"superseded {supersede_id}, kept {keep_id}"
                                )
                                break

                except json.JSONDecodeError:
                    logger.warning(f"Contradiction check JSON parse failed for {entity_name}/{category}")
                except Exception as e:
                    logger.warning(f"Contradiction check error for {entity_name}: {e}")
                    self.report["errors"].append(f"contradiction/{entity_name}: {e}")

            if entity_modified:
                facts_file.write_text(json.dumps(facts_data, indent=2))

        self.report["contradictions_found"] = contradictions_found
        self.report["contradictions_resolved"] = contradictions_resolved
        self.report["phases"]["resolve"] = {
            "entities_checked": len(hot_entity_names),
            "found": contradictions_found,
            "resolved": contradictions_resolved,
            "log": contradiction_log,
        }
        logger.info(f"Contradictions: found {contradictions_found}, resolved {contradictions_resolved}")

    # -- Output ----------------------------------------------------------------

    def _write_log(self) -> None:
        CONSOLIDATION_DIR.mkdir(parents=True, exist_ok=True)
        log_file = CONSOLIDATION_DIR / f"{self.today}.json"
        self.report["completed_at"] = _now_str()
        log_file.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
        logger.info(f"Log written: {log_file}")

    def _write_breadcrumb(self) -> None:
        """Write a LIGHTHOUSE reasoning entry -- overnight context for the agent."""
        archived = self.report.get("facts_archived", 0)
        resolved = self.report.get("contradictions_resolved", 0)
        promoted = self.report.get("insights_promoted", 0)
        scored = self.report.get("facts_scored", 0)
        patterns = self.report.get("patterns", [])
        hot = self.report.get("phases", {}).get("replay", {}).get("hot_entities", [])
        errors = self.report.get("errors", [])

        lines = [
            f"Nightly consolidation completed {self.today}.",
            "",
            "**Run summary:**",
            f"- {scored} facts scored for relevance decay",
            f"- {archived} facts archived to cold storage (score < {ARCHIVE_THRESHOLD}, age > {ARCHIVE_MIN_AGE_DAYS}d)",
            f"- {resolved} fact contradictions auto-resolved (older superseded by newer)",
            f"- {promoted} cross-layer insights promoted to MEMORY.md",
        ]

        if hot:
            lines.append(f"- Hot entities (cross-layer signals): {', '.join(hot[:8])}")

        if patterns:
            lines.append("")
            lines.append("**Insights added to MEMORY.md:**")
            for p in patterns:
                lines.append(f"- {p.get('insight', '').strip()}")

        contradiction_log = self.report.get("phases", {}).get("resolve", {}).get("log", [])
        if contradiction_log:
            lines.append("")
            lines.append("**Contradictions resolved:**")
            for c in contradiction_log[:5]:
                lines.append(
                    f"- {c['entity']}/{c['category']}: superseded `{c['superseded_id']}`, "
                    f"kept `{c['kept_id']}` -- {c['reason']}"
                )

        if errors:
            lines.append("")
            lines.append(f"**Errors ({len(errors)}):** {'; '.join(errors[:3])}")

        content = "\n".join(lines)

        try:
            from relay.lighthouse import write_entry
            result = write_entry(
                "reasoning",
                f"Consolidation pass {self.today}",
                content,
                tags=["nightly", "consolidation"],
            )
            if result.get("success"):
                logger.info(f"Breadcrumb written: {result['filename']}")
            else:
                logger.warning(f"Breadcrumb write failed: {result.get('error')}")
        except Exception as e:
            logger.error(f"Breadcrumb write error: {e}")


def run() -> dict:
    """Run the nightly consolidation pass."""
    return ConsolidationPass().run()
