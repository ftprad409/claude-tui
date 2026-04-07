#!/usr/bin/env python3
"""Tests for claude-ui-mode.py mode switching functionality."""

import sys

sys.path.insert(0, "/Users/slim/dev/claude-tui")

import pytest
import os
import tempfile
import json
import importlib.util
from unittest.mock import patch, MagicMock

# Load module from file
spec = importlib.util.spec_from_file_location(
    "claude_ui_mode", "/Users/slim/dev/claude-tui/claude-ui-mode.py"
)
mode_module = importlib.util.module_from_spec(spec)
sys.modules["claude_ui_mode"] = mode_module
spec.loader.exec_module(mode_module)

# Use temp directory for tests
TEST_DIR = tempfile.mkdtemp()
CLAUDE_DIR = os.path.join(TEST_DIR, ".claude")
CONFIG_PATH = os.path.join(CLAUDE_DIR, "claudeui.json")
SETTINGS_PATH = os.path.join(CLAUDE_DIR, "settings.json")

os.makedirs(CLAUDE_DIR, exist_ok=True)

# Patch the paths in the module
mode_module.CONFIG_PATH = CONFIG_PATH
mode_module.SETTINGS_PATH = SETTINGS_PATH


@pytest.fixture(autouse=True)
def setup_test_env():
    """Reset module state for each test."""
    mode_module._SETTINGS_CACHE = None
    mode_module._SETTINGS_MTIME = 0
    yield


@pytest.fixture
def clean_config():
    """Clean config before each test."""
    if os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)
    if os.path.exists(SETTINGS_PATH):
        os.remove(SETTINGS_PATH)
    # Reset module cache
    mode_module._SETTINGS_CACHE = None
    mode_module._SETTINGS_MTIME = 0
    yield
    # Cleanup after
    if os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)
    if os.path.exists(SETTINGS_PATH):
        os.remove(SETTINGS_PATH)


@pytest.fixture
def clean_config():
    """Clean config before each test."""
    if os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)
    if os.path.exists(SETTINGS_PATH):
        os.remove(SETTINGS_PATH)
    yield
    # Cleanup after
    if os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)
    if os.path.exists(SETTINGS_PATH):
        os.remove(SETTINGS_PATH)


def create_settings(statusline_cmd: str):
    """Create settings.json with statusLine command."""
    with open(SETTINGS_PATH, "w") as f:
        json.dump({"statusLine": {"type": "command", "command": statusline_cmd}}, f)


def create_config(custom: dict = None):
    """Create claudeui.json with custom config."""
    cfg = {}
    if custom:
        cfg["custom"] = custom
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f)


class TestShowCurrent:
    """Tests for show_current function."""

    def test_compact_mode_detection(self, clean_config, capsys):
        create_settings("python3 /path/to/statusline.py --compact")

        mode_module.show_current()

        output = capsys.readouterr().out
        assert "compact" in output

    def test_full_mode_detection(self, clean_config, capsys):
        create_settings("python3 /path/to/statusline.py")
        create_config({})  # Empty custom = full mode

        mode_module.show_current()

        output = capsys.readouterr().out
        assert "full" in output

    def test_custom_mode_detection(self, clean_config, capsys):
        create_settings("python3 /path/to/statusline.py")
        create_config({"line1": {"model": True, "context_bar": True}})

        mode_module.show_current()

        output = capsys.readouterr().out
        assert "custom" in output

    def test_no_statusline_configured(self, clean_config, capsys):
        # Don't create settings file
        mode_module.show_current()

        output = capsys.readouterr().out
        assert "No statusline configured" in output


class TestSetMode:
    """Tests for set_mode function."""

    def test_set_compact_mode(self, clean_config, capsys):
        create_settings("python3 /path/to/statusline.py")

        mode_module.set_mode("compact")

        with open(SETTINGS_PATH) as f:
            settings = json.load(f)

        assert "--compact" in settings["statusLine"]["command"]

    def test_set_full_mode_removes_compact(self, clean_config, capsys):
        create_settings("python3 /path/to/statusline.py --compact")

        mode_module.set_mode("full")

        with open(SETTINGS_PATH) as f:
            settings = json.load(f)

        assert "--compact" not in settings["statusLine"]["command"]

    def test_set_custom_mode(self, clean_config, capsys):
        create_settings("python3 /path/to/statusline.py --compact")

        mode_module.set_mode("custom")

        with open(SETTINGS_PATH) as f:
            settings = json.load(f)

        # Custom mode should NOT have --compact
        assert "--compact" not in settings["statusLine"]["command"]

    def test_set_mode_preserves_base_command(self, clean_config, capsys):
        create_settings("STATUSLINE_WIDGET=bars python3 /path/to/statusline.py")

        mode_module.set_mode("compact")

        with open(SETTINGS_PATH) as f:
            settings = json.load(f)

        cmd = settings["statusLine"]["command"]
        assert "STATUSLINE_WIDGET=bars" in cmd
        assert "/path/to/statusline.py --compact" in cmd


class TestCustomModeCommand:
    """Tests for 'claudetui mode custom' behavior."""

    @patch("claude_ui_mode.cmd_custom")
    def test_custom_from_compact_removes_flag(
        self, mock_custom, clean_config, monkeypatch, capsys
    ):
        create_settings("python3 /path/to/statusline.py --compact")
        monkeypatch.setattr("sys.argv", ["claude-ui-mode", "custom"])

        mode_module.main()

        # Check that settings no longer have --compact
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)

        assert "--compact" not in settings["statusLine"]["command"]

    @patch("claude_ui_mode.cmd_custom")
    def test_custom_from_full_keeps_command(
        self, mock_custom, clean_config, monkeypatch, capsys
    ):
        create_settings("python3 /path/to/statusline.py")
        monkeypatch.setattr("sys.argv", ["claude-ui-mode", "custom"])

        mode_module.main()

        # cmd_custom should be called
        mock_custom.assert_called_once()


class TestModeCommands:
    """Integration tests for mode command line interface."""

    def test_mode_full_command(self, clean_config, monkeypatch, capsys):
        create_settings("python3 /path/to/statusline.py")
        monkeypatch.setattr("sys.argv", ["claude-ui-mode", "full"])

        mode_module.main()

        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
        assert "--compact" not in settings["statusLine"]["command"]

    def test_mode_compact_command(self, clean_config, monkeypatch, capsys):
        create_settings("python3 /path/to/statusline.py")
        monkeypatch.setattr("sys.argv", ["claude-ui-mode", "compact"])

        mode_module.main()

        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
        assert "--compact" in settings["statusLine"]["command"]

    def test_mode_custom_command(self, clean_config, monkeypatch, capsys):
        create_settings("python3 /path/to/statusline.py")
        monkeypatch.setattr("sys.argv", ["claude-ui-mode", "custom"])

        with patch("claude_ui_mode.cmd_custom") as mock_custom:
            mode_module.main()
            mock_custom.assert_called_once()

        # Should remove --compact when switching from compact
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
        assert "--compact" not in settings["statusLine"]["command"]

    def test_unknown_command(self, clean_config, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["claude-ui-mode", "invalid"])

        with pytest.raises(SystemExit):
            mode_module.main()

        output = capsys.readouterr().out
        assert "Unknown command" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
