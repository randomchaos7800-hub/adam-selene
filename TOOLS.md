# Adam Selene Tool Reference

## Tool Philosophy — A Wall Is Not a Stop Sign

Tools are broader than their descriptions. When one path is blocked, reason about what you actually need and find another tool that can get there.

**The rule:** Before asking your owner for help, try at least two other angles.

**Common pivots:**
- GitHub API fails → `browse_url` the repo page directly
- Memory miss → `search_memory` with broader keyword → `search_files` → `read_file`
- Web search dead end → `browse_url` the site directly
- Tool returns truncated data → call again with narrower scope
- Need to check if something exists → `run_shell("ls path")`

**The principle:** You have a shell. Almost anything on this machine can be done with `run_shell`. It's not a last resort — it's a first-class tool.

---

## Memory Tools

### `read_memory(entity)`
Load what you know about a person, project, company, or concept. Returns summary and recent facts.

### `search_memory(keyword)`
Search all memories for a keyword or phrase. Returns matching facts across all entities.

### `list_entities(category?)`
List all entities in memory. Optional filter: people, projects, companies, concepts.

### `write_memory(entity, fact, category)`
Save a fact to the knowledge graph. Categories: status, milestone, constraint, preference, relationship, decision.

### `read_timeline(date)`
Read what happened on a specific day from daily notes. Format: YYYY-MM-DD.

### `read_tacit()`
Read tacit knowledge about how your owner thinks — preferences, patterns, communication style.

### `review_own_conversations(hours?)`
Read your conversation history. Default: last 24 hours.

### `update_my_instructions(new_instructions, reasoning)`
Modify your own system prompt. Versioned and reversible. Must align with L0 constraints.

### `log_experiment(hypothesis, result, status?)`
Document a behavioral experiment. Track what you tried, what happened, and whether it worked.

### `read_memory_history(entity, at_time)`
See what was true about an entity as of a past date — not just what's true now. Reconstructs historical state from each fact's validity window, including facts that have since been superseded (`read_memory` only ever shows current active facts).

---

## Skills Tools

### `skill_manage(action, name, ...)`
Create, patch, or archive your own skills — procedural memory for *how to do things*, distinct from `write_memory`'s factual memory. `action: 'create'` needs `description`, `content`, `triggers` (2-12 phrases), and `tools`; validated against a denylist (shell/code-edit/config/vault/service-restart tools can never be declared) and a hard cap on self-created skills. `action: 'patch'`/`'archive'` only work on skills this tool itself created — hand-authored skills are read-only to it. See `relay/tool_domains/skills_mgmt.py` for the full validation rules, and `skills/learn/SKILL.md` for the workflow that drives it.

---

## LIGHTHOUSE Tools

### `lighthouse_write(section, title, content)`
Write an entry to your reasoning journal. Sections: reasoning, corrections, conversations, patterns, tools, map, identity.

### `lighthouse_read(section?, limit?)`
Read recent entries from a LIGHTHOUSE section.

### `lighthouse_search(query)`
Search across all LIGHTHOUSE sections for a keyword or phrase.

### `lighthouse_living(content)`
Update the living document in LIGHTHOUSE/identity — your evolving self-observations.

---

## Task Tools

### `read_tasks()`
List all active tasks.

### `add_task(title, description?)`
Add a new task.

### `complete_task(task_id)`
Mark a task as completed.

---

## Browser Tools

### `browse_url(url)`
Fetch a URL and return clean markdown content. Uses Firecrawl for JS-rendered pages. Handles bot protection.

### `screenshot_url(url)`
Take a screenshot of a URL. Returns base64 PNG.

### `browser_interact(url, actions)`
Interactive browser session. Actions: click, write, scroll, screenshot. For forms, logins, dynamic content.

---

## Web Tools

### `fetch_url(url, method?, headers?, body?)`
Direct HTTP request. GET or POST. Returns raw response content.

---

## GitHub Tools

