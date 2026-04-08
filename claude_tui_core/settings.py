"""
Single source of truth for loading and reading ~/.claude/claudeui.json.
Supports automatic hot-reloading based on file mtime.
"""

import json
import os

_SETTINGS_CACHE = None
_SETTINGS_MTIME = 0

def load_settings():
    """
    Load shared settings from ~/.claude/claudeui.json.
    Re-reads the file if it has been modified since last load.
    """
    global _SETTINGS_CACHE, _SETTINGS_MTIME
    path = os.path.join(os.path.expanduser("~"), ".claude", "claudeui.json")
    try:
        mtime = os.path.getmtime(path)
        if _SETTINGS_CACHE is not None and mtime == _SETTINGS_MTIME:
            return _SETTINGS_CACHE
        with open(path, "r") as f:
            _SETTINGS_CACHE = json.load(f)
        _SETTINGS_MTIME = mtime
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _SETTINGS_CACHE = {}
    return _SETTINGS_CACHE

def get_setting(*keys, default=None):
    """
    Get a nested setting value.
    Example: get_setting('sparkline', 'mode')
    """
    cfg = load_settings()
    for key in keys:
        if isinstance(cfg, dict):
            cfg = cfg.get(key)
        else:
            return default
    return cfg if cfg is not None else default

def reset_settings_cache():
    """Reset settings cache so the next access re-reads from disk."""
    global _SETTINGS_CACHE, _SETTINGS_MTIME
    _SETTINGS_CACHE = None
    _SETTINGS_MTIME = 0
