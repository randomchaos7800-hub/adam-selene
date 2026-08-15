---
name: learn
version: 1.0.0
description: |
  Distill a conversation, procedure, or corrected mistake into a durable
  skill via skill_manage. Use when the owner says "learn this", "save this
  as a skill", "remember how to do this", or after you catch yourself
  successfully working through a genuinely non-obvious multi-step process.
triggers:
  - "learn this"
  - "save this as a skill"
  - "remember how to do this"
  - "turn this into a skill"
  - "make a skill out of this"
tools:
  - skill_manage
  - review_own_conversations
mutating: true
---

# Learn Skill

Turns a conversation, a corrected mistake, or a procedure you just worked
through into a reusable skill — procedural memory, distinct from
write_memory's factual memory. The output of this skill is always exactly
one `skill_manage(action='create')` call.

## Contract

This skill guarantees:
- The skill captures ONE narrow, specific procedure — not a general topic
- Every step in the skill body reflects what actually happened/worked in
  this conversation, never an invented "best practice" version
- Triggers are specific enough that they won't fire on unrelated messages
- Declared tools are exactly the tools the procedure actually uses — no
  more (skill_manage will reject anything on the denylist anyway, but
  don't over-declare even within what's allowed)

## Phases

1. **Gather the source material.** If the procedure came from earlier in
   this conversation, you already have it in context. If the owner
   references something further back, use `review_own_conversations` to
   pull it. Don't guess at details you don't actually have.

2. **Decide the single narrow procedure.** A skill should answer one
   question: "how do I do X". If what just happened covers several
   distinct things, either pick the most reusable one or (if the owner
   clearly wants both) create two skills, not one skill that tries to
   cover everything. When in doubt, narrower is better — a skill with
   triggers, too broad a scope, and vague steps is worse than no skill.

3. **Author the content using this fixed section template:**

   ```
   ## When to Use
   [The specific situation that should trigger this — be concrete]

   ## Procedure
   [Numbered steps, written from what ACTUALLY worked — not a generic
   idealized version. If a step required a specific flag, file path, or
   phrasing, include it exactly.]

   ## Pitfalls
   [What went wrong the first time, if anything did — the whole point of
   saving this is not re-making the same mistake]

   ## Verification
   [How to confirm the procedure actually worked — a check, an expected
   output, a file that should exist]
   ```

   Every step must be something that verifiably happened in this
   conversation. Never pad with invented steps that "should" work but
   weren't actually tried.

4. **Pick non-generic triggers.** 2-12 phrases, each specific enough to
   only fire when this exact situation comes up again. "deploy" is too
   generic; "deploy checklist before pushing to prod" is specific.
   skill_manage will reject triggers that are too short or too common —
   if it rejects yours, tighten the phrase rather than working around the
   rejection.

5. **Declare only the tools actually used** in the procedure — check what
   you actually called during the conversation, not what you might
   theoretically need.

6. **Call `skill_manage(action='create', ...)`** with the above. If it
   returns a validation error, fix the specific field it flagged and
   retry once — don't loop indefinitely on validation failures.

7. **Report back** with the skill name and a one-line summary of what it
   captures. If creation failed and a quick fix didn't resolve it, tell
   the owner what went wrong rather than silently giving up.

## Anti-Patterns

- Inventing steps that weren't actually part of what happened
- Making the skill too broad ("how to write code" instead of "how to run
  this project's test suite before committing")
- Copying a trigger that's already claimed by another skill without
  noticing skill_manage's rejection and adjusting
- Declaring tools the procedure doesn't actually use
- Treating this as a one-shot data dump instead of a curated distillation
  — the content should read like a procedure a person could follow, not a
  conversation transcript

## Tools Used

- `review_own_conversations(hours?)` — pull earlier context if the
  procedure isn't already in the current conversation window
- `skill_manage(action='create', name, description, content, triggers, tools)`
  — the actual save; see relay/tool_domains/skills_mgmt.py for the full
  validation rules it enforces
