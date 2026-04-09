"""
Test suite for Switchboard API routing and spend tracking.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import tempfile
import sqlite3
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from relay.switchboard import Switchboard, BudgetExceededError


class TestSwitchboard(unittest.TestCase):
    """Test cases for Switchboard class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.memory_path = Path(self.temp_dir.name)

    def tearDown(self):
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    def test_init_creates_database(self):
        """Test that __init__ creates the SQLite database."""
        switchboard = Switchboard(self.memory_path)
        db_path = self.memory_path / "spend.db"
        self.assertTrue(db_path.exists(), "Database file should be created")

        # Verify table exists
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='spends'"
        )
        result = cursor.fetchone()
        conn.close()
        self.assertIsNotNone(result, "spends table should exist")

    def test_tier_1_routes_to_gemini_flash(self):
        """Test that tier 1 routes to google/gemini-flash-1.5."""
        switchboard = Switchboard(self.memory_path, daily_budget_usd=10.0)

        # Mock response
        mock_response = MagicMock()
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch("anthropic.Anthropic") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            switchboard.call(
                tier=1,
                messages=[{"role": "user", "content": "test"}],
                system="test system",
            )

            # Verify correct client was created
            mock_client_class.assert_called_once()
            call_kwargs = mock_client_class.call_args[1]
            self.assertEqual(call_kwargs["base_url"], "https://openrouter.ai/api/v1")

            # Verify correct model was used
            messages_call = mock_client.messages.create.call_args
            self.assertEqual(messages_call[1]["model"], "google/gemini-flash-1.5")

    def test_tier_2_routes_to_haiku(self):
        """Test that tier 2 routes to anthropic/claude-3.5-haiku."""
        switchboard = Switchboard(self.memory_path, daily_budget_usd=10.0)

        # Mock response
        mock_response = MagicMock()
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch("anthropic.Anthropic") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            switchboard.call(
                tier=2,
                messages=[{"role": "user", "content": "test"}],
                system="test system",
            )

            # Verify correct model was used
            messages_call = mock_client.messages.create.call_args
            self.assertEqual(messages_call[1]["model"], "anthropic/claude-3.5-haiku")

    def test_spend_logging_writes_to_db(self):
        """Test that spend is logged to the database."""
        switchboard = Switchboard(self.memory_path, daily_budget_usd=10.0)

        # Mock response
        mock_response = MagicMock()
        mock_response.usage.input_tokens = 1000
        mock_response.usage.output_tokens = 500

        with patch("anthropic.Anthropic"):
            with patch.object(switchboard, "call", wraps=switchboard.call):
                # We'll manually call the logging to test it
                switchboard._log_spend(
                    tier=1,
                    model="google/gemini-flash-1.5",
                    tokens_in=1000,
                    tokens_out=500,
                    cost_usd=0.00001,
                )

        # Verify data was logged
        conn = sqlite3.connect(switchboard.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM spends")
        rows = cursor.fetchall()
        conn.close()

        self.assertEqual(len(rows), 1, "One spend record should be logged")
        _, timestamp, tier, model, tokens_in, tokens_out, cost = rows[0]
        self.assertEqual(tier, 1)
        self.assertEqual(model, "google/gemini-flash-1.5")
        self.assertEqual(tokens_in, 1000)
        self.assertEqual(tokens_out, 500)
        self.assertAlmostEqual(cost, 0.00001, places=6)

    def test_get_daily_spend_sums_correctly(self):
        """Test that get_daily_spend sums today's costs correctly."""
        switchboard = Switchboard(self.memory_path, daily_budget_usd=10.0)

        # Log multiple spends
        switchboard._log_spend(
            tier=1,
            model="google/gemini-flash-1.5",
            tokens_in=1000,
            tokens_out=500,
            cost_usd=0.00001,
        )
        switchboard._log_spend(
            tier=2,
            model="anthropic/claude-3.5-haiku",
            tokens_in=500,
            tokens_out=250,
            cost_usd=0.00025,
        )
        switchboard._log_spend(
            tier=2,
            model="anthropic/claude-3.5-haiku",
            tokens_in=250,
            tokens_out=125,
            cost_usd=0.00015,
        )

        total_spend = switchboard.get_daily_spend()
        expected_total = 0.00001 + 0.00025 + 0.00015
        self.assertAlmostEqual(total_spend, expected_total, places=6)

    def test_budget_exceeded_error_raised_at_limit(self):
        """Test that BudgetExceededError is raised when spend >= budget."""
        switchboard = Switchboard(self.memory_path, daily_budget_usd=1.0)

        # Log spend that equals budget
        switchboard._log_spend(
            tier=2,
            model="anthropic/claude-3.5-haiku",
            tokens_in=4_000_000,
            tokens_out=1_000_000,
            cost_usd=1.0,
        )

        # Try to make another call - should raise error
        mock_response = MagicMock()
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch("anthropic.Anthropic"):
            with self.assertRaises(BudgetExceededError):
                switchboard.call(
                    tier=1,
                    messages=[{"role": "user", "content": "test"}],
                    system="test system",
                )

    def test_budget_exceeded_error_raised_over_limit(self):
        """Test that BudgetExceededError is raised when spend > budget."""
        switchboard = Switchboard(self.memory_path, daily_budget_usd=0.50)

        # Log spend that exceeds budget
        switchboard._log_spend(
            tier=2,
            model="anthropic/claude-3.5-haiku",
            tokens_in=2_000_000,
            tokens_out=500_000,
            cost_usd=0.75,
        )

        # Try to make another call - should raise error
        with patch("anthropic.Anthropic"):
            with self.assertRaises(BudgetExceededError):
                switchboard.call(
                    tier=1,
                    messages=[{"role": "user", "content": "test"}],
                    system="test system",
                )

    def test_call_allowed_under_budget(self):
        """Test that call succeeds when under budget."""
        switchboard = Switchboard(self.memory_path, daily_budget_usd=10.0)

        # Log spend under budget
        switchboard._log_spend(
            tier=1,
            model="google/gemini-flash-1.5",
            tokens_in=1000,
            tokens_out=500,
            cost_usd=0.00001,
        )

        # Mock response
        mock_response = MagicMock()
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch("anthropic.Anthropic") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            result = switchboard.call(
                tier=2,
                messages=[{"role": "user", "content": "test"}],
                system="test system",
            )

            self.assertIsNotNone(result)

    def test_invalid_tier_raises_error(self):
        """Test that invalid tier raises ValueError."""
        switchboard = Switchboard(self.memory_path, daily_budget_usd=10.0)

        with patch("anthropic.Anthropic"):
            with self.assertRaises(ValueError):
                switchboard.call(
                    tier=99,
                    messages=[{"role": "user", "content": "test"}],
                    system="test system",
                )

    def test_cost_calculation_tier_1(self):
        """Test cost calculation for Tier 1 (Gemini Flash)."""
        switchboard = Switchboard(self.memory_path)

        # Gemini Flash: $0.00001 per 1M input and output
        cost = switchboard._calculate_cost(tier=1, tokens_in=1_000_000, tokens_out=1_000_000)
        expected = (1_000_000 / 1_000_000) * 0.00001 + (1_000_000 / 1_000_000) * 0.00001
        self.assertAlmostEqual(cost, expected, places=8)

    def test_cost_calculation_tier_2(self):
        """Test cost calculation for Tier 2 (Claude Haiku)."""
        switchboard = Switchboard(self.memory_path)

        # Haiku: $0.25/1M input, $1.25/1M output
        cost = switchboard._calculate_cost(tier=2, tokens_in=1_000_000, tokens_out=1_000_000)
        expected = (1_000_000 / 1_000_000) * 0.25 + (1_000_000 / 1_000_000) * 1.25
        self.assertAlmostEqual(cost, expected, places=8)

    def test_call_with_tools(self):
        """Test that call passes tools parameter correctly."""
        switchboard = Switchboard(self.memory_path, daily_budget_usd=10.0)

        tools = [{"name": "test_tool", "description": "A test tool"}]

        mock_response = MagicMock()
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch("anthropic.Anthropic") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            switchboard.call(
                tier=1,
                messages=[{"role": "user", "content": "test"}],
                system="test system",
                tools=tools,
            )

            # Verify tools were passed
            messages_call = mock_client.messages.create.call_args
            self.assertEqual(messages_call[1]["tools"], tools)

    def test_call_with_custom_max_tokens(self):
        """Test that call uses custom max_tokens."""
        switchboard = Switchboard(self.memory_path, daily_budget_usd=10.0)

        mock_response = MagicMock()
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch("anthropic.Anthropic") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            switchboard.call(
                tier=1,
                messages=[{"role": "user", "content": "test"}],
                system="test system",
                max_tokens=2048,
            )

            # Verify max_tokens were passed
            messages_call = mock_client.messages.create.call_args
            self.assertEqual(messages_call[1]["max_tokens"], 2048)

    def test_daily_spend_returns_zero_when_no_logs(self):
        """Test that get_daily_spend returns 0 when no logs exist."""
        switchboard = Switchboard(self.memory_path)
        daily_spend = switchboard.get_daily_spend()
        self.assertEqual(daily_spend, 0.0)

    def test_api_key_from_environment(self):
        """Test that API key is read from environment."""
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test_key_123"}):
            switchboard = Switchboard(self.memory_path)
            self.assertEqual(switchboard.api_key, "test_key_123")


if __name__ == "__main__":
    unittest.main()
