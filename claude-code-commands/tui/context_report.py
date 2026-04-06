#!/usr/bin/env python3
"""Context window analysis — growth curve, compaction timeline, predictions."""

import sys
import os
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import (
    parse_transcript,
    format_duration,
    format_tokens,
    get_transcript_path,
    get_context_limit,
    DEFAULT_CONTEXT_LIMIT,
)


def prepare_chart_data(history: list, width: int) -> tuple[list, set]:
    """Convert history to points and track compaction indices."""
    points: list = []
    compact_indices: set = set()
    for i, v in enumerate(history):
        if v is None:
            compact_indices.add(len(points))
            points.append(0)
        else:
            points.append(v)
    if not points:
        return [], set()

    if len(points) <= width:
        return points, compact_indices

    step = len(points) / width
    sampled: list = []
    compact_sampled: set = set()
    for i in range(width):
        start = int(i * step)
        end = int((i + 1) * step)
        chunk = points[start:end]
        sampled.append(max(chunk))
        for j in range(start, end):
            if j in compact_indices:
                compact_sampled.add(i)
    return sampled, compact_sampled


def draw_chart_row(
    row: int, height: int, scale: int, points: list, compact_indices: set
) -> str:
    """Draw a single row of the chart."""
    threshold = row / height * scale
    if row == height:
        label = f"{format_tokens(int(scale)):>5} ┤"
    elif row == height // 2:
        label = f"{format_tokens(int(scale // 2)):>5} ┤"
    elif row == 0:
        label = "    0 ┼"
    else:
        label = "      │"

    chars: list = []
    for i, v in enumerate(points):
        if i in compact_indices and row == 0:
            chars.append("↓")
        elif v >= threshold:
            chars.append("█")
        else:
            chars.append(" ")
    return label + "".join(chars)


def draw_chart(
    history: list,
    width: int = 50,
    height: int = 10,
    context_limit: int = DEFAULT_CONTEXT_LIMIT,
) -> str:
    """Draw an ASCII chart of context usage over time."""
    points, compact_indices = prepare_chart_data(history, width)
    if not points:
        return ""

    scale = context_limit
    lines: list = []
    for row in range(height, -1, -1):
        lines.append(draw_chart_row(row, height, scale, points, compact_indices))

    axis = "      └" + "─" * len(points)
    lines.append(axis)
    return "\n".join(lines)


def calculate_growth_rates(history: list) -> tuple[float, float, float]:
    """Calculate overall and recent growth rates."""
    overall_growth = 0.0
    last5_growth = 0.0
    last10_growth = 0.0

    if len(history) >= 2:
        overall_growth = (history[-1] - history[0]) / (len(history) - 1)
    if len(history) >= 6:
        h5 = history[-5:]
        last5_growth = (h5[-1] - h5[0]) / (len(h5) - 1)
    if len(history) >= 11:
        h10 = history[-10:]
        last10_growth = (h10[-1] - h10[0]) / (len(h10) - 1)

    return overall_growth, last5_growth, last10_growth


def predict_turns(growth: float, remaining: int) -> str:
    """Predict turns until compaction."""
    if growth > 0:
        return str(int(remaining / growth))
    return "∞"


def calc_current_state(r: dict, ctx_limit: int) -> dict[str, Any]:
    """Calculate current state data."""
    duration = format_duration(r["start_time"], r["end_time"])
    ratio = r["last_context"] / ctx_limit * 100
    remaining = ctx_limit - r["last_context"]

    return {
        "last_context": r["last_context"],
        "ctx_limit": ctx_limit,
        "ratio": ratio,
        "remaining": remaining,
        "turns": r["turns"],
        "compact_count": r["compact_count"],
        "duration": duration,
        "turns_since_compact": r["turns_since_compact"],
    }


def format_current_state(data: dict[str, Any]) -> str:
    """Format current state section."""
    return f"""
### Current State

  Metric              │ Value
  ────────────────────┼──────────────────────────
  Context used        │ {format_tokens(data["last_context"])} / {format_tokens(data["ctx_limit"])} ({data["ratio"]:.1f}%)
  Remaining capacity  │ {format_tokens(data["remaining"])} ({100 - data["ratio"]:.1f}%)
  Turns in session    │ {data["turns"]}
  Compactions         │ {data["compact_count"]}
  Duration            │ {data["duration"]}
  Turns since compact │ {data["turns_since_compact"]}
"""


def calc_compaction_timeline(r: dict) -> Optional[dict[str, Any]]:
    """Calculate compaction timeline data."""
    if not r["compact_events"]:
        return None

    events: list = []
    for i, evt in enumerate(r["compact_events"], 1):
        events.append(
            {
                "num": i,
                "turn": evt["turn"],
                "context_before": evt["context_before"],
                "turns_since_last": evt["turns_since_last"],
            }
        )

    avg_turns = sum(e["turns_since_last"] for e in events) / len(events)

    return {
        "events": events,
        "avg_turns": avg_turns,
    }


