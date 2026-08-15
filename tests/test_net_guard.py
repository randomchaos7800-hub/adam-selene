import socket
import unittest
from unittest.mock import patch

from relay.net_guard import resolve_public, pin_host, _is_private_address


class TestIsPrivateAddress(unittest.TestCase):
    def test_loopback_is_private(self):
        self.assertTrue(_is_private_address("127.0.0.1"))

    def test_link_local_is_private(self):
        # Cloud metadata endpoint — the canonical SSRF target.
        self.assertTrue(_is_private_address("169.254.169.254"))

    def test_rfc1918_is_private(self):
        self.assertTrue(_is_private_address("10.0.0.5"))
        self.assertTrue(_is_private_address("192.168.1.1"))
        self.assertTrue(_is_private_address("172.16.0.1"))

    def test_public_ip_is_not_private(self):
        self.assertFalse(_is_private_address("8.8.8.8"))

    def test_ipv6_loopback_is_private(self):
        self.assertTrue(_is_private_address("::1"))

    def test_ipv4_mapped_ipv6_private_is_detected(self):
        # ::ffff:127.0.0.1 — a private v4 address smuggled through v6 mapping.
        self.assertTrue(_is_private_address("::ffff:127.0.0.1"))

    def test_unparseable_defaults_to_unsafe(self):
        self.assertTrue(_is_private_address("not-an-ip"))


class TestResolvePublic(unittest.TestCase):
    def test_rejects_non_http_scheme(self):
        ok, reason = resolve_public("file:///etc/passwd")
        self.assertFalse(ok)
        self.assertIn("scheme", reason)

    def test_rejects_missing_hostname(self):
        ok, reason = resolve_public("http://")
        self.assertFalse(ok)

    @patch("socket.getaddrinfo")
    def test_rejects_hostname_resolving_to_private_ip(self, mock_resolve):
        mock_resolve.return_value = [(socket.AF_INET, None, None, "", ("127.0.0.1", 0))]
        ok, reason = resolve_public("http://sneaky.example.com/")
        self.assertFalse(ok)
        self.assertIn("private", reason)

    @patch("socket.getaddrinfo")
    def test_allows_hostname_resolving_to_public_ip(self, mock_resolve):
        mock_resolve.return_value = [(socket.AF_INET, None, None, "", ("93.184.216.34", 0))]
        ok, reason = resolve_public("http://example.com/")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    @patch("socket.getaddrinfo")
    def test_rejects_if_any_resolved_address_is_private(self, mock_resolve):
        # DNS can return multiple A records — one bad address is enough to reject.
        mock_resolve.return_value = [
            (socket.AF_INET, None, None, "", ("93.184.216.34", 0)),
            (socket.AF_INET, None, None, "", ("10.0.0.1", 0)),
        ]
        ok, reason = resolve_public("http://mixed.example.com/")
        self.assertFalse(ok)

    @patch("socket.getaddrinfo", side_effect=socket.gaierror("no such host"))
    def test_dns_failure_is_rejected(self, mock_resolve):
        ok, reason = resolve_public("http://does-not-exist.invalid/")
        self.assertFalse(ok)
        self.assertIn("DNS", reason)


class TestPinHost(unittest.TestCase):
    @patch("socket.getaddrinfo")
    def test_pins_hostname_to_resolved_ip_during_context(self, mock_resolve):
        mock_resolve.return_value = [(socket.AF_INET, None, None, "", ("93.184.216.34", 0))]
        real_getaddrinfo = socket.getaddrinfo

        with pin_host("http://example.com/"):
            # Inside the context, a lookup for the exact same hostname
            # should resolve via the pinned shim, not a fresh DNS call.
            infos = socket.getaddrinfo("example.com", None)
            self.assertEqual(infos[0][4][0], "93.184.216.34")

        # Original resolver restored on exit.
        self.assertIs(socket.getaddrinfo, real_getaddrinfo)

    @patch("socket.getaddrinfo")
    def test_restores_resolver_even_on_exception(self, mock_resolve):
        mock_resolve.return_value = [(socket.AF_INET, None, None, "", ("93.184.216.34", 0))]
        real_getaddrinfo = socket.getaddrinfo

        with self.assertRaises(ValueError):
            with pin_host("http://example.com/"):
                raise ValueError("boom")

        self.assertIs(socket.getaddrinfo, real_getaddrinfo)


if __name__ == "__main__":
    unittest.main()
