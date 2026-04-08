"""Shared TUI components and widgets."""

from .colors import GREEN, YELLOW, ORANGE, RED, RESET
from .settings import get_setting  # We will need to pull get_setting

def _rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"

def _lerp_rgb(stops, t):
    t = max(0.0, min(1.0, t))
    for i in range(len(stops) - 1):
        if t <= stops[i + 1][0]:
            seg_t = (t - stops[i][0]) / (stops[i + 1][0] - stops[i][0])
            r = int(stops[i][1] + (stops[i + 1][1] - stops[i][1]) * seg_t)
            g = int(stops[i][2] + (stops[i + 1][2] - stops[i][2]) * seg_t)
            b = int(stops[i][3] + (stops[i + 1][3] - stops[i][3]) * seg_t)
            return _rgb(r, g, b)
    return _rgb(stops[-1][1], stops[-1][2], stops[-1][3])

def build_sparkline(values, width=20, mode="tail", merge_size=2):
    if not values:
        return ""
    none_indices = [i for i, v in enumerate(values) if v is None]
    keep_set = set(none_indices[-3:])
    values = [0 if (v is None and i not in keep_set) else v for i, v in enumerate(values)]
    if mode == "merge":
        merged = []
        for i in range(0, len(values), merge_size):
            bucket = values[i : i + merge_size]
            merged.append(None if None in bucket else sum(v for v in bucket if v is not None))
        values = merged[-width:] if len(merged) > width else merged
    elif len(values) > width:
        values = values[-width:]
    blocks = "▁▂▃▄▅▆▇█"
    peak = max((v for v in values if v is not None), default=1) or 1
    chars = []
    total = max(len(values), 1)
    for idx_pos, v in enumerate(values):
        recency = idx_pos / max(total - 1, 1)
        if v is None:
            # Compaction boundary marker: distinct but less loud than full red.
            # Fade older markers to reduce noise.
            marker_color = "\033[38;2;185;155;198m" if recency < 0.5 else "\033[38;2;205;170;210m"
            chars.append(f"{marker_color}↓{RESET}")
            continue
        r = v / peak
        idx = max(0, min(int(r * (len(blocks) - 1)), len(blocks) - 1))
        # Smoother, modern palette tuned for legibility in dark terminals.
        if r < 0.20:
            base = (145, 215, 165)
        elif r < 0.40:
            base = (130, 210, 205)
        elif r < 0.60:
            base = (190, 214, 155)
        elif r < 0.80:
            base = (232, 196, 140)
        else:
            base = (240, 160, 150)
        # Recency fade: older points are dimmer, latest points are brighter.
        fade = 0.65 + (0.35 * recency)
        r_ch = min(255, int(base[0] * fade))
        g_ch = min(255, int(base[1] * fade))
        b_ch = min(255, int(base[2] * fade))
        color = f"\033[38;2;{r_ch};{g_ch};{b_ch}m"
        chars.append(f"{color}{blocks[idx]}{RESET}")
    return "".join(chars)

def build_progress_bar(ratio, length=20, compact_ratio=None, pct_label=""):
    ratio = max(0.0, min(ratio, 1.0))
    precise_fill = ratio * length
    full_cells = int(precise_fill)
    remainder = precise_fill - full_cells
    partials = "▏▎▍▌▋▊▉"
    full_char = "▮"
    empty_char = "▯"
    stops = [
        (0.00, 166, 227, 161),
        (0.30, 148, 226, 213),
        (0.55, 249, 226, 175),
        (0.80, 250, 179, 135),
        (1.00, 243, 139, 168),
    ]
    empty_color = "\033[38;2;55;59;80m"
    head_color = "\033[38;2;214;226;240m"

    bar_parts = []
    for i in range(length):
        pos = i / max(length - 1, 1)
        if i < full_cells:
            bar_parts.append(f"{_lerp_rgb(stops, pos)}{full_char}{RESET}")
            continue
        if i == full_cells and remainder > 0:
            partial_idx = max(0, min(int(remainder * len(partials)) - 1, len(partials) - 1))
            bar_parts.append(f"{_lerp_rgb(stops, pos)}{partials[partial_idx]}{RESET}")
            continue
        bar_parts.append(f"{empty_color}{empty_char}{RESET}")

    # Subtle head marker on the active frontier for easier visual tracking.
    if 0 < precise_fill < length and remainder == 0:
        head_idx = min(full_cells, length - 1)
        bar_parts[head_idx] = f"{head_color}▌{RESET}"

    # Compact ceiling tick marker (when available) to show compaction threshold.
    if compact_ratio and 0 < compact_ratio < 1:
        tick_idx = min(max(int(compact_ratio * length), 0), length - 1)
        if tick_idx >= full_cells:
            bar_parts[tick_idx] = f"\033[38;2;140;145;170m┆{RESET}"

    bar = "".join(bar_parts)
    bar = f"\033[38;2;90;95;120m▏{RESET}{bar}\033[38;2;90;95;120m▕{RESET}"
    fill_of_ceiling = ratio / compact_ratio if compact_ratio and compact_ratio > 0 else ratio
    if fill_of_ceiling < 0.60:
        pct_color = GREEN
    elif fill_of_ceiling < 0.85:
        pct_color = YELLOW
    elif fill_of_ceiling < 0.95:
        pct_color = ORANGE
    else:
        pct_color = RED
    pct_value = int(ratio * 100)
    if pct_label:
        pct_text = f"{pct_label} {pct_value:>2}%"
    else:
        pct_text = f"{pct_value:>3}%"
    return f"{bar} {pct_color}{pct_text}{RESET}"
