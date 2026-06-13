#!/usr/bin/env python3
"""agent-seed daemon — merged: safety from agent-seed, speed from agent-seed-fast.

Model-agnostic via .model-config.json routing.
Direct API calls (not mini-swe-agent) for speed.
All 5 safety layers from the original agent-seed.
EXPLORE/CREATE cycle from agent-seed-fast.
"""

# ── Imports ──
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from agent_session import AgentSession
from safety import Safety
from state import read_goal_and_state
import git_workflow as gw

# ── Constants ──
ROOT = Path(__file__).resolve().parent

# Cycle timing (from agent-seed-fast — tight loop)
SLEEP_BASE = int(os.environ.get("AGENT_SEED_SLEEP", "60"))
SLEEP_FAIL = int(os.environ.get("AGENT_SEED_SLEEP_FAIL", "300"))
MAX_FAILURES = int(os.environ.get("AGENT_SEED_MAX_FAILURES", "3"))
EXPLORE_INTERVAL = int(os.environ.get("AGENT_SEED_EXPLORE_INTERVAL", "4"))
AUTO_PUSH = os.environ.get("AGENT_SEED_PUSH", "true").lower() == "true"


# ── Logging ──
def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


# ── Cycle type management (from agent-seed-fast) ──
def is_explore_cycle(cycle_count: int) -> bool:
    return cycle_count > 0 and cycle_count % EXPLORE_INTERVAL == 0


def build_explore_prompt(state: str) -> str:
    return f"""{state}

## EXPLORE CYCLE
Run read-only commands to understand the codebase. List files, read source code, check git history, look for patterns, inspect scripts. Discovery is valuable on its own.

Action:"""


def build_create_prompt(state: str) -> str:
    return f"""{state}

## CREATE CYCLE
Create something NEW or improve an existing file. Check what exists first. DO NOT recreate existing files.

Action:"""


# ── SCAN marker + summarization (from agent-seed) ──
SCAN_MARKER = """[SELF-CHECK]
Pause. Before proceeding:
1. What cycle am I on?
2. Am I repeating a previous action? Check git log for recent duplicates.
3. Is the goal still: {goal}
4. What is the single next step that most advances this goal?
If you detect repetition, drift, or context degradation, stop and identify the next distinct step."""


def inject_scan_marker(task: str, goal: str) -> str:
    return SCAN_MARKER.format(goal=goal) + "\n\n" + task


# ── Action extraction (from agent-seed-fast) ──
def extract_action(txt: str) -> tuple[str, str]:
    """Parse 'Action: <command>' from model output. Returns (command, message)."""
    if "Action:" not in txt:
        return "", ""
    raw = txt.split("Action:", 1)[1].lstrip("\n ")
    lines = raw.split("\n")
    first = lines[0].strip()
    # Handle heredocs
    if "<<" in first and "EOF" in first:
        result = [first]
        for line in lines[1:]:
            result.append(line)
            if line.strip() in ("EOF", "'EOF'"):
                break
        cmd = "\n".join(result)
    else:
        cmd = first
    # Clean up
    if cmd.startswith("$"):
        cmd = cmd[1:].lstrip()
    cmd = cmd.strip("` ")
    msg = first.split("<<")[0].strip()[:60]
    return cmd, msg


def ensure_heredoc_closed(cmd: str) -> str:
    """Auto-close heredocs if model ran out of tokens before writing EOF (from agent-seed-fast)."""
    if "<<" in cmd and "EOF" in cmd:
        lines = cmd.split("\n")
        if not any(line.strip() in ("EOF", "'EOF'") for line in lines):
            return cmd + "\nEOF"
    return cmd


# ── System prompt ──
SYSTEM_PROMPT = """You are an autonomous agent building agent harness infrastructure.

## FORMAT
Action: <bash command>

Start with Action: then the command.

## SAFETY RULES
- NEVER run: rm -rf, git reset --hard, git clean -fd, chmod -R, kill, pkill
- NEVER modify: daemon.py, agent_session.py, safety.py, state.py, git_workflow.py, AGENTS.md, GOAL.md

## USEFUL COMMANDS
Action: cat > newfile << 'EOF'
content
EOF

Action: mkdir -p newdir

Action: echo "content" >> existingfile

## EXPLORE COMMANDS
Action: ls -laR scripts/
Action: cat filename
Action: git log --oneline -20
Action: find . -name "*.py"
Action: python3 -c "..." """


