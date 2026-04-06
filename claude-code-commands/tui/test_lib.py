#!/usr/bin/env python3
"""Tests for lib.py parsing and formatting utilities."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import (
    parse_transcript,
    format_tokens,
    format_duration,
    get_pricing,
    calc_cost,
    get_context_limit,
    find_transcript,
    has_text_content,
    calc_user_turn,
    calc_usage,
    is_compaction_event,
)


class TestFormatTokens(unittest.TestCase):
    """Tests for format_tokens function."""

    def test_small_number(self):
        self.assertEqual(format_tokens(0), "0")
        self.assertEqual(format_tokens(1), "1")
        self.assertEqual(format_tokens(999), "999")

    def test_thousands(self):
        self.assertEqual(format_tokens(1000), "1.0k")
        self.assertEqual(format_tokens(15000), "15.0k")
        self.assertEqual(format_tokens(999999), "1000.0k")

    def test_millions(self):
        self.assertEqual(format_tokens(1_000_000), "1.0M")
        self.assertEqual(format_tokens(2_500_000), "2.5M")
        self.assertEqual(format_tokens(10_000_000), "10.0M")


class TestFormatDuration(unittest.TestCase):
    """Tests for format_duration function."""

    def test_none_input(self):
        self.assertEqual(format_duration(None), "unknown")

    def test_valid_duration(self):
        result = format_duration("2024-01-01T10:00:00Z", "2024-01-01T10:30:00Z")
        self.assertEqual(result, "30m")

    def test_hours_duration(self):
        result = format_duration("2024-01-01T10:00:00Z", "2024-01-01T12:00:00Z")
        self.assertEqual(result, "2h 0m")

    def test_invalid_format(self):
        self.assertEqual(format_duration("not-a-timestamp"), "unknown")

    def test_end_to_now(self):
        result = format_duration("2024-01-01T10:00:00Z")
        self.assertIn("m", result)


class TestGetPricing(unittest.TestCase):
    """Tests for get_pricing function."""

    def test_sonnet_4_6(self):
        pricing = get_pricing("claude-sonnet-4-6-20250529")
        self.assertEqual(pricing["input"], 3.0)
        self.assertEqual(pricing["output"], 15.0)

    def test_opus_4(self):
        pricing = get_pricing("claude-opus-4-6-20250529")
        self.assertEqual(pricing["input"], 15.0)
        self.assertEqual(pricing["output"], 75.0)

    def test_haiku(self):
        pricing = get_pricing("claude-haiku-4-5")
        self.assertEqual(pricing["input"], 0.80)
        self.assertEqual(pricing["output"], 4.0)

    def test_unknown_model(self):
        pricing = get_pricing("unknown-model")
        self.assertEqual(pricing["input"], 3.0)
        self.assertEqual(pricing["output"], 15.0)


class TestCalcCost(unittest.TestCase):
    """Tests for calc_cost function."""

    def test_basic_calculation(self):
        tokens = {"input": 1000, "cache_read": 0, "cache_creation": 0, "output": 500}
        pricing = {
            "input": 3.0,
            "cache_read": 0.30,
            "cache_write": 3.75,
            "output": 15.0,
        }
        cost = calc_cost(tokens, pricing)
        self.assertAlmostEqual(cost["input"], 0.003)
        self.assertAlmostEqual(cost["output"], 0.0075)
        self.assertAlmostEqual(cost["total"], 0.0105)

    def test_with_cache(self):
        tokens = {
            "input": 1000,
            "cache_read": 2000,
            "cache_creation": 500,
            "output": 500,
        }
        pricing = {
            "input": 3.0,
            "cache_read": 0.30,
            "cache_write": 3.75,
            "output": 15.0,
        }
        cost = calc_cost(tokens, pricing)
        self.assertEqual(cost["cache_read"], 0.0006)
        self.assertEqual(cost["cache_write"], 0.001875)


class TestGetContextLimit(unittest.TestCase):
    """Tests for get_context_limit function."""

    def test_opus_4(self):
        limit = get_context_limit("claude-opus-4-6-20250529")
        self.assertEqual(limit, 1_000_000)

    def test_default(self):
        limit = get_context_limit("claude-sonnet-4-6")
        self.assertEqual(limit, 200_000)

    def test_unknown(self):
        limit = get_context_limit("unknown-model")
        self.assertEqual(limit, 200_000)


class TestHasTextContent(unittest.TestCase):
    """Tests for has_text_content function."""

    def test_list_with_text(self):
        content = [{"type": "text", "text": "hello"}]
        self.assertTrue(has_text_content(content))

    def test_list_without_text(self):
        content = [{"type": "tool_use", "name": "Read"}]
        self.assertFalse(has_text_content(content))

    def test_string_with_text(self):
        self.assertTrue(has_text_content("hello world"))

    def test_empty_string(self):
        self.assertFalse(has_text_content(""))

    def test_none(self):
        self.assertFalse(has_text_content(None))


class TestCalcUserTurn(unittest.TestCase):
    """Tests for calc_user_turn function."""

    def test_user_turn_with_text(self):
        obj = {"type": "user", "message": {"content": "hello"}}
        result = calc_user_turn(obj)
        self.assertTrue(result["is_user_turn"])

    def test_user_turn_without_text(self):
        obj = {"type": "user", "message": {"content": ""}}
        result = calc_user_turn(obj)
        self.assertFalse(result["is_user_turn"])

    def test_assistant_turn(self):
        obj = {"type": "assistant", "message": {"content": "response"}}
        result = calc_user_turn(obj)
        self.assertFalse(result["is_user_turn"])

    def test_meta_turn(self):
        obj = {"type": "user", "isMeta": True, "message": {"content": "meta"}}
        result = calc_user_turn(obj)
        self.assertFalse(result["is_user_turn"])


class TestCalcUsage(unittest.TestCase):
    """Tests for calc_usage function."""

    def test_with_usage(self):
        usage = {
            "input_tokens": 1000,
            "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 200,
            "output_tokens": 300,
        }
        result = calc_usage(usage)
        self.assertIsNotNone(result)
        self.assertEqual(result["input"], 1000)
        self.assertEqual(result["total"], 2000)

    def test_empty_usage(self):
        self.assertIsNone(calc_usage({}))
        self.assertIsNone(calc_usage(None))


class TestIsCompactionEvent(unittest.TestCase):
    """Tests for is_compaction_event function."""

    def test_summary_event(self):
        obj = {"type": "summary", "text": "session summary"}
        self.assertTrue(is_compaction_event(obj))

    def test_compact_boundary(self):
        obj = {"type": "system", "subtype": "compact_boundary"}
        self.assertTrue(is_compaction_event(obj))

    def test_regular_message(self):
        obj = {"type": "user", "message": {"content": "hello"}}
        self.assertFalse(is_compaction_event(obj))


class TestParseTranscript(unittest.TestCase):
    """Tests for parse_transcript function."""

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("")
            f.flush()
            path = f.name

        try:
            result = parse_transcript(path)
            self.assertEqual(result["turns"], 0)
            self.assertEqual(result["responses"], 0)
        finally:
            os.unlink(path)

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("not valid json\n")
            f.flush()
            path = f.name

        try:
            result = parse_transcript(path)
            self.assertEqual(result["turns"], 0)
        finally:
            os.unlink(path)

    def test_valid_jsonl(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"type": "user", "timestamp": "2024-01-01T10:00:00Z", "message": {"content": "hello"}}\n'
            )
            f.write(
                '{"type": "assistant", "timestamp": "2024-01-01T10:00:01Z", "message": {"model": "claude-sonnet-4-6", "content": [], "usage": {"input_tokens": 100, "output_tokens": 50}}}\n'
            )
            f.flush()
            path = f.name

        try:
            result = parse_transcript(path)
            self.assertEqual(result["turns"], 1)
            self.assertEqual(result["responses"], 1)
            self.assertEqual(result["tokens"]["input"], 100)
            self.assertEqual(result["tokens"]["output"], 50)
            self.assertEqual(result["model"], "claude-sonnet-4-6")
        finally:
            os.unlink(path)


class TestFindTranscript(unittest.TestCase):
    """Tests for find_transcript function."""

    def test_nonexistent_directory(self):
        result = find_transcript("/nonexistent/path/to/project")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
