import asyncio
import json
import unittest
from unittest.mock import Mock, patch

from relay.goal_loop import GoalLoop, _is_enabled, _max_turns, _extract_first_json_object


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestGoalLoopSettings(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch("relay.goal_loop.config.load_settings", return_value={}):
            self.assertFalse(_is_enabled())

    def test_enabled_when_configured(self):
        with patch("relay.goal_loop.config.load_settings", return_value={"goals": {"enabled": True}}):
            self.assertTrue(_is_enabled())

    def test_default_max_turns(self):
        with patch("relay.goal_loop.config.load_settings", return_value={}):
            self.assertEqual(_max_turns(), 10)

    def test_configured_max_turns(self):
        with patch("relay.goal_loop.config.load_settings", return_value={"goals": {"max_turns": 3}}):
            self.assertEqual(_max_turns(), 3)


class TestGoalLoopStart(unittest.TestCase):
    def test_start_refuses_when_disabled(self):
        loop = GoalLoop()
        with patch("relay.goal_loop.config.load_settings", return_value={"goals": {"enabled": False}}):
            result = loop.start("do the thing", "owner", "telegram")
        self.assertFalse(result["started"])
        self.assertIn("disabled", result["error"])

    def test_start_refuses_when_already_running(self):
        loop = GoalLoop()
        loop._state = Mock(status="running", goal="existing goal")
        with patch("relay.goal_loop.config.load_settings", return_value={"goals": {"enabled": True}}):
            result = loop.start("new goal", "owner", "telegram")
        self.assertFalse(result["started"])
        self.assertIn("already running", result["error"])


class TestGoalLoopControls(unittest.TestCase):
    def test_pause_with_no_active_goal(self):
        loop = GoalLoop()
        result = loop.pause()
        self.assertFalse(result["ok"])

    def test_stop_with_no_active_goal(self):
        loop = GoalLoop()
        result = loop.stop()
        self.assertFalse(result["ok"])

    def test_resume_with_no_paused_goal(self):
        loop = GoalLoop()
        result = loop.resume()
        self.assertFalse(result["ok"])

    def test_status_when_inactive(self):
        loop = GoalLoop()
        self.assertEqual(loop.status(), {"active": False})

    def test_pause_then_stop_transitions(self):
        loop = GoalLoop()
        loop._state = Mock(status="running", goal="g", turn=2, to_dict=lambda: {"goal": "g", "status": "running"})
        loop._task = None
        pause_result = loop.pause()
        self.assertTrue(pause_result["ok"])
        self.assertEqual(loop._state.status, "paused")


class TestExtractFirstJsonObject(unittest.TestCase):
    def test_extracts_simple_object(self):
        self.assertEqual(_extract_first_json_object('{"done": true}'), '{"done": true}')

    def test_extracts_object_embedded_in_prose(self):
        text = 'Here is my verdict: {"done": true, "reason": "finished"} — hope that helps.'
        self.assertEqual(_extract_first_json_object(text), '{"done": true, "reason": "finished"}')

    def test_returns_none_when_no_object_present(self):
        self.assertIsNone(_extract_first_json_object("no json here at all"))

    def test_returns_none_on_unterminated_object(self):
        self.assertIsNone(_extract_first_json_object('{"done": true'))

    def test_ignores_braces_inside_string_values(self):
        text = '{"done": false, "reason": "the {config} block needs work"}'
        result = _extract_first_json_object(text)
        self.assertEqual(json.loads(result), {"done": False, "reason": "the {config} block needs work"})

    def test_stops_at_first_complete_object_not_greedy_to_the_last_brace(self):
        # The actual regression this guards: a response containing TWO
        # separate JSON-like fragments must not get spliced into one
        # malformed blob spanning both of them.
        text = 'Example format: {"done": false} — my actual answer: {"done": true, "reason": "done now"}'
        result = _extract_first_json_object(text)
        self.assertEqual(json.loads(result), {"done": False})

    def test_handles_escaped_quotes_inside_string_values(self):
        text = r'{"done": true, "reason": "user said \"stop\" explicitly"}'
        result = _extract_first_json_object(text)
        self.assertEqual(json.loads(result)["reason"], 'user said "stop" explicitly')


class TestGoalReachedJudge(unittest.TestCase):
    def test_judge_returns_true_on_done_verdict(self):
        loop = GoalLoop()
        state = Mock(goal="g", turn=1, max_turns=10, reason="")
        switchboard = Mock()
        response = Mock()
        response.content = [Mock(text='{"done": true, "reason": "finished"}')]
        switchboard.call.return_value = response

        result = _run(loop._goal_reached(switchboard, state, "some response text"))
        self.assertTrue(result)
        self.assertEqual(state.reason, "finished")

    def test_judge_returns_false_on_not_done_verdict(self):
        loop = GoalLoop()
        state = Mock(goal="g", turn=1, max_turns=10, reason="")
        switchboard = Mock()
        response = Mock()
        response.content = [Mock(text='{"done": false, "reason": "still working"}')]
        switchboard.call.return_value = response

        result = _run(loop._goal_reached(switchboard, state, "some response text"))
        self.assertFalse(result)

    def test_judge_fails_open_on_exception(self):
        loop = GoalLoop()
        state = Mock(goal="g", turn=1, max_turns=10, reason="")
        switchboard = Mock()
        switchboard.call.side_effect = Exception("backend down")

        result = _run(loop._goal_reached(switchboard, state, "some response text"))
        self.assertFalse(result)  # fails open = keeps going, not "done"

    def test_judge_fails_open_on_unparseable_response(self):
        loop = GoalLoop()
        state = Mock(goal="g", turn=1, max_turns=10, reason="")
        switchboard = Mock()
        response = Mock()
        response.content = [Mock(text="not json at all")]
        switchboard.call.return_value = response

        result = _run(loop._goal_reached(switchboard, state, "some response text"))
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
