"""Tests for the current RelayV3 implementation."""

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import sys

sys.modules.setdefault("openai", SimpleNamespace(OpenAI=Mock()))
sys.modules.setdefault("httpx", SimpleNamespace(get=Mock()))

sys.path.insert(0, str(Path(__file__).parent.parent))

from relay.relay import RelayV3


class TestRelayHelpers(unittest.TestCase):
    def test_build_memory_context_uses_search_results(self):
        with patch("relay.relay.storage.search_facts") as mock_search:
            relay = RelayV3.__new__(RelayV3)
            mock_search.return_value = [{
                "entity": "project_x",
                "fact": {"fact": "Project X ships next week", "category": "milestone"},
                "relevance_score": 2,
            }]

            context = RelayV3._build_memory_context(relay, "Tell me about Project X")

        self.assertIn("## Relevant Memory", context)
        self.assertIn("project_x: Project X ships next week [milestone]", context)

    def test_format_result_wraps_exceptions_as_json(self):
        relay = RelayV3.__new__(RelayV3)
        result = RelayV3._format_result(relay, "read_memory", RuntimeError("boom"))
        self.assertIn('"tool": "read_memory"', result)
        self.assertIn('"message": "boom"', result)


class TestRelayFlow(unittest.TestCase):
    def test_collect_response_returns_final_text(self):
        relay = RelayV3.__new__(RelayV3)

        async def fake_stream(*_args, **_kwargs):
            yield {"type": "start"}
            yield {"type": "final_text", "text": "hello"}

        relay.relay_stream = fake_stream
        result = asyncio.run(RelayV3._collect_response(relay, "msg", "user", None, "test"))
        self.assertEqual(result, "hello")

    def test_call_with_retry_returns_none_after_failure(self):
        relay = RelayV3.__new__(RelayV3)
        relay.tools = []
        relay.max_output_tokens = 128
        relay.switchboard = Mock()
        relay.switchboard.call.side_effect = RuntimeError("fatal")

        with patch("relay.relay.asyncio.to_thread", new=AsyncMock(side_effect=RuntimeError("fatal"))):
            with patch("relay.relay.log_failure"):
                result = asyncio.run(RelayV3._call_with_retry(relay, [], "sys"))

        self.assertIsNone(result)

    def test_execute_reads_passes_identity_context(self):
        relay = RelayV3.__new__(RelayV3)
        relay.session_store = Mock()
        block = SimpleNamespace(name="read_memory", input={"entity": "dino"})

        with patch("relay.relay.asyncio.to_thread", new=AsyncMock(return_value="ok")) as mock_to_thread:
            results = asyncio.run(RelayV3._execute_reads(relay, [block], "owner-id", "telegram"))

        self.assertEqual(results, ["ok"])
        args, kwargs = mock_to_thread.call_args
        self.assertEqual(args[0].__name__, "execute_tool")
        self.assertEqual(kwargs["user_id"], "owner-id")
        self.assertEqual(kwargs["interface"], "telegram")


if __name__ == "__main__":
    unittest.main()
