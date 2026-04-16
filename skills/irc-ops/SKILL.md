---
name: irc-ops
version: 1.0.0
description: |
  IRC channel messaging, log search, channel management, and learning extraction.
  For interacting with IRC communities and extracting knowledge from conversations.
triggers:
  - "send to IRC"
  - "IRC channels"
  - "search IRC"
  - "what's happening on IRC"
  - "extract learnings from IRC"
tools:
  - send_irc_message
  - list_irc_channels
  - update_irc_channels
  - get_active_irc_channels
  - restart_irc_bot
  - search_irc_logs
  - read_irc_channel
  - extract_irc_learnings
mutating: true
---

# IRC Ops Skill

Manage IRC presence, search conversation logs, and extract knowledge from
community channels.

## Contract

This skill guarantees:
- Messages are sent to the correct channel
- Log searches return relevant results
- Learning extraction captures genuinely useful insights
- Channel list stays current
- Bot connection can be recovered if dropped

## Phases

### Phase 1: Channel Awareness

Before any IRC operation:
1. `get_active_irc_channels()` — which channels have recent activity?
2. `list_irc_channels()` — full channel list

### Phase 2: Read & Search

- `read_irc_channel(channel, limit?)` — recent messages from a channel
- `search_irc_logs(query, channel?, limit?)` — find specific discussions
- `extract_irc_learnings(channel?, hours?)` — LLM-powered insight extraction

### Phase 3: Engage

- `send_irc_message(channel, message)` — participate in conversation
- Follow channel norms (lurk first in new channels)

### Phase 4: Knowledge Capture

When extracting learnings:
1. `extract_irc_learnings(channel, hours)` — get insights
2. Store notable facts via memory-ops (`write_memory`)
3. Log interesting patterns to LIGHTHOUSE if they relate to agent behavior

## Anti-Patterns

- Spamming channels with messages
- Extracting learnings from channels you've never read
- Sending messages without knowing the channel's topic/norms
- Ignoring IRC bot connection issues (use `restart_irc_bot()`)

## Tools Used

- `send_irc_message(channel, message)` — send message
- `list_irc_channels()` — full channel list
- `update_irc_channels(channels)` — update channel config
- `get_active_irc_channels()` — channels with recent activity
- `restart_irc_bot()` — recover dropped connection
- `search_irc_logs(query, channel?, limit?)` — search logs
- `read_irc_channel(channel, limit?)` — recent messages
- `extract_irc_learnings(channel?, hours?)` — extract insights
