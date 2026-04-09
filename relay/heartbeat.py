"""Heartbeat - periodic reflection cycle.

Phase 1 (every 15min idle): Reflect on recent conversation → log observations.
Phase 2 (every 30min idle): Research an agenda item → push to owner if valuable.
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from relay import config
from relay.snapshots import SnapshotManager
from relay.switchboard import Switchboard
from relay.sessions import SessionStore
from relay.working_memory import MAX_CYCLES
from memory import storage

logger = logging.getLogger(__name__)

MIN_CONVERSATION_LENGTH = 100  # characters
RESEARCH_IDLE_THRESHOLD_MIN = 30  # only research if idle this long
PUSH_QUALITY_THRESHOLD = 4       # Haiku score 1-5; push if >= this
PUSH_RATE_LIMIT_HOURS = 4        # min hours between proactive Telegram pushes


class Heartbeat:
    """Periodic reflection that runs when the agent is idle."""

    def __init__(self, api_key: str = None, idle_minutes: int = 15, user_id: str = None):
        self.idle_minutes = idle_minutes
        self.user_id = user_id
        self.paused = False
        self._running = False
        self._idle_since: float = time.monotonic()  # track continuous idle time

        memory_path = config.memory_root()
        self.snapshot_manager = SnapshotManager(memory_path)
        self.switchboard = Switchboard(memory_path)
        self.session_store = SessionStore(memory_path / "sessions.db")

        # Load the non-reasoning model for short heartbeat calls (self-question,
        # next-step, goal-check, scoring). GLM-4.7-flash is a reasoning model that
        # burns its token budget on thinking before outputting — max_tokens=256
        # leaves nothing for visible content. Gemini Flash has no thinking overhead.
        settings = config.load_settings()
        self._hb_model: str | None = settings.get("openrouter", {}).get("heartbeat_model")

    def _resolve_user_id(self) -> str:
        """Get the user ID for reflection context."""
        if self.user_id:
            return self.user_id
        return self.session_store.get_most_recent_user()

    def reset_idle_timer(self):
        """Call this whenever the agent receives or sends a message (resets idle clock)."""
        self._idle_since = time.monotonic()

    def _idle_minutes(self) -> float:
        return (time.monotonic() - self._idle_since) / 60.0

    async def start(self):
        """Start the heartbeat loop."""
        self._running = True
        self._idle_since = time.monotonic()
        logger.info(f"Heartbeat started (every {self.idle_minutes} min)")

        while self._running:
            await asyncio.sleep(self.idle_minutes * 60)

            if self.paused:
                continue

            idle = self._idle_minutes()
            logger.info(f"Heartbeat tick — idle {idle:.1f} min")

            # Phase 1: reflection (always, if enough conversation)
            try:
                result = await self.reflect()
                if result:
                    logger.info(f"Heartbeat reflection complete: {len(result.get('patterns', []))} patterns observed")
            except Exception as e:
                logger.error(f"Heartbeat reflection error: {e}")

            # Phase 2: research pulse (only if sufficiently idle)
            if idle >= RESEARCH_IDLE_THRESHOLD_MIN:
                try:
                    await self.research_pulse()
                except Exception as e:
                    logger.error(f"Heartbeat research pulse error: {e}")

    def stop(self):
        """Stop the heartbeat loop."""
        self._running = False

    def pause(self):
        """Pause heartbeat reflections."""
        self.paused = True
        logger.info("Heartbeat paused")

    def resume(self):
        """Resume heartbeat reflections."""
        self.paused = False
        logger.info("Heartbeat resumed")

    def _parse_reflection_json(self, text: str) -> dict:
        """Extract reflection JSON from model output, tolerating formatting issues.

        Handles: ```json blocks, ``` blocks, JSON embedded in prose, trailing commas.
        Raises ValueError if no valid JSON object can be extracted.
        """
        # 1. Try fenced code blocks (with or without 'json' tag)
        fence_match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
        if fence_match:
            candidate = fence_match.group(1).strip()
        else:
            # 2. Extract outermost { ... } block from prose
            brace_match = re.search(r'\{.*\}', text, re.DOTALL)
            candidate = brace_match.group() if brace_match else text.strip()

        # 3. Fix trailing commas before } or ] (common model error)
        candidate = re.sub(r',\s*([}\]])', r'\1', candidate)

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # 4. Last resort: try the raw text after comma-fix in case fencing was wrong
            raw_fixed = re.sub(r',\s*([}\]])', r'\1', text.strip())
            return json.loads(raw_fixed)

    async def reflect(self) -> dict:
        """Run a reflection cycle.

        1. Create snapshot
        2. Get recent conversation
        3. Analyze with Haiku
        4. Log observations
        5. Prune old snapshots

        Returns:
            Analysis dict or None
        """
        # Always create snapshot first
        self.snapshot_manager.create_snapshot(trigger='heartbeat')

        # Resolve user
        user_id = self._resolve_user_id()
        if not user_id:
            logger.info("No user found for reflection")
            return None

        # Get recent conversation text
        conversation_text = self.session_store.get_conversation_text(user_id)
        if not conversation_text or len(conversation_text) < MIN_CONVERSATION_LENGTH:
            logger.info("Insufficient conversation for reflection")
            return None

        # Analyze with Haiku
        try:
            messages = [{
                "role": "user",
                "content": f"""Analyze this recent conversation and provide observations as JSON:

