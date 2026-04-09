# Summary Synthesis Prompt

Rewrite this entity summary based on current active facts.

Entity: {name}
Category: {category}

## Current Facts
{facts}

## Previous Summary
{previous_summary}

---

Write a new summary that:
- Can be read in 30 seconds
- Captures CURRENT state, not history
- Highlights what's most relevant RIGHT NOW
- Uses natural language, not bullet points
- Is concise but complete

The summary should read like a brief you'd give someone who asks "what's the deal with [entity]?"

Output ONLY the summary text. No preamble, no "Here's the summary:", just the summary itself.
