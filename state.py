"""state.py — read project state and build task context."""

import json
import subprocess
from pathlib import Path


def read_goal_and_state(repo_root: Path) -> str:
    """Build task prompt from GOAL.md, git status, git log, eval score."""
    parts = []

    # Read GOAL.md
    goal_path = repo_root / "GOAL.md"
    goal = ""
    if goal_path.exists():
        goal = goal_path.read_text().strip()
    parts.append(f"GOAL: {goal}")
    parts.append("")

    # Git status
    try:
        r = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=repo_root,
        )
        status_out = r.stdout.strip() or "(clean)"
        parts.append(f"Git status:\n{status_out}")
        parts.append("")
    except Exception as e:
        parts.append(f"Git status: (error: {e})")
        parts.append("")

    # Git log
    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "-10"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=repo_root,
        )
        log_out = r.stdout.strip() or "(no commits)"
        parts.append(f"Recent commits:\n{log_out}")
        parts.append("")
    except Exception as e:
        parts.append(f"Git log: (error: {e})")
        parts.append("")

    # Run eval
    eval_script = repo_root / "scripts" / "eval"
    if eval_script.exists():
        try:
            r = subprocess.run(
                ["bash", "scripts/eval", "--json"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=repo_root,
            )
            if r.returncode == 0 and r.stdout.strip():
                try:
                    eval_data = json.loads(r.stdout)
                    score = eval_data.get("score", "?")
                    passed = eval_data.get("passed", "?")
                    total = eval_data.get("total", "?")
                    failed_items = [
                        c["check"]
                        for c in eval_data.get("checks", [])
                        if c.get("status") == "FAIL"
                    ]
                    parts.append(f"Eval score: {score}/100 ({passed}/{total} passed)")
                    if failed_items:
                        parts.append(f"Failing checks:")
                        for item in failed_items:
                            parts.append(f"  - {item}")
                    parts.append("")
                except (json.JSONDecodeError, KeyError):
                    parts.append("Eval score: (parse error)")
                    parts.append("")
            else:
                parts.append("Eval score: (script error)")
                parts.append("")
        except Exception as e:
            parts.append(f"Eval score: (error: {e})")
            parts.append("")

    return "\n".join(parts)
