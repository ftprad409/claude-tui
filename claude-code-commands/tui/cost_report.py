#!/usr/bin/env python3
"""Cost analysis — spending breakdown, cache savings, projections."""

import sys
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import (
    parse_transcript,
    get_pricing,
    calc_cost,
    format_duration,
    format_tokens,
    get_transcript_path,
)


def calc_cost_summary(
    r: dict, pricing: dict, cost: dict, duration: str
) -> dict[str, Any]:
    """Calculate cost summary data."""
    return {
        "model": r["model"],
        "duration": duration,
        "turns": r["turns"],
        "tokens": r["tokens"],
        "cost": cost,
        "pricing": pricing,
    }


def format_cost_summary(data: dict[str, Any]) -> str:
    """Format cost summary section."""
    r = data
    cost = data["cost"]
    pricing = data["pricing"]
    return f"""
### Cost Summary

  Model: {r["model"]}
  Duration: {r["duration"]}
  Turns: {r["turns"]}

  Category       │ Tokens          │ Cost       │ $/M
  ───────────────┼─────────────────┼────────────┼──────
  Input          │ {r["tokens"]["input"]:>15,} │ ${cost["input"]:>9.2f} │ ${pricing["input"]:.2f}
  Cache Read     │ {r["tokens"]["cache_read"]:>15,} │ ${cost["cache_read"]:>9.2f} │ ${pricing["cache_read"]:.2f}
  Output         │ {r["tokens"]["output"]:>15,} │ ${cost["output"]:>9.2f} │ ${pricing["output"]:.2f}
  ───────────────┼─────────────────┼────────────┼──────
  Total          │                 │ ${cost["total"]:>9.2f} │"""


def calc_cache_savings(r: dict, pricing: dict) -> dict[str, Any]:
    """Calculate cache savings data."""
    cache_without = r["tokens"]["cache_read"] * pricing["input"] / 1_000_000
    cache_actual = r["tokens"]["cache_read"] * pricing["cache_read"] / 1_000_000
    saved = cache_without - cache_actual
    save_pct = saved / cache_without * 100 if cache_without > 0 else 0

    return {
        "cache_read": r["tokens"]["cache_read"],
        "cache_actual": cache_actual,
        "cache_without": cache_without,
        "saved": saved,
        "save_pct": save_pct,
    }


def format_cache_savings(data: dict[str, Any]) -> str:
    """Format cache savings section."""
    return f"""
### Cache Savings

  Tokens served from cache: {data["cache_read"]:,}
  Cost with caching:        ${data["cache_actual"]:.2f}
  Cost without caching:     ${data["cache_without"]:.2f}
  You saved:                ${data["saved"]:.2f} ({data["save_pct"]:.0f}%)"""


def calc_per_turn_costs(r: dict, pricing: dict) -> list[dict[str, Any]]:
    """Calculate per-turn costs."""
    per_turn: list = []
    for resp in r["per_response"]:
        rc = (
            resp["input"] * pricing["input"] / 1_000_000
            + resp["cache_read"] * pricing["cache_read"] / 1_000_000
            + resp["output"] * pricing["output"] / 1_000_000
        )
        per_turn.append({"turn": resp["turn"], "cost": rc, "output": resp["output"]})
    return per_turn


def calc_per_turn_summary(
    per_turn: list[dict[str, Any]],
    cost: dict,
    turns: int,
) -> Optional[dict[str, Any]]:
    """Calculate per-turn summary data."""
    if not per_turn:
        return None

    avg = cost["total"] / turns if turns > 0 else 0
    most_expensive = max(per_turn, key=lambda x: x["cost"])
    cheapest = min(per_turn, key=lambda x: x["cost"])

    return {
        "avg": avg,
        "most_expensive": most_expensive,
        "cheapest": cheapest,
    }


def format_per_turn_summary(data: Optional[dict[str, Any]]) -> str:
    """Format per-turn cost section."""
    if data is None:
        return ""

    return f"""
### Cost Per Turn

  Average:          ~${data["avg"]:.2f}/turn
  Most expensive:   Turn {data["most_expensive"]["turn"]} (${data["most_expensive"]["cost"]:.3f}, {data["most_expensive"]["output"]:,} output tokens)
  Cheapest:         Turn {data["cheapest"]["turn"]} (${data["cheapest"]["cost"]:.4f})"""


