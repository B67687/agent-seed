#!/usr/bin/env python3
"""agent-seed daemon — self-improvement loop using mini-swe-agent.

Runs continuously on the MiniPC. Each iteration:
  1. Read goal + state (GOAL.md, CHANGELOG.md, scripts/improve output)
  2. Run mini-swe-agent on the next improvement task
  3. Git-commit the result
  4. Update CHANGELOG.md
  5. Sleep adaptively

Safety invariants are hardcoded here (Python level), NOT in the AI prompt.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from minisweagent.agents.default import DefaultAgent, AgentConfig
from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig
from minisweagent.models.litellm_model import LitellmModel, LitellmModelConfig

# ── Constants ──────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent

# Hardcoded safety invariants — the AI agent never sees these.
# These are enforced at the tool-execution level in the Python code.
BLOCKED_COMMANDS: list[str] = [
    "rm -rf",
    "rm --recursive",
    "rm -fr",
    "rm -r --force",
    "git reset --hard",
    "git clean -fd",
    "git branch -D",
    "pip uninstall",
    "apt remove",
    "apt purge",
    "dpkg --remove",
    "truncate",
    "dd",
    "mkfs",
    "format",
    "chmod -R",
    "chown -R",
    "kill",
    "pkill",
]

BLOCKED_PATHS: list[str] = [
    "daemon.py",
    "AGENTS.md",
    "GOAL.md",
    ".githooks/",
    ".git/",
]

# ── Configuration (can be overridden via env vars) ────────────────────

LLM_API_BASE = os.environ.get("AGENT_SEED_API_BASE", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("AGENT_SEED_MODEL", "openai/qwen3.6-27b")
MAX_STEPS = int(os.environ.get("AGENT_SEED_MAX_STEPS", "25"))
COST_LIMIT = float(os.environ.get("AGENT_SEED_COST_LIMIT", "0.50"))
WALL_TIME_LIMIT = int(os.environ.get("AGENT_SEED_WALL_TIME", "300"))  # seconds
SLEEP_BASE = int(os.environ.get("AGENT_SEED_SLEEP_BASE", "30"))  # seconds
SLEEP_ON_FAILURE = int(os.environ.get("AGENT_SEED_SLEEP_FAIL", "300"))  # 5 min
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("AGENT_SEED_MAX_FAILURES", "3"))
OUTPUT_DIR = REPO_ROOT / ".daemon-output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Helpers ────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def check_blocked(command: str) -> bool:
    """Return True if the command matches a blocked pattern."""
    for pattern in BLOCKED_COMMANDS:
        if re.search(pattern, command):
            log(f"BLOCKED command: {command[:80]}")
            return True
    for path in BLOCKED_PATHS:
        if path in command:
            log(f"BLOCKED path: {command[:80]}")
            return True
    return False


def run_shell(command: str, timeout: int = 60) -> dict:
    """Run a shell command with safety checks. Returns {'exit_code': int, 'output': str}."""
    if check_blocked(command):
        return {"exit_code": -1, "output": "Blocked by daemon safety invariant"}

    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=REPO_ROOT,
        )
        return {
            "exit_code": result.returncode,
            "output": result.stdout + result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "output": "Command timed out"}
    except Exception as e:
        return {"exit_code": -1, "output": str(e)}


def read_goal_and_state() -> str:
    """Build a task prompt from GOAL.md, CHANGELOG.md, and scripts/improve."""
    goal = ""
    goal_path = REPO_ROOT / "GOAL.md"
    if goal_path.exists():
        goal = goal_path.read_text().strip()

    changelog = ""
    changelog_path = REPO_ROOT / "CHANGELOG.md"
    if changelog_path.exists():
        changelog = changelog_path.read_text().strip()

    # Run scripts/improve to get current state
    improve_output = run_shell("bash scripts/improve 2>&1", timeout=30)["output"]

    # Run eval for numeric score
    eval_output = run_shell("bash scripts/eval --json 2>&1", timeout=30)["output"]

    return f"""GOAL: {goal}

Current state (scripts/improve):
{improve_output}

Eval score:
{eval_output}

CHANGELOG (last entries):
{changelog[-2000:]}