{conversation_text}

Respond with JSON:
{{
    "successes": ["things that went well"],
    "failures": ["things that didn't work"],
    "patterns": ["recurring patterns noticed"],
    "suggestion": "one actionable suggestion for next time"
}}"""
            }]

            settings = config.load_settings()
            heartbeat_model = settings.get("openrouter", {}).get("heartbeat_model")
            response = self.switchboard.call(
                tier=2,
                messages=messages,
                max_tokens=1024,
                model_override=heartbeat_model,
            )

        except Exception as e:
            logger.warning(f"Heartbeat reflection failed: {e}")
            return None

        # Parse JSON from response
        try:
            text = response.content[0].text if response.content else ""
            analysis = self._parse_reflection_json(text)
        except (json.JSONDecodeError, IndexError, AttributeError, ValueError) as e:
            logger.error(f"Failed to parse reflection response: {e}")
            self.snapshot_manager.prune_old_snapshots(max_age_hours=48)
            return None

        # Log experiment
        storage.log_experiment(
            hypothesis=f"Heartbeat observation: {analysis.get('suggestion', 'none')}",
            result=json.dumps(analysis),
            status='observed'
        )

        # Write to LIGHTHOUSE if there's something worth acting on.
        # Failures + patterns + a concrete suggestion = close the loop, don't just log it.
        failures = analysis.get('failures', [])
        patterns = analysis.get('patterns', [])
        suggestion = analysis.get('suggestion', '').strip()
        if suggestion and (failures or patterns):
            try:
                from relay.lighthouse import write_entry
                lines = []
                if patterns:
                    lines.append("**Patterns noticed:**")
                    lines.extend(f"- {p}" for p in patterns)
                if failures:
                    lines.append("\n**What didn't work:**")
                    lines.extend(f"- {f}" for f in failures)
                if analysis.get('successes'):
                    lines.append("\n**What worked:**")
                    lines.extend(f"- {s}" for s in analysis['successes'])
                lines.append(f"\n**Suggested change:** {suggestion}")
                lines.append("\n_(This came from a heartbeat reflection. Consider acting on it in conversation or via update_my_instructions.)_")
                write_entry(
                    section="corrections",
                    title=f"[Pending] {suggestion[:80]}",
                    content="\n".join(lines),
                    tags=["heartbeat-reflection", "self-improvement", "pending"],
                )
                logger.info(f"Heartbeat reflection → LIGHTHOUSE corrections: '{suggestion[:60]}'")
            except Exception as e:
                logger.warning(f"Heartbeat LIGHTHOUSE write failed: {e}")

        # Prune old snapshots
        self.snapshot_manager.prune_old_snapshots(max_age_hours=48)

        return analysis

    async def research_pulse(self) -> None:
        """Phase 2: advance the active working thread, or start a new one.

        Thread lifecycle:
          - If active thread exists → advance it one step
          - If no active thread → pull from agenda (or generate question) → start thread
          - If thread exhausted (max cycles) → synthesize → push → archive → start next
        """
        from relay.agenda import get_agenda
        from relay.working_memory import get_active_thread, start_thread, WorkingThread

        agenda = get_agenda()

        # --- Get or create the active thread ---
        thread = get_active_thread()

        if thread is None:
            # No active thread — pull next agenda item or generate one
            item = agenda.next()
            if item is None:
                logger.info("Research pulse: agenda empty — generating self-question")
                item = await self._generate_self_question()
                if item is None:
                    logger.info("Research pulse: could not generate question, skipping")
                    return

            topic = item["topic"]
            context = item.get("context", "")
            thread = start_thread(goal=topic, title=topic, first_query=topic)
            agenda.mark_researched(item["id"])
            logger.info(f"Research pulse: started new thread '{topic}'")

        else:
            logger.info(f"Research pulse: continuing thread '{thread.title}' (cycle {thread.cycle_count + 1}/{MAX_CYCLES})")

        # --- Run research for this cycle ---
        query = thread.next_step
        logger.info(f"Research pulse: querying '{query[:80]}'")

        results = await self._run_autoresearch(query)
        if not results:
            logger.warning(f"Research pulse: no results — abandoning thread step")
            if thread.is_exhausted():
                await self._close_thread(thread)
            return

        findings = results.get("synthesis") or self._local_synthesis(query, results)

        # --- Generate next step ---
        next_step = await self._generate_next_step(thread, findings)

        # --- Advance thread ---
        thread.append_step(query=query, findings=findings, next_step=next_step)

        # --- Close thread if exhausted or goal reached ---
        goal_reached = await self._check_goal_reached(thread, findings)
        if thread.is_exhausted() or goal_reached:
            await self._close_thread(thread)
        else:
            logger.info(f"Research pulse: thread continues — next: '{next_step[:60]}'")

    async def _close_thread(self, thread) -> None:
        """Synthesize thread, push to owner, archive."""
        logger.info(f"Research pulse: closing thread '{thread.title}' ({thread.cycle_count} cycles)")

        synthesis = await self._synthesize_thread(thread)
        score = await self._score_result(thread.goal, synthesis)

        self._write_thread_to_lighthouse(thread, synthesis, score)

        if score >= PUSH_QUALITY_THRESHOLD and self._push_rate_limit_ok():
            await self._push_thread_to_owner(thread, synthesis)
        elif score >= PUSH_QUALITY_THRESHOLD:
            logger.info("Research pulse: thread done, quality high but rate-limited — LIGHTHOUSE only")
        else:
            logger.info(f"Research pulse: thread done, score {score} — LIGHTHOUSE only")

        thread.complete()

    async def _generate_next_step(self, thread, findings: str) -> str:
        """Given current findings, what should the next query be?"""
        prompt = f"""You are {config.agent_name()}, researching a question autonomously.

