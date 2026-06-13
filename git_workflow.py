"""git_workflow.py — commit, push, rollback, gc."""

import subprocess
from pathlib import Path


def git_commit(repo_root: Path, message: str) -> bool:
    """git add -A && git commit. Returns True if committed."""
    try:
        subprocess.run(
            ["git", "add", "-A"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=repo_root,
        )
        r = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=repo_root,
        )
        if r.returncode == 0:
            return True
        if "nothing to commit" in r.stdout + r.stderr:
            return False
        return False
    except Exception:
        return False


def git_push(repo_root: Path) -> bool:
    """git push origin main. Returns True if pushed."""
    try:
        r = subprocess.run(
            ["git", "push", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
        )
        return r.returncode == 0
    except Exception:
        return False


def git_rollback(repo_root: Path):
    """git checkout -- . && git reset HEAD~2. Undo last iteration."""
    try:
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
    except Exception:
        pass


def git_gc(repo_root: Path):
    """git gc --auto. Run every 50 cycles."""
    try:
        subprocess.run(
            ["git", "gc", "--auto"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=repo_root,
        )
    except Exception:
        pass
