# Claude Code Commands — Session Intelligence

Custom slash commands for Claude Code that provide deep session analytics on demand.

```
/tui:session    Full session report — context, cost, tools, thinking
/tui:cost       Cost deep dive — spending breakdown, cache savings, projections
/tui:perf       Performance analysis — tool efficiency, errors, file heatmap
/tui:context    Context window analysis — growth curve, compaction timeline, predictions
```

## Installation

Copy the `tui` folder to your Claude Code commands directory:

```bash
cp -r claude-code-commands/tui ~/.claude/commands/
```

The commands become available immediately as `/tui:session`, `/tui:cost`, etc.

## Commands

### `/tui:session` — Full Session Report

Complete session overview with context usage, cost breakdown, tool activity, and thinking analysis. Start here for a quick health check.

### `/tui:cost` — Cost Analysis

Detailed spending breakdown by token category, cache savings calculation, per-turn cost trend, and budget projection based on current burn rate.

### `/tui:context` — Context Deep Dive

ASCII growth curve, compaction timeline, per-turn breakdown, growth rate analysis with multiple prediction windows (last 5/10/all turns), and actionable recommendations.

### `/tui:perf` — Performance Analysis

Tool usage with success rates, error patterns, file activity heatmap, session phase detection, and efficiency metrics.

## How It Works

Each command has a companion Python script that parses the transcript JSONL file and outputs a formatted report. The markdown command tells Claude to run the script and show the output — one Bash call, minimal token cost, consistent results.

```
tui/
├── session.md           # Command: /tui:session
├── session_report.py    # Parser + formatter
├── cost.md              # Command: /tui:cost
├── cost_report.py
├── perf.md              # Command: /tui:perf
├── perf_report.py
├── context.md           # Command: /tui:context
├── context_report.py
└── lib.py               # Shared transcript parsing library
```

The status line provides the always-visible glance. These commands provide the deep dive.

## Requirements

- Claude Code with custom commands support
- Python 3.8+, stdlib only — no external dependencies

## Conventions

- All analysis is read-only — commands never modify the transcript or project files
- Scripts auto-detect the current project's transcript from the working directory
- Output uses box-drawing characters and tables for clean terminal formatting
