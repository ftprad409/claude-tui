"""Display formatting utilities — chips, tool trails, file edits, line wrapping."""

import re

from .constants import CYAN, GRAY, GREEN, ORANGE, RED, RESET, YELLOW

from claude_tui_components.utils import visible_len


def threshold_color(value, thresholds):
    """Pick color by threshold. thresholds is [(limit, color), ...] checked in order, with a fallback last."""
    for limit, color in thresholds[:-1]:
        if value <= limit:
            return color
    return thresholds[-1][1]


def chip(label, value, color=GRAY):
    """Compact badge-like token for dense telemetry."""
    return f"{color}{label}{RESET} {value}"


def turns_left_from_prediction(compact_prediction):
    """Extract numeric turns-left from a prediction string like 'ETA 24k'."""
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


def format_tool_trail(recent_tools):
    """Format recent tool calls as compact trail items."""
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
    """Format top file edits as compact badges."""
    if not file_edits:
        return []
    top = sorted(file_edits.items(), key=lambda x: -x[1])[:3]
    return [f"{YELLOW}{n}{RESET}{GRAY}×{c}{RESET}" for n, c in top]


def _wrap_items(items, max_width):
    """Wrap items into lines joined by arrows, respecting max_width."""
    arrow = f" {GRAY}→{RESET} "
    arrow_vis = 3
    lines = []
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
    return lines, cur_line, cur_width


def wrap_line_parts(items, file_edit_parts, max_width):
    """Wrap tool trail items and file edits into terminal-width lines."""
    if not items and not file_edit_parts:
        return []

    lines, cur_line, cur_width = _wrap_items(items, max_width)
    arrow = f" {GRAY}→{RESET} "
    edit_str = " ".join(file_edit_parts) if file_edit_parts else ""

    if cur_line:
        tail = arrow.join(cur_line)
        if edit_str and cur_width + 1 + visible_len(edit_str) <= max_width:
            tail += f" {GRAY}⋮{RESET}{edit_str}"
        elif edit_str:
            lines.append(f" {tail}")
            tail = f" {edit_str}"
        lines.append(f" {tail}")
    elif edit_str:
        lines.append(f" {edit_str}")

    return lines
