#!/usr/bin/env python3
"""Live session monitor — runs in a separate terminal window.

Usage:
    python3 monitor.py              # auto-detect latest session
    python3 monitor.py <session-id> # monitor specific session
    python3 monitor.py --list       # list recent sessions

Hotkeys:
    s  session stats        d  session details
    l  event log            w  efficiency chart
    e  export session       o  project sessions
    c  settings             a  API cost (legacy)
    i  Claude status        ?  help overlay
    q  quit
"""

import json
import os
import select
import shutil
import textwrap
import subprocess
import sys
import termios
import threading
import time
import tty
import signal
from datetime import datetime, timezone
from pathlib import Path

from lib import (
    _visible_len, _truncate_ansi, _visual_rows,
    load_settings, get_setting, reset_settings_cache,
    MODEL_PRICING, CONTEXT_LIMIT, DEFAULT_CONTEXT_LIMIT, COMPACT_BUFFER,
    RESET, BOLD, DIM, GREEN, YELLOW, ORANGE, RED, CYAN, MAGENTA, WHITE, GRAY,
    CLEAR, HIDE_CURSOR, SHOW_CURSOR, ERASE_LINE, ALT_SCREEN_ON, ALT_SCREEN_OFF,
    LOGO_GREEN, M_DARK, M_MID, M_BRIGHT, PULSE_NEW, PULSE_IDLE,
    find_transcript, find_latest_transcript, find_session_by_id,
    parse_transcript, get_pricing, calc_cost, efficiency_color,
    format_duration_live, format_event_time, format_tokens, get_terminal_width,
)
from chart import (
    _build_segments, _render_horizontal_chart, _render_vertical_chart,
    show_efficiency_chart, run_standalone as _run_chart_standalone,
)

_original_termios = None


# ── Claude status page (Atlassian Statuspage v2 API) ────────────────

_STATUS_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", "api-status-cache.json"
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
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Fetch fresh in background thread to avoid blocking curses loop
    def _do_fetch():
        try:
            import http.client
            import ssl
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(
                "status.claude.com", timeout=2, context=ctx
            )
            try:
                conn.request("GET", "/api/v2/summary.json",
                              headers={"Accept": "application/json"})
                resp = conn.getresponse()
                if resp.status == 200:
                    data = json.loads(resp.read())
                    result = {
                        "fetched_at": time.time(),
                        "status": data.get("status", {}).get("indicator", "none"),
                        "components": {
                            c["name"]: c["status"]
                            for c in data.get("components", [])
                        },
                        "incidents": [
                            {"name": i["name"], "status": i["status"],
                             "impact": i["impact"]}
                            for i in data.get("incidents", [])
                        ],
                    }
                    os.makedirs(os.path.dirname(_STATUS_CACHE_PATH), exist_ok=True)
                    tmp = _STATUS_CACHE_PATH + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(result, f)
                    os.replace(tmp, _STATUS_CACHE_PATH)
            finally:
                conn.close()
        except Exception:
            pass

    t = threading.Thread(target=_do_fetch, daemon=True)
    t.start()

    return cache  # return stale cache while refresh happens in background


def _format_api_status(status_data):
    """Format API status for display. Returns colored string or empty."""
    if not status_data:
        return ""

    show_when_ok = get_setting("status", "show_when_operational", default=False)
    components = status_data.get("components", {})
    overall = status_data.get("status", "none")

    severity_order = ["operational", "degraded_performance",
                      "partial_outage", "major_outage"]
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

    if overall == "minor":
        return f"{YELLOW}\u25b2 degraded{RESET}"
    elif overall == "major":
        return f"{ORANGE}\u25b2 outage{RESET}"
    elif overall == "critical":
        return f"{RED}\u25b2 outage{RESET}"

    return ""


# ── Dashboard rendering ─────────────────────────────────────────────

def color_ratio(ratio, compact_ratio=None):
    """Get color for a context ratio based on proximity to compaction."""
    if compact_ratio and compact_ratio > 0:
        fill = ratio / compact_ratio
    else:
        fill = ratio
    if fill < 0.60:
        return GREEN
    elif fill < 0.85:
        return YELLOW
    elif fill < 0.95:
        return ORANGE
    return RED


def build_bar(ratio, width=30, compact_ratio=None):
    """Build a colored progress bar."""
    filled = int(width * min(ratio, 1.0))
    bar = "█" * filled + "░" * (width - filled)
    return f"{color_ratio(ratio, compact_ratio)}{bar}{RESET}"


