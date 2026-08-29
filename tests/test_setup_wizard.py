"""Тесты для мастера настройки Setup Wizard и работы с автозапуском."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ui_setup


def test_validate_gemini_key_empty():
    ok, msg = ui_setup.validate_gemini_key("")
    assert not ok
    assert "пустым" in msg.lower()


def test_validate_gemini_key_too_short():
    ok, msg = ui_setup.validate_gemini_key("12345")
    assert not ok
    assert "короткий" in msg.lower()


def test_validate_gemini_key_valid_mock():
    with patch("google.genai.Client") as mock_client:
        instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "pong"
        instance.models.generate_content.return_value = mock_response
        mock_client.return_value = instance

        ok, msg = ui_setup.validate_gemini_key("AIzaSyD-dummy-valid-looking-key-123456789")
        assert ok
        assert "активен" in msg.lower()


def test_config_save_and_load(tmp_path, monkeypatch):
    test_config = tmp_path / "test_api_keys.json"
    monkeypatch.setattr(ui_setup, "_CONFIG_FILE", test_config)

    data = {"gemini_api_key": "test_key_abc", "gemini_model": "gemini-2.5-flash"}
    assert ui_setup.save_config_data(data)

    loaded = ui_setup.load_config_data()
    assert loaded["gemini_api_key"] == "test_key_abc"
    assert loaded["gemini_model"] == "gemini-2.5-flash"
