# claude_tui_core — Shared Domain Logic

The centralized domain logic layer following SOLID/SRP principles. Used by every ClaudeTUI tool (statusline, monitor, sniffer, stats, manager) and injected via `PYTHONPATH` during subprocess execution by `claudetui.py`.

Responsibilities:

- **Single source of truth** for Anthropic model data (pricing, context windows) with deterministic fuzzy aliases for shorthand keys like `opus`, `sonnet3`, `haiku4`
- **External HTTP** — status page polling and OAuth usage tracking with User-Agent-aware rate-limit handling
- **Display formatting** for status and usage data
- **Configuration** — unified loader with hot-reloading via `mtime` check

The network layer uses lock-aware caching with exponential backoff (2min → 4min → 8min) and preserves cached values across rate-limited windows.

## Modules

| Module | Purpose |
|--------|---------|
| `models.py` | Model registry — pricing tables, context windows, fuzzy alias resolution |
| `network.py` | Claude status page + OAuth plan usage API with cache and backoff |
| `formatting.py` | Display formatters for status indicators and usage bars |
| `settings.py` | Shared config loader — `load_settings()` / `get_setting(*keys, default=...)` from `~/.claude/claudeui.json` |

## Claude Status Page integration

- API: Atlassian Statuspage v2 — `https://status.claude.com/api/v2/summary.json`
- Cache: `~/.claude/api-status-cache.json` with background refresh support
- Indicator shown on statusline line 2 / monitor header when not operational

## Plan usage integration

- API: OAuth usage endpoint — `https://api.anthropic.com/api/oauth/usage`
- Auth: redundant token sources (env var, credentials JSON, macOS Keychain)
- Centralized `fetch_usage()` and `format_usage_*` helpers

## Gotcha — OAuth rate limiting

The usage API frequently returns `429`. `network.py` handles exponential backoff and preserves the existing cache so UIs keep showing the last good value instead of blanking out.

## Testing

```bash
python3 claude_tui_core/test_core.py -v
```

## Requirements

- Python 3.13+, stdlib only — no external dependencies
