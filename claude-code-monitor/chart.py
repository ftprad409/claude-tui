"""Efficiency chart — token waste visualization per segment.

Usage:
    python3 chart.py              # auto-detect latest session
    python3 chart.py <session-id> # chart for specific session

Hotkeys:
    v  toggle horizontal/vertical view
    q  close
"""

import os
import select
import shutil
import sys
import termios
import tty

from lib import (
    CONTEXT_LIMIT, DEFAULT_CONTEXT_LIMIT, RESET, BOLD, DIM, GREEN, YELLOW, ORANGE, RED, CYAN, GRAY,
    CLEAR, HIDE_CURSOR, SHOW_CURSOR, ALT_SCREEN_ON, ALT_SCREEN_OFF,
    format_tokens, get_terminal_width, parse_transcript,
    find_transcript, find_session_by_id, efficiency_color,
)

# ── Chart constants ───────────────────────────────────────────────────

EFFICIENCY_LEGEND = f"    {CYAN}▒▒{RESET} system   {YELLOW}▓▓{RESET} summary   {GREEN}██{RESET} useful   {GRAY}░░{RESET} headroom"


# ── Segment building ─────────────────────────────────────────────────

def _build_segments(r, context_limit=None):
    """Build per-segment data from compact_events and current context.

    Each segment shows:
      - system: system prompt tokens (constant overhead, always present)
      - summary: compaction summary tokens (actual compaction cost)
      - useful: new tokens added during this segment
      - headroom: reserved space for compaction (context_limit - peak)
      - peak: total context at end of segment
    """
    if context_limit is None:
        context_limit = r.get("context_limit", DEFAULT_CONTEXT_LIMIT)
    segments = []
    num_compactions = 0
    sys_prompt = r.get("system_prompt_tokens", 0)
    prev_rebuild = 0
    prev_system = sys_prompt  # first segment also has system prompt
    for evt in r["compact_events"]:
        peak = evt["context_before"]
        headroom = max(0, context_limit - peak)
        summary = max(0, prev_rebuild - prev_system)
        useful = peak - prev_system - summary  # exclude system & summary shown separately
        segments.append({
            "peak": peak,
            "useful": max(useful, 0),
            "system": prev_system,
            "summary": summary,
            "headroom": headroom,
        })
        num_compactions += 1
        prev_rebuild = evt.get("context_after", 0)
        prev_system = evt.get("system_prompt", sys_prompt)
    # Current (active) segment — only if post-compaction data exists
    current = r["last_context"]
    if current > 0 and (num_compactions == 0 or prev_rebuild > 0):
        summary = max(0, prev_rebuild - prev_system)
        useful = current - prev_system - summary
        segments.append({
            "peak": current,
            "useful": max(useful, 0),
            "system": prev_system,
            "summary": summary,
            "headroom": 0,  # active segment — still growing
            "active": True,
        })
    return segments, num_compactions


# ── Horizontal chart ──────────────────────────────────────────────────

