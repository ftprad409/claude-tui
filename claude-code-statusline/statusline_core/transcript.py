"""Transcript parsing and metric calculations."""

import json
import os
from datetime import datetime, timezone
from typing import TypedDict

from .constants import (
    COMPACT_BUFFER,
    DEFAULT_CONTEXT_LIMIT,
    GRAY,
    GREEN,
    MODEL_CONTEXT_WINDOW,
    MODEL_PRICING,
    ORANGE,
    RED,
    RESET,
    UTC_OFFSET,
    YELLOW,
)
from .debug import debug_log
from .settings import is_visible


class InputData(TypedDict):
    model: str
    model_id: str
    cwd: str
    transcript_path: str
    session_id: str


class ContextMetrics(TypedDict):
    ratio: float
    compact_ratio: float


def get_context_limit(model_id):
    for key, limit in MODEL_CONTEXT_WINDOW.items():
        if key in model_id:
            return limit
    return DEFAULT_CONTEXT_LIMIT


def get_model_pricing(model_id):
    for key, pricing in MODEL_PRICING.items():
        if key in model_id:
            return pricing
    return MODEL_PRICING["claude-sonnet-4-6"]


def parse_input_data(data: dict) -> InputData:
    return {
        "model": data.get("model", {}).get("display_name", "unknown"),
        "model_id": data.get("model", {}).get("id", ""),
        "cwd": os.path.basename(data.get("workspace", {}).get("current_dir", "")),
        "transcript_path": data.get("transcript_path", ""),
        "session_id": data.get("session_id", "")[:8],
    }


def _new_metrics():
    return {
        "context_tokens": 0,
        "input_tokens_total": 0,
        "cache_read_tokens_total": 0,
        "cache_creation_tokens_total": 0,
        "output_tokens_total": 0,
        "compact_count": 0,
        "files_touched": set(),
        "session_start": None,
        "tool_calls": 0,
        "tool_errors": 0,
        "subagent_count": 0,
        "turn_count": 0,
        "thinking_count": 0,
        "context_history": [],
        "context_per_turn": [],
        "tokens_wasted": 0,
        "total_context_built": 0,
        "recent_tools": [],
        "current_turn_file_edits": {},
        "turns_since_compact": 0,
        "context_at_last_compact": 0,
        "_pre_compact_ctx": 0,
    }


def _is_compaction_entry(obj):
    return obj.get("type") == "summary" or (
        obj.get("type") == "system" and obj.get("subtype") == "compact_boundary"
    )


def _iter_json_objects(lines):
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _extract_latest_context_tokens(lines, result):
    for obj in _iter_json_objects(reversed(lines)):
        if _is_compaction_entry(obj):
            break
        if obj.get("type") == "assistant" and "message" in obj and "usage" in obj["message"]:
            usage = obj["message"]["usage"]
            keys = [
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "output_tokens",
            ]
            if all(k in usage for k in keys):
                result["context_tokens"] = sum(usage[k] for k in keys)
                return


def _update_turn_state(obj, result):
    if obj.get("type") != "user" or "message" not in obj:
        return
    content = obj["message"].get("content", [])
    has_text = False
    if isinstance(content, list):
        has_text = any(isinstance(b, dict) and b.get("type") == "text" for b in content)
    elif isinstance(content, str) and content.strip():
        has_text = True
    if has_text:
        result["turn_count"] += 1
        result["turns_since_compact"] += 1
        result["recent_tools"] = []
        result["current_turn_file_edits"] = {}


def _update_usage_metrics(obj, result, context_limit):
    if obj.get("type") != "assistant" or "message" not in obj or "usage" not in obj["message"]:
        return
    usage = obj["message"]["usage"]
    result["input_tokens_total"] += usage.get("input_tokens", 0)
    result["cache_read_tokens_total"] += usage.get("cache_read_input_tokens", 0)
    result["cache_creation_tokens_total"] += usage.get("cache_creation_input_tokens", 0)
    result["output_tokens_total"] += usage.get("output_tokens", 0)
    keys_ctx = ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"]
    ctx_snapshot = sum(usage.get(k, 0) for k in keys_ctx)
    result["_pre_compact_ctx"] = ctx_snapshot
    if result["context_at_last_compact"] == -1:
        result["context_at_last_compact"] = ctx_snapshot
        if result["compact_count"] > 0 and result.get("_ctx_before_compact", 0) > 0:
            pre = result["_ctx_before_compact"]
            cache_r = usage.get("cache_read_input_tokens", 0)
            headroom = max(0, context_limit - pre)
            summary = max(0, ctx_snapshot - cache_r)
            result["tokens_wasted"] += headroom + summary
    turn = result["turn_count"]
    if result["context_per_turn"] and result["context_per_turn"][-1][0] == turn:
        result["context_per_turn"][-1] = (turn, ctx_snapshot)
    else:
        result["context_per_turn"].append((turn, ctx_snapshot))
    out_tok = usage.get("output_tokens", 0)
    if out_tok > 0:
        result["context_history"].append(out_tok)