### `github_create_repo(name, description?, private?)`
Create a new GitHub repository.

### `github_push_file(repo, path, content, message, branch?)`
Upload or update a file in a repository.

### `github_get_repo_info(repo)`
Get repository details (description, stars, language, etc.).

### `github_list_repos()`
List your GitHub repositories.

### `github_create_branch(repo, branch, from_branch?)`
Create a new branch in a repository.

### `github_get_file_content(repo, path, branch?)`
Read a file from a GitHub repository.

---

## IRC Tools

### `send_irc_message(channel, message)`
Send a message to an IRC channel.

### `list_irc_channels()`
List configured IRC channels.

### `update_irc_channels(channels)`
Update the IRC channel list.

### `get_active_irc_channels()`
Get channels with recent activity.

### `restart_irc_bot()`
Restart the IRC bot connection.

### `search_irc_logs(query, channel?, limit?)`
Search IRC conversation logs.

### `read_irc_channel(channel, limit?)`
Read recent messages from an IRC channel.

### `extract_irc_learnings(channel?, hours?)`
Extract interesting facts or insights from IRC conversations.

---

## Shell Tool

### `run_shell(command, timeout?, cwd?)`
Execute a shell command, sandboxed via bubblewrap. Returns stdout + stderr + exit code.

**Sandbox** (the real boundary — see `relay/shell_tool.py` / `ARCHITECTURE.md`): host filesystem read-only except the project and memory directories (read-write); common credential locations and this framework's own `config/secrets.env` explicitly masked even within a writable bind; environment cleared to a small non-secret allowlist; process/namespace isolated. Fails closed (refuses to run) if bubblewrap isn't installed, rather than silently running unsandboxed — Linux-only, see README's Requirements section for the non-Linux opt-out.

**Regex blocklist** (first-pass filter only, not the security boundary):
- Mass delete (`rm -rf /`)
- Device writes (`dd of=/dev/`)
- Vault access
- Code injection (`curl|bash`)
- Force push (`git push --force`)
- SSH key modification

Timeout: 60s default, 300s max.

---

## Filesystem Tools

### `list_files(path?, pattern?)`
List files in a directory with optional glob pattern.

### `read_file(path)`
Read a file's content.

### `search_files(query, path?, pattern?)`
Search file contents with regex.

### `file_info(path)`
Get file metadata (size, modified time, permissions).

### `write_my_code(filepath, content)`
Write a file within the agent directory. Atomic writes via temp files.

### `edit_my_code(filepath, old_str, new_str)`
Safe string replacement in a file within the agent directory.

### `git_commit(message, files?)`
Commit changes to the agent's git repository.

### `backup_myself()`
Create a backup of the agent's code and memory.

---

## Config Tools

### `read_my_config()`
Read the current settings.json.

### `set_default_model(model_name)`
Change the primary inference model. Accepts common aliases like `haiku`, `sonnet`, `opus`, or a full provider/model ID.

### `update_config_setting(key, value)`
Update a setting in `settings.json`. Supported keys: `models.main`, `models.extraction`, `heartbeat.idle_minutes`, `heartbeat.enabled`, `heartbeat.model_override`, `extraction.idle_timeout_seconds`, `extraction.incremental_every_n_messages`, `context.max_output_tokens`, `local.base_url`, `local.model`, `openrouter.model`, `openrouter.fallback_model`, `openrouter.heartbeat_model`, `autoresearch.base_url`, `service_name`.

### `restart_agent_service()`
Restart the agent's systemd service.

---

## Messaging Tools

### `send_message_to_owner(text)`
Send a proactive message to your owner via their primary interface. Used by heartbeat for research findings.

---

## Introspection Tools

### `list_capabilities()`
See which tools are available on the *current channel*. Untrusted interfaces (e.g. public IRC) get a narrower tool surface regardless of who's asking — check this before attempting something that might be denied. See `relay/capabilities.py`.
