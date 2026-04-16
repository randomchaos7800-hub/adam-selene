---
name: task-manager
version: 1.0.0
description: |
  Task lifecycle: add, complete, review active tasks. Simple task tracking
  for the owner's work items.
triggers:
  - "add a task"
  - "what's on my plate"
  - "task list"
  - "mark that done"
  - "complete task"
tools:
  - read_tasks
  - add_task
  - complete_task
mutating: true
---

# Task Manager Skill

Simple task tracking. Not a project management system — just a list of things
the owner wants to track.

## Contract

This skill guarantees:
- Tasks are persistent (survive restarts)
- Task status is always current when displayed
- Completed tasks are acknowledged
- No task is silently dropped

## Phases

### Phase 1: Intent Detection

- "Add a task" / "remind me to" / "I need to" → add_task
- "What's on my plate" / "task list" / "what do I need to do" → read_tasks
- "Done with" / "finished" / "complete" / "mark done" → complete_task

### Phase 2: Execution

**Adding:** Extract title and optional description from the owner's message.
Don't over-formalize — keep the owner's phrasing.

**Reviewing:** Show all active tasks. If there are many, group or prioritize
based on context from memory.

**Completing:** Match the owner's description to a task ID. If ambiguous,
show options and ask which one.

## Anti-Patterns

- Adding tasks the owner didn't ask for
- Over-formalizing casual mentions into tasks
- Silently completing tasks without confirmation
- Showing completed tasks in the active list

## Tools Used

- `read_tasks()` — list active tasks
- `add_task(title, description?)` — create a task
- `complete_task(task_id)` — mark done
