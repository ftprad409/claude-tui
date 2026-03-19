# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ClaudeTUI is a collection of standalone utilities for Claude Code. Each tool lives in its own subdirectory with its own README.

### Top-Level Scripts

- `claudetui.py` — CLI dispatcher. Routes all `claudetui *` subcommands to the correct tool. Detects version from git tags, falls back to `_FALLBACK_VERSION`. Also implements the `sniff` launcher (auto-detects sniffer port, sets `ANTHROPIC_BASE_URL`, execs `claude`).
- `claude-ui-mode.py` — Statusline mode switcher. `claudetui mode full|compact|custom`. The `custom` subcommand launches a curses TUI for toggling components.
- `install.sh` / `uninstall.sh` — Configures `~/.claude/settings.json` (statusLine, hooks), installs commands to `~/.claude/commands/tui/`, symlinks `claudetui` to PATH.

## Tools

### claude-code-statusline

Real-time status bar for Claude Code. Single-file script.

- Entry point: `claude-code-statusline/statusline.py`
- Reads session JSON from stdin (provided by Claude Code's `statusLine` feature)
- Parses the transcript JSONL file for token usage, compaction events, tool calls, errors, turns, cache ratio, and thinking blocks
- Three modes: `full` (3-line), `compact` (1-line), `custom` (configurable). Switch via `claudetui mode`
- Custom config: `~/.claude/claudeui.json` under `"custom"` key, with `is_visible(line, component)` helper
- Pluggable widget system (3x7 grid): `matrix`, `hex`, `bars`, `progress`, `none`
- Context window: auto-detected from model ID (`claude-opus-4` → 1M, others → 200k)
- Compaction prediction: fixed 33k buffer model (`context_limit - COMPACT_BUFFER`), not percentage-based
- Progress bar colors scale relative to compaction ceiling, not raw percentage

### claude-code-session-stats

Post-session analytics tool. Single-file script.

- Entry point: `claude-code-session-stats/session-stats.py`
- CLI tool — parses transcript JSONL files from `~/.claude/projects/`
- Generates cost breakdown, token sparkline, tool usage, file activity reports

### claude-code-session-manager

Session browser and manager. Single-file script.

- Entry point: `claude-code-session-manager/session-manager.py`
- Subcommands: `list`, `show`, `resume`, `diff`, `export`
- Reads from `~/.claude/projects/` directory structure

### claude-code-commands

Custom slash commands for in-session analytics. Markdown files installed to `~/.claude/commands/tui/`.

- Commands: `session` (full report), `cost` (spending breakdown), `perf` (tool efficiency), `context` (growth curve)
- Each command instructs Claude to read the current transcript JSONL and present formatted analysis
- No external dependencies — commands are pure markdown prompts
- Transcript path resolved via: `~/.claude/projects/$(pwd | sed 's|/|-|g; s|^-||')/*.jsonl`

### claude-code-monitor

Live session dashboard for a separate terminal.

- Entry point: `claude-code-monitor/monitor.py`
- Shared library: `claude-code-monitor/lib.py` (transcript parsing, formatting, constants, pricing)
- Chart module: `claude-code-monitor/chart.py` (efficiency chart rendering and segment building)
- Tests: `claude-code-monitor/test_monitor.py` (run with `python3 -v`)
- Watches transcript file for changes, refreshes on file change
- Args: none (auto-detect), `<session-id>`, `--list`, or `--chart [session-id]`
- Hotkeys: `s` stats, `d` details, `l` log viewer, `w` efficiency chart, `e` export, `o` sessions, `c` config, `i` Claude status, `?` help
- Efficiency chart: `w` hotkey or `claudetui chart` standalone — 4-component bar chart: system (cyan), summary (yellow), useful (green), headroom (gray). Press `?` for info overlay. Live updates via transcript file polling
- Log viewer: `f` cycles filter (all/errors/bash/edits/search/agents/skills/compactions), `a` toggles live auto-scroll
- Agent tracking: logs spawns/completions in event log; CURRENT section shows active/total agents per turn
- Skill tracking: logs skill invocations in event log; CURRENT section shows active skill while running
- Context window: auto-detected from model in transcript (`claude-opus-4` → 1M, others → 200k); stored as `r["context_limit"]`
- Compaction prediction: fixed 33k buffer model; progress bar colors scale to compaction ceiling

### claude-code-sniffer

API call interceptor proxy. Self-contained single-file script.

- Entry point: `claude-code-sniffer/sniffer.py`
- Transparent HTTP proxy using `ANTHROPIC_BASE_URL=http://localhost:PORT`
- Receives plain HTTP from Claude Code, forwards to `https://api.anthropic.com` over HTTPS
- Captures raw request/response bodies, HTTP headers, latency, SSE streaming events
- Console shows: tokens, cost, latency, traffic size, cache ratio, content block types, tool names, sub-agents
- Content block types: `T`=thinking, `t`=text, `U`=tool_use, `S`=server_tool_use, `W`=web_search_tool_result, `M`/`m`=mcp
- Sub-agent tracking: detects sub-agents by `Agent` tool presence in request tool list (session IDs are shared); groups by model + system_length for labeling
- Compaction detection: main-session only (sub-agents ignored), per-session-ID to avoid false positives across sessions; triggers on >50% message count drop or >70% body size drop
- Logs to `~/.claude/api-sniffer/sniffer-{timestamp}.jsonl`
- CLI: `claudetui sniffer [--port PORT] [--full] [--no-redact] [--quiet]`
- Launch helper: `claudetui sniff [--port PORT] [claude args...]` — auto-detects sniffer port, falls back to direct launch
- Multi-port: each sniffer writes `~/.claude/api-sniffer/.port.{PORT}`, cleaned up on shutdown
- API keys redacted from logs by default; log files created with `0o600` permissions

### claude-code-hooks

Claude Code hooks for automatic in-session context. Three hook scripts:

- `claude-code-hooks/session-heatmap.py` — SessionStart: shows file activity hotspots
- `claude-code-hooks/post-edit-deps.py` — PostToolUse (Edit|Write): shows reverse dependencies
- `claude-code-hooks/pre-edit-churn.py` — PreToolUse (Edit|Write): warns about high-churn files
- Configured via `hooks` in `~/.claude/settings.json`

### Shared Settings

- Config file: `~/.claude/claudeui.json` — shared between statusline and monitor
- Hot-reloads: both tools re-read on file change, no restart needed
- Settings: `sparkline.mode` (`"tail"` or `"merge"`), `sparkline.merge_size` (turns per bar, default: 2), `monitor.log_lines` (0–50, default: 8, 0 = off)
- Status page: `status.enabled` (default: `true`), `status.ttl` (cache TTL in seconds, default: `120`, min: `30`), `status.show_when_operational` (default: `false`)
- Config loader: `load_settings()` / `get_setting(*keys, default=...)` in each tool (statusline is self-contained; monitor imports from `lib.py`)

### Claude Status Page Integration

- API: Atlassian Statuspage v2 at `https://status.claude.com/api/v2/summary.json` (public, no auth)
- Cache: `~/.claude/api-status-cache.json` with configurable TTL (default 120s)
- Statusline: shows indicator on line2 when any component is not operational (▲ degraded / ▲ outage)
- Monitor: same indicator in header line; `i` hotkey opens detailed status overlay with all components + incidents
- Fetcher is inlined in both statusline.py and monitor.py (same convention as MODEL_PRICING etc.)

## Testing

```bash
python3 claude-code-monitor/test_monitor.py -v   # monitor: parsing, waste model, chart
python3 claude-code-sniffer/test_sniffer.py -v   # sniffer: formatters, SSE, session tracker, compaction
```

Quick syntax check for all tools:
```bash
python3 -c "import py_compile; [py_compile.compile(f, doraise=True) for f in ['claude-code-statusline/statusline.py', 'claude-code-monitor/lib.py', 'claude-code-monitor/monitor.py', 'claude-code-monitor/chart.py', 'claude-code-sniffer/sniffer.py', 'claude-code-commands/tui/lib.py', 'claudetui.py']]"
```

Run tests before and after refactoring to verify no regressions.

## Local Development

To test local changes to the statusline or hooks, update `~/.claude/settings.json` to point to your local repo instead of the installed path:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /path/to/your/repo/claude-code-statusline/statusline.py"
  }
}
```

The same applies to hook commands — replace the installed path with your local repo path. Remember to restore the original path when done testing (or re-run `./install.sh`).

`claudetui` and its subcommands can be tested directly without changing settings:
```bash
python3 claudetui.py monitor         # test the monitor
python3 claudetui.py chart           # test efficiency chart standalone
python3 claudetui.py mode custom     # test the configurator TUI
python3 claudetui.py mode --help     # test CLI
python3 claudetui.py --help          # test dispatcher
```

## Release Workflow

1. Bump `_FALLBACK_VERSION` in `claudetui.py`
2. Commit, tag (`git tag v0.X.Y`), push with `--tags`
3. Version is auto-detected from git tags at runtime; fallback used for curl/brew installs

## Gotchas

- **Duplicated constants**: `MODEL_PRICING`, `MODEL_CONTEXT_WINDOW`, `COMPACT_BUFFER`, and `get_context_limit()` exist in 3 places: `statusline.py`, `monitor/lib.py`, and `commands/tui/lib.py`. The statusline is self-contained (no imports from monitor); commands are standalone too. Keep all three in sync when updating pricing or adding models.
- **Duplicated fetcher**: `_fetch_api_status()` and `_format_api_status()` exist in both `statusline.py` and `monitor.py`. Keep in sync when changing status page logic.
- **Transcript format**: Compaction entries use `{"type": "system", "subtype": "compact_boundary"}`. Thinking blocks use `{"type": "thinking"}` in assistant message content (token counts redacted by the API).
- **Widget API**: Widget functions have signature `widget_fn(frame, ratio) -> list[str]` returning exactly 3 rows.

## Conventions

- Each tool is self-contained in its own directory with a README.md
- Python 3.8+, stdlib only — no external dependencies
- All tools parse Claude Code's JSONL transcript format from `~/.claude/projects/`
- MIT licensed
