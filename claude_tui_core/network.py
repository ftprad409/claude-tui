"""
External HTTP communication — fetching, caching, and locking.
Handles status.claude.com and Anthropic OAuth usage API.
"""

import fcntl
import http.client
import json
import os
import ssl
import subprocess
import threading
import time
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

# Re-export formatting for backward compatibility
from .formatting import (  # noqa: E402
    format_api_status,
    format_usage_session,
    format_usage_weekly,
)


def _read_json_file(path: str) -> dict | None:
    """Read and parse a JSON file. Returns None on failure."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_file(path: str, payload: dict) -> None:
    """Atomically write JSON payload to a file path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


def _try_acquire_lock(lock_file: str) -> IO | None:
    """Acquire an exclusive non-blocking lock. Returns file descriptor or None."""
    lock_fd = None
    try:
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        lock_fd = open(lock_file, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except (IOError, OSError):
        if lock_fd:
            try:
                lock_fd.close()
            except (IOError, OSError):
                pass
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


def _fetch_https_json(
    host: str, path: str, headers: dict[str, str], timeout: int
) -> tuple[int | None, dict | None]:
    """Issue a GET request and decode JSON on HTTP 200 responses."""
    conn = None
    try:
        conn = http.client.HTTPSConnection(
            host, timeout=timeout, context=ssl.create_default_context()
        )
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        body = resp.read()
        if status != 200:
            return status, None
        return status, json.loads(body)
    except (
        OSError,
        TimeoutError,
        http.client.HTTPException,
        ssl.SSLError,
        ValueError,
    ):
        return None, None
    finally:
        if conn:
            conn.close()


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

        status, data = _fetch_https_json(
            "status.claude.com",
            "/api/v2/summary.json",
            {"Accept": APPLICATION_JSON},
            timeout=2,
        )
        if status != 200 or not data:
            return cache

        fresh = {
            "fetched_at": time.time(),
            "status": data.get("status", {}).get("indicator", "none"),
            "components": {c["name"]: c["status"] for c in data.get("components", [])},
            "incidents": [
                {
                    "name": i["name"],
                    "status": i["status"],
                    "impact": i["impact"],
                }
                for i in data.get("incidents", [])
            ],
        }
        try:
            _write_json_file(STATUS_CACHE_PATH, fresh)
        except OSError:
            pass
        return fresh
    finally:
        _release_lock(lock_fd)


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
    except (OSError, ValueError):
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
    except (subprocess.SubprocessError, OSError, ValueError):
        pass

    return None


def _build_usage_cache(data):
    """Build a usage cache dict from API response data."""
    return {
        "fetched_at": time.time(),
        "five_hour": data.get("five_hour", {}),
        "seven_day": data.get("seven_day", {}),
        "seven_day_sonnet": data.get("seven_day_sonnet", {}),
        "extra_usage": data.get("extra_usage", {}),
        "retry_count": 0,
        "retry_after": 0,
    }


def _handle_usage_429(cache):
    """Apply exponential backoff on 429 and persist to disk."""
    if not cache:
        cache = {"fetched_at": 0, "retry_count": 0, "retry_after": 0}
    current_retry = cache.get("retry_count", 0)
    backoff = min(120 * (2**current_retry), 600)
    cache["retry_count"] = current_retry + 1
    cache["retry_after"] = time.time() + backoff
    try:
        _write_json_file(USAGE_CACHE_PATH, cache)
    except OSError:
        pass
    return cache


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

        status, data = _fetch_https_json(
            "api.anthropic.com",
            "/api/oauth/usage",
            {
                "Accept": APPLICATION_JSON,
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "claude-code/2.1.80",
            },
            timeout=3,
        )
        if status == 200 and data:
            fresh = _build_usage_cache(data)
            try:
                _write_json_file(USAGE_CACHE_PATH, fresh)
            except OSError:
                pass
            return fresh

        if status == 429:
            return _handle_usage_429(cache)
    finally:
        _release_lock(lock_fd)

    return cache
