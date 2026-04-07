"""Lightweight debug logging for statusline internals."""

import os
import sys


def debug_log(message):
    """Write debug output only when STATUSLINE_DEBUG is enabled."""
    if os.environ.get("STATUSLINE_DEBUG", "").lower() in ("1", "true", "yes", "on"):
        sys.stderr.write(f"[statusline] {message}\n")