def format_compaction_timeline(data: Optional[dict[str, Any]]) -> str:
    """Format compaction timeline section."""
    if data is None:
        return ""

    lines: list = [
        "### Compaction Timeline\n",
        "  #  │ Turn │ Context Before │ Turns Between",
        "  ───┼──────┼───────────────┼──────────────",
    ]
    for evt in data["events"]:
        lines.append(
            f"  {evt['num']:>2} │ {evt['turn']:>4} │ {format_tokens(evt['context_before']):>13} │ {evt['turns_since_last']:>13}"
        )

    lines.append(f"\n  Average turns between compactions: {data['avg_turns']:.0f}")
    return "\n".join(lines) + "\n"


def calc_per_turn_breakdown(
    r: dict, ctx_limit: int, overall_growth: float
) -> Optional[dict[str, Any]]:
    """Calculate per-turn breakdown data."""
    responses = r["per_response"]
    if not responses:
        return None

    prev_ctx = responses[-16]["ctx"] if len(responses) >= 16 else 0
    avg_delta = overall_growth if overall_growth > 0 else 1

    turns: list = []
    for resp in responses[-15:]:
        delta = resp["ctx"] - prev_ctx
        pct = resp["ctx"] / ctx_limit * 100

        if delta > avg_delta * 3 and avg_delta > 0:
            note = "large growth"
        elif delta < 0:
            note = "after compact"
        else:
            note = ""

        turns.append(
            {
                "turn": resp["turn"],
                "ctx": resp["ctx"],
                "delta": delta,
                "pct": pct,
                "note": note,
            }
        )
        prev_ctx = resp["ctx"]

    return {"turns": turns}


def format_per_turn_breakdown(data: Optional[dict[str, Any]]) -> str:
    """Format per-turn breakdown section."""
    if data is None:
        return ""

    lines: list = [
        """
### Per-Turn Breakdown (Last 15 Responses)

  Turn │ Context   │ Delta     │ % Used │ Note
  ─────┼───────────┼───────────┼────────┼──────────"""
    ]

    for turn in data["turns"]:
        lines.append(
            f"  {turn['turn']:>4} │ {format_tokens(turn['ctx']):>9} │ {'+' if turn['delta'] >= 0 else ''}{format_tokens(int(turn['delta'])):>8} │ {turn['pct']:>5.1f}% │ {turn['note']}"
        )

    return "\n".join(lines)


def calc_recommendations(
    r: dict, ratio: float, last5_growth: float, overall_growth: float
) -> dict[str, list]:
    """Calculate recommendations data."""
    messages: list = []

    if ratio > 80:
        messages.append(
            "⚠ Context usage is high. Consider compacting soon with /compact."
        )
    elif ratio > 60:
        messages.append("ℹ Context at moderate usage. Monitor growth rate.")
    else:
        messages.append("✓ Context usage is healthy. Plenty of room remaining.")

    if r["compact_count"] >= 3:
        messages.append(
            "⚠ Multiple compactions detected. Consider starting a fresh session."
        )

    if last5_growth > overall_growth * 2 and overall_growth > 0:
        messages.append(
            "⚠ Growth rate accelerating. Recent turns consuming more context."
        )

    return {"messages": messages}


def format_recommendations(data: dict[str, list]) -> str:
    """Format recommendations section."""
    if not data["messages"]:
        return ""

    lines: list = ["\n### Recommendations"]
    for msg in data["messages"]:
        lines.append(f"  {msg}")

    return "\n".join(lines) + "\n"


def format_growth_analysis(
    overall_growth: float,
    last5_growth: float,
    last10_growth: float,
    ctx_limit: int,
) -> str:
    """Format growth analysis section."""
    return f"""
### Growth Analysis

  Period         │ Growth/Response │ Turns to Compact
  ───────────────┼─────────────────┼─────────────────
  Last 5         │ {format_tokens(int(last5_growth)):>15} │ ~{predict_turns(last5_growth, ctx_limit):>14}
  Last 10        │ {format_tokens(int(last10_growth)):>15} │ ~{predict_turns(last10_growth, ctx_limit):>14}
  Overall        │ {format_tokens(int(overall_growth)):>15} │ ~{predict_turns(overall_growth, ctx_limit):>14}"""


def main() -> None:
    """Main entry point."""
    path = get_transcript_path()
    if not path:
        print("Error: No transcript found.")
        sys.exit(1)

    r = parse_transcript(path)
    ctx_limit = get_context_limit(r["model"])

    history = [v for v in r["context_history"] if v is not None]
    overall_growth, last5_growth, last10_growth = calculate_growth_rates(history)

    current_state = calc_current_state(r, ctx_limit)
    print(format_current_state(current_state))

    print("### Context Growth Curve\n")
    chart = draw_chart(r["context_history"], context_limit=ctx_limit)
    if chart:
        print(chart)
    print()

    timeline_data = calc_compaction_timeline(r)
    print(format_compaction_timeline(timeline_data))

    print(
        format_growth_analysis(overall_growth, last5_growth, last10_growth, ctx_limit)
    )

    breakdown_data = calc_per_turn_breakdown(r, ctx_limit, overall_growth)
    print(format_per_turn_breakdown(breakdown_data))

    rec_data = calc_recommendations(
        r, current_state["ratio"], last5_growth, overall_growth
    )
    print(format_recommendations(rec_data))


if __name__ == "__main__":
    main()
