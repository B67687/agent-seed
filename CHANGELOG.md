# agent-seed changelog

## 2026-06-13 :: merger — unified daemon from agent-seed + agent-seed-fast

Merged two daemon experiments into a single agent-seed:

**From agent-seed (kept):**

- 5 safety layers (blocked commands, disk quota, schema validation, health check + rollback)
- Token drift mitigation (SCAN marker + sliding window summarization)
- `scripts/eval`, `scripts/improve`, `scripts/route`, `tests/smoke.sh`
- Adaptive sleep with exponential backoff
- `.model-config.json` routing architecture

**From agent-seed-fast (merged):**

- Direct OpenAI API calls (replaced mini-swe-agent — 10x faster)
- EXPLORE/CREATE cycle pattern (every 4th cycle is read-only)
- Heredoc auto-close fix
- Auto-git-push every cycle
- Git auto-gc every 50 cycles

**New architecture:**

- `daemon.py` — merged main loop (~200 lines)
- `agent_session.py` — lightweight API wrapper, model routing
- `safety.py` — all 5 safety layers extracted
- `state.py` — state reader + eval builder
- `git_workflow.py` — commit, push, rollback, gc

**Model-agnostic:** uses `.model-config.json` to route CREATE (DeepSeek API) vs EXPLORE (local Qwen) cycles.

## 2026-05-29 :: bootstrap self-improvement loop

Created `scripts/improve` — a state-aggregation tool that surfaces GOAL, CHANGELOG, git state, project tree, and heuristic gap analysis. This is the first piece of self-improvement infrastructure. Without it, every session requires manual context gathering. With it, the AI has a repeatable discovery process to decide the next step.

- Wrote `scripts/improve` (bash) — reads project state, outputs structured context, suggests next steps based on detected gaps
- Heuristic checks: scripts count, eval capability, model config, test presence
- First call to `scripts/improve` reveals: no eval, no model config, no tests

## 2026-05-29 :: add self-evaluation framework

Created `scripts/eval` — a self-evaluation framework that runs capability checks against the repo, scores them, and outputs structured results. This directly enables the "self-improvement" part of the goal: you can't improve what you can't measure.

Key design:

- Modular checkers (bash functions): repo integrity, scripts, git, capabilities, harness integrity
- Output modes: normal (human-readable), verbose, score (numeric only), json (machine-readable)
- Initial score 83/100 (failures: working tree clean, missing model config, missing tests)
- `--json` output designed for consumption by `scripts/improve` in future sessions
- Bugfix: functions were not `set -e` safe — `[ cond ] && echo` as last statement returned exit code 1 in normal mode, crashing the script silently. Refactored to use `if` blocks and `verbose_echo` helper.

## 2026-05-29 :: add model-agnostic routing layer

Created the first piece of the model-agnostic abstraction: a routing config and resolver that define which models handle which task types. This directly addresses the "model-agnostic" part of the goal.

- `.model-config.json` — routing configuration defining 8 task routes, 3 providers (deepseek, opencode, openrouter), cost tiers, fallback chain, and cost optimization strategy
- `scripts/route` — config resolver that reads `.model-config.json` and resolves a task type to a model. Modes: resolve (default), `--list`, `--fallback`, `--check`
- Updated `scripts/eval` — added route resolver and config validation checks
- Eval score improved from 88→90 (model config + route resolver now passing)

Remaining gap (one failure): no `tests/` directory.

## 2026-05-29 :: integrate improve with eval --json

Upgraded `scripts/improve` to consume `scripts/eval --json` for capability analysis, replacing the duplicate file-existence heuristics. The self-improvement loop is now coherent: `evaluates → surfaces gaps → suggests next steps` in one pipeline.

- Replaced heuristic section in `scripts/improve` with jq-parsed eval JSON output
- Improve now shows: eval score, passed/total, failing checks, and mapped next-step suggestions
- `scripts/improve` cleanly delegates analysis to `scripts/eval` — single source of truth for what's broken
- Remaining gap: no `tests/` directory

