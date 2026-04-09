"""RelayV3 — Routes through Switchboard (Anthropic SDK)."""
import json, logging, os, threading
from pathlib import Path
from typing import Optional
from memory import storage, extraction
from relay.sessions import SessionStore
from relay.switchboard import Switchboard
from relay.tools import TOOL_DEFINITIONS, execute_tool, generate_tool_summary
from relay import session_log
from relay.working_memory import log_failure
from relay import config

logger = logging.getLogger(__name__)
PROMPT_PATH = Path(__file__).parent.parent / "config" / "agent_prompt.md"
SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.json"
MAX_TOOL_DEPTH = 40

def load_base_prompt():
    return PROMPT_PATH.read_text() if PROMPT_PATH.exists() else f"You are {config.agent_name()}."

def _load_settings() -> dict:
    return json.loads(SETTINGS_PATH.read_text()) if SETTINGS_PATH.exists() else {}

class RelayV3:
    def __init__(self, tier=None):
        """Initialize relay. tier param ignored (kept for backwards compat)."""
        memory_path = config.memory_root()
        self.switchboard = Switchboard(memory_path)
        self.session_store = SessionStore()
        storage.init_memory()
        self.tools = TOOL_DEFINITIONS

        settings = _load_settings()
        ctx = settings.get("context", {})
        self.max_output_tokens = ctx.get("max_output_tokens", 8192)
        self.incremental_every_n = settings.get("extraction", {}).get("incremental_every_n_messages", 10)
        logger.info(f"RelayV3: {self.switchboard.MODEL}, {len(self.tools)} tools, max_output={self.max_output_tokens}")

    def _build_system_prompt(self):
        p = storage.load_system_prompt_from_memory()
        base = p if p else load_base_prompt()
        return base + "\n\n---\n\n" + generate_tool_summary()

    def _build_context_messages(self, user_id):
        snap = self.session_store.get_session_snapshot(user_id)
        return [{"role": m["role"], "content": m["content"]} for m in snap] if snap else []

    def respond(self, message, user_id=None, images=None, interface="unknown"):
        if user_id is None:
            user_id = config.owner_user_id()
        # Ensure a session is open for this thread
        sid, sat = session_log.current_session()
        if not sid:
            session_log.start_session(user_id=user_id, interface=interface)

        session_log.log_user_message(message, interface=interface)

        msgs = self._build_context_messages(user_id)
        msgs.append({"role": "user", "content": message})
        sys_prompt = self._build_system_prompt()

        import time as _time
        _t0 = _time.monotonic()
        session_log.log_model_call(
            model=self.switchboard.MODEL,
            messages_count=len(msgs),
            max_tokens=self.max_output_tokens,
        )

        try:
            resp = self.switchboard.call(
                messages=msgs,
                system=sys_prompt,
                tools=self.tools,
                max_tokens=self.max_output_tokens
            )
        except Exception as e:
            session_log.log_error(str(e), context="model_call")
            logger.error(f"API call failed: {e}")
            log_failure(context="model_call", error=str(e), recovery="Returning error to user")
            return f"Sorry, error: {e}"

        latency_ms = int((_time.monotonic() - _t0) * 1000)
        text = self._process_response(resp, msgs, user_id, sys_prompt)
        session_log.log_model_response(text, stop_reason=resp.stop_reason, latency_ms=latency_ms)

        self.session_store.save_exchange(user_id, message, text)
        self._maybe_extract_incremental(user_id)
        return text

    def _maybe_extract_incremental(self, user_id: str) -> None:
        """Fire extraction in background every N messages during active conversation."""
        try:
            count = self.session_store.get_today_message_count(user_id)
            if count > 0 and count % self.incremental_every_n == 0:
                logger.info(f"Incremental extraction triggered at {count} messages")
                conv_text = self.session_store.get_conversation_text(user_id, hours=1)
                if conv_text and len(conv_text.strip()) > 50:
                    threading.Thread(
                        target=extraction.run,
                        args=(conv_text,),
                        daemon=True
                    ).start()
        except Exception as e:
            logger.warning(f"Incremental extraction check failed (non-fatal): {e}")

    def _process_response(self, resp, msgs, user_id, sys_prompt, _depth=0):
        """Process Anthropic response format."""
        if _depth >= MAX_TOOL_DEPTH:
            logger.warning(f"Tool call depth limit ({MAX_TOOL_DEPTH}) reached")
            return "[Reached maximum tool call depth — task may be too complex to complete in one pass. Try breaking it into smaller steps.]"

        has_tool_use = resp.stop_reason == "tool_use"

        if has_tool_use:
            # Build assistant message with content blocks
            assistant_content = []
            tool_calls_to_execute = []

            for block in resp.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })
                    tool_calls_to_execute.append(block)

            # Add assistant message to conversation
            msgs.append({"role": "assistant", "content": assistant_content})

            # Execute tools and build tool result messages
            for tool_block in tool_calls_to_execute:
                logger.info(f"Tool: {tool_block.name}({tool_block.input})")
                session_log.log_tool_call(tool_block.name, tool_block.input)
                result = execute_tool(
                    tool_block.name,
                    tool_block.input,
                    session_store=self.session_store,
                    user_id=user_id
                )
                session_log.log_tool_result(tool_block.name, str(result)[:200])

                # Add tool result message
                msgs.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": str(result)
                    }]
                })

            # Make follow-up call with tool results
            try:
                follow = self.switchboard.call(
                    messages=msgs,
                    system=sys_prompt,
                    tools=self.tools,
                    max_tokens=self.max_output_tokens
                )
            except Exception as e:
                logger.error(f"Follow-up API call failed: {e}")
                log_failure(context="follow_up_model_call", error=str(e), recovery="Returning error to user")
                return f"Sorry, error: {e}"

            return self._process_response(follow, msgs, user_id, sys_prompt, _depth + 1)

        # No tool use - extract text from content blocks
        text_parts = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)

        return "".join(text_parts) if text_parts else ""

    def get_conversation_text(self, user_id, hours=24):
        return self.session_store.get_conversation_text(user_id, hours=hours)

    def get_last_message_time(self, user_id):
        return self.session_store.get_last_message_time(user_id)

_relay_instance = None
def get_relay(tier=2):
    global _relay_instance
    if not _relay_instance:
        _relay_instance = RelayV3(tier)
    return _relay_instance
