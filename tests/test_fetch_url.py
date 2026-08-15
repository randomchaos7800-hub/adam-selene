import unittest
from unittest.mock import Mock, patch

from relay.tools import execute_tool


def _response(status_code=200, content=b"hello world", headers=None):
    resp = Mock()
    resp.status_code = status_code
    resp.headers = headers or {"Content-Type": "text/plain"}
    resp.iter_content = Mock(return_value=[content])
    resp.raise_for_status = Mock()
    if status_code >= 400:
        import requests
        err = requests.exceptions.HTTPError(response=Mock(status_code=status_code, reason="Error"))
        resp.raise_for_status.side_effect = err
    return resp


class TestFetchUrl(unittest.TestCase):
    def setUp(self):
        self.resolve_patcher = patch("relay.net_guard.resolve_public", return_value=(True, "", frozenset({"93.184.216.34"})))
        self.pin_patcher = patch("relay.net_guard.pin_host")
        self.mock_resolve = self.resolve_patcher.start()
        self.mock_pin = self.pin_patcher.start()
        self.mock_pin.return_value.__enter__ = Mock(return_value=None)
        self.mock_pin.return_value.__exit__ = Mock(return_value=False)
        self.addCleanup(self.resolve_patcher.stop)
        self.addCleanup(self.pin_patcher.stop)

    def test_blocked_by_ssrf_guard_returns_error_without_network_call(self):
        with patch("relay.net_guard.resolve_public", return_value=(False, "private address", frozenset())):
            with patch("requests.get") as mock_get:
                result = execute_tool("fetch_url", {"url": "http://169.254.169.254/"}, interface="telegram")
        self.assertIn("blocked", result)
        mock_get.assert_not_called()

    def test_successful_get_returns_content(self):
        with patch("requests.get", return_value=_response(200, b"hello world")):
            result = execute_tool("fetch_url", {"url": "http://example.com/"}, interface="telegram")
        self.assertIn("hello world", result)
        self.assertIn("Status: 200", result)

    def test_redirect_is_followed_and_revalidated(self):
        redirect_resp = _response(302, b"", headers={"Location": "http://example.com/final"})
        final_resp = _response(200, b"final content")
        with patch("requests.get", side_effect=[redirect_resp, final_resp]):
            result = execute_tool("fetch_url", {"url": "http://example.com/start"}, interface="telegram")
        self.assertIn("final content", result)
        # Both the original and the redirect target must have gone through
        # the SSRF guard independently — not followed blind.
        self.assertEqual(self.mock_resolve.call_count, 2)
        self.mock_resolve.assert_any_call("http://example.com/start")
        self.mock_resolve.assert_any_call("http://example.com/final")

    def test_redirect_into_private_address_is_blocked(self):
        redirect_resp = _response(302, b"", headers={"Location": "http://169.254.169.254/steal"})
        with patch("requests.get", return_value=redirect_resp), \
             patch("relay.net_guard.resolve_public", side_effect=[
                 (True, "", frozenset({"93.184.216.34"})),
                 (False, "private address", frozenset()),
             ]):
            result = execute_tool("fetch_url", {"url": "http://example.com/start"}, interface="telegram")
        self.assertIn("blocked", result)

    def test_redirect_with_no_location_header_is_an_error(self):
        redirect_resp = _response(302, b"", headers={})
        with patch("requests.get", return_value=redirect_resp):
            result = execute_tool("fetch_url", {"url": "http://example.com/start"}, interface="telegram")
        self.assertIn("Error", result)
        self.assertIn("Location", result)

    def test_too_many_redirects_gives_up(self):
        # Always redirects to itself — must not loop forever.
        redirect_resp = _response(302, b"", headers={"Location": "http://example.com/loop"})
        with patch("requests.get", return_value=redirect_resp):
            result = execute_tool("fetch_url", {"url": "http://example.com/loop"}, interface="telegram")
        self.assertIn("too many redirects", result)

    def test_post_downgrades_to_get_on_302(self):
        redirect_resp = _response(302, b"", headers={"Location": "http://example.com/final"})
        final_resp = _response(200, b"ok")
        with patch("requests.post", return_value=redirect_resp) as mock_post, \
             patch("requests.get", return_value=final_resp) as mock_get:
            result = execute_tool(
                "fetch_url",
                {"url": "http://example.com/start", "method": "POST", "data": {"x": 1}},
                interface="telegram",
            )
        self.assertIn("ok", result)
        mock_post.assert_called_once()   # only the initial POST
        mock_get.assert_called_once()    # the redirect hop switched to GET

    def test_http_error_status_is_reported(self):
        with patch("requests.get", return_value=_response(404)):
            result = execute_tool("fetch_url", {"url": "http://example.com/missing"}, interface="telegram")
        self.assertIn("404", result)


if __name__ == "__main__":
    unittest.main()