def calc_cost_trend(per_turn: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Calculate cost trend data."""
    if len(per_turn) < 2:
        return None

    turn_costs: defaultdict = defaultdict(float)
    for pt in per_turn:
        turn_costs[pt["turn"]] += pt["cost"]

    sorted_turns = sorted(turn_costs.items())
    last10 = sorted_turns[-10:]

    return {
        "sorted_turns": sorted_turns,
        "last10": last10,
    }


def calc_trend_direction(sorted_turns: list) -> Optional[str]:
    """Calculate trend direction."""
    if len(sorted_turns) < 10:
        return None

    first_half = [c for _, c in sorted_turns[: len(sorted_turns) // 2]]
    second_half = [c for _, c in sorted_turns[len(sorted_turns) // 2 :]]
    avg_first = sum(first_half) / len(first_half)
    avg_second = sum(second_half) / len(second_half)

    if avg_second > avg_first * 1.2:
        return "Increasing ↑ — later turns cost more"
    elif avg_second < avg_first * 0.8:
        return "Decreasing ↓ — later turns cost less"
    else:
        return "Stable → — consistent spend per turn"


def calc_cumulative_costs(last10: list, total_cost: float) -> list[dict[str, Any]]:
    """Calculate cumulative costs per turn."""
    cumulative = total_cost - sum(c for _, c in last10)
    result: list = []
    for turn, tc in last10:
        cumulative += tc
        result.append({"turn": turn, "cost": tc, "cumulative": cumulative})
    return result


def format_cost_trend(
    trend_data: Optional[dict[str, Any]],
    cumulative_data: list[dict[str, Any]],
    sorted_turns: list,
) -> str:
    """Format cost trend section."""
    if trend_data is None:
        return ""

    lines: list = [
        """
### Cost Trend (Last 10 Turns)

  Turn │ Cost     │ Cumulative
  ─────┼──────────┼───────────"""
    ]

    for item in cumulative_data:
        lines.append(
            f"  {item['turn']:>4} │ ${item['cost']:>7.3f} │ ${item['cumulative']:>8.2f}"
        )

    trend = calc_trend_direction(sorted_turns)
    if trend:
        lines.append(f"\n  Trend: {trend}")

    return "\n".join(lines)


def parse_timestamps(r: dict) -> tuple[Optional[datetime], Optional[datetime]]:
    """Parse start and end timestamps."""
    if not r["start_time"] or not r["end_time"]:
        return None, None

    try:
        start = datetime.fromisoformat(r["start_time"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(r["end_time"].replace("Z", "+00:00"))
        return start, end
    except (ValueError, OSError):
        return None, None


def calc_budget_projection(r: dict, cost: dict) -> Optional[dict[str, float]]:
    """Calculate budget projection data."""
    start, end = parse_timestamps(r)
    if start is None or end is None:
        return None

    mins = max((end - start).total_seconds() / 60, 1)
    cost_per_min = cost["total"] / mins
    cost_per_turn_avg = cost["total"] / max(r["turns"], 1)

    return {
        "cost_per_min": cost_per_min,
        "projected_2h": cost_per_min * 120,
        "projected_10_turns": cost_per_turn_avg * 10,
    }


def format_budget_projection(data: Optional[dict[str, float]]) -> str:
    """Format budget projection section."""
    if data is None:
        return ""

    return f"""
### Budget Projection

  Cost per minute:              ${data["cost_per_min"]:.3f}
  Projected for 2h session:     ${data["projected_2h"]:.2f}
  Projected for 10 more turns:  ${data["projected_10_turns"]:.2f}"""


def main() -> None:
    """Main entry point."""
    path = get_transcript_path()
    if not path:
        print("Error: No transcript found.")
        sys.exit(1)

    r = parse_transcript(path)
    pricing = get_pricing(r["model"])
    cost = calc_cost(r["tokens"], pricing)
    duration = format_duration(r["start_time"], r["end_time"])

    cost_summary = calc_cost_summary(r, pricing, cost, duration)
    print(format_cost_summary(cost_summary))

    cache_data = calc_cache_savings(r, pricing)
    print(format_cache_savings(cache_data))

    per_turn = calc_per_turn_costs(r, pricing)
    per_turn_summary = calc_per_turn_summary(per_turn, cost, r["turns"])
    print(format_per_turn_summary(per_turn_summary))

    trend_data = calc_cost_trend(per_turn)
    if trend_data:
        cumulative_data = calc_cumulative_costs(trend_data["last10"], cost["total"])
        print(
            format_cost_trend(trend_data, cumulative_data, trend_data["sorted_turns"])
        )

    budget_data = calc_budget_projection(r, cost)
    print(format_budget_projection(budget_data))

    print()


if __name__ == "__main__":
    main()
