#!/usr/bin/env python3
"""Comprehensive tests for statusline SRP modules."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import statusline
from statusline_core import api_clients, git_info, render, transcript
from statusline_core.constants import DEFAULT_CONTEXT_LIMIT


def _write_jsonl(path, rows):
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


class TestTranscriptParsing(unittest.TestCase):
    def test_parse_input_data(self):
        data = {
            "model": {"display_name": "Sonnet", "id": "claude-sonnet-4-6"},
            "workspace": {"current_dir": "/tmp/project"},
            "transcript_path": "/tmp/t.jsonl",
            "session_id": "abcdef123456",
        }
        parsed = transcript.parse_input_data(data)
        self.assertEqual(parsed["model"], "Sonnet")
        self.assertEqual(parsed["cwd"], "project")
        self.assertEqual(parsed["session_id"], "abcdef12")

    def test_parse_transcript_metrics_and_tools(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.jsonl")
            rows = [
                {
                    "type": "user",
                    "timestamp": "2026-04-07T00:00:00Z",
                    "message": {"content": [{"type": "text", "text": "hi"}]},
                },
                {
                    "type": "assistant",
                    "message": {
                        "usage": {
                            "input_tokens": 100,
                            "cache_creation_input_tokens": 10,
                            "cache_read_input_tokens": 40,
                            "output_tokens": 20,
                        },
                        "content": [
                            {"type": "thinking", "text": "..."},
                            {
                                "type": "tool_use",
                                "name": "Edit",
                                "input": {"path": "/tmp/a.py"},
                            },
                            {
                                "type": "tool_use",
                                "id": "task_1",
                                "name": "Task",
                                "input": {},
                            },
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "is_error": True}]},
                },
                {"type": "system", "subtype": "compact_boundary"},
            ]
            _write_jsonl(p, rows)
            m = transcript.parse_transcript(p, context_limit=200_000)
            self.assertEqual(m["turn_count"], 1)
            self.assertEqual(m["tool_calls"], 2)
            self.assertEqual(m["tool_errors"], 1)
            self.assertEqual(m["compact_count"], 1)
            self.assertEqual(m["thinking_count"], 1)
            self.assertIn("/tmp/a.py", m["files_touched"])
            self.assertEqual(m["subagent_count"], 1)

    def test_context_limit_and_pricing_lookup(self):
        self.assertEqual(transcript.get_context_limit("claude-opus-4-6"), 1_000_000)
        self.assertEqual(
            transcript.get_context_limit("unknown-model"), DEFAULT_CONTEXT_LIMIT
        )
        p = transcript.get_model_pricing("claude-sonnet-4-6")
        self.assertIn("input", p)

    def test_compaction_prediction_and_efficiency(self):
        metrics = {
            "context_per_turn": [(1, 1000), (2, 1300), (3, 1700)],
            "context_at_last_compact": 900,
            "total_context_built": 10000,
            "tokens_wasted": 1000,
        }
        pred = transcript.calculate_compaction_prediction(
            1700, 200_000, 3, metrics, ratio=0.2
        )
        self.assertIn("ETA", pred)
        with mock.patch("statusline_core.transcript.is_visible", return_value=True):
            eff = transcript.calculate_efficiency(metrics, 2000)
        self.assertIn("eff", eff)


class TestFormattingAndRender(unittest.TestCase):
    def test_token_cost_duration_format(self):
        self.assertEqual(transcript.format_tokens(1200), "1.2k")
        self.assertEqual(transcript.format_cost(0.001), "<$0.01")
        self.assertEqual(transcript.format_duration(""), "0m")

    def test_cache_ratio_and_part(self):
        metrics = {"input_tokens_total": 100, "cache_read_tokens_total": 100}
        pct, color = transcript.calculate_cache_ratio(metrics)
        part = transcript.format_cache_part(pct, color)
        self.assertIn("cache", part)

    def test_progress_bar_and_sparkline(self):
        bar = render.build_progress_bar(0.5, length=10, compact_ratio=0.8)
        self.assertIn("%", bar)
        sp = render.build_sparkline([10, 20, None, 5], width=8)
        self.assertTrue(len(sp) > 0)

    def test_wrap_and_terminal_width(self):
        lines = render.wrap_line_parts(
            ["a", "b", "c"],
            ["file.py"],
            max_width=6,
        )
        self.assertTrue(len(lines) >= 1)
        self.assertIsInstance(render.calculate_terminal_width(), int)

    def test_line_builders(self):
        metrics = {
            "compact_count": 1,
            "turn_count": 2,
            "files_touched": {"a.py"},
            "tool_errors": 0,
            "thinking_count": 1,
            "subagent_count": 1,
            "recent_tools": ["Edit a.py"],
            "current_turn_file_edits": {"a.py": 2},
        }
        with mock.patch("statusline_core.render.is_visible", return_value=True):
            l1 = render.build_line1_parts(
                "bar",
                "1k",
                "200k",
                "ETA 10 turns",
                "Sonnet",
                "spark",
                "$1.00",
                "10m",
                metrics,
                "90% eff",
                "abcd1234",
            )
            l2 = render.build_line2_parts(
                {"five_hour": {"utilization": 10, "resets_at": ""}},
                "proj",
                "main",
                metrics,
                "80% cache",
                80,
                "~$0.50/turn",
                "status",
            )
            l3 = render.build_line3_parts(
                {"seven_day": {"utilization": 50, "resets_at": ""}},
                metrics,
            )
            compact = render.build_compact_line("Sonnet", "bar", "1k", "200k", {})
        self.assertTrue(len(l1) > 0)
        self.assertTrue(len(l2) > 0)
        self.assertIsInstance(l3, list)
        self.assertTrue(isinstance(compact, str))


class TestApiClientFormatting(unittest.TestCase):
    def test_api_status_formatting(self):
        data = {
            "status": "none",
            "components": {"Claude Code API": "major_outage"},
            "incidents": [],
        }
        with mock.patch("statusline_core.api_clients.get_setting", return_value=False):
            out = api_clients.format_api_status(data)
        self.assertIn("outage", out)

    def test_usage_bar_formatters(self):
        usage = {
            "five_hour": {"utilization": 25, "resets_at": "2099-01-01T00:00:00Z"},
            "seven_day": {"utilization": 75, "resets_at": "2099-01-02T00:00:00Z"},
        }
        s = api_clients.format_usage_session(usage)
        w = api_clients.format_usage_weekly(usage)
        self.assertIn("%", s)
        self.assertIn("W ", w)

    def test_fetch_short_circuit_when_disabled(self):
        with mock.patch("statusline_core.api_clients.get_setting", return_value=False):
            self.assertIsNone(api_clients.fetch_usage())
            self.assertIsNone(api_clients.fetch_api_status())


class TestGitInfo(unittest.TestCase):
    def test_git_helpers_fail_closed(self):
        with mock.patch("subprocess.run", side_effect=RuntimeError("boom")):
            self.assertEqual(git_info.get_git_branch(), "")
            self.assertEqual(git_info.get_git_diff_stat(), "")


class TestEntrypointIntegration(unittest.TestCase):
    def _minimal_payload(self, transcript_path):
        return {
            "model": {"display_name": "Sonnet", "id": "claude-sonnet-4-6"},
            "workspace": {"current_dir": "/tmp/proj"},
            "transcript_path": transcript_path,
            "session_id": "abcdef123456",
        }

    def _write_min_transcript(self):
        td = tempfile.TemporaryDirectory()
        p = os.path.join(td.name, "session.jsonl")
        _write_jsonl(
            p,
            [
                {
                    "type": "user",
                    "timestamp": "2026-04-07T00:00:00Z",
                    "message": {"content": [{"type": "text", "text": "hi"}]},
                },
                {
                    "type": "assistant",
                    "message": {
                        "usage": {
                            "input_tokens": 100,
                            "cache_creation_input_tokens": 10,
                            "cache_read_input_tokens": 40,
                            "output_tokens": 20,
                        },
                        "content": [],
                    },
                },
            ],
        )
        return td, p

    def test_main_compact_mode_outputs_line(self):
        td, p = self._write_min_transcript()
        self.addCleanup(td.cleanup)
        payload = self._minimal_payload(p)
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["statusline.py", "--compact"]), mock.patch(
            "sys.stdin", io.StringIO(json.dumps(payload))
        ), mock.patch("statusline.fetch_usage", return_value=None), mock.patch(
            "statusline.fetch_api_status", return_value=None
        ), mock.patch(
            "statusline.format_api_status", return_value=""
        ), redirect_stdout(
            out
        ):
            statusline.main()
        self.assertTrue(out.getvalue().strip())

    def test_main_full_mode_outputs_lines(self):
        td, p = self._write_min_transcript()
        self.addCleanup(td.cleanup)
        payload = self._minimal_payload(p)
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["statusline.py"]), mock.patch(
            "sys.stdin", io.StringIO(json.dumps(payload))
        ), mock.patch("statusline.fetch_usage", return_value=None), mock.patch(
            "statusline.fetch_api_status", return_value=None
        ), mock.patch(
            "statusline.format_api_status", return_value=""
        ), mock.patch.dict(
            os.environ, {"STATUSLINE_WIDGET": "none"}, clear=False
        ), redirect_stdout(
            out
        ):
            statusline.main()
        self.assertTrue(len(out.getvalue().splitlines()) >= 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
#!/usr/bin/env python3
"""Tests for statusline.py helper functions."""

import sys

sys.path.insert(0, "/Users/slim/dev/claude-tui/claude-code-statusline")

import pytest
from statusline import (
    parse_input_data,
    calculate_context_metrics,
    calculate_session_cost,
    calculate_cache_ratio,
    format_cache_part,
    format_git_branch,
    calculate_cost_per_turn,
    calculate_compaction_prediction,
    calculate_efficiency,
    format_tool_trail,
    format_file_edits,
)


class TestParseInputData:
    """Tests for parse_input_data function."""

    def test_valid_input(self):
        data = {
            "model": {"display_name": "Claude", "id": "claude-sonnet-4-6"},
            "workspace": {"current_dir": "/home/user/project"},
            "transcript_path": "/path/to/transcript",
            "session_id": "abc12345678",
        }
        result = parse_input_data(data)
        assert result["model"] == "Claude"
        assert result["model_id"] == "claude-sonnet-4-6"
        assert result["cwd"] == "project"
        assert result["transcript_path"] == "/path/to/transcript"
        assert result["session_id"] == "abc12345"

    def test_missing_model(self):
        data = {"workspace": {"current_dir": "/home/user"}}
        result = parse_input_data(data)
        assert result["model"] == "unknown"
        assert result["model_id"] == ""

    def test_empty_workspace(self):
        data = {}
        result = parse_input_data(data)
        assert result["cwd"] == ""

    def test_short_session_id(self):
        data = {"session_id": "abc"}
        result = parse_input_data(data)
        assert result["session_id"] == "abc"


class TestCalculateContextMetrics:
    """Tests for calculate_context_metrics function."""

    def test_normal_ratio(self):
        result = calculate_context_metrics(50000, 200000)
        assert result["ratio"] == 0.25
        assert result["compact_ratio"] == 0.835

    def test_zero_context_limit(self):
        result = calculate_context_metrics(0, 0)
        assert result["ratio"] == 0
        assert result["compact_ratio"] == 0.83

    def test_full_context(self):
        result = calculate_context_metrics(200000, 200000)
        assert result["ratio"] == 1.0
        assert result["compact_ratio"] == 0.835

    def test_half_context(self):
        result = calculate_context_metrics(100000, 200000)
        assert result["ratio"] == 0.5


class TestCalculateSessionCost:
    """Tests for calculate_session_cost function."""

    def test_full_cost_calculation(self):
        metrics = {
            "input_tokens_total": 100000,
            "cache_read_tokens_total": 50000,
            "cache_creation_tokens_total": 20000,
            "output_tokens_total": 30000,
        }
        pricing = {
            "input": 3.0,
            "cache_read": 0.30,
            "cache_write": 3.75,
            "output": 15.0,
        }
        cost = calculate_session_cost(metrics, pricing)
        assert cost > 0

    def test_zero_tokens(self):
        metrics = {
            "input_tokens_total": 0,
            "cache_read_tokens_total": 0,
            "cache_creation_tokens_total": 0,
            "output_tokens_total": 0,
        }
        pricing = {"input": 3.0, "cache_read": 0.3, "output": 15.0}
        cost = calculate_session_cost(metrics, pricing)
        assert cost == 0.0


class TestCalculateCacheRatio:
    """Tests for calculate_cache_ratio function."""

    def test_high_cache(self):
        metrics = {
            "input_tokens_total": 30000,
            "cache_read_tokens_total": 70000,
        }
        pct, color = calculate_cache_ratio(metrics)
        assert pct == 70
        assert color == "\033[92m"

    def test_medium_cache(self):
        metrics = {
            "input_tokens_total": 60000,
            "cache_read_tokens_total": 40000,
        }
        pct, color = calculate_cache_ratio(metrics)
        assert pct == 40
        assert color == "\033[93m"

    def test_low_cache(self):
        metrics = {
            "input_tokens_total": 80000,
            "cache_read_tokens_total": 20000,
        }
        pct, color = calculate_cache_ratio(metrics)
        assert pct == 20
        assert color == "\033[38;5;208m"

    def test_zero_total(self):
        metrics = {
            "input_tokens_total": 0,
            "cache_read_tokens_total": 0,
        }
        pct, color = calculate_cache_ratio(metrics)
        assert pct == 0


class TestFormatCachePart:
    """Tests for format_cache_part function."""

    def test_non_zero_cache(self):
        result = format_cache_part(50, "\033[92m")
        assert "50%" in result
        assert "cache" in result

    def test_zero_cache(self):
        result = format_cache_part(0, "\033[90m")
        assert "0%" in result


class TestFormatGitBranch:
    """Tests for format_git_branch function."""

    def test_branch_with_diff(self):
        result = format_git_branch("main", "+10 -5")
        assert "main" in result
        assert "+10" in result

    def test_branch_without_diff(self):
        result = format_git_branch("feature", "")
        assert "feature" in result

    def test_empty_branch(self):
        result = format_git_branch("", "")
        assert result == ""


class TestCalculateCostPerTurn:
    """Tests for calculate_cost_per_turn function."""

    def test_with_turns(self):
        result = calculate_cost_per_turn(1.50, 3)
        assert "0.50" in result
        assert "turn" in result

    def test_zero_turns(self):
        result = calculate_cost_per_turn(1.50, 0)
        assert result == ""


class TestCalculateCompactionPrediction:
    """Tests for calculate_compaction_prediction function."""

    def test_no_compaction_yet(self):
        metrics = {"context_per_turn": [], "context_at_last_compact": 0}
        result = calculate_compaction_prediction(50000, 200000, 1, metrics, 0.25)
        assert result == ""

    def test_full_context(self):
        metrics = {"context_per_turn": [], "context_at_last_compact": 0}
        result = calculate_compaction_prediction(200000, 200000, 5, metrics, 1.0)
        assert result == ""

    def test_low_ratio(self):
        metrics = {
            "context_per_turn": [(1, 30000), (2, 35000), (3, 40000)],
            "context_at_last_compact": 10000,
        }
        result = calculate_compaction_prediction(40000, 200000, 3, metrics, 0.2)
        assert "ETA" in result


class TestCalculateEfficiency:
    """Tests for calculate_efficiency function."""

    def test_no_waste(self, monkeypatch):
        monkeypatch.setattr("statusline.is_visible", lambda *args: True)
        metrics = {"total_context_built": 100000, "tokens_wasted": 0}
        result = calculate_efficiency(metrics, 50000)
        assert "100%" in result

    def test_with_waste(self, monkeypatch):
        monkeypatch.setattr("statusline.is_visible", lambda *args: True)
        metrics = {"total_context_built": 100000, "tokens_wasted": 30000}
        result = calculate_efficiency(metrics, 50000)
        assert "80%" in result

    def test_zero_total(self):
        metrics = {"total_context_built": 0, "tokens_wasted": 0}
        result = calculate_efficiency(metrics, 0)
        assert result == ""


class TestFormatToolTrail:
    """Tests for format_tool_trail function."""

    def test_empty_input(self):
        result = format_tool_trail([])
        assert result == []

    def test_single_tool(self):
        result = format_tool_trail(["Read file.txt"])
        assert len(result) == 1
        assert "read" in result[0].lower()

    def test_multiple_tools(self):
        result = format_tool_trail(["Read a.txt", "Write b.txt", "Edit c.txt"])
        assert len(result) == 3


class TestFormatFileEdits:
    """Tests for format_file_edits function."""

    def test_empty_input(self):
        result = format_file_edits({})
        assert result == []

    def test_multiple_edits(self):
        edits = {"a.txt": 10, "b.txt": 5, "c.txt": 3, "d.txt": 1}
        result = format_file_edits(edits)
        assert len(result) == 3
        assert "a.txt" in result[0]
        assert "10" in result[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
