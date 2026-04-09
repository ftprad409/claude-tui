#!/bin/bash
# Generate coverage report for SonarQube
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# Clean previous data
rm -f "$ROOT/.coverage" "$ROOT/.coverage.*" "$ROOT/coverage.xml"

# Run each test suite with coverage
PYTHONPATH="$ROOT" coverage run --data-file="$ROOT/.coverage.core" \
    --source="$ROOT/claude_tui_core,$ROOT/claude_tui_components" \
    -m unittest claude_tui_core.test_core claude_tui_components.test_components

PYTHONPATH="$ROOT/claude-code-statusline:$ROOT" coverage run --data-file="$ROOT/.coverage.statusline" \
    --source="$ROOT/claude-code-statusline/statusline_core,$ROOT/claude_tui_core,$ROOT/claude_tui_components" \
    -m unittest discover -s "$ROOT/claude-code-statusline" -p "test_*.py"

PYTHONPATH="$ROOT/claude-code-monitor:$ROOT" coverage run --data-file="$ROOT/.coverage.monitor" \
    --source="$ROOT/claude-code-monitor,$ROOT/claude_tui_core,$ROOT/claude_tui_components" \
    -m unittest discover -s "$ROOT/claude-code-monitor" -p "test_*.py"

# Combine and report
cd "$ROOT"
coverage combine .coverage.core .coverage.statusline .coverage.monitor
coverage xml -o coverage.xml
coverage report --show-missing
