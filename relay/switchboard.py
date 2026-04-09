"""Switchboard - Routes API calls via Ollama (OpenAI-compatible).

Translates Anthropic-format messages/tools to OpenAI format, wraps responses
in Anthropic-compatible duck-typed objects so relay.py/heartbeat.py are unchanged.
"""

import json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Anthropic-compatible response duck-types
# ---------------------------------------------------------------------------

class OllamaTextBlock:
    type = "text"
    def __init__(self, text: str):
        self.text = text


class OllamaToolUseBlock:
    type = "tool_use"
    def __init__(self, id: str, name: str, input: dict):
        self.id = id
        self.name = name
        self.input = input


class OllamaUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class OllamaResponse:
    """Duck-types Anthropic Message so callers need zero changes."""

    def __init__(self, oai_response):
        choice = oai_response.choices[0]
        msg = choice.message

        # Build content blocks
        self.content = []

        if msg.content:
            self.content.append(OllamaTextBlock(msg.content))

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    input_dict = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    input_dict = {}
                self.content.append(OllamaToolUseBlock(
                    id=tc.id,
                    name=tc.function.name,
                    input=input_dict,
                ))

        # stop_reason: map OpenAI finish_reason → Anthropic stop_reason
        finish = choice.finish_reason or "stop"
        if finish == "tool_calls":
            self.stop_reason = "tool_use"
        else:
            self.stop_reason = "end_turn"

        usage = oai_response.usage
        self.usage = OllamaUsage(
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------

def _convert_messages(messages: list) -> list:
    """Convert Anthropic-format message list to OpenAI format.

    Handles:
    - Regular text messages (pass through)
    - Assistant messages with tool_use blocks → OpenAI tool_calls
    - User messages with tool_result blocks → OpenAI role=tool messages
    """
    out = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        # Simple string content — pass through
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        # List content — inspect block types
        if isinstance(content, list):
            # Check if this is a tool_result message (user role, tool results)
            if role == "user" and all(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content
            ):
                for block in content:
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        # Anthropic sometimes nests content as list of text blocks
                        result_content = " ".join(
                            b.get("text", "") for b in result_content
                            if isinstance(b, dict)
                        )
                    out.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": str(result_content),
                    })
                continue

            # Check if assistant message has tool_use blocks
            if role == "assistant":
                text_parts = []
                tool_calls = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", str(uuid.uuid4())),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })
                oai_msg = {
                    "role": "assistant",
                    "content": " ".join(text_parts) if text_parts else None,
                }
                if tool_calls:
                    oai_msg["tool_calls"] = tool_calls
                out.append(oai_msg)
                continue

            # Mixed user content (e.g. text + image) — extract text
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            out.append({"role": role, "content": " ".join(text_parts)})
            continue

        # Fallback
        out.append({"role": role, "content": str(content) if content else ""})

    return out


def _convert_tools(tools: list) -> list:
    """Convert Anthropic tool definitions to OpenAI function format."""
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        })
    return out


# ---------------------------------------------------------------------------
# Switchboard
# ---------------------------------------------------------------------------

