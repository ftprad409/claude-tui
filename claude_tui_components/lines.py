"""Line-level widgets — compose progress bars with labels into full display lines."""

from .colors import CYAN, YELLOW, GRAY, DIM, RESET
from .utils import format_tokens
from .widgets import build_progress_bar


def build_bar_line(ratio, length, pct_label="", icon="", suffix="", threshold=None):
    """Generic progress bar line: bar + optional icon + optional suffix."""
    bar = build_progress_bar(ratio, length, threshold=threshold, pct_label=pct_label)
    parts = [bar]
    if icon:
        parts.append(f"{YELLOW}{icon}{RESET}")
    if suffix:
        parts.append(suffix)
    return " ".join(parts)


def format_token_suffix(ctx_used, ctx_limit):
    """Format token count as '⚡used/limit' with colors.

    Accepts raw numbers or pre-formatted strings.
    """
    tokens = format_tokens(int(ctx_used)) if isinstance(ctx_used, (int, float)) else ctx_used
    limit = format_tokens(int(ctx_limit)) if isinstance(ctx_limit, (int, float)) else ctx_limit
    return f"{YELLOW}⚡{RESET}{CYAN}{tokens}{RESET}{DIM}/{RESET}{GRAY}{limit}{RESET}"


def build_context_line(ratio, length, threshold, ctx_used, ctx_limit):
    """Context progress bar with token count suffix."""
    return build_bar_line(ratio, length, pct_label="C", suffix=format_token_suffix(ctx_used, ctx_limit), threshold=threshold)
