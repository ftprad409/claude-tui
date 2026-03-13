#!/usr/bin/env python3
"""Tests for monitor.py — run before/after refactoring to verify no regressions.

Usage: python3 -m pytest claude-code-monitor/test_monitor.py -v
   or: python3 claude-code-monitor/test_monitor.py
"""
import json
import os
import re
import sys
import tempfile
import unittest

# Import monitor module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# We need to import the module carefully since it has side effects at module level
# Parse the file to extract functions without running main()
_monitor_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.py")
_module_code = open(_monitor_path).read()
# Execute everything up to main() to get all function definitions
_ns = {"__name__": "monitor", "__file__": _monitor_path}
exec(compile(_module_code.split("\ndef main(")[0], _monitor_path, "exec"), _ns)

# Pull out the functions and constants we need to test
CONTEXT_LIMIT = _ns["CONTEXT_LIMIT"]
format_tokens = _ns["format_tokens"]
_build_segments = _ns["_build_segments"]
_render_horizontal_chart = _ns["_render_horizontal_chart"]
_render_vertical_chart = _ns["_render_vertical_chart"]
parse_transcript = _ns["parse_transcript"]

# Strip ANSI escape codes for assertion comparisons
def strip_ansi(s):
    return re.sub(r"\033\[[^m]*m", "", s)


# ── format_tokens ─────────────────────────────────────────────────────

class TestFormatTokens(unittest.TestCase):
    def test_millions(self):
        self.assertEqual(format_tokens(1_500_000), "1.5M")
        self.assertEqual(format_tokens(2_000_000), "2.0M")

    def test_thousands(self):
        self.assertEqual(format_tokens(167_000), "167.0k")
        self.assertEqual(format_tokens(33_100), "33.1k")
        self.assertEqual(format_tokens(1_000), "1.0k")

    def test_small(self):
        self.assertEqual(format_tokens(999), "999")
        self.assertEqual(format_tokens(0), "0")


# ── _build_segments ───────────────────────────────────────────────────

class TestBuildSegments(unittest.TestCase):
    def test_no_compactions_active_segment(self):
        """Fresh session with no compactions — single active segment."""
        r = {
            "compact_events": [],
            "last_context": 80_000,
        }
        segs, comps = _build_segments(r)
        self.assertEqual(len(segs), 1)
        self.assertEqual(len(comps), 0)
        seg = segs[0]
        self.assertTrue(seg["active"])
        self.assertEqual(seg["peak"], 80_000)
        self.assertEqual(seg["useful"], 80_000)
        self.assertEqual(seg["rebuild"], 0)
        self.assertEqual(seg["headroom"], 0)

    def test_no_compactions_zero_context(self):
        """No compactions, no context yet — no segments."""
        r = {
            "compact_events": [],
            "last_context": 0,
        }
        segs, comps = _build_segments(r)
        self.assertEqual(len(segs), 0)
        self.assertEqual(len(comps), 0)

    def test_one_compaction(self):
        """Single compaction: Seg 1 (completed) + Seg 2 (active)."""
        r = {
            "compact_events": [
                {"context_before": 167_000, "context_after": 33_100},
            ],
            "last_context": 80_000,
        }
        segs, comps = _build_segments(r)

        # Two segments: one completed, one active
        self.assertEqual(len(segs), 2)
        self.assertEqual(len(comps), 1)

        # Seg 1: all useful (first segment, no rebuild)
        s1 = segs[0]
        self.assertEqual(s1["peak"], 167_000)
        self.assertEqual(s1["useful"], 167_000)
        self.assertEqual(s1["rebuild"], 0)
        self.assertEqual(s1["headroom"], CONTEXT_LIMIT - 167_000)  # 33k
        self.assertNotIn("active", s1)

        # Compaction: lost + survived
        c1 = comps[0]
        self.assertEqual(c1["survived"], 33_100)
        self.assertEqual(c1["lost"], 167_000 - 33_100)  # 133.9k

        # Seg 2: active, rebuild = survived from compaction
        s2 = segs[1]
        self.assertTrue(s2["active"])
        self.assertEqual(s2["peak"], 80_000)
        self.assertEqual(s2["rebuild"], 33_100)
        self.assertEqual(s2["useful"], 80_000 - 33_100)  # 46.9k
        self.assertEqual(s2["headroom"], 0)  # active segment

    def test_two_compactions(self):
        """Two compactions: 3 segments."""
        r = {
            "compact_events": [
                {"context_before": 167_000, "context_after": 33_100},
                {"context_before": 147_500, "context_after": 24_900},
            ],
            "last_context": 50_000,
        }
        segs, comps = _build_segments(r)
        self.assertEqual(len(segs), 3)
        self.assertEqual(len(comps), 2)

        # Seg 1: no rebuild, all useful
        self.assertEqual(segs[0]["rebuild"], 0)
        self.assertEqual(segs[0]["useful"], 167_000)
        self.assertEqual(segs[0]["headroom"], CONTEXT_LIMIT - 167_000)

        # Seg 2: rebuild from compaction 1
        self.assertEqual(segs[1]["rebuild"], 33_100)
        self.assertEqual(segs[1]["useful"], 147_500 - 33_100)  # 114.4k
        self.assertEqual(segs[1]["headroom"], CONTEXT_LIMIT - 147_500)

        # Seg 3: rebuild from compaction 2
        self.assertEqual(segs[2]["rebuild"], 24_900)
        self.assertEqual(segs[2]["useful"], 50_000 - 24_900)  # 25.1k
        self.assertTrue(segs[2]["active"])

    def test_segment_useful_never_negative(self):
        """If current context <= rebuild, useful should be 0 not negative."""
        r = {
            "compact_events": [
                {"context_before": 167_000, "context_after": 33_100},
            ],
            "last_context": 33_100,  # exactly rebuild, no new work
        }
        segs, _ = _build_segments(r)
        self.assertEqual(segs[1]["useful"], 0)

    def test_compaction_without_context_after(self):
        """Compaction event missing context_after defaults to 0."""
        r = {
            "compact_events": [
                {"context_before": 167_000},  # no context_after
            ],
            "last_context": 50_000,
        }
        segs, comps = _build_segments(r)
        self.assertEqual(comps[0]["survived"], 0)
        self.assertEqual(comps[0]["lost"], 0)  # survived=0 → lost guard
        self.assertEqual(segs[1]["rebuild"], 0)


