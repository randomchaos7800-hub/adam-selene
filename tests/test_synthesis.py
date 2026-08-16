import unittest
from unittest.mock import Mock, patch

from memory.synthesis import Synthesizer


class TestSynthesizeEntityAuthorityMarking(unittest.TestCase):
    """Verifies the authority-collapse fix: a low-authority fact must be
    visibly marked in what the synthesis model sees, not silently blended
    in with the same confidence as everything else."""

    def _make_synthesizer(self):
        # Mock the OpenAI class itself rather than letting Synthesizer.__init__
        # build a real httpx-backed client — some other test module in the
        # suite (test_heartbeat.py) stubs sys.modules["httpx"] at import
        # time for its own unrelated purposes, and that stub can still be
        # active depending on test collection order, breaking a REAL
        # OpenAI() client's internals in a way unrelated to anything this
        # test actually cares about (fact-formatting logic, not the SDK).
        with patch("memory.synthesis.load_settings", return_value={"openrouter": {}}), \
             patch.dict("os.environ", {"OPENROUTER_API_KEY": "fake-key"}), \
             patch("memory.synthesis.OpenAI", return_value=Mock()):
            return Synthesizer()

    def _facts_text_passed_to_model(self, entity_data):
        # Isolate exactly the facts-formatting behavior under test — the
        # real synthesis.md prompt template's INSTRUCTIONS also mention
        # "[UNVERIFIED]" by name (that's how the model is told what it
        # means), so asserting against the full prompt would false-fail
        # on the instructional text itself, not the fact formatting.
        synth = self._make_synthesizer()
        response = Mock()
        response.choices = [Mock(message=Mock(content="a summary"))]
        with patch("memory.synthesis.storage.read_entity", return_value=entity_data), \
             patch("memory.synthesis.load_synthesis_prompt", return_value="{facts}"), \
             patch.object(synth.client.chat.completions, "create", return_value=response) as mock_create:
            synth.synthesize_entity("alice")
        return mock_create.call_args.kwargs["messages"][0]["content"]

    def test_low_authority_fact_is_marked_unverified(self):
        entity_data = {
            "category": "people",
            "summary": "",
            "recent_facts": [
                {"category": "status", "fact": "unconfirmed claim", "authority": "low", "timestamp": "2026-01-01"},
            ],
        }
        prompt = self._facts_text_passed_to_model(entity_data)
        self.assertIn("[UNVERIFIED]", prompt)
        self.assertIn("unconfirmed claim", prompt)

    def test_standard_authority_fact_is_not_marked(self):
        entity_data = {
            "category": "people",
            "summary": "",
            "recent_facts": [
                {"category": "status", "fact": "ordinary fact", "authority": "standard", "timestamp": "2026-01-01"},
            ],
        }
        prompt = self._facts_text_passed_to_model(entity_data)
        self.assertNotIn("[UNVERIFIED]", prompt)

    def test_high_authority_fact_is_not_marked(self):
        entity_data = {
            "category": "constraint",
            "summary": "",
            "recent_facts": [
                {"category": "constraint", "fact": "explicit directive", "authority": "high", "timestamp": "2026-01-01"},
            ],
        }
        prompt = self._facts_text_passed_to_model(entity_data)
        self.assertNotIn("[UNVERIFIED]", prompt)

    def test_legacy_fact_without_authority_field_is_not_marked(self):
        # Missing authority defaults to "standard"-equivalent trust via
        # is_actionable_authority()'s legacy fallback — must not
        # retroactively flag old facts as unverified.
        entity_data = {
            "category": "people",
            "summary": "",
            "recent_facts": [
                {"category": "status", "fact": "old fact", "timestamp": "2026-01-01"},
            ],
        }
        prompt = self._facts_text_passed_to_model(entity_data)
        self.assertNotIn("[UNVERIFIED]", prompt)

    def test_mixed_authority_facts_only_low_ones_marked(self):
        entity_data = {
            "category": "people",
            "summary": "",
            "recent_facts": [
                {"category": "status", "fact": "trusted fact", "authority": "standard", "timestamp": "2026-01-01"},
                {"category": "status", "fact": "sketchy fact", "authority": "low", "timestamp": "2026-01-02"},
            ],
        }
        prompt = self._facts_text_passed_to_model(entity_data)
        self.assertIn("[UNVERIFIED] sketchy fact", prompt)
        trusted_line = next(line for line in prompt.splitlines() if "trusted fact" in line)
        self.assertNotIn("[UNVERIFIED]", trusted_line)


if __name__ == "__main__":
    unittest.main()
