"""Status API and usage API integrations (with shared cache)."""

import json
import os
import subprocess
import time
from datetime import datetime

from .constants import (
    APPLICATION_JSON,
    CLAUDE_DIR,
    GRAY,
    GREEN,
    ORANGE,
    RED,
    RESET,
    UTC_OFFSET,
    YELLOW,
)
from .debug import debug_log
from .settings import get_setting

_STATUS_CACHE_PATH = os.path.join(os.path.expanduser("~"), CLAUDE_DIR, "api-status-cache.json")
_STATUS_LOCK_PATH = _STATUS_CACHE_PATH + ".lock"
_USAGE_CACHE_PATH = os.path.join(os.path.expanduser("~"), CLAUDE_DIR, "usage-cache.json")


def _read_json_file(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _try_acquire_lock(lock_file):
    try:
        import fcntl

        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        lock_fd = open(lock_file, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except (IOError, OSError):
        return None


def _release_lock(lock_fd):
    if not lock_fd:
        return
    import fcntl

    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    lock_fd.close()


def fetch_api_status():
    if not get_setting("status", "enabled", default=True):
        return None
    ttl = max(30, get_setting("status", "ttl", default=120))
    cache = _read_json_file(_STATUS_CACHE_PATH)
    if cache and time.time() - cache.get("fetched_at", 0) < ttl:
        return cache
    lock_fd = _try_acquire_lock(_STATUS_LOCK_PATH)
    if lock_fd is None:
        return cache
    try:
        refreshed = _read_json_file(_STATUS_CACHE_PATH)
        if refreshed and time.time() - refreshed.get("fetched_at", 0) < ttl:
            return refreshed
        try:
            import http.client
            import ssl

            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection("status.claude.com", timeout=2, context=ctx)
            try:
                conn.request("GET", "/api/v2/summary.json", headers={"Accept": APPLICATION_JSON})
                resp = conn.getresponse()
                if resp.status == 200:
                    data = json.loads(resp.read())
                    cache = {
                        "fetched_at": time.time(),
                        "status": data.get("status", {}).get("indicator", "none"),
                        "components": {c["name"]: c["status"] for c in data.get("components", [])},
                        "incidents": [
                            {"name": i["name"], "status": i["status"], "impact": i["impact"]}
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
            debug_log("fetch_api_status network fetch failed")
            pass
        return cache
    finally:
        _release_lock(lock_fd)


def format_api_status(status_data):
    if not status_data:
        return ""
    show_when_ok = get_setting("status", "show_when_operational", default=False)
    components = status_data.get("components", {})
    overall = status_data.get("status", "none")
    severity_order = ["operational", "degraded_performance", "partial_outage", "major_outage"]
    worst = "operational"
    worst_name = ""
    for name, st in components.items():
        if st in severity_order and severity_order.index(st) > severity_order.index(worst):
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
    if overall in ("major",):
        return f"{ORANGE}▲ outage{RESET}"
    if overall == "critical":
        return f"{RED}▲ outage{RESET}"
    return ""


def _load_oauth_token():
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        return token
    creds_path = os.path.join(os.path.expanduser("~"), CLAUDE_DIR, ".credentials.json")
    try:
        with open(creds_path, "r") as f:
            creds = json.load(f)
        token = creds.get("claudeAiOauth", {}).get("accessToken") or creds.get("accessToken")
        if token:
            return token
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            blob = json.loads(result.stdout.strip())
            token = blob.get("claudeAiOauth", {}).get("accessToken") or blob.get("accessToken")
            if token:
                return token
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _usage_cache_should_refresh(cache, now, rate_limit):
    if not cache:
        return True
    if now < cache.get("retry_after", 0):
        return False
    five_hour_reset = cache.get("five_hour", {}).get("resets_at", "")
    if five_hour_reset:
        try:
            reset_dt = datetime.fromisoformat(five_hour_reset.replace("Z", UTC_OFFSET))
            if reset_dt.timestamp() < now:
                return True
        except Exception:
            debug_log("usage_cache_should_refresh reset time parse failed")
            pass
    return now - cache.get("fetched_at", 0) >= rate_limit


def _fetch_usage_from_api(token, cache):
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
                return {
                    "fetched_at": time.time(),
                    "five_hour": data.get("five_hour", {}),
                    "seven_day": data.get("seven_day", {}),
                    "seven_day_sonnet": data.get("seven_day_sonnet", {}),
                    "extra_usage": data.get("extra_usage", {}),
                }
            if resp.status == 429:
                has_usage_data = bool(cache and cache.get("five_hour"))
                if not has_usage_data:
                    return None
                current_retry = cache.get("retry_count", 0)
                backoff_seconds = min(120 * (2**current_retry), 600)
                cache["retry_count"] = current_retry + 1
                cache["retry_after"] = time.time() + backoff_seconds
                return cache
        finally:
            conn.close()
    except Exception:
        debug_log("fetch_usage_from_api failed")
        pass
    return cache


def fetch_usage():
    if not get_setting("usage", "enabled", default=True):
        return None
    rate_limit = max(60, get_setting("usage", "rate_limit", default=60))
    cache = _read_json_file(_USAGE_CACHE_PATH)
    lock_fd = _try_acquire_lock(_USAGE_CACHE_PATH + ".lock")
    now = time.time()
    try:
        if cache and now < cache.get("retry_after", 0):
            return cache
        if lock_fd is None:
            return cache
        cache = _read_json_file(_USAGE_CACHE_PATH) or cache
        now = time.time()
        if not _usage_cache_should_refresh(cache, now, rate_limit):
            return cache
        token = _load_oauth_token()
        if not token:
            return cache
        fresh = _fetch_usage_from_api(token, cache)
        if fresh is not None:
            os.makedirs(os.path.dirname(_USAGE_CACHE_PATH), exist_ok=True)
            tmp = _USAGE_CACHE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(fresh, f)
            os.replace(tmp, _USAGE_CACHE_PATH)
        return fresh
    finally:
        _release_lock(lock_fd)


def _color_for_ratio(ratio):
    if ratio >= 0.95:
        return RED
    if ratio >= 0.85:
        return ORANGE
    if ratio >= 0.60:
        return YELLOW
    return GREEN


def _rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


def _lerp_rgb(stops, t):
    t = max(0.0, min(1.0, t))
    for i in range(len(stops) - 1):
        if t <= stops[i + 1][0]:
            seg_t = (t - stops[i][0]) / (stops[i + 1][0] - stops[i][0])
            r = int(stops[i][1] + (stops[i + 1][1] - stops[i][1]) * seg_t)
            g = int(stops[i][2] + (stops[i + 1][2] - stops[i][2]) * seg_t)
            b = int(stops[i][3] + (stops[i + 1][3] - stops[i][3]) * seg_t)
            return _rgb(r, g, b)
    return _rgb(stops[-1][1], stops[-1][2], stops[-1][3])


def _build_modern_usage_bar(ratio, length):
    ratio = max(0.0, min(ratio, 1.0))
    precise_fill = ratio * length
    full_cells = int(precise_fill)
    remainder = precise_fill - full_cells
    partials = "▏▎▍▌▋▊▉"
    full_char = "▮"
    empty_char = "▯"
    stops = [
        (0.00, 166, 227, 161),
        (0.30, 148, 226, 213),
        (0.55, 249, 226, 175),
        (0.80, 250, 179, 135),
        (1.00, 243, 139, 168),
    ]
    empty_color = "\033[38;2;55;59;80m"
    head_color = "\033[38;2;214;226;240m"
    parts = []
    for i in range(length):
        pos = i / max(length - 1, 1)
        if i < full_cells:
            parts.append(f"{_lerp_rgb(stops, pos)}{full_char}{RESET}")
            continue
        if i == full_cells and remainder > 0:
            partial_idx = max(0, min(int(remainder * len(partials)) - 1, len(partials) - 1))
            parts.append(f"{_lerp_rgb(stops, pos)}{partials[partial_idx]}{RESET}")
            continue
        parts.append(f"{empty_color}{empty_char}{RESET}")
    if 0 < precise_fill < length and remainder == 0:
        head_idx = min(full_cells, length - 1)
        parts[head_idx] = f"{head_color}▌{RESET}"
    core = "".join(parts)
    return f"\033[38;2;90;95;120m▏{RESET}{core}\033[38;2;90;95;120m▕{RESET}"


def _format_reset_time(iso_time):
    if not iso_time:
        return ""
    try:
        reset_dt = datetime.fromisoformat(iso_time.replace("Z", UTC_OFFSET))
        now_dt = datetime.now(reset_dt.tzinfo)
        diff = (reset_dt - now_dt).total_seconds()
        if diff <= 0:
            return ""
        hours = int(diff // 3600)
        mins = int((diff % 3600) // 60)
        return f"{hours}h{mins:02d}m" if hours > 0 else f"{mins}m"
    except Exception:
        debug_log("format_reset_time parse failed")
        return ""


def format_usage_session(usage_data, length=20):
    if not usage_data:
        return ""
    five_hour = usage_data.get("five_hour", {})
    if not five_hour:
        return ""
    pct = five_hour.get("utilization", 0)
    if pct is None:
        return ""
    ratio = min(pct / 100.0, 1.0)
    color = _color_for_ratio(ratio)
    bar = _build_modern_usage_bar(ratio, length)
    pct_str = f"{color}{int(pct):>3}%{RESET}"
    reset_str = (f" ↻ {_format_reset_time(five_hour.get('resets_at', ''))}").ljust(7)
    return f"{bar} {pct_str} {reset_str}"


def format_usage_weekly(usage_data, length=20):
    if not usage_data:
        return ""
    seven_day = usage_data.get("seven_day", {})
    if not seven_day:
        return ""
    pct = seven_day.get("utilization", 0)
    if pct is None:
        return ""
    ratio = min(pct / 100.0, 1.0)
    color = _color_for_ratio(ratio)
    bar = _build_modern_usage_bar(ratio, length)
    pct_str = f"{color}{int(pct):>3}%{RESET}{GRAY}w{RESET}"
    reset = _format_reset_time(seven_day.get("resets_at", ""))
    reset_str = (f" ↻ {reset}" if reset else "").ljust(7)
    return f"{bar} {pct_str} {reset_str}"