def build_sparkline(values, width=50):
    """Build colored sparkline showing per-turn token spend.

    Scaled relative to the peak value so the shape reveals
    which turns were expensive vs cheap.
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
        merged = []
        for i in range(0, len(values), merge_size):
            bucket = values[i:i + merge_size]
            if None in bucket:
                merged.append(None)
            else:
                merged.append(sum(v for v in bucket if v is not None))
        values = merged
        if len(values) > width:
            values = values[-width:]
    else:
        if len(values) > width:
            values = values[-width:]

    blocks = "▁▂▃▄▅▆▇█"
    peak = max((v for v in values if v is not None and v > 0), default=1)
    scale = peak if peak > 0 else 1
    chars = []
    for v in values:
        if v is None:
            chars.append(f"{MAGENTA}↓{RESET}")
            continue
        r = v / scale
        idx = max(0, min(int(r * (len(blocks) - 1)), len(blocks) - 1))
        if r < 0.25:
            color = GREEN
        elif r < 0.50:
            color = CYAN
        elif r < 0.75:
            color = YELLOW
        else:
            color = ORANGE
        chars.append(f"{color}{blocks[idx]}{RESET}")
    return "".join(chars)


def render_matrix_header(frame, width=60, active=True):
    """Render just the matrix rain header line.

    When active=True, animates the rain. When False, shows a static dim line.
    """
    rain = "10110010011101001011100101100110100"
    speeds = [1, 3, 2, 1, 2, 3, 1, 2, 3, 1, 2, 3, 2, 1, 3]
    if active:
        m_colors = [M_DARK, M_DARK, M_MID, M_MID, M_BRIGHT]
    else:
        m_colors = [M_DARK, M_DARK, M_DARK]
    line = ""
    for c in range(width):
        f = frame if active else 0
        idx = c * 5 - f * speeds[c % len(speeds)]
        ch = rain[idx % len(rain)]
        cidx = c * 3 - f * speeds[c % len(speeds)]
        mc = m_colors[cidx % len(m_colors)]
        line += f"{mc}{ch}{RESET}"
    return line


def render_dashboard(r, idle_secs, just_updated, term_width):
    """Render full dashboard as (header_lines, log_lines) tuple.

    header_lines: static sections pinned below matrix header
    log_lines: scrollable LOG section that fills remaining space
    """
    header_lines = _render_header_body(r, idle_secs, just_updated, term_width)
    log_lines = _render_log(r, term_width)
    return header_lines, log_lines


def _render_header_body(r, idle_secs, just_updated, term_width):
    """Render static dashboard sections (session, context, activity, current, session totals)."""
    ctx_limit = r.get("context_limit", DEFAULT_CONTEXT_LIMIT)
    ctx_used = r["last_context"]
    ratio = ctx_used / ctx_limit if ctx_used > 0 else 0
    compact_ratio = (ctx_limit - COMPACT_BUFFER) / ctx_limit if ctx_limit > 0 else 0.83
    duration = format_duration_live(r["start_time"])
    w = min(term_width - 2, 120)  # content width, cap at 120
    bar_width = max(20, min(w - 30, 50))
    bar = build_bar(ratio, bar_width, compact_ratio=compact_ratio)
    spark_width = max(20, min(w - 10, 80))
    sparkline = build_sparkline(r["context_history"], spark_width)

    # Compaction prediction — fixed buffer model: triggers when remaining < COMPACT_BUFFER
    compact_ceiling = ctx_limit - COMPACT_BUFFER
    env_pct = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "")
    if env_pct.isdigit() and 1 <= int(env_pct) <= 100:
        compact_ceiling = min(compact_ceiling, ctx_limit * int(env_pct) / 100)
    turns_left = "—"
    if r["turns_since_compact"] >= 2 and ratio > 0 and ratio < 1.0:
        remaining = compact_ceiling - ctx_used

        # EMA on per-turn context deltas (since last compaction)
        turn_contexts = list(r["context_per_turn"].values())
        if len(turn_contexts) >= 3:
            deltas = [turn_contexts[i] - turn_contexts[i - 1]
                      for i in range(1, len(turn_contexts))
                      if turn_contexts[i] > turn_contexts[i - 1]]
            if deltas:
                alpha = 2 / (min(len(deltas), 5) + 1)
                ema = deltas[0]
                for d in deltas[1:]:
                    ema = alpha * d + (1 - alpha) * ema
                growth = ema
            else:
                growth = 0
        else:
            # Fallback to simple average
            baseline = r.get("context_at_last_compact", 0)
            growth_since = ctx_used - baseline if baseline > 0 else ctx_used
            growth = growth_since / max(r["turns_since_compact"], 1)

        if growth > 0 and remaining > 0:
            tl = int(remaining / growth)
            c = color_ratio(1.0 - tl / 100 if tl < 100 else 0)
            turns_left = f"{c}~{tl}{RESET}"

    # Cache ratio
    total_input = r["tokens"]["input"] + r["tokens"]["cache_read"]
    cache_pct = int(r["tokens"]["cache_read"] / total_input * 100) if total_input > 0 else 0

    # Activity status
    if idle_secs < 5:
        status_dot = f"{GREEN}●{RESET}"
        status_text = f"{GREEN}ACTIVE{RESET}"
    elif idle_secs < 30:
        status_dot = f"{YELLOW}●{RESET}"
        status_text = f"{YELLOW}WORKING{RESET}"
    elif idle_secs < 120:
        status_dot = f"{ORANGE}○{RESET}"
        status_text = f"{ORANGE}IDLE {int(idle_secs)}s{RESET}"
    else:
        idle_m = int(idle_secs / 60)
        status_dot = f"{GRAY}○{RESET}"
        status_text = f"{GRAY}IDLE {idle_m}m{RESET}"

    # Separator color — pulse on new data (full terminal width like matrix)
    sep_color = PULSE_NEW if just_updated else M_MID
    sep = f"{sep_color}{'─' * term_width}{RESET}"

    # Turn timer
    turn_timer = ""
    if r["waiting_for_response"] and r["last_user_ts"]:
        try:
            user_dt = datetime.fromisoformat(r["last_user_ts"].replace("Z", "+00:00"))
            turn_secs = int((datetime.now(timezone.utc) - user_dt).total_seconds())
            if turn_secs < 60:
                tt = f"{turn_secs}s"
            elif turn_secs < 3600:
                tt = f"{turn_secs // 60}m {turn_secs % 60:02d}s"
            else:
                tt = f"{turn_secs // 3600}h {(turn_secs % 3600) // 60}m"
            # Color: green <30s, yellow <2m, orange <5m, red >5m
            if turn_secs < 30:
                tc = GREEN
            elif turn_secs < 120:
                tc = YELLOW
            elif turn_secs < 300:
                tc = ORANGE
            else:
                tc = RED
            turn_timer = f"  {DIM}│{RESET}  {tc}⏱ {tt}{RESET}"
        except Exception:
            pass

    lines = []
    lines.append(sep)
    # Claude API status indicator (appended to header when not operational)
    api_status_str = _format_api_status(_fetch_api_status())
    api_suffix = f"  {DIM}│{RESET}  {api_status_str}" if api_status_str else ""
    lines.append(f"  {status_dot} {BOLD}{MAGENTA}{r['model'] or 'unknown'}{RESET}  {DIM}│{RESET}  {GRAY}{r['session_id']}{RESET}  {DIM}│{RESET}  {WHITE}{duration}{RESET}  {DIM}│{RESET}  {status_text}{turn_timer}{api_suffix}")
    lines.append("")

    # Context section
    lines.append(f"  {BOLD}CONTEXT{RESET}")
    lines.append(f"  {bar}  {color_ratio(ratio, compact_ratio)}{ratio * 100:.1f}%{RESET}  {CYAN}{format_tokens(int(ctx_used))}{RESET}{DIM}/{RESET}{GRAY}{format_tokens(ctx_limit)}{RESET}")
    lines.append(f"  {sparkline}")
    compact_line = f"  {DIM}Compactions:{RESET} {CYAN}{r['compact_count']}{RESET}  {DIM}│{RESET}  {DIM}Turns left:{RESET} {turns_left}  {DIM}│{RESET}  {DIM}Since compact:{RESET} {CYAN}{r['turns_since_compact']}{RESET}"

    # Compaction alert — highlight if just happened
    if r["compact_events"]:
        last_compact = r["compact_events"][-1]
        if last_compact["turns_since_last"] == 0 and r["turns_since_compact"] <= 2:
            compact_line += f"  {BOLD}{YELLOW}⚡ JUST COMPACTED{RESET}"

    lines.append(compact_line)

    # Context efficiency score
    total_built = r["total_context_built"] + ctx_used  # all segments + current
    if total_built > 0:
        wasted = r["tokens_wasted"]
        eff = max(0, 1 - wasted / total_built) if wasted > 0 else 1.0
        eff_pct = int(eff * 100)
        eff_color = efficiency_color(eff_pct)
        wasted_str = format_tokens(int(wasted)) if wasted > 0 else "0"
        total_str = format_tokens(int(total_built))
        lines.append(f"  {DIM}Efficiency:{RESET} {eff_color}{eff_pct}%{RESET}  {DIM}│{RESET}  {DIM}Wasted:{RESET} {RED}{wasted_str}{RESET}{DIM}/{RESET}{GRAY}{total_str}{RESET}")
    lines.append("")

    # Activity section
    lines.append(f"  {BOLD}ACTIVITY{RESET}")
    n_edited = len(r["files_edited"])
    n_created = r["files_created"]
    la = r["lines_added"]
    lr = r["lines_removed"]
    activity_parts = [f"  {CYAN}{n_edited}{RESET} {DIM}files edited{RESET}"]
    if n_created > 0:
        activity_parts.append(f"{CYAN}{n_created}{RESET} {DIM}new{RESET}")
    activity_parts.append(f"{GREEN}+{la}{RESET} {DIM}/{RESET} {RED}-{lr}{RESET} {DIM}lines{RESET}")
    lines.append(f"  {DIM}│{RESET}  ".join(activity_parts))

    # Tool breakdown (use actual tool counts to match stats)
    tc = r["tool_counts"]
    tool_parts = []
    for name, label in [("Read", "read"), ("Edit", "edit"), ("Grep", "grep"),
                         ("Bash", "bash"), ("Write", "write"), ("Glob", "glob")]:
        count = tc.get(name, 0)
        if count > 0:
            tool_parts.append(f"{GRAY}{count}{RESET} {DIM}{label}{RESET}")
    if r["subagent_count"] > 0:
        tool_parts.append(f"{GRAY}{r['subagent_count']}{RESET} {DIM}agents{RESET}")
    lines.append(f"  " + f"  {DIM}│{RESET}  ".join(tool_parts))

    lines.append("")

    # ── Activity: Current turn (this question/answer) ──
    lines.append(f"  {BOLD}CURRENT{RESET}")

    turn_tools = sum(r["turn_tool_counts"].values())
    turn_top3 = r["turn_tool_counts"].most_common(3)
    turn_tools_str = "  ".join(f"{DIM}{t}:{RESET}{CYAN}{c}{RESET}" for t, c in turn_top3)
    lines.append(f"  {CYAN}{turn_tools}{RESET} tools  {DIM}│{RESET}  {turn_tools_str}")

    # Live tool trace + file edit counts (same format as statusline)
    if r["recent_tools"] or r["turn_files_edited"]:
        parts = []
        if r["recent_tools"]:
            trail = []
            for t in r["recent_tools"][-6:]:
                p = t.split()
                if len(p) >= 2:
                    trail.append(f"{DIM}{p[0].lower()}{RESET} {GREEN}{p[-1]}{RESET}")
                else:
                    trail.append(f"{DIM}{p[0].lower()}{RESET}")
            parts.append(f" {DIM}→{RESET} ".join(trail))
        if r["turn_files_edited"]:
            top = sorted(r["turn_files_edited"].items(), key=lambda x: -x[1])[:3]
            parts.append(" ".join(
                f"{YELLOW}{n}{RESET}{DIM}×{c}{RESET}" for n, c in top
            ))
        lines.append(f"  {f' {DIM}│{RESET} '.join(parts)}")

    # Current turn files
    turn_all_files = set(list(r["turn_files_read"].keys()) + list(r["turn_files_edited"].keys()))
    max_turn_files = 3 if w < 60 else 5
    turn_top_files = sorted(
        turn_all_files,
        key=lambda f: -(r["turn_files_read"].get(f, 0) + r["turn_files_edited"].get(f, 0))
    )[:max_turn_files]
    if turn_top_files:
        file_parts = []
        for f in turn_top_files:
            reads = r["turn_files_read"].get(f, 0)
            edits = r["turn_files_edited"].get(f, 0)
            file_parts.append(f"{GREEN}{f}{RESET}{DIM}({reads}r/{edits}e){RESET}")
        lines.append(f"  {' '.join(file_parts)}")

    turn_err_color = RED if r["turn_tool_errors"] > 3 else ORANGE if r["turn_tool_errors"] > 0 else GREEN
    turn_bottom = f"  {turn_err_color}{r['turn_tool_errors']}{RESET} errors  {DIM}│{RESET}  {MAGENTA}{r['turn_thinking']}{RESET} thinking"
    if r["turn_agents_spawned"] > 0:
        active = len(r["turn_agents_pending"])
        total = r["turn_agents_spawned"]
        if active > 0:
            turn_bottom += f"  {DIM}│{RESET}  {YELLOW}{active}{RESET}{DIM}/{RESET}{CYAN}{total}{RESET} agents"
        else:
            turn_bottom += f"  {DIM}│{RESET}  {CYAN}{total}{RESET} agents"
    if r["turn_skill_active"]:
        turn_bottom += f"  {DIM}│{RESET}  {YELLOW}⚡{RESET} {CYAN}{r['turn_skill_active']}{RESET}"
    lines.append(turn_bottom)

    # Last error detail
    if r["last_error_msg"]:
        err_text = r["last_error_msg"].replace("\n", " ").strip()
        max_line = w - 4
        label = f"  {DIM}Last error:{RESET} "
        first_line_max = max_line - 12
        lines.append(f"{label}{RED}{err_text[:first_line_max]}{RESET}")
        remaining = err_text[first_line_max:]
        while remaining:
            lines.append(f"  {RED}{remaining[:max_line]}{RESET}")
            remaining = remaining[max_line:]

    lines.append("")

    # ── Activity: Session totals ──
    lines.append(f"  {BOLD}SESSION{RESET}")
    total_tools = sum(r["tool_counts"].values())
    top3 = r["tool_counts"].most_common(3)
    tools_str = "  ".join(f"{DIM}{t}:{RESET}{GRAY}{c}{RESET}" for t, c in top3)
    lines.append(f"  {GRAY}{r['turns']}{RESET} {DIM}turns{RESET}  {DIM}│{RESET}  {GRAY}{total_tools}{RESET} {DIM}tools{RESET}  {DIM}│{RESET}  {tools_str}")

    # Session files
    max_files = 3 if w < 60 else 5
    top_files = sorted(
        set(list(r["files_read"].keys()) + list(r["files_edited"].keys())),
        key=lambda f: -(r["files_read"].get(f, 0) + r["files_edited"].get(f, 0))
    )[:max_files]
    if top_files:
        file_parts = []
        for f in top_files:
            reads = r["files_read"].get(f, 0)
            edits = r["files_edited"].get(f, 0)
            file_parts.append(f"{DIM}{f}({reads}r/{edits}e){RESET}")
        lines.append(f"  {' '.join(file_parts)}")

    err_color = RED if r["tool_errors"] > 5 else ORANGE if r["tool_errors"] > 0 else GREEN
    think_pct = r["thinking_count"] / max(r["responses"], 1) * 100
    session_stats = f"  {err_color}{r['tool_errors']}{RESET} {DIM}errors{RESET}  {DIM}│{RESET}  {GRAY}{r['thinking_count']}{RESET} {DIM}thinking ({think_pct:.0f}%){RESET}  {DIM}│{RESET}  {GRAY}{cache_pct}%{RESET} {DIM}cache{RESET}"
    if r["subagent_count"] > 0:
        session_stats += f"  {DIM}│{RESET}  {GRAY}{r['subagent_count']}{RESET} {DIM}agents{RESET}"
    if r["skill_count"] > 0:
        session_stats += f"  {DIM}│{RESET}  {GRAY}{r['skill_count']}{RESET} {DIM}skills{RESET}"
    lines.append(session_stats)

    # Truncate lines that would wrap (skip separator which should be full-width)
    return [
        _truncate_ansi(l, term_width) if _visible_len(l) > term_width else l
        for l in lines
    ]


def _render_log(r, term_width):
    """Render the LOG section (scrollable area)."""
    max_log = get_setting("monitor", "log_lines", default=8)
    if max_log is False or max_log == 0:
        return []

    w = min(term_width - 2, 120)
    lines = []

    if r["event_log"]:
        events = r["event_log"]
        if isinstance(max_log, int) and max_log > 0:
            events = events[-max_log:]
        max_desc = w - 14  # 2 indent + 10 timestamp + 2 gap
        indent = " " * 14  # align continuation with description start
        for evt_ts, evt_desc in events:
            t = format_event_time(evt_ts) if evt_ts else "??:??:??"
            # Color events by type
            if evt_desc.startswith("error:"):
                evt_color = RED
            elif evt_desc.startswith("⚡"):
                evt_color = YELLOW
            elif evt_desc.startswith("$"):
                evt_color = CYAN
            elif "edit" in evt_desc or "write" in evt_desc:
                evt_color = GREEN
            elif evt_desc.startswith("grep:") or evt_desc.startswith("glob:"):
                evt_color = MAGENTA
            else:
                evt_color = GRAY
            wrapped = textwrap.wrap(evt_desc, width=max_desc, break_long_words=True, break_on_hyphens=False)
            if not wrapped:
                wrapped = [evt_desc]
            lines.append(f"  {DIM}{t}{RESET}  {evt_color}{wrapped[0]}{RESET}")
            for cont in wrapped[1:]:
                lines.append(f"  {indent}{evt_color}{cont}{RESET}")

    return lines


def render_footer(term_width):
    """Render the sticky footer hotkey bar, adapted to terminal width."""
    sep = f"{DIM}{'─' * term_width}{RESET}"

    if term_width >= 70:
        # Full labels
        keys = (
            f"  {BOLD}{CYAN}s{RESET}{DIM}tats{RESET}  "
            f"{BOLD}{CYAN}d{RESET}{DIM}etails{RESET}  "
            f"{BOLD}{CYAN}l{RESET}{DIM}og{RESET}  "
            f"{BOLD}{CYAN}w{RESET}{DIM}aste{RESET}  "
            f"{BOLD}{CYAN}e{RESET}{DIM}xport{RESET}  "
            f"{DIM}sessi{RESET}{BOLD}{CYAN}o{RESET}{DIM}ns{RESET}  "
            f"{BOLD}{CYAN}c{RESET}{DIM}onfig{RESET}  "
            f"{BOLD}{CYAN}i{RESET}{DIM}nfo{RESET}  "
            f"{BOLD}{CYAN}?{RESET}{DIM}help{RESET}  "
            f"{BOLD}{CYAN}q{RESET}{DIM}uit{RESET}"
        )
    elif term_width >= 40:
        # Short labels
        keys = (
            f" {BOLD}{CYAN}s{RESET}{DIM}tat{RESET} "
            f"{BOLD}{CYAN}d{RESET}{DIM}tl{RESET} "
            f"{BOLD}{CYAN}l{RESET}{DIM}og{RESET} "
            f"{BOLD}{CYAN}w{RESET}{DIM}st{RESET} "
            f"{BOLD}{CYAN}e{RESET}{DIM}xp{RESET} "
            f"{BOLD}{CYAN}o{RESET}{DIM}ss{RESET} "
            f"{BOLD}{CYAN}c{RESET}{DIM}fg{RESET} "
            f"{BOLD}{CYAN}i{RESET}{DIM}nf{RESET} "
            f"{BOLD}{CYAN}?{RESET} "
            f"{BOLD}{CYAN}q{RESET}"
        )
    else:
        # Keys only
        keys = (
            f" {BOLD}{CYAN}s{RESET} "
            f"{BOLD}{CYAN}d{RESET} "
            f"{BOLD}{CYAN}l{RESET} "
            f"{BOLD}{CYAN}w{RESET} "
            f"{BOLD}{CYAN}e{RESET} "
            f"{BOLD}{CYAN}o{RESET} "
            f"{BOLD}{CYAN}c{RESET} "
            f"{BOLD}{CYAN}i{RESET} "
            f"{BOLD}{CYAN}?{RESET} "
            f"{BOLD}{CYAN}q{RESET}"
        )
    return f"{sep}\n{keys}"


def _read_claude_settings():
    """Read ~/.claude/settings.json."""
    path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_claude_settings(settings):
    """Write ~/.claude/settings.json (atomic)."""
    path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def show_settings_panel(term_width):
    """Interactive settings panel for compaction and display config."""
    out = sys.stdout
    fd = sys.stdin.fileno()
    w = min(term_width - 4, 64)

    while True:
        # Read current values
        settings = _read_claude_settings()
        auto_compact = settings.get("autoCompact", True)
        compact_pct = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "")
        sparkline_mode = get_setting("sparkline", "mode", default="tail")
        merge_size = get_setting("sparkline", "merge_size", default=2)
        log_lines = get_setting("monitor", "log_lines", default=8)

        lines = []
        lines.append("")
        lines.append(f"  {BOLD}{'─' * w}{RESET}")
        lines.append(f"  {BOLD}  SETTINGS{RESET}")
        lines.append(f"  {'─' * w}")
        lines.append("")
        lines.append(f"  {RED}⚠ Changes apply to the next Claude Code session{RESET}")
        lines.append("")
        lines.append(f"  {BOLD}  Compaction{RESET}")
        lines.append(f"  {'─' * w}")
        lines.append("")
        ac_status = f"{GREEN}ON{RESET}" if auto_compact else f"{RED}OFF{RESET}"
        lines.append(f"    {BOLD}{CYAN}1{RESET}   Auto-compact          {ac_status}")
        lines.append(f"        {DIM}Toggle automatic context compaction{RESET}")
        lines.append("")
        pct_display = f"{CYAN}{compact_pct}%{RESET}" if compact_pct else f"{DIM}not set (Claude default){RESET}"
        lines.append(f"    {BOLD}{CYAN}2{RESET}   Compact threshold      {pct_display}")
        lines.append(f"        {DIM}CLAUDE_AUTOCOMPACT_PCT_OVERRIDE (1-100){RESET}")
        lines.append(f"        {DIM}Saved to ~/.claude/claudeui.env — source it in your shell profile{RESET}")
        lines.append("")
        lines.append(f"        {RED}⚠{RESET}  {RED}Claude compacts ~33k tokens before the context limit.{RESET}")
        lines.append(f"           {RED}Lower values = compact sooner (more headroom, lose context earlier).{RESET}")
        lines.append(f"           {RED}This env var can only lower the threshold, not raise it.{RESET}")
        lines.append("")
        lines.append(f"  {BOLD}  Display{RESET}")
        lines.append(f"  {'─' * w}")
        lines.append("")
        lines.append(f"    {BOLD}{CYAN}3{RESET}   Sparkline mode         {CYAN}{sparkline_mode}{RESET}")
        lines.append(f"        {DIM}\"tail\" (last N turns) or \"merge\" (combine turns){RESET}")
        lines.append("")
        if sparkline_mode == "merge":
            lines.append(f"    {BOLD}{CYAN}4{RESET}   Merge size             {CYAN}{merge_size}{RESET}")
            lines.append(f"        {DIM}Turns per bar in merge mode{RESET}")
            lines.append("")
        lines.append(f"  {BOLD}  Monitor{RESET}")
        lines.append(f"  {'─' * w}")
        lines.append("")
        if log_lines is False or log_lines == 0:
            log_display = f"{RED}OFF{RESET}"
        else:
            log_display = f"{CYAN}{log_lines}{RESET}"
        lines.append(f"    {BOLD}{CYAN}5{RESET}   Log lines              {log_display}")
        lines.append(f"        {DIM}Number of log entries on monitor screen (0 = off){RESET}")
        lines.append("")
        lines.append(f"  {'─' * w}")
        lines.append(f"  {DIM}Press {BOLD}1-5{RESET}{DIM} to change, {BOLD}ESC{RESET}{DIM} or {BOLD}q{RESET}{DIM} to close{RESET}")

        out.write(CLEAR + "\n".join(lines))
        out.flush()

        # Wait for input
        while True:
            if select.select([sys.stdin], [], [], 0.1)[0]:
                byte = os.read(fd, 1)
                # Drain escape sequences
                while select.select([sys.stdin], [], [], 0.01)[0]:
                    os.read(fd, 1)
                ch = byte.decode("utf-8", errors="ignore")

                if ch in ("\x1b", "q", "Q"):
                    return

                elif ch == "1":
                    # Toggle autoCompact
                    settings["autoCompact"] = not auto_compact
                    _write_claude_settings(settings)
                    break  # re-render

                elif ch == "2":
                    # Edit compact threshold
                    val = _input_number(out, fd, w,
                                        "Compact threshold (1-100)",
                                        compact_pct if compact_pct else "not set", 1, 100)
                    if val is not None:
                        # Write to shell profile
                        _save_env_override(
                            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", str(val))
                    break  # re-render

                elif ch == "3":
                    # Toggle sparkline mode
                    new_mode = "merge" if sparkline_mode == "tail" else "tail"
                    _save_claudeui_setting("sparkline", "mode", new_mode)
                    break  # re-render

                elif ch == "4" and sparkline_mode == "merge":
                    # Edit merge size
                    val = _input_number(out, fd, w,
                                        "Merge size (1-10)",
                                        merge_size, 1, 10)
                    if val is not None:
                        _save_claudeui_setting(
                            "sparkline", "merge_size", val)
                    break  # re-render

                elif ch == "5":
                    # Edit log lines (0 = off)
                    current_display = log_lines if isinstance(log_lines, int) and log_lines > 0 else "0 (off)"
                    val = _input_number(out, fd, w,
                                        "Log lines (0 = off, 1-50)",
                                        current_display, 0, 50)
                    if val is not None:
                        _save_claudeui_setting(
                            "monitor", "log_lines", val)
                    break  # re-render


def _input_number(out, fd, w, prompt, current, min_val, max_val):
    """Show inline number input, return int or None on cancel."""
    buf = ""
    while True:
        lines = []
        lines.append("")
        lines.append(f"  {BOLD}{'─' * w}{RESET}")
        lines.append(f"  {BOLD}  {prompt}{RESET}")
        lines.append(f"  {'─' * w}")
        lines.append("")
        lines.append(f"    Current: {CYAN}{current}{RESET}")
        lines.append(f"    New:     {BOLD}{buf}▌{RESET}")
        lines.append("")
        lines.append(f"  {DIM}Type a number, ENTER to confirm, ESC to cancel{RESET}")
        out.write(CLEAR + "\n".join(lines))
        out.flush()

        if select.select([sys.stdin], [], [], 0.1)[0]:
            byte = os.read(fd, 1)
            ch = byte.decode("utf-8", errors="ignore")
            if ch == "\x1b":
                # Drain escape sequence
                while select.select([sys.stdin], [], [], 0.01)[0]:
                    os.read(fd, 1)
                return None
            elif ch in ("\r", "\n"):
                if buf:
                    try:
                        val = int(buf)
                        if min_val <= val <= max_val:
                            return val
                    except ValueError:
                        pass
                return None
            elif ch == "\x7f" and buf:  # backspace
                buf = buf[:-1]
            elif ch.isdigit() and len(buf) < 5:
                buf += ch


def _save_claudeui_setting(*keys_and_value):
    """Save a setting to ~/.claude/claudeui.json. Last arg is value."""
    path = os.path.join(os.path.expanduser("~"), ".claude", "claudeui.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cfg = {}
    # Navigate/create nested keys
    keys = keys_and_value[:-1]
    value = keys_and_value[-1]
    d = cfg
    for k in keys[:-1]:
        if k not in d or not isinstance(d[k], dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    # Force settings reload
    reset_settings_cache()


def _save_env_override(var_name, value):
    """Save env var to ~/.claude/claudeui.env for user to source."""
    path = os.path.join(os.path.expanduser("~"), ".claude", "claudeui.env")
    lines = []
    found = False
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if line.startswith(f"export {var_name}="):
                    lines.append(f"export {var_name}={value}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"export {var_name}={value}\n")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(lines)
    os.replace(tmp, path)
    # Also set in current process for display
    os.environ[var_name] = value


def render_help_overlay(term_width):
    """Render help overlay."""
    w = min(term_width - 4, 60)
    lines = []
    lines.append("")
    lines.append(f"  {BOLD}{'─' * w}{RESET}")
    lines.append(f"  {BOLD}  KEYBOARD SHORTCUTS{RESET}")
    lines.append(f"  {'─' * w}")
    lines.append("")
    shortcuts = [
        ("s", "Session stats — full breakdown, token sparkline, tool usage"),
        ("d", "Session details — detailed session view from session-manager"),
        ("l", "Event log — scrollable, f to filter, a for live auto-scroll"),
        ("w", "Efficiency chart — token waste per segment, v to toggle view"),
        ("e", "Export session — save session as markdown"),
        ("o", "List sessions — browse sessions for this project"),
        ("c", "Settings — compaction, sparkline, display config"),
        ("a", "API cost breakdown (legacy)"),
        ("i", "Claude status — component health, active incidents"),
        ("?", "Toggle this help overlay"),
        ("q", "Quit the monitor"),
    ]
    for key, desc in shortcuts:
        lines.append(f"    {BOLD}{CYAN}{key}{RESET}   {desc}")
    lines.append("")
    features = [
        "Live duration updates every second",
        "Activity indicator: ● ACTIVE / ● WORKING / ○ IDLE",
        "Green pulse on separator when new data arrives",
        "⚡ JUST COMPACTED alert after compaction events",
        "Activity tracking — files edited, lines added/removed",
        "Live tool trace — last 5 tool calls",
        "Last error message displayed inline",
        "Auto-follow — switches to new session when current ends",
        "Adapts to terminal width",
    ]
    lines.append(f"  {BOLD}  FEATURES{RESET}")
    lines.append(f"  {'─' * w}")
    lines.append("")
    for feat in features:
        lines.append(f"    {DIM}•{RESET} {feat}")
    lines.append("")
    lines.append(f"  {BOLD}  SETTINGS{RESET}")
    lines.append(f"  {'─' * w}")
    lines.append("")
    lines.append(f"    Edit {CYAN}~/.claude/claudeui.json{RESET} (hot-reloads):")
    lines.append(f"    {DIM}•{RESET} sparkline.mode      {DIM}—{RESET} \"tail\" (last N) or \"merge\" (combine turns)")
    lines.append(f"    {DIM}•{RESET} sparkline.merge_size {DIM}—{RESET} turns per bar in merge mode (default: 2)")
    lines.append("")
    lines.append(f"  {BOLD}{'─' * w}{RESET}")
    lines.append(f"  {DIM}Press any key to close{RESET}")
    return lines


def render_status_overlay(term_width):
    """Render Claude API status overlay with all components and incidents."""
    w = min(term_width - 4, 60)
    lines = []
    lines.append("")
    lines.append(f"  {BOLD}{'─' * w}{RESET}")
    lines.append(f"  {BOLD}  CLAUDE STATUS{RESET}  {DIM}status.claude.com{RESET}")
    lines.append(f"  {'─' * w}")
    lines.append("")

    status_data = _fetch_api_status()
    if not status_data:
        lines.append(f"    {GRAY}Fetching status...{RESET}")
        lines.append("")
        lines.append(f"  {BOLD}{'─' * w}{RESET}")
        lines.append(f"  {DIM}Press any key to close{RESET}")
        return lines

    # Component statuses
    status_icons = {
        "operational": f"{GREEN}\u25cf operational{RESET}",
        "degraded_performance": f"{YELLOW}\u25b2 degraded{RESET}",
        "partial_outage": f"{ORANGE}\u25b2 partial outage{RESET}",
        "major_outage": f"{RED}\u25b2 major outage{RESET}",
    }

    components = status_data.get("components", {})
    max_name = max((len(n) for n in components), default=0)
    for name, st in components.items():
        icon = status_icons.get(st, f"{GRAY}{st}{RESET}")
        lines.append(f"    {WHITE}{name:<{max_name}}{RESET}  {icon}")

    # Active incidents
    incidents = status_data.get("incidents", [])
    if incidents:
        lines.append("")
        lines.append(f"  {BOLD}  ACTIVE INCIDENTS{RESET}")
        lines.append(f"  {'─' * w}")
        lines.append("")
        for inc in incidents:
            impact = inc.get("impact", "none")
            if impact == "critical":
                ic = RED
            elif impact == "major":
                ic = ORANGE
            else:
                ic = YELLOW
            lines.append(
                f"    {ic}\u25b2{RESET} {inc['name']} {DIM}— {inc['status']}{RESET}"
            )
    else:
        lines.append("")
        lines.append(f"    {GREEN}No active incidents{RESET}")

    # Cache age
    age = time.time() - status_data.get("fetched_at", 0)
    if age < 60:
        age_str = f"{int(age)}s ago"
    else:
        age_str = f"{int(age / 60)}m ago"
    lines.append("")
    lines.append(f"    {DIM}Updated {age_str}{RESET}")
    lines.append("")
    lines.append(f"  {BOLD}{'─' * w}{RESET}")
    lines.append(f"  {DIM}Press any key to close{RESET}")
    return lines


def render_cost_overlay(r, term_width):
    """Render legacy cost overlay (API billing details)."""
    w = min(term_width - 4, 60)
    pricing = get_pricing(r["model"])
    cost = calc_cost(r["tokens"], pricing)

    # Cache savings
    cache_without = r["tokens"]["cache_read"] * pricing["input"] / 1_000_000
    cache_actual = r["tokens"]["cache_read"] * pricing["cache_read"] / 1_000_000
    saved = cache_without - cache_actual

    # Cost per turn
    cpt = cost["total"] / r["turns"] if r["turns"] > 0 else 0

    # Cost per minute
    cost_per_min = ""
    if r["start_time"]:
        try:
            start = datetime.fromisoformat(r["start_time"].replace("Z", "+00:00"))
            elapsed_min = (datetime.now(timezone.utc) - start).total_seconds() / 60
            if elapsed_min > 1:
                cpm = cost["total"] / elapsed_min
                cost_per_min = f"  {DIM}│{RESET}  {ORANGE}${cpm:.2f}/min{RESET}"
        except Exception:
            pass

    # Token breakdown
    tok_total = r["tokens"]["input"] + r["tokens"]["cache_read"] + r["tokens"]["cache_creation"] + r["tokens"]["output"]

    lines = []
    lines.append("")
    lines.append(f"  {BOLD}{'─' * w}{RESET}")
    lines.append(f"  {BOLD}  COST (API billing){RESET}")
    lines.append(f"  {'─' * w}")
    lines.append("")
    lines.append(f"    {YELLOW}${cost['total']:.2f}{RESET} total  {DIM}│{RESET}  ~{GRAY}${cpt:.2f}/turn{RESET}{cost_per_min}  {DIM}│{RESET}  {GREEN}${saved:.2f} saved{RESET}")
    lines.append("")
    lines.append(f"    {DIM}Input:{RESET}       ${cost['input']:.2f}")
    lines.append(f"    {DIM}Cache read:{RESET}  ${cost['cache_read']:.2f}")
    lines.append(f"    {DIM}Cache write:{RESET} ${cost['cache_write']:.2f}")
    lines.append(f"    {DIM}Output:{RESET}      ${cost['output']:.2f}")

    if tok_total > 0:
        lines.append("")
        tb_width = max(20, min(w - 6, 50))
        inp_frac = r["tokens"]["input"] / tok_total
        cache_r_frac = r["tokens"]["cache_read"] / tok_total
        cache_w_frac = r["tokens"]["cache_creation"] / tok_total
        out_frac = r["tokens"]["output"] / tok_total
        inp_w = max(1, int(tb_width * inp_frac)) if inp_frac > 0.005 else 0
        out_w = max(1, int(tb_width * out_frac)) if out_frac > 0.005 else 0
        cw_w = max(1, int(tb_width * cache_w_frac)) if cache_w_frac > 0.005 else 0
        cr_w = tb_width - inp_w - cw_w - out_w
        tok_bar = f"{CYAN}{'█' * inp_w}{RESET}{GREEN}{'█' * cr_w}{RESET}{MAGENTA}{'█' * cw_w}{RESET}{YELLOW}{'█' * out_w}{RESET}"
        tok_legend = f"{CYAN}■{RESET}{DIM}input {inp_frac:.0%}{RESET}  {GREEN}■{RESET}{DIM}cache {cache_r_frac:.0%}{RESET}  {MAGENTA}■{RESET}{DIM}write {cache_w_frac:.0%}{RESET}  {YELLOW}■{RESET}{DIM}output {out_frac:.0%}{RESET}"
        lines.append(f"    {tok_bar}")
        lines.append(f"    {tok_legend}")

    lines.append("")
    lines.append(f"  {BOLD}{'─' * w}{RESET}")
    lines.append(f"  {DIM}Press any key to close{RESET}")
    return lines


FILTER_NAMES = ["all", "errors", "bash", "edits", "search", "agents", "skills", "compactions"]

FILTER_MATCHERS = {
    "all": lambda d: True,
    "errors": lambda d: d.startswith("error:"),
    "bash": lambda d: d.startswith("$"),
    "edits": lambda d: any(w in d for w in ("edit ", "write ")),
    "search": lambda d: any(d.startswith(p) for p in ("grep:", "glob:", "read ")),
    "agents": lambda d: d.startswith("agent:"),
    "skills": lambda d: d.startswith("skill:"),
    "compactions": lambda d: d.startswith("⚡"),
}


def _build_log_lines(raw_log, max_desc, filter_name="all"):
    """Build formatted display lines from raw event log."""
    indent = " " * 14
    matcher = FILTER_MATCHERS.get(filter_name, FILTER_MATCHERS["all"])
    lines = []
    event_count = 0
    for evt_ts, evt_desc in raw_log:
        if not matcher(evt_desc):
            continue
        event_count += 1
        t = format_event_time(evt_ts) if evt_ts else "??:??:??"
        if evt_desc.startswith("error:"):
            evt_color = RED
        elif evt_desc.startswith("⚡"):
            evt_color = YELLOW
        elif evt_desc.startswith("$"):
            evt_color = CYAN
        elif "edit" in evt_desc or "write" in evt_desc:
            evt_color = GREEN
        elif evt_desc.startswith("grep:") or evt_desc.startswith("glob:"):
            evt_color = MAGENTA
        else:
            evt_color = GRAY
        wrapped = textwrap.wrap(evt_desc, width=max_desc, break_long_words=True, break_on_hyphens=False)
        if not wrapped:
            wrapped = [evt_desc]
        lines.append(f"  {DIM}{t}{RESET}  {evt_color}{wrapped[0]}{RESET}")
        for cont in wrapped[1:]:
            lines.append(f"  {indent}{evt_color}{cont}{RESET}")
    return lines, event_count


def show_log_viewer(transcript_path, term_width):
    """Interactive log viewer with filtering and auto-scroll."""
    out = sys.stdout

    filter_idx = 0  # index into FILTER_NAMES
    auto_follow = True
    last_mtime = 0
    last_w = 0
    raw_log = []
    log_lines = []
    event_count = 0
    total = 0
    scroll_pos = 0
    needs_rebuild = True
    needs_redraw = True

    while True:
        # Recalculate width each loop iteration (handles terminal resize)
        cur_tw = shutil.get_terminal_size().columns
        w = cur_tw - 4
        max_desc = w - 14
        if w != last_w:
            last_w = w
            needs_rebuild = True

        # Reload transcript if file changed or first run
        try:
            mtime = os.stat(transcript_path).st_mtime
        except FileNotFoundError:
            mtime = last_mtime
        if mtime != last_mtime:
            last_mtime = mtime
            r = parse_transcript(transcript_path)
            raw_log = r.get("full_log", [])
            needs_rebuild = True

        if needs_rebuild:
            filter_name = FILTER_NAMES[filter_idx]
            log_lines, event_count = _build_log_lines(raw_log, max_desc, filter_name)
            total = len(log_lines)
            term_h = shutil.get_terminal_size().lines
            page_size = max(1, term_h - 5)
            max_scroll = max(0, total - page_size)
            if auto_follow:
                scroll_pos = max_scroll
            else:
                scroll_pos = min(scroll_pos, max_scroll)
            needs_rebuild = False
            needs_redraw = True

        # Render only when needed
        if needs_redraw:
            visible = log_lines[scroll_pos:scroll_pos + page_size]
            filter_name = FILTER_NAMES[filter_idx]
            filter_label = f"  filter: {BOLD}{filter_name}{RESET}" if filter_name != "all" else ""
            follow_label = f"  {GREEN}● LIVE{RESET}" if auto_follow else ""
            header = f"  {BOLD}LOG{RESET}  {DIM}({event_count} events){RESET}{filter_label}{follow_label}"
            pos_info = f"{scroll_pos + 1}-{min(scroll_pos + page_size, total)}/{total}" if total > 0 else "0/0"
            footer = f"  {DIM}j/k ↑/↓  ^D/^U  ^F/^B  g/G  f filter  a live  q close{RESET}  {DIM}{pos_info}{RESET}"

            buf = CLEAR + header + "\n"
            buf += f"  {'─' * w}\n"
            buf += "\n".join(visible) + "\n"
            pad = page_size - len(visible)
            if pad > 0:
                buf += "\n" * pad
            buf += f"  {'─' * w}\n"
            buf += footer
            out.write(buf)
            out.flush()
            needs_redraw = False

        # Wait for key or auto-refresh (1s when following, blocking when not)
        timeout = 1.0 if auto_follow else 60.0
        deadline = time.time() + timeout
        got_key = False
        while time.time() < deadline:
            wait = max(0.01, deadline - time.time())
            if select.select([sys.stdin], [], [], wait)[0]:
                raw = os.read(sys.stdin.fileno(), 8).decode("utf-8", errors="ignore")
                if raw in ("q", "Q", "\x1b"):
                    return
                elif raw in ("\x1b[A", "k", "K"):  # up
                    scroll_pos = max(0, scroll_pos - 1)
                    auto_follow = False
                    got_key = True
                    break
                elif raw in ("\x1b[B", "j", "J"):  # down
                    scroll_pos = min(max(0, total - page_size), scroll_pos + 1)
                    if scroll_pos >= max(0, total - page_size):
                        auto_follow = True
                    got_key = True
                    break
                elif raw in ("\x1b[5~", "\x02"):  # page up / Ctrl+B
                    scroll_pos = max(0, scroll_pos - page_size)
                    auto_follow = False
                    got_key = True
                    break
                elif raw in ("\x1b[6~", "\x06"):  # page down / Ctrl+F
                    scroll_pos = min(max(0, total - page_size), scroll_pos + page_size)
                    if scroll_pos >= max(0, total - page_size):
                        auto_follow = True
                    got_key = True
                    break
                elif raw == "\x04":  # Ctrl+D — half page down
                    scroll_pos = min(max(0, total - page_size), scroll_pos + page_size // 2)
                    if scroll_pos >= max(0, total - page_size):
                        auto_follow = True
                    got_key = True
                    break
                elif raw == "\x15":  # Ctrl+U — half page up
                    scroll_pos = max(0, scroll_pos - page_size // 2)
                    auto_follow = False
                    got_key = True
                    break
                elif raw == "g":  # top
                    scroll_pos = 0
                    auto_follow = False
                    got_key = True
                    break
                elif raw == "G":  # bottom
                    scroll_pos = max(0, total - page_size)
                    auto_follow = True
                    got_key = True
                    break
                elif raw in ("f", "F"):  # cycle filter
                    filter_idx = (filter_idx + 1) % len(FILTER_NAMES)
                    needs_rebuild = True
                    got_key = True
                    break
                elif raw in ("a", "A"):  # toggle auto-follow
                    auto_follow = not auto_follow
                    if auto_follow:
                        scroll_pos = max(0, total - page_size)
                    got_key = True
                    break
            else:
                # No input — if auto-follow, check for file changes
                if auto_follow:
                    try:
                        new_mtime = os.stat(transcript_path).st_mtime
                    except FileNotFoundError:
                        new_mtime = last_mtime
                    if new_mtime != last_mtime:
                        needs_rebuild = True
                        break
        if got_key:
            needs_redraw = True
        elif not needs_rebuild:
            # Auto-follow timeout — check for new data
            if auto_follow:
                needs_rebuild = True


# ── Session management ──────────────────────────────────────────────

def list_sessions():
    """List recent sessions across all projects."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        print("No sessions found.")
        return

    sessions = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            mtime = jsonl.stat().st_mtime
            size = jsonl.stat().st_size
            sessions.append((jsonl, project_dir.name, jsonl.stem[:8], mtime, size))

    sessions.sort(key=lambda x: -x[3])

    print(f"\n  {BOLD}Recent Sessions{RESET}\n")
    print(f"  {'ID':<10} {'Project':<40} {'Size':>8}  {'Modified'}")
    print(f"  {'─' * 10} {'─' * 40} {'─' * 8}  {'─' * 20}")
    for path, project, sid, mtime, size in sessions[:15]:
        dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        size_str = format_tokens(size)
        proj_short = project.replace("-Users-", "~/").replace("-", "/")
        print(f"  {sid:<10} {proj_short:<40} {size_str:>8}  {dt}")

    print(f"\n  {DIM}Usage: python3 monitor.py <session-id>{RESET}\n")


