"""Shared parsing and formatting for UI commands."""

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from claude_tui_core.models import (
    MODEL_PRICING,
    MODEL_CONTEXT_WINDOW,
    DEFAULT_CONTEXT_LIMIT,
    get_context_limit
)


def find_transcript(cwd: Optional[str] = None) -> Optional[str]:
    """Find the most recent transcript for the given working directory."""
    if cwd is None:
        cwd = os.getcwd()
    projects_dir = Path.home() / ".claude" / "projects"
    project_name = "-" + cwd.replace("/", "-").lstrip("-")
    project_dir = projects_dir / project_name
    if not project_dir.exists():
        project_name = cwd.replace("/", "-").lstrip("-")
        project_dir = projects_dir / project_name
    if not project_dir.exists():
        return None
    jsonl_files = sorted(
        project_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True
    )
    return str(jsonl_files[0]) if jsonl_files else None


def create_empty_result(path: str) -> dict[str, Any]:
    """Create empty result dict with all fields."""
    return {
        "path": path,
        "model": "",
        "session_id": Path(path).stem[:8],
        "start_time": None,
        "end_time": None,
        "turns": 0,
        "responses": 0,
        "compact_count": 0,
        "compact_events": [],
        "tokens": {
            "input": 0,
            "cache_read": 0,
            "cache_creation": 0,
            "output": 0,
        },
        "context_history": [],
        "per_response": [],
        "tool_counts": Counter(),
        "tool_errors": 0,
        "tool_error_details": [],
        "files_read": Counter(),
        "files_edited": Counter(),
        "thinking_count": 0,
        "subagent_count": 0,
        "turns_since_compact": 0,
    }


def read_transcript_lines(path: str) -> list[str]:
    """Read transcript file and return list of lines."""
    try:
        with open(path, "r") as f:
            return f.readlines()
    except (FileNotFoundError, PermissionError):
        return []


def parse_json_line(line: str) -> Optional[dict[str, Any]]:
    """Parse a single JSON line, return None if invalid."""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def update_timestamps(ts: Optional[str]) -> dict[str, Optional[str]]:
    """Calculate timestamp updates."""
    if not ts:
        return {"start_time": None, "end_time": None}
    return {"start_time": ts, "end_time": ts}


def merge_timestamps(
    existing: dict[str, Any],
    updates: dict[str, Optional[str]],
) -> dict[str, Any]:
    """Merge timestamp updates with existing data."""
    if updates["start_time"] and not existing.get("start_time"):
        existing["start_time"] = updates["start_time"]
    if updates["end_time"]:
        existing["end_time"] = updates["end_time"]
    return existing


def extract_model(obj: dict[str, Any]) -> Optional[str]:
    """Extract model from object if present."""
    etype = obj.get("type", "")
    if etype == "assistant" and "message" in obj:
        model = obj["message"].get("model", "")
        if model:
            return model
    return None


def merge_model(existing: str, new_model: Optional[str]) -> str:
    """Merge model if not already set."""
    if not existing and new_model:
        return new_model
    return existing


def has_text_content(content: Any) -> bool:
    """Check if content has text."""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return True
    elif isinstance(content, str) and content.strip():
        return True
    return False


def calc_user_turn(obj: dict[str, Any]) -> dict[str, bool]:
    """Calculate user turn data from object."""
    etype = obj.get("type", "")
    if etype == "user" and not obj.get("isMeta"):
        content = obj.get("message", {}).get("content", "")
        if has_text_content(content):
            return {"is_user_turn": True, "has_text": True}
    return {"is_user_turn": False, "has_text": False}


def extract_error_text(block: dict[str, Any]) -> str:
    """Extract error text from tool result block."""
    bc = block.get("content", "")
    if isinstance(bc, list):
        for b in bc:
            if isinstance(b, dict) and b.get("type") == "text":
                return b.get("text", "")[:100]
    elif isinstance(bc, str):
        return bc[:100]
    return ""


def calc_tool_error(obj: dict[str, Any]) -> Optional[dict[str, str]]:
    """Calculate tool error data from object."""
    etype = obj.get("type", "")
    if etype != "user" or "message" not in obj:
        return None

    content = obj.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return None

    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_result":
            continue
        if not block.get("is_error"):
            continue

        return {
            "error_text": extract_error_text(block),
            "tool_id": block.get("tool_use_id", ""),
        }
    return None


def merge_tool_errors(
    data: dict[str, Any], error: Optional[dict[str, str]]
) -> dict[str, Any]:
    """Merge tool error into accumulated data."""
    if error:
        data["tool_errors"] += 1
        data["tool_error_details"].append(
            {
                "turn": data.get("current_turn", 0),
                "error": error["error_text"],
            }
        )
    return data


