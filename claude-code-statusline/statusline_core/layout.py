"""Terminal layout calculations — sizing, fitting, width breakpoints."""

from claude_tui_components.utils import visible_len


def calculate_bar_widths(term_cols_padded):
    """Determine bar and sparkline widths based on available terminal space."""
    if term_cols_padded <= 90:
        return 12, 8
    if term_cols_padded <= 120:
        return 16, 12
    if term_cols_padded <= 150:
        return 20, 16
    return 24, 20


def fit_parts(parts, max_width, sep_vis=3):
    """Fit line parts into available width, dropping overflow."""
    fitted = []
    used = 1  # leading space in output
    for part in parts:
        part_width = visible_len(part)
        extra = part_width if not fitted else sep_vis + part_width
        if used + extra > max_width:
            if not fitted:
                fitted.append(part)
            break
        fitted.append(part)
        used += extra
    return fitted
