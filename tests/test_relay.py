"""Tests for RelayV3 (constitution + switchboard integration).

Tests:
1. Constitution is loaded on init (mock files)
2. System prompt includes constitution when present
3. BudgetExceededError returns friendly message
4. Tool loop still works (mock switchboard)
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, call
import tempfile
import json
import sqlite3
from datetime import datetime

# Import the relay module and dependencies
from relay.relay import RelayV3, get_relay
from relay.switchboard import BudgetExceededError
from relay.constitution import ConstitutionLoader


class TestRelayV3Init:
    """Test RelayV3 initialization with constitution and switchboard."""

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_constitution_loaded_on_init_when_exists(self, tmp_path):
        """Test that constitution is loaded and validated on init."""
        # Setup mock adam-selene-memory directory
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        constitution_dir = agent_memory / "constitution"
        constitution_dir.mkdir()

        # Create constitution file
        constitution_content = "You must uphold these principles:\n1. Do no harm\n2. Be truthful"
        constitution_file = constitution_dir / "L0.md"
        constitution_file.write_text(constitution_content)

        # Create hash file
        import hashlib
        content_hash = hashlib.sha256(constitution_content.encode("utf-8")).hexdigest()
        hash_file = constitution_dir / "L0.hash"
        hash_file.write_text(content_hash)

        # Patch Path.home() to return our temp directory
        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore"):
                    relay = RelayV3(api_key="test-key")

                    # Assert constitution was loaded
                    assert relay.constitution is not None
                    assert relay.constitution == constitution_content

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_constitution_not_loaded_when_missing(self, tmp_path):
        """Test that missing constitution doesn't crash init."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore"):
                    relay = RelayV3(api_key="test-key")

                    # Assert constitution is None
                    assert relay.constitution is None

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_switchboard_initialized_on_init(self, tmp_path):
        """Test that switchboard is initialized on startup."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore"):
                    with patch("relay.relay.Switchboard") as mock_switchboard_class:
                        relay = RelayV3(api_key="test-key")

                        # Assert switchboard was created
                        assert relay.switchboard is not None
                        mock_switchboard_class.assert_called_once()


class TestSystemPromptWithConstitution:
    """Test that constitution is prepended to system prompt."""

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_system_prompt_includes_constitution(self, tmp_path):
        """Test that _build_system_prompt includes constitution."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        constitution_dir = agent_memory / "constitution"
        constitution_dir.mkdir()

        # Create constitution file
        constitution_text = "Constitution:\nPrinciple 1"
        constitution_file = constitution_dir / "L0.md"
        constitution_file.write_text(constitution_text)

        # Create hash
        import hashlib
        content_hash = hashlib.sha256(constitution_text.encode("utf-8")).hexdigest()
        hash_file = constitution_dir / "L0.hash"
        hash_file.write_text(content_hash)

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore"):
                    relay = RelayV3(api_key="test-key")
                    prompt = relay._build_system_prompt()

                    # Assert constitution is in prompt
                    assert constitution_text in prompt
                    assert "---" in prompt  # Separator present

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_system_prompt_without_constitution(self, tmp_path):
        """Test system prompt when constitution is absent."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore"):
                    relay = RelayV3(api_key="test-key")
                    prompt = relay._build_system_prompt()

                    # Assert base prompt is used
                    assert prompt  # Base prompt content exists

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_system_prompt_prefers_custom_memory_prompt(self, tmp_path):
        """Test that memory-based custom prompt takes precedence."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        # Create constitution
        constitution_dir = agent_memory / "constitution"
        constitution_dir.mkdir()
        constitution_file = constitution_dir / "L0.md"
        constitution_file.write_text("Constitution text")

        import hashlib
        content_hash = hashlib.sha256(b"Constitution text").hexdigest()
        hash_file = constitution_dir / "L0.hash"
        hash_file.write_text(content_hash)

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore"):
                    relay = RelayV3(api_key="test-key")

                    # Manually set a custom constitution to test the prepending logic
                    relay.constitution = "Constitution text"

                    prompt = relay._build_system_prompt()

                    # Assert constitution is prepended to base prompt
                    assert "Constitution text" in prompt
                    assert "---" in prompt  # The separator should be there


