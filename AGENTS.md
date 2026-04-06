# AGENTS.md

## Quick Commands

```bash
python3 claudetui.py monitor         # test monitor
python3 claudetui.py chart           # test efficiency chart
python3 claudetui.py mode custom      # test configurator TUI
python3 claudetui.py --help           # test dispatcher
python3 claude-code-monitor/test_monitor.py -v   # monitor tests
python3 claude-code-sniffer/test_sniffer.py -v   # sniffer tests
```

## Testing

Only two tools have tests: monitor and sniffer. Run both before/after refactoring.

## Gotchas (hard-won knowledge)

- **`MODEL_PRICING`** duplicated in 6 files: statusline.py, monitor/lib.py, commands/tui/lib.py, session-stats.py, session-manager.py, sniffer.py (abbreviated keys). Keep in sync when updating pricing.
- **`MODEL_CONTEXT_WINDOW`/`COMPACT_BUFFER`/`get_context_limit()`** duplicated in 3 files. Sync together.
- **`_fetch_api_status()` and `_format_api_status()`** duplicated in statusline.py and monitor.py. Sync when changing status page logic.
- **`_fetch_usage()`, `_format_usage_session()`, `_format_usage_weekly()`** in statusline.py only. OAuth token from `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`) or `CLAUDE_CODE_OAUTH_TOKEN` env.
- **`usage`** (line2) and **`usage_weekly`** (line3) are separate configurable components in custom mode.
- **Widget API**: `widget_fn(frame, ratio) -> list[str]` must return exactly 3 rows.

## Local Development

Test tools directly without modifying `~/.claude/settings.json`:
```bash
python3 claudetui.py monitor
python3 claudetui.py chart
```

To test statusline locally, edit `~/.claude/settings.json` to point to your repo path instead of installed path.

## Release

1. Bump `_FALLBACK_VERSION` in `claudetui.py`
2. Commit, tag (`git tag v0.X.Y`), push with `--tags`

## Key Files

- Entry point: `claudetui.py` (routes subcommands to tools)
- Each tool in its own directory, self-contained with README
- Config: `~/.claude/claudeui.json` (hot-reloads, no restart needed)