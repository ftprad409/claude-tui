"""Vertical context usage meter."""

RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
ORANGE = "\033[38;5;208m"
RED = "\033[91m"
WIDTH = 7


def render(frame, ratio, **_):
    """Return 3 rows of vertical progress meter."""
    rows = []
    for row in range(3):
        # Each row represents a third: row0=67-100%, row1=34-66%, row2=0-33%
        row_min = (2 - row) / 3.0
        row_max = (3 - row) / 3.0
        if ratio >= row_max:
            fill = 1.0
        elif ratio <= row_min:
            fill = 0.0
        else:
            fill = (ratio - row_min) / (row_max - row_min)
        bar = ""
        for c in range(WIDTH):
            col_pos = (c + 0.5) / WIDTH
            bar += "█" if col_pos <= fill else " "
        if ratio < 0.50:
            color = GREEN
        elif ratio < 0.75:
            color = YELLOW
        elif ratio < 0.90:
            color = ORANGE
        else:
            color = RED
        rows.append(f"{color}{bar}{RESET}")
    return rows
