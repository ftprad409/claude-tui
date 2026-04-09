"""Line composition — builds statusline line parts from DisplayState."""

from .api_clients import format_usage_session, format_usage_weekly
from .constants import BOLD, CYAN, GRAY, GREEN, MAGENTA, ORANGE, RED, RESET, WHITE, YELLOW
from .formatters import chip, format_file_edits, format_tool_trail, threshold_color, turns_left_from_prediction, wrap_line_parts
from .settings import is_visible

from claude_tui_components.utils import get_terminal_cols
from claude_tui_components.widgets import build_progress_bar, build_sparkline  # re-exported for tests
from claude_tui_components.lines import format_token_suffix


def calculate_terminal_width(buffer=30, widget_offset=10):
    return get_terminal_cols() - buffer - widget_offset


def _build_context_block(ds):
    """Build the context bar + token count + prediction block for line 1."""
    token_suffix = format_token_suffix(ds.tokens_str, ds.limit_str)
    prediction_suffix = ""
    if ds.compact_prediction and is_visible("line1", "compact_prediction"):
        prediction_suffix = f" {GRAY}⋮{RESET} {ds.compact_prediction}"

    if is_visible("line1", "context_bar"):
        ctx = ds.bar
        if is_visible("line1", "token_count"):
            ctx += f" {token_suffix}"
        return ctx + prediction_suffix
    if is_visible("line1", "token_count"):
        return token_suffix + prediction_suffix
    if prediction_suffix:
        return ds.compact_prediction
    return None


def _build_compact_alert(ds):
    """Build compaction urgency alert if turns left is low."""
    turns_left = turns_left_from_prediction(ds.compact_prediction)
    if turns_left is None or turns_left > 12:
        return None
    if turns_left <= 5:
        return f"{RED}⚠ COMPACT SOON{RESET}"
    return f"{ORANGE}△ compact soon{RESET}"


def build_line1_parts(ds):
    """Build line 1 parts from DisplayState."""
    parts = []
    ctx_block = _build_context_block(ds)
    if ctx_block:
        parts.append(ctx_block)
    if is_visible("line1", "model"):
        parts.append(f"{BOLD}{MAGENTA}{ds.model}{RESET}")
    if ds.sparkline_part and is_visible("line1", "sparkline"):
        parts.append(ds.sparkline_part)
    if is_visible("line1", "cost"):
        parts.append(f"{YELLOW}{ds.cost_str}{RESET}")
    if is_visible("line1", "duration"):
        parts.append(f"{WHITE}⏱ {ds.duration_str}{RESET}")
    if is_visible("line1", "compact_count"):
        parts.append(chip("CMP", f"{CYAN}{ds.metrics['compact_count']}{RESET}{GRAY}x{RESET}"))
    if ds.efficiency_part:
        parts.append(ds.efficiency_part)
    if is_visible("line1", "session_id"):
        parts.append(f"{GRAY}#{RESET}{GRAY}{ds.session_id}{RESET}")
    alert = _build_compact_alert(ds)
    if alert:
        parts.append(alert)
    return parts


def _build_error_chip(ds):
    """Build error chip with severity color."""
    errors = ds.metrics["tool_errors"]
    if errors > 0:
        color = RED if errors > 5 else ORANGE
        return chip("ERR", f"{color}{errors}{RESET}", color)
    return chip("ERR", f"{GREEN}0{RESET}", GREEN)


def build_line2_parts(ds):
    """Build line 2 parts from DisplayState."""
    parts = []
    if is_visible("line2", "usage"):
        usage_str = format_usage_session(ds.usage, length=ds.bar_length)
        if usage_str:
            parts.append(usage_str)
    if is_visible("line2", "cwd"):
        parts.append(f"{GREEN}{ds.cwd}{RESET}")
    if ds.branch_part and is_visible("line2", "git_branch"):
        parts.append(ds.branch_part)
    if is_visible("line2", "turns"):
        turns = ds.metrics["turn_count"]
        color = threshold_color(turns, [(20, GREEN), (60, YELLOW), (0, ORANGE)])
        parts.append(chip("TRN", f"{color}{turns}{RESET}", color))
    if is_visible("line2", "files"):
        parts.append(chip("FIL", f"{CYAN}{len(ds.metrics['files_touched'])}{RESET}"))
    if is_visible("line2", "errors"):
        parts.append(_build_error_chip(ds))
    if is_visible("line2", "cache"):
        cache_token = ds.cache_part.split(" ")[0]
        color = threshold_color(-ds.cache_pct, [(-85, GREEN), (-60, YELLOW), (0, ORANGE)])
        parts.append(chip("CAC", f"{color}{cache_token}{RESET}", color))
    if ds.metrics["thinking_count"] > 0 and is_visible("line2", "thinking"):
        thk = ds.metrics["thinking_count"]
        color = threshold_color(thk, [(2, GREEN), (6, YELLOW), (0, ORANGE)])
        parts.append(chip("THK", f"{color}{thk}{RESET}", color))
    if ds.cost_per_turn and is_visible("line2", "cost_per_turn"):
        parts.append(ds.cost_per_turn)
    if ds.metrics["subagent_count"] > 0 and is_visible("line2", "agents"):
        parts.append(chip("AGT", f"{CYAN}{ds.metrics['subagent_count']}{RESET}"))
    return parts


def build_line3_parts(ds):
    """Build line 3 parts from DisplayState."""
    lines = []
    if is_visible("line3", "usage_weekly"):
        weekly_str = format_usage_weekly(ds.usage, length=ds.bar_length)
        if weekly_str:
            lines.append(weekly_str)
    wrapped = wrap_line_parts(
        format_tool_trail(ds.metrics.get("recent_tools")),
        format_file_edits(ds.metrics.get("current_turn_file_edits")),
        calculate_terminal_width(),
    )
    lines.extend(wrapped)
    return lines


def build_compact_line(ds):
    """Build compact single-line from DisplayState."""
    parts = []
    if is_visible("line1", "model"):
        parts.append(f"{BOLD}{MAGENTA}{ds.model}{RESET}")
    if is_visible("line1", "context_bar"):
        ctx = f"{ds.bar}"
        if is_visible("line1", "token_count"):
            ctx += f" {format_token_suffix(ds.tokens_str, ds.limit_str)}"
        parts.append(ctx)
    if ds.usage:
        session = format_usage_session(ds.usage, length=ds.bar_length)
        weekly = format_usage_weekly(ds.usage, length=ds.bar_length)
        if session:
            parts.append(session)
        if weekly:
            parts.append(weekly)
    sep = f" {GRAY}⋮{RESET} "
    return sep.join(parts) if parts else ""