# ── Waste model ───────────────────────────────────────────────────────

class TestWasteModel(unittest.TestCase):
    """Test the headroom + rebuild waste calculation."""

    def _compute_waste(self, compact_events, last_context):
        """Simulate waste calculation matching parse_transcript logic."""
        tokens_wasted = 0
        total_context_built = 0
        for evt in compact_events:
            pre = evt["context_before"]
            ctx_after = evt.get("context_after", 0)
            if pre > 0:
                headroom = max(0, CONTEXT_LIMIT - pre)
                rebuild = ctx_after
                tokens_wasted += headroom + rebuild
            total_context_built += CONTEXT_LIMIT
        total_built = total_context_built + last_context
        eff = max(0, 1 - tokens_wasted / total_built) if total_built > 0 else 1.0
        return tokens_wasted, total_built, eff

    def test_no_compactions_100_percent(self):
        """Fresh session = 100% efficiency."""
        wasted, total, eff = self._compute_waste([], 80_000)
        self.assertEqual(wasted, 0)
        self.assertEqual(total, 80_000)
        self.assertAlmostEqual(eff, 1.0)

    def test_one_compaction(self):
        """Single compaction: waste = headroom + rebuild."""
        events = [{"context_before": 167_000, "context_after": 33_100}]
        wasted, total, eff = self._compute_waste(events, 80_000)
        expected_headroom = CONTEXT_LIMIT - 167_000  # 33k
        expected_rebuild = 33_100
        self.assertEqual(wasted, expected_headroom + expected_rebuild)  # 66.1k
        self.assertEqual(total, CONTEXT_LIMIT + 80_000)  # 280k
        # Efficiency = 1 - 66100/280000 = ~76.4%
        self.assertAlmostEqual(eff, 1 - 66_100 / 280_000, places=3)

    def test_two_compactions(self):
        """Two compactions: waste accumulates headroom + rebuild for each."""
        events = [
            {"context_before": 167_000, "context_after": 33_100},
            {"context_before": 147_500, "context_after": 24_900},
        ]
        wasted, total, eff = self._compute_waste(events, 50_000)
        h1 = CONTEXT_LIMIT - 167_000   # 33k
        r1 = 33_100
        h2 = CONTEXT_LIMIT - 147_500   # 52.5k
        r2 = 24_900
        self.assertEqual(wasted, h1 + r1 + h2 + r2)  # 143.5k
        self.assertEqual(total, CONTEXT_LIMIT * 2 + 50_000)  # 450k
        self.assertAlmostEqual(eff, 1 - 143_500 / 450_000, places=3)

    def test_efficiency_bounds(self):
        """Efficiency should always be in [0, 1]."""
        # Edge case: very small context after many compactions
        events = [{"context_before": 190_000, "context_after": 5_000}]
        wasted, total, eff = self._compute_waste(events, 5_000)
        self.assertGreaterEqual(eff, 0)
        self.assertLessEqual(eff, 1)