class Switchboard:
    """Routes API calls. Primary: OpenRouter. Fallback: local llama-server."""

    def __init__(self, memory_path=None, daily_budget_usd: float = None):
        # memory_path and daily_budget_usd kept for call-site compatibility, ignored
        settings_path = Path(__file__).parent.parent / "config" / "settings.json"
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}

        local_cfg = settings.get("local", {})
        pod_url = local_cfg.get("base_url", "http://127.0.0.1:8081/v1")
        pod_model = local_cfg.get("model", "smartagent")
        self.POD_URL = pod_url
        self.MODEL = pod_model
        self.pod_client = OpenAI(base_url=pod_url, api_key="local")

        # OpenRouter fallback
        or_cfg = settings.get("openrouter", {})
        self.openrouter_model = or_cfg.get("model", "z-ai/glm-4.7-flash")
        self.openrouter_fallback_model = or_cfg.get("fallback_model", None)
        or_url = or_cfg.get("base_url", "https://openrouter.ai/api/v1")
        or_key = os.environ.get("OPENROUTER_API_KEY")
        self.openrouter_client = OpenAI(base_url=or_url, api_key=or_key) if or_key else None

        # Local server startup command (for lazy start)
        self.local_cmd = local_cfg.get("cmd", [])

        logger.info(f"Switchboard initialized (model: {self.MODEL}, pod: {self.POD_URL}, openrouter: {self.openrouter_model})")

    def _ensure_local_server(self) -> bool:
        """Start llama-server if not running. Returns True when ready."""
        health_url = self.POD_URL.replace("/v1", "/health")
        try:
            r = httpx.get(health_url, timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass

        if not self.local_cmd:
            logger.warning("No local_cmd configured, cannot start llama-server")
            return False

        logger.info("Starting llama-server (lazy start)...")
        subprocess.Popen(
            self.local_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait up to 90s for it to be ready
        for i in range(90):
            time.sleep(1)
            try:
                r = httpx.get(health_url, timeout=2)
                if r.status_code == 200:
                    logger.info(f"llama-server ready after {i+1}s")
                    return True
            except Exception:
                pass

        logger.error("llama-server failed to start within 90s")
        return False

    def call(self, tier: int = None, messages: list = None, system: str = None,
             tools: list = None, max_tokens: int = 4096, model_override: str = None) -> OllamaResponse:
        """Make an API call.

        Args:
            tier: Ignored (backwards compatibility)
            messages: List of message dicts (Anthropic format)
            system: System prompt string
            tools: Tool definitions (Anthropic format)
            max_tokens: Max response tokens

        Returns:
            OllamaResponse — duck-types Anthropic Message (.content, .stop_reason, .usage)

        Raises:
            Exception: On any API error (pod down, timeout, etc.)
        """
        # Build OpenAI-format messages
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(_convert_messages(messages or []))

        # Build kwargs
        kwargs = {
            "model": model_override or self.MODEL,
            "messages": oai_messages,
            "max_tokens": max_tokens,
        }

        if tools:
            kwargs["tools"] = _convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        logger.info(f"Ollama call: {len(oai_messages)} messages, tools={bool(tools)}")

        local_err = None

        # 1. Try OpenRouter (primary)
        if self.openrouter_client:
            or_model = model_override or self.openrouter_model
            try:
                or_kwargs = dict(kwargs)
                or_kwargs["model"] = or_model
                response = self.openrouter_client.chat.completions.create(**or_kwargs)
                logger.info(f"Response from OpenRouter ({or_model})")
                return OllamaResponse(response)
            except Exception as or_err:
                logger.warning(f"OpenRouter {or_model} failed ({or_err})")
                # Try fallback model if configured and different from primary
                if self.openrouter_fallback_model and self.openrouter_fallback_model != or_model and not model_override:
                    try:
                        fb_kwargs = dict(kwargs)
                        fb_kwargs["model"] = self.openrouter_fallback_model
                        response = self.openrouter_client.chat.completions.create(**fb_kwargs)
                        logger.info(f"Response from OpenRouter fallback ({self.openrouter_fallback_model})")
                        return OllamaResponse(response)
                    except Exception as fb_err:
                        logger.warning(f"OpenRouter fallback also failed ({fb_err}), trying local")

        # 2. Fallback: local llama-server (backup/heartbeats)
        self._ensure_local_server()
        try:
            local_kwargs = dict(kwargs)
            local_kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
            response = self.pod_client.chat.completions.create(**local_kwargs)
            logger.info("Response from local llama-server")
            return OllamaResponse(response)
        except Exception as local_err:
            logger.error(f"All backends failed. Local: {local_err}")
            raise

        return OllamaResponse(response)