# ── Input handling ──────────────────────────────────────────────────

VALID_KEYS = frozenset("qQsSdDlLwWeEoOcCaAiI?")


def get_key():
    """Non-blocking key read. Drains buffer, returns last meaningful key or None."""
    key = None
    fd = sys.stdin.fileno()
    while select.select([sys.stdin], [], [], 0)[0]:
        byte = os.read(fd, 1).decode("utf-8", errors="ignore")
        if byte in VALID_KEYS:
            key = byte
    return key


# ── External tool runner ────────────────────────────────────────────

def find_tool_script(name):
    """Find a sibling tool script relative to this monitor script."""
    monitor_dir = Path(__file__).resolve().parent
    repo_dir = monitor_dir.parent
    candidates = {
        "stats": repo_dir / "claude-code-session-stats" / "session-stats.py",
        "manager": repo_dir / "claude-code-session-manager" / "session-manager.py",
    }
    return str(candidates.get(name, ""))


def run_tool(script_path, args):
    """Run an external tool script, pausing the monitor."""
    # Leave alt screen so tool output goes to normal buffer
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _original_termios)
    sys.stdout.write(ALT_SCREEN_OFF + SHOW_CURSOR)
    sys.stdout.flush()
    cmd = [sys.executable, script_path] + args
    try:
        subprocess.run(cmd)
    except Exception as e:
        print(f"\n{RED}Error running tool: {e}{RESET}")
    print(f"\n{DIM}Press any key to return to monitor...{RESET}")
    # Switch to cbreak for the "any key" wait
    tty.setcbreak(sys.stdin.fileno())
    os.read(sys.stdin.fileno(), 1)
    # Re-enter alt screen for monitor
    sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR)
    sys.stdout.flush()


