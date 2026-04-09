"""GitHub integration tools.

These tools allow the agent to create repositories and push files to GitHub
for sharing work and collaborating on projects.
"""

import os
import requests
import json
import base64
from typing import Dict, Any, Optional

from relay import config

# GitHub credentials from environment
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "")

_USER_AGENT = f"{config.agent_name()}-Agent"


def execute_github_tool(tool_name: str, **kwargs) -> Dict[str, Any]:
    """Execute a GitHub tool and return structured result."""

    if tool_name == "github_create_repo":
        return create_repo(**kwargs)
    elif tool_name == "github_push_file":
        return push_file(**kwargs)
    elif tool_name == "github_get_repo_info":
        return get_repo_info(**kwargs)
    elif tool_name == "github_list_repos":
        return list_repos()
    elif tool_name == "github_create_branch":
        return create_branch(**kwargs)
    elif tool_name == "github_get_file_content":
        return get_file_content(**kwargs)
    else:
        return {"success": False, "error": f"Unknown GitHub tool: {tool_name}"}


def create_repo(repo_name: str, description: str = "", private: bool = True) -> Dict[str, Any]:
    """Create a new GitHub repository."""

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": _USER_AGENT
    }

    data = {
        "name": repo_name,
        "description": description,
        "private": private,
        "auto_init": True,  # Create with README
        "gitignore_template": "Python"  # Default gitignore
    }

    try:
        response = requests.post(
            "https://api.github.com/user/repos",
            headers=headers,
            json=data
        )

        if response.status_code == 201:
            repo_data = response.json()
            return {
                "success": True,
                "message": f"Repository '{repo_name}' created successfully",
                "repo_url": repo_data["html_url"],
                "clone_url": repo_data["clone_url"],
                "ssh_url": repo_data["ssh_url"]
            }
        elif response.status_code == 422:
            return {
                "success": False,
                "error": f"Repository '{repo_name}' already exists"
            }
        else:
            return {
                "success": False,
                "error": f"GitHub API error: {response.status_code} - {response.text}"
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Network error: {str(e)}"
        }


def push_file(repo_name: str, file_path: str, content: str, commit_message: str = "Update file") -> Dict[str, Any]:
    """Push a file to a GitHub repository."""

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": _USER_AGENT
    }

    # Encode content as base64
    content_encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')

    # Check if file already exists to get its SHA
    file_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/contents/{file_path}"

    try:
        # Get existing file info
        existing_response = requests.get(file_url, headers=headers)
        sha = None
        if existing_response.status_code == 200:
            sha = existing_response.json().get("sha")

        # Prepare data for file creation/update
        data = {
            "message": commit_message,
            "content": content_encoded
        }

        if sha:
            data["sha"] = sha  # Required for updates

        # Create or update the file
        response = requests.put(file_url, headers=headers, json=data)

        if response.status_code in [200, 201]:
            result = response.json()
            action = "updated" if sha else "created"
            return {
                "success": True,
                "message": f"File '{file_path}' {action} in '{repo_name}'",
                "commit_url": result["commit"]["html_url"],
                "file_url": result["content"]["html_url"]
            }
        else:
            return {
                "success": False,
                "error": f"GitHub API error: {response.status_code} - {response.text}"
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Network error: {str(e)}"
        }


def get_repo_info(repo_name: str) -> Dict[str, Any]:
    """Get information about a repository."""

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": _USER_AGENT
    }

    try:
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}",
            headers=headers
        )

        if response.status_code == 200:
            repo_data = response.json()
            return {
                "success": True,
                "repo": {
                    "name": repo_data["name"],
                    "description": repo_data["description"],
                    "private": repo_data["private"],
                    "url": repo_data["html_url"],
                    "clone_url": repo_data["clone_url"],
                    "ssh_url": repo_data["ssh_url"],
                    "created_at": repo_data["created_at"],
                    "updated_at": repo_data["updated_at"],
                    "size": repo_data["size"],
                    "language": repo_data["language"],
                    "default_branch": repo_data["default_branch"]
                }
            }
        elif response.status_code == 404:
            return {
                "success": False,
                "error": f"Repository '{repo_name}' not found"
            }
        else:
            return {
                "success": False,
                "error": f"GitHub API error: {response.status_code} - {response.text}"
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Network error: {str(e)}"
        }


def list_repos() -> Dict[str, Any]:
    """List all repositories accessible with current credentials."""

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": _USER_AGENT
    }

    try:
        response = requests.get(
            "https://api.github.com/user/repos",
            headers=headers,
            params={"sort": "updated", "per_page": 50}
        )

        if response.status_code == 200:
            repos = response.json()
            repo_list = []

            for repo in repos:
                repo_list.append({
                    "name": repo["name"],
                    "description": repo["description"],
                    "private": repo["private"],
                    "url": repo["html_url"],
                    "updated_at": repo["updated_at"],
                    "language": repo["language"]
                })

            return {
                "success": True,
                "message": f"Found {len(repo_list)} repositories",
                "repos": repo_list
            }
        else:
            return {
                "success": False,
                "error": f"GitHub API error: {response.status_code} - {response.text}"
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Network error: {str(e)}"
        }


def create_branch(repo_name: str, branch_name: str, from_branch: str = "main") -> Dict[str, Any]:
    """Create a new branch in the repository."""

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": _USER_AGENT
    }

    try:
        # Get the SHA of the source branch
        ref_response = requests.get(
            f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/git/ref/heads/{from_branch}",
            headers=headers
        )

        if ref_response.status_code != 200:
            return {
                "success": False,
                "error": f"Source branch '{from_branch}' not found"
            }

        source_sha = ref_response.json()["object"]["sha"]

        # Create the new branch
        data = {
            "ref": f"refs/heads/{branch_name}",
            "sha": source_sha
        }

        response = requests.post(
            f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/git/refs",
            headers=headers,
            json=data
        )

        if response.status_code == 201:
            return {
                "success": True,
                "message": f"Branch '{branch_name}' created from '{from_branch}'"
            }
        elif response.status_code == 422:
            return {
                "success": False,
                "error": f"Branch '{branch_name}' already exists"
            }
        else:
            return {
                "success": False,
                "error": f"GitHub API error: {response.status_code} - {response.text}"
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Network error: {str(e)}"
        }


def get_file_content(repo_name: str, file_path: str, branch: str = "main") -> Dict[str, Any]:
    """Get the content of a file from a GitHub repository."""

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": _USER_AGENT
    }

    try:
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/contents/{file_path}",
            headers=headers,
            params={"ref": branch}
        )

        if response.status_code == 200:
            file_data = response.json()

            # Decode base64 content
            content = base64.b64decode(file_data["content"]).decode('utf-8')

            return {
                "success": True,
                "file": {
                    "name": file_data["name"],
                    "path": file_data["path"],
                    "content": content,
                    "size": file_data["size"],
                    "sha": file_data["sha"],
                    "url": file_data["html_url"]
                }
            }
        elif response.status_code == 404:
            return {
                "success": False,
                "error": f"File '{file_path}' not found in '{repo_name}'"
            }
        else:
            return {
                "success": False,
                "error": f"GitHub API error: {response.status_code} - {response.text}"
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Network error: {str(e)}"
        }
