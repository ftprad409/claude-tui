"""Display formatting for API status and usage data."""

from datetime import datetime, timezone

from .settings import get_setting

# Constants
UTC_OFFSET = "+00:00"

# ANSI colors (kept local to avoid coupling to components layer)
GREEN = "\033[92m"
YELLOW = "\033[93m"
ORANGE = "\033[38;5;208m"
RED = "\033[91m"
GRAY = "\033[90m"
RESET = "\033[0m"

_SEVERITY_RANK = {
    "operational": 0,
    "degraded_performance": 1,
    "partial_outage": 2,
    "major_outage": 3,
}

_SEVERITY_DISPLAY = {
    "degraded_performance": (YELLOW, "degraded"),
    "partial_outage": (ORANGE, "partial outage"),
    "major_outage": (RED, "outage"),
}

_OVERALL_DISPLAY = {
    "minor": (YELLOW, "degraded"),
    "major": (ORANGE, "outage"),
    "critical": (RED, "outage"),
}


def _find_worst_component(components):
    """Find the most severe component status."""
    worst, worst_name = "operational", ""
    worst_rank = 0
    for name, st in components.items():
        rank = _SEVERITY_RANK.get(st, 0)
        if rank > worst_rank:
            worst, worst_name, worst_rank = st, name, rank
    return worst, worst_name


def format_api_status(status_data):
    """Format status data into a colored display string."""
    if not status_data:
        return ""

    components = status_data.get("components", {})
    overall = status_data.get("status", "none")
    worst, worst_name = _find_worst_component(components)

    if worst == "operational" and overall == "none":
        show_when_ok = get_setting("status", "show_when_operational", default=False)
        return f"{GREEN}●{RESET} {GRAY}ok{RESET}" if show_when_ok else ""

    if worst in _SEVERITY_DISPLAY:
        color, label = _SEVERITY_DISPLAY[worst]
        if worst != "degraded_performance" and "Code" in worst_name:
            label = f"Code {label.split()[0]}"
        return f"{color}▲ {label}{RESET}"

    if overall in _OVERALL_DISPLAY:
        color, label = _OVERALL_DISPLAY[overall]
        return f"{color}▲ {label}{RESET}"

    return ""


def _format_reset_countdown(reset_iso: str) -> str:
    """Format ISO reset timestamp into a compact countdown label."""
    if not isinstance(reset_iso, str) or not reset_iso:
        return ""
    try:
        reset_dt = datetime.fromisoformat(reset_iso.replace("Z", UTC_OFFSET))
    except ValueError:
        return ""

    diff = (reset_dt - datetime.now(timezone.utc)).total_seconds()
    if diff <= 0:
        return ""

    h, m = int(diff // 3600), int((diff % 3600) // 60)
    return f"{h}h{m:02d}m" if h > 0 else f"{m}m"


def _format_usage_bar(usage_data, key, pct_label, length=20):
    """Format a usage window as a progress bar line."""
    from claude_tui_components.lines import build_bar_line

    if not usage_data:
        return ""
    window = usage_data.get(key, {})
    pct = window.get("utilization", 0)
    if pct is None:
        return ""

    ratio = min(pct / 100.0, 1.0)
    countdown = _format_reset_countdown(window.get("resets_at", ""))
    return build_bar_line(ratio, length, pct_label=pct_label, icon="⏱" if countdown else "", suffix=countdown)


def format_usage_session(usage_data, length=20):
    """Format session (5-hour) usage for display."""
    return _format_usage_bar(usage_data, "five_hour", "S", length)


def format_usage_weekly(usage_data, length=20):
    """Format weekly (7-day) usage for display."""
    return _format_usage_bar(usage_data, "seven_day", "W", length)
