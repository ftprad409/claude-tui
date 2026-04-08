"""Shared utilities for claude-code-monitor: parsing, formatting, constants."""

import json
import os
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ── ANSI helpers ──────────────────────────────────────────────────────

from claude_tui_components.utils import visible_len as _visible_len, truncate as _truncate_ansi, visual_rows as _visual_rows


# ── Settings ──────────────────────────────────────────────────────────

_SETTINGS_CACHE = None
_SETTINGS_MTIME = 0


def load_settings():
    """Load shared settings from ~/.claude/claudeui.json.

    Re-reads the file if it has been modified since last load,
    so users can tweak settings while the monitor is running.
    """
    global _SETTINGS_CACHE, _SETTINGS_MTIME
    path = os.path.join(os.path.expanduser("~"), ".claude", "claudeui.json")
    try:
        mtime = os.path.getmtime(path)
        if _SETTINGS_CACHE is not None and mtime == _SETTINGS_MTIME:
            return _SETTINGS_CACHE
        with open(path, "r") as f:
            _SETTINGS_CACHE = json.load(f)
        _SETTINGS_MTIME = mtime
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _SETTINGS_CACHE = {}
    return _SETTINGS_CACHE


def get_setting(*keys, default=None):
    """Get a nested setting value. e.g. get_setting('sparkline', 'mode')."""
    cfg = load_settings()
    for key in keys:
        if isinstance(cfg, dict):
            cfg = cfg.get(key)
        else:
            return default
    return cfg if cfg is not None else default


def reset_settings_cache():
    """Reset the settings cache so next load_settings() re-reads the file."""
    global _SETTINGS_CACHE, _SETTINGS_MTIME
    _SETTINGS_CACHE = None
    _SETTINGS_MTIME = 0


# ── Pricing and limits ────────────────────────────────────────────────

MODEL_PRICING = {
    # Claude 4.6 / 4.5  (cache_write = 1.25x input per Anthropic pricing)
    "claude-opus-4-6": {"input": 15.0, "cache_read": 1.5, "cache_write": 18.75, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "cache_read": 0.30, "cache_write": 3.75, "output": 15.0},
    "claude-haiku-4-5": {"input": 0.80, "cache_read": 0.08, "cache_write": 1.0, "output": 4.0},
    # Claude 3.5
    "claude-sonnet-3-5": {"input": 3.0, "cache_read": 0.30, "cache_write": 3.75, "output": 15.0},
    "claude-haiku-3-5": {"input": 0.80, "cache_read": 0.08, "cache_write": 1.0, "output": 4.0},
}
# Context window sizes by model family
MODEL_CONTEXT_WINDOW = {
    "claude-opus-4": 1_000_000,   # 1M context via anthropic-beta flag
}
DEFAULT_CONTEXT_LIMIT = 200_000
CONTEXT_LIMIT = DEFAULT_CONTEXT_LIMIT  # backward compat for tests
# Compaction triggers when remaining capacity drops below this buffer
COMPACT_BUFFER = 33_000


def get_context_limit(model_id):
    """Get context window size for a model ID."""
    for key, limit in MODEL_CONTEXT_WINDOW.items():
        if key in model_id:
            return limit
    return DEFAULT_CONTEXT_LIMIT


# ── ANSI codes ────────────────────────────────────────────────────────

from claude_tui_components.colors import (
    RESET, BOLD, DIM, GREEN, YELLOW, ORANGE, RED, CYAN, MAGENTA, WHITE, GRAY,
    CLEAR, HIDE_CURSOR, SHOW_CURSOR, ERASE_LINE, ALT_SCREEN_ON, ALT_SCREEN_OFF,
    LOGO_GREEN, M_DARK, M_MID, M_BRIGHT, PULSE_NEW, PULSE_IDLE
)


# ── Transcript discovery ──────────────────────────────────────────────

def find_transcript(cwd=None):
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
    jsonl_files = sorted(project_dir.glob("*.jsonl"),
                         key=lambda f: f.stat().st_mtime, reverse=True)
    return str(jsonl_files[0]) if jsonl_files else None


def find_latest_transcript():
    """Find the most recently modified transcript across all projects."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    latest = None
    latest_mtime = 0
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            mtime = jsonl.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest = str(jsonl)
    return latest


def find_session_by_id(session_id):
    """Find transcript path by session ID prefix."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            if jsonl.stem.startswith(session_id):
                return str(jsonl)
    return None


# ── Transcript parsing

