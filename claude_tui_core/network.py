"""
Single source of truth for all external HTTP communication.
Handles status.claude.com and Anthropic OAuth usage API with caching and locking.
"""

import fcntl
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import IO

from .settings import get_setting

# Paths
CLAUDE_DIR = ".claude"
STATUS_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), CLAUDE_DIR, "api-status-cache.json"
)
STATUS_LOCK_PATH = STATUS_CACHE_PATH + ".lock"
USAGE_CACHE_PATH = os.path.join(os.path.expanduser("~"), CLAUDE_DIR, "usage-cache.json")
USAGE_LOCK_PATH = USAGE_CACHE_PATH + ".lock"

# Constants
APPLICATION_JSON = "application/json"
UTC_OFFSET = "+00:00"

# ANSI-ish colors (logic only, doesn't import from components to keep core lean)
GREEN = "\033[92m"
YELLOW = "\033[93m"
ORANGE = "\033[38;5;208m"
RED = "\033[91m"
GRAY = "\033[90m"
RESET = "\033[0m"


def _read_json_file(path: str) -> dict | None:
    """Read and parse a JSON file. Returns None on failure."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _try_acquire_lock(lock_file: str) -> IO | None:
    """Acquire an exclusive non-blocking lock. Returns file descriptor or None."""
    try:
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        lock_fd = open(lock_file, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except (IOError, OSError):
        return None


def _release_lock(lock_fd: IO | None) -> None:
    """Release a lock and close the file descriptor."""
    if not lock_fd:
        return
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
    except (IOError, OSError):
        pass


def fetch_api_status(background=False):
    """Get Claude API status from status.claude.com."""
    if not get_setting("status", "enabled", default=True):
        return None

    ttl = max(30, get_setting("status", "ttl", default=120))
    cache = _read_json_file(STATUS_CACHE_PATH)

    is_stale = not cache or time.time() - cache.get("fetched_at", 0) >= ttl

    if not is_stale:
        return cache

    if background:
        # Start background refresh and return stale cache immediately
        t = threading.Thread(
            target=fetch_api_status, kwargs={"background": False}, daemon=True
        )
        t.start()
        return cache

    lock_fd = _try_acquire_lock(STATUS_LOCK_PATH)
    if lock_fd is None:
        return cache  # Return stale if locked

    try:
        # Re-check after acquiring lock
        refreshed = _read_json_file(STATUS_CACHE_PATH)
        if refreshed and time.time() - refreshed.get("fetched_at", 0) < ttl:
            return refreshed

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
                os.makedirs(os.path.dirname(STATUS_CACHE_PATH), exist_ok=True)
                tmp = STATUS_CACHE_PATH + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(cache, f)
                os.replace(tmp, STATUS_CACHE_PATH)
                return cache
        finally:
            conn.close()
    except Exception:
        pass
    finally:
        _release_lock(lock_fd)

    return cache


def format_api_status(status_data):
    """Format status data into a colored display string."""
    if not status_data:
        return ""

    show_when_ok = get_setting("status", "show_when_operational", default=False)
    components = status_data.get("components", {})
    overall = status_data.get("status", "none")

    severity_order = [
        "operational",
        "degraded_performance",
        "partial_outage",
        "major_outage",
    ]
    worst = "operational"
    worst_name = ""

    for name, st in components.items():
        if st in severity_order and severity_order.index(st) > severity_order.index(
            worst
        ):
            worst = st
            worst_name = name

    if worst == "operational" and overall == "none":
        return f"{GREEN}●{RESET} {GRAY}ok{RESET}" if show_when_ok else ""

    if worst == "degraded_performance":
        return f"{YELLOW}▲ degraded{RESET}"
    if worst == "partial_outage":
        label = "Code partial" if "Code" in worst_name else "partial outage"
        return f"{ORANGE}▲ {label}{RESET}"
    if worst == "major_outage":
        label = "Code outage" if "Code" in worst_name else "outage"
        return f"{RED}▲ {label}{RESET}"

    if overall == "minor":
        return f"{YELLOW}▲ degraded{RESET}"
    if overall in ("major", "critical"):
        color = ORANGE if overall == "major" else RED
        return f"{color}▲ outage{RESET}"

    return ""


def _load_oauth_token():
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        return token

    creds_path = os.path.join(os.path.expanduser("~"), CLAUDE_DIR, ".credentials.json")
    try:
        if os.path.exists(creds_path):
            with open(creds_path, "r") as f:
                creds = json.load(f)
            token = creds.get("claudeAiOauth", {}).get("accessToken") or creds.get(
                "accessToken"
            )
            if token:
                return token
    except (OSError, json.JSONDecodeError):
        pass

    # Keychain fallback
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
    except Exception:
        pass

    return None


def fetch_usage(background=False):
    """Fetch usage data from Anthropic API."""
    if not get_setting("usage", "enabled", default=True):
        return None

    rate_limit = max(60, get_setting("usage", "rate_limit", default=60))
    cache = _read_json_file(USAGE_CACHE_PATH)
    now = time.time()

    if cache and now < cache.get("retry_after", 0):
        return cache

    is_stale = not cache or (now - cache.get("fetched_at", 0) >= rate_limit)

    if not is_stale:
        return cache

    if background:
        t = threading.Thread(
            target=fetch_usage, kwargs={"background": False}, daemon=True
        )
        t.start()
        return cache

    lock_fd = _try_acquire_lock(USAGE_LOCK_PATH)
    if lock_fd is None:
        return cache

    try:
        token = _load_oauth_token()
        if not token:
            return cache

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
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": "oauth-2025-04-20",
                },
            )
            resp = conn.getresponse()
            if resp.status == 200:
                data = json.loads(resp.read())
                fresh = {
                    "fetched_at": time.time(),
                    "five_hour": data.get("five_hour", {}),
                    "seven_day": data.get("seven_day", {}),
                    "seven_day_sonnet": data.get("seven_day_sonnet", {}),
                    "extra_usage": data.get("extra_usage", {}),
                    "retry_count": 0,
                    "retry_after": 0,
                }
                os.makedirs(os.path.dirname(USAGE_CACHE_PATH), exist_ok=True)
                tmp = USAGE_CACHE_PATH + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(fresh, f)
                os.replace(tmp, USAGE_CACHE_PATH)
                return fresh

            if resp.status == 429:
                if not cache:
                    return None
                current_retry = cache.get("retry_count", 0)
                backoff = min(120 * (2**current_retry), 600)
                cache["retry_count"] = current_retry + 1
                cache["retry_after"] = time.time() + backoff
                return cache
        finally:
            conn.close()
    except Exception:
        pass
    finally:
        _release_lock(lock_fd)

    return cache


def format_usage_session(usage_data, length=20):
    """Format session usage for display."""
    from claude_tui_components.widgets import build_progress_bar

    if not usage_data:
        return ""
    five_hour = usage_data.get("five_hour", {})
    pct = five_hour.get("utilization", 0)
    if pct is None:
        return ""

    ratio = min(pct / 100.0, 1.0)
    bar = build_progress_bar(ratio, length=length, pct_label="S")

    reset_iso = five_hour.get("resets_at", "")
    reset_str = ""
    if reset_iso:
        try:
            reset_dt = datetime.fromisoformat(reset_iso.replace("Z", UTC_OFFSET))
            diff = (reset_dt - datetime.now(timezone.utc)).total_seconds()
            if diff > 0:
                h, m = int(diff // 3600), int((diff % 3600) // 60)
                reset_str = f" ⏱ {h}h{m:02d}m" if h > 0 else f" ⏱ {m}m"
        except:
            pass

    return f"{bar}{reset_str.ljust(8)}"


def format_usage_weekly(usage_data, length=20):
    """Format weekly usage for display."""
    from claude_tui_components.widgets import build_progress_bar

    if not usage_data:
        return ""
    seven_day = usage_data.get("seven_day", {})
    pct = seven_day.get("utilization", 0)
    if pct is None:
        return ""

    ratio = min(pct / 100.0, 1.0)
    bar = build_progress_bar(ratio, length=length, pct_label="W")

    reset_iso = seven_day.get("resets_at", "")
    reset_str = ""
    if reset_iso:
        try:
            reset_dt = datetime.fromisoformat(reset_iso.replace("Z", UTC_OFFSET))
            diff = (reset_dt - datetime.now(timezone.utc)).total_seconds()
            if diff > 0:
                h, m = int(diff // 3600), int((diff % 3600) // 60)
                reset_str = f" ⏱ {h}h{m:02d}m" if h > 0 else f" ⏱ {m}m"
        except:
            pass

    return f"{bar}{reset_str.ljust(8)}"
