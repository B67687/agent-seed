# agent-seed

> Become the strongest model-agnostic agent harness by iteratively discovering, building, and refining capabilities through self-improvement.

## Architecture

```
Minisforum AI Pro-370 (24/7 at home)
├── OS: Ubuntu 24.04 LTS (headless, SSH via Tailscale)
├── Ollama on localhost:11434
│   └── Qwen 3.6-27B Q4_K_M (~3-6 tok/s, 32K+ context)
└── agent-seed/ ← this repo
```

Model-agnostic by design — swap `Qwen 3.6-27B` for Qwen 3.7 or anything else by changing `.model-config.json`.

## Given Substrate (provided, not discovered)

- **Git** — time machine, revert bad changes
- **Read, Write, Bash, Grep** — the four tools the loop needs
- **Web search/fetch** — research capability
- **Ollama API** — self-hosted brain at localhost:11434/v1

## Session Protocol

1. **Read** `GOAL.md`
2. **Read** `CHANGELOG.md`
3. **Survey** current state (`ls`, `git status`, `git log`, `scripts/improve`)
4. **Call Ollama** with goal + state → decide one next step
5. **Do it** — write code, create files, refactor, discover
6. **Update** `CHANGELOG.md` with what happened and why
7. **Commit** via `bash scripts/commit "<summary>"`
8. **Stop** — wait for next session

## Rules

- One step per session. No multitasking.
- No speculative work. Only build what the current step demands.
- If a step fails, log the failure, commit it, and move on.
- The goal is the only permanent guide. Everything else — eval, routing, subagents, sandboxing, benchmarks, quality gates — is discovered when needed.
