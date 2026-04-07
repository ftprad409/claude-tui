"""Rendering and line composition helpers."""

import os
import re
import shutil
import subprocess

from .api_clients import format_usage_session, format_usage_weekly
from .constants import BOLD, CYAN, GRAY, GREEN, MAGENTA, ORANGE, RED, RESET, WHITE, YELLOW
from .debug import debug_log
from .settings import get_setting, is_visible

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible_len(s):
    return len(_ANSI_RE.sub("", s))


def truncate(s, max_cols):
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


def get_terminal_cols():
    import fcntl, struct, termios

    try:
        pid = os.getpid()
        for _ in range(10):
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "ppid=,tty="], capture_output=True, text=True, timeout=1
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
        debug_log("get_terminal_cols fallback to shutil")
        pass
    return shutil.get_terminal_size().columns


def build_sparkline(values, width=20):
    if not values:
        return ""
    none_indices = [i for i, v in enumerate(values) if v is None]
    keep_set = set(none_indices[-3:])
    values = [0 if (v is None and i not in keep_set) else v for i, v in enumerate(values)]
    mode = get_setting("sparkline", "mode", default="tail")
    if mode == "merge":
        merge_size = get_setting("sparkline", "merge_size", default=2)
        merged = []
        for i in range(0, len(values), merge_size):
            bucket = values[i : i + merge_size]
            merged.append(None if None in bucket else sum(v for v in bucket if v is not None))
        values = merged[-width:] if len(merged) > width else merged
    elif len(values) > width:
        values = values[-width:]
    blocks = "▁▂▃▄▅▆▇█"
    peak = max((v for v in values if v is not None), default=1) or 1
    chars = []
    total = max(len(values), 1)
    for idx_pos, v in enumerate(values):
        recency = idx_pos / max(total - 1, 1)
        if v is None:
            # Compaction boundary marker: distinct but less loud than full red.
            # Fade older markers to reduce noise.
            marker_color = "\033[38;2;185;155;198m" if recency < 0.5 else "\033[38;2;205;170;210m"
            chars.append(f"{marker_color}↓{RESET}")
            continue
        r = v / peak
        idx = max(0, min(int(r * (len(blocks) - 1)), len(blocks) - 1))
        # Smoother, modern palette tuned for legibility in dark terminals.
        if r < 0.20:
            base = (145, 215, 165)
        elif r < 0.40:
            base = (130, 210, 205)
        elif r < 0.60:
            base = (190, 214, 155)
        elif r < 0.80:
            base = (232, 196, 140)
        else:
            base = (240, 160, 150)
        # Recency fade: older points are dimmer, latest points are brighter.
        fade = 0.65 + (0.35 * recency)
        r_ch = min(255, int(base[0] * fade))
        g_ch = min(255, int(base[1] * fade))
        b_ch = min(255, int(base[2] * fade))
        color = f"\033[38;2;{r_ch};{g_ch};{b_ch}m"
        chars.append(f"{color}{blocks[idx]}{RESET}")
    return "".join(chars)


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


def build_progress_bar(ratio, length=20, compact_ratio=None, pct_label=""):
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

    bar_parts = []
    for i in range(length):
        pos = i / max(length - 1, 1)
        if i < full_cells:
            bar_parts.append(f"{_lerp_rgb(stops, pos)}{full_char}{RESET}")
            continue
        if i == full_cells and remainder > 0:
            partial_idx = max(0, min(int(remainder * len(partials)) - 1, len(partials) - 1))
            bar_parts.append(f"{_lerp_rgb(stops, pos)}{partials[partial_idx]}{RESET}")
            continue
        bar_parts.append(f"{empty_color}{empty_char}{RESET}")

    # Subtle head marker on the active frontier for easier visual tracking.
    if 0 < precise_fill < length and remainder == 0:
        head_idx = min(full_cells, length - 1)
        bar_parts[head_idx] = f"{head_color}▌{RESET}"

    # Compact ceiling tick marker (when available) to show compaction threshold.
    if compact_ratio and 0 < compact_ratio < 1:
        tick_idx = min(max(int(compact_ratio * length), 0), length - 1)
        if tick_idx >= full_cells:
            bar_parts[tick_idx] = f"\033[38;2;140;145;170m┆{RESET}"

    bar = "".join(bar_parts)
    bar = f"\033[38;2;90;95;120m▏{RESET}{bar}\033[38;2;90;95;120m▕{RESET}"
    fill_of_ceiling = ratio / compact_ratio if compact_ratio and compact_ratio > 0 else ratio
    if fill_of_ceiling < 0.60:
        pct_color = GREEN
    elif fill_of_ceiling < 0.85:
        pct_color = YELLOW
    elif fill_of_ceiling < 0.95:
        pct_color = ORANGE
    else:
        pct_color = RED
    pct_value = int(ratio * 100)
    if pct_label:
        pct_text = f"{pct_label} {pct_value:>2}%"
    else:
        pct_text = f"{pct_value:>3}%"
    return f"{bar} {pct_color}{pct_text}{RESET}"


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
    if is_visible("line1", "context_bar"):
        ctx = f"{bar}"
        if is_visible("line1", "token_count"):
            ctx += f" {CYAN}{tokens_str}{RESET}{dim}/{RESET}{GRAY}{limit_str}{RESET}"
        if compact_prediction and is_visible("line1", "compact_prediction"):
            ctx += f" {dim}⋮{RESET} {compact_prediction}"
        parts.append(ctx)
    elif is_visible("line1", "token_count"):
        ctx = f"{CYAN}{tokens_str}{RESET}{dim}/{RESET}{GRAY}{limit_str}{RESET}"
        if compact_prediction and is_visible("line1", "compact_prediction"):
            ctx += f" {dim}⋮{RESET} {compact_prediction}"
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
        parts.append(f"{bar}")
        if is_visible("line1", "token_count"):
            parts.append(f"{CYAN}{tokens_str}{RESET}{GRAY}/{RESET}{GRAY}{limit_str}{RESET}")
    if usage:
        session = format_usage_session(usage, length=usage_bar_length)
        weekly = format_usage_weekly(usage, length=usage_bar_length)
        if session:
            parts.append(session)
        if weekly:
            parts.append(weekly)
    sep = f" {GRAY}⋮{RESET} "
    return sep.join(parts) if parts else ""
