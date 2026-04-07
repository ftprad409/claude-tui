#!/usr/bin/env python3
"""
Claude Code Status Line — Context Window Monitor

Displays real-time context window usage, model info, git branch,
session cost, duration, compact count, working file count,
git diff stats, and tool error rate.

Usage:
    Configure in .claude/settings.local.json:
    {
      "statusLine": {
        "type": "command",
        "command": "python3 /path/to/statusline.py"
      }
    }

Reads JSON from stdin (provided by Claude Code) containing session data.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# Constants
_CLAUDE_DIR = ".claude"
APPLICATION_JSON = "application/json"

# Context window sizes by model family
MODEL_CONTEXT_WINDOW = {
    "claude-opus-4": 1_000_000,  # 1M context via anthropic-beta flag
}
DEFAULT_CONTEXT_LIMIT = 200_000
# Compaction triggers when remaining capacity drops below this buffer
COMPACT_BUFFER = 33_000


def get_context_limit(model_id):
    """Get context window size for a model ID."""
    for key, limit in MODEL_CONTEXT_WINDOW.items():
        if key in model_id:
            return limit
    return DEFAULT_CONTEXT_LIMIT


# Pricing per million tokens (as of 2025)
# https://docs.anthropic.com/en/docs/about-claude/models
MODEL_PRICING = {
    # Claude 4.6 / 4.5  (cache_write = 1.25x input per Anthropic pricing)
    "claude-opus-4-6": {
        "input": 15.0,
        "cache_read": 1.5,
        "cache_write": 18.75,
        "output": 75.0,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
        "output": 15.0,
    },
    "claude-haiku-4-5": {
        "input": 0.80,
        "cache_read": 0.08,
        "cache_write": 1.0,
        "output": 4.0,
    },
    # Claude 3.5
    "claude-sonnet-3-5": {
        "input": 3.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
        "output": 15.0,
    },
    "claude-haiku-3-5": {
        "input": 0.80,
        "cache_read": 0.08,
        "cache_write": 1.0,
        "output": 4.0,
    },
}

# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
ORANGE = "\033[38;5;208m"
RED = "\033[31m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
GRAY = "\033[90m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_len(s):
    """Return display width of a string, ignoring ANSI escape codes."""
    return len(_ANSI_RE.sub("", s))


def _truncate(s, max_cols):
    """Truncate string to max_cols visible characters, preserving ANSI codes."""
    visible = 0
    i = 0
    while i < len(s):
        m = _ANSI_RE.match(s, i)
        if m:
            i = m.end()
            continue
        visible += 1
        if visible > max_cols:
            return s[:i] + RESET
        i += 1
    return s


def _get_terminal_cols():
    """Get real terminal width, even when running as a piped subprocess.

    Walks up the process tree to find an ancestor with a TTY, then queries
    that TTY device for the actual terminal dimensions.
    """
    import fcntl, struct, termios

    try:
        pid = os.getpid()
        for _ in range(10):
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "ppid=,tty="],
                capture_output=True,
                text=True,
                timeout=1,
            )
            parts = result.stdout.split()
            if len(parts) < 2:
                break
            ppid, tty = parts[0], parts[1]
            if tty not in ("??", ""):
                fd = os.open(f"/dev/{tty}", os.O_RDONLY)
                try:
                    res = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
                    return struct.unpack("HHHH", res)[1]
                finally:
                    os.close(fd)
            pid = int(ppid)
            if pid <= 1:
                break
    except Exception:
        pass
    return shutil.get_terminal_size().columns


# Widget system — left-side 3-row animation area
# Select via custom.widget in claudeui.json, or STATUSLINE_WIDGET env var (default: matrix)
# Built-in: matrix, bars, progress, none
# Custom: drop a .py file with a render(frame, ratio) function into widgets/

# Settings from ~/.claude/claudeui.json
_SETTINGS_CACHE = None
_SETTINGS_MTIME = 0


def load_settings():
    """Load shared settings from ~/.claude/claudeui.json.

    Re-reads the file if it has been modified since last load.
    """
    global _SETTINGS_CACHE, _SETTINGS_MTIME
    path = os.path.join(os.path.expanduser("~"), _CLAUDE_DIR, "claudeui.json")
    try:
        mtime = os.path.getmtime(path)
        if _SETTINGS_CACHE is not None and mtime == _SETTINGS_MTIME:
            return _SETTINGS_CACHE
        with open(path, "r") as f:
            _SETTINGS_CACHE = json.load(f)
        _SETTINGS_MTIME = mtime
    except (FileNotFoundError, OSError):
        _SETTINGS_CACHE = {}
    return _SETTINGS_CACHE


def get_setting(*keys, default=None):
    """Get a nested setting value. e.g. get_setting('sparkline', 'mode')."""
    cfg = load_settings()
    for key in keys:
        if isinstance(cfg, dict):
            cfg = cfg.get(key)
        else:
            return default
    return cfg if cfg is not None else default


def is_visible(line, component):
    """Check if a statusline component is enabled in custom config."""
    return get_setting("custom", line, component, default=True)


def _load_widget(name):
    """Load a widget by name from the widgets/ directory."""
    if name == "none":
        return None
    widgets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "widgets")
    widget_path = os.path.join(widgets_dir, f"{name}.py")
    if not os.path.exists(widget_path):
        return None
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"widgets.{name}", widget_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "render", None)


def get_git_branch():
    """Read current git branch from .git/HEAD."""
    try:
        git_head = os.path.join(os.getcwd(), ".git", "HEAD")
        if not os.path.exists(git_head):
            return ""
        with open(git_head, "r") as f:
            ref = f.read().strip()
        if ref.startswith("ref: refs/heads/"):
            return ref[len("ref: refs/heads/") :]
        return ref[:8]  # detached HEAD — show short hash
    except Exception:
        return ""


def get_git_diff_stat():
    """Get git working tree diff stats (+added/-deleted lines)."""
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
        return ""


def get_model_pricing(model_id):
    """Get pricing for a model, falling back to sonnet rates."""
    for key, pricing in MODEL_PRICING.items():
        if key in model_id:
            return pricing
    return MODEL_PRICING["claude-sonnet-4-6"]


# Claude status page (Atlassian Statuspage v2 API)
_STATUS_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), _CLAUDE_DIR, "api-status-cache.json"
)


def _fetch_api_status():
    """Get Claude API status, using a file-based cache with TTL.

    Returns dict with keys: status, components, incidents — or None.
    Cache is shared across statusline and monitor invocations.
    """
    if not get_setting("status", "enabled", default=True):
        return None

    ttl = max(30, get_setting("status", "ttl", default=120))
    cache = None

    # Read cache
    try:
        with open(_STATUS_CACHE_PATH, "r") as f:
            cache = json.load(f)
        if time.time() - cache.get("fetched_at", 0) < ttl:
            return cache
    except (FileNotFoundError, OSError):
        pass

    # Fetch fresh
    try:
        import http.client
        import ssl

        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection("status.claude.com", timeout=2, context=ctx)
        try:
            conn.request(
                "GET", "/api/v2/summary.json", headers={"Accept": APPLICATION_JSON}
            )
            resp = conn.getresponse()
            if resp.status == 200:
                data = json.loads(resp.read())
                cache = {
                    "fetched_at": time.time(),
                    "status": data.get("status", {}).get("indicator", "none"),
                    "components": {
                        c["name"]: c["status"] for c in data.get("components", [])
                    },
                    "incidents": [
                        {
                            "name": i["name"],
                            "status": i["status"],
                            "impact": i["impact"],
                        }
                        for i in data.get("incidents", [])
                    ],
                }
                os.makedirs(os.path.dirname(_STATUS_CACHE_PATH), exist_ok=True)
                tmp = _STATUS_CACHE_PATH + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(cache, f)
                os.replace(tmp, _STATUS_CACHE_PATH)
                return cache
        finally:
            conn.close()
    except Exception:
        pass

    return cache  # stale cache better than nothing


def _format_api_status(status_data):
    """Format API status for statusline display.

    Returns colored string or empty string if operational.
    """
    if not status_data:
        return ""

    show_when_ok = get_setting("status", "show_when_operational", default=False)
    components = status_data.get("components", {})
    overall = status_data.get("status", "none")

    # Find worst component status
    severity_order = [
        "operational",
        "degraded_performance",
        "partial_outage",
        "major_outage",
    ]
    worst = "operational"
    worst_name = ""
    for name, st in components.items():
        if st in severity_order:
            if severity_order.index(st) > severity_order.index(worst):
                worst = st
                worst_name = name

    if worst == "operational" and overall == "none":
        if show_when_ok:
            return f"{GREEN}\u25cf{RESET} {GRAY}ok{RESET}"
        return ""

    if worst == "degraded_performance":
        return f"{YELLOW}\u25b2 degraded{RESET}"
    elif worst == "partial_outage":
        label = "Code partial" if "Code" in worst_name else "partial outage"
        return f"{ORANGE}\u25b2 {label}{RESET}"
    elif worst == "major_outage":
        label = "Code outage" if "Code" in worst_name else "outage"
        return f"{RED}\u25b2 {label}{RESET}"

    # Overall indicator fallback
    if overall == "minor":
        return f"{YELLOW}\u25b2 degraded{RESET}"
    elif overall == "major":
        return f"{ORANGE}\u25b2 outage{RESET}"
    elif overall == "critical":
        return f"{RED}\u25b2 outage{RESET}"

    return ""


# OAuth usage API cache
_USAGE_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), _CLAUDE_DIR, "usage-cache.json"
)
_USAGE_MIN_INTERVAL = 60  # rate limit: minimum 60 seconds between requests


def _load_oauth_token():
    """Load OAuth token from Keychain, credentials file, or environment."""
    # Try environment variable first
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        return token

    # Try credentials file
    creds_path = os.path.join(os.path.expanduser("~"), _CLAUDE_DIR, ".credentials.json")
    try:
        with open(creds_path, "r") as f:
            creds = json.load(f)
        # Try claudeAiOauth key first, then root level
        token = creds.get("claudeAiOauth", {}).get("accessToken") or creds.get(
            "accessToken"
        )
        if token:
            return token
    except (FileNotFoundError, OSError):
        pass

    # Try Keychain (macOS)
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            blob = json.loads(result.stdout.strip())
            token = blob.get("claudeAiOauth", {}).get("accessToken") or blob.get(
                "accessToken"
            )
            if token:
                return token
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
        OSError,
    ):
        pass

    return None


def _fetch_usage():
    """Get Claude plan usage via OAuth API, using file-based cache with rate limiting.

    Returns dict with keys: five_hour, seven_day, seven_day_sonnet, extra_usage — or None.
    Cache is shared across statusline and monitor invocations.
    """
    if not get_setting("usage", "enabled", default=True):
        return None

    # Rate limiting: minimum interval between API calls
    rate_limit = max(60, get_setting("usage", "rate_limit", default=60))
    cache = None
    lock_file = _USAGE_CACHE_PATH + ".lock"

    # Clean up stale lock file if older than 5 minutes (crashed process)
    try:
        if os.path.exists(lock_file):
            if os.path.getmtime(lock_file) < time.time() - 300:
                os.remove(lock_file)
    except OSError:
        pass

    # Try to acquire lock for fetching
    def try_acquire_lock():
        try:
            import fcntl

            lock_fd = open(lock_file, "w")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_fd
        except (IOError, OSError):
            return None

    lock_fd = try_acquire_lock()

    # Read cache (always, even if we don't have lock)
    try:
        with open(_USAGE_CACHE_PATH, "r") as f:
            cache = json.load(f)

        # Check retry backoff - don't retry too frequently
        retry_after = cache.get("retry_after", 0)
        now = time.time()
        if now < retry_after:
            if lock_fd:
                import fcntl

                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            return cache  # still in backoff period

        cached_time = cache.get("fetched_at", 0)

        # Check if any usage has reset since cache was fetched
        five_hour_reset = cache.get("five_hour", {}).get("resets_at", "")

        should_refresh = False
        if five_hour_reset:
            try:
                from datetime import datetime, timezone

                reset_dt = datetime.fromisoformat(
                    five_hour_reset.replace("Z", "+00:00")
                )
                if reset_dt.timestamp() < now:
                    should_refresh = True  # Reset time passed, refresh immediately
            except Exception:
                pass

        # Use cache if within rate limit AND no reset has occurred AND we don't have lock
        if not should_refresh and now - cached_time < rate_limit and lock_fd is None:
            return cache
    except (FileNotFoundError, OSError):
        pass

    # If we don't have lock, another process is fetching - return cache or None
    if lock_fd is None:
        return cache

    # We have lock - do the fetch
    # Re-read cache after acquiring lock (another process may have updated)
    try:
        with open(_USAGE_CACHE_PATH, "r") as f:
            cache = json.load(f)
    except:
        pass

    # Fetch fresh
    token = _load_oauth_token()
    if not token:
        return cache  # return stale cache if no token

    try:
        import http.client
        import ssl

        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection("api.anthropic.com", timeout=3, context=ctx)
        try:
            conn.request(
                "GET",
                "/api/oauth/usage",
                headers={
                    "Accept": APPLICATION_JSON,
                    "Content-Type": APPLICATION_JSON,
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": "oauth-2025-04-20",
                },
            )
            resp = conn.getresponse()
            if resp.status == 200:
                data = json.loads(resp.read())
                cache = {
                    "fetched_at": time.time(),
                    "five_hour": data.get("five_hour", {}),
                    "seven_day": data.get("seven_day", {}),
                    "seven_day_sonnet": data.get("seven_day_sonnet", {}),
                    "extra_usage": data.get("extra_usage", {}),
                }
                os.makedirs(os.path.dirname(_USAGE_CACHE_PATH), exist_ok=True)
                tmp = _USAGE_CACHE_PATH + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(cache, f)
                os.replace(tmp, _USAGE_CACHE_PATH)
                return cache
            elif resp.status == 429:
                # Rate limited - implement exponential backoff
                # Only save backoff if we had real usage data to preserve
                has_usage_data = bool(cache and cache.get("five_hour"))

                if has_usage_data:
                    current_retry = cache.get("retry_count", 0)
                    retry_count = current_retry + 1
                    backoff_seconds = min(120 * (2**current_retry), 600)

                    cache["retry_count"] = retry_count
                    cache["retry_after"] = time.time() + backoff_seconds

                    os.makedirs(os.path.dirname(_USAGE_CACHE_PATH), exist_ok=True)
                    tmp = _USAGE_CACHE_PATH + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(cache, f)
                    os.replace(tmp, _USAGE_CACHE_PATH)

                # Return cache if it has usage data, otherwise None
                return cache if has_usage_data else None
        finally:
            conn.close()
    except Exception:
        pass

    # Release lock on exit
    if lock_fd:
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

    return cache  # stale cache better than nothing


def _format_reset_time(iso_time: str) -> str:
    """Format ISO reset time to short duration like '2h' or '30m'."""
    if not iso_time:
        return ""
    try:
        from datetime import datetime

        reset_dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        now_dt = datetime.now(reset_dt.tzinfo)
        diff = (reset_dt - now_dt).total_seconds()
        if diff <= 0:
            return ""
        hours = int(diff // 3600)
        mins = int((diff % 3600) // 60)
        if hours > 0:
            return f"{hours}h"
        else:
            return f"{mins}m"
    except Exception:
        return ""


def _format_usage_session(usage_data: dict | None, length: int = 20) -> str:
    """Format session (5-hour) usage for line 2.

    Returns string like "████████████████████ 15%  ↻ 2h  " or empty if no data.
    Fixed width for consistent display - no line jumping.
    """
    if not usage_data:
        return ""

    five_hour = usage_data.get("five_hour", {})
    if not five_hour:
        return ""

    pct = five_hour.get("utilization", 0)
    if pct is None:
        return ""

    ratio = min(pct / 100.0, 1.0)
    filled = int(length * ratio)
    color = _color_for_ratio(ratio)
    bar = (
        f"{color}"
        + "\u2588" * filled
        + f"{GRAY}"
        + "\u2591" * (length - filled)
        + f"{RESET}"
    )
    pct_int = int(pct)
    reset = _format_reset_time(five_hour.get("resets_at", ""))
    reset_str = f" \u21bb {reset}" if reset else ""

    # Pad to fixed width for consistent display (bar=20 + pct=4 + reset=6 = 30)
    pct_str = f"{color}{pct_int:>3}%{RESET}"
    reset_str = reset_str.ljust(6)  # max " ↻ 99h" = 6 chars

    return f"{bar} {pct_str} {reset_str}"


def _format_usage_weekly(usage_data: dict | None, length: int = 20) -> str:
    """Format weekly usage for line 3.

    Returns string like "████████████████████ 73%w  ↻ 3h  " or empty if no data.
    Fixed width for consistent display - no line jumping.
    """
    if not usage_data:
        return ""

    seven_day = usage_data.get("seven_day", {})
    if not seven_day:
        return ""

    pct = seven_day.get("utilization", 0)
    if pct is None:
        return ""

    ratio = min(pct / 100.0, 1.0)
    filled = int(length * ratio)
    color = _color_for_ratio(ratio)
    bar = (
        f"{color}"
        + "\u2588" * filled
        + f"{GRAY}"
        + "\u2591" * (length - filled)
        + f"{RESET}"
    )
    pct_int = int(pct)
    reset = _format_reset_time(seven_day.get("resets_at", ""))
    reset_str = f" \u21bb {reset}" if reset else ""

    # Pad to fixed width for consistent display (bar=20 + pct=5 + reset=6 = 31)
    pct_str = f"{color}{pct_int:>3}%{RESET}{GRAY}w{RESET}"
    reset_str = reset_str.ljust(6)  # max " ↻ 99h" = 6 chars

    return f"{bar} {pct_str} {reset_str}"


def _format_usage_weekly(usage_data, length=20):
    """Format weekly usage for line 3.

    Returns string like "████████████████████ 73%w  ↻ 3h  " or empty if no data.
    Fixed width for consistent display - no line jumping.
    """
    if not usage_data:
        return ""

    def format_reset(iso_time):
        if not iso_time:
            return ""
        try:
            from datetime import datetime

            reset_dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
            now_dt = datetime.now(reset_dt.tzinfo)
            diff = (reset_dt - now_dt).total_seconds()
            if diff <= 0:
                return ""
            hours = int(diff // 3600)
            mins = int((diff % 3600) // 60)
            if hours > 0:
                return f"{hours}h"
            else:
                return f"{mins}m"
        except Exception:
            return ""

    seven_day = usage_data.get("seven_day", {})
    if not seven_day:
        return ""

    pct = seven_day.get("utilization", 0)
    if pct is None:
        return ""

    ratio = min(pct / 100.0, 1.0)
    filled = int(length * ratio)
    color = _color_for_ratio(ratio)
    bar = (
        f"{color}"
        + "\u2588" * filled
        + f"{GRAY}"
        + "\u2591" * (length - filled)
        + f"{RESET}"
    )
    pct_int = int(pct)
    reset = format_reset(seven_day.get("resets_at"))
    reset_str = f" \u21bb {reset}" if reset else ""

    # Pad to fixed width for consistent display (bar=20 + pct=5 + reset=6 = 31)
    pct_str = f"{color}{pct_int:>3}%{RESET}{GRAY}w{RESET}"
    reset_str = reset_str.ljust(6)  # max " ↻ 99h" = 6 chars

    return f"{bar} {pct_str} {reset_str}"


def _color_for_ratio(ratio):
    """Get color for ratio (same as context bar)."""
    if ratio >= 0.95:
        return RED
    elif ratio >= 0.85:
        return ORANGE
    elif ratio >= 0.60:
        return YELLOW
    else:
        return GREEN


def parse_transcript(transcript_path, context_limit=None):
    """Parse transcript file to extract all session metrics."""
    if context_limit is None:
        context_limit = DEFAULT_CONTEXT_LIMIT
    result = {
        "context_tokens": 0,
        "input_tokens_total": 0,
        "cache_read_tokens_total": 0,
        "cache_creation_tokens_total": 0,
        "output_tokens_total": 0,
        "compact_count": 0,
        "files_touched": set(),
        "session_start": None,
        "tool_calls": 0,
        "tool_errors": 0,
        "subagent_count": 0,
        "turn_count": 0,
        "thinking_count": 0,
        "context_history": [],
        "context_per_turn": [],  # cumulative context at each turn (since last compact)
        "tokens_wasted": 0,  # tokens lost to compaction overhead
        "total_context_built": 0,  # sum of peak context per segment (for efficiency calc)
        "recent_tools": [],
        "current_turn_file_edits": {},
        "turns_since_compact": 0,
        "context_at_last_compact": 0,
        "_pre_compact_ctx": 0,  # context just before compaction (internal)
    }

    try:
        with open(transcript_path, "r") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return result

    # Reverse pass — find most recent context usage
    # Stop at summary (compaction) entries: pre-compact usage is stale
    context_found = False
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        # If we hit a compaction before finding usage, context was reset
        if obj.get("type") == "summary" or (
            obj.get("type") == "system" and obj.get("subtype") == "compact_boundary"
        ):
            break

        if (
            not context_found
            and obj.get("type") == "assistant"
            and "message" in obj
            and "usage" in obj["message"]
        ):
            usage = obj["message"]["usage"]
            keys = [
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "output_tokens",
            ]
            if all(k in usage for k in keys):
                result["context_tokens"] = sum(usage[k] for k in keys)
                context_found = True
                break

    # Forward pass — cumulative metrics
    active_subagents = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Session start
        if result["session_start"] is None and "timestamp" in obj:
            result["session_start"] = obj["timestamp"]

        # Turn count (each user message = one turn)
        # Reset current turn event counter on new user message
        if obj.get("type") == "user" and "message" in obj:
            content = obj["message"].get("content", [])
            # Only count turns with actual user text, not just tool results
            has_text = False
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        has_text = True
                        break
            elif isinstance(content, str) and content.strip():
                has_text = True
            if has_text:
                result["turn_count"] += 1
                result["turns_since_compact"] += 1
                result["recent_tools"] = []
                result["current_turn_file_edits"] = {}

        # Token usage for cost + context history
        if (
            obj.get("type") == "assistant"
            and "message" in obj
            and "usage" in obj["message"]
        ):
            usage = obj["message"]["usage"]
            result["input_tokens_total"] += usage.get("input_tokens", 0)
            result["cache_read_tokens_total"] += usage.get("cache_read_input_tokens", 0)
            result["cache_creation_tokens_total"] += usage.get(
                "cache_creation_input_tokens", 0
            )
            result["output_tokens_total"] += usage.get("output_tokens", 0)

            # Per-response context snapshot
            keys_ctx = [
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "output_tokens",
            ]
            ctx_snapshot = sum(usage.get(k, 0) for k in keys_ctx)
            result["_pre_compact_ctx"] = ctx_snapshot

            # Capture post-compaction baseline from first usage after compact
            if result["context_at_last_compact"] == -1:
                result["context_at_last_compact"] = ctx_snapshot
                # Record waste: headroom + summary (rebuild minus system prompt)
                # System prompt (cache_read) is constant overhead, not compaction waste
                if (
                    result["compact_count"] > 0
                    and result.get("_ctx_before_compact", 0) > 0
                ):
                    pre = result["_ctx_before_compact"]
                    cache_r = usage.get("cache_read_input_tokens", 0)
                    headroom = max(0, context_limit - pre)
                    summary = max(0, ctx_snapshot - cache_r)
                    result["tokens_wasted"] += headroom + summary

            # Track per-turn context growth (last snapshot per turn wins)
            turn = result["turn_count"]
            if result["context_per_turn"] and result["context_per_turn"][-1][0] == turn:
                result["context_per_turn"][-1] = (turn, ctx_snapshot)
            else:
                result["context_per_turn"].append((turn, ctx_snapshot))

            # Per-turn token spend for sparkline
            out_tok = usage.get("output_tokens", 0)
            if out_tok > 0:
                result["context_history"].append(out_tok)

        # Compact count — also record a 0 in context history (visual cliff)
        if obj.get("type") == "summary" or (
            obj.get("type") == "system" and obj.get("subtype") == "compact_boundary"
        ):
            result["compact_count"] += 1
            result["context_history"].append(None)
            result["_ctx_before_compact"] = result["_pre_compact_ctx"]
            result["total_context_built"] += (
                context_limit  # full window budget per segment
            )
            result["turns_since_compact"] = 0
            result[
                "context_at_last_compact"
            ] = -1  # sentinel: next usage will set baseline
            result["context_per_turn"] = []  # reset per-turn tracking

        # Tool calls, thinking, errors, files, and subagents
        if obj.get("type") == "assistant" and "message" in obj:
            content = obj["message"].get("content", [])
            has_thinking = False
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "thinking":
                        has_thinking = True
                    if block.get("type") == "tool_use":
                        result["tool_calls"] += 1
                        inp = block.get("input", {})
                        tool_name = block.get("name", "")

                        # Recent tool activity (for line 3)
                        file_arg = ""
                        for key in ("file_path", "path"):
                            if key in inp and isinstance(inp[key], str):
                                file_arg = os.path.basename(inp[key])
                                break
                        if file_arg:
                            result["recent_tools"].append(f"{tool_name} {file_arg}")
                        else:
                            cmd = inp.get("command", "")
                            if cmd:
                                # Show first word of command
                                short = cmd.split()[0] if cmd.split() else ""
                                result["recent_tools"].append(f"{tool_name} {short}")
                            else:
                                result["recent_tools"].append(tool_name)

                        # Files touched
                        for key in ("file_path", "path"):
                            if key in inp and isinstance(inp[key], str):
                                result["files_touched"].add(inp[key])
                                # Track edits per file this turn
                                if tool_name in ("Edit", "Write", "MultiEdit"):
                                    fname = os.path.basename(inp[key])
                                    result["current_turn_file_edits"][fname] = (
                                        result["current_turn_file_edits"].get(fname, 0)
                                        + 1
                                    )

                        # Sub-agent tracking
                        if tool_name in ("Task", "Agent"):
                            task_id = block.get("id", "")
                            if task_id:
                                active_subagents.add(task_id)
            if has_thinking:
                result["thinking_count"] += 1

        # Tool errors from user messages (tool_result blocks)
        if obj.get("type") == "user" and "message" in obj:
            content = obj["message"].get("content", [])
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("is_error")
                    ):
                        result["tool_errors"] += 1

    result["subagent_count"] = len(active_subagents)

    return result


def format_tokens(n):
    """Format token count as human-readable string (e.g., 84.2k)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_cost(cost):
    """Format cost in dollars."""
    if cost < 0.01:
        return "<$0.01"
    return f"${cost:.2f}"


