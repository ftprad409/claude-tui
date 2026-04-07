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
        assert "turns left" in result


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
