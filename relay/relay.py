"""RelayV3 — Async generator architecture with structured error recovery.

Architecture:
- relay_stream()    : async generator — yields events at each step (tools, errors, final text)
- respond()         : sync entry point — collects generator, returns final string (interface compat)
- _call_with_retry(): switchboard call with transient error backoff
- _execute_reads()  : parallel tool execution via asyncio.gather (READ_TOOLS)
- _execute_write()  : serial tool execution, stops on first failure (WRITE_TOOLS)

System prompt layout (static → dynamic for API cache efficiency):
  1. Base constitution (agent_prompt.md) — fully static
  2. Memory-stored instruction overlay — changes only via update_my_instructions
  3. Tool summary — static, generated from TOOL_DEFINITIONS
  (Future: dynamic memory context appended last)

Sub-agent isolation design note (Principle 7):
  No module-level mutable state except _relay_instance singleton.
  All per-call state is passed as parameters. relay_stream() can be instantiated
  multiple times concurrently with isolated state when sub-agents are added.
"""
import asyncio
import concurrent.futures
import json
import logging
import threading
import time
from pathlib import Path
from typing import AsyncGenerator

from memory import storage, extraction
from relay.sessions import SessionStore
from relay.switchboard import Switchboard
from relay.tools import TOOL_DEFINITIONS, READ_TOOLS, execute_tool, generate_tool_summary
from relay import session_log
from relay.working_memory import log_failure
from relay import config

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "config" / "agent_prompt.md"
SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"
MAX_TOOL_DEPTH = 40
_RETRY_MAX = 3          # max attempts for transient errors (1 initial + 2 retries)
_RETRY_BACKOFF = [1, 3]  # seconds before retry 1 and retry 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    return json.loads(SETTINGS_PATH.read_text()) if SETTINGS_PATH.exists() else {}


def _load_base_prompt() -> str:
    return PROMPT_PATH.read_text() if PROMPT_PATH.exists() else f"You are {config.agent_name()}."


def _is_transient(exc: Exception) -> bool:
    """True for errors that are worth retrying (network, rate limit, overload)."""
    msg = str(exc).lower()
    return any(k in msg for k in ("timeout", "429", "rate limit", "overloaded", "503", "502", "connection"))


def _run_async(coro):
    """Run an async coroutine from a sync context.

    Handles two cases:
    - No running event loop (most callers): asyncio.run() directly.
    - Running event loop (e.g. Slack Bolt Socket Mode thread): spawn a fresh
      thread with its own loop so we don't block or nest loops.
    """
    try:
        asyncio.get_running_loop()
        # There IS a running loop — run in a separate thread with its own loop
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=300)
    except RuntimeError:
        # No running loop — safe to use asyncio.run
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# RelayV3
# ---------------------------------------------------------------------------