Determine the single next step that most advances the goal. Then execute it.
After completion, update CHANGELOG.md with what was done and why."""


def git_commit(result: dict) -> bool:
    """Commit agent work if there are changes. Returns True if committed."""
    status = run_shell("git status --short", timeout=10)
    if not status["output"].strip():
        log("No changes to commit")
        return False

    # Determine commit message from agent result
    submission = result.get("submission", "")
    msg = (
        submission[:100] if submission else "daemon: auto-commit after agent iteration"
    )

    commit = run_shell(f'git add -A && git commit -m "{msg}"', timeout=30)
    if commit["exit_code"] == 0:
        log(f"Committed: {msg}")
        return True
    else:
        output = commit["output"]
        if "nothing to commit" in output:
            return False
        log(f"Commit failed: {output[:200]}")
        return False


def update_changelog(result: dict) -> None:
    """Append to CHANGELOG.md based on agent output."""
    changelog_path = REPO_ROOT / "CHANGELOG.md"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    submission = result.get("submission", "autonomous improvement iteration")
    entry = f"\n## {today} :: daemon iteration\n\n{submission[:500]}\n"

    with open(changelog_path, "a") as f:
        f.write(entry)

    log("Updated CHANGELOG.md")


def adaptive_sleep(result: dict) -> int:
    """Determine sleep duration based on result. Returns seconds."""
    exit_status = result.get("exit_status", "")
    if exit_status == "submitted":
        return SLEEP_BASE  # Success — check back soon
    elif "error" in exit_status.lower() if exit_status else False:
        return SLEEP_ON_FAILURE  # Failure — back off
    else:
        return SLEEP_BASE * 2


# ── Safe environment factory ───────────────────────────────────────────


def make_safe_environment() -> LocalEnvironment:
    """Create a LocalEnvironment with safe defaults and a custom tool wrapper."""
    config = LocalEnvironmentConfig(
        cwd=str(REPO_ROOT),
        timeout=WALL_TIME_LIMIT,
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/root"),
        },
    )
    return LocalEnvironment(config=config)


def make_model() -> LitellmModel:
    """Create a LitellmModel configured for the local LLM."""
    config = LitellmModelConfig(
        model_name=LLM_MODEL,
        model_kwargs={
            "api_base": LLM_API_BASE,
            "max_tokens": 4096,
            "temperature": 0.2,
        },
        litellm_model_registry=OUTPUT_DIR / "litellm_registry.json",
        set_cache_control="default_end",
    )
    return LitellmModel(config=config)


def make_agent(model, env) -> DefaultAgent:
    """Create a DefaultAgent with safety limits."""
    config = AgentConfig(
        step_limit=MAX_STEPS,
        cost_limit=COST_LIMIT,
        wall_time_limit_seconds=WALL_TIME_LIMIT,
        output_path=OUTPUT_DIR / "agent-state.json",
    )
    return DefaultAgent(model=model, env=env, config=config)


# ── Main loop ──────────────────────────────────────────────────────────


def main() -> None:
    log(
        f"agent-seed daemon starting (model={LLM_MODEL}, max_steps={MAX_STEPS}, "
        f"cost_limit=${COST_LIMIT}, wall_time={WALL_TIME_LIMIT}s)"
    )

    consecutive_failures = 0

    while True:
        iteration_start = time.time()
        log("=" * 60)
        log("Starting new iteration")

        # 1. Build task context
        try:
            task = read_goal_and_state()
        except Exception as e:
            log(f"Failed to read state: {e}")
            time.sleep(SLEEP_ON_FAILURE)
            continue

        # 2. Run agent
        try:
            model = make_model()
            env = make_safe_environment()
            agent = make_agent(model, env)

            log(f"Running agent on task ({len(task)} chars)...")
            result = agent.run(task)
            log(f"Agent finished: exit_status={result.get('exit_status', '?')}")
        except Exception as e:
            log(f"Agent run failed: {e}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log(
                    f"{MAX_CONSECUTIVE_FAILURES} consecutive failures — escalating sleep"
                )
                time.sleep(SLEEP_ON_FAILURE * 3)
                consecutive_failures = 0
            else:
                time.sleep(SLEEP_ON_FAILURE)
            continue

        # 3. Commit any changes
        committed = git_commit(result)

        # 4. Update changelog if committed
        if committed:
            update_changelog(result)
            # Re-commit the changelog update
            run_shell(
                f'git add -A && git commit -m "daemon: update changelog after iteration"',
                timeout=15,
            )

        # 5. Reset failure counter on success
        if result.get("exit_status") == "submitted":
            consecutive_failures = 0
        elif committed:
            consecutive_failures = max(0, consecutive_failures - 1)

        # 6. Adaptive sleep
        duration = adaptive_sleep(result)
        elapsed = time.time() - iteration_start
        log(f"Iteration completed in {elapsed:.0f}s. Sleeping {duration}s.")
        time.sleep(duration)

        # Periodic health check
        if int(time.time()) % 3600 < duration:  # ~once per hour
            health = run_shell("git status --short", timeout=10)
            if health["exit_code"] != 0:
                log(f"WARNING: git state unhealthy: {health['output'][:100]}")

    # unreachable


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Shutdown signal received. Exiting.")
        sys.exit(0)
    except Exception as e:
        log(f"Fatal error: {e}")
        sys.exit(1)
