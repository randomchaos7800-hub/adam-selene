import unittest

from relay import capabilities


class TestCapabilities(unittest.TestCase):
    def test_trusted_interface_allows_everything(self):
        self.assertTrue(capabilities.is_allowed("run_shell", "telegram"))
        self.assertTrue(capabilities.is_allowed("vault_get", "slack"))
        self.assertTrue(capabilities.is_allowed("anything_at_all", "cli"))

    def test_untrusted_interface_restricted_to_allowlist(self):
        self.assertTrue(capabilities.is_allowed("read_memory", "irc"))
        self.assertTrue(capabilities.is_allowed("write_memory", "irc"))
        self.assertFalse(capabilities.is_allowed("run_shell", "irc"))
        self.assertFalse(capabilities.is_allowed("vault_get", "irc"))
        self.assertFalse(capabilities.is_allowed("write_my_code", "irc"))

    def test_unrecognized_interface_fails_closed(self):
        # Fail closed: an interface not in INTERFACE_TIERS is UNTRUSTED,
        # not TRUSTED — a new/misconfigured interface can't inherit full
        # privileges by omission.
        self.assertEqual(capabilities.tier_for("some_new_channel"), capabilities.UNTRUSTED)
        self.assertFalse(capabilities.is_allowed("run_shell", "some_new_channel"))
        self.assertFalse(capabilities.is_allowed("run_shell", "unknown"))

    def test_check_returns_terse_reason_without_enumerating_tools(self):
        result = capabilities.check("run_shell", "irc")
        self.assertFalse(result["allowed"])
        self.assertIn("run_shell", result["reason"])
        # Denial must not leak the full tool surface as a discovery oracle.
        self.assertNotIn("vault_get", result["reason"])

    def test_check_allows_on_trusted_interface(self):
        result = capabilities.check("run_shell", "telegram")
        self.assertTrue(result["allowed"])

    def test_list_capabilities_untrusted_shows_allowlist_only(self):
        text = capabilities.list_capabilities("irc")
        self.assertIn("read_memory", text)
        self.assertNotIn("run_shell", text)

    def test_list_capabilities_trusted_shows_full_surface_message(self):
        text = capabilities.list_capabilities("telegram")
        self.assertIn("trusted", text.lower())


if __name__ == "__main__":
    unittest.main()