def _apply_compaction_state(obj, result, context_limit):
    if not _is_compaction_entry(obj):
        return
    result["compact_count"] += 1
    result["context_history"].append(None)
    result["_ctx_before_compact"] = result["_pre_compact_ctx"]
    result["total_context_built"] += context_limit
    result["turns_since_compact"] = 0
    result["context_at_last_compact"] = -1
    result["context_per_turn"] = []


def _record_tool_use(block, result, active_subagents):
    result["tool_calls"] += 1
    inp = block.get("input", {})
    tool_name = block.get("name", "")
    file_arg = ""
    for key in ("file_path", "path"):
        if key in inp and isinstance(inp[key], str):
            file_arg = os.path.basename(inp[key])
            break
    if file_arg:
        result["recent_tools"].append(f"{tool_name} {file_arg}")
    else:
        cmd = inp.get("command", "")
        short = cmd.split()[0] if cmd else ""
        result["recent_tools"].append(f"{tool_name} {short}".strip())
    for key in ("file_path", "path"):
        if key in inp and isinstance(inp[key], str):
            result["files_touched"].add(inp[key])
            if tool_name in ("Edit", "Write", "MultiEdit"):
                fname = os.path.basename(inp[key])
                result["current_turn_file_edits"][fname] = result["current_turn_file_edits"].get(fname, 0) + 1
    if tool_name in ("Task", "Agent"):
        task_id = block.get("id", "")
        if task_id:
            active_subagents.add(task_id)


def _update_tool_activity(obj, result, active_subagents):
    if obj.get("type") != "assistant" or "message" not in obj:
        return
    content = obj["message"].get("content", [])
    has_thinking = False
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "thinking":
                has_thinking = True
            if block.get("type") == "tool_use":
                _record_tool_use(block, result, active_subagents)
    if has_thinking:
        result["thinking_count"] += 1


def _update_tool_errors(obj, result):
    if obj.get("type") != "user" or "message" not in obj:
        return
    content = obj["message"].get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                result["tool_errors"] += 1


def parse_transcript(transcript_path, context_limit=None):
    if context_limit is None:
        context_limit = DEFAULT_CONTEXT_LIMIT
    result = _new_metrics()
    try:
        with open(transcript_path, "r") as f:
            lines = f.readlines()
    except OSError:
        debug_log(f"parse_transcript could not read: {transcript_path}")
        return result

    _extract_latest_context_tokens(lines, result)

    active_subagents = set()
    for obj in _iter_json_objects(lines):
        if result["session_start"] is None and "timestamp" in obj:
            result["session_start"] = obj["timestamp"]
        _update_turn_state(obj, result)
        _update_usage_metrics(obj, result, context_limit)
        _apply_compaction_state(obj, result, context_limit)
        _update_tool_activity(obj, result, active_subagents)
        _update_tool_errors(obj, result)

    result["subagent_count"] = len(active_subagents)
    return result


def format_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_cost(cost):
    return "<$0.01" if cost < 0.01 else f"${cost:.2f}"


def format_duration(start_timestamp):
    if not start_timestamp:
        return "0m"
    try:
        start = datetime.fromisoformat(start_timestamp.replace("Z", UTC_OFFSET))
        now = datetime.now(timezone.utc)
        total_minutes = int((now - start).total_seconds() / 60)
        if total_minutes < 60:
            return f"{total_minutes}m"
        return f"{total_minutes // 60}h {total_minutes % 60:02d}m"
    except Exception:
        debug_log("format_duration parse failed")
        return "?m"


def calculate_context_metrics(ctx_used: int, context_limit: int) -> ContextMetrics:
    ratio = ctx_used / context_limit if context_limit > 0 else 0
    compact_ratio = (context_limit - COMPACT_BUFFER) / context_limit if context_limit > 0 else 0.83
    return {"ratio": ratio, "compact_ratio": compact_ratio}


def calculate_session_cost(metrics: dict, pricing: dict) -> float:
    return (
        metrics["input_tokens_total"] * pricing["input"] / 1_000_000
        + metrics["cache_read_tokens_total"] * pricing["cache_read"] / 1_000_000
        + metrics["cache_creation_tokens_total"] * pricing.get("cache_write", pricing["input"] * 1.25) / 1_000_000
        + metrics["output_tokens_total"] * pricing["output"] / 1_000_000
    )


