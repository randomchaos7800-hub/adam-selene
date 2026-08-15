"""Goal loop — autonomous multi-turn pursuit of an owner-given objective.

OFF BY DEFAULT. Opt in via settings.json: {"goals": {"enabled": true}}.

This is a real autonomy/safety tradeoff, not a free feature: once started,
the agent re-prompts itself through the normal conversation path — with
full tool access — for up to max_turns without the owner approving each
individual step. It is bounded (a hard turn cap regardless of what the
loop's own judge decides) and interruptible (/goal pause|stop), but it is
still the framework's most autonomous mode. Read this whole docstring, and
the README section on it, before enabling it. If you don't have a
specific use case in mind for unattended multi-step execution, leave it
off — the rest of the framework (ordinary conversation, heartbeat idle
reflection, the research agenda system) already covers most of what a
personal agent needs without this.

Mechanism:
  - Each turn re-injects a continuation prompt through relay.respond() —
    the SAME path an owner message takes — so every step shows up in
    ordinary conversation history, not a hidden side channel. Nothing
    about this bypasses existing tool gating (capabilities.py,
    PRIVILEGED_TOOLS, L0_GUARDED_TOOLS all still apply per-turn exactly as
    they would in a normal conversation).
  - After each turn, a cheap, separately-prompted switchboard call judges
    done-vs-continue with a one-line JSON verdict. The judge fails OPEN —
    if it errors or the response doesn't parse, the loop continues rather
    than silently stopping. This is deliberate: the turn budget, not the
    judge, is the actual safety backstop. Don't rely on the judge alone.
  - max_turns (default 10, configurable) is a hard stop regardless of
    judge behavior — the loop cannot run indefinitely even if the judge
    never returns a clean "done".

Only one goal can be active at a time (matches the single-active-thread
pattern already used by relay/working_memory.py for research). Starting a
new goal while one is active replaces it.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from relay import config

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 10
JUDGE_PROMPT = """You're checking whether an autonomous task has been completed.

Goal: {goal}
Turn: {turn}/{max_turns}

Most recent response:
{response}

Has the goal been substantially achieved? Reply with ONLY this JSON, nothing else:
{{"done": true|false, "reason": "one short sentence"}}"""

CONTINUATION_PROMPT = """[Autonomous goal loop — turn {turn}/{max_turns}]

Goal: {goal}

Continue working toward this goal. If you believe it's complete, say so clearly
and summarize what you accomplished."""


def _is_enabled() -> bool:
    return config.load_settings().get("goals", {}).get("enabled", False)


def _max_turns() -> int:
    return config.load_settings().get("goals", {}).get("max_turns", DEFAULT_MAX_TURNS)


@dataclass
class GoalState:
    goal: str
    user_id: str
    interface: str
    max_turns: int
    turn: int = 0
    status: str = "running"  # running | paused | done | failed | stopped
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_response: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "turn": self.turn,
            "max_turns": self.max_turns,
            "status": self.status,
            "started_at": self.started_at,
            "reason": self.reason,
        }


class GoalLoop:
    """Manages the single active goal, if any."""

    def __init__(self):
        self._state: GoalState | None = None
        self._task: asyncio.Task | None = None

    def status(self) -> dict:
        if self._state is None:
            return {"active": False}
        return {"active": True, **self._state.to_dict()}

    def start(self, goal: str, user_id: str, interface: str) -> dict:
        if not _is_enabled():
            return {"started": False, "error": "Goal loop is disabled. Enable via settings.json: goals.enabled = true."}
        if self._state is not None and self._state.status == "running":
            return {"started": False, "error": f"A goal is already running: '{self._state.goal}'. Stop it first."}

        self._state = GoalState(goal=goal, user_id=user_id, interface=interface, max_turns=_max_turns())
        self._task = asyncio.create_task(self._run())
        logger.info(f"Goal loop started: '{goal}' (max_turns={self._state.max_turns})")
        return {"started": True, "goal": goal, "max_turns": self._state.max_turns}

    def pause(self) -> dict:
        if self._state is None or self._state.status != "running":
            return {"ok": False, "error": "No running goal to pause."}
        self._state.status = "paused"
        return {"ok": True}

    def resume(self) -> dict:
        if self._state is None or self._state.status != "paused":
            return {"ok": False, "error": "No paused goal to resume."}
        self._state.status = "running"
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return {"ok": True}

    def stop(self) -> dict:
        if self._state is None:
            return {"ok": False, "error": "No active goal."}
        self._state.status = "stopped"
        if self._task and not self._task.done():
            self._task.cancel()
        return {"ok": True, "goal": self._state.goal, "turns_completed": self._state.turn}

    async def _run(self) -> None:
        from relay.relay import get_relay
        from relay.switchboard import Switchboard

        state = self._state
        relay = get_relay()
        switchboard = Switchboard(config.memory_root())

        while state.status == "running" and state.turn < state.max_turns:
            state.turn += 1
            prompt = CONTINUATION_PROMPT.format(goal=state.goal, turn=state.turn, max_turns=state.max_turns)

            try:
                response = await asyncio.to_thread(relay.respond, prompt, state.user_id, None, state.interface)
            except Exception as e:
                logger.error(f"Goal loop turn {state.turn} failed: {e}")
                state.status = "failed"
                state.reason = f"turn error: {e}"
                return

            state.last_response = response

            if await self._goal_reached(switchboard, state, response):
                state.status = "done"
                logger.info(f"Goal loop complete after {state.turn} turns: '{state.goal}'")
                return

        if state.status == "running":
            state.status = "done"
            state.reason = f"reached max_turns ({state.max_turns})"
            logger.info(f"Goal loop hit turn cap: '{state.goal}'")

    async def _goal_reached(self, switchboard, state: GoalState, response: str) -> bool:
        """Judge done-vs-continue. Fails OPEN (continue) on any error — the
        turn cap in _run(), not this check, is the real backstop."""
        prompt = JUDGE_PROMPT.format(goal=state.goal, turn=state.turn, max_turns=state.max_turns, response=response[:800])
        try:
            judge_response = await asyncio.to_thread(
                switchboard.call,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
            )
            text = judge_response.content[0].text if judge_response.content else ""
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if not match:
                return False
            data = json.loads(match.group())
            done = bool(data.get("done", False))
            if done:
                state.reason = data.get("reason", "")
            return done
        except Exception as e:
            logger.debug(f"Goal loop judge failed (failing open — continuing): {e}")
            return False


_loop_instance: GoalLoop | None = None


def get_goal_loop() -> GoalLoop:
    global _loop_instance
    if _loop_instance is None:
        _loop_instance = GoalLoop()
    return _loop_instance
