import os
import json

_SETTINGS_CACHE = None
_SETTINGS_MTIME = 0

def load_settings():
    """Load config from ~/.claude/claudeui.json"""
    global _SETTINGS_CACHE, _SETTINGS_MTIME
    path = os.path.join(os.path.expanduser("~"), ".claude", "claudeui.json")
    try:
        mtime = os.path.getmtime(path)
        if _SETTINGS_CACHE is not None and mtime == _SETTINGS_MTIME:
            return _SETTINGS_CACHE
        with open(path, "r") as f:
            _SETTINGS_CACHE = json.load(f)
        _SETTINGS_MTIME = mtime
    except Exception:
        _SETTINGS_CACHE = {}
    return _SETTINGS_CACHE

def get_setting(*keys, default=None):
    """Deep fetch setting."""
    cfg = load_settings()
    for key in keys:
        if isinstance(cfg, dict):
            cfg = cfg.get(key)
        else:
            return default
    return cfg if cfg is not None else default