def export_session(path, session_id):
    """Export current session as markdown."""
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _original_termios)
    sys.stdout.write(ALT_SCREEN_OFF + SHOW_CURSOR)
    sys.stdout.flush()

    manager = find_tool_script("manager")
    if os.path.exists(manager):
        export_path = f"{session_id}-export.md"
        try:
            with open(export_path, "w") as f:
                subprocess.run([sys.executable, manager, "export", session_id], stdout=f)
            print(f"\n{GREEN}Exported to {export_path}{RESET}")
        except Exception as e:
            print(f"\n{RED}Export failed: {e}{RESET}")
    else:
        print(f"\n{YELLOW}session-manager not found — cannot export{RESET}")

    print(f"\n{DIM}Press any key to return to monitor...{RESET}")
    tty.setcbreak(sys.stdin.fileno())
    os.read(sys.stdin.fileno(), 1)
    sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR)
    sys.stdout.flush()


# ── Splash screen ────────────────────────────────────────────────────

LOGO_LINES = [
    (f" {BOLD} ██████╗ ██╗      █████╗ ██╗   ██╗██████╗ ███████╗", f"{LOGO_GREEN}████████╗██╗   ██╗██╗{RESET}"),
    (f" {BOLD}██╔════╝ ██║     ██╔══██╗██║   ██║██╔══██╗██╔════╝", f"{LOGO_GREEN}╚══██╔══╝██║   ██║██║{RESET}"),
    (f" {BOLD}██║      ██║     ███████║██║   ██║██║  ██║█████╗  ", f"{LOGO_GREEN}   ██║   ██║   ██║██║{RESET}"),
    (f" {BOLD}██║      ██║     ██╔══██║██║   ██║██║  ██║██╔══╝  ", f"{LOGO_GREEN}   ██║   ██║   ██║██║{RESET}"),
    (f" {BOLD}╚██████╗ ███████╗██║  ██║╚██████╔╝██████╔╝███████╗", f"{LOGO_GREEN}   ██║   ╚██████╔╝██║{RESET}"),
    (f" {BOLD} ╚═════╝ ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝", f"{LOGO_GREEN}   ╚═╝    ╚═════╝ ╚═╝{RESET}"),
]


