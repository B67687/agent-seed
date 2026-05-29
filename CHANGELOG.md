# agent-seed changelog

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

Seed created.
