"""
Single source of truth for all Anthropic model domain data.
Owns: MODEL_PRICING, MODEL_CONTEXT_WINDOW, COMPACT_BUFFER, get_context_limit(), get_model_pricing()
"""

# Context window sizes by model family
MODEL_CONTEXT_WINDOW = {
    "claude-opus-4": 1_000_000,
}
DEFAULT_CONTEXT_LIMIT = 200_000
COMPACT_BUFFER = 33_000

# Pricing per million tokens (input, cache_read, cache_write, output)
# Note: cache_write is typically 1.25x input price
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


def get_context_limit(model_id: str) -> int:
    """Resolve context window for any model ID string."""
    for key, limit in MODEL_CONTEXT_WINDOW.items():
        if key in model_id:
            return limit
    return DEFAULT_CONTEXT_LIMIT


def get_model_pricing(model_id: str) -> dict:
    """Resolve pricing dictionary for a model ID. Falls back to Sonnet 4.6."""
    for key, pricing in MODEL_PRICING.items():
        if key in model_id:
            return pricing
    return MODEL_PRICING["claude-sonnet-4-6"]


def get_model_pricing_fuzzy(model_id: str) -> dict:
    """
    Fuzzy pricing lookup for abbreviated model keys (used by sniffer).
    Handles 'claude-sonnet-4' -> 'claude-sonnet-4-6', etc.
    """
    if not model_id:
        return MODEL_PRICING["claude-sonnet-4-6"]
    
    m = model_id.lower().replace("-", "")
    
    # Try exact-ish match first
    for key, pricing in MODEL_PRICING.items():
        if key.replace("-", "") in m:
            return pricing
            
    # Try prefix matching
    for key, pricing in MODEL_PRICING.items():
        # Strip trailing version/date parts for broader matching
        # e.g. 'claude-sonnet-4-6' -> 'claudesonnet4'
        base_key = key.lower().replace("-", "")
        if base_key.startswith(m) or m.startswith(base_key[:12]):
            return pricing
            
    return MODEL_PRICING["claude-sonnet-4-6"]
