"""Equalizer bars pulsing in a wave pattern."""

RESET = "\033[0m"
GREEN = "\033[92m"
WIDTH = 7


def render(frame, ratio, **_):
    """Return 3 rows of equalizer bar animation."""
    heights_list = [
        [1, 3, 2, 1, 2, 3, 1], [2, 2, 3, 2, 3, 2, 2],
        [3, 1, 2, 3, 2, 1, 3], [2, 2, 1, 2, 1, 2, 2],
        [1, 3, 2, 1, 2, 3, 1], [2, 2, 3, 2, 3, 2, 2],
        [3, 1, 2, 3, 2, 1, 3], [2, 3, 1, 2, 1, 3, 2],
    ]
    heights = heights_list[frame % len(heights_list)]
    rows = []
    for row in range(3):
        level = 3 - row
        rows.append(
            f"{GREEN}{''.join('█' if h >= level else ' ' for h in heights)}{RESET}"
        )
    return rows
