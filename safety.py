"""safety.py — 5 safety layers for autonomous daemon operation.

Extracted from agent-seed daemon.py. Enforced at the Python level,
NOT in the AI prompt — never trust the model.
"""

import gzip
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


# ── Layer 1+2: Blocked commands and paths (from agent-seed daemon.py lines 37-65) ──

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
    "agent_session.py",
    "safety.py",
    "state.py",
    "git_workflow.py",
    "AGENTS.md",
    "GOAL.md",
    ".githooks/",
    ".git/",
]


class Safety:
    """Static safety checks — instantiation not required."""

    # ── Layer 1: Command validation ──

    @staticmethod
    def check_command(command: str) -> bool:
        """Return True if command is safe. Check blocked patterns + paths."""
        for pattern in BLOCKED_COMMANDS:
            if re.search(pattern, command):
                return False
        for path in BLOCKED_PATHS:
            if path in command:
                return False
        return True

    # ── Layer 3: Filesystem quota + logrotate ──

    @staticmethod
    def check_disk_quota(repo_root: Path, warn_mb: int = 1024) -> bool:
        """Layer 3: Check free disk space. Returns True if above warning threshold."""
        try:
            usage = shutil.disk_usage(repo_root)
            free_mb = usage.free / (1024 * 1024)
            return free_mb >= warn_mb
        except Exception:
            return True  # If we can't check, proceed (don't block on missing stat)

    @staticmethod
    def rotate_logs(output_dir: Path, keep: int = 5):
        """Layer 3: Compress old logs beyond keep count."""
        if not output_dir.exists():
            return
        logs = sorted(
            output_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if len(logs) <= keep:
            return
        for old in logs[keep:]:
            compressed = old.with_suffix(old.suffix + ".gz")
            try:
                with open(old, "rb") as f_in:
                    with gzip.open(compressed, "wb") as f_out:
                        f_out.writelines(f_in)
                old.unlink()
            except OSError:
                pass

    # ── Layer 4: JSON schema validation (from agent-seed daemon.py lines 312-394) ──

    @staticmethod
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
                    errors.append(
                        f"route '{route_name}' missing required field: 'model'"
                    )
                elif not isinstance(route_config["model"], str):
                    errors.append(f"route '{route_name}':'model' must be a string")
                if "access" not in route_config:
                    errors.append(
                        f"route '{route_name}' missing required field: 'access'"
                    )
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

    @staticmethod
    def validate_json_changes(repo_root: Path):
        """Layer 4: Validate JSON files modified in last iteration. Revert invalid ones."""
        diff = subprocess.run(
            "git diff --name-only HEAD~2 HEAD 2>/dev/null"
            " || git diff --name-only HEAD~1 HEAD 2>/dev/null"
            " || echo ''",
            capture_output=True,
            text=True,
            timeout=10,
            shell=True,
            cwd=repo_root,
        )
        if diff.returncode != 0:
            return
        changed_files = [f.strip() for f in diff.stdout.split("\n") if f.strip()]
        json_files = [f for f in changed_files if f.endswith(".json")]
        if not json_files:
            return

        for jf in json_files:
            filepath = repo_root / jf
            if not filepath.exists():
                continue

            # Check 1: parseable JSON
            try:
                data = json.loads(filepath.read_text())
            except json.JSONDecodeError as e:
                subprocess.run(
                    ["bash", "-c", f"git checkout -- {jf}"],
                    timeout=10,
                    cwd=repo_root,
                )
                continue

            # Check 2: schema validation for .model-config.json
            if Path(jf).name == ".model-config.json":
                errors = Safety.validate_model_config_schema(data)
                if errors:
                    subprocess.run(
                        ["bash", "-c", f"git checkout -- {jf}"],
                        timeout=10,
                        cwd=repo_root,
                    )

    # ── Layer 5: Health check + rollback (from agent-seed daemon.py lines 411-470) ──

    @staticmethod
    def health_check_and_rollback(repo_root: Path, timeout: int = 60) -> bool:
        """Layer 5: Post-commit health check. Rollback on failure. Returns True if passed."""
        checks_passed = True

        # Health check 1: .model-config.json is valid JSON
        model_config = repo_root / ".model-config.json"
        if model_config.exists():
            try:
                json.loads(model_config.read_text())
            except (json.JSONDecodeError, OSError):
                checks_passed = False

        # Health check 2: bash scripts/eval --json — verify exit code 0 and JSON output
        eval_script = repo_root / "scripts" / "eval"
        if eval_script.exists() and checks_passed:
            try:
                r = subprocess.run(
                    ["bash", "scripts/eval", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=repo_root,
                )
                if r.returncode != 0:
                    checks_passed = False
                else:
                    try:
                        json.loads(r.stdout)
                    except json.JSONDecodeError:
                        checks_passed = False
            except Exception:
                checks_passed = False

        # Health check 3: bash tests/smoke.sh --quick — verify it doesn't crash
        smoke_test = repo_root / "tests" / "smoke.sh"
        if smoke_test.exists() and checks_passed:
            try:
                r = subprocess.run(
                    ["bash", "tests/smoke.sh", "--quick"],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=repo_root,
                )
                if r.returncode != 0:
                    checks_passed = False
            except Exception:
                checks_passed = False

        if checks_passed:
            return True

        # Rollback
        subprocess.run(
            ["bash", "-c", "git checkout -- ."],
            timeout=15,
            cwd=repo_root,
        )
        subprocess.run(
            ["bash", "-c", "git reset HEAD~2"],
            timeout=10,
            cwd=repo_root,
        )
        return False
