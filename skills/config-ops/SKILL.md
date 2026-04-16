---
name: config-ops
version: 1.0.0
description: |
  Runtime configuration: model switching, settings updates, service management.
  Read current state or make changes to agent behavior at runtime.
triggers:
  - "change model"
  - "switch to"
  - "what model are you using"
  - "update settings"
  - "restart"
  - "read config"
tools:
  - read_my_config
  - set_default_model
  - update_config_setting
  - restart_agent_service
mutating: true
---

# Config Ops Skill

Runtime configuration management. Change models, update settings, restart the
service — all without editing files manually.

## Contract

This skill guarantees:
- Current config is readable at any time
- Model changes take effect on next inference call
- Settings updates are validated (LRU cache cleared on change)
- Service restart is clean (systemd managed, auto-restart enabled)

## Phases

### Phase 1: Read Current State

`read_my_config()` returns the full settings.json:
- Current models (main, extraction, heartbeat, local)
- Context window sizes
- Extraction frequency
- Interface configuration
- Heartbeat timing

### Phase 2: Make Changes

**Model switching:** `set_default_model(model)` — changes the primary inference model.
Takes effect on next message. Common models: gemma-4-31b, glm-4.7-flash, etc.

**Settings update:** `update_config_setting(key, value)` — update any setting.
Uses dot notation: `"openrouter.heartbeat_model"`, `"context.max_output_tokens"`, etc.

### Phase 3: Service Management

`restart_agent_service()` — restart via systemd. Use when:
- Settings changes require a fresh start
- The agent is in a bad state
- After code modifications via code-ops

## Anti-Patterns

- Changing models without understanding the tradeoffs (context size, speed, cost)
- Restarting the service when a config reload would suffice
- Changing extraction frequency without understanding the impact on API costs

## Tools Used

- `read_my_config()` — read current settings
- `set_default_model(model)` — change primary model
- `update_config_setting(key, value)` — update a setting
- `restart_agent_service()` — restart via systemd