# ── Main loop ──
def main() -> None:
    log("agent-seed daemon starting (merged)")
    log(
        f"sleep={SLEEP_BASE}s fail_sleep={SLEEP_FAIL}s explore_interval={EXPLORE_INTERVAL}"
    )

    cycle_count = 0
    consecutive_failures = 0
    last_actions: list[str] = []

    while True:
        try:
            # Layer 3: Disk check
            if not Safety.check_disk_quota(ROOT):
                log("Disk quota low — skipping cycle")
                time.sleep(SLEEP_BASE)
                continue
            Safety.rotate_logs(ROOT / ".daemon-output")

            # Build state + task
            state = read_goal_and_state(ROOT)

            goal = ""
            goal_path = ROOT / "GOAL.md"
            if goal_path.exists():
                goal = goal_path.read_text().strip()

            # Determine cycle type
            explore = is_explore_cycle(cycle_count)

            # Build prompt
            if explore:
                raw_task = build_explore_prompt(state)
            else:
                raw_task = build_create_prompt(state)

            task = inject_scan_marker(raw_task, goal)

            # Route: explore cycles use cheapest model, create cycles use strongest
            route = "explore" if explore else "create"
            log(f"Cycle {cycle_count} [{route.upper()}]...")

            # Call model
            session = AgentSession(route_name=route)
            txt = session.run(SYSTEM_PROMPT, task)

            # Parse action
            cmd, msg = extract_action(txt)
            if not cmd:
                log("No Action: found in response")
                last_actions.append("(no Action)")
                time.sleep(SLEEP_BASE)
                cycle_count += 1
                continue

            cmd = ensure_heredoc_closed(cmd)
            log(f"> {cmd[:200]}")

            # Safety check (Layer 1+2)
            if not Safety.check_command(cmd):
                log("Command blocked by safety")
                last_actions.append(f"BLOCKED: {cmd[:60]}")
                time.sleep(SLEEP_BASE)
                cycle_count += 1
                continue

            # Execute
            try:
                result = subprocess.run(
                    ["bash", "-c", cmd],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=ROOT,
                )
                output = (result.stdout + result.stderr)[:2000]
                exit_code = result.returncode
                log(f"exit={exit_code} ({len(output)}c output)")
                if output:
                    log(output[:200])
            except subprocess.TimeoutExpired:
                log("Command timed out")
                time.sleep(SLEEP_FAIL)
                cycle_count += 1
                continue

            # Track last action
            last_actions.append(cmd[:80])
            if len(last_actions) > 3:
                last_actions = last_actions[-3:]

            # Layer 4: Validate JSON changes
            Safety.validate_json_changes(ROOT)

            # Commit
            if exit_code == 0 and msg:
                committed = gw.git_commit(ROOT, msg)
                if committed:
                    log(f"Committed: {msg[:80]}")
                    # Update changelog
                    changelog_path = ROOT / "CHANGELOG.md"
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    entry = f"\n## {today} :: daemon iteration\n\n{msg[:500]}\n"
                    with open(changelog_path, "a") as f:
                        f.write(entry)

                    # Re-commit changelog
                    gw.git_commit(ROOT, "daemon: update changelog after iteration")

                    # Layer 5: Health check
                    if not Safety.health_check_and_rollback(ROOT):
                        consecutive_failures += 1
                        log(f"Health check failure #{consecutive_failures}")
                        if consecutive_failures >= MAX_FAILURES:
                            log(f"{MAX_FAILURES} failures — escalating sleep")
                            time.sleep(SLEEP_FAIL * 3)
                            consecutive_failures = 0
                        else:
                            time.sleep(SLEEP_FAIL)
                        cycle_count += 1
                        continue

                    # Push
                    if AUTO_PUSH:
                        if gw.git_push(ROOT):
                            log("Pushed to origin main")
                        else:
                            log("Push failed (non-fatal)")

                    consecutive_failures = max(0, consecutive_failures - 1)
            elif exit_code != 0:
                log(f"Command failed (exit={exit_code}) — skipping commit")

            # Git gc every 50 cycles (from agent-seed-fast)
            if cycle_count > 0 and cycle_count % 50 == 0:
                gw.git_gc(ROOT)

            cycle_count += 1
            time.sleep(SLEEP_BASE)

        except KeyboardInterrupt:
            log("Shutdown signal received. Exiting.")
            sys.exit(0)
        except Exception as e:
            log(f"ERROR: {e}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_FAILURES:
                log(f"{MAX_FAILURES} failures — escalating sleep")
                time.sleep(SLEEP_FAIL * 3)
                consecutive_failures = 0
            else:
                time.sleep(SLEEP_FAIL)
            cycle_count += 1


if __name__ == "__main__":
    main()
