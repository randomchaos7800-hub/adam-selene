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
        ok, reason, ips = resolve_public("file:///etc/passwd")
        self.assertFalse(ok)
        self.assertIn("scheme", reason)
        self.assertEqual(ips, frozenset())

    def test_rejects_missing_hostname(self):
        ok, reason, ips = resolve_public("http://")
        self.assertFalse(ok)
        self.assertEqual(ips, frozenset())

    @patch("socket.getaddrinfo")
    def test_rejects_hostname_resolving_to_private_ip(self, mock_resolve):
        mock_resolve.return_value = [(socket.AF_INET, None, None, "", ("127.0.0.1", 0))]
        ok, reason, ips = resolve_public("http://sneaky.example.com/")
        self.assertFalse(ok)
        self.assertIn("private", reason)
        self.assertEqual(ips, frozenset())

    @patch("socket.getaddrinfo")
    def test_allows_hostname_resolving_to_public_ip(self, mock_resolve):
        mock_resolve.return_value = [(socket.AF_INET, None, None, "", ("93.184.216.34", 0))]
        ok, reason, ips = resolve_public("http://example.com/")
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(ips, frozenset({"93.184.216.34"}))

    @patch("socket.getaddrinfo")
    def test_rejects_if_any_resolved_address_is_private(self, mock_resolve):
        # DNS can return multiple A records — one bad address is enough to reject.
        mock_resolve.return_value = [
            (socket.AF_INET, None, None, "", ("93.184.216.34", 0)),
            (socket.AF_INET, None, None, "", ("10.0.0.1", 0)),
        ]
        ok, reason, ips = resolve_public("http://mixed.example.com/")
        self.assertFalse(ok)
        self.assertEqual(ips, frozenset())

    @patch("socket.getaddrinfo", side_effect=socket.gaierror("no such host"))
    def test_dns_failure_is_rejected(self, mock_resolve):
        ok, reason, ips = resolve_public("http://does-not-exist.invalid/")
        self.assertFalse(ok)
        self.assertIn("DNS", reason)


class TestPinHost(unittest.TestCase):
    def tearDown(self):
        # Belt-and-suspenders: make sure no test leaves the process-wide
        # shim installed if something went wrong mid-test.
        import relay.net_guard as ng
        if ng._shim_installed:
            socket.getaddrinfo = ng._real_getaddrinfo
            ng._shim_installed = False
            ng._pinned_hosts.clear()
            ng._pin_refcounts.clear()

    def test_does_not_reresolve_hostname_itself(self):
        # pin_host must not perform its own independent DNS lookup to
        # decide what to trust — only the caller-supplied allowed_ips.
        with patch("socket.getaddrinfo") as mock_resolve:
            mock_resolve.return_value = [(socket.AF_INET, None, None, "", ("93.184.216.34", 0))]
            with pin_host("example.com", frozenset({"93.184.216.34"})):
                pass
            # The only getaddrinfo call happens INSIDE the pinned shim when
            # something actually looks up the host — pin_host's own setup
            # must not have triggered a lookup before that.
            mock_resolve.assert_not_called()

    def test_constrains_resolution_to_allowed_ips(self):
        real = socket.getaddrinfo
        with patch("socket.getaddrinfo", wraps=lambda *a, **k: [(socket.AF_INET, None, None, "", ("93.184.216.34", 0)), (socket.AF_INET, None, None, "", ("10.0.0.1", 0))]):
            with pin_host("example.com", frozenset({"93.184.216.34"})):
                infos = socket.getaddrinfo("example.com", None)
                ips = {info[4][0] for info in infos}
                self.assertEqual(ips, {"93.184.216.34"})  # the 10.0.0.1 entry filtered out
        self.assertIs(socket.getaddrinfo, real)

    def test_rebinding_to_unvalidated_address_raises(self):
        # Simulates DNS rebinding: the address returned inside the pinned
        # window doesn't match what was validated at all.
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, None, None, "", ("10.0.0.1", 0))]):
            with pin_host("example.com", frozenset({"93.184.216.34"})):
                with self.assertRaises(socket.gaierror):
                    socket.getaddrinfo("example.com", None)

    def test_unrelated_hostname_passes_through_unfiltered(self):
        real_result = [(socket.AF_INET, None, None, "", ("1.2.3.4", 0))]
        with patch("socket.getaddrinfo", return_value=real_result) as mock_resolve:
            with pin_host("example.com", frozenset({"93.184.216.34"})):
                result = socket.getaddrinfo("other-host.com", None)
        self.assertEqual(result, real_result)

    def test_restores_resolver_on_exit(self):
        real = socket.getaddrinfo
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, None, None, "", ("93.184.216.34", 0))]):
            with pin_host("example.com", frozenset({"93.184.216.34"})):
                pass
        self.assertIs(socket.getaddrinfo, real)

    def test_restores_resolver_even_on_exception(self):
        real = socket.getaddrinfo
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, None, None, "", ("93.184.216.34", 0))]):
            with self.assertRaises(ValueError):
                with pin_host("example.com", frozenset({"93.184.216.34"})):
                    raise ValueError("boom")
        self.assertIs(socket.getaddrinfo, real)

    def test_empty_allowed_ips_is_a_no_op(self):
        real = socket.getaddrinfo
        with pin_host("example.com", frozenset()):
            self.assertIs(socket.getaddrinfo, real)

    def test_nested_pins_for_different_hosts_do_not_clobber_each_other(self):
        # Regression test for the original bug: one caller's exit used to
        # unconditionally restore the real resolver, silently un-pinning a
        # still-active sibling call for a different host. Nested context
        # managers exercise the same refcounting/dict logic concurrent
        # calls would, deterministically.
        real = socket.getaddrinfo

        def resolver(host, *a, **k):
            if host == "a.com":
                return [(socket.AF_INET, None, None, "", ("1.1.1.1", 0))]
            return [(socket.AF_INET, None, None, "", ("2.2.2.2", 0))]

        with patch("socket.getaddrinfo", side_effect=resolver):
            with pin_host("a.com", frozenset({"1.1.1.1"})):
                with pin_host("b.com", frozenset({"2.2.2.2"})):
                    pass
                # b's exit must not have restored the real resolver or
                # un-pinned a, which is still an active outer context.
                self.assertIsNot(socket.getaddrinfo, real)
                a_result = socket.getaddrinfo("a.com", None)
                self.assertEqual({i[4][0] for i in a_result}, {"1.1.1.1"})
        self.assertIs(socket.getaddrinfo, real)

    def test_nested_pins_for_the_same_host_share_one_refcounted_entry(self):
        real = socket.getaddrinfo
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, None, None, "", ("1.1.1.1", 0))]):
            with pin_host("a.com", frozenset({"1.1.1.1"})):
                with pin_host("a.com", frozenset({"1.1.1.1"})):
                    pass
                # Inner exit decremented the refcount but must not have
                # torn down the shim while the outer call is still active.
                self.assertIsNot(socket.getaddrinfo, real)
                result = socket.getaddrinfo("a.com", None)
                self.assertEqual({i[4][0] for i in result}, {"1.1.1.1"})
        self.assertIs(socket.getaddrinfo, real)


if __name__ == "__main__":
    unittest.main()