def _render_horizontal_chart(segments, num_compactions, w, context_limit=DEFAULT_CONTEXT_LIMIT):
    """Render horizontal bar chart of segments."""
    lines = []
    # Scale all bars to context_limit so completed segments show full width
    scale_ref = context_limit
    bar_width = max(20, w - 24)

    lines.append(f"  {BOLD}CONTEXT EFFICIENCY — HORIZONTAL{RESET}")
    lines.append("")
    lines.append(EFFICIENCY_LEGEND)
    lines.append("")

    for i, seg in enumerate(segments):
        is_active = seg.get("active", False)
        label = f"{'→ ' if is_active else '  '}Seg {i + 1}"
        has_compaction = i < num_compactions

        system_t = seg["system"]
        summary_t = seg["summary"]
        useful_t = seg["useful"]
        headroom_t = seg["headroom"]
        total_t = system_t + summary_t + useful_t + headroom_t

        scale = bar_width / scale_ref if scale_ref > 0 else 1
        total_bar = min(int(total_t * scale), bar_width)
        if total_bar == 0 and total_t > 0:
            total_bar = 1

        # Distribute bar proportionally
        if total_t > 0:
            system_w = int(total_bar * system_t / total_t)
            summary_w = int(total_bar * summary_t / total_t)
            headroom_w = int(total_bar * headroom_t / total_t)
            useful_w = total_bar - system_w - summary_w - headroom_w
        else:
            system_w = summary_w = useful_w = headroom_w = 0
        # Ensure minimum 1 char for non-zero components
        if system_t > 0 and system_w == 0:
            system_w = 1
            useful_w = max(0, useful_w - 1)
        if summary_t > 0 and summary_w == 0:
            summary_w = 1
            useful_w = max(0, useful_w - 1)
        if headroom_t > 0 and headroom_w == 0:
            headroom_w = 1
            useful_w = max(0, useful_w - 1)

        bar = ""
        if system_w > 0:
            bar += f"{CYAN}{'▒' * system_w}{RESET}"
        if summary_w > 0:
            bar += f"{YELLOW}{'▓' * summary_w}{RESET}"
        if useful_w > 0:
            bar += f"{GREEN}{'█' * useful_w}{RESET}"
        if headroom_w > 0:
            bar += f"{GRAY}{'░' * headroom_w}{RESET}"

        peak_str = format_tokens(int(seg["peak"]))
        if not is_active:
            lines.append(f"  {BOLD}{label}{RESET}  {bar}  {GRAY}{format_tokens(context_limit)}{RESET}")
        else:
            lines.append(f"  {BOLD}{label}{RESET}  {bar}  {GRAY}{peak_str}{RESET}")

        # Detail line
        parts = []
        if system_t > 0:
            parts.append(f"{CYAN}{format_tokens(int(system_t))}{RESET} system")
        if summary_t > 0:
            parts.append(f"{YELLOW}{format_tokens(int(summary_t))}{RESET} summary")
        parts.append(f"{GREEN}{format_tokens(int(useful_t))}{RESET} useful")
        if headroom_t > 0:
            parts.append(f"{GRAY}{format_tokens(int(headroom_t))}{RESET} headroom")
        if has_compaction:
            parts.append(f"{DIM}→ compacted{RESET}")
        detail = f"{DIM} │ {RESET}".join(parts)
        lines.append(f"          {detail}")

        # Compaction marker between segments
        if has_compaction:
            lines.append(f"          {DIM}──── compact #{i + 1} ────{RESET}")

    return lines


# ── Vertical chart ────────────────────────────────────────────────────

