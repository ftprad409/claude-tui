# claude_tui_components — Shared UI Library

A centralized UI library containing all shared visual elements used across ClaudeTUI tools: progress bars, sparklines, line-level widgets, string truncation utilities, and color sequences.

It is dynamically injected via `PYTHONPATH` during subprocess execution by `claudetui.py`, ensuring a single unified true-color aesthetic across the statusline, monitor dashboard, and interactive configurator (`mode custom`) without code duplication.

## Modules

| Module | Purpose |
|--------|---------|
| `colors.py` | True-color RGB sequences, palette constants, and `_lerp_rgb` gradient helper |
| `lines.py` | Line-level widgets — progress bars, sparklines, status chips |
| `widgets.py` | 3×7 grid widgets (`matrix`, `hex`, `bars`, `progress`, `none`) for the statusline left-side animation |
| `utils.py` | String truncation, width-aware padding, and display helpers |
| `settings.py` | Component-level settings helpers shared with `claude_tui_core.settings` |

## Widget API

Widget functions have signature `widget_fn(frame, ratio) -> list[str]` and must return exactly 3 rows.

## Testing

```bash
python3 claude_tui_components/test_components.py -v
```

## Requirements

- Python 3.13+, stdlib only — no external dependencies