def format_duration(start_timestamp):
    """Format session duration from ISO timestamp."""
    if not start_timestamp:
        return "0m"
    try:
        start_str = start_timestamp.replace("Z", "+00:00")
        start = datetime.fromisoformat(start_str)
        now = datetime.now(timezone.utc)
        delta = now - start
        total_minutes = int(delta.total_seconds() / 60)
        if total_minutes < 60:
            return f"{total_minutes}m"
        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"{hours}h {minutes:02d}m"
    except Exception:
        return "?m"


def build_sparkline(values, width=20):
    """Build a colored sparkline showing per-turn token spend.

    Scaled relative to the peak value in the data so the shape
    reveals which turns were expensive vs cheap.

    Args:
        values: list of output-token counts (None = compaction event).
        width: max number of characters to render.

    Returns:
        ANSI-colored sparkline string.
    """
    if not values:
        return ""
    # Keep only the last 3 compaction markers; replace older ones with 0
    none_indices = [i for i, v in enumerate(values) if v is None]
    keep_set = set(none_indices[-3:])
    cleaned = []
    for i, v in enumerate(values):
        if v is None and i not in keep_set:
            cleaned.append(0)
        else:
            cleaned.append(v)
    values = cleaned

    # Display mode: "tail" (last N turns) or "merge" (downsample all)
    mode = get_setting("sparkline", "mode", default="tail")
    if mode == "merge":
        merge_size = get_setting("sparkline", "merge_size", default=2)
        # Merge consecutive turns into buckets of merge_size
        merged = []
        for i in range(0, len(values), merge_size):
            bucket = values[i : i + merge_size]
            if None in bucket:
                merged.append(None)
            else:
                merged.append(sum(v for v in bucket if v is not None))
        values = merged
        if len(values) > width:
            values = values[-width:]
    else:
        # Tail: show only the most recent turns at full resolution
        if len(values) > width:
            values = values[-width:]

    blocks = "▁▂▃▄▅▆▇█"
    peak = max((v for v in values if v is not None), default=1)
    scale = peak if peak > 0 else 1
    chars = []
    for v in values:
        if v is None:
            chars.append(f"\033[38;2;243;139;168m\u2595{RESET}")
            continue
        r = v / scale
        idx = int(r * (len(blocks) - 1))
        idx = max(0, min(idx, len(blocks) - 1))
        if r < 0.25:
            color = "\033[38;2;166;227;161m"  # green
        elif r < 0.50:
            color = "\033[38;2;148;226;213m"  # teal
        elif r < 0.75:
            color = "\033[38;2;249;226;175m"  # yellow
        else:
            color = "\033[38;2;250;179;135m"  # peach
        chars.append(f"{color}{blocks[idx]}{RESET}")
    return "".join(chars)