def _render_vertical_chart(segments, num_compactions, w, h, context_limit=DEFAULT_CONTEXT_LIMIT):
    """Render vertical stacked bar chart of segments."""
    lines = []
    scale_ref = context_limit
    chart_height = max(8, min(h - 12, 20))
    col_width = max(6, min(12, (w - 10) // max(len(segments), 1)))
    num_cols = min(len(segments), (w - 10) // col_width)

    lines.append(f"  {BOLD}CONTEXT EFFICIENCY — VERTICAL{RESET}")
    lines.append("")
    lines.append(EFFICIENCY_LEGEND)
    lines.append("")

    # Build columns: system (bottom) + summary + useful + headroom (top)
    cols = []
    display_segs = segments[-num_cols:]
    for seg in display_segs:
        total_t = seg["system"] + seg["summary"] + seg["useful"] + seg["headroom"]
        total_rows = int(total_t / scale_ref * chart_height) if scale_ref > 0 else 0
        system_rows = int(seg["system"] / scale_ref * chart_height) if scale_ref > 0 else 0
        summary_rows = int(seg["summary"] / scale_ref * chart_height) if scale_ref > 0 else 0
        headroom_rows = int(seg["headroom"] / scale_ref * chart_height) if scale_ref > 0 else 0
        useful_rows = total_rows - system_rows - summary_rows - headroom_rows
        if useful_rows < 0:
            useful_rows = 0
        cols.append((system_rows, summary_rows, useful_rows, headroom_rows, total_rows))

    # Y-axis labels
    for row in range(chart_height, 0, -1):
        if row == chart_height:
            y_label = format_tokens(context_limit)
        elif row == chart_height // 2:
            y_label = format_tokens(context_limit // 2)
        elif row == 1:
            y_label = "0"
        else:
            y_label = ""
        line = f"  {GRAY}{y_label:>6s}{RESET} │"

        for ci, (sy, sm, us, hr, _) in enumerate(cols):
            # From bottom: system (cyan), summary (yellow), useful (green), headroom (gray)
            bar_char = "    "
            if row <= sy:
                bar_char = f"{CYAN}▒▒▒▒{RESET}"
            elif row <= sy + sm:
                bar_char = f"{YELLOW}▓▓▓▓{RESET}"
            elif row <= sy + sm + us:
                bar_char = f"{GREEN}████{RESET}"
            elif row <= sy + sm + us + hr:
                bar_char = f"{GRAY}░░░░{RESET}"
            padding = " " * max(0, col_width - 4)
            line += bar_char + padding
        lines.append(line)

    # X-axis
    x_axis = f"  {'':>6s} └"
    for i in range(len(cols)):
        x_axis += f"{'─' * col_width}"
    lines.append(x_axis)

    # Labels
    label_line = f"  {'':>6s}  "
    for i in range(len(cols)):
        seg_idx = len(segments) - num_cols + i
        is_active = segments[seg_idx].get("active", False)
        label = f"{'→' if is_active else 'S'}{seg_idx + 1}"
        label_line += f"{BOLD}{label}{RESET}" + " " * max(0, col_width - len(label))
    lines.append(label_line)

    # Peak values
    peak_line = f"  {'':>6s}  "
    for i in range(len(cols)):
        seg_idx = len(segments) - num_cols + i
        pk = format_tokens(int(segments[seg_idx]["peak"]))
        peak_line += f"{GRAY}{pk}{RESET}" + " " * max(0, col_width - len(pk))
    lines.append(peak_line)

    return lines


# ── Info overlay ─────────────────────────────────────────────────────

def _show_info(out):
    """Show info overlay explaining chart metrics."""
    lines = [
        "",
        f"  {BOLD}WHAT DO THESE METRICS MEAN?{RESET}",
        "",
        f"  Every time you press Enter, Claude Code sends to the API:",
        f"  {DIM}system prompt + full conversation history + your new message{RESET}",
        f"  The API is stateless — it re-reads everything on every call.",
        "",
        f"  {BOLD}Bar components:{RESET}",
        "",
        f"  {CYAN}▒▒ system{RESET}     System prompt: Claude Code instructions, tool definitions,",
        f"               CLAUDE.md. Constant ~14k tokens on every API call.",
        f"               Cached server-side at 10x discount. Can't avoid it.",
        "",
        f"  {YELLOW}▓▓ summary{RESET}   Compaction summary: after compaction, Claude re-reads a",
        f"               compressed version of your conversation (~11-19k tokens).",
        f"               This is the real overhead of compaction.",
        "",
        f"  {GREEN}██ useful{RESET}    Your actual work: prompts, responses, tool calls, code",
        f"               edits, file reads, test output.",
        "",
        f"  {GRAY}░░ headroom{RESET}  Unused capacity. Compaction triggers at ~83% of context window.",
        f"               The remaining ~33k is reserved for the compaction process.",
        "",
        f"  {BOLD}Summary line:{RESET}",
        "",
        f"  {BOLD}Efficiency{RESET}   % of tokens that were useful work.",
        f"               100% = no compactions. Drops with each compaction.",
        f"  {BOLD}Wasted{RESET}       Summaries + headroom across all compactions.",
        f"               {DIM}(system prompt excluded — it's constant overhead, not waste){RESET}",
        f"  {BOLD}Segments{RESET}     One per compaction cycle + the current active segment.",
        "",
        f"  {DIM}Press any key to return{RESET}",
        "",
    ]
    out.write(CLEAR + "\n".join(lines))
    out.flush()
    # Wait for any key
    while True:
        if select.select([sys.stdin], [], [], 0.1)[0]:
            os.read(sys.stdin.fileno(), 1)
            while select.select([sys.stdin], [], [], 0.01)[0]:
                os.read(sys.stdin.fileno(), 1)
            return


# ── Interactive chart viewer ──────────────────────────────────────────

def show_efficiency_chart(r, term_width, transcript_path=None):
    """Interactive efficiency chart with horizontal/vertical toggle and live updates."""
    out = sys.stdout
    mode = "horizontal"
    term_height = shutil.get_terminal_size().lines
    last_mtime = 0
    if transcript_path:
        try:
            last_mtime = os.path.getmtime(transcript_path)
        except OSError:
            pass

    # Initial build
    segments, num_compactions = _build_segments(r)
    if not segments:
        return
    total_wasted = r["tokens_wasted"]
    total_built = r["total_context_built"] + r["last_context"]
    eff_pct = int(max(0, 1 - total_wasted / total_built) * 100) if total_built > 0 else 100

    while True:
        # Re-parse transcript if file changed
        if transcript_path:
            try:
                mtime = os.path.getmtime(transcript_path)
            except OSError:
                mtime = last_mtime
            if mtime != last_mtime:
                last_mtime = mtime
                r = parse_transcript(transcript_path)
                segments, num_compactions = _build_segments(r)
                if not segments:
                    return
                total_wasted = r["tokens_wasted"]
                total_built = r["total_context_built"] + r["last_context"]
                eff_pct = int(max(0, 1 - total_wasted / total_built) * 100) if total_built > 0 else 100

        lines = []
        lines.append("")
        w = term_width - 2

        ctx_limit = r.get("context_limit", DEFAULT_CONTEXT_LIMIT)
        if mode == "horizontal":
            lines.extend(_render_horizontal_chart(segments, num_compactions, w, context_limit=ctx_limit))
        else:
            lines.extend(_render_vertical_chart(segments, num_compactions, w, term_height, context_limit=ctx_limit))

        # Summary
        lines.append("")
        eff_color = efficiency_color(eff_pct)
        wasted_str = format_tokens(int(total_wasted)) if total_wasted > 0 else "0"
        total_str = format_tokens(int(total_built))
        lines.append(f"  {BOLD}Efficiency:{RESET} {eff_color}{eff_pct}%{RESET}  {DIM}│{RESET}  {DIM}Wasted:{RESET} {RED}{wasted_str}{RESET}{DIM}/{RESET}{GRAY}{total_str}{RESET}  {DIM}│{RESET}  {DIM}Segments:{RESET} {CYAN}{len(segments)}{RESET}  {DIM}│{RESET}  {DIM}Compactions:{RESET} {CYAN}{r['compact_count']}{RESET}")
        lines.append("")
        lines.append(f"  {DIM}v{RESET} toggle view  {DIM}?{RESET} info  {DIM}q{RESET} close")
        lines.append("")

        out.write(CLEAR + "\n".join(lines))
        out.flush()

        # Wait for keypress or file change (poll every 0.5s)
        while True:
            if select.select([sys.stdin], [], [], 0.5)[0]:
                byte = os.read(sys.stdin.fileno(), 1)
                # Drain escape sequence
                while select.select([sys.stdin], [], [], 0.01)[0]:
                    os.read(sys.stdin.fileno(), 1)
                key = byte.decode("utf-8", errors="ignore")
                if key in ("v", "V"):
                    mode = "vertical" if mode == "horizontal" else "horizontal"
                    term_width = get_terminal_width()
                    term_height = shutil.get_terminal_size().lines
                    break
                elif key == "?":
                    _show_info(out)
                    break  # redraw chart after info dismissed
                elif key in ("q", "Q", "\x1b"):
                    return
            # Check for file changes
            if transcript_path:
                try:
                    mtime = os.path.getmtime(transcript_path)
                except OSError:
                    mtime = last_mtime
                if mtime != last_mtime:
                    break  # re-render with fresh data


# ── Standalone entry point ────────────────────────────────────────────

def run_standalone(session_id=None):
    """Run efficiency chart as a standalone screen — no monitor chrome."""
    # Find transcript
    if session_id:
        path = find_session_by_id(session_id)
        if not path:
            print(f"Session '{session_id}' not found. Use --list to see sessions.")
            sys.exit(1)
    else:
        path = find_transcript()
        if not path:
            print("No active session found. Use --list or pass a session ID.")
            sys.exit(1)

    # Parse
    r = parse_transcript(path)
    if not r:
        print("Failed to parse transcript.")
        sys.exit(1)

    if not r["compact_events"]:
        print("No compactions yet — efficiency is 100%.")
        return

    # Enter raw mode and alt screen
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR)
        sys.stdout.flush()
        term_width = get_terminal_width()
        show_efficiency_chart(r, term_width, transcript_path=path)
    finally:
        sys.stdout.write(SHOW_CURSOR + ALT_SCREEN_OFF)
        sys.stdout.flush()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else None
    run_standalone(sid)