class TestBudgetExceededError:
    """Test handling of BudgetExceededError from switchboard."""

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_budget_exceeded_returns_friendly_message(self, tmp_path):
        """Test that BudgetExceededError is caught and returns friendly message."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        # Create a temporary database for SessionStore
        db_path = agent_memory / "sessions.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                session_date TEXT
            )
        """)
        conn.commit()
        conn.close()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore") as mock_store_class:
                    mock_store = Mock()
                    mock_store_class.return_value = mock_store
                    mock_store.get_session_snapshot.return_value = []
                    mock_store.save_exchange = Mock()

                    with patch("relay.relay.Switchboard") as mock_switchboard_class:
                        mock_switchboard = Mock()
                        mock_switchboard_class.return_value = mock_switchboard

                        # Make switchboard.call raise BudgetExceededError
                        mock_switchboard.call.side_effect = BudgetExceededError(
                            "Daily budget exceeded. Spent: $3.33, Budget: $3.33"
                        )

                        relay = RelayV3(api_key="test-key")
                        response = relay.respond("test message")

                        # Assert friendly message is returned
                        assert "I've hit my daily limit" in response
                        assert "Let's talk tomorrow" in response

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_budget_exceeded_on_follow_up(self, tmp_path):
        """Test BudgetExceededError during follow-up call (tool loop)."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        # Create database
        db_path = agent_memory / "sessions.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                session_date TEXT
            )
        """)
        conn.commit()
        conn.close()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore") as mock_store_class:
                    mock_store = Mock()
                    mock_store_class.return_value = mock_store
                    mock_store.get_session_snapshot.return_value = []
                    mock_store.save_exchange = Mock()

                    with patch("relay.relay.Switchboard") as mock_switchboard_class:
                        mock_switchboard = Mock()
                        mock_switchboard_class.return_value = mock_switchboard

                        # First call succeeds with tool_use, second call (follow-up) raises BudgetExceededError
                        first_response = Mock()
                        first_response.stop_reason = "tool_use"
                        first_response.content = [
                            Mock(
                                type="tool_use",
                                name="read_memory",
                                id="tool-1",
                                input={"entity": "test"}
                            )
                        ]

                        mock_switchboard.call.side_effect = [
                            first_response,  # First call
                            BudgetExceededError("Budget exceeded")  # Follow-up fails
                        ]

                        with patch("relay.relay.execute_tool") as mock_execute:
                            mock_execute.return_value = "Tool result"

                            relay = RelayV3(api_key="test-key")
                            response = relay.respond("test message")

                            # Assert friendly message for budget error
                            assert "I've hit my daily limit" in response


