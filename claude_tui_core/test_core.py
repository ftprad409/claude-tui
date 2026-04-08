"""
Permanent test suite for claude_tui_core.
Covers models (pricing/limits), settings (hot-reload), and network formatting.
"""

import unittest
import os
import json
import time
from unittest.mock import patch, MagicMock

from claude_tui_core.models import (
    get_context_limit, 
    get_model_pricing, 
    get_model_pricing_fuzzy,
    MODEL_PRICING
)
from claude_tui_core.settings import get_setting, load_settings, reset_settings_cache
from claude_tui_core.network import (
    format_api_status, 
    format_usage_session, 
    fetch_api_status
)

class TestModels(unittest.TestCase):
    def test_context_limits(self):
        # Opus 4 has 1M
        self.assertEqual(get_context_limit("claude-opus-4-6"), 1_000_000)
        # Claude 3 Opus defaults to 200k
        self.assertEqual(get_context_limit("claude-3-opus-20240229"), 200_000)
        # Others default to 200k
        self.assertEqual(get_context_limit("claude-sonnet-3-5"), 200_000)
        self.assertEqual(get_context_limit("unknown"), 200_000)

    def test_pricing_lookup(self):
        p = get_model_pricing("claude-sonnet-4-6")
        self.assertEqual(p["input"], 3.0)
        self.assertEqual(p["output"], 15.0)

    def test_fuzzy_pricing(self):
        # Sonnet 4 abbreviated
        p = get_model_pricing_fuzzy("claude-sonnet-4")
        self.assertEqual(p["input"], 3.0)
        
        # Sonnet 3.5 abbreviated
        p2 = get_model_pricing_fuzzy("claude-sonnet-3")
        self.assertEqual(p2["input"], 3.0)
        
        # Empty/Unknown defaults to Sonnet 4.6
        self.assertEqual(get_model_pricing_fuzzy(""), MODEL_PRICING["claude-sonnet-4-6"])
        self.assertEqual(get_model_pricing_fuzzy("unknown"), MODEL_PRICING["claude-sonnet-4-6"])

class TestSettings(unittest.TestCase):
    def setUp(self):
        reset_settings_cache()
        self.test_config_path = os.path.expanduser("~/.claude/claudeui_test.json")

    @patch("os.path.join")
    @patch("os.path.getmtime")
    def test_hot_reload(self, mock_mtime, mock_join):
        # Setup mock path
        mock_join.return_value = "dummy_config.json"
        
        # Fake config content
        config_v1 = {"sparkline": {"enabled": True}}
        config_v2 = {"sparkline": {"enabled": False}}
        
        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(config_v1))):
            mock_mtime.return_value = 100
            self.assertTrue(get_setting("sparkline", "enabled"))
            
        # Change mtime and content
        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(config_v2))):
            mock_mtime.return_value = 200
            self.assertFalse(get_setting("sparkline", "enabled"))

    def test_defaults(self):
        self.assertEqual(get_setting("nonexistent", default="ok"), "ok")
        self.assertEqual(get_setting("nested", "not", "there", default=123), 123)

class TestNetwork(unittest.TestCase):
    def test_format_api_status(self):
        # Operational
        self.assertEqual(format_api_status({"status": "none", "components": {}}), "")
        
        # Major outage
        status = {
            "status": "critical",
            "components": {"Claude Code": "major_outage"}
        }
        res = format_api_status(status)
        self.assertIn("outage", res.lower())

    @patch("claude_tui_components.widgets.build_progress_bar")
    def test_format_usage(self, mock_bar):
        mock_bar.return_value = "[BAR]"
        usage = {
            "five_hour": {"utilization": 50.0, "resets_at": "2026-04-08T12:00:00Z"}
        }
        res = format_usage_session(usage)
        self.assertIn("[BAR]", res)

    @patch("claude_tui_core.network._read_json_file")
    @patch("threading.Thread")
    def test_background_refresh_trigger(self, mock_thread, mock_read):
        # Mock stale cache
        mock_read.return_value = {"fetched_at": 0, "status": "none"}
        
        # Call with background=True
        res = fetch_api_status(background=True)
        
        # Should return stale cache
        self.assertEqual(res["status"], "none")
        # Should have started a thread
        mock_thread.assert_called_once()

if __name__ == "__main__":
    unittest.main()
