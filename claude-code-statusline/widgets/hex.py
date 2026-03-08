"""Hex rain with true RGB Matrix colors."""

RESET = "\033[0m"
WIDTH = 7


def render(frame, ratio, **_):
    """Return 3 rows of hex rain animation."""
    rain = "4F7A1D03E9B85C2F6A0E7D1B39C5F82A4"
    speeds = [1, 3, 2, 1, 2, 3, 1]
    dark = "\033[38;2;0;59;0m"       # #003B00
    mid = "\033[38;2;3;160;98m"      # #03A062
    bright = "\033[38;2;0;255;65m"   # #00FF41
    colors = [dark, dark, mid, mid, bright]
    rows = []
    for r in range(3):
        line = []
        for c in range(WIDTH):
            idx = c * 5 + r - frame * speeds[c % len(speeds)]
            ch = rain[idx % len(rain)]
            cidx = c * 3 + r - frame * speeds[c % len(speeds)]
            color = colors[cidx % len(colors)]
            line.append(f"{color}{ch}{RESET}")
        rows.append("".join(line))
    return rows
