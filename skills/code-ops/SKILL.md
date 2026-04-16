---
name: code-ops
version: 1.0.0
description: |
  Self-modification, file operations, git commits, and GitHub repo management.
  Owner-only skill — all tools require owner authorization.
triggers:
  - "edit your code"
  - "write a file"
  - "commit"
  - "push to github"
  - "create a repo"
  - "backup yourself"
  - "update your instructions"
tools:
  - write_my_code
  - edit_my_code
  - git_commit
  - list_files
  - read_file
  - search_files
  - file_info
  - backup_myself
  - list_backups
  - restore_from_backup
  - github_create_repo
  - github_push_file
  - github_get_repo_info
  - github_list_repos
  - github_create_branch
  - github_get_file_content
  - run_shell
  - vault_get
  - vault_set
  - store_credential
  - read_credential
  - update_my_instructions
mutating: true
---

# Code Ops Skill — Self-Modification & File Management

> **Convention:** See `skills/conventions/owner-auth.md` — all tools in this skill
> require owner authorization.

This skill covers three domains:
1. **Self-modification** — editing the agent's own code and instructions
2. **Local filesystem** — reading, writing, searching files within the agent directory
3. **GitHub** — remote repository management

## Contract

This skill guarantees:
- All writes are within the agent directory (no escaping to system paths)
- `write_my_code` uses atomic writes via temp files (no partial writes)
- `edit_my_code` uses safe string replacement (old_str must match exactly)
- Git commits have meaningful messages
- Self-modification via `update_my_instructions` is L0-validated and versioned
- Backups capture code + memory state
- Shell commands are blocklist-validated before execution

## Phases

### Phase 1: Understand Before Modifying

Before any code change:
1. `read_file(path)` — read the current file
2. `search_files(query)` — find related code
3. Understand the change in context

### Phase 2: Execute Change

**For edits:** `edit_my_code(filepath, old_str, new_str)` — surgical string replacement.
Preferred over full-file writes for existing files.

**For new files:** `write_my_code(filepath, content)` — atomic write via temp file.

**For commits:** `git_commit(message, files?)` — commit with descriptive message.

### Phase 3: Verify

After any code change:
1. Check the file looks correct (`read_file`)
2. If TypeScript: `run_shell("npx tsc --noEmit")` — must be clean
3. If Python: `run_shell("python -c 'import module'")` — syntax check

### GitHub Operations

- `github_create_repo(name)` — create new repo
- `github_push_file(repo, path, content, message)` — push file
- `github_get_file_content(repo, path)` — read remote file
- `github_list_repos()` — enumerate repos

### Self-Modification

`update_my_instructions(new_instructions, reasoning)` modifies the agent's system
prompt overlay. This is the most powerful tool — use it deliberately:
1. State the change clearly
2. Provide reasoning that aligns with L0 constraints
3. The change is versioned and reversible
4. Log the experiment: `log_experiment(hypothesis, result)`

### Vault & Credentials

- `vault_get(key)` — returns masked value (never full secret in context)
- `vault_set(key, value)` — stores via stdin (not CLI args)
- `store_credential(name, value)` — local credential storage
- `read_credential(name)` — returns key name + masked preview only

## Anti-Patterns

- Writing code without reading the file first
- Making changes without understanding the surrounding code
- Committing without checking TypeScript/Python compilation
- Using `write_my_code` for small edits (use `edit_my_code` instead)
- Storing secrets in code files instead of vault
- Self-modifying instructions without logging the experiment

## Tools Used

All 22 tools listed in the frontmatter. See TOOLS.md for full signatures.