def calculate_cache_ratio(metrics: dict):
    total_input = metrics["input_tokens_total"] + metrics["cache_read_tokens_total"]
    if total_input <= 0:
        return 0, GRAY
    cache_pct = int(metrics["cache_read_tokens_total"] / total_input * 100)
    if cache_pct >= 70:
        return cache_pct, GREEN
    if cache_pct >= 40:
        return cache_pct, YELLOW
    return cache_pct, ORANGE


def format_cache_part(cache_pct, cache_color):
    return f"{cache_color}{cache_pct}%{RESET} cache" if cache_pct > 0 else f"{GRAY}0%{RESET} cache"


def calculate_cost_per_turn(cost: float, turn_count: int) -> str:
    if turn_count > 0:
        return f"{GRAY}~{format_cost(cost / turn_count)}/turn{RESET}"
    return ""


def format_context_trend(metrics: dict) -> str:
    """Readable context growth trend (tokens per turn)."""
    points = [ctx for _, ctx in metrics.get("context_per_turn", [])]
    if len(points) < 2:
        return ""
    deltas = [points[i] - points[i - 1] for i in range(1, len(points))]
    recent = deltas[-4:]
    if not recent:
        return ""
    avg_delta = sum(recent) / len(recent)
    magnitude = format_tokens(abs(int(avg_delta)))
    if avg_delta > 1200:
        color = ORANGE if avg_delta > 5000 else YELLOW
        return f"{color}CTX ↑{magnitude}{RESET}{GRAY}/t{RESET}"
    if avg_delta < -1200:
        return f"{GREEN}CTX ↓{magnitude}{RESET}{GRAY}/t{RESET}"
    return f"{GRAY}CTX →{magnitude}{RESET}{GRAY}/t{RESET}"


def _get_compact_ceiling(context_limit: int) -> float:
    compact_ceiling = context_limit - COMPACT_BUFFER
    env_pct = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "")
    if env_pct.isdigit() and 1 <= int(env_pct) <= 100:
        compact_ceiling = min(compact_ceiling, context_limit * int(env_pct) / 100)
    return compact_ceiling


def _estimate_context_growth_per_turn(turn_contexts: list[int], ctx_used: int, turns_since: int, baseline: int) -> float:
    if len(turn_contexts) >= 3:
        deltas = [
            turn_contexts[i] - turn_contexts[i - 1]
            for i in range(1, len(turn_contexts))
            if turn_contexts[i] > turn_contexts[i - 1]
        ]
        if not deltas:
            return 0
        alpha = 2 / (min(len(deltas), 5) + 1)
        ema = deltas[0]
        for d in deltas[1:]:
            ema = alpha * d + (1 - alpha) * ema
        return ema
    growth_since = ctx_used - baseline if baseline > 0 else ctx_used
    return growth_since / max(turns_since, 1)


def _prediction_color(turns_left: int) -> str:
    if turns_left <= 5:
        return RED
    if turns_left <= 15:
        return ORANGE
    if turns_left <= 30:
        return YELLOW
    return GREEN


def _format_turns_left(turns_left: int) -> str:
    """Compact turns-left formatter for cleaner statusline output."""
    if turns_left >= 1_000_000:
        return f"{turns_left / 1_000_000:.1f}M"
    if turns_left >= 1_000:
        return f"{turns_left / 1_000:.1f}k"
    return str(turns_left)


def calculate_compaction_prediction(
    ctx_used: int,
    context_limit: int,
    turns_since: int,
    metrics: dict,
    ratio: float,
    detailed: bool = True,
) -> str:
    if turns_since < 2 or ratio <= 0 or ratio >= 1.0:
        return ""
    remaining_tokens = _get_compact_ceiling(context_limit) - ctx_used
    growth_per_turn = _estimate_context_growth_per_turn(
        [ctx for _, ctx in metrics["context_per_turn"]],
        ctx_used,
        turns_since,
        metrics["context_at_last_compact"],
    )
    if growth_per_turn <= 0 or remaining_tokens <= 0:
        return ""
    turns_left = int(remaining_tokens / growth_per_turn)
    turns_str = _format_turns_left(turns_left)
    if detailed:
        return f"{_prediction_color(turns_left)}ETA {turns_str}{RESET} {GRAY}turns{RESET}"
    return f"{_prediction_color(turns_left)}ETA {turns_str}{RESET}"


def calculate_efficiency(metrics: dict, ctx_used: int) -> str:
    total_built = metrics["total_context_built"] + ctx_used
    if total_built <= 0 or not is_visible("line1", "efficiency"):
        return ""
    wasted = metrics["tokens_wasted"]
    eff_pct = int((max(0, 1 - wasted / total_built) if wasted > 0 else 1.0) * 100)
    if eff_pct >= 90:
        color = GREEN
    elif eff_pct >= 70:
        color = YELLOW
    elif eff_pct >= 50:
        color = ORANGE
    else:
        color = RED
    return f"{color}{eff_pct}%{RESET} {GRAY}eff{RESET}"