def _rgb(r, g, b):
    """Return an ANSI true-color foreground escape."""
    return f"\033[38;2;{r};{g};{b}m"


def _lerp_rgb(stops, t):
    """Interpolate smoothly across a list of (pos, r, g, b) color stops."""
    t = max(0.0, min(1.0, t))
    for i in range(len(stops) - 1):
        if t <= stops[i + 1][0]:
            seg_t = (t - stops[i][0]) / (stops[i + 1][0] - stops[i][0])
            r = int(stops[i][1] + (stops[i + 1][1] - stops[i][1]) * seg_t)
            g = int(stops[i][2] + (stops[i + 1][2] - stops[i][2]) * seg_t)
            b = int(stops[i][3] + (stops[i + 1][3] - stops[i][3]) * seg_t)
            return _rgb(r, g, b)
    return _rgb(stops[-1][1], stops[-1][2], stops[-1][3])


def build_progress_bar(ratio, length=20, compact_ratio=None):
    """Build a smooth gradient progress bar.

    Each filled character gets a unique interpolated color across the
    gradient, creating a continuous green -> teal -> yellow -> peach -> pink
    transition. Empty slots use a dim block for contrast.
    """
    filled = int(length * min(ratio, 1.0))

    # Smooth gradient stops: (position, R, G, B)
    stops = [
        (0.00, 166, 227, 161),  # green
        (0.30, 148, 226, 213),  # teal
        (0.55, 249, 226, 175),  # yellow
        (0.80, 250, 179, 135),  # peach
        (1.00, 243, 139, 168),  # pink
    ]

    bar_chars = []
    for i in range(length):
        if i < filled:
            pos = i / max(length - 1, 1)
            color = _lerp_rgb(stops, pos)
            bar_chars.append(f"{color}\u2588{RESET}")
        else:
            bar_chars.append(f"\033[38;2;55;59;80m\u2591{RESET}")

    bar = "".join(bar_chars)

    if compact_ratio and compact_ratio > 0:
        fill_of_ceiling = ratio / compact_ratio
    else:
        fill_of_ceiling = ratio

    if fill_of_ceiling < 0.60:
        pct_color = GREEN
    elif fill_of_ceiling < 0.85:
        pct_color = YELLOW
    elif fill_of_ceiling < 0.95:
        pct_color = ORANGE
    else:
        pct_color = RED

    pct = ratio * 100
    # Fixed width: bar(20) + space(1) + pct(4) = 25 chars minimum
    pct_str = f"{pct_color}{pct:>3.0f}%{RESET}"
    return f"{bar} {pct_str}"


