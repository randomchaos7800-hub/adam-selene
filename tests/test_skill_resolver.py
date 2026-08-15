import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay import skill_resolver


class TestBuildSkillPrompt(unittest.TestCase):
    def test_includes_self_learning_nudge(self):
        with patch.object(skill_resolver, "RESOLVER_PATH", Path("/nonexistent/RESOLVER.md")):
            prompt = skill_resolver.build_skill_prompt()
        self.assertIn("skill_manage", prompt)

    def test_appends_nudge_after_resolver_content(self):
        with tempfile.TemporaryDirectory() as d:
            resolver_path = Path(d) / "RESOLVER.md"
            resolver_path.write_text("RESOLVER BODY")
            with patch.object(skill_resolver, "RESOLVER_PATH", resolver_path):
                prompt = skill_resolver.build_skill_prompt()
        self.assertTrue(prompt.startswith("RESOLVER BODY"))
        self.assertIn(skill_resolver.SELF_LEARNING_COMPACT, prompt)


class TestFilterToolDefinitions(unittest.TestCase):
    def _defs(self, names):
        return [{"name": n} for n in names]

    def test_list_capabilities_always_reaches_the_model(self):
        # Regression test: no skill's frontmatter declares
        # list_capabilities, and ALWAYS_ON_SKILLS always contributes a
        # non-empty tools set, so the "expose everything" fallback never
        # fires — without ALWAYS_AVAILABLE_TOOLS, this tool was silently
        # filtered out on every real turn despite being fully implemented.
        with patch.object(skill_resolver, "get_skill_tools", return_value={"read_memory"}):
            result = skill_resolver.filter_tool_definitions(
                self._defs(["read_memory", "write_memory", "list_capabilities"]),
                ["memory-ops"],
            )
        names = {t["name"] for t in result}
        self.assertIn("list_capabilities", names)
        self.assertIn("read_memory", names)
        self.assertNotIn("write_memory", names)

    def test_empty_allowed_tools_still_falls_back_to_everything(self):
        with patch.object(skill_resolver, "get_skill_tools", return_value=set()):
            result = skill_resolver.filter_tool_definitions(
                self._defs(["a", "b"]), ["some-skill"],
            )
        self.assertEqual({t["name"] for t in result}, {"a", "b"})


class TestBumpUsage(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.usage_path = Path(self.tempdir.name) / ".usage.json"
        self.patcher = patch.object(skill_resolver, "USAGE_PATH", self.usage_path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.tempdir.cleanup)

    def test_creates_usage_file_with_counts(self):
        skill_resolver._bump_usage(["query"])
        data = json.loads(self.usage_path.read_text())
        self.assertEqual(data["query"]["use_count"], 1)
        self.assertIsNotNone(data["query"]["last_used"])

    def test_increments_existing_count(self):
        skill_resolver._bump_usage(["query"])
        skill_resolver._bump_usage(["query"])
        data = json.loads(self.usage_path.read_text())
        self.assertEqual(data["query"]["use_count"], 2)

    def test_skips_always_on_skills(self):
        skill_resolver._bump_usage(["signal-detector", "memory-ops", "query"])
        data = json.loads(self.usage_path.read_text())
        self.assertNotIn("signal-detector", data)
        self.assertNotIn("memory-ops", data)
        self.assertIn("query", data)

    def test_never_raises_on_write_failure(self):
        with patch.object(skill_resolver, "USAGE_PATH", Path("/nonexistent-dir/.usage.json")):
            try:
                skill_resolver._bump_usage(["query"])
            except Exception as e:
                self.fail(f"_bump_usage raised unexpectedly: {e}")

    def test_concurrent_bumps_do_not_lose_increments(self):
        # Regression test for the unlocked read-modify-write race: without
        # locking, N concurrent _bump_usage calls for the same skill would
        # frequently total less than N due to lost updates.
        import threading

        n_threads = 20

        def _bump():
            skill_resolver._bump_usage(["query"])

        threads = [threading.Thread(target=_bump) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        data = json.loads(self.usage_path.read_text())
        self.assertEqual(data["query"]["use_count"], n_threads)


class TestResolveSkillsUsageIntegration(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.usage_path = Path(self.tempdir.name) / ".usage.json"
        self.patcher = patch.object(skill_resolver, "USAGE_PATH", self.usage_path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.tempdir.cleanup)

    def test_resolve_skills_bumps_usage_for_matched_specific_skills(self):
        with patch.object(skill_resolver, "_build_trigger_map", lambda: None), \
             patch.object(skill_resolver, "_TRIGGER_MAP", {}):
            skill_resolver.resolve_skills("some message with no trigger match")
        # Falls back to "query" as the default specific skill.
        data = json.loads(self.usage_path.read_text())
        self.assertIn("query", data)


if __name__ == "__main__":
    unittest.main()
