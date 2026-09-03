"""Tests for JARVIS voice switcher and dynamic provider."""

import os
import json
import pytest
import main as jarvis_main


def test_voice_provider_get_set(tmp_path, monkeypatch):
    test_config = tmp_path / "api_keys.json"
    test_config.write_text(json.dumps({"jarvis_voice": "gemini"}), encoding="utf-8")
    monkeypatch.setattr(jarvis_main, "API_CONFIG", test_config)

    # Set to fish
    jarvis_main.set_voice_provider("fish")
    assert jarvis_main.get_voice_provider() == "fish"

    # Verify saved to config
    saved = json.loads(test_config.read_text(encoding="utf-8"))
    assert saved.get("jarvis_voice") == "fish"

    # Set back to gemini
    jarvis_main.set_voice_provider("gemini")
    assert jarvis_main.get_voice_provider() == "gemini"

    saved = json.loads(test_config.read_text(encoding="utf-8"))
    assert saved.get("jarvis_voice") == "gemini"


def test_switch_voice_tool_schema():
    tools = {t["name"]: t for t in jarvis_main.TOOLS}
    assert "switch_voice" in tools
    schema = tools["switch_voice"]
    assert "provider" in schema["parameters"]["properties"]
    assert "fish" in schema["parameters"]["properties"]["provider"]["enum"]
    assert "gemini" in schema["parameters"]["properties"]["provider"]["enum"]