class RelayV3:
    def __init__(self, tier=None):
        """Initialize relay. tier param ignored (kept for backwards compat)."""
        memory_path = config.memory_root()
        self.switchboard = Switchboard(memory_path)
        self.session_store = SessionStore()
        storage.init_memory()
        self.tools = TOOL_DEFINITIONS
        self._valid_tool_names = frozenset(t["name"] for t in self.tools)

        settings = _load_settings()
        ctx = settings.get("context", {})
        self.max_output_tokens = ctx.get("max_output_tokens", 8192)
        self.incremental_every_n = settings.get("extraction", {}).get("incremental_every_n_messages", 10)
        logger.info(f"RelayV3: {self.switchboard.MODEL}, {len(self.tools)} tools, max_output={self.max_output_tokens}")

    # ------------------------------------------------------------------
    # System prompt — static prefix first for API cache efficiency
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Assemble system prompt with static content at the top.

        Cache-efficiency layout:
          Layer 1 (most static): base constitution / agent_prompt.md
          Layer 2 (semi-static): memory-stored instruction overlay (only changes
                                 when agent calls update_my_instructions)
          Layer 3 (static):      tool summary from TOOL_DEFINITIONS
          (Future layer 4):      dynamic retrieved memory context — always last

        Keeping layers 1-3 identical across calls maximises API prefix cache hits.
        """
        # Layer 1+2: base constitution, optionally overridden by memory
        base = _load_base_prompt()
        memory_prompt = storage.load_system_prompt_from_memory()
        if memory_prompt and memory_prompt.strip() != base.strip():
            static_prefix = memory_prompt  # Agent has updated its own instructions
        else:
            static_prefix = base

        # Layer 3: tool summary (deterministic, changes only when tools are added/removed)
        tool_section = generate_tool_summary()

        return static_prefix + "\n\n---\n\n" + tool_section

    def _build_context_messages(self, user_id: str) -> list:
        snap = self.session_store.get_session_snapshot(user_id)
        return [{"role": m["role"], "content": m["content"]} for m in snap] if snap else []

    # ------------------------------------------------------------------
    # Public sync API — unchanged for all interface callers
    # ------------------------------------------------------------------

    def respond(self, message: str, user_id: str = None, images=None, interface: str = "unknown") -> str:
        """Sync entry point — collects async generator output and returns final text.

        All interface callers (Telegram, Slack, IRC) use this unchanged API.
        Internally runs relay_stream() and buffers the result.
        """
        if user_id is None:
            user_id = config.owner_user_id()
        return _run_async(self._collect_response(message, user_id, images, interface))

    async def _collect_response(self, message: str, user_id: str, images, interface: str) -> str:
        """Async collector — drains relay_stream() and returns the final text."""
        final_parts = []
        async for event in self.relay_stream(message, user_id, images, interface):
            if event["type"] == "final_text":
                final_parts.append(event["text"])
            elif event["type"] == "error" and not final_parts:
                # No text built yet — surface error as the response
                final_parts.append(event["message"])
        return "".join(final_parts)

    # ------------------------------------------------------------------
    # Async generator — core relay loop
    # ------------------------------------------------------------------

    async def relay_stream(
        self,
        message: str,
        user_id: str = None,
        images=None,
        interface: str = "unknown",
    ) -> AsyncGenerator[dict, None]:
        """Core relay loop as an async generator.

        Yields event dicts:
          {"type": "start"}
          {"type": "tool_result", "tool": str, "result": str}
          {"type": "error", "error_type": str, "message": str, ...}
          {"type": "final_text", "text": str}

        All per-call state is local — no module-level mutation — so future
        sub-agent instantiation can run multiple generators concurrently.
        """
        if user_id is None:
            user_id = config.owner_user_id()

        # --- Session setup ---
        sid, _ = session_log.current_session()
        if not sid:
            session_log.start_session(user_id=user_id, interface=interface)
        session_log.log_user_message(message, interface=interface)

        msgs = self._build_context_messages(user_id)
        msgs.append({"role": "user", "content": message})
        sys_prompt = self._build_system_prompt()

        yield {"type": "start"}

        t0 = time.monotonic()
        session_log.log_model_call(
            model=self.switchboard.MODEL,
            messages_count=len(msgs),
            max_tokens=self.max_output_tokens,
        )

        # --- Initial model call ---
        resp = await self._call_with_retry(msgs, sys_prompt)
        if resp is None:
            msg = "I can't reach any inference backend right now. Will retry on your next message."
            yield {"type": "error", "error_type": "switchboard_failure", "message": msg}
            log_failure(context="initial_model_call", error="All backends failed", recovery="Told user")
            return

        # --- Agentic loop (replaces recursive _process_response) ---
        final_text = ""
        depth = 0

        while True:
            if depth >= MAX_TOOL_DEPTH:
                n_tool_turns = sum(
                    1 for m in msgs
                    if isinstance(m.get("content"), list)
                    and any(
                        isinstance(b, dict) and b.get("type") == "tool_use"
                        for b in m["content"]
                    )
                )
                summary = (
                    f"[Reached maximum tool call depth ({MAX_TOOL_DEPTH} steps). "
                    f"Completed {n_tool_turns} tool interactions. "
                    f"Try breaking this task into smaller steps.]"
                )
                yield {
                    "type": "error",
                    "error_type": "depth_limit",
                    "message": summary,
                }
                final_text = summary
                break

            # Terminal response — extract text and exit loop
            if resp.stop_reason != "tool_use":
                final_text = "".join(b.text for b in resp.content if b.type == "text")
                break

            # --- Parse tool calls from this response ---
            assistant_content = []
            tool_blocks = []

            for block in resp.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                    tool_blocks.append(block)

            msgs.append({"role": "assistant", "content": assistant_content})

            # --- Classify tools: reads parallel, writes serial ---
            reads = [b for b in tool_blocks if b.name in READ_TOOLS]
            writes = [b for b in tool_blocks if b.name not in READ_TOOLS]

            result_map: dict[str, str] = {}  # tool_use_id → result string

            # Execute reads in parallel
            if reads:
                read_results = await self._execute_reads(reads, user_id, interface)
                for block, result in zip(reads, read_results):
                    result_str = self._format_result(block.name, result)
                    result_map[block.id] = result_str
                    yield {"type": "tool_result", "tool": block.name, "result": result_str[:200]}

            # Execute writes serially — stop on first failure
            if writes:
                for block in writes:
                    result = await self._execute_write(block, user_id, interface)
                    result_str = self._format_result(block.name, result)
                    result_map[block.id] = result_str
                    yield {"type": "tool_result", "tool": block.name, "result": result_str[:200]}
                    if isinstance(result, Exception) or (isinstance(result_str, str) and result_str.startswith('{"tool":')):
                        yield {
                            "type": "error",
                            "error_type": "write_failure",
                            "message": f"Write tool '{block.name}' failed — subsequent writes in this response were skipped.",
                            "tool": block.name,
                            "detail": result_str,
                        }
                        break

            # Build tool result messages in original call order
            for block in tool_blocks:
                if block.id not in result_map:
                    continue
                msgs.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_map[block.id],
                    }],
                })

            # --- Follow-up model call ---
            follow = await self._call_with_retry(msgs, sys_prompt)
            if follow is None:
                partial = "".join(b.text for b in resp.content if b.type == "text")
                msg = "Lost connection to inference backend mid-task."
                if partial:
                    msg += f" Partial response: {partial}"
                yield {"type": "error", "error_type": "switchboard_failure", "message": msg}
                final_text = msg
                break

            # Check for hallucinated tool names — nudge the model once
            if follow.stop_reason == "tool_use":
                bad_tools = [
                    b.name for b in follow.content
                    if b.type == "tool_use" and b.name not in self._valid_tool_names
                ]
                if bad_tools:
                    logger.warning(f"Hallucinated tool(s): {bad_tools}")
                    valid_sample = ", ".join(list(self._valid_tool_names)[:12]) + "..."
                    nudge = (
                        f"Your last response referenced tool(s) that don't exist: {bad_tools}. "
                        f"Available tools include: {valid_sample}. "
                        f"Please continue using only real tool names."
                    )
                    msgs.append({"role": "user", "content": nudge})
                    follow = await self._call_with_retry(msgs, sys_prompt)
                    if follow is None:
                        yield {
                            "type": "error",
                            "error_type": "model_failure",
                            "message": "Couldn't recover from invalid tool call.",
                        }
                        final_text = "Error: invalid tool name and couldn't recover."
                        break

            resp = follow
            depth += 1

        # --- Wrap up ---
        latency_ms = int((time.monotonic() - t0) * 1000)
        session_log.log_model_response(final_text, stop_reason=getattr(resp, "stop_reason", "unknown"), latency_ms=latency_ms)
        self.session_store.save_exchange(user_id, message, final_text)
        self._maybe_extract_incremental(user_id)

        yield {"type": "final_text", "text": final_text}

    # ------------------------------------------------------------------
    # Switchboard call with transient retry
    # ------------------------------------------------------------------

    async def _call_with_retry(self, msgs: list, sys_prompt: str, attempt: int = 0):
        """Call switchboard. Retries transient errors up to _RETRY_MAX times.

        Returns None on total failure (caller must yield an error event).
        """
        try:
            return await asyncio.to_thread(
                self.switchboard.call,
                messages=msgs,
                system=sys_prompt,
                tools=self.tools,
                max_tokens=self.max_output_tokens,
            )
        except Exception as e:
            if _is_transient(e) and attempt < _RETRY_MAX - 1:
                delay = _RETRY_BACKOFF[attempt]
                logger.warning(f"Transient error attempt {attempt + 1}/{_RETRY_MAX}: {e}. Retry in {delay}s.")
                await asyncio.sleep(delay)
                return await self._call_with_retry(msgs, sys_prompt, attempt + 1)
            logger.error(f"Switchboard failed after {attempt + 1} attempts: {e}")
            log_failure(context="model_call", error=str(e), recovery="Exhausted retries")
            return None

    # ------------------------------------------------------------------
    # Tool execution — parallel reads, serial writes
    # ------------------------------------------------------------------

    async def _execute_reads(self, blocks: list, user_id: str, interface: str = "unknown") -> list:
        """Execute read tools in parallel. Returns list of str-or-Exception."""
        async def _one(block):
            session_log.log_tool_call(block.name, block.input)
            logger.info(f"Tool (read,parallel): {block.name}")
            try:
                return await asyncio.to_thread(
                    execute_tool, block.name, block.input,
                    session_store=self.session_store, user_id=user_id, interface=interface,
                )
            except Exception as e:
                logger.error(f"Read tool {block.name} failed: {e}")
                return e

        return list(await asyncio.gather(*[_one(b) for b in blocks]))

    async def _execute_write(self, block, user_id: str, interface: str = "unknown"):
        """Execute a single write tool. Returns str-or-Exception."""
        session_log.log_tool_call(block.name, block.input)
        logger.info(f"Tool (write,serial): {block.name}")
        try:
            return await asyncio.to_thread(
                execute_tool, block.name, block.input,
                session_store=self.session_store, user_id=user_id, interface=interface,
            )
        except Exception as e:
            logger.error(f"Write tool {block.name} failed: {e}")
            return e

    def _format_result(self, tool_name: str, result) -> str:
        """Format tool result for the model. Exceptions become structured error JSON."""
        if isinstance(result, Exception):
            error_type = "transient" if _is_transient(result) else "tool_failure"
            return json.dumps({
                "tool": tool_name,
                "error_type": error_type,
                "message": str(result)[:300],
                "suggestion": "Try an alternative approach or rephrase the query.",
            })
        return str(result)

    # ------------------------------------------------------------------
    # Incremental extraction
    # ------------------------------------------------------------------

    def _maybe_extract_incremental(self, user_id: str) -> None:
        try:
            count = self.session_store.get_today_message_count(user_id)
            if count > 0 and count % self.incremental_every_n == 0:
                logger.info(f"Incremental extraction triggered at {count} messages")
                conv_text = self.session_store.get_conversation_text(user_id, hours=1)
                if conv_text and len(conv_text.strip()) > 50:
                    threading.Thread(
                        target=extraction.run, args=(conv_text,), daemon=True
                    ).start()
        except Exception as e:
            logger.warning(f"Incremental extraction check failed (non-fatal): {e}")

    # ------------------------------------------------------------------
    # Passthrough helpers (used by heartbeat and interfaces)
    # ------------------------------------------------------------------

    def get_conversation_text(self, user_id, hours=24):
        return self.session_store.get_conversation_text(user_id, hours=hours)

    def get_last_message_time(self, user_id):
        return self.session_store.get_last_message_time(user_id)


# ---------------------------------------------------------------------------
# Module-level singleton (only mutable module-level state)
# ---------------------------------------------------------------------------

_relay_instance = None

def get_relay(tier=2):
    global _relay_instance
    if not _relay_instance:
        _relay_instance = RelayV3(tier)
    return _relay_instance