def show_splash(out, status_text="Searching for session..."):
    """Render the splash screen with logo and status line."""
    term_h = shutil.get_terminal_size().lines
    term_w = shutil.get_terminal_size().columns
    logo_height = len(LOGO_LINES)
    # Center vertically (logo + 2 blank + status + subtitle)
    top_pad = max(0, (term_h - logo_height - 4) // 2)

    out.write(CLEAR)
    out.write("\n" * top_pad)

    for claude_part, ui_part in LOGO_LINES:
        line = claude_part + ui_part
        # Rough center: logo is ~71 chars wide
        pad = max(0, (term_w - 71) // 2)
        out.write(" " * pad + line + "\n")

    out.write("\n")
    subtitle = f"{DIM}Live Session Monitor{RESET}"
    pad = max(0, (term_w - 20) // 2)
    out.write(" " * pad + subtitle + "\n\n")

    # Status line
    status_pad = max(0, (term_w - len(status_text)) // 2)
    out.write(" " * status_pad + f"{CYAN}{status_text}{RESET}")
    out.flush()


def update_splash_status(out, status_text):
    """Update just the status line on the splash screen."""
    term_h = shutil.get_terminal_size().lines
    term_w = shutil.get_terminal_size().columns
    logo_height = len(LOGO_LINES)
    top_pad = max(0, (term_h - logo_height - 4) // 2)
    status_row = top_pad + logo_height + 3  # logo + blank + subtitle + blank
    out.write(f"\033[{status_row};1H{ERASE_LINE}")
    pad = max(0, (term_w - len(status_text)) // 2)
    out.write(" " * pad + f"{CYAN}{status_text}{RESET}")
    out.flush()


# ── Main loop ───────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        list_sessions()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "--chart":
        _run_chart_standalone(sys.argv[2] if len(sys.argv) > 2 else None)
        return

    global _original_termios
    old_settings = termios.tcgetattr(sys.stdin)
    _original_termios = old_settings
    out = sys.stdout

    running = True

    def handle_sigint(sig, frame_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGWINCH, lambda s, f: None)  # handle terminal resize

    try:
        tty.setcbreak(sys.stdin.fileno())
        out.write(ALT_SCREEN_ON + HIDE_CURSOR)
        out.flush()

        # ── Splash screen with background loading ──
        splash_start = time.time()
        show_splash(out)

        # Load session + settings in background thread
        load_result = {}

        def _load_session():
            load_result["settings"] = load_settings()
            if len(sys.argv) > 1:
                load_result["path"] = find_session_by_id(sys.argv[1])
            else:
                load_result["path"] = find_transcript()
            if load_result.get("path"):
                try:
                    load_result["data"] = parse_transcript(load_result["path"])
                except Exception:
                    load_result["data"] = None

        loader = threading.Thread(target=_load_session, daemon=True)
        loader.start()

        # Animate splash status while loading
        dots = 0
        while loader.is_alive():
            dots = (dots + 1) % 4
            status = "Searching for session" + "." * dots + " " * (3 - dots)
            if load_result.get("path"):
                sid = Path(load_result["path"]).stem[:8]
                status = f"Loading session {sid}" + "." * dots + " " * (3 - dots)
            update_splash_status(out, status)
            time.sleep(0.3)

        # Ensure splash shows for at least 1.2s
        elapsed = time.time() - splash_start
        if elapsed < 1.2:
            if load_result.get("path"):
                sid = Path(load_result["path"]).stem[:8]
                update_splash_status(out, f"Session {sid} ready")
            time.sleep(1.2 - elapsed)

        # Check if session was found
        path = load_result.get("path")
        if not path:
            out.write(SHOW_CURSOR + ALT_SCREEN_OFF)
            out.flush()
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            if len(sys.argv) > 1:
                print(f"Session '{sys.argv[1]}' not found. Use --list to see sessions.")
            else:
                print("No active session found. Use --list or pass a session ID.")
            sys.exit(1)

        session_id = Path(path).stem[:8]
        r = load_result.get("data")

        last_mtime = 0
        frame = 0
        needs_full_redraw = True
        show_help = False
        last_data_time = time.time()
        last_duration_sec = -1  # track when to redraw for live duration
        just_updated = False
        update_flash_until = 0  # timestamp until which the pulse is shown

        # Force initial parse if background load succeeded
        cached_header = None
        cached_log = None
        if r:
            try:
                last_mtime = os.stat(path).st_mtime
            except FileNotFoundError:
                pass
            term_width = get_terminal_width()
            cached_header, cached_log = render_dashboard(r, 0, True, term_width)
            needs_full_redraw = True

        while running:
            try:
                now = time.time()
                term_width = get_terminal_width()

                # Check for keypress
                key = get_key()
                if key:
                    if key in ("q", "Q"):
                        break
                    elif key == "?" and not show_help:
                        show_help = True
                        help_lines = render_help_overlay(term_width)
                        out.write(CLEAR + "\n".join(help_lines))
                        out.flush()
                        # Wait for any key to close help
                        while running:
                            if select.select([sys.stdin], [], [], 0.05)[0]:
                                byte = os.read(sys.stdin.fileno(), 1)
                                # Drain any remaining escape sequence bytes
                                while select.select([sys.stdin], [], [], 0.01)[0]:
                                    os.read(sys.stdin.fileno(), 1)
                                break
                        show_help = False
                        needs_full_redraw = True
                        continue
                    elif key in ("s", "S"):
                        script = find_tool_script("stats")
                        if os.path.exists(script):
                            run_tool(script, [session_id])
                            needs_full_redraw = True
                            cached_header = cached_log = None
                    elif key in ("d", "D"):
                        script = find_tool_script("manager")
                        if os.path.exists(script):
                            run_tool(script, ["show", session_id])
                            needs_full_redraw = True
                            cached_header = cached_log = None
                    elif key in ("l", "L"):
                        if r is not None:
                            show_log_viewer(path, term_width)
                            needs_full_redraw = True
                            cached_header = cached_log = None
                    elif key in ("e", "E"):
                        export_session(path, session_id)
                        needs_full_redraw = True
                        cached_header = cached_log = None
                    elif key in ("o", "O"):
                        script = find_tool_script("manager")
                        if os.path.exists(script):
                            project_name = Path(path).parent.name
                            run_tool(script, ["list", f"--project={project_name}"])
                            needs_full_redraw = True
                            cached_header = cached_log = None
                    elif key in ("w", "W"):
                        if r is not None:
                            show_efficiency_chart(r, term_width, transcript_path=path)
                            needs_full_redraw = True
                            cached_header = cached_log = None
                    elif key in ("c", "C"):
                        show_settings_panel(term_width)
                        needs_full_redraw = True
                        cached_header = cached_log = None
                        continue
                    elif key in ("a", "A"):
                        if r is not None:
                            cost_lines = render_cost_overlay(r, term_width)
                            out.write(CLEAR + "\n".join(cost_lines))
                            out.flush()
                            while running:
                                if select.select([sys.stdin], [], [], 0.05)[0]:
                                    os.read(sys.stdin.fileno(), 1)
                                    while select.select([sys.stdin], [], [], 0.01)[0]:
                                        os.read(sys.stdin.fileno(), 1)
                                    break
                            needs_full_redraw = True
                            continue
                    elif key in ("i", "I"):
                        status_lines = render_status_overlay(term_width)
                        out.write(CLEAR + "\n".join(status_lines))
                        out.flush()
                        while running:
                            if select.select([sys.stdin], [], [], 0.05)[0]:
                                os.read(sys.stdin.fileno(), 1)
                                while select.select([sys.stdin], [], [], 0.01)[0]:
                                    os.read(sys.stdin.fileno(), 1)
                                break
                        needs_full_redraw = True
                        continue

                # Re-parse transcript only when file changes
                try:
                    mtime = os.stat(path).st_mtime
                except FileNotFoundError:
                    # Session file gone — try auto-follow
                    new_path = find_latest_transcript()
                    if new_path and new_path != path:
                        path = new_path
                        session_id = Path(path).stem[:8]
                        cached_header = cached_log = None
                        needs_full_redraw = True
                    time.sleep(1)
                    continue

                if mtime != last_mtime or cached_header is None:
                    last_mtime = mtime
                    update_flash_until = now + 0.5  # pulse for 500ms
                    try:
                        r = parse_transcript(path)
                        idle_secs = 0  # fresh data = active
                        last_data_time = now
                        cached_header, cached_log = render_dashboard(r, idle_secs, True, term_width)
                        needs_full_redraw = True
                    except Exception as e:
                        if cached_header is None:
                            cached_header = [f"  {RED}Error: {e}{RESET}"]
                            cached_log = []

                # Live duration + idle status update every second
                elapsed = now - last_data_time if r else 0
                current_sec = int(now)
                if current_sec != last_duration_sec:
                    last_duration_sec = current_sec
                    if r:
                        just_updated = now < update_flash_until
                        idle_secs = now - last_data_time
                        cached_header, cached_log = render_dashboard(r, idle_secs, just_updated, term_width)
                        needs_full_redraw = True

                # Auto-follow: check for newer session in same project
                # directory after 10s idle, or any project after 5 min
                if elapsed > 10 and current_sec % 5 == 0:
                    project_dir = Path(path).parent
                    siblings = sorted(
                        project_dir.glob("*.jsonl"),
                        key=lambda f: f.stat().st_mtime, reverse=True)
                    newest = str(siblings[0]) if siblings else None
                    if newest and newest != path:
                        path = newest
                        session_id = Path(path).stem[:8]
                        last_mtime = 0
                        cached_header = cached_log = None
                        needs_full_redraw = True
                    elif elapsed > 300 and current_sec % 10 == 0:
                        new_path = find_latest_transcript()
                        if new_path and new_path != path:
                            new_mtime = os.stat(new_path).st_mtime
                            if new_mtime > last_mtime:
                                path = new_path
                                session_id = Path(path).stem[:8]
                                last_mtime = 0
                                cached_header = cached_log = None
                                needs_full_redraw = True

                # Matrix animates when Claude is working or transcript just changed
                idle = now - last_data_time
                is_active = bool(r and r.get("waiting_for_response")) or idle < 5
                term_h = shutil.get_terminal_size().lines

                if needs_full_redraw:
                    matrix_line = render_matrix_header(frame, term_width, active=is_active)
                    footer = render_footer(term_width)

                    # Layout: row 1 = matrix, rows 2..N = header, remaining = log, last 2 = footer
                    header_str = "\n".join(cached_header) if cached_header else ""
                    # Count visual rows (lines that wrap take 2+ rows)
                    header_visual_rows = _visual_rows(cached_header, term_width) if cached_header else 0
                    # 1 (matrix) + header visual rows + footer (2 rows: separator + keys)
                    fixed_rows = 1 + header_visual_rows + 2
                    log_space = max(0, term_h - fixed_rows)

                    # Clear screen and write: matrix + header
                    out.write(CLEAR + matrix_line + "\n" + header_str)
                    log_start_row = 2 + header_visual_rows  # 1-based, after matrix + header

                    # LOG title: render between header and log entries, only if room
                    if cached_log and log_space >= 3:
                        event_count = len(r.get("full_log", r.get("event_log", [])))
                        out.write(f"\033[{log_start_row};1H\n  {BOLD}LOG{RESET}  {DIM}({event_count} events){RESET}")
                        log_start_row += 2  # blank line + title
                        log_space -= 2

                    # Truncate log to fit available space (visual rows)
                    log_lines = cached_log if cached_log else []
                    if log_lines and log_space > 0:
                        fitted = []
                        used = 0
                        for line in reversed(log_lines):
                            rows = _visual_rows([line], term_width)
                            if used + rows > log_space:
                                break
                            fitted.append(line)
                            used += rows
                        log_lines = list(reversed(fitted))
                    elif log_space <= 0:
                        log_lines = []
                    log_str = "\n".join(log_lines)

                    # Clear log area and write log
                    for row in range(log_start_row, term_h - 1):
                        out.write(f"\033[{row};1H{ERASE_LINE}")
                    out.write(f"\033[{log_start_row};1H{log_str}")
                    # Pin footer to bottom
                    footer_row = term_h - 1  # separator line
                    out.write(f"\033[{footer_row};1H{footer}")
                    out.flush()
                    needs_full_redraw = False
                elif is_active:
                    # Animate matrix header at 100ms
                    matrix_line = render_matrix_header(frame, term_width, active=True)
                    out.write(f"\033[1;1H{ERASE_LINE}{matrix_line}")
                    out.flush()

                if is_active:
                    frame += 1
                    time.sleep(0.1)
                else:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                break
            except Exception as e:
                out.write(SHOW_CURSOR)
                out.flush()
                print(f"\n{RED}Error: {e}{RESET}")
                time.sleep(5)
                needs_full_redraw = True
    finally:
        out.write(SHOW_CURSOR + ALT_SCREEN_OFF)
        out.flush()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    print(f"\n{DIM}Monitor stopped.{RESET}")


if __name__ == "__main__":
    main()
