"""Output rendering — writes formatted statusline to stdout."""

from .constants import GRAY, RESET
from .layout import fit_parts
from .settings import get_setting, load_widget

import os

from claude_tui_components.utils import visible_len, truncate


def render_compact(compact_line):
    """Print compact single-line mode."""
    if compact_line:
        print(f" {compact_line}")


def render_full(line1_parts, line2_parts, line3_lines, ratio, metrics, term_cols_padded, script_dir):
    """Print full/custom multi-line mode with optional widget."""
    widget_name = get_setting("custom", "widget", default=None) or os.environ.get(
        "STATUSLINE_WIDGET", "matrix"
    )
    widget_fn = load_widget(script_dir, widget_name)
    sep = f" {GRAY}⋮{RESET} "

    line1_fitted = fit_parts(line1_parts, term_cols_padded)
    line2_fitted = fit_parts(line2_parts, term_cols_padded)

    line1_str = f" {sep.join(line1_fitted)}" if line1_fitted else ""
    line2_str = f" {sep.join(line2_fitted)}" if line2_fitted else ""

    if widget_fn:
        _render_with_widget(widget_fn, line1_str, line2_str, line3_lines, ratio, metrics, term_cols_padded)
    else:
        _render_plain(line1_str, line2_str, line3_lines, term_cols_padded)


def _render_with_widget(widget_fn, line1_str, line2_str, line3_lines, ratio, metrics, max_width):
    """Print lines with widget column prefix."""
    wdg = widget_fn(frame=metrics["tool_calls"], ratio=ratio)
    print(truncate(f" {wdg[0]}{line1_str}", max_width))
    print(truncate(f" {wdg[1]}{line2_str}", max_width))
    first_extra = line3_lines[0] if line3_lines else ""
    if first_extra:
        print(truncate(f" {wdg[2]} {first_extra}", max_width))
    for extra_line in line3_lines[1:]:
        print(truncate(f"        {extra_line}", max_width))


def _render_plain(line1_str, line2_str, line3_lines, max_width):
    """Print lines without widget column."""
    if line1_str:
        print(truncate(line1_str, max_width))
    if line2_str:
        print(truncate(line2_str, max_width))
    for i, extra_line in enumerate(line3_lines):
        print(truncate(extra_line if i == 0 else f"        {extra_line}", max_width))