def extract_subagent_id(block: dict[str, Any]) -> Optional[str]:
    """Extract subagent ID from tool use block."""
    name = block.get("name", "")
    if name in ("Task", "Agent"):
        return block.get("id", "")
    return None


def extract_filename(block: dict[str, Any], name: str) -> tuple[Optional[str], bool]:
    """Extract filename from tool input."""
    inp = block.get("input", {})
    fp = inp.get("file_path", inp.get("path", ""))
    if fp:
        return os.path.basename(fp), name in ("Edit", "Write", "MultiEdit")
    return None, False


def process_single_block(block: Any) -> dict[str, Optional[str] | bool]:
    """Process a single content block and return extracted data."""
    result: dict[str, Optional[str] | bool] = {
        "tool_name": None,
        "subagent_id": None,
        "filename": None,
        "is_edited": False,
        "has_thinking": False,
    }

    if not isinstance(block, dict):
        return result

    btype = block.get("type")
    if btype == "thinking":
        result["has_thinking"] = True

    if btype == "tool_use":
        result["tool_name"] = block.get("name", "unknown")
        result["subagent_id"] = extract_subagent_id(block)
        fname: Optional[str] = None
        is_edited_val: bool = False
        fname, is_edited_val = extract_filename(block, str(result["tool_name"]))
        result["filename"] = fname
        result["is_edited"] = is_edited_val

    return result


def calc_tool_uses(content: Any) -> dict[str, Any]:
    """Calculate tool uses data from content blocks."""
    tool_counts: Counter = Counter()
    subagent_ids: set = set()
    files_read: Counter = Counter()
    files_edited: Counter = Counter()
    has_thinking = False

    if not isinstance(content, list):
        return {
            "tool_counts": tool_counts,
            "subagent_ids": subagent_ids,
            "files_read": files_read,
            "files_edited": files_edited,
            "has_thinking": has_thinking,
        }

    for block in content:
        block_data = process_single_block(block)

        if block_data["has_thinking"]:
            has_thinking = True

        if block_data["tool_name"]:
            tool_counts[block_data["tool_name"]] += 1

        if block_data["subagent_id"]:
            subagent_ids.add(block_data["subagent_id"])

        if block_data["filename"]:
            if block_data["is_edited"]:
                files_edited[block_data["filename"]] += 1
            else:
                files_read[block_data["filename"]] += 1

    return {
        "tool_counts": tool_counts,
        "subagent_ids": subagent_ids,
        "files_read": files_read,
        "files_edited": files_edited,
        "has_thinking": has_thinking,
    }


def merge_tool_uses(data: dict[str, Any], tool_data: dict[str, Any]) -> dict[str, Any]:
    """Merge tool uses into accumulated data."""
    data["tool_counts"].update(tool_data["tool_counts"])
    data["files_read"].update(tool_data["files_read"])
    data["files_edited"].update(tool_data["files_edited"])
    if tool_data["has_thinking"]:
        data["thinking_count"] += 1
    return data


def calc_usage(usage: Any) -> Optional[dict[str, int]]:
    """Calculate usage data from usage dict."""
    if not usage:
        return None

    inp_t = usage.get("input_tokens", 0)
    cache_r = usage.get("cache_read_input_tokens", 0)
    cache_c = usage.get("cache_creation_input_tokens", 0)
    out_t = usage.get("output_tokens", 0)

    return {
        "input": inp_t,
        "cache_read": cache_r,
        "cache_creation": cache_c,
        "output": out_t,
        "total": inp_t + cache_r + cache_c + out_t,
    }


def merge_usage(
    data: dict[str, Any], usage: Optional[dict[str, int]]
) -> dict[str, Any]:
    """Merge usage into accumulated data."""
    if not usage:
        return data

    data["tokens"]["input"] += usage["input"]
    data["tokens"]["cache_read"] += usage["cache_read"]
    data["tokens"]["cache_creation"] += usage["cache_creation"]
    data["tokens"]["output"] += usage["output"]

    return data


def calc_response_context(
    usage: dict[str, int],
    current_turn: int,
    ts: Optional[str],
) -> Optional[dict[str, Any]]:
    """Calculate response context data."""
    if not usage:
        return None

    return {
        "turn": current_turn,
        "ctx": usage["total"],
        "input": usage["input"],
        "cache_read": usage["cache_read"],
        "output": usage["output"],
        "timestamp": ts,
    }


