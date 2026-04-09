"""DisplayState — pre-computed display values passed to line builders."""

from dataclasses import dataclass, field


@dataclass
class DisplayState:
    """All pre-computed values needed to render statusline output."""
    # Identity
    model: str = ""
    session_id: str = ""
    cwd: str = ""

    # Context bar
    bar: str = ""
    tokens_str: str = ""
    limit_str: str = ""

    # Metrics
    metrics: dict = field(default_factory=dict)
    usage: object = None

    # Computed display strings
    compact_prediction: str = ""
    sparkline_part: str = ""
    cost_str: str = ""
    duration_str: str = ""
    efficiency_part: str = ""
    branch_part: str = ""
    cache_part: str = ""
    cache_pct: int = 0
    cost_per_turn: str = ""

    # Layout
    bar_length: int = 20
