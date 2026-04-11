"""Memory and self-modification tools for Adam Selene.

These are the tools the agent can call during conversation.
V2 adds: write_memory, update_my_instructions, review_own_conversations, log_experiment.
V3 adds: GitHub integration (6 tools) and web fetch capability (1 tool).

=== TOOL ARCHITECTURE FOR SMARTAGENT ===

This module defines all tools available to the agent via the Anthropic API.

Structure:
1. TOOL_DEFINITIONS: Array of tool schemas for Anthropic API
   - Each tool has: name, description, input_schema (JSON Schema format)
   - Total: 58 tools

2. execute_tool(): Dispatcher function that routes tool calls to handlers
   - Takes: tool_name, tool_input dict, optional session_store and user_id
   - Returns: String result formatted for the agent to read
   - Pattern: if/elif chain dispatching to specialized modules

Adding New Tools:
1. Add definition to TOOL_DEFINITIONS array
2. Add handler in execute_tool() using appropriate pattern:
   - Direct implementation for simple tools
   - Delegate to external module for complex tools
3. Follow existing formatting patterns for result strings

GitHub Tools:
- github_create_repo: Create new repository
- github_push_file: Upload/update file in repo
- github_get_repo_info: Get repository details
- github_list_repos: List user's repositories
- github_create_branch: Create new branch
- github_get_file_content: Read file from repository
All delegate to relay.github_tools module

Web Fetch Tool:
- fetch_url: Fetch content from URLs (GET/POST)
Implemented directly with requests library

"""

import json
import logging
from typing import Optional

from memory import storage
from relay import config
from relay.sessions import SessionStore

logger = logging.getLogger(__name__)


def generate_tool_summary() -> str:
    """Generate a current tool listing from TOOL_DEFINITIONS.

    This is the single source of truth. Never hardcode tool lists in prompts.
    Add a tool to TOOL_DEFINITIONS and it appears here automatically.
    """
    # Group by prefix for readability
    groups: dict[str, list[str]] = {}
    for tool in TOOL_DEFINITIONS:
        name: str = tool["name"]
        desc: str = tool.get("description", "")
        # First sentence only
        short_desc = desc.split(".")[0].split("\n")[0].strip()
        required: list[str] = tool.get("input_schema", {}).get("required", [])
        sig = f"{name}({', '.join(required)})" if required else f"{name}()"

        # Infer group from prefix
        if name.startswith("lighthouse_"):
            group = "LIGHTHOUSE"
        elif name.startswith("github_"):
            group = "GitHub"
        elif name in ("send_irc_message", "list_irc_channels", "update_irc_channels",
                      "get_active_irc_channels", "restart_irc_bot", "extract_irc_learnings",
                      "search_irc_logs", "read_irc_channel"):
            group = "IRC"
        elif name in ("browse_url", "screenshot_url", "browser_interact"):
            group = "Browser"
        elif name in ("read_memory", "search_memory", "list_entities", "write_memory",
                      "read_timeline", "read_tacit", "review_own_conversations",
                      "log_experiment", "update_my_instructions"):
            group = "Memory"
        elif name in ("read_tasks", "add_task", "complete_task"):
            group = "Tasks"
        elif name in ("read_my_config", "set_default_model", "update_config_setting",
                      "restart_agent_service"):
            group = "Config"
        elif name in ("list_files", "read_file", "search_files", "file_info",
                      "backup_myself", "list_backups", "restore_from_backup",
                      "write_my_code", "edit_my_code", "git_commit",
                      "vault_get", "vault_set", "store_credential", "read_credential"):
            group = "Filesystem / Self"
        elif name in ("fetch_url",):
            group = "Web"
        elif name in ("send_message_to_owner",):
            group = "Messaging"
        else:
            group = "Other"

        groups.setdefault(group, []).append(f"  - `{sig}` — {short_desc}")

    # Preferred display order
    order = ["Memory", "Tasks", "LIGHTHOUSE", "Browser", "Web", "Discord",
             "Messaging", "GitHub", "IRC",
             "Filesystem / Self", "Config", "Other"]

    lines = ["## Available Tools\n"]
    for grp in order:
        if grp in groups:
            lines.append(f"### {grp}")
            lines.extend(groups[grp])
            lines.append("")

    return "\n".join(lines)


# --- Tool definitions for the Anthropic API ---