def merge_response_context(
    data: dict[str, Any],
    ctx_data: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Merge response context into accumulated data."""
    if ctx_data:
        data["context_history"].append(ctx_data["ctx"])
        data["per_response"].append(ctx_data)
    return data


def is_compaction_event(obj: dict[str, Any]) -> bool:
    """Check if object represents a compaction event."""
    etype = obj.get("type", "")
    if etype == "summary":
        return True
    if etype == "system" and obj.get("subtype") == "compact_boundary":
        return True
    return False


def calc_compaction(
    ctx_before: int,
    current_turn: int,
    ts: Optional[str],
    turns_since_compact: int,
) -> dict[str, Any]:
    """Calculate compaction event data."""
    return {
        "turn": current_turn,
        "context_before": ctx_before,
        "turns_since_last": turns_since_compact,
        "timestamp": ts,
    }


def merge_compaction(
    data: dict[str, Any],
    compaction: dict[str, Any],
) -> dict[str, Any]:
    """Merge compaction into accumulated data."""
    data["compact_count"] += 1
    data["context_history"].append(None)
    data["compact_events"].append(compaction)
    data["turns_since_compact"] = 0
    return data


def merge_turn_count(
    data: dict[str, Any],
    is_user_turn: bool,
) -> dict[str, Any]:
    """Merge turn count into accumulated data."""
    if is_user_turn:
        data["turns"] += 1
        data["turns_since_compact"] += 1
    return data


def parse_transcript(path: str) -> dict[str, Any]:
    """Parse a transcript JSONL file into a comprehensive report dict."""
    data = create_empty_result(path)

    lines = read_transcript_lines(path)

    for line in lines:
        obj = parse_json_line(line)
        if obj is None:
            continue

        ts = obj.get("timestamp")
        timestamps = update_timestamps(ts)
        data = merge_timestamps(data, timestamps)

        model = extract_model(obj)
        data["model"] = merge_model(data["model"], model)

        user_turn = calc_user_turn(obj)
        data = merge_turn_count(data, user_turn["is_user_turn"])
        data["current_turn"] = data["turns"]

        error = calc_tool_error(obj)
        data = merge_tool_errors(data, error)

        etype = obj.get("type", "")
        if etype == "assistant" and "message" in obj:
            msg = obj["message"]
            content = msg.get("content", [])
            usage = msg.get("usage", {})

            data["responses"] += 1

            tool_data = calc_tool_uses(content)
            data = merge_tool_uses(data, tool_data)
            data["subagents"] = data.get("subagents", set()) | tool_data["subagent_ids"]

            usage_data = calc_usage(usage)
            data = merge_usage(data, usage_data)

            if usage_data:
                ctx_data = calc_response_context(usage_data, data["turns"], ts)
                data = merge_response_context(data, ctx_data)
                if ctx_data is not None:
                    data["last_context"] = ctx_data["ctx"]
            else:
                data["last_context"] = 0

        if is_compaction_event(obj):
            compaction = calc_compaction(
                data.get("last_context", 0),
                data["turns"],
                ts,
                data["turns_since_compact"],
            )
            data = merge_compaction(data, compaction)

    data["subagent_count"] = len(data.get("subagents", set()))
    data["context_limit"] = get_context_limit(data["model"])
    data["last_context"] = data.get("last_context", 0)
    data.pop("subagents", None)
    data.pop("current_turn", None)

    return data


def get_pricing(model: str) -> dict[str, float]:
    """Get pricing dict for a model string."""
    for key, p in MODEL_PRICING.items():
        if key in model:
            return p
    return MODEL_PRICING["claude-sonnet-4-6"]


def calc_cost(
    tokens: dict[str, int],
    pricing: dict[str, float],
) -> dict[str, float]:
    """Calculate costs from token counts and pricing."""
    c_input = tokens["input"] * pricing["input"] / 1_000_000
    c_cache_read = tokens["cache_read"] * pricing["cache_read"] / 1_000_000
    c_cache_write = (
        tokens.get("cache_creation", 0)
        * pricing.get("cache_write", pricing["input"] * 1.25)
        / 1_000_000
    )
    c_output = tokens["output"] * pricing["output"] / 1_000_000
    return {
        "input": c_input,
        "cache_read": c_cache_read,
        "cache_write": c_cache_write,
        "output": c_output,
        "total": c_input + c_cache_read + c_cache_write + c_output,
    }


def format_duration(start_ts: Optional[str], end_ts: Optional[str] = None) -> str:
    """Format duration between two ISO timestamps."""
    if not start_ts:
        return "unknown"
    try:
        start = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
        if end_ts:
            end = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
        else:
            end = datetime.now(timezone.utc)
        secs = int((end - start).total_seconds())
        h, m = secs // 3600, (secs % 3600) // 60
        return f"{h}h {m}m" if h > 0 else f"{m}m"
    except (ValueError, OSError):
        return "unknown"


def format_tokens(n: int) -> str:
    """Format token count as human-readable."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def get_transcript_path() -> Optional[str]:
    """Get transcript path from argv or auto-detect."""
    if len(sys.argv) > 1:
        return sys.argv[1]
    return find_transcript()
