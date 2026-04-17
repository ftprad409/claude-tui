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
- Progress bar: smooth true-color RGB gradient (`_lerp_rgb`) across 5 color stops (green→teal→yellow→peach→pink); percentage color scales relative to compaction ceiling

> **Personal note:** I prefer running in `compact` mode by default. Set default mode in `~/.claude/claudeui.json` under `"default_mode": "compact"`.

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

Live session da