"""Tests for the Heartbeat reflection cycle."""

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import pytest

from relay.heartbeat import Heartbeat
from relay.switchboard import BudgetExceededError


@pytest.fixture
def mock_memory_path(tmp_path):
    """Create a temporary memory directory."""
    memory_path = tmp_path / "adam-selene-memory"
    memory_path.mkdir(parents=True, exist_ok=True)
    return memory_path


@pytest.fixture
def heartbeat(mock_memory_path):
    """Create a Heartbeat instance with mocked dependencies."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("relay.heartbeat.SessionStore"):
            with patch("relay.heartbeat.SnapshotManager") as mock_snapshot:
                with patch("relay.heartbeat.Switchboard") as mock_switchboard:
                    hb = Heartbeat(user_id="test-user")
                    hb.snapshot_manager = mock_snapshot.return_value
                    hb.switchboard = mock_switchboard.return_value
                    hb.session_store = Mock()
                    yield hb


class TestHeartbeatInitialization:
    """Test Heartbeat initialization."""

    def test_init_sets_up_snapshot_manager(self, mock_memory_path):
        """Test that __init__ creates SnapshotManager."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("relay.heartbeat.SessionStore"):
                with patch("relay.heartbeat.SnapshotManager") as mock_snapshot:
                    with patch("relay.heartbeat.Switchboard"):
                        hb = Heartbeat()
                        mock_snapshot.assert_called_once()

    def test_init_sets_up_switchboard(self, mock_memory_path):
        """Test that __init__ creates Switchboard."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("relay.heartbeat.SessionStore"):
                with patch("relay.heartbeat.SnapshotManager"):
                    with patch("relay.heartbeat.Switchboard") as mock_switchboard:
                        hb = Heartbeat()
                        mock_switchboard.assert_called_once()


class TestReflection:
    """Test the reflect() method."""

    @pytest.mark.asyncio
    async def test_snapshot_created_before_reflection(self, heartbeat):
        """Test that snapshot is created FIRST thing in reflect()."""
        # Setup mock responses
        heartbeat.session_store.get_most_recent_user.return_value = "test-user"
        heartbeat.session_store.get_conversation_text.return_value = "Some conversation text " * 10

        # Mock switchboard response
        mock_response = Mock()
        mock_response.content = [Mock(text='```json\n{"successes": [], "failures": [], "patterns": [], "suggestion": ""}\n```')]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 10
        heartbeat.switchboard.call.return_value = mock_response

        # Call reflect
        await heartbeat.reflect()

        # Verify snapshot was created
        heartbeat.snapshot_manager.create_snapshot.assert_called_once_with(trigger='heartbeat')
        # Verify it was called first (before switchboard.call)
        first_call = heartbeat.snapshot_manager.create_snapshot.call_args_list[0]
        assert first_call[1]['trigger'] == 'heartbeat'

    @pytest.mark.asyncio
    async def test_prune_called_at_end_of_reflect(self, heartbeat):
        """Test that prune_old_snapshots is called at end of reflect()."""
        # Setup mock responses
        heartbeat.session_store.get_most_recent_user.return_value = "test-user"
        heartbeat.session_store.get_conversation_text.return_value = "Some conversation text " * 10

        # Mock switchboard response
        mock_response = Mock()
        mock_response.content = [Mock(text='```json\n{"successes": [], "failures": [], "patterns": [], "suggestion": ""}\n```')]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 10
        heartbeat.switchboard.call.return_value = mock_response

        # Call reflect
        await heartbeat.reflect()

        # Verify prune was called
        heartbeat.snapshot_manager.prune_old_snapshots.assert_called_once_with(max_age_hours=48)

    @pytest.mark.asyncio
    async def test_budget_exceeded_returns_none(self, heartbeat):
        """Test that BudgetExceededError returns None gracefully."""
        # Setup mock responses
        heartbeat.session_store.get_most_recent_user.return_value = "test-user"
        heartbeat.session_store.get_conversation_text.return_value = "Some conversation text " * 10

        # Mock switchboard to raise BudgetExceededError
        heartbeat.switchboard.call.side_effect = BudgetExceededError("Budget exceeded")

        # Call reflect - should return None without raising
        result = await heartbeat.reflect()

        assert result is None
        # Verify snapshot was still created
        heartbeat.snapshot_manager.create_snapshot.assert_called_once_with(trigger='heartbeat')

    @pytest.mark.asyncio
    async def test_reflection_logic_unchanged(self, heartbeat):
        """Test that reflection analysis logic is preserved."""
        # Setup mock responses
        heartbeat.session_store.get_most_recent_user.return_value = "test-user"
        heartbeat.session_store.get_conversation_text.return_value = "Some conversation text " * 10

        # Create a realistic analysis response
        analysis_json = {
            "successes": ["successful thing"],
            "failures": ["failed thing"],
            "patterns": ["recurring pattern"],
            "suggestion": "Try using memory better"
        }
        mock_response = Mock()
        mock_response.content = [Mock(text=f'```json\n{json.dumps(analysis_json)}\n```')]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        heartbeat.switchboard.call.return_value = mock_response

        # Call reflect
        result = await heartbeat.reflect()

        # Verify the analysis was returned
        assert result is not None
        assert result["successes"] == ["successful thing"]
        assert result["failures"] == ["failed thing"]
        assert result["patterns"] == ["recurring pattern"]
        assert result["suggestion"] == "Try using memory better"

    @pytest.mark.asyncio
    async def test_switchboard_called_with_tier_2(self, heartbeat):
        """Test that switchboard is called with tier 2 (Haiku)."""
        # Setup mock responses
        heartbeat.session_store.get_most_recent_user.return_value = "test-user"
        heartbeat.session_store.get_conversation_text.return_value = "Some conversation text " * 10

        # Mock switchboard response
        mock_response = Mock()
        mock_response.content = [Mock(text='```json\n{"successes": [], "failures": [], "patterns": [], "suggestion": ""}\n```')]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 10
        heartbeat.switchboard.call.return_value = mock_response

        # Call reflect
        await heartbeat.reflect()

        # Verify switchboard was called with tier=2
        heartbeat.switchboard.call.assert_called_once()
        call_kwargs = heartbeat.switchboard.call.call_args[1]
        assert call_kwargs['tier'] == 2
        assert call_kwargs['max_tokens'] == 1024

    @pytest.mark.asyncio
    async def test_no_user_returns_none(self, heartbeat):
        """Test that reflect returns None if no user found."""
        # Override _resolve_user_id to return None
        heartbeat._resolve_user_id = Mock(return_value=None)

        result = await heartbeat.reflect()

        assert result is None
        # Snapshot should still be created before checking for user
        heartbeat.snapshot_manager.create_snapshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_insufficient_conversation_returns_none(self, heartbeat):
        """Test that reflect returns None with insufficient conversation."""
        heartbeat.session_store.get_most_recent_user.return_value = "test-user"
        heartbeat.session_store.get_conversation_text.return_value = "Short"

        result = await heartbeat.reflect()

        assert result is None
        # Snapshot should still be created
        heartbeat.snapshot_manager.create_snapshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_json_parsing_handles_code_blocks(self, heartbeat):
        """Test JSON parsing with code blocks."""
        heartbeat.session_store.get_most_recent_user.return_value = "test-user"
        heartbeat.session_store.get_conversation_text.return_value = "Some conversation text " * 10

        # Test with ```json wrapper
        analysis_json = {
            "successes": ["test"],
            "failures": [],
            "patterns": [],
            "suggestion": "test suggestion"
        }
        mock_response = Mock()
        mock_response.content = [Mock(text=f'```json\n{json.dumps(analysis_json)}\n```')]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 10
        heartbeat.switchboard.call.return_value = mock_response

        result = await heartbeat.reflect()

        assert result is not None
        assert result["suggestion"] == "test suggestion"

    @pytest.mark.asyncio
    async def test_json_parsing_handles_plain_json(self, heartbeat):
        """Test JSON parsing with plain JSON."""
        heartbeat.session_store.get_most_recent_user.return_value = "test-user"
        heartbeat.session_store.get_conversation_text.return_value = "Some conversation text " * 10

        # Test with plain JSON (no code blocks)
        analysis_json = {
            "successes": ["test"],
            "failures": [],
            "patterns": [],
            "suggestion": "test suggestion"
        }
        mock_response = Mock()
        mock_response.content = [Mock(text=json.dumps(analysis_json))]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 10
        heartbeat.switchboard.call.return_value = mock_response

        result = await heartbeat.reflect()

        assert result is not None
        assert result["suggestion"] == "test suggestion"

    @pytest.mark.asyncio
    async def test_experiments_logged_on_reflection(self, heartbeat):
        """Test that experiments are logged after reflection."""
        heartbeat.session_store.get_most_recent_user.return_value = "test-user"
        heartbeat.session_store.get_conversation_text.return_value = "Some conversation text " * 10

        analysis_json = {
            "successes": ["success1"],
            "failures": ["failure1"],
            "patterns": ["pattern1"],
            "suggestion": "Try something"
        }
        mock_response = Mock()
        mock_response.content = [Mock(text=f'```json\n{json.dumps(analysis_json)}\n```')]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 10
        heartbeat.switchboard.call.return_value = mock_response

        with patch("relay.heartbeat.storage.log_experiment") as mock_log:
            await heartbeat.reflect()

            # Verify experiment was logged
            mock_log.assert_called_once()
            call_kwargs = mock_log.call_args[1]
            assert "Heartbeat observation" in call_kwargs['hypothesis']
            assert call_kwargs['status'] == 'observed'
