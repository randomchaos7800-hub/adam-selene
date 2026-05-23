"""Tests for the current OpenAI-compatible switchboard implementation."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys
from types import SimpleNamespace

sys.modules.setdefault("httpx", SimpleNamespace(get=MagicMock()))
sys.modules.setdefault("openai", SimpleNamespace(OpenAI=MagicMock()))

sys.path.insert(0, str(Path(__file__).parent.parent))

from relay.switchboard import (
    OllamaResponse,
    Switchboard,
    _convert_messages,
    _convert_tools,
)


class TestMessageConversion(unittest.TestCase):
    def test_convert_tool_result_blocks_to_tool_messages(self):
        messages = [{
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "done",
            }],
        }]

        converted = _convert_messages(messages)
        self.assertEqual(converted, [{
            "role": "tool",
            "tool_call_id": "tool-1",
            "content": "done",
        }])

    def test_convert_assistant_tool_use_blocks(self):
        messages = [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "abc", "name": "read_memory", "input": {"entity": "dino"}},
            ],
        }]

        converted = _convert_messages(messages)
        self.assertEqual(converted[0]["role"], "assistant")
        self.assertEqual(converted[0]["content"], "checking")
        self.assertEqual(converted[0]["tool_calls"][0]["function"]["name"], "read_memory")

    def test_convert_tools_to_openai_function_schema(self):
        tools = [{
            "name": "read_memory",
            "description": "Read memory",
            "input_schema": {"type": "object", "properties": {"entity": {"type": "string"}}},
        }]

        converted = _convert_tools(tools)
        self.assertEqual(converted[0]["type"], "function")
        self.assertEqual(converted[0]["function"]["name"], "read_memory")


class TestSwitchboardCall(unittest.TestCase):
    @patch("relay.switchboard.OpenAI")
    def test_call_prefers_openrouter_when_key_present(self, mock_openai_class):
        pod_client = MagicMock()
        openrouter_client = MagicMock()
        mock_openai_class.side_effect = [pod_client, openrouter_client]

        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content="ok", tool_calls=[]), finish_reason="stop")]
        response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        openrouter_client.chat.completions.create.return_value = response

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            switchboard = Switchboard()
            result = switchboard.call(messages=[{"role": "user", "content": "hi"}], system="sys")

        self.assertIsInstance(result, OllamaResponse)
        openrouter_client.chat.completions.create.assert_called_once()
        pod_client.chat.completions.create.assert_not_called()

    @patch("relay.switchboard.OpenAI")
    @patch.object(Switchboard, "_ensure_local_server", return_value=True)
    def test_call_falls_back_to_local_when_openrouter_unavailable(self, _mock_ensure, mock_openai_class):
        pod_client = MagicMock()
        openrouter_client = None
        mock_openai_class.side_effect = [pod_client]

        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content="ok", tool_calls=[]), finish_reason="stop")]
        response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        pod_client.chat.completions.create.return_value = response

        with patch.dict("os.environ", {}, clear=False):
            switchboard = Switchboard()
            switchboard.openrouter_client = openrouter_client
            result = switchboard.call(messages=[{"role": "user", "content": "hi"}], system="sys")

        self.assertIsInstance(result, OllamaResponse)
        pod_client.chat.completions.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