# ── Chart rendering ───────────────────────────────────────────────────

class TestHorizontalChart(unittest.TestCase):
    def _get_lines(self, compact_events, last_context, width=100):
        r = {
            "compact_events": compact_events,
            "last_context": last_context,
        }
        segs, comps = _build_segments(r)
        return [strip_ansi(l) for l in _render_horizontal_chart(segs, comps, width)]

    def test_header_and_legend(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        self.assertTrue(any("HORIZONTAL" in l for l in lines))
        self.assertTrue(any("useful" in l and "rebuild" in l and "headroom" in l for l in lines))

    def test_segment_labels(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        self.assertTrue(any("Seg 1" in l for l in lines))
        self.assertTrue(any("Seg 2" in l for l in lines))

    def test_active_segment_marker(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        # Active segment should have → prefix
        self.assertTrue(any("→ Seg 2" in l for l in lines))

    def test_compaction_marker(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        self.assertTrue(any("compact #1" in l for l in lines))

    def test_detail_shows_survived_and_lost(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        self.assertTrue(any("survived" in l and "lost" in l for l in lines))

    def test_completed_segment_shows_200k(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        # Completed segment should show CONTEXT_LIMIT as total
        self.assertTrue(any("200.0k" in l and "Seg 1" in l for l in lines))

    def test_no_compactions(self):
        """Single active segment, no compactions — no crash."""
        r = {"compact_events": [], "last_context": 80_000}
        segs, comps = _build_segments(r)
        lines = _render_horizontal_chart(segs, comps, 100)
        clean = [strip_ansi(l) for l in lines]
        self.assertTrue(any("Seg 1" in l for l in clean))
        # No compaction markers
        self.assertFalse(any("compact #" in l for l in clean))

    def test_rebuild_shown_in_seg2(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        # Seg 2 detail should mention rebuild
        seg2_lines = []
        in_seg2 = False
        for l in lines:
            if "Seg 2" in l:
                in_seg2 = True
            elif "Seg " in l and "Seg 2" not in l:
                in_seg2 = False
            if in_seg2:
                seg2_lines.append(l)
        self.assertTrue(any("rebuild" in l for l in seg2_lines))

    def test_headroom_shown_for_completed(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        self.assertTrue(any("headroom" in l for l in lines))

    def test_no_headroom_for_active(self):
        """Active segment should not show headroom."""
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        # Find lines after active segment marker
        active_detail = []
        found_active = False
        for l in lines:
            if "→ Seg" in l:
                found_active = True
                continue
            if found_active and ("Seg " in l or "compact" in l):
                break
            if found_active:
                active_detail.append(l)
        # Active segment detail should not mention headroom
        self.assertFalse(any("headroom" in l for l in active_detail))


class TestVerticalChart(unittest.TestCase):
    def _get_lines(self, compact_events, last_context, width=80, height=30):
        r = {
            "compact_events": compact_events,
            "last_context": last_context,
        }
        segs, comps = _build_segments(r)
        return [strip_ansi(l) for l in _render_vertical_chart(segs, comps, width, height)]

    def test_header_and_legend(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        self.assertTrue(any("VERTICAL" in l for l in lines))
        self.assertTrue(any("useful" in l and "rebuild" in l and "headroom" in l for l in lines))

    def test_y_axis_labels(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        self.assertTrue(any("200.0k" in l for l in lines))
        self.assertTrue(any("100.0k" in l for l in lines))
        self.assertTrue(any("0" in l for l in lines))

    def test_segment_labels(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        self.assertTrue(any("S1" in l for l in lines))
        # Active segment
        self.assertTrue(any("→2" in l for l in lines))

    def test_peak_values(self):
        lines = self._get_lines(
            [{"context_before": 167_000, "context_after": 33_100}],
            80_000,
        )
        self.assertTrue(any("167.0k" in l for l in lines))
        self.assertTrue(any("80.0k" in l for l in lines))

    def test_no_compactions(self):
        """Single active segment — no crash."""
        r = {"compact_events": [], "last_context": 80_000}
        segs, comps = _build_segments(r)
        lines = _render_vertical_chart(segs, comps, 80, 30)
        self.assertTrue(len(lines) > 0)


# ── parse_transcript with JSONL ───────────────────────────────────────

class TestParseTranscript(unittest.TestCase):
    def _write_jsonl(self, entries):
        """Write entries to a temp JSONL file, return path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
        f.close()
        return f.name

    def _make_assistant_usage(self, input_t=0, cache_r=0, cache_c=0, output_t=0, turn=1):
        """Create an assistant message with usage stats."""
        return {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": input_t,
                    "cache_read_input_tokens": cache_r,
                    "cache_creation_input_tokens": cache_c,
                    "output_tokens": output_t,
                },
                "content": [],
            },
            "turn_number": turn,
        }

    def _make_compaction(self):
        return {"type": "system", "subtype": "compact_boundary"}

    def _make_user_turn(self, turn=1):
        return {"type": "human", "turn_number": turn}

    def test_basic_no_compaction(self):
        """Simple session, no compactions — 100% efficiency."""
        entries = [
            self._make_user_turn(1),
            self._make_assistant_usage(input_t=50000, output_t=10000, turn=1),
            self._make_user_turn(2),
            self._make_assistant_usage(input_t=60000, output_t=15000, turn=2),
        ]
        path = self._write_jsonl(entries)
        try:
            r = parse_transcript(path)
            self.assertEqual(r["compact_count"], 0)
            self.assertEqual(r["tokens_wasted"], 0)
            self.assertEqual(r["total_context_built"], 0)
        finally:
            os.unlink(path)

    def test_compaction_waste_calculation(self):
        """Verify waste = headroom + rebuild after compaction."""
        # Build up context to 167k, then compact, then resume at 33k
        entries = [
            self._make_user_turn(1),
            self._make_assistant_usage(input_t=100000, output_t=67000, turn=1),
            # Compaction fires
            self._make_compaction(),
            # First response after compaction — this is the rebuild context
            self._make_user_turn(2),
            self._make_assistant_usage(input_t=25000, cache_r=5000, output_t=3100, turn=2),
        ]
        path = self._write_jsonl(entries)
        try:
            r = parse_transcript(path)
            self.assertEqual(r["compact_count"], 1)
            # context_before = 100000+67000 = 167000
            # context_after = 25000+5000+3100 = 33100
            # headroom = 200000 - 167000 = 33000
            # rebuild = 33100
            # wasted = 33000 + 33100 = 66100
            self.assertEqual(r["tokens_wasted"], 66_100)
            self.assertEqual(r["total_context_built"], CONTEXT_LIMIT)
            # Verify compact event has context_after
            self.assertEqual(len(r["compact_events"]), 1)
            self.assertEqual(r["compact_events"][0]["context_before"], 167_000)
            self.assertEqual(r["compact_events"][0]["context_after"], 33_100)
        finally:
            os.unlink(path)

    def test_two_compactions(self):
        """Two compactions accumulate waste correctly."""
        entries = [
            self._make_user_turn(1),
            self._make_assistant_usage(input_t=100000, output_t=67000, turn=1),
            self._make_compaction(),
            self._make_user_turn(2),
            self._make_assistant_usage(input_t=25000, cache_r=5000, output_t=3100, turn=2),
            # More work in segment 2
            self._make_user_turn(3),
            self._make_assistant_usage(input_t=90000, output_t=57500, turn=3),
            self._make_compaction(),
            self._make_user_turn(4),
            self._make_assistant_usage(input_t=20000, cache_r=3000, output_t=1900, turn=4),
        ]
        path = self._write_jsonl(entries)
        try:
            r = parse_transcript(path)
            self.assertEqual(r["compact_count"], 2)
            # Compaction 1: peak=167000, after=33100
            # headroom1 = 200000-167000 = 33000, rebuild1 = 33100 → 66100
            # Compaction 2: peak=147500, after=24900
            # headroom2 = 200000-147500 = 52500, rebuild2 = 24900 → 77400
            self.assertEqual(r["tokens_wasted"], 66_100 + 77_400)  # 143500
            self.assertEqual(r["total_context_built"], CONTEXT_LIMIT * 2)
        finally:
            os.unlink(path)

    def test_last_context_tracked(self):
        """last_context reflects the final context snapshot."""
        entries = [
            self._make_user_turn(1),
            self._make_assistant_usage(input_t=40000, output_t=10000, turn=1),
            self._make_user_turn(2),
            self._make_assistant_usage(input_t=55000, output_t=15000, turn=2),
        ]
        path = self._write_jsonl(entries)
        try:
            r = parse_transcript(path)
            self.assertEqual(r["last_context"], 55000 + 15000)  # 70k
        finally:
            os.unlink(path)


# ── CONTEXT_LIMIT constant ─────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def test_context_limit(self):
        self.assertEqual(CONTEXT_LIMIT, 200_000)


if __name__ == "__main__":
    unittest.main()