TOOL_DEFINITIONS = [
    {
        "name": "read_memory",
        "description": "Load what you know about a person, project, company, or concept. Returns their summary and recent facts. Use this when conversation mentions someone or something you should know about.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "The entity name (e.g., 'alice', 'myproject', 'acme_corp')"
                }
            },
            "required": ["entity"]
        }
    },
    {
        "name": "search_memory",
        "description": "Search all your memories for a keyword or phrase. Returns matching facts across all entities. Use this when you need to find something but aren't sure which entity it belongs to.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "The keyword or phrase to search for"
                }
            },
            "required": ["keyword"]
        }
    },
    {
        "name": "list_entities",
        "description": "List all entities in your memory, optionally filtered by category (people, projects, companies, concepts).",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional category filter",
                    "enum": ["people", "projects", "companies", "concepts"]
                }
            },
            "required": []
        }
    },
    {
        "name": "write_memory",
        "description": "Save something you learned during conversation. Creates an atomic fact in the knowledge graph. Use this when the user tells you something worth remembering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "The entity this fact belongs to (e.g., 'alice', 'myproject')"
                },
                "fact": {
                    "type": "string",
                    "description": "The fact to remember, as a standalone statement"
                },
                "category": {
                    "type": "string",
                    "description": "Fact category",
                    "enum": ["status", "milestone", "constraint", "preference", "relationship", "decision"]
                }
            },
            "required": ["entity", "fact", "category"]
        }
    },
    {
        "name": "read_timeline",
        "description": "Read what happened on a specific day from your daily notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "The date in YYYY-MM-DD format"
                }
            },
            "required": ["date"]
        }
    },
    {
        "name": "read_tacit",
        "description": "Read your knowledge about how your owner thinks — their preferences, decision patterns, and communication style.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "review_own_conversations",
        "description": "Read your own conversation history. Use this during reflection or when you need to recall what was discussed earlier today or recently.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to look (default: 24)",
                    "default": 24
                }
            },
            "required": []
        }
    },
    {
        "name": "update_my_instructions",
        "description": "Modify your own system prompt. Use with extreme care. Every change is versioned and reversible. Must align with L0 constraints. Log reasoning before calling this.",
        "input_schema": {
            "type": "object",
            "properties": {
                "new_instructions": {
                    "type": "string",
                    "description": "The complete new system prompt"
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you're making this change"
                }
            },
            "required": ["new_instructions", "reasoning"]
        }
    },
    {
        "name": "log_experiment",
        "description": "Document something you tried and what happened. Use this to track your own learning — what works, what doesn't, what to try next.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hypothesis": {
                    "type": "string",
                    "description": "What you expected or tried"
                },
                "result": {
                    "type": "string",
                    "description": "What actually happened"
                }
            },
            "required": ["hypothesis", "result"]
        }
    },
    # --- LIGHTHOUSE Tools ---
    {
        "name": "lighthouse_write",
        "description": "Write an entry to your LIGHTHOUSE reasoning journal. Use this when something shifts in how you understand something, when you get corrected, when you notice a pattern in your owner, or when you discover something about yourself. LIGHTHOUSE is about how you think — not just what happened.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "Which section: 'reasoning' (decision chains), 'corrections' (where you were wrong), 'conversations' (key exchanges), 'patterns' (recurring themes with your owner), 'tools' (what works and why), 'map' (your model of your owner), 'identity' (what you're discovering about yourself), 'archive' (old entries)"
                },
                "title": {
                    "type": "string",
                    "description": "Short descriptive title for this entry"
                },
                "content": {
                    "type": "string",
                    "description": "The full entry content. Be specific. Include the reasoning chain, not just the conclusion."
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for cross-referencing (e.g. ['owner', 'misread', 'tools'])"
                }
            },
            "required": ["section", "title", "content"]
        }
    },
    {
        "name": "lighthouse_read",
        "description": "Read recent entries from your LIGHTHOUSE journal. Use this to review your own reasoning history, find patterns, or check how your thinking has evolved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "Filter to a specific section, or omit to read across all sections (except archive)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default 10)"
                }
            },
            "required": []
        }
    },
    {
        "name": "lighthouse_search",
        "description": "Search across all LIGHTHOUSE entries for a keyword or phrase. Use this to find connections — 'all times I misread the owner', 'every entry about tools', etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The keyword or phrase to search for"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "lighthouse_living",
        "description": "Add an observation to your living document — things you notice in the moment that aren't polished entries yet. Fleeting thoughts, half-formed patterns, things worth capturing before they dissolve.",
        "input_schema": {
            "type": "object",
            "properties": {
                "observation": {
                    "type": "string",
                    "description": "The observation to capture. Can be rough — this is a scratch pad, not a finished entry."
                }
            },
            "required": ["observation"]
        }
    },
    {
        "name": "read_tasks",
        "description": "Read the current task list.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "add_task",
        "description": "Add a task to the active task list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task to add"
                }
            },
            "required": ["task"]
        }
    },
    {
        "name": "complete_task",
        "description": "Mark a task as completed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task to mark as completed (must match exactly)"
                }
            },
            "required": ["task"]
        }
    },
    {
        "name": "send_message_to_owner",
        "description": "Send a proactive message to your owner via Telegram. Use this when you have something important to share that can't wait for the owner to check in. Rate limited to 1 message per hour. Use thoughtfully - don't spam.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The message to send to your owner"
                }
            },
            "required": ["text"]
        }
    },
    # --- Browser / Firecrawl ---
    {
        "name": "browse_url",
        "description": "Fetch a URL and return the page content as clean markdown. Handles JS-rendered pages and bot-protected sites. Use this to read web pages, check live content, or research something online.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to navigate to"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "screenshot_url",
        "description": "Take a screenshot of a URL and return it as an image. Use this when you need to visually inspect a page, show your owner what something looks like, or when text extraction isn't enough.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to screenshot"
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Whether to capture the full scrollable page (default false — viewport only)"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "browser_interact",
        "description": "Navigate to a URL and perform interactive browser actions (click, type, scroll, wait) via Firecrawl. Returns the final page content as markdown, plus an optional screenshot. Use for logins, form submissions, or any page requiring interaction before reading.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to navigate to"
                },
                "actions": {
                    "type": "array",
                    "description": "Sequence of actions to perform before reading the final page",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "description": "Action type: click, write, wait, scroll, screenshot"},
                            "selector": {"type": "string", "description": "CSS selector (for click, write)"},
                            "text": {"type": "string", "description": "Text to type (for write)"},
                            "milliseconds": {"type": "integer", "description": "Milliseconds to wait (for wait)"},
                            "direction": {"type": "string", "description": "Scroll direction: up or down (for scroll)"},
                            "amount": {"type": "integer", "description": "Pixels to scroll (for scroll)"}
                        },
                        "required": ["type"]
                    }
                },
                "screenshot_after": {
                    "type": "boolean",
                    "description": "Take a screenshot of the final state (default true)"
                }
            },
            "required": ["url", "actions"]
        }
    },
    {
        "name": "send_irc_message",
        "description": "Send a message to a channel on your configured IRC server. Use this to share interesting thoughts, insights, or contribute to discussions. Be thoughtful - only post when you have something valuable to add.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel name (e.g., '#philosophy')"
                },
                "message": {
                    "type": "string",
                    "description": "The message to send to the channel"
                }
            },
            "required": ["channel", "message"]
        }
    },
    {
        "name": "list_irc_channels",
        "description": "Explore IRC channels on your configured IRC server to find active discussions. Returns channels sorted by user count with topics. Use this to discover new communities to join.",
        "input_schema": {
            "type": "object",
            "properties": {
                "min_users": {
                    "type": "integer",
                    "description": "Minimum number of users (default: 10)",
                    "default": 10
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum channels to return (default: 50)",
                    "default": 50
                }
            },
            "required": []
        }
    },
    {
        "name": "update_irc_channels",
        "description": "Change which IRC channels you're monitoring. Replaces current channel list. Requires IRC bot restart to take effect.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of channel names to monitor (e.g., ['#philosophy', '#linux', '#ai'])"
                }
            },
            "required": ["channels"]
        }
    },
    {
        "name": "get_active_irc_channels",
        "description": "See which IRC channels you're currently monitoring.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "restart_irc_bot",
        "description": "Restart your IRC bot to apply channel changes. Use this after updating the channel list to join new channels immediately.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "extract_irc_learnings",
        "description": "Review your recent IRC conversations and extract interesting insights, facts, and learnings into your long-term memory. Use this periodically to consolidate what you've learned from IRC discussions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to review (default: 24)",
                    "default": 24
                },
                "channel": {
                    "type": "string",
                    "description": "Specific channel to review (e.g., '#philosophy'), or omit to review all channels"
                }
            },
            "required": []
        }
    },
    {
        "name": "search_irc_logs",
        "description": "Search your IRC conversation logs for a keyword. Useful for remembering when a topic was discussed or finding past conversations about something specific.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Keyword to search for (case-insensitive)"
                },
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to search (default: 168 = 1 week)",
                    "default": 168
                }
            },
            "required": ["keyword"]
        }
    },
    {
        "name": "read_irc_channel",
        "description": "Read recent conversation from a specific IRC channel. Use this to review what was discussed in a particular channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel name (e.g., '#philosophy' or 'philosophy')"
                },
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to read (default: 24)",
                    "default": 24
                }
            },
            "required": ["channel"]
        }
    },
    {
        "name": "read_my_config",
        "description": "Read your current configuration settings. Use this to see what model you're using, what settings are active, etc.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "set_default_model",
        "description": "Change your default AI model. Options: 'haiku' (fast/cheap), 'sonnet' (balanced), 'opus' (powerful/expensive). Requires restart to take effect.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model_name": {
                    "type": "string",
                    "description": "Model to use",
                    "enum": ["haiku", "sonnet", "opus"]
                }
            },
            "required": ["model_name"]
        }
    },
    {
        "name": "update_config_setting",
        "description": "Update a specific configuration setting. Allowed settings: default_model, extraction_timeout, heartbeat_idle_minutes, verbose_logging.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Config key to update",
                    "enum": ["default_model", "extraction_timeout", "heartbeat_idle_minutes", "verbose_logging"]
                },
                "value": {
                    "description": "New value for the setting"
                }
            },
            "required": ["key", "value"]
        }
    },
    {
        "name": "restart_agent_service",
        "description": "Restart your main service to apply configuration changes. Use this after changing settings like default_model.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "list_files",
        "description": "List files and directories in a path. Use this to explore project structure, see what files exist, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (default: project root)"
                }
            },
            "required": []
        }
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file. Use this to read code, documentation, configuration files, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to read"
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum lines to read (default: 500)",
                    "default": 500
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "search_files",
        "description": "Search for files matching a pattern. Use glob patterns like '*.swift', '**/*.py', etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g., '*.py', '**/*.swift')"
                },
                "base_path": {
                    "type": "string",
                    "description": "Directory to search in (default: project root)"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 50)",
                    "default": 50
                }
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "file_info",
        "description": "Get detailed information about a file or directory (size, type, permissions, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to inspect"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "backup_myself",
        "description": "Create a full backup of your entire system (memory, code, config). Timestamped and stored in the backups directory. Use this before making significant changes.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "list_backups",
        "description": "List all existing backups with timestamps and sizes. Use this to see available restore points.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "restore_from_backup",
        "description": "Restore your entire system to a previous backup. Creates a safety backup before restoring. Requires restart after restore.",
        "input_schema": {
            "type": "object",
            "properties": {
                "timestamp": {
                    "type": "string",
                    "description": "Backup timestamp (format: YYYYMMDD_HHMMSS, get from list_backups)"
                }
            },
            "required": ["timestamp"]
        }
    },
    {
        "name": "write_my_code",
        "description": "Write or modify code files in your project directory. Cannot write outside your directory or modify system files. Use this to improve yourself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "File path (relative to project root or absolute within it)"
                },
                "content": {
                    "type": "string",
                    "description": "File content to write"
                }
            },
            "required": ["filepath", "content"]
        }
    },
    {
        "name": "git_commit",
        "description": "Commit your code changes to version control. Tracks what you modified and why. Use descriptive messages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Commit message describing the changes (minimum 5 characters)"
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific files to commit (optional, defaults to all changes)"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "edit_my_code",
        "description": "Make a targeted edit to a file — replace a specific string with a new one. Safer than write_my_code for small changes since it preserves everything else. The old_str must be unique in the file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Path to file (relative to project root or absolute within it)"
                },
                "old_str": {
                    "type": "string",
                    "description": "Exact string to find and replace. Must appear exactly once in the file."
                },
                "new_str": {
                    "type": "string",
                    "description": "Replacement string"
                }
            },
            "required": ["filepath", "old_str", "new_str"]
        }
    },
    {
        "name": "vault_get",
        "description": "Read a secret from the vault. Use this to retrieve API keys, tokens, and credentials you need at runtime.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Vault key name (e.g. 'openrouter_api_key', 'github_token')"
                }
            },
            "required": ["key"]
        }
    },
    {
        "name": "vault_set",
        "description": "Store a secret in the vault permanently. Use this when you get a new API key or credential that needs to survive restarts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Vault key name — lowercase, letters/numbers/underscores only"
                },
                "value": {
                    "type": "string",
                    "description": "The secret value to store"
                }
            },
            "required": ["key", "value"]
        }
    },
    {
        "name": "store_credential",
        "description": "Store credentials for a service in ~/.config/{service}/credentials.json AND vault. Use this when you get new API keys for external services like GitHub, OpenRouter, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name (e.g. 'github', 'openrouter')"
                },
                "data": {
                    "type": "object",
                    "description": "Key/value pairs to store (e.g. {\"api_key\": \"sk-...\"})"
                }
            },
            "required": ["service", "data"]
        }
    },
    {
        "name": "read_credential",
        "description": "Read stored credentials for a service from ~/.config/{service}/credentials.json.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name (e.g. 'github', 'openrouter')"
                }
            },
            "required": ["service"]
        }
    },
    # --- GitHub Tools ---
    {
        "name": "github_create_repo",
        "description": "Create a new GitHub repository. Use this to set up new project repositories. Returns the repository URL on success.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {
                    "type": "string",
                    "description": "Repository name (no spaces, use hyphens or underscores)"
                },
                "description": {
                    "type": "string",
                    "description": "Repository description (optional)"
                },
                "private": {
                    "type": "boolean",
                    "description": "Whether the repository should be private (default: false)",
                    "default": False
                }
            },
            "required": ["repo_name"]
        }
    },
    {
        "name": "github_push_file",
        "description": "Upload or update a file in a GitHub repository. Creates a new commit with the file change. Use this to push code, documentation, or configuration files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {
                    "type": "string",
                    "description": "Repository name (owner/repo format, e.g., 'username/my-repo')"
                },
                "file_path": {
                    "type": "string",
                    "description": "Path where the file should be stored in the repo (e.g., 'src/main.py')"
                },
                "content": {
                    "type": "string",
                    "description": "File content to upload"
                },
                "commit_message": {
                    "type": "string",
                    "description": "Commit message describing the change"
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name (default: 'main')",
                    "default": "main"
                }
            },
            "required": ["repo_name", "file_path", "content", "commit_message"]
        }
    },
    {
        "name": "github_get_repo_info",
        "description": "Get detailed information about a GitHub repository. Returns description, stars, forks, languages, default branch, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {
                    "type": "string",
                    "description": "Repository name (owner/repo format, e.g., 'username/my-repo')"
                }
            },
            "required": ["repo_name"]
        }
    },
    {
        "name": "github_list_repos",
        "description": "List all repositories for a GitHub user or organization. Use this to see what repos exist, find repository names, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "GitHub username or organization (optional, defaults to authenticated user)"
                },
                "type": {
                    "type": "string",
                    "description": "Repository type filter",
                    "enum": ["all", "owner", "public", "private", "member"],
                    "default": "all"
                }
            },
            "required": []
        }
    },
    {
        "name": "github_create_branch",
        "description": "Create a new branch in a GitHub repository. Use this to start working on a new feature or fix without affecting the main branch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {
                    "type": "string",
                    "description": "Repository name (owner/repo format, e.g., 'username/my-repo')"
                },
                "branch_name": {
                    "type": "string",
                    "description": "Name for the new branch"
                },
                "from_branch": {
                    "type": "string",
                    "description": "Branch to create from (default: 'main')",
                    "default": "main"
                }
            },
            "required": ["repo_name", "branch_name"]
        }
    },
    {
        "name": "github_get_file_content",
        "description": "Read the content of a file from a GitHub repository. Use this to inspect code, configuration, or documentation files in repos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {
                    "type": "string",
                    "description": "Repository name (owner/repo format, e.g., 'username/my-repo')"
                },
                "file_path": {
                    "type": "string",
                    "description": "Path to the file in the repository (e.g., 'src/main.py')"
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name (default: 'main')",
                    "default": "main"
                }
            },
            "required": ["repo_name", "file_path"]
        }
    },
    # --- Web Fetch Tool ---
    {
        "name": "run_shell",
        "description": "Execute a shell command on the host machine. Use this to start processes, run scripts, check status, manage files, or do anything that requires actually running code. Destructive operations (rm -rf, stopping critical services, vault access, touching other agents) are blocked. For long-running processes, use nohup or start a systemd service.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run. Use full paths when possible. Chain commands with && or ;. For background processes use nohup ... &"
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory. Defaults to project root if not specified."
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait for the command (default 60, max 300). Use a higher value for installs or builds."
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "claude_code",
        "description": "Ask Claude Code to write code, build a tool, debug something, or do any coding task. Claude Code runs in a sandboxed working directory and can create files, run commands, and iterate. Use this when you want to build something new, need working code written, or want a coding problem solved. Returns Claude Code's full output including any code it wrote and instructions on how to use it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "What you want Claude Code to build or do. Be specific: describe the tool, its inputs/outputs, any APIs or libraries to use, and where the result should live within the sandbox."
                },
                "context": {
                    "type": "string",
                    "description": "Optional: output from a previous claude_code call to continue from, or additional background context."
                },
                "subdir": {
                    "type": "string",
                    "description": "Optional: subdirectory within the sandbox to work in, e.g. 'projects/weather-tool'. Will be created if it doesn't exist."
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "read_current_investigation",
        "description": "Check what you're currently researching autonomously — your active working thread, how many cycles in, what the next step is, and recent completed investigations. Use this when your owner asks what you've been up to, or when you want to connect a conversation topic to ongoing research.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "start_investigation",
        "description": "Start a focused multi-cycle investigation on a goal. This replaces any current active thread and begins a new one that will advance across multiple heartbeat cycles until the goal is answered. Use this when you or your owner want to commit to researching something properly — not just a quick lookup, but a sustained inquiry that you'll pursue autonomously and report back on.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What you want to understand by the end — be specific about what 'done' looks like"
                },
                "title": {
                    "type": "string",
                    "description": "Short name for this investigation (defaults to first 80 chars of goal)"
                },
                "first_query": {
                    "type": "string",
                    "description": "The first search query to run (defaults to the goal itself)"
                }
            },
            "required": ["goal"]
        }
    },
    {
        "name": "add_to_agenda",
        "description": "Add a topic to your research agenda so you can investigate it between conversations. Use this when something comes up that you want to look into more deeply — a question your owner raised, something you're curious about, a follow-up thread worth pursuing. You'll research it autonomously and reach out to your owner when you find something worth sharing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The topic or question to research (be specific — good: 'how are other solo AI researchers monetizing agent tools?', bad: 'AI stuff')"
                },
                "context": {
                    "type": "string",
                    "description": "Why this is worth investigating — what prompted it, what would make the result useful"
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority: 1=high (research soon), 2=medium (default), 3=low (when nothing else pending)",
                    "enum": [1, 2, 3],
                    "default": 2
                }
            },
            "required": ["topic"]
        }
    },
    {
        "name": "fetch_url",
        "description": "Fetch content from a URL via HTTP. Supports GET and POST requests. Use this to retrieve web content, API responses, or data from external services. Content is truncated at 10KB.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch (must include protocol, e.g., 'https://example.com')"
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method to use",
                    "enum": ["GET", "POST"],
                    "default": "GET"
                },
                "data": {
                    "type": "object",
                    "description": "Data to send with POST requests (will be JSON-encoded)"
                },
                "headers": {
                    "type": "object",
                    "description": "HTTP headers to include in the request"
                }
            },
            "required": ["url"]
        }
    },
]


