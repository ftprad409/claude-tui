"""Shared constants for statusline modules."""

# Base paths / protocol constants
CLAUDE_DIR = ".claude"
APPLICATION_JSON = "application/json"
UTC_OFFSET = "+00:00"

# Context window sizes by model family
MODEL_CONTEXT_WINDOW = {
    "claude-opus-4": 1_000_000,
}
DEFAULT_CONTEXT_LIMIT = 200_000
COMPACT_BUFFER = 33_000

# Pricing per million tokens
MODEL_PRICING = {
    "claude-opus-4-6": {
        "input": 15.0,
        "cache_read": 1.5,
        "cache_write": 18.75,
        "output": 75.0,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
        "output": 15.0,
    },
    "claude-haiku-4-5": {
        "input": 0.80,
        "cache_read": 0.08,
        "cache_write": 1.0,
        "output": 4.0,
    },
    "claude-sonnet-3-5": {
        "input": 3.0,
        "cache_read": 0.30,
        "cache_write": 3.75,
        "output": 15.0,
    },
    "claude-haiku-3-5": {
        "input": 0.80,
        "cache_read": 0.08,
        "cache_write": 1.0,
        "output": 4.0,
    },
}

# ANSI colors
from claude_tui_components.colors import (
    RESET, BOLD, GREEN, YELLOW, ORANGE, RED, CYAN, MAGENTA, WHITE, GRAY
)
