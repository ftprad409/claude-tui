"""Git helpers for statusline."""

import subprocess

from .constants import GREEN, RED, RESET
from .debug import debug_log


def get_git_branch():
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        branch = result.stdout.strip()
        if result.returncode == 0 and branch:
            return branch
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        debug_log("get_git_branch failed")
        return ""


def get_git_diff_stat():
    try:
        result = subprocess.run(
            ["git", "diff", "--shortstat"], capture_output=True, text=True, timeout=3
        )
        stat = result.stdout.strip()
        if not stat:
            return ""
        insertions = 0
        deletions = 0
        for part in stat.split(","):
            part = part.strip()
            if "insertion" in part:
                insertions = int(part.split()[0])
            elif "deletion" in part:
                deletions = int(part.split()[0])
        parts = []
        if insertions:
            parts.append(f"{GREEN}+{insertions}{RESET}")
        if deletions:
            parts.append(f"{RED}-{deletions}{RESET}")
        return " ".join(parts) if parts else ""
    except Exception:
        debug_log("get_git_diff_stat failed")
        return ""