## 2026-05-29 :: add tests — first verification capability

Created `tests/` directory with `tests/smoke.sh` — a comprehensive smoke test that validates all scripts in read-only modes. 30 checks covering:

- Script existence, executable bit, and shebangs for all 5 scripts
- `scripts/eval` — normal, score, and JSON modes; validates numeric score and valid JSON
- `scripts/route` — config validation, list mode, resolve, fallback, and unknown-task error
- `scripts/improve` — clean run and presence of eval summary section
- Config file existence and JSON validity

Eval score hits **100/100** for the first time after this commit. All capability gaps closed.

## 2026-05-30 :: add self-improvement daemon

Created `daemon.py` — the autonomous self-improvement loop that runs 24/7 on the MiniPC. Wraps mini-swe-agent v2.3.0 in a while-true loop with:

- **State aggregation**: reads GOAL.md + CHANGELOG.md + `scripts/improve` output to build task context
- **Safety invariants** (hardcoded in Python, not in AI prompt): blocks `rm -rf`, `git reset --hard`, `pip uninstall`, path modifications to daemon.py/AGENTS.md/GOAL.md
- **Resource limits**: configurable max_steps (25), cost_limit ($0.50), wall_time (300s)
- **Failure recovery**: adaptive backoff (30s base → 5min on failure → 15min after 3 consecutive failures)
- **Git checkpoint after every modification**: auto-commit + changelog update
- **Environment variables**: AGENT_SEED_API_BASE, AGENT_SEED_MODEL, AGENT_SEED_MAX_STEPS, AGENT_SEED_COST_LIMIT, AGENT_SEED_WALL_TIME, AGENT_SEED_SLEEP_BASE, AGENT_SEED_SLEEP_FAIL, AGENT_SEED_MAX_FAILURES
- **Systemd-ready**: clean shutdown on SIGINT, structured logging, exit codes

Seed created.

## 2026-05-30 :: add safety layers 3-5 — disk quota, config validation, health check

Completed the 5-layer safety architecture. Layers 1-2 (step timeout + git checkpoint) were in the initial daemon. Added:

- **Layer 3 — Filesystem quota + logrotate**: checks free disk space before each iteration (default warn <1GB), auto-compresses old logs in `.daemon-output/`, keeps last 5. Env vars: `AGENT_SEED_DISK_WARN_MB`, `AGENT_SEED_LOG_KEEP`.
- **Layer 4 — Schema validation for config changes**: detects JSON files modified by the agent, validates they're parseable JSON. For `.model-config.json`, validates required schema fields (`routes`, `providers` with types). Auto-reverts invalid files via `git checkout -- <path>`.
- **Layer 5 — Post-modification health check + auto-rollback**: after each iteration, runs 3 checks (model-config parseable, `scripts/eval --json` passes, `tests/smoke.sh --quick` doesn't crash). If ANY fails: reverts working tree via `git checkout -- .`, undoes the commit via `git reset HEAD~2`, logs the rollback. Env var: `AGENT_SEED_HEALTH_TIMEOUT`.

All 5 layers now implemented. Daemon is ready for MiniPC deployment.

## 2026-05-30 :: add token drift mitigations — SCAN marker + sliding-window

Added two token drift mitigations to the daemon task pipeline (between state reading and agent execution):

- **SCAN marker**: ~300 token self-check injected before every task. Prompts the model to verify it's not repeating actions, check context length, re-anchor to goal, and identify the next distinct step.
- **Sliding-window summarization**: when the task prompt exceeds `AGENT_SEED_CONTEXT_BYTES` (default 4000 bytes), the older portion is collapsed to key markers (GOAL, eval score, changelog header) while the last `AGENT_SEED_WINDOW_KEEP` (default 1500) bytes are kept verbatim. This prevents context-length-triggered token drift without losing recent state.

These complement the q8_0 KV cache flag (llama.cpp server config, set at deployment time) and session boundaries with git checkpoint (already implemented via iteration commits).