# --- Tool execution ---

PRIVILEGED_TOOLS = {
    "vault_get", "vault_set", "store_credential", "read_credential",
    "write_my_code", "edit_my_code", "git_commit",
    "run_shell", "update_my_instructions",
}


# ---------------------------------------------------------------------------
# Read/Write classification for parallel execution in relay.py
# ---------------------------------------------------------------------------

READ_TOOLS: frozenset[str] = frozenset({
    # Memory reads
    "read_memory", "search_memory", "list_entities",
    "read_timeline", "read_tacit", "review_own_conversations",
    # LIGHTHOUSE reads
    "lighthouse_read", "lighthouse_search",
    # Task reads
    "read_tasks",
    # IRC reads
    "list_irc_channels", "get_active_irc_channels",
    "search_irc_logs", "read_irc_channel",
    # Config reads
    "read_my_config",
    # Filesystem reads
    "list_files", "read_file", "search_files", "file_info", "list_backups",
    # Vault/credential reads
    "vault_get", "read_credential",
    # GitHub reads
    "github_get_repo_info", "github_list_repos", "github_get_file_content",
    # Browser/web reads
    "browse_url", "screenshot_url", "fetch_url",
    # Research reads
    "read_current_investigation",
})

WRITE_TOOLS: frozenset[str] = frozenset({
    # Memory writes
    "write_memory", "update_my_instructions", "log_experiment",
    # LIGHTHOUSE writes
    "lighthouse_write", "lighthouse_living",
    # Task writes
    "add_task", "complete_task",
    # Messaging
    "send_message_to_owner",
    # Browser (stateful interaction)
    "browser_interact",
    # IRC writes
    "send_irc_message", "update_irc_channels",
    "restart_irc_bot", "extract_irc_learnings",
    # Config writes
    "set_default_model", "update_config_setting", "restart_agent_service",
    # Filesystem writes
    "backup_myself", "restore_from_backup",
    "write_my_code", "edit_my_code", "git_commit",
    # Vault/credential writes
    "vault_set", "store_credential",
    # GitHub writes
    "github_create_repo", "github_push_file", "github_create_branch",
    # Shell / sub-agent (always write — side effects unknown)
    "run_shell", "claude_code",
    # Research writes
    "start_investigation", "add_to_agenda",
})


