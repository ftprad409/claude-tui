#!/usr/bin/env python3
"""Claude Code Statusline entrypoint (orchestration only)."""

import json
import os
import sys

from statusline_core.api_clients import (
    fetch_api_status,
    fetch_usage,
    format_api_status,
)
from statusline_core.constants import GRAY, RESET
from statusline_core.git_info import get_git_branch, get_git_diff_stat
from statusline_core.render import (
    build_compact_line,
    build_line1_parts,
    build_line2_parts,
    build_line3_parts,
    build_progress_bar,
    build_sparkline,
    get_terminal_cols,
    truncate,
    visible_len,
)
from statusline_core.settings import get_setting, is_visible, load_widget
from statusline_core.transcript import (
    calculate_cache_ratio,
    calculate_compaction_prediction,
    calculate_context_metrics,
    calculate_cost_per_turn,
    calculate_efficiency,
    calculate_session_cost,
    format_cache_part,
    format_duration,
    format_tokens,
    get_context_limit,
    get_model_pricing,
    parse_input_data,
    parse_transcript,
)


def main():
    compact_mode = "--compact" in sys.argv
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        print("statusline: no data")
        return

    basic = parse_input_data(data)
    model = basic["model"]
    model_id = basic["model_id"]
    cwd = basic["cwd"]
    session_id = basic["session_id"]

    context_limit = get_context_limit(model_id)
    metrics = parse_transcript(basic["transcript_path"], context_limit=context_limit)
    ctx_used = metrics["context_tokens"]
    ctx_metrics = calculate_context_metrics(ctx_used, context_limit)
    ratio = ctx_metrics["ratio"]

    term_cols = get_terminal_cols()
    buffer = get_setting("custom", "buffer", default=30)
    term_cols_padded = term_cols - buffer

    if term_cols_padded <= 90:
        bar_length = 12
        spark_width = 8
    elif term_cols_padded <= 120:
        bar_length = 16
        spark_width = 12
    elif term_cols_padded <= 150:
        bar_length = 20
        spark_width = 16
    else:
        bar_length = 24
        spark_width = 20

    bar = build_progress_bar(
        ratio, length=bar_length, threshold=ctx_metrics["compact_ratio"], pct_label="C"
    )
    tokens_str = format_tokens(int(ctx_used))
    limit_str = format_tokens(context_limit)
    cost = calculate_session_cost(metrics, get_model_pricing(model_id))
    cost_str = f"${cost:.2f}" if cost >= 0.01 else "<$0.01"
    duration_str = format_duration(metrics["session_start"])

    branch_part = ""
    branch = get_git_branch()
    if branch:
        from statusline_core.render import format_git_branch

        branch_part = format_git_branch(branch, get_git_diff_stat())

    cache_pct, cache_color = calculate_cache_ratio(metrics)
    cache_part = format_cache_part(cache_pct, cache_color)
    cost_per_turn = calculate_cost_per_turn(cost, metrics["turn_count"])
    sparkline_part = build_sparkline(metrics["context_history"], width=spark_width)
    detailed_eta = term_cols_padded >= 140
    compact_prediction = calculate_compaction_prediction(
        ctx_used,
        context_limit,
        metrics["turns_since_compact"],
        metrics,
        ratio,
        detailed=detailed_eta,
    )
    efficiency_part = calculate_efficiency(metrics, ctx_used)

    usage = None
    if is_visible("line2", "usage") or is_visible("line3", "usage_weekly") or compact_mode:
        usage = fetch_usage(background=True)

    line1_parts = build_line1_parts(
        bar,
        tokens_str,
        limit_str,
        compact_prediction,
        model,
        sparkline_part,
        cost_str,
        duration_str,
        metrics,
        efficiency_part,
        session_id,
    )
    line2_parts = build_line2_parts(
        usage,
        cwd,
        branch_part,
        metrics,
        cache_part,
        cache_pct,
        cost_per_turn,
        "",
        usage_bar_length=bar_length,
    )
    if is_visible("line2", "api_status"):
        api_status_str = format_api_status(fetch_api_status(background=True))
        if api_status_str:
            line2_parts.append(api_status_str)
    line3_lines = build_line3_parts(usage, metrics, usage_bar_length=bar_length)

    if compact_mode:
        compact_line = build_compact_line(
            model, bar, tokens_str, limit_str, usage, usage_bar_length=bar_length
        )
        if compact_line:
            print(f" {compact_line}")
        return

    widget_name = get_setting("custom", "widget", default=None) or os.environ.get(
        "STATUSLINE_WIDGET", "matrix"
    )
    widget_fn = load_widget(os.path.dirname(os.path.abspath(__file__)), widget_name)
    sep = f" {GRAY}⋮{RESET} "
    sep_vis = 3

    def fit_parts(parts, max_width):
        fitted = []
        used = 1  # leading space in output
        for part in parts:
            part_width = visible_len(part)
            extra = part_width if not fitted else sep_vis + part_width
            if used + extra > max_width:
                if not fitted:
                    fitted.append(part)
                break
            fitted.append(part)
            used += extra
        return fitted

    line1_fitted = fit_parts(line1_parts, term_cols_padded)
    line2_fitted = fit_parts(line2_parts, term_cols_padded)

    line1_str = f" {sep.join(line1_fitted)}" if line1_fitted else ""
    line2_str = f" {sep.join(line2_fitted)}" if line2_fitted else ""

    if widget_fn:
        wdg = widget_fn(frame=metrics["tool_calls"], ratio=ratio)
        print(truncate(f" {wdg[0]}{line1_str}", term_cols_padded))
        print(truncate(f" {wdg[1]}{line2_str}", term_cols_padded))
        first_extra = line3_lines[0] if line3_lines else ""
        if first_extra:
            print(truncate(f" {wdg[2]} {first_extra}", term_cols_padded))
        for extra_line in line3_lines[1:]:
            print(truncate(f"        {extra_line}", term_cols_padded))
    else:
        if line1_str:
            print(truncate(line1_str, term_cols_padded))
        if line2_str:
            print(truncate(line2_str, term_cols_padded))
        for i, extra_line in enumerate(line3_lines):
            print(truncate(extra_line if i == 0 else f"        {extra_line}", term_cols_padded))


if __name__ == "__main__":
    main()
