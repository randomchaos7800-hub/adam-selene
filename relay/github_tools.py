"""GitHub integration tools for Adam Selene.

Auth: GitHub App (App ID + RSA private key) → installation token (1 hr, cached).
Env vars: GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_B64, GITHUB_USERNAME.
Fallback: GITHUB_TOKEN (PAT) for local dev without App creds.

Supports full CRUD: create repos, push files, read files, list repos, create branches.
"""

import base64
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import jwt
import requests

from relay import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credentials — read at import time (dotenv must be loaded before import)
# ---------------------------------------------------------------------------

GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "")
_APP_ID = os.environ.get("GITHUB_APP_ID", "")
_APP_KEY_B64 = os.environ.get("GITHUB_APP_PRIVATE_KEY_B64", "")
_PAT_FALLBACK = os.environ.get("GITHUB_TOKEN", "")

_USER_AGENT = f"{config.agent_name()}-Agent"

# ---------------------------------------------------------------------------
# Token cache — minted once per hour, refreshed with 120s buffer
# ---------------------------------------------------------------------------

_cached_token: str = ""
_token_expires_at: float = 0.0
_TOKEN_REFRESH_BUFFER = 120  # seconds before expiry to refresh


def _get_token() -> str:
    """Return a valid GitHub token. Uses cache; mints new App token when needed."""
    global _cached_token, _token_expires_at

    if _cached_token and time.time() < _token_expires_at - _TOKEN_REFRESH_BUFFER:
        return _cached_token

    if _APP_ID and _APP_KEY_B64:
        token, expires_at = _mint_installation_token(_APP_ID, _APP_KEY_B64)
        _cached_token = token
        _token_expires_at = expires_at
        return _cached_token

    if _PAT_FALLBACK:
        _cached_token = _PAT_FALLBACK
        _token_expires_at = float("inf")
        return _cached_token

    raise RuntimeError(
        "No GitHub credentials — set GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY_B64 or GITHUB_TOKEN"
    )


def _mint_installation_token(app_id: str, key_b64: str) -> tuple[str, float]:
    """Mint a GitHub App installation token. Returns (token, expires_at_unix)."""
    private_key_pem = base64.b64decode(key_b64).decode("utf-8")
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 540,
        "iss": str(app_id),  # iss MUST be str — GitHub rejects int
    }
    app_jwt = jwt.encode(payload, private_key_pem, algorithm="RS256")

    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": _USER_AGENT,
    }

    inst_resp = requests.get("https://api.github.com/app/installations", headers=headers)
    inst_resp.raise_for_status()
    installations = inst_resp.json()
    if not installations:
        raise RuntimeError("GitHub App has no installations")

    token_resp = requests.post(
        f"https://api.github.com/app/installations/{installations[0]['id']}/access_tokens",
        headers=headers,
    )
    token_resp.raise_for_status()
    data = token_resp.json()

    # expires_at is ISO 8601; convert to unix timestamp
    from datetime import datetime, timezone
    expires_str = data.get("expires_at", "")
    if expires_str:
        expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00")).timestamp()
    else:
        expires_at = time.time() + 3600

    return data["token"], expires_at


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"token {_get_token()}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": _USER_AGENT,
    }