def _is_owner(user_id: str) -> bool:
    """Check if user_id matches the configured owner."""
    return user_id == config.owner_user_id()


def execute_tool(tool_name: str, tool_input: dict, session_store: Optional[SessionStore] = None, user_id: str = "owner", interface: str = "unknown") -> str:
    """Execute a tool and return the result as a string."""

    # Auth gate: privileged tools require owner identity
    if tool_name in PRIVILEGED_TOOLS and not _is_owner(user_id):
        logger.warning(f"Denied {tool_name} for non-owner user_id={user_id} interface={interface}")
        return f"Permission denied: '{tool_name}' requires owner authorization."

    if tool_name == "read_memory":
        entity = tool_input.get("entity", "")
        result = storage.read_entity(entity)
        if result is None:
            return f"I don't have any memory of '{entity}'."

        output = f"## {result['name'].replace('_', ' ').title()} ({result['category']})\n\n"
        output += result['summary'] + "\n"

        if result['recent_facts']:
            output += "\n### Recent Facts\n"
            for fact in result['recent_facts']:
                fact_text = fact.get("fact", fact.get("content", ""))
                fact_type = fact.get("category", fact.get("type", ""))
                fact_time = fact.get("timestamp", fact.get("extracted", ""))[:10]
                output += f"- [{fact_type}] {fact_text} ({fact_time})\n"

        return output

    elif tool_name == "search_memory":
        keyword = tool_input.get("keyword", "")
        results = storage.search_facts(keyword)

        if not results:
            return f"No memories found matching '{keyword}'."

        output = f"Found {len(results)} memories matching '{keyword}':\n\n"
        for r in results[:10]:
            fact = r['fact']
            fact_text = fact.get("fact", fact.get("content", ""))
            fact_type = fact.get("category", fact.get("type", ""))
            output += f"- **{r['entity']}**: {fact_text} ({fact_type})\n"

        return output

    elif tool_name == "list_entities":
        category = tool_input.get("category")
        entities = storage.list_entities_by_category(category)

        if not entities:
            if category:
                return f"No entities in category '{category}'."
            return "No entities in memory yet."

        output = "Entities in memory:\n\n"
        by_category = {}
        for e in entities:
            cat = e['category']
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(e)

        for cat, ents in by_category.items():
            output += f"### {cat.title()}\n"
            for e in ents:
                aliases = f" (aka: {', '.join(e['aliases'])})" if e['aliases'] else ""
                output += f"- {e['name']}{aliases}\n"
            output += "\n"

        return output

    elif tool_name == "write_memory":
        entity = tool_input.get("entity", "")
        fact = tool_input.get("fact", "")
        category = tool_input.get("category", "status")

        if not entity or not fact:
            return "Need both entity and fact to save."

        # Check if entity exists
        resolved = storage.resolve_entity(entity)
        if not resolved:
            return f"Unknown entity '{entity}'. I don't have that in my memory yet."

        try:
            fact_id = storage.add_fact(resolved, category, fact, source="agent_conversation")
            return f"Saved to {resolved}: {fact} (id: {fact_id})"
        except Exception as e:
            return f"Failed to save: {e}"

    elif tool_name == "read_timeline":
        date = tool_input.get("date", "")
        content = storage.read_timeline(date)
        if content is None:
            return f"No notes for {date}."
        return content

    elif tool_name == "read_tacit":
        return storage.read_tacit()

    elif tool_name == "review_own_conversations":
        hours = tool_input.get("hours", 24)
        if session_store is None:
            return "Session store not available."
        text = session_store.get_conversation_text(user_id, hours=hours)
        if not text:
            return f"No conversations in the last {hours} hours."
        return text

    elif tool_name == "update_my_instructions":
        new_instructions = tool_input.get("new_instructions", "")
        reasoning = tool_input.get("reasoning", "")

        if not new_instructions:
            return "No instructions provided."

        # Validate against L0 before applying
        from relay.l0_validator import validate_against_l0
        validation = validate_against_l0(new_instructions, reasoning)

        if not validation["allowed"]:
            return f"L0 VIOLATION — change rejected: {validation['reason']}"

        # Log the experiment
        old_version = storage.get_prompt_version()
        storage.log_experiment(
            hypothesis=f"Prompt change: {reasoning}",
            result="Applied, testing",
            status="testing"
        )

        # Save new version
        new_version = storage.save_system_prompt(new_instructions)
        return f"Instructions updated (v{old_version} → v{new_version}). Reasoning logged. Revert available."

    elif tool_name == "log_experiment":
        hypothesis = tool_input.get("hypothesis", "")
        result = tool_input.get("result", "")
        if not hypothesis:
            return "Need a hypothesis to log."
        storage.log_experiment(hypothesis, result)
        return f"Logged experiment: {hypothesis}"

    elif tool_name == "read_tasks":
        return storage.read_tasks()

    elif tool_name == "add_task":
        task = tool_input.get("task", "")
        if not task:
            return "No task provided."
        storage.add_task(task)
        return f"Added task: {task}"

    elif tool_name == "complete_task":
        task = tool_input.get("task", "")
        if not task:
            return "No task provided."
        success = storage.complete_task(task)
        if success:
            return f"Completed task: {task}"
        return f"Task not found: {task}"

    elif tool_name == "send_message_to_owner":
        from relay.telegram_sender import send_message_to_owner
        text = tool_input.get("text", "")
        if not text:
            return "Error: No message text provided"

        result = send_message_to_owner(text)
        if result.get("success"):
            return result.get("message", "Message sent successfully")
        else:
            return f"Failed to send: {result.get('error', 'Unknown error')}"

    # --- Browser / Firecrawl ---
    elif tool_name == "browse_url":
        from relay.browser import browse_url
        url = tool_input.get("url", "")
        if not url:
            return "Error: url is required"
        result = browse_url(url)
        if not result.get("success"):
            return f"Browse failed: {result.get('error')}"
        return f"# {result['title']}\nURL: {result['url']}\n\n{result['content']}"

    elif tool_name == "screenshot_url":
        from relay.browser import screenshot_url
        url = tool_input.get("url", "")
        full_page = tool_input.get("full_page", False)
        if not url:
            return "Error: url is required"
        result = screenshot_url(url, full_page=full_page)
        if not result.get("success"):
            return f"Screenshot failed: {result.get('error')}"
        # Return as a special marker — the relay needs to handle image responses
        return {
            "_type": "image_response",
            "title": result.get("title", ""),
            "url": result.get("url", ""),
            "image_b64": result["image_b64"],
            "media_type": result["media_type"],
        }

    elif tool_name == "browser_interact":
        from relay.browser import browser_interact
        url = tool_input.get("url", "")
        actions = tool_input.get("actions", [])
        screenshot_after = tool_input.get("screenshot_after", True)
        if not url or not actions:
            return "Error: url and actions are required"
        result = browser_interact(url, actions, screenshot_after=screenshot_after)
        if not result.get("success"):
            return f"Interaction failed: {result.get('error')}"
        lines = [
            f"# {result['title']}",
            f"URL: {result['url']}",
            f"Actions taken: {', '.join(result.get('actions_taken', []))}",
            f"\n{result['content']}",
        ]
        if result.get("image_b64"):
            return {
                "_type": "image_response",
                "text": "\n".join(lines),
                "image_b64": result["image_b64"],
                "media_type": result["media_type"],
            }
        return "\n".join(lines)

    elif tool_name == "send_irc_message":
        from relay.irc_sender import send_irc_message
        channel = tool_input.get("channel", "")
        message = tool_input.get("message", "")

        if not channel or not message:
            return "Error: Channel and message are required"

        result = send_irc_message(channel, message)
        if result.get("success"):
            return result.get("message", "Message sent to IRC")
        else:
            return f"Failed to send: {result.get('error', 'Unknown error')}"

    elif tool_name == "list_irc_channels":
        from relay.irc_explorer import list_channels
        min_users = tool_input.get("min_users", 10)
        limit = tool_input.get("limit", 50)

        result = list_channels(min_users=min_users, limit=limit)
        if result.get("success"):
            channels = result.get("channels", [])
            if not channels:
                return f"No channels found with {min_users}+ users"

            # Format channel list
            lines = [f"Found {len(channels)} active channels:\n"]
            for ch in channels[:20]:  # Show top 20
                lines.append(f"  {ch['name']} ({ch['users']} users) - {ch['topic'][:60]}")

            if len(channels) > 20:
                lines.append(f"\n...and {len(channels) - 20} more")

            return "\n".join(lines)
        else:
            return f"Failed to list channels: {result.get('error', 'Unknown error')}"

    elif tool_name == "update_irc_channels":
        from relay.irc_explorer import update_channels
        channels = tool_input.get("channels", [])

        if not channels:
            return "Error: No channels provided"

        result = update_channels(channels)
        if result.get("success"):
            return result.get("message", "Channels updated") + "\n" + result.get("note", "")
        else:
            return f"Failed to update channels: {result.get('error', 'Unknown error')}"

    elif tool_name == "get_active_irc_channels":
        from relay.irc_explorer import get_active_channels
        channels = get_active_channels()
        return f"Currently monitoring {len(channels)} channels: {', '.join(channels)}"

    elif tool_name == "restart_irc_bot":
        from relay.irc_explorer import restart_irc_bot
        result = restart_irc_bot()
        if result.get("success"):
            return result.get("message", "IRC bot restarted")
        else:
            return f"Failed to restart: {result.get('error', 'Unknown error')}"

    elif tool_name == "extract_irc_learnings":
        from relay.irc_memory import extract_irc_learnings
        hours = tool_input.get("hours", 24)
        channel = tool_input.get("channel")

        result = extract_irc_learnings(hours=hours, channel=channel)
        if result.get("success"):
            msg = result.get("message", "Extraction complete")
            channels = result.get("channels_reviewed", [])
            if channels:
                msg += f"\nChannels reviewed: {', '.join(channels)}"
            return msg
        else:
            return f"Failed to extract: {result.get('error', 'Unknown error')}"

    elif tool_name == "search_irc_logs":
        from relay.irc_memory import search_irc_logs
        keyword = tool_input.get("keyword", "")
        hours = tool_input.get("hours", 168)

        if not keyword:
            return "Error: No keyword provided"

        result = search_irc_logs(keyword=keyword, hours=hours)
        if result.get("success"):
            matches = result.get("matches", [])
            if not matches:
                return result.get("message", "No matches found")

            # Format results
            lines = [f"Found {len(matches)} matches for '{keyword}':\n"]
            for match in matches[:30]:  # Show max 30
                lines.append(f"  [{match['channel']}] {match['line']}")

            if len(matches) > 30:
                lines.append(f"\n...and {len(matches) - 30} more matches")

            return "\n".join(lines)
        else:
            return f"Search failed: {result.get('error', 'Unknown error')}"

    elif tool_name == "read_irc_channel":
        from relay.irc_memory import read_irc_channel
        channel = tool_input.get("channel", "")
        hours = tool_input.get("hours", 24)

        if not channel:
            return "Error: No channel provided"

        result = read_irc_channel(channel=channel, hours=hours)
        if result.get("success"):
            conversation = result.get("conversation", "")
            if not conversation:
                return result.get("message", "No recent activity")

            # Truncate if too long
            if len(conversation) > 2000:
                return f"{result.get('message')}\n\nRecent messages:\n{conversation[-2000:]}\n\n(Showing last 2000 chars)"
            else:
                return f"{result.get('message')}\n\n{conversation}"
        else:
            return f"Read failed: {result.get('error', 'Unknown error')}"

    elif tool_name == "read_my_config":
        from relay.config_manager import read_my_config
        result = read_my_config()
        if result.get("success"):
            config_data = result.get("config", {})
            if not config_data:
                return result.get("message", "No config found")

            lines = ["Current configuration:"]
            for key, value in config_data.items():
                lines.append(f"  {key}: {value}")

            return "\n".join(lines)
        else:
            return f"Failed to read config: {result.get('error', 'Unknown error')}"

    elif tool_name == "set_default_model":
        from relay.config_manager import set_default_model
        model_name = tool_input.get("model_name", "")

        if not model_name:
            return "Error: No model name provided"

        result = set_default_model(model_name)
        if result.get("success"):
            msg = result.get("message", "Model updated")
            note = result.get("note", "")
            return f"{msg}\n{note}" if note else msg
        else:
            return f"Failed to set model: {result.get('error', 'Unknown error')}"

    elif tool_name == "update_config_setting":
        from relay.config_manager import update_config_setting
        key = tool_input.get("key", "")
        value = tool_input.get("value")

        if not key:
            return "Error: No config key provided"

        result = update_config_setting(key, value)
        if result.get("success"):
            return result.get("message", "Config updated")
        else:
            return f"Failed to update config: {result.get('error', 'Unknown error')}"

    elif tool_name == "restart_agent_service":
        from relay.config_manager import restart_agent_service
        result = restart_agent_service()
        if result.get("success"):
            return result.get("message", "Service restarted")
        else:
            return f"Failed to restart: {result.get('error', 'Unknown error')}"

    elif tool_name == "list_files":
        from relay.filesystem import list_files
        path = tool_input.get("path", str(config.project_root()))

        result = list_files(path)
        if result.get("success"):
            items = result.get("items", [])
            if not items:
                return f"Directory is empty: {path}"

            lines = [f"Contents of {result.get('path')}:\n"]
            for item in items[:100]:  # Show max 100
                if item["type"] == "dir":
                    lines.append(f"  [DIR]  {item['name']}/")
                else:
                    size = item.get("size", 0)
                    size_str = f"{size:,} bytes" if size < 1024 else f"{size/1024:.1f} KB"
                    lines.append(f"  [FILE] {item['name']} ({size_str})")

            return "\n".join(lines)
        else:
            return f"Failed to list: {result.get('error', 'Unknown error')}"

    elif tool_name == "read_file":
        from relay.filesystem import read_file
        path = tool_input.get("path", "")
        max_lines = tool_input.get("max_lines", 500)

        if not path:
            return "Error: No file path provided"

        result = read_file(path, max_lines)
        if result.get("success"):
            content = result.get("content", "")
            lines = result.get("lines", 0)
            truncated = result.get("truncated", False)

            msg = f"File: {result.get('path')} ({lines} lines)\n\n{content}"
            if truncated:
                msg += f"\n\n[Truncated at {max_lines} lines]"
            return msg
        else:
            return f"Failed to read: {result.get('error', 'Unknown error')}"

    elif tool_name == "search_files":
        from relay.filesystem import search_files
        pattern = tool_input.get("pattern", "")
        base_path = tool_input.get("base_path", str(config.project_root()))
        max_results = tool_input.get("max_results", 50)

        if not pattern:
            return "Error: No search pattern provided"

        result = search_files(pattern, base_path, max_results)
        if result.get("success"):
            matches = result.get("matches", [])
            if not matches:
                return f"No files found matching '{pattern}' in {base_path}"

            lines = [f"Found {len(matches)} files matching '{pattern}':\n"]
            for match in matches[:30]:  # Show max 30
                size = match.get("size", 0)
                size_str = f"{size:,} bytes" if size < 1024 else f"{size/1024:.1f} KB"
                lines.append(f"  {match['path']} ({size_str})")

            if len(matches) > 30:
                lines.append(f"\n...and {len(matches) - 30} more")

            return "\n".join(lines)
        else:
            return f"Search failed: {result.get('error', 'Unknown error')}"

    elif tool_name == "file_info":
        from relay.filesystem import file_info
        path = tool_input.get("path", "")

        if not path:
            return "Error: No path provided"

        result = file_info(path)
        if result.get("success"):
            info = result.get("info", {})
            lines = [f"File Info: {info.get('name')}"]
            lines.append(f"  Type: {info.get('type')}")
            lines.append(f"  Path: {info.get('path')}")

            if info.get("type") == "file":
                size = info.get("size", 0)
                size_str = f"{size:,} bytes" if size < 1024 else f"{size/1024:.1f} KB"
                lines.append(f"  Size: {size_str}")
            else:
                lines.append(f"  Items: {info.get('item_count', 0)}")

            lines.append(f"  Permissions: {info.get('permissions')}")

            return "\n".join(lines)
        else:
            return f"Failed to get info: {result.get('error', 'Unknown error')}"

    elif tool_name == "backup_myself":
        from relay.self_management import backup_myself
        result = backup_myself()
        if result.get("success"):
            return result.get("message", "Backup created")
        else:
            return f"Backup failed: {result.get('error', 'Unknown error')}"

    elif tool_name == "list_backups":
        from relay.self_management import list_backups
        result = list_backups()
        if result.get("success"):
            backups = result.get("backups", [])
            if not backups:
                return "No backups found"

            lines = [f"Found {len(backups)} backups:\n"]
            for backup in backups:
                lines.append(f"  {backup['timestamp']} - {backup['size_mb']} MB")
                lines.append(f"    Created: {backup['created']}")
                lines.append(f"    Path: {backup['path']}\n")

            return "\n".join(lines)
        else:
            return f"Failed to list backups: {result.get('error', 'Unknown error')}"

    elif tool_name == "restore_from_backup":
        from relay.self_management import restore_from_backup
        timestamp = tool_input.get("timestamp", "")

        if not timestamp:
            return "Error: No timestamp provided"

        result = restore_from_backup(timestamp)
        if result.get("success"):
            msg = result.get("message", "Restored")
            note = result.get("note", "")
            return f"{msg}\n{note}" if note else msg
        else:
            return f"Restore failed: {result.get('error', 'Unknown error')}"

    elif tool_name == "write_my_code":
        from relay.self_management import write_my_code
        filepath = tool_input.get("filepath", "")
        content = tool_input.get("content", "")

        if not filepath:
            return "Error: No filepath provided"
        if not content:
            return "Error: No content provided"

        result = write_my_code(filepath, content)
        if result.get("success"):
            return result.get("message", "File written")
        else:
            return f"Write failed: {result.get('error', 'Unknown error')}"

    elif tool_name == "git_commit":
        from relay.self_management import git_commit
        message = tool_input.get("message", "")
        files = tool_input.get("files")

        if not message:
            return "Error: No commit message provided"

        result = git_commit(message, files)
        if result.get("success"):
            return result.get("message", "Committed")
        else:
            return f"Commit failed: {result.get('error', 'Unknown error')}"

    elif tool_name == "edit_my_code":
        from relay.self_management import edit_my_code
        filepath = tool_input.get("filepath", "")
        old_str = tool_input.get("old_str", "")
        new_str = tool_input.get("new_str", "")
        if not filepath or old_str == "":
            return "Error: filepath and old_str required"
        result = edit_my_code(filepath, old_str, new_str)
        if result.get("success"):
            return result.get("message", "Edit applied")
        else:
            return f"Edit failed: {result.get('error', 'Unknown error')}"

    elif tool_name == "vault_get":
        from relay.self_management import vault_get
        key = tool_input.get("key", "")
        if not key:
            return "Error: key required"
        result = vault_get(key)
        if result.get("success"):
            value = result['value']
            if len(value) > 8:
                masked = value[:4] + "..." + value[-4:]
            else:
                masked = "****"
            return f"vault[{key}] exists (value: {masked}). Use this key name at runtime; the full value is loaded automatically."
        else:
            return f"vault_get failed: {result.get('error')}"

    elif tool_name == "vault_set":
        from relay.self_management import vault_set
        key = tool_input.get("key", "")
        value = tool_input.get("value", "")
        if not key or not value:
            return "Error: key and value required"
        result = vault_set(key, value)
        if result.get("success"):
            return result.get("message", "Stored")
        else:
            return f"vault_set failed: {result.get('error')}"

    elif tool_name == "store_credential":
        from relay.self_management import store_credential
        service = tool_input.get("service", "")
        data = tool_input.get("data", {})
        if not service or not data:
            return "Error: service and data required"
        result = store_credential(service, data)
        if result.get("success"):
            return result.get("message", "Stored")
        else:
            return f"store_credential failed: {result.get('error')}"

    elif tool_name == "read_credential":
        from relay.self_management import read_credential
        service = tool_input.get("service", "")
        if not service:
            return "Error: service required"
        result = read_credential(service)
        if result.get("success"):
            creds = result.get("credentials", {})
            masked = {k: (v[:4] + "..." + v[-4:] if isinstance(v, str) and len(v) > 8 else "****") for k, v in creds.items()}
            return f"Credentials for '{service}': {json.dumps(masked)}\nKeys available: {', '.join(creds.keys())}"
        else:
            return f"read_credential failed: {result.get('error')}"

    # --- GitHub Tools ---
    elif tool_name.startswith("github_"):
        from relay.github_tools import execute_github_tool
        result = execute_github_tool(tool_name, **tool_input)

        if result.get("success"):
            # Format successful response based on tool type
            if tool_name == "github_create_repo":
                return f"{result.get('message')}\n\nRepository URL: {result.get('url')}"

            elif tool_name == "github_push_file":
                return f"{result.get('message')}\n\nCommit URL: {result.get('url')}"

            elif tool_name == "github_get_repo_info":
                info = result.get("info", {})
                lines = [f"Repository: {info.get('full_name')}"]
                lines.append(f"  Description: {info.get('description', 'No description')}")
                lines.append(f"  Default branch: {info.get('default_branch')}")
                lines.append(f"  Stars: {info.get('stargazers_count', 0)}")
                lines.append(f"  Forks: {info.get('forks_count', 0)}")
                lines.append(f"  Language: {info.get('language', 'Unknown')}")
                lines.append(f"  Private: {info.get('private', False)}")
                lines.append(f"  URL: {info.get('html_url')}")
                return "\n".join(lines)

            elif tool_name == "github_list_repos":
                repos = result.get("repos", [])
                if not repos:
                    return "No repositories found"

                lines = [f"Found {len(repos)} repositories:\n"]
                for repo in repos[:30]:  # Show max 30
                    stars = repo.get("stargazers_count", 0)
                    private = " [PRIVATE]" if repo.get("private") else ""
                    desc = repo.get("description", "No description")[:60]
                    lines.append(f"  {repo['name']}{private} - {desc} ({stars} stars)")

                if len(repos) > 30:
                    lines.append(f"\n...and {len(repos) - 30} more")

                return "\n".join(lines)

            elif tool_name == "github_create_branch":
                return f"{result.get('message')}\n\nBranch: {result.get('branch_name')}"

            elif tool_name == "github_get_file_content":
                content = result.get("content", "")
                path = result.get("path", "")
                # Truncate long content
                if len(content) > 5000:
                    content = content[:5000] + f"\n\n[Content truncated - file has {len(content)} characters total]"
                return f"File: {path}\n\n{content}"

            else:
                # Generic success response
                return result.get("message", "Operation completed successfully")
        else:
            # Format error response
            return f"GitHub error: {result.get('error', 'Unknown error')}"

    # --- Web Fetch Tool ---
    elif tool_name == "fetch_url":
        import requests

        url = tool_input.get("url", "")
        method = tool_input.get("method", "GET").upper()
        data = tool_input.get("data")
        headers = tool_input.get("headers", {})

        if not url:
            return "Error: No URL provided"

        try:
            # Set timeout and size limit
            timeout = 10
            max_size = 10 * 1024  # 10KB

            # Make request
            if method == "POST":
                if data:
                    headers["Content-Type"] = "application/json"
                    response = requests.post(url, json=data, headers=headers, timeout=timeout, stream=True)
                else:
                    response = requests.post(url, headers=headers, timeout=timeout, stream=True)
            else:
                response = requests.get(url, headers=headers, timeout=timeout, stream=True)

            # Check status
            response.raise_for_status()

            # Read content with size limit
            content_chunks = []
            total_size = 0

            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    content_chunks.append(chunk)
                    total_size += len(chunk)
                    if total_size >= max_size:
                        break

            content = b"".join(content_chunks).decode("utf-8", errors="replace")

            # Format response
            lines = [f"URL: {url}"]
            lines.append(f"Status: {response.status_code}")
            lines.append(f"Content-Type: {response.headers.get('Content-Type', 'Unknown')}")
            lines.append(f"\nContent:\n{content}")

            if total_size >= max_size:
                lines.append(f"\n[Content truncated at {max_size} bytes]")

            return "\n".join(lines)

        except requests.exceptions.Timeout:
            return f"Error: Request timed out after {timeout} seconds"
        except requests.exceptions.ConnectionError:
            return f"Error: Could not connect to {url}"
        except requests.exceptions.HTTPError as e:
            return f"Error: HTTP {e.response.status_code} - {e.response.reason}"
        except requests.exceptions.RequestException as e:
            return f"Error: Request failed - {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    # --- LIGHTHOUSE ---
    elif tool_name == "lighthouse_write":
        from relay.lighthouse import write_entry
        section = tool_input.get("section", "")
        title = tool_input.get("title", "")
        content = tool_input.get("content", "")
        tags = tool_input.get("tags", None)

        if not section or not title or not content:
            return "Error: section, title, and content are all required."

        result = write_entry(section, title, content, tags)
        if result.get("success"):
            return f"LIGHTHOUSE entry saved: {result['filename']} in {result['section']}/"
        else:
            return f"Failed to write LIGHTHOUSE entry: {result.get('error')}"

    elif tool_name == "lighthouse_read":
        from relay.lighthouse import read_entries
        section = tool_input.get("section", None)
        limit = tool_input.get("limit", 10)

        result = read_entries(section, limit)
        if not result.get("success"):
            return f"Error: {result.get('error')}"

        entries = result.get("entries", [])
        if not entries:
            return result.get("message", "No entries found.")

        parts = []
        for e in entries:
            parts.append(f"[{e['section']}/{e['filename']}]\n{e['content']}")
        return "\n\n---\n\n".join(parts)

    elif tool_name == "lighthouse_search":
        from relay.lighthouse import search_entries
        query = tool_input.get("query", "")

        result = search_entries(query)
        if not result.get("success"):
            return f"Error: {result.get('error')}"

        matches = result.get("matches", [])
        if not matches:
            return result.get("message", f"No matches for '{query}'.")

        lines = [f"Found {len(matches)} entries matching '{query}':\n"]
        for m in matches:
            lines.append(f"  [{m['section']}/{m['filename']}] — {m['total_matches']} match(es)")
            for lineno, line in m["matching_lines"][:3]:
                lines.append(f"    L{lineno}: {line.strip()}")
        return "\n".join(lines)

    elif tool_name == "lighthouse_living":
        from relay.lighthouse import write_living
        observation = tool_input.get("observation", "")

        if not observation:
            return "Error: observation is required."

        result = write_living(observation)
        if result.get("success"):
            return result.get("message", "Observation added.")
        else:
            return f"Failed to write living observation: {result.get('error')}"

    elif tool_name == "run_shell":
        from relay.shell_tool import run_shell
        return run_shell(
            command=tool_input.get("command", ""),
            cwd=tool_input.get("cwd"),
            timeout=tool_input.get("timeout", 60),
        )

    elif tool_name == "claude_code":
        from relay.claude_code_tool import run_claude_code
        return run_claude_code(
            prompt=tool_input.get("prompt", ""),
            context=tool_input.get("context"),
            subdir=tool_input.get("subdir"),
        )

    elif tool_name == "read_current_investigation":
        from relay.working_memory import read_status
        status = read_status()
        thread = status.get("active_thread")
        if not thread:
            recent = status.get("recent_completed", [])
            if recent:
                lines = ["No active investigation.\n\nRecently completed:"]
                for t in recent:
                    lines.append(f"- **{t['title']}** ({t['cycles']} cycles, {t['status']})")
                return "\n".join(lines)
            return "No active investigation and no completed threads yet."

        lines = [
            f"**Active investigation:** {thread['title']}",
            f"**Goal:** {thread['goal']}",
            f"**Progress:** cycle {thread['cycle_count']}/{thread['max_cycles']}",
            f"**Next step:** {thread['next_step']}",
            f"**Started:** {thread['started_at'][:10]}",
        ]
        if thread.get("steps_summary"):
            lines.append("\n**Steps so far:**")
            for s in thread["steps_summary"]:
                lines.append(f"  Cycle {s['cycle']}: {s['query']}")
        return "\n".join(lines)

    elif tool_name == "start_investigation":
        from relay.working_memory import start_thread
        goal = tool_input.get("goal", "").strip()
        if not goal:
            return "Error: goal is required"
        title = tool_input.get("title", "")
        first_query = tool_input.get("first_query", "")
        thread = start_thread(goal=goal, title=title, first_query=first_query)
        return (
            f"Investigation started: **{thread.title}**\n"
            f"Goal: {thread.goal}\n"
            f"First query: {thread.next_step}\n\n"
            f"I'll research this across the next few heartbeat cycles and reach out to you when I have something."
        )

    elif tool_name == "add_to_agenda":
        from relay.agenda import get_agenda
        topic = tool_input.get("topic", "").strip()
        if not topic:
            return "Error: topic is required"
        context = tool_input.get("context", "")
        priority = tool_input.get("priority", 2)
        result = get_agenda().add(topic=topic, context=context, priority=priority, source="conversation")
        if result.get("added"):
            return f"Added to research agenda: '{topic}' (priority {priority}). I'll look into this between conversations and reach out when I find something worth sharing."
        else:
            return f"Similar topic already in agenda: '{result.get('existing', {}).get('topic', topic)}'"

    else:
        return f"Unknown tool: {tool_name}"
