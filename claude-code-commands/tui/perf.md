---
description: Performance analysis — tool efficiency, error patterns, file activity heatmap, thinking usage
allowed-tools: Bash
---

Run the performance report script and present the output to the user. Do not add commentary — just show the report.

```bash
python3 "$(python3 -c "import os; print(os.path.dirname(os.path.realpath(os.path.expanduser('~/.claude/commands/tui/perf.md'))))")/perf_report.py"
```

If the above path fails, try:

```bash
python3 ~/.claude/commands/tui/perf_report.py
```

Show the output as-is in a code block. If there are notable findings (high error rates, unusual tool patterns, efficiency concerns), add a brief 1-2 sentence summary after the report.
