---
name: web-research
version: 1.0.0
description: |
  Browse URLs, fetch pages, take screenshots, and interact with web content.
  The web access layer for research and content retrieval.
triggers:
  - owner shares a URL
  - "browse this"
  - "what's on this page"
  - "screenshot"
  - "fetch"
  - URL detected in message
tools:
  - browse_url
  - screenshot_url
  - browser_interact
  - fetch_url
mutating: false
---

# Web Research Skill

Web access tools for fetching, rendering, and interacting with online content.

## Contract

This skill guarantees:
- Memory is checked FIRST (convention: memory-first) before web research
- `browse_url` returns clean markdown (JS-rendered via Firecrawl)
- `screenshot_url` returns base64 PNG for visual content
- `browser_interact` handles dynamic pages (forms, logins, scroll-to-load)
- `fetch_url` handles raw HTTP for APIs and JSON endpoints
- Bot protection is handled transparently

## Tool Selection

| Need | Tool | Notes |
|------|------|-------|
| Read a web page | `browse_url(url)` | Firecrawl renders JS, returns markdown |
| Visual capture | `screenshot_url(url)` | Base64 PNG, useful for layouts/charts |
| Fill forms / click buttons | `browser_interact(url, actions)` | Interactive session |
| Raw HTTP (API, JSON) | `fetch_url(url, method?, headers?, body?)` | GET or POST |

## Phases

### Phase 1: URL Detection

When the owner shares a URL or asks about web content:
1. Identify what they want (read content, screenshot, interact, or raw fetch)
2. Pick the right tool

### Phase 2: Fetch

Execute the appropriate tool. If one fails, pivot:
- `browse_url` fails → try `fetch_url` for raw content
- `fetch_url` blocked → try `browse_url` (Firecrawl handles bot protection)
- Page needs interaction → `browser_interact` with click/scroll actions

### Phase 3: Extract & Store

After fetching:
1. Extract key information relevant to the owner's question
2. If entities mentioned → feed into signal-detector / memory-ops
3. If research context → feed into research skill's working memory

## Anti-Patterns

- Using `fetch_url` for JS-heavy pages (use `browse_url`)
- Using `browse_url` for simple API endpoints (use `fetch_url`)
- Fetching entire pages when a specific section was asked about
- Not pivoting when a tool fails (try another angle)

## Tools Used

- `browse_url(url)` — Firecrawl-rendered page → markdown
- `screenshot_url(url)` — visual capture → base64 PNG
- `browser_interact(url, actions)` — interactive session (click, write, scroll)
- `fetch_url(url, method?, headers?, body?)` — raw HTTP request
