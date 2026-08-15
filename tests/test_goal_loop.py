import asyncio
import unittest
from unittest.mock import Mock, patch

from relay.goal_loop import GoalLoop, _is_enabled, _max_turns


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
