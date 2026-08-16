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
- Preserves the distinction between facts marked `[UNVERIFIED]` above and everything else. An `[UNVERIFIED]` fact came from a lower-trust source (an offhand remark, or content extracted from something other than the owner's own words) — mention it with visible hedging ("mentioned once that...", "unconfirmed:...") if it's worth including at all, never phrase it with the same flat, declarative confidence as the other facts. Don't silently launder an unverified claim into a stated fact just because rewriting it that way reads more cleanly.

The summary should read like a brief you'd give someone who asks "what's the deal with [entity]?"

Output ONLY the summary text. No preamble, no "Here's the summary:", just the summary itself.