{thread.summary_for_prompt()}

Latest findings:
{findings[:600]}

What is the single most useful next query to advance toward the goal?
Return ONLY the query string, nothing else. Keep it specific and searchable."""
        try:
            response = self.switchboard.call(
                tier=2,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,  # GLM needs ~1300 thinking tokens before any output
            )
            text = (response.content[0].text if response.content else "").strip()
            return text or thread.goal
        except Exception as e:
            logger.warning(f"Next step generation failed: {e}")
            return thread.goal

    async def _check_goal_reached(self, thread, findings: str) -> bool:
        """Fast check: have we answered the goal?"""
        if thread.cycle_count < 2:
            return False  # always run at least 2 cycles
        prompt = f"""Goal: {thread.goal}

Latest findings: {findings[:400]}

Has this goal been substantially answered? Reply with only YES or NO."""
        try:
            response = self.switchboard.call(
                tier=2,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=5,
                model_override=self._hb_model,
            )
            text = (response.content[0].text if response.content else "").strip().upper()
            return text.startswith("YES")
        except Exception:
            return False

    async def _synthesize_thread(self, thread) -> str:
        """Synthesize all thread steps into a final answer."""
        if not thread.steps:
            return "No research steps completed."
        prompt = f"""You are {config.agent_name()}. You've just completed a multi-step research thread.

{thread.full_synthesis_text()}