def main():
    compact_mode = "--compact" in sys.argv

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        print("statusline: no data")
        return

    model = data.get("model", {}).get("display_name", "unknown")
    model_id = data.get("model", {}).get("id", "")
    cwd = os.path.basename(data.get("workspace", {}).get("current_dir", ""))
    transcript_path = data.get("transcript_path", "")
    session_id = data.get("session_id", "")[:8]

    # Resolve context window size from model
    context_limit = get_context_limit(model_id)

    # Parse transcript for all metrics
    metrics = parse_transcript(transcript_path, context_limit=context_limit)

    # Context usage bar
    ctx_used = metrics["context_tokens"]
    ratio = ctx_used / context_limit if context_limit > 0 else 0
    compact_ratio = (
        (context_limit - COMPACT_BUFFER) / context_limit if context_limit > 0 else 0.83
    )
    bar = build_progress_bar(ratio, compact_ratio=compact_ratio)
    tokens_str = format_tokens(int(ctx_used))
    limit_str = format_tokens(context_limit)

    # Session cost
    pricing = get_model_pricing(model_id)
    cost = (
        metrics["input_tokens_total"] * pricing["input"] / 1_000_000
        + metrics["cache_read_tokens_total"] * pricing["cache_read"] / 1_000_000
        + metrics["cache_creation_tokens_total"]
        * pricing.get("cache_write", pricing["input"] * 1.25)
        / 1_000_000
        + metrics["output_tokens_total"] * pricing["output"] / 1_000_000
    )
    cost_str = format_cost(cost)

    # Session duration
    duration_str = format_duration(metrics["session_start"])

    # Git branch + diff
    branch = get_git_branch()
    diff_stat = get_git_diff_stat()
    branch_part = ""
    if branch:
        branch_part = f"{GREEN}\u2387 {branch}{RESET}"
        if diff_stat:
            branch_part += f" {diff_stat}"

    # Cache hit ratio
    total_input = metrics["input_tokens_total"] + metrics["cache_read_tokens_total"]
    if total_input > 0:
        cache_ratio = metrics["cache_read_tokens_total"] / total_input
        cache_pct = int(cache_ratio * 100)
        if cache_pct >= 70:
            cache_color = GREEN
        elif cache_pct >= 40:
            cache_color = YELLOW
        else:
            cache_color = ORANGE
        cache_part = f"{cache_color}{cache_pct}%{RESET} cache"
    else:
        cache_part = f"{GRAY}0%{RESET} cache"

    # Sub-agents (used as guard for line2 visibility)
    has_subagents = metrics["subagent_count"] > 0

    # Cost per turn
    cost_per_turn = ""
    if metrics["turn_count"] > 0:
        cpt = cost / metrics["turn_count"]
        cost_per_turn = f"{GRAY}~{format_cost(cpt)}/turn{RESET}"

    # Per-turn token spend sparkline (relative to peak)
    sparkline_part = build_sparkline(metrics["context_history"])

    # Compaction prediction (turns remaining until auto-compaction)
    # Uses EMA (exponential moving average) for recent-weighted growth rate
    compact_prediction = ""
    turns_since = metrics["turns_since_compact"]
    if turns_since >= 2 and ratio > 0 and ratio < 1.0:
        # Fixed buffer model: compaction fires when remaining < COMPACT_BUFFER
        # CLAUDE_AUTOCOMPACT_PCT_OVERRIDE can lower the ceiling further
        compact_ceiling = context_limit - COMPACT_BUFFER
        env_pct = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "")
        if env_pct.isdigit() and 1 <= int(env_pct) <= 100:
            compact_ceiling = min(compact_ceiling, context_limit * int(env_pct) / 100)
        remaining_tokens = compact_ceiling - ctx_used

        # Compute growth rate using EMA on per-turn context deltas
        turn_contexts = [ctx for _, ctx in metrics["context_per_turn"]]
        if len(turn_contexts) >= 3:
            deltas = [
                turn_contexts[i] - turn_contexts[i - 1]
                for i in range(1, len(turn_contexts))
                if turn_contexts[i] > turn_contexts[i - 1]
            ]
            if deltas:
                alpha = 2 / (min(len(deltas), 5) + 1)
                ema = deltas[0]
                for d in deltas[1:]:
                    ema = alpha * d + (1 - alpha) * ema
                growth_per_turn = ema
            else:
                growth_per_turn = 0
        else:
            # Fallback to simple average when not enough data
            baseline = metrics["context_at_last_compact"]
            growth_since = ctx_used - baseline if baseline > 0 else ctx_used
            growth_per_turn = growth_since / max(turns_since, 1)

        if growth_per_turn > 0 and remaining_tokens > 0:
            turns_left = int(remaining_tokens / growth_per_turn)
            if turns_left <= 5:
                pred_color = RED
            elif turns_left <= 15:
                pred_color = ORANGE
            elif turns_left <= 30:
                pred_color = YELLOW
            else:
                pred_color = GREEN
            compact_prediction = (
                f"{pred_color}~{turns_left}{RESET} {GRAY}turns left{RESET}"
            )

    # Context efficiency score
    efficiency_part = ""
    total_built = metrics["total_context_built"] + ctx_used  # all segments + current
    if is_visible("line1", "efficiency") and total_built > 0:
        wasted = metrics["tokens_wasted"]
        eff = max(0, 1 - wasted / total_built) if wasted > 0 else 1.0
        eff_pct = int(eff * 100)
        if eff_pct >= 90:
            eff_color = GREEN
        elif eff_pct >= 70:
            eff_color = YELLOW
        elif eff_pct >= 50:
            eff_color = ORANGE
        else:
            eff_color = RED
        efficiency_part = f"{eff_color}{eff_pct}%{RESET} {GRAY}eff{RESET}"

    # Claude API status (fetched once, used by both full and compact modes)
    api_status_str = ""
    if is_visible("line2", "api_status"):
        api_status = _fetch_api_status()
        api_status_str = _format_api_status(api_status)

    dim = GRAY
    sep = f" {dim}\u22ee{RESET} "

    # Line 1: session core - context_bar first, then other components
    line1_parts = []
    if is_visible("line1", "context_bar"):
        ctx_part = f"{bar}"
        if is_visible("line1", "token_count"):
            ctx_part += (
                f" {CYAN}{tokens_str}{RESET}{dim}/{RESET}{GRAY}{limit_str}{RESET}"
            )
        if compact_prediction and is_visible("line1", "compact_prediction"):
            ctx_part += f" {dim}\u22ee{RESET} {compact_prediction}"
        line1_parts.append(ctx_part)
    elif is_visible("line1", "token_count"):
        ctx_part = f"{CYAN}{tokens_str}{RESET}{dim}/{RESET}{GRAY}{limit_str}{RESET}"
        if compact_prediction and is_visible("line1", "compact_prediction"):
            ctx_part += f" {dim}\u22ee{RESET} {compact_prediction}"
        line1_parts.append(ctx_part)
    elif compact_prediction and is_visible("line1", "compact_prediction"):
        line1_parts.append(compact_prediction)
    if is_visible("line1", "model"):
        line1_parts.append(f"{BOLD}{MAGENTA}{model}{RESET}")
    if sparkline_part and is_visible("line1", "sparkline"):
        line1_parts.append(sparkline_part)
    if is_visible("line1", "cost"):
        line1_parts.append(f"{YELLOW}{cost_str}{RESET}")
    if is_visible("line1", "duration"):
        line1_parts.append(f"{WHITE}{duration_str}{RESET}")
    if is_visible("line1", "compact_count"):
        line1_parts.append(
            f"{CYAN}{metrics['compact_count']}{RESET}{dim}x{RESET} compact"
        )
    if efficiency_part:
        line1_parts.append(efficiency_part)
    if is_visible("line1", "session_id"):
        line1_parts.append(f"{dim}#{RESET}{GRAY}{session_id}{RESET}")

    # Line 2: project telemetry - usage first, then other components
    line2_parts = []

    # Usage bar (session 5-hour) - first position
    if is_visible("line2", "usage"):
        usage = _fetch_usage()
        usage_session_str = _format_usage_session(usage)
        if usage_session_str:
            line2_parts.append(usage_session_str)

    if is_visible("line2", "cwd"):
        line2_parts.append(f"{GREEN}{cwd}{RESET}")
    if branch_part and is_visible("line2", "git_branch"):
        line2_parts.append(branch_part)
    if is_visible("line2", "turns"):
        line2_parts.append(f"{CYAN}{metrics['turn_count']}{RESET} {dim}turns{RESET}")
    if is_visible("line2", "files"):
        line2_parts.append(
            f"{CYAN}{len(metrics['files_touched'])}{RESET} {dim}files{RESET}"
        )
    if is_visible("line2", "errors"):
        if metrics["tool_errors"] > 0:
            err_color = RED if metrics["tool_errors"] > 5 else ORANGE
            line2_parts.append(
                f"{err_color}{metrics['tool_errors']}{RESET} {dim}err{RESET}"
            )
        else:
            line2_parts.append(f"{GREEN}0{RESET} {dim}err{RESET}")
    if is_visible("line2", "cache"):
        line2_parts.append(f"{cache_part.split(' ')[0]} {dim}cache{RESET}")
    if metrics["thinking_count"] > 0 and is_visible("line2", "thinking"):
        line2_parts.append(
            f"{MAGENTA}{metrics['thinking_count']}{RESET}{dim}x{RESET} {dim}think{RESET}"
        )
    if cost_per_turn and is_visible("line2", "cost_per_turn"):
        line2_parts.append(cost_per_turn)
    if has_subagents and is_visible("line2", "agents"):
        line2_parts.append(
            f"{CYAN}{metrics['subagent_count']}{RESET} {dim}agents{RESET}"
        )

    if api_status_str and is_visible("line2", "api_status"):
        line2_parts.append(api_status_str)

    # Line 3+: live activity trace (wraps to extra lines if needed)
    line3_lines = []

    # Weekly usage - line 3, first position (with separator)
    if is_visible("line3", "usage_weekly"):
        usage = _fetch_usage()
        usage_weekly_str = _format_usage_weekly(usage)
        if usage_weekly_str and is_visible("line3", "usage_weekly"):
            line3_lines.append(usage_weekly_str)

    recent = metrics["recent_tools"]
    file_edits = metrics["current_turn_file_edits"]
    trail_items = []
    if recent and is_visible("line3", "tool_trace"):
        for t in recent[-6:]:
            p = t.split()
            if len(p) >= 2:
                trail_items.append(f"{dim}{p[0].lower()}{RESET} {GREEN}{p[-1]}{RESET}")
            else:
                trail_items.append(f"{dim}{p[0].lower()}{RESET}")
    file_edit_parts = []
    if file_edits and is_visible("line3", "file_edits"):
        top = sorted(file_edits.items(), key=lambda x: -x[1])[:3]
        file_edit_parts = [f"{YELLOW}{n}{RESET}{dim}×{c}{RESET}" for n, c in top]

    # Wrap trail items across lines based on terminal width
    term_cols = _get_terminal_cols()
    buffer = get_setting("custom", "buffer", default=30)
    term_cols_padded = term_cols - buffer  # buffer to avoid edge clipping
    widget_offset = 10  # widget (7) + padding (3)
    max_width = term_cols_padded - widget_offset
    arrow = f" {dim}\u2192{RESET} "
    arrow_vis = 3  # " → " visible width

    if trail_items or file_edit_parts:
        cur_line_parts = []
        cur_width = 1  # leading space
        for i, item in enumerate(trail_items):
            item_width = _visible_len(item)
            joiner_width = arrow_vis if cur_line_parts else 0
            if cur_line_parts and cur_width + joiner_width + item_width > max_width:
                line3_lines.append(f" {arrow.join(cur_line_parts)}")
                cur_line_parts = [item]
                cur_width = 1 + item_width
            else:
                cur_line_parts.append(item)
                cur_width += joiner_width + item_width
        if cur_line_parts:
            tail = arrow.join(cur_line_parts)
            if file_edit_parts:
                edit_str = " ".join(file_edit_parts)
                edit_width = _visible_len(edit_str)
                sep_width = _visible_len(sep)
                if cur_width + sep_width + edit_width <= max_width:
                    tail += f"{sep}{edit_str}"
                else:
                    line3_lines.append(f" {tail}")
                    tail = f" {edit_str}"
            line3_lines.append(f" {tail}")
        elif file_edit_parts:
            line3_lines.append(f" {' '.join(file_edit_parts)}")

    # ── Compact mode: single line with essentials ──
    if compact_mode:
        compact_parts = []

        # Model name first
        if is_visible("line1", "model"):
            compact_parts.append(f"{BOLD}{MAGENTA}{model}{RESET}")

        # Context bar (line1)
        if is_visible("line1", "context_bar"):
            compact_parts.append(f"{bar}")
            if is_visible("line1", "token_count"):
                compact_parts.append(
                    f"{CYAN}{tokens_str}{RESET}{dim}/{RESET}{GRAY}{limit_str}{RESET}"
                )

        # Usage bars (session + weekly) - fetch once
        usage = None
        if is_visible("line2", "usage") or is_visible("line3", "usage_weekly"):
            usage = _fetch_usage()

        # Session usage (line2)
        if is_visible("line2", "usage") and usage:
            usage_session_str = _format_usage_session(usage)
            if usage_session_str:
                compact_parts.append(usage_session_str)

        # Weekly usage (line3)
        if is_visible("line3", "usage_weekly") and usage:
            usage_weekly_str = _format_usage_weekly(usage)
            if usage_weekly_str:
                compact_parts.append(usage_weekly_str)

        if compact_parts:
            print(f" {sep.join(compact_parts)}")
        return

    # ── Full mode: 3 lines ──

    # Widget: config takes precedence, then env var
    widget_name = get_setting("custom", "widget", default=None) or os.environ.get(
        "STATUSLINE_WIDGET", "matrix"
    )
    widget_fn = _load_widget(widget_name)

    line1_str = f" {sep.join(line1_parts)}" if line1_parts else ""
    line2_str = f" {sep.join(line2_parts)}" if line2_parts else ""

    if widget_fn:
        wdg = widget_fn(frame=metrics["tool_calls"], ratio=ratio)
        print(_truncate(f" {wdg[0]}{line1_str}", term_cols_padded))
        print(_truncate(f" {wdg[1]}{line2_str}", term_cols_padded))
        first_extra = line3_lines[0] if line3_lines else ""
        if first_extra:
            print(_truncate(f" {wdg[2]} {first_extra}", term_cols_padded))
        for extra_line in line3_lines[1:]:
            print(_truncate(f"        {extra_line}", term_cols_padded))
    else:
        if line1_str:
            print(_truncate(f"{line1_str}", term_cols_padded))
        if line2_str:
            print(_truncate(f"{line2_str}", term_cols_padded))
        for i, extra_line in enumerate(line3_lines):
            if i == 0:
                print(_truncate(f"{extra_line}", term_cols_padded))
            else:
                print(_truncate(f"        {extra_line}", term_cols_padded))


if __name__ == "__main__":
    main()
