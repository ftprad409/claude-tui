"""
claude_tui_core — shared domain logic layer.

Each sub-module has a single responsibility (SRP):
  models   — Anthropic model registry (pricing, context windows)
  network  — External HTTP communication (status page, OAuth usage)
  settings — Config file loading with hot-reload
"""
from .models import (
    MODEL_PRICING,
    MODEL_CONTEXT_WINDOW,
    DEFAULT_CONTEXT_LIMIT,
    COMPACT_BUFFER,
    get_context_limit,
    get_model_pricing,
    get_model_pricing_fuzzy,
)
from .settings import load_settings, get_setting, reset_settings_cache
from .network import (
    fetch_api_status,
    fetch_usage,
)
from .formatting import (
    format_api_status,
    format_usage_session,
    format_usage_weekly,
)

__all__ = [
    "MODEL_PRICING",
    "MODEL_CONTEXT_WINDOW",
    "DEFAULT_CONTEXT_LIMIT",
    "COMPACT_BUFFER",
    "get_context_limit",
    "get_model_pricing",
    "get_model_pricing_fuzzy",
    "load_settings",
    "get_setting",
    "reset_settings_cache",
    "fetch_api_status",
    "format_api_status",
    "fetch_usage",
    "format_usage_session",
    "format_usage_weekly",
]
