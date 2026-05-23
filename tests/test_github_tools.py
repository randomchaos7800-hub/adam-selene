import os
import unittest
from unittest.mock import Mock, patch

from relay import github_tools


class TestGitHubTools(unittest.TestCase):
    def setUp(self):
        github_tools._cached_token = ""
        github_tools._token_expires_at = 0.0
        github_tools._cached_token_source = None

    def test_full_name_uses_runtime_environment(self):
        with patch.dict(os.environ, {"GITHUB_USERNAME": "runtime-user"}, clear=False):
            self.assertEqual(github_tools._full_name("repo"), "runtime-user/repo")

    def test_create_repo_sets_timeout(self):
        response = Mock(status_code=201)
        response.json.return_value = {
            "html_url": "https://github.com/example/repo",
            "clone_url": "https://github.com/example/repo.git",
            "ssh_url": "git@github.com:example/repo.git",
        }
        with patch.object(github_tools, "_headers", return_value={"Authorization": "token x"}):
            with patch("relay.github_tools.requests.request", return_value=response) as request_mock:
                result = github_tools.create_repo("repo")

        self.assertTrue(result["success"])
        self.assertEqual(request_mock.call_args.kwargs["timeout"], github_tools.GITHUB_API_TIMEOUT)

    def test_token_refreshes_when_environment_changes(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "first-token"}, clear=False):
            self.assertEqual(github_tools._get_token(), "first-token")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "second-token"}, clear=False):
            self.assertEqual(github_tools._get_token(), "second-token")


if __name__ == "__main__":
    unittest.main()
