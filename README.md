# agent-seed

> Build the strongest model-agnostic agent harness through self-improvement.

A self-improving autonomous daemon that runs a while-true loop: read state,
call LLM, execute actions, validate changes, commit, health-check, repeat.

## Architecture

```
daemon.py          — merged main loop (~200 lines)
agent_session.py   — lightweight API wrapper, model routing from .model-config.json
safety.py          — 5 safety layers (blocked commands, disk quota, JSON validation, health check)
state.py           — reads GOAL.md + git state + eval results
git_workflow.py    — commit, push, rollback, gc
```

## Configuration

Edit `.model-config.json` to set up model providers and routing:

```json
"providers": {
    "deepseek": { "type": "api", "base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY" },
    "local":    { "type": "local", "base_url": "http://localhost:11434/v1" }
}
"routes": {
    "create":  { "model": "deepseek-chat", "provider": "deepseek", "access": "read_write" },
    "explore": { "model": "qwen3.6:27b", "provider": "local", "access": "read_only" }
}
```

Set API keys via environment variables (e.g. `DEEPSEEK_API_KEY`).

## Running

```bash
python3 daemon.py
```

Environment variables:

- `AGENT_SEED_SLEEP` — base sleep between cycles (default: 60s)
- `AGENT_SEED_SLEEP_FAIL` — sleep after failure (default: 300s)
- `AGENT_SEED_MAX_FAILURES` — consecutive failures before escalation (default: 3)
- `AGENT_SEED_EXPLORE_INTERVAL` — every Nth cycle is EXPLORE (default: 4)
- `AGENT_SEED_PUSH` — auto-push to origin main (default: true)
- `DEEPSEEK_API_KEY` — API key for DeepSeek (if using deepseek provider)

## Safety

5 safety layers, enforced at the Python level (never trust the model):

1. **Blocked commands** — `rm -rf`, `git reset --hard`, dangerous patterns
2. **Blocked paths** — `daemon.py`, `safety.py`, `AGENTS.md`, `GOAL.md` etc.
3. **Disk quota + log rotation** — checks free space, compresses old logs
4. **JSON schema validation** — validates config file changes, auto-reverts
5. **Health check + rollback** — post-commit checks, git rollback on failure

## Scripts

- `scripts/eval` — self-evaluation framework
- `scripts/improve` — state aggregation for improvement decisions
- `scripts/route` — model route resolver
- `scripts/go` — iteration protocol reference
- `scripts/commit` — safe commit wrapper

## Tests

```bash
bash tests/smoke.sh
```