def _full_name(repo_name: str) -> str:
    """Return 'owner/repo' — auto-prepend username if not already qualified."""
    if "/" in repo_name:
        return repo_name
    return f"{GITHUB_USERNAME}/{repo_name}"


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def execute_github_tool(tool_name: str, **kwargs) -> Dict[str, Any]:
    """Execute a GitHub tool and return structured result."""
    dispatch = {
        "github_create_repo": create_repo,
        "github_push_file": push_file,
        "github_get_repo_info": get_repo_info,
        "github_list_repos": lambda **kw: list_repos(),
        "github_create_branch": create_branch,
        "github_get_file_content": get_file_content,
    }
    fn = dispatch.get(tool_name)
    if fn is None:
        return {"success": False, "error": f"Unknown GitHub tool: {tool_name}"}
    try:
        return fn(**kwargs)
    except Exception as e:
        logger.error(f"GitHub tool {tool_name} failed: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def create_repo(repo_name: str, description: str = "", private: bool = True) -> Dict[str, Any]:
    """Create a new GitHub repository."""
    data = {
        "name": repo_name,
        "description": description,
        "private": private,
        "auto_init": True,
        "gitignore_template": "Python",
    }
    try:
        resp = requests.post("https://api.github.com/user/repos", headers=_headers(), json=data)
        if resp.status_code == 201:
            repo_data = resp.json()
            return {
                "success": True,
                "message": f"Repository '{repo_name}' created successfully",
                "repo_url": repo_data["html_url"],
                "clone_url": repo_data["clone_url"],
                "ssh_url": repo_data["ssh_url"],
            }
        elif resp.status_code == 422:
            return {"success": False, "error": f"Repository '{repo_name}' already exists"}
        else:
            return {"success": False, "error": f"GitHub API error: {resp.status_code} - {resp.text}"}
    except Exception as e:
        return {"success": False, "error": f"Error: {e}"}


def push_file(
    repo_name: str,
    file_path: str,
    content: str,
    commit_message: str = "Update file",
) -> Dict[str, Any]:
    """Push a file to a GitHub repository (create or update)."""
    full = _full_name(repo_name)
    content_encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    file_url = f"https://api.github.com/repos/{full}/contents/{file_path}"

    try:
        existing = requests.get(file_url, headers=_headers())
        sha: Optional[str] = None
        if existing.status_code == 200:
            sha = existing.json().get("sha")

        payload: Dict[str, Any] = {"message": commit_message, "content": content_encoded}
        if sha:
            payload["sha"] = sha

        resp = requests.put(file_url, headers=_headers(), json=payload)
        if resp.status_code in [200, 201]:
            result = resp.json()
            action = "updated" if sha else "created"
            return {
                "success": True,
                "message": f"File '{file_path}' {action} in '{repo_name}'",
                "commit_url": result["commit"]["html_url"],
                "file_url": result["content"]["html_url"],
            }
        else:
            return {"success": False, "error": f"GitHub API error: {resp.status_code} - {resp.text}"}
    except Exception as e:
        return {"success": False, "error": f"Error: {e}"}


def get_repo_info(repo_name: str) -> Dict[str, Any]:
    """Get information about a repository."""
    full = _full_name(repo_name)
    try:
        resp = requests.get(f"https://api.github.com/repos/{full}", headers=_headers())
        if resp.status_code == 200:
            d = resp.json()
            return {
                "success": True,
                "repo": {
                    "name": d["name"],
                    "description": d.get("description"),
                    "private": d["private"],
                    "url": d["html_url"],
                    "clone_url": d["clone_url"],
                    "ssh_url": d["ssh_url"],
                    "created_at": d["created_at"],
                    "updated_at": d["updated_at"],
                    "size": d["size"],
                    "language": d.get("language"),
                    "default_branch": d["default_branch"],
                },
            }
        elif resp.status_code == 404:
            return {"success": False, "error": f"Repository '{repo_name}' not found"}
        else:
            return {"success": False, "error": f"GitHub API error: {resp.status_code} - {resp.text}"}
    except Exception as e:
        return {"success": False, "error": f"Error: {e}"}


def list_repos() -> Dict[str, Any]:
    """List all repositories accessible with current credentials."""
    try:
        # Installation token → use installation-scoped endpoint
        if _APP_ID and _APP_KEY_B64:
            resp = requests.get(
                "https://api.github.com/installation/repositories?per_page=50",
                headers=_headers(),
            )
            if not resp.ok:
                return {"success": False, "error": f"GitHub {resp.status_code}: {resp.text}"}
            repos = resp.json().get("repositories", [])
        else:
            # PAT fallback
            resp = requests.get(
                "https://api.github.com/user/repos",
                headers=_headers(),
                params={"sort": "updated", "per_page": 50},
            )
            if not resp.ok:
                return {"success": False, "error": f"GitHub {resp.status_code}: {resp.text}"}
            repos = resp.json()

        repo_list = []
        for repo in repos:
            repo_list.append({
                "name": repo["name"],
                "description": (repo.get("description") or "No description")[:80],
                "private": repo["private"],
                "url": repo["html_url"],
                "updated_at": repo.get("updated_at", ""),
                "language": repo.get("language"),
            })

        return {
            "success": True,
            "message": f"Found {len(repo_list)} repositories",
            "repos": repo_list,
        }
    except Exception as e:
        return {"success": False, "error": f"Error: {e}"}


def create_branch(
    repo_name: str,
    branch_name: str,
    from_branch: str = "main",
) -> Dict[str, Any]:
    """Create a new branch in the repository."""
    full = _full_name(repo_name)
    try:
        ref_resp = requests.get(
            f"https://api.github.com/repos/{full}/git/ref/heads/{from_branch}",
            headers=_headers(),
        )
        if ref_resp.status_code != 200:
            return {"success": False, "error": f"Source branch '{from_branch}' not found"}

        source_sha = ref_resp.json()["object"]["sha"]
        data = {"ref": f"refs/heads/{branch_name}", "sha": source_sha}
        resp = requests.post(
            f"https://api.github.com/repos/{full}/git/refs",
            headers=_headers(),
            json=data,
        )
        if resp.status_code == 201:
            return {"success": True, "message": f"Branch '{branch_name}' created from '{from_branch}'"}
        elif resp.status_code == 422:
            return {"success": False, "error": f"Branch '{branch_name}' already exists"}
        else:
            return {"success": False, "error": f"GitHub API error: {resp.status_code} - {resp.text}"}
    except Exception as e:
        return {"success": False, "error": f"Error: {e}"}


def get_file_content(
    repo_name: str,
    file_path: str,
    branch: str = "main",
) -> Dict[str, Any]:
    """Get the content of a file from a GitHub repository."""
    full = _full_name(repo_name)
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{full}/contents/{file_path}",
            headers=_headers(),
            params={"ref": branch},
        )
        if resp.status_code == 200:
            file_data = resp.json()
            content = base64.b64decode(file_data["content"]).decode("utf-8")
            return {
                "success": True,
                "file": {
                    "name": file_data["name"],
                    "path": file_data["path"],
                    "content": content,
                    "size": file_data["size"],
                    "sha": file_data["sha"],
                    "url": file_data["html_url"],
                },
            }
        elif resp.status_code == 404:
            return {"success": False, "error": f"File '{file_path}' not found in '{repo_name}'"}
        else:
            return {"success": False, "error": f"GitHub API error: {resp.status_code} - {resp.text}"}
    except Exception as e:
        return {"success": False, "error": f"Error: {e}"}
