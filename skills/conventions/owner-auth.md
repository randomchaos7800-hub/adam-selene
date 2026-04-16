# Convention: Owner Authorization

Privileged tools require owner identity verification before execution.

**Privileged tools:**
- `vault_get`, `vault_set`
- `store_credential`, `read_credential`
- `write_my_code`, `edit_my_code`, `git_commit`
- `run_shell`
- `update_my_instructions`

**Enforcement:** `execute_tool()` checks `user_id == config.owner_user_id()`.
Non-owner requests return `"Permission denied"` and are logged.

**Interface tracking:** Every tool call records the originating interface
(slack, telegram, irc) for audit purposes.

**Apply to:** code-ops, config-ops, and any skill that invokes privileged tools.
IRC users, Slack guests, and unknown user_ids are non-owners by default.