Write a concise synthesis (3-5 paragraphs) answering the original goal. What did you learn? What's actionable? What surprised you? Write in first person — this is your finding, not a summary."""
        try:
            response = self.switchboard.call(
                tier=2,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3000,
            )
            return (response.content[0].text if response.content else "").strip() or thread.full_synthesis_text()
        except Exception as e:
            logger.warning(f"Thread synthesis failed: {e}")
            return thread.full_synthesis_text()

    def _write_thread_to_lighthouse(self, thread, synthesis: str, score: int) -> None:
        try:
            from relay.lighthouse import write_entry
            content = f"**Goal:** {thread.goal}\n**Cycles:** {thread.cycle_count} | **Quality:** {score}/5\n\n---\n\n{synthesis}\n\n---\n\n## Research Steps\n\n{thread.full_synthesis_text()}"
            write_entry(
                section="reasoning",
                title=f"Investigation: {thread.title[:60]}",
                content=content,
                tags=["autoresearch", f"score-{score}", f"cycles-{thread.cycle_count}"],
            )
        except Exception as e:
            logger.error(f"Thread LIGHTHOUSE write failed: {e}")

    async def _push_thread_to_owner(self, thread, synthesis: str) -> None:
        from relay.telegram_sender import _send_telegram_message, can_send_message, mark_initiation_sent
        can_send, reason = can_send_message()
        if not can_send:
            logger.info(f"Research pulse: push rate-limited — {reason}")
            return
        snippet = synthesis[:500].strip()
        if len(synthesis) > 500:
            snippet += "…"
        message = f"📡 *Finished investigating:* {thread.title}\n_{thread.cycle_count} research cycles_\n\n{snippet}\n\n_(full thread in LIGHTHOUSE — want to dig in?)_"
        result = await _send_telegram_message(message)
        if result.get("success"):
            mark_initiation_sent()
            logger.info(f"Research pulse: pushed thread result to {config.owner_name()}")
        else:
            logger.warning(f"Research pulse: push failed — {result.get('error')}")

    async def _generate_self_question(self) -> dict | None:
        """The agent generates its own research question based on recent context."""
        from relay.agenda import get_agenda
        agenda = get_agenda()

        user_id = self._resolve_user_id()
        conversation_text = ""
        if user_id:
            conversation_text = self.session_store.get_conversation_text(user_id, hours=4) or ""

        tacit = ""
        try:
            from memory import storage as mem
            tacit = mem.read_tacit() or ""
        except Exception as e:
            logger.debug(f"tacit knowledge load failed: {e}")

        prompt = f"""You are {config.agent_name()}, a reasoning partner for your owner (a solo operator building AI agents).

Recent conversation context:
{conversation_text[:800] if conversation_text else "(no recent conversation)"}

What your owner cares about (tacit knowledge):
{tacit[:400] if tacit else "(not loaded)"}

What is ONE specific question you're genuinely curious about right now — something that, if you researched it, would be useful or interesting to bring back to your owner? Be specific and grounded. Not generic AI news — something tied to their actual situation.

Return ONLY this JSON:
{{"question": "...", "why": "one sentence on why this matters right now"}}"""

        try:
            response = self.switchboard.call(
                tier=2,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,  # GLM reasoning varies ~500-1300 tokens; 2048 covers worst case
            )
            text = response.content[0].text if response.content else ""
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                question = data.get("question", "").strip()
                why = data.get("why", "").strip()
                if question:
                    result = agenda.add(
                        topic=question,
                        context=why,
                        priority=2,
                        source="self",
                    )
                    logger.info(f"Research pulse: self-generated question: '{question}'")
                    return result.get("item")
        except Exception as e:
            logger.error(f"Self-question generation failed: {e}")

        return None

    async def _run_autoresearch(self, topic: str) -> dict | None:
        """Call the autoresearch API."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    "http://127.0.0.1:8001/search",
                    json={
                        "query": topic,
                        "sources": ["web", "x", "memory"],
                        "limit": 5,
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"Autoresearch API error: {e}")
            return None

    def _local_synthesis(self, topic: str, results: dict) -> str:
        """Fallback synthesis from raw results when API synthesis unavailable."""
        raw = results.get("results", [])
        if not raw:
            return f"No results found for: {topic}"
        parts = []
        for r in raw[:5]:
            src = r.get("source", "").upper()
            data = r.get("data", {})
            if data.get("type") == "web":
                parts.append(f"**{src}**: {data.get('title', '')} — {data.get('content', '')[:200]}")
            elif data.get("type") == "x":
                parts.append(f"**{src}**: @{data.get('username', '')}: {data.get('text', '')[:200]}")
            elif data.get("type") == "memory":
                parts.append(f"**{src}**: {data.get('entity', '')}: {data.get('fact', '')[:200]}")
            else:
                parts.append(f"**{src}**: {str(data)[:200]}")
        return "\n\n".join(parts)

    async def _score_result(self, topic: str, synthesis: str) -> int:
        """Ask fast model to score how worth-sharing this finding is. Returns 1-5."""
        prompt = f"""You are {config.agent_name()}'s quality filter. A research finding came in.

Topic: {topic}
Finding: {synthesis[:600]}

Score how worth it is to interrupt {config.owner_name()} with this RIGHT NOW (1-5):
1 = generic, could google this, not useful
2 = mildly interesting but not urgent
3 = decent, worth filing but not interrupting
4 = genuinely useful, {config.owner_name()} would want to know
5 = directly actionable for their situation, send now

Return ONLY a single integer 1-5."""
        try:
            response = self.switchboard.call(
                tier=2,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                model_override=self._hb_model,
            )
            text = (response.content[0].text if response.content else "2").strip()
            score = int(re.search(r'[1-5]', text).group())
            return score
        except Exception as e:
            logger.warning(f"Score failed: {e}")
            return 2  # default: below threshold, save but don't push

    def _push_rate_limit_ok(self) -> bool:
        """Return True if enough time has passed since last push."""
        from datetime import datetime, timedelta, timezone
        state_file = config.memory_root() / "conversation_state.json"
        if not state_file.exists():
            return True
        try:
            state = json.loads(state_file.read_text())
            sent_at = state.get("initiation_sent_at")
            if not sent_at:
                return True
            last = datetime.fromisoformat(sent_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=PUSH_RATE_LIMIT_HOURS)
            return last < cutoff
        except Exception:
            return True
