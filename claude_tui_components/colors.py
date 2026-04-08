"""Shared ANSI colors and terminal escape codes."""

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
ORANGE = "\033[38;5;208m"
RED = "\033[31m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
GRAY = "\033[90m"

CLEAR = "\033[2J\033[H"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
ERASE_LINE = "\033[2K"
ALT_SCREEN_ON = "\033[?1049h"
ALT_SCREEN_OFF = "\033[?1049l"
LOGO_GREEN = "\033[38;5;46m"

# Matrix colors
M_DARK = "\033[38;2;0;59;0m"
M_MID = "\033[38;2;3;160;98m"
M_BRIGHT = "\033[38;2;0;255;65m"

# Activity pulse colors
PULSE_NEW = "\033[38;2;0;255;65m"  # bright green flash
PULSE_IDLE = "\033[38;2;80;80;80m"  # dim gray
