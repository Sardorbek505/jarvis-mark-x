"""Тесты для мастера настройки Setup Wizard и работы с автозапуском."""
from unittest.mock import MagicMock, patch


import ui_setup
import core.paths


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
    # Полная изоляция от реального %APPDATA% и репозитория
    test_user_dir = tmp_path / "user_jarvis"
    test_app_dir = tmp_path / "app_jarvis"
    test_user_dir.mkdir(parents=True, exist_ok=True)
    test_app_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(core.paths, "get_user_data_dir", lambda: test_user_dir)
    monkeypatch.setattr(core.paths, "get_app_dir", lambda: test_app_dir)

    data = {"gemini_api_key": "test_isolated_key_123", "gemini_model": "gemini-2.5-flash"}
    assert ui_setup.save_config_data(data)

    loaded = ui_setup.load_config_data()
    assert loaded["gemini_api_key"] == "test_isolated_key_123"
    assert loaded["gemini_model"] == "gemini-2.5-flash"