def parse_transcript(path):
    """Parse a transcript JSONL file into a comprehensive report dict."""
    r = {
        "path": path, "model": "", "session_id": Path(path).stem[:8],
        "start_time": None, "end_time": None,
        "turns": 0, "responses": 0,
        "compact_count": 0, "compact_events": [], "tokens_wasted": 0, "total_context_built": 0,
        "system_prompt_tokens": 0,
        "tokens": {"input": 0, "cache_read": 0, "cache_creation": 0, "output": 0},
        "context_history": [], "per_response": [], "context_per_turn": {},
        "tool_counts": Counter(), "tool_errors": 0, "tool_error_details": [],
        "files_read": Counter(), "files_edited": Counter(),
        "lines_added": 0, "lines_removed": 0, "files_created": 0,
        "thinking_count": 0, "subagent_count": 0, "skill_count": 0, "turns_since_compact": 0,
        "recent_tools": [],  # last N tool calls for live trace
        "last_error_msg": "",
        # Current turn (current question/answer)
        "turn_tool_counts": Counter(),
        "turn_tool_errors": 0,
        "turn_files_read": Counter(),
        "turn_files_edited": Counter(),
        "turn_thinking": 0,
        "turn_agents_spawned": 0,
        "turn_agents_pending": set(),
        "turn_skill_active": None,  # name of currently running skill (Skill tool_use without result)
        # Turn timer
        "last_user_ts": None,   # timestamp of last user message
        "last_assist_ts": None, # timestamp of last assistant response
        "waiting_for_response": False,
        # Event log
        "event_log": [],  # list of (timestamp_str, description)
    }
    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return r

    context_limit = DEFAULT_CONTEXT_LIMIT  # resolved when model is detected
    subagents = set()
    agent_labels = {}  # tool_use_id -> description
    current_turn = 0
    last_context = 0
    context_at_last_compact = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = obj.get("timestamp")
        etype = obj.get("type", "")
        if ts:
            if r["start_time"] is None:
                r["start_time"] = ts
            r["end_time"] = ts
        if not r["model"] and etype == "assistant" and "message" in obj:
            r["model"] = obj["message"].get("model", "")
            if r["model"]:
                context_limit = get_context_limit(r["model"])

        # User turns
        if etype == "user" and not obj.get("isMeta"):
            content = obj.get("message", {}).get("content", "")
            has_text = False
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        has_text = True
                        break
            elif isinstance(content, str) and content.strip():
                has_text = True
            if has_text:
                current_turn += 1
                r["turns"] += 1
                r["turns_since_compact"] += 1
                r["last_user_ts"] = ts
                r["waiting_for_response"] = True
                r["recent_tools"] = []
                r["turn_tool_counts"] = Counter()
                r["turn_tool_errors"] = 0
                r["turn_files_read"] = Counter()
                r["turn_files_edited"] = Counter()
                r["turn_thinking"] = 0
                r["turn_agents_spawned"] = 0
                r["turn_agents_pending"] = set()
                r["turn_skill_active"] = None

        # Tool errors
        if etype == "user" and "message" in obj:
            content = obj.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if (isinstance(block, dict)
                            and block.get("type") == "tool_result"
                            and block.get("is_error")):
                        # Clear active skill if this error is from a Skill tool
                        err_tid = block.get("tool_use_id", "")
                        if err_tid in agent_labels and agent_labels[err_tid].startswith("skill:"):
                            r["turn_skill_active"] = None
                            del agent_labels[err_tid]
                        r["tool_errors"] += 1
                        r["turn_tool_errors"] += 1
                        # Capture error message
                        err_content = block.get("content", "")
                        if isinstance(err_content, list):
                            for eb in err_content:
                                if isinstance(eb, dict) and eb.get("type") == "text":
                                    err_content = eb.get("text", "")
                                    break
                        if isinstance(err_content, str) and err_content:
                            r["last_error_msg"] = err_content[:300]
                        r["tool_error_details"].append({
                            "turn": current_turn,
                            "error": r["last_error_msg"],
                        })
                        err_msg = r["last_error_msg"] if r["last_error_msg"] else "unknown"
                        r["event_log"].append((ts, f"error: {err_msg}"))

        # Agent results
        if etype == "user" and "message" in obj:
            content = obj.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if (isinstance(block, dict)
                            and block.get("type") == "tool_result"
                            and not block.get("is_error")):
                        tool_id = block.get("tool_use_id", "")
                        if tool_id in agent_labels:
                            if agent_labels[tool_id].startswith("skill:"):
                                r["turn_skill_active"] = None
                                del agent_labels[tool_id]
                                continue
                            r["turn_agents_pending"].discard(tool_id)
                            label = agent_labels[tool_id]
                            # Extract first line of agent result as summary
                            result_text = ""
                            rc = block.get("content", "")
                            if isinstance(rc, list):
                                for rb in rc:
                                    if isinstance(rb, dict) and rb.get("type") == "text":
                                        result_text = rb.get("text", "")
                                        break
                            elif isinstance(rc, str):
                                result_text = rc
                            # Get first meaningful line as summary
                            summary = ""
                            for line in result_text.split("\n"):
                                line = line.strip()
                                if line and not line.startswith("<") and not line.startswith("agentId:"):
                                    summary = line[:120]
                                    break
                            if summary:
                                r["event_log"].append((ts, f"agent done: {label} → {summary}"))
                            else:
                                r["event_log"].append((ts, f"agent done: {label}"))

        # Assistant responses
        if etype == "assistant" and "message" in obj:
            msg = obj["message"]
            content = msg.get("content", [])
            usage = msg.get("usage", {})
            r["responses"] += 1
            r["last_assist_ts"] = ts
            r["waiting_for_response"] = False
            has_thinking = False
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "thinking":
                            has_thinking = True
                        if block.get("type") == "tool_use":
                            name = block.get("name", "unknown")
                            r["tool_counts"][name] += 1
                            r["turn_tool_counts"][name] += 1
                            inp = block.get("input", {})
                            if name in ("Task", "Agent"):
                                tid = block.get("id", "")
                                if tid:
                                    subagents.add(tid)
                                agent_desc = inp.get("description", "")
                                agent_type = inp.get("subagent_type", "")
                                agent_label = agent_desc or agent_type or "subagent"
                                r["event_log"].append((ts, f"agent: {agent_label}"))
                                r["recent_tools"].append(f"agent {agent_label}")
                                r["turn_agents_spawned"] += 1
                                if tid:
                                    agent_labels[tid] = agent_label
                                    r["turn_agents_pending"].add(tid)
                                continue
                            if name == "Skill":
                                skill_name = inp.get("skill", "")
                                tid = block.get("id", "")
                                if skill_name:
                                    r["turn_skill_active"] = skill_name
                                    r["skill_count"] += 1
                                    r["event_log"].append((ts, f"skill: /{skill_name}"))
                                    if tid:
                                        agent_labels[tid] = f"skill:{skill_name}"
                                continue
                            fp = inp.get("file_path", inp.get("path", ""))
                            # Build tool trace entry + event log
                            if fp:
                                fname = os.path.basename(fp)
                                if name in ("Edit", "Write", "MultiEdit"):
                                    r["files_edited"][fname] += 1
                                    r["turn_files_edited"][fname] += 1
                                    # Count lines added/removed
                                    if name == "Edit":
                                        old = inp.get("old_string", "")
                                        new = inp.get("new_string", "")
                                        if old or new:
                                            r["lines_removed"] += old.count("\n") + 1
                                            r["lines_added"] += new.count("\n") + 1
                                    elif name == "Write":
                                        write_content = inp.get("content", "")
                                        if write_content:
                                            r["files_created"] += 1
                                            r["lines_added"] += write_content.count("\n") + 1
                                    elif name == "MultiEdit":
                                        for edit in inp.get("edits", []):
                                            old = edit.get("old_string", "")
                                            new = edit.get("new_string", "")
                                            if old or new:
                                                r["lines_removed"] += old.count("\n") + 1
                                                r["lines_added"] += new.count("\n") + 1
                                else:
                                    r["files_read"][fname] += 1
                                    r["turn_files_read"][fname] += 1
                                trace_entry = f"{name.lower()} {fname}"
                                r["recent_tools"].append(trace_entry)
                                r["event_log"].append((ts, trace_entry))
                            else:
                                cmd = inp.get("command", "")
                                if cmd:
                                    cmd_short = cmd.split()[0] if cmd else ""
                                    trace_entry = f"{name.lower()} {cmd_short}"
                                    # Full command for event log (clean up multiline)
                                    cmd_clean = cmd.replace("\n", " ").strip()
                                    r["event_log"].append((ts, f"$ {cmd_clean}"))
                                else:
                                    query = inp.get("pattern", inp.get("query", inp.get("prompt", inp.get("skill", ""))))
                                    if query:
                                        q_clean = str(query).replace("\n", " ").strip()
                                        trace_entry = f"{name.lower()} {q_clean}"
                                        r["event_log"].append((ts, f"{name.lower()}: {q_clean}"))
                                    else:
                                        trace_entry = name.lower()
                                        r["event_log"].append((ts, trace_entry))
                                r["recent_tools"].append(trace_entry)
            if has_thinking:
                r["thinking_count"] += 1
                r["turn_thinking"] += 1
            if usage:
                inp_t = usage.get("input_tokens", 0)
                cache_r = usage.get("cache_read_input_tokens", 0)
                cache_c = usage.get("cache_creation_input_tokens", 0)
                out_t = usage.get("output_tokens", 0)
                r["tokens"]["input"] += inp_t
                r["tokens"]["cache_read"] += cache_r
                r["tokens"]["cache_creation"] += cache_c
                r["tokens"]["output"] += out_t
                ctx = inp_t + cache_r + cache_c + out_t
                last_context = ctx
                if context_at_last_compact == -1:
                    context_at_last_compact = ctx
                    # Detect system prompt from first post-compaction cache_read
                    if r["system_prompt_tokens"] == 0 and cache_r > 0:
                        r["system_prompt_tokens"] = cache_r
                    # Compute waste: headroom + summary (rebuild minus system prompt)
                    if r["compact_events"]:
                        evt = r["compact_events"][-1]
                        evt["context_after"] = ctx
                        evt["system_prompt"] = cache_r
                        pre = evt["context_before"]
                        if pre > 0:
                            headroom = max(0, context_limit - pre)
                            summary = max(0, ctx - cache_r)  # rebuild minus system prompt
                            r["tokens_wasted"] += headroom + summary
                # Track per-turn context (last snapshot per turn wins)
                r["context_per_turn"][current_turn] = ctx
                if out_t > 0:
                    r["context_history"].append(out_t)
                r["per_response"].append({
                    "turn": current_turn, "ctx": ctx, "output": out_t,
                    "timestamp": ts,
                })

        # Compaction
        if (etype == "summary" or
                (etype == "system" and obj.get("subtype") == "compact_boundary")):
            r["compact_count"] += 1
            r["total_context_built"] += context_limit  # full window budget per segment
            r["context_history"].append(None)
            r["event_log"].append((ts, f"⚡ compaction #{r['compact_count']}"))
            r["compact_events"].append({
                "turn": current_turn,
                "context_before": last_context,
                "turns_since_last": r["turns_since_compact"],
            })
            r["turns_since_compact"] = 0
            r["context_per_turn"] = {}  # reset per-turn tracking
            context_at_last_compact = -1  # sentinel: next usage sets baseline

    r["context_at_last_compact"] = context_at_last_compact
    r["subagent_count"] = len(subagents)
    r["last_context"] = last_context
    r["recent_tools"] = r["recent_tools"][-5:]
    r["full_log"] = list(r["event_log"])  # full log for viewer
    max_log = get_setting("monitor", "log_lines", default=8)
    if max_log is False or max_log == 0:
        r["event_log"] = []
    elif isinstance(max_log, int) and max_log > 0:
        r["event_log"] = r["event_log"][-max_log:]
    else:
        r["event_log"] = r["event_log"][-8:]  # invalid config, fallback
    r["context_limit"] = context_limit
    return r

# ── Formatting helpers ────────────────────────────────────────────────

def get_pricing(model):
    for key, p in MODEL_PRICING.items():
        if key in model:
            return p
    return MODEL_PRICING["claude-sonnet-4-6"]


def calc_cost(tokens, pricing):
    c_input = tokens["input"] * pricing["input"] / 1_000_000
    c_cache_read = tokens["cache_read"] * pricing["cache_read"] / 1_000_000
    c_cache_write = tokens["cache_creation"] * pricing.get("cache_write", pricing["input"] * 1.25) / 1_000_000
    c_output = tokens["output"] * pricing["output"] / 1_000_000
    return {"input": c_input, "cache_read": c_cache_read, "cache_write": c_cache_write,
            "output": c_output, "total": c_input + c_cache_read + c_cache_write + c_output}


def efficiency_color(eff_pct):
    """Return ANSI color for an efficiency percentage."""
    if eff_pct >= 90:
        return GREEN
    elif eff_pct >= 70:
        return YELLOW
    elif eff_pct >= 50:
        return ORANGE
    return RED


def format_duration_live(start_ts):
    """Format duration from start to NOW (live updating)."""
    try:
        start = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
        secs = int((datetime.now(timezone.utc) - start).total_seconds())
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        if h > 0:
            return f"{h}h {m:02d}m {s:02d}s"
        return f"{m}m {s:02d}s"
    except Exception:
        return "—"


def format_event_time(ts_str):
    """Format timestamp to HH:MM:SS for event log."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        local = dt.astimezone()
        return local.strftime("%H:%M:%S")
    except Exception:
        return "??:??:??"


def format_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def get_terminal_width():
    """Get terminal width, default 80."""
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80
