# AGENTS.md

## Quick Commands

```bash
python3 claudetui.py monitor         # test monitor
python3 claudetui.py chart           # test efficiency chart
python3 claudetui.py mode custom      # test configurator TUI
python3 claudetui.py --help           # test dispatcher
python3 claude-code-monitor/test_monitor.py -v   # monitor tests
python3 claude-code-sniffer/test_sniffer.py -v   # sniffer tests
PYTHONPATH=. python3 claude_tui_core/test_core.py -v   # core tests
```

## Testing

Only two tools have tests: monitor and sniffer. Run both before/after refactoring.

## Gotchas (hard-won knowledge)

- **`python3 claudetui.py uninstall`** removes the repo directory! Use with caution - it deletes the git repo folder

- **Usage API rate limiting**: The OAuth usage API frequently returns 429 errors. `claude_tui_core/network.py` handles exponential backoff (2min → 4min → 8min) and preserves cache.

- **Model Data**: Centralized in `claude_tui_core/models.py`. Keep in sync when updating pricing.
- **Status Page / Usage**: Centralized logic in `claude_tui_core/network.py`.
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
- Shared UI Library: `claude_tui_components/` (holds `build_progress_bar`, `build_sparkline`, ANSI colors, string utils)
- Shared Logic Core: `claude_tui_core/` (models, network, settings/hot-reload)
- Each tool in its own directory, self-contained with README
- Config: `~/.claude/claudeui.json` (hot-reloads, no restart needed)
- Tests: `claude_tui_core/test_core.py` (unit tests for core library)

## Personal Notes

- Forked for learning purposes; upstream is slima4/claude-tui
- Before pulling upstream changes, check `claude_tui_core/models.py` for pricing drift — that file changes often
