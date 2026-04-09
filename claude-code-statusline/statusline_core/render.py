"""Rendering and line composition helpers."""

import os
import re
import shutil
import subprocess

from .api_clients import format_usage_session, format_usage_weekly
from .constants import BOLD, CYAN, GRAY, GREEN, MAGENTA, ORANGE, RED, RESET, WHITE, YELLOW
from .debug import debug_log
from .settings import is_visible

from claude_tui_components.utils import visible_len, truncate, get_terminal_cols, visual_rows
from claude_tui_components.widgets import build_progress_bar, build_sparkline
from claude_tui_components.lines import build_bar_line, format_token_suffix


def format_git_branch(branch, diff_stat):
    if not branch:
        return ""
    part = f"{GREEN}⎇ {branch}{RESET}"
    return f"{part} {diff_stat}" if diff_stat else part


def format_tool_trail(recent_tools):
    if not recent_tools:
        return []
    items = []
    for t in recent_tools[-6:]:
        p = t.split()
        items.append(
            f"{GRAY}{p[0].lower()}{RESET} {GREEN}{p[-1]}{RESET}" if len(p) >= 2 else f"{GRAY}{p[0].lower()}{RESET}"
        )
    return items


def format_file_edits(file_edits):
    if not file_edits:
        return []
    top = sorted(file_edits.items(), key=lambda x: -x[1])[:3]
    return [f"{YELLOW}{n}{RESET}{GRAY}×{c}{RESET}" for n, c in top]


def _chip(label, value, color=GRAY):
    """Compact badge-like token for dense telemetry."""
    return f"{color}{label}{RESET} {value}"


def _turns_left_from_prediction(compact_prediction):
    m = re.search(r"ETA\s+(\d+(?:\.\d+)?)([kM]?)", compact_prediction or "")
    if not m:
        return None
    value = float(m.group(1))
    suffix = m.group(2)
    if suffix == "k":
        value *= 1_000
    elif suffix == "M":
        value *= 1_000_000
    return int(value)


def wrap_line_parts(items, file_edit_parts, max_width):
    if not items and not file_edit_parts:
        return []
    lines = []
    arrow = f" {GRAY}→{RESET} "
    arrow_vis = 3
    cur_line, cur_width = [], 1
    for item in items:
        item_width = visible_len(item)
        joiner = arrow_vis if cur_line else 0
        if cur_line and cur_width + joiner + item_width > max_width:
            lines.append(f" {arrow.join(cur_line)}")
            cur_line, cur_width = [item], 1 + item_width
        else:
            cur_line.append(item)
            cur_width += joiner + item_width
    if cur_line:
        tail = arrow.join(cur_line)
        if file_edit_parts:
            edit_str = " ".join(file_edit_parts)
            if cur_width + 1 + visible_len(edit_str) <= max_width:
                tail += f" {GRAY}⋮{RESET}{edit_str}"
            else:
                lines.append(f" {tail}")
                tail = f" {edit_str}"
        lines.append(f" {tail}")
    elif file_edit_parts:
        lines.append(f" {' '.join(file_edit_parts)}")
    return lines


def calculate_terminal_width(buffer=30, widget_offset=10):
    return get_terminal_cols() - buffer - widget_offset


def build_line1_parts(bar, tokens_str, limit_str, compact_prediction, model, sparkline_part, cost_str, duration_str, metrics, efficiency_part, session_id):
    parts = []
    dim = GRAY
    token_suffix = format_token_suffix(tokens_str, limit_str)
    if is_visible("line1", "context_bar"):
        ctx = f"{bar}"
        if is_visible("line1", "token_count"):
            ctx += f" {token_suffix}"
        if compact_prediction and is_visible("line1", "compact_prediction"):
            ctx += f" {GRAY}⋮{RESET} {compact_prediction}"
        parts.append(ctx)
    elif is_visible("line1", "token_count"):
        ctx = token_suffix
        if compact_prediction and is_visible("line1", "compact_prediction"):
            ctx += f" {GRAY}⋮{RESET} {compact_prediction}"
        parts.append(ctx)
    elif compact_prediction and is_visible("line1", "compact_prediction"):
        parts.append(compact_prediction)
    if is_visible("line1", "model"):
        parts.append(f"{BOLD}{MAGENTA}{model}{RESET}")
    if sparkline_part and is_visible("line1", "sparkline"):
        parts.append(sparkline_part)
    if is_visible("line1", "cost"):
        parts.append(f"{YELLOW}{cost_str}{RESET}")
    if is_visible("line1", "duration"):
        parts.append(f"{WHITE}⏱ {duration_str}{RESET}")
    if is_visible("line1", "compact_count"):
        parts.append(_chip("CMP", f"{CYAN}{metrics['compact_count']}{RESET}{dim}x{RESET}"))
    if efficiency_part:
        parts.append(efficiency_part)
    if is_visible("line1", "session_id"):
        parts.append(f"{dim}#{RESET}{GRAY}{session_id}{RESET}")
    turns_left = _turns_left_from_prediction(compact_prediction)
    if turns_left is not None and turns_left <= 12:
        if turns_left <= 5:
            parts.append(f"{RED}⚠ COMPACT SOON{RESET}")
        else:
            parts.append(f"{ORANGE}△ compact soon{RESET}")
    return parts


def build_line2_parts(
    usage,
    cwd,
    branch_part,
    metrics,
    cache_part,
    cache_pct,
    cost_per_turn,
    api_status_str,
    usage_bar_length=20,
):
    parts = []
    dim = GRAY
    if is_visible("line2", "usage"):
        usage_str = format_usage_session(usage, length=usage_bar_length)
        if usage_str:
            parts.append(usage_str)
    if is_visible("line2", "cwd"):
        parts.append(f"{GREEN}{cwd}{RESET}")
    if branch_part and is_visible("line2", "git_branch"):
        parts.append(branch_part)
    if is_visible("line2", "turns"):
        turns = metrics["turn_count"]
        turns_color = GREEN if turns <= 20 else (YELLOW if turns <= 60 else ORANGE)
        parts.append(_chip("TRN", f"{turns_color}{turns}{RESET}", turns_color))
    if is_visible("line2", "files"):
        parts.append(_chip("FIL", f"{CYAN}{len(metrics['files_touched'])}{RESET}"))
    if is_visible("line2", "errors"):
        if metrics["tool_errors"] > 0:
            err_color = RED if metrics["tool_errors"] > 5 else ORANGE
            parts.append(_chip("ERR", f"{err_color}{metrics['tool_errors']}{RESET}", err_color))
        else:
            parts.append(_chip("ERR", f"{GREEN}0{RESET}", GREEN))
    if is_visible("line2", "cache"):
        cache_token = cache_part.split(" ")[0]
        # Lower cache hit rate implies more paid input usage.
        cache_color = GREEN if cache_pct >= 85 else (YELLOW if cache_pct >= 60 else ORANGE)
        parts.append(_chip("CAC", f"{cache_color}{cache_token}{RESET}", cache_color))
    if metrics["thinking_count"] > 0 and is_visible("line2", "thinking"):
        thk = metrics["thinking_count"]
        thk_color = GREEN if thk <= 2 else (YELLOW if thk <= 6 else ORANGE)
        parts.append(_chip("THK", f"{thk_color}{thk}{RESET}", thk_color))
    if cost_per_turn and is_visible("line2", "cost_per_turn"):
        parts.append(cost_per_turn)
    if metrics["subagent_count"] > 0 and is_visible("line2", "agents"):
        parts.append(_chip("AGT", f"{CYAN}{metrics['subagent_count']}{RESET}"))
    if api_status_str and is_visible("line2", "api_status"):
        parts.append(api_status_str)
    return parts


def build_line3_parts(usage, metrics, usage_bar_length=20):
    lines = []
    if is_visible("line3", "usage_weekly"):
        weekly_str = format_usage_weekly(usage, length=usage_bar_length)
        if weekly_str:
            lines.append(weekly_str)
    wrapped = wrap_line_parts(
        format_tool_trail(metrics.get("recent_tools")),
        format_file_edits(metrics.get("current_turn_file_edits")),
        calculate_terminal_width(),
    )
    lines.extend(wrapped)
    return lines


def build_compact_line(model, bar, tokens_str, limit_str, usage, usage_bar_length=20):
    parts = []
    if is_visible("line1", "model"):
        parts.append(f"{BOLD}{MAGENTA}{model}{RESET}")
    if is_visible("line1", "context_bar"):
        ctx = f"{bar}"
        if is_visible("line1", "token_count"):
            ctx += f" {format_token_suffix(tokens_str, limit_str)}"
        parts.append(ctx)
    if usage:
        session = format_usage_session(usage, length=usage_bar_length)
        weekly = format_usage_weekly(usage, length=usage_bar_length)
        if session:
            parts.append(session)
        if weekly:
            parts.append(weekly)
    sep = f" {GRAY}⋮{RESET} "
    return sep.join(parts) if parts else ""
