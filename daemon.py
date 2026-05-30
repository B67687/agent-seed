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

import gzip
import json
import os
import re
import shutil
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

# ── Token drift mitigation constants ────────────────────────────────

SCAN_MARKER = """[SELF-CHECK]
Pause. Before proceeding:
1. What session turn am I on? (rough estimate based on context length)
2. Am I repeating a previous action? Check git log for recent duplicates.
3. Is the goal still: advance the strongest model-agnostic agent harness?
4. What is the single next step that most advances this goal?

If you detect repetition, drift, or context degradation, stop and summarize
what you've done so far, then identify the next distinct step. Do NOT repeat
the last action unless there's evidence it failed.
"""

CONTEXT_SUMMARY_TRIGGER = int(os.environ.get("AGENT_SEED_CONTEXT_BYTES", "4000"))
"""Task prompt bytes above this trigger sliding-window summarization."""

WINDOW_KEEP_VERBATIM = int(os.environ.get("AGENT_SEED_WINDOW_KEEP", "1500"))
"""Keep this many bytes verbatim from the end of the task prompt. Summarize everything older."""


def inject_scan_marker(task: str) -> str:
    """Inject a SCAN marker before the task to re-focus the model and detect drift."""
    return SCAN_MARKER + "\n\n" + task


def summarize_if_long(task: str) -> str:
    """Apply sliding-window summarization if the task prompt exceeds threshold."""
    if len(task.encode("utf-8")) <= CONTEXT_SUMMARY_TRIGGER:
        return task

    # Keep the end verbatim (recent state, eval score, changelog)
    keep_bytes = min(WINDOW_KEEP_VERBATIM, len(task.encode("utf-8")) // 2)
    task_bytes = task.encode("utf-8")
    cutoff = len(task_bytes) - keep_bytes
    # Find clean byte boundary
    while cutoff > 0 and cutoff < len(task_bytes) and task_bytes[cutoff] & 0xC0 == 0x80:
        cutoff -= 1
    old_part = task_bytes[:cutoff].decode("utf-8", errors="replace")
    recent_part = task_bytes[cutoff:].decode("utf-8", errors="replace")

    # Summarize old part by truncating to key sections
    lines = old_part.split("\n")
    summary_lines: list[str] = []
    seen_goal = False
    for line in lines:
        if line.startswith("GOAL:") and not seen_goal:
            summary_lines.append(line)
            seen_goal = True
        elif line.startswith("Eval score:"):
            summary_lines.append(line + " (see below for full)")
        elif line.startswith("CHANGELOG"):
            summary_lines.append(line)
        elif line.startswith("Current state"):
            summary_lines.append(line)
        elif (
            line.strip()
            and summary_lines
            and summary_lines[-1].startswith("Current state")
        ):
            summary_lines.append("  [...] summarized — full output below")
            summary_lines.append("")

    summarized = "\n".join(summary_lines)
    result = f"[CONTEXT SUMMARIZED — older sections collapsed to key markers]\n\n{summarized}\n\n---\n\n{recent_part}"
    log(
        f"Context compressed: {len(task.encode('utf-8'))} -> {len(result.encode('utf-8'))} bytes "
        f"(kept last {keep_bytes}b verbatim)"
    )
    return result


# ── Configuration (can be overridden via env vars) ────────────────────

LLM_API_BASE = os.environ.get("AGENT_SEED_API_BASE", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("AGENT_SEED_MODEL", "openai/qwen3.6-27b")
MAX_STEPS = int(os.environ.get("AGENT_SEED_MAX_STEPS", "25"))
COST_LIMIT = float(os.environ.get("AGENT_SEED_COST_LIMIT", "0.50"))
WALL_TIME_LIMIT = int(os.environ.get("AGENT_SEED_WALL_TIME", "300"))  # seconds
SLEEP_BASE = int(os.environ.get("AGENT_SEED_SLEEP_BASE", "30"))  # seconds
SLEEP_ON_FAILURE = int(os.environ.get("AGENT_SEED_SLEEP_FAIL", "300"))  # 5 min
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("AGENT_SEED_MAX_FAILURES", "3"))

# Layer 3 (filesystem quota) and Layer 5 (health check) configuration
DISK_WARN_MB = int(os.environ.get("AGENT_SEED_DISK_WARN_MB", "1024"))
LOG_KEEP = int(os.environ.get("AGENT_SEED_LOG_KEEP", "5"))
HEALTH_TIMEOUT = int(os.environ.get("AGENT_SEED_HEALTH_TIMEOUT", "60"))

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


# ── Layer 3: Filesystem quota + logrotate ────────────────────────────


def check_disk_quota() -> bool:
    """Check if free disk space is above the warning threshold. Returns True if OK."""
    usage = shutil.disk_usage(REPO_ROOT)
    free_mb = usage.free / (1024 * 1024)
    if free_mb < DISK_WARN_MB:
        log(
            f"DISK QUOTA: {free_mb:.0f} MB free (threshold: {DISK_WARN_MB} MB)"
            " — skipping iteration"
        )
        return False
    log(f"Disk OK: {free_mb:.0f} MB free")
    return True


def rotate_logs() -> None:
    """Rotate log files in .daemon-output/ — keep last N, compress older ones."""
    logs = sorted(
        OUTPUT_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if len(logs) <= LOG_KEEP:
        return
    for old in logs[LOG_KEEP:]:
        compressed = old.with_suffix(old.suffix + ".gz")
        try:
            with open(old, "rb") as f_in:
                with gzip.open(compressed, "wb") as f_out:
                    f_out.writelines(f_in)
            old.unlink()
            log(f"Rotated (compressed): {old.name}")
        except OSError as e:
            log(f"Log rotation failed for {old.name}: {e}")


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


# ── Layer 4: Schema validation for config changes ─────────────────────


def validate_model_config_schema(data: dict) -> list[str]:
    """Validate .model-config.json structure. Returns list of error messages (empty = valid)."""
    errors: list[str] = []
    if "providers" not in data:
        errors.append("missing required field: 'providers'")
    elif not isinstance(data["providers"], dict):
        errors.append("'providers' must be an object")

    if "routes" not in data:
        errors.append("missing required field: 'routes'")
    elif not isinstance(data["routes"], dict):
        errors.append("'routes' must be an object")

    if "routes" in data and isinstance(data["routes"], dict):
        for route_name, route_config in data["routes"].items():
            if not isinstance(route_config, dict):
                errors.append(f"route '{route_name}' must be an object")
                continue
            if "model" not in route_config:
                errors.append(f"route '{route_name}' missing required field: 'model'")
            elif not isinstance(route_config["model"], str):
                errors.append(f"route '{route_name}':'model' must be a string")
            if "access" not in route_config:
                errors.append(f"route '{route_name}' missing required field: 'access'")
            elif route_config["access"] not in ("read_write", "read_only"):
                errors.append(
                    f"route '{route_name}':'access' must be 'read_write' or 'read_only'"
                )

    if "providers" in data and isinstance(data["providers"], dict):
        for provider_name, provider_config in data["providers"].items():
            if not isinstance(provider_config, dict):
                errors.append(f"provider '{provider_name}' must be an object")
                continue
            if "type" not in provider_config:
                errors.append(
                    f"provider '{provider_name}' missing required field: 'type'"
                )

    return errors


def validate_json_changes() -> None:
    """Validate JSON files modified in the last iteration. Reverts invalid files."""
    # Determine diff range: HEAD~2 for agent work + changelog, fallback to ~1
    diff = run_shell(
        "git diff --name-only HEAD~2 HEAD 2>/dev/null"
        " || git diff --name-only HEAD~1 HEAD 2>/dev/null"
        " || echo ''",
        timeout=10,
    )
    if diff["exit_code"] != 0:
        return
    changed_files = [f.strip() for f in diff["output"].split("\n") if f.strip()]
    json_files = [f for f in changed_files if f.endswith(".json")]
    if not json_files:
        return

    for jf in json_files:
        filepath = REPO_ROOT / jf
        if not filepath.exists():
            continue

        # Check 1: parseable JSON
        try:
            data = json.loads(filepath.read_text())
        except json.JSONDecodeError as e:
            log(f"VALIDATION FAILED: {jf} is not valid JSON: {e}")
            run_shell(f"git checkout -- {jf}", timeout=10)
            log(f"VALIDATION ACTION: reverted {jf}")
            continue

        # Check 2: schema validation for .model-config.json
        if Path(jf).name == ".model-config.json":
            errors = validate_model_config_schema(data)
            if errors:
                log(f"VALIDATION FAILED: {jf} schema errors: {'; '.join(errors)}")
                run_shell(f"git checkout -- {jf}", timeout=10)
                log(f"VALIDATION ACTION: reverted {jf}")
            else:
                log(f"VALIDATION OK: {jf} passes schema check")
        else:
            log(f"VALIDATION OK: {jf} is valid JSON")


def adaptive_sleep(result: dict) -> int:
    """Determine sleep duration based on result. Returns seconds."""
    exit_status = result.get("exit_status", "")
    if exit_status == "submitted":
        return SLEEP_BASE  # Success — check back soon
    elif "error" in exit_status.lower() if exit_status else False:
        return SLEEP_ON_FAILURE  # Failure — back off
    else:
        return SLEEP_BASE * 2


# ── Layer 5: Post-modification health check + auto-rollback ────────────


def health_check_and_rollback() -> bool:
    """Run health checks after commit. Rolls back all changes on failure. Returns True if passed."""
    checks_passed = True

    # Health check 1: .model-config.json is valid JSON
    model_config = REPO_ROOT / ".model-config.json"
    if model_config.exists():
        check1 = run_shell(
            "python3 -c \"import json; json.load(open('.model-config.json'))\"",
            timeout=HEALTH_TIMEOUT,
        )
        if check1["exit_code"] != 0:
            log(
                "HEALTH CHECK 1 FAILED: .model-config.json is not valid JSON:"
                f" {check1['output'][:200]}"
            )
            checks_passed = False

    # Health check 2: bash scripts/eval --json — verify exit code 0 and JSON output
    eval_script = REPO_ROOT / "scripts" / "eval"
    if eval_script.exists():
        check2 = run_shell("bash scripts/eval --json", timeout=HEALTH_TIMEOUT)
        if check2["exit_code"] != 0:
            log(
                "HEALTH CHECK 2 FAILED: scripts/eval --json returned non-zero:"
                f" {check2['output'][:200]}"
            )
            checks_passed = False
        else:
            try:
                json.loads(check2["output"])
            except json.JSONDecodeError:
                log(
                    "HEALTH CHECK 2 FAILED: scripts/eval --json output is not valid JSON"
                )
                checks_passed = False

    # Health check 3: bash tests/smoke.sh --quick — verify it doesn't crash
    smoke_test = REPO_ROOT / "tests" / "smoke.sh"
    if smoke_test.exists():
        check3 = run_shell("bash tests/smoke.sh --quick", timeout=HEALTH_TIMEOUT)
        if check3["exit_code"] != 0:
            log(
                "HEALTH CHECK 3 FAILED: tests/smoke.sh --quick crashed:"
                f" {check3['output'][:200]}"
            )
            checks_passed = False

    if checks_passed:
        log("All health checks passed")
        return True

    # ── Rollback ──────────────────────────────────────────────────────
    log(
        "ROLLBACK: all health checks did not pass — reverting changes from this iteration"
    )
    run_shell("git checkout -- .", timeout=15)
    run_shell("git reset HEAD~2", timeout=10)
    log("ROLLBACK: complete — working tree reverted, commits undone")
    return False


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

        # ── Layer 3: Filesystem quota + logrotate ──────────────────
        if not check_disk_quota():
            time.sleep(SLEEP_BASE)
            continue
        rotate_logs()

        # 1. Build task context
        try:
            task = read_goal_and_state()
        except Exception as e:
            log(f"Failed to read state: {e}")
            time.sleep(SLEEP_ON_FAILURE)
            continue

        # ── Token drift mitigation: SCAN marker + sliding-window ────
        task = inject_scan_marker(task)
        task = summarize_if_long(task)

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

        # ── Layer 4: Schema validation for config changes ──────────
        validate_json_changes()

        # ── Layer 5: Post-modification health check + auto-rollback ──
        if committed:
            if not health_check_and_rollback():
                consecutive_failures += 1
                log(f"Health check failure #{consecutive_failures}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log(
                        f"{MAX_CONSECUTIVE_FAILURES} consecutive health failures"
                        " — escalating sleep"
                    )
                    time.sleep(SLEEP_ON_FAILURE * 3)
                    consecutive_failures = 0
                else:
                    time.sleep(SLEEP_ON_FAILURE)
                continue

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
