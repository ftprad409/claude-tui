import unittest
import os
import json
from unittest.mock import patch

from claude_tui_components.utils import visible_len, truncate, visual_rows
from claude_tui_components.colors import BOLD, RED, RESET
from claude_tui_components.settings import get_setting
from claude_tui_components.widgets import build_progress_bar, build_sparkline

class TestUtils(unittest.TestCase):
    def test_visible_len(self):
        plain = "hello world"
        colored = f"{BOLD}{RED}hello world{RESET}"
        self.assertEqual(visible_len(plain), 11)
        self.assertEqual(visible_len(colored), 11)

    def test_truncate(self):
        colored = f"{BOLD}{RED}hello world{RESET}"
        self.assertEqual(visible_len(truncate(colored, 5)), 5)
        # Should keep ANSI codes despite truncation
        trunc = truncate(colored, 5)
        self.assertTrue("\033[" in trunc)
        self.assertTrue("hello" in trunc or "hell\u2026" in trunc)  # 'hello'

    def test_visual_rows(self):
        self.assertEqual(visual_rows(["hello"], 10), 1)
        # 11 chars on 10 width = 2 rows
        self.assertEqual(visual_rows(["hello world"], 10), 2)
        # with ansi
        colored = f"{BOLD}{RED}hello world{RESET}"
        self.assertEqual(visual_rows([colored], 10), 2)


class TestSettings(unittest.TestCase):
    @patch("claude_tui_components.settings.load_settings")
    def test_get_setting(self, mock_load):
        mock_load.return_value = {
            "sparkline": {"mode": "tail"},
            "enabled": True
        }
        self.assertEqual(get_setting("sparkline", "mode"), "tail")
        self.assertEqual(get_setting("enabled"), True)
        self.assertEqual(get_setting("sparkline", "missing", default="default_val"), "default_val")
        self.assertEqual(get_setting("missing_section", default=5), 5)


class TestWidgets(unittest.TestCase):
    def test_build_progress_bar(self):
        # Default behavior
        bar = build_progress_bar(0.5, length=20)
        self.assertTrue("%" in bar)
        self.assertTrue(visible_len(bar) > 20) # length 20 + padding borders + percentage text
        
        # Test 0 and 1
        bar_0 = build_progress_bar(0.0, length=10)
        self.assertTrue("0%" in bar_0)
        
        bar_1 = build_progress_bar(1.0, length=10)
        self.assertTrue("100%" in bar_1)

    def test_build_sparkline_tail_mode(self):
        values = [1, 2, 3, None, 5, 6]
        spark = build_sparkline(values, width=10, mode="tail")
        
        # It should produce a string that has visual length equal to width
        self.assertEqual(visible_len(spark), 6)
        # It should contain ANSI escapes for true color
        self.assertTrue("\033[38;2" in spark)
        
    def test_build_sparkline_merge_mode(self):
        values = [1, 2, 3, 4, 10, None, 5]
        spark = build_sparkline(values, width=10, mode="merge", merge_size=2)
        self.assertEqual(visible_len(spark), 4) # 7 elements / 2 merge size = 4 buckets

if __name__ == "__main__":
    unittest.main()
