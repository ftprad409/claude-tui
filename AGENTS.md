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

- **`python3 claudetui.py uninstall`** removes the repo directory! Use with caution - it deletes the git repo folder

- **Usage API rate limiting**: The OAuth usage API frequently returns 429 errors. Solutions:
  - Exponential backoff: 2min → 4min → 8min (max 10min)
  - Force refresh when five_hour.resets_at time has passed
  - Preserve cache on 429 only if cache has real usage data (five_hour exists)

- **`MODEL_PRICING`** duplicated in 6 files: `statusline_core/constants.py`, `monitor/lib.py`, `commands/tui/lib.py`, `session-stats.py`, `session-manager.py`, `sniffer.py` (abbreviated keys). Keep in sync when updating pricing.
- **`MODEL_CONTEXT_WINDOW`/`COMPACT_BUFFER`/`get_context_limit()`** duplicated in 3 files. Sync together.
- **Status page fetcher parity**: `statusline_core/api_clients.py` (`fetch_api_status()`/`format_api_status()`) and `monitor.py` (`_fetch_api_status()`/`_format_api_status()`) should stay behaviorally aligned.
- **Usage fetch + formatting** now live in `statusline_core/api_clients.py`: `fetch_usage()`, `format_usage_session()`, `format_usage_weekly()`. OAuth token from `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`) or `CLAUDE_CODE_OAUTH_TOKEN` env.
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