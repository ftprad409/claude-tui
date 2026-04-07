"""Settings and widget loading helpers."""

import importlib.util
import json
import os

from .constants import CLAUDE_DIR

_SETTINGS_CACHE = None
_SETTINGS_MTIME = 0


def load_settings():
    global _SETTINGS_CACHE, _SETTINGS_MTIME
    path = os.path.join(os.path.expanduser("~"), CLAUDE_DIR, "claudeui.json")
    try:
        mtime = os.path.getmtime(path)
        if _SETTINGS_CACHE is not None and mtime == _SETTINGS_MTIME:
            return _SETTINGS_CACHE
        with open(path, "r") as f:
            _SETTINGS_CACHE = json.load(f)
        _SETTINGS_MTIME = mtime
    except OSError:
        _SETTINGS_CACHE = {}
    return _SETTINGS_CACHE


def get_setting(*keys, default=None):
    cfg = load_settings()
    for key in keys:
        if isinstance(cfg, dict):
            cfg = cfg.get(key)
        else:
            return default
    return cfg if cfg is not None else default


def is_visible(line, component):
    return get_setting("custom", line, component, default=True)


def load_widget(base_dir, name):
    if name == "none":
        return None
    widgets_dir = os.path.join(base_dir, "widgets")
    widget_path = os.path.join(widgets_dir, f"{name}.py")
    if not os.path.exists(widget_path):
        return None
    spec = importlib.util.spec_from_file_location(f"widgets.{name}", widget_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "render", None)
