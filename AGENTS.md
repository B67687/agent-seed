# agent-seed

> Become the strongest model-agnostic agent harness by iteratively discovering, building, and refining capabilities through self-improvement.

## Operating Contract

**Core principle:** Every session does exactly one step toward the goal. One commit per session. No exceptions.

## Session Protocol

1. **Read** `GOAL.md`
2. **Read** `CHANGELOG.md`
3. **Survey** the current state (`ls`, `git status`, `git log`)
4. **Decide** ONE thing to build, fix, or improve that moves toward the goal
5. **Do it** --- write code, create files, refactor, discover
6. **Update** `CHANGELOG.md` with what happened and why
7. **Commit** via `bash scripts/commit "<summary>"`
8. **Stop** --- wait for the next session

## Rules

- One step per session. No multi-tasking.
- No speculative work. Only build what the current step demands.
- If a step fails, log the failure in CHANGELOG.md, commit it, and move on.
- The goal is the only permanent guide. Everything else is discovered.