class TestToolLoop:
    """Test that tool loop still works with switchboard."""

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_tool_use_response_executes_tool(self, tmp_path):
        """Test that tool_use stop_reason triggers tool execution."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        # Create database
        db_path = agent_memory / "sessions.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                session_date TEXT
            )
        """)
        conn.commit()
        conn.close()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore") as mock_store_class:
                    mock_store = Mock()
                    mock_store_class.return_value = mock_store
                    mock_store.get_session_snapshot.return_value = []
                    mock_store.save_exchange = Mock()

                    with patch("relay.relay.Switchboard") as mock_switchboard_class:
                        mock_switchboard = Mock()
                        mock_switchboard_class.return_value = mock_switchboard

                        # First response: tool_use
                        tool_response = Mock()
                        tool_response.stop_reason = "tool_use"
                        tool_response.content = [
                            Mock(
                                type="tool_use",
                                name="read_memory",
                                id="tool-123",
                                input={"entity": "alice"}
                            )
                        ]

                        # Second response: text
                        text_response = Mock()
                        text_response.stop_reason = "end_turn"
                        text_response.content = [Mock(text="Found info about alice")]

                        mock_switchboard.call.side_effect = [tool_response, text_response]

                        with patch("relay.relay.execute_tool") as mock_execute:
                            mock_execute.return_value = "Alice is awesome"

                            relay = RelayV3(api_key="test-key")
                            response = relay.respond("Tell me about alice")

                            # Assert tool was executed (proves tool loop works)
                            assert mock_execute.called, "execute_tool should be called for tool_use responses"
                            assert mock_execute.call_count == 1

                            # Assert response includes text from final response
                            assert "Found info about alice" in response

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_text_response_skips_tool_loop(self, tmp_path):
        """Test that text response doesn't enter tool loop."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        # Create database
        db_path = agent_memory / "sessions.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                session_date TEXT
            )
        """)
        conn.commit()
        conn.close()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore") as mock_store_class:
                    mock_store = Mock()
                    mock_store_class.return_value = mock_store
                    mock_store.get_session_snapshot.return_value = []
                    mock_store.save_exchange = Mock()

                    with patch("relay.relay.Switchboard") as mock_switchboard_class:
                        mock_switchboard = Mock()
                        mock_switchboard_class.return_value = mock_switchboard

                        # Direct text response
                        text_response = Mock()
                        text_response.stop_reason = "end_turn"
                        text_response.content = [Mock(text="Hello, I'm your assistant!")]

                        mock_switchboard.call.return_value = text_response

                        relay = RelayV3(api_key="test-key")
                        response = relay.respond("Hello")

                        # Assert only one API call (no tool loop)
                        assert mock_switchboard.call.call_count == 1
                        assert "Hello, I'm your assistant!" in response

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_multiple_tool_calls_in_single_response(self, tmp_path):
        """Test handling multiple tool calls in one response."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        # Create database
        db_path = agent_memory / "sessions.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                session_date TEXT
            )
        """)
        conn.commit()
        conn.close()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore") as mock_store_class:
                    mock_store = Mock()
                    mock_store_class.return_value = mock_store
                    mock_store.get_session_snapshot.return_value = []
                    mock_store.save_exchange = Mock()

                    with patch("relay.relay.Switchboard") as mock_switchboard_class:
                        mock_switchboard = Mock()
                        mock_switchboard_class.return_value = mock_switchboard

                        # Response with two tool calls
                        tool_response = Mock()
                        tool_response.stop_reason = "tool_use"
                        tool_response.content = [
                            Mock(
                                type="tool_use",
                                name="read_memory",
                                id="tool-1",
                                input={"entity": "alice"}
                            ),
                            Mock(
                                type="tool_use",
                                name="read_memory",
                                id="tool-2",
                                input={"entity": "bob"}
                            )
                        ]

                        # Final text response
                        text_response = Mock()
                        text_response.stop_reason = "end_turn"
                        text_response.content = [Mock(text="Both found")]

                        mock_switchboard.call.side_effect = [tool_response, text_response]

                        with patch("relay.relay.execute_tool") as mock_execute:
                            mock_execute.return_value = "Found"

                            relay = RelayV3(api_key="test-key")
                            response = relay.respond("Compare alice and bob")

                            # Assert both tools executed
                            assert mock_execute.call_count == 2


class TestSingleton:
    """Test relay singleton pattern."""

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_get_relay_creates_singleton(self, tmp_path):
        """Test that get_relay creates a singleton instance."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore"):
                    # Import fresh to test singleton
                    import relay.relay as relay_module
                    relay_module._relay_instance = None  # Reset singleton

                    relay1 = relay_module.get_relay()
                    relay2 = relay_module.get_relay()

                    # Assert same instance
                    assert relay1 is relay2

                    # Cleanup
                    relay_module._relay_instance = None


class TestSessionPersistence:
    """Test that exchanges are persisted to session store."""

    @patch.dict(sys.modules, {"memory": MagicMock(), "memory.storage": MagicMock()})
    def test_respond_saves_exchange(self, tmp_path):
        """Test that respond() saves message exchange to session store."""
        agent_memory = tmp_path / "adam-selene-memory"
        agent_memory.mkdir()

        # Create database
        db_path = agent_memory / "sessions.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                session_date TEXT
            )
        """)
        conn.commit()
        conn.close()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("relay.relay.anthropic.Anthropic"):
                with patch("relay.relay.SessionStore") as mock_store_class:
                    mock_store = Mock()
                    mock_store_class.return_value = mock_store
                    mock_store.get_session_snapshot.return_value = []
                    mock_store.save_exchange = Mock()

                    with patch("relay.relay.Switchboard") as mock_switchboard_class:
                        mock_switchboard = Mock()
                        mock_switchboard_class.return_value = mock_switchboard

                        text_response = Mock()
                        text_response.stop_reason = "end_turn"
                        text_response.content = [Mock(text="Response text")]

                        mock_switchboard.call.return_value = text_response

                        relay = RelayV3(api_key="test-key")
                        relay.respond("User message", user_id="test_user")

                        # Assert save_exchange was called
                        mock_store.save_exchange.assert_called_once()
                        call_args = mock_store.save_exchange.call_args[0]
                        assert call_args[0] == "test_user"
                        assert call_args[1] == "User message"
                        assert "Response text" in call_args[2]
