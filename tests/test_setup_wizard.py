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


_app = None


def _wizard(tmp_path, monkeypatch, devices):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    import sounddevice as sd
    global _app
    _app = QApplication.instance() or QApplication([])
    user_dir = tmp_path / "user"
    monkeypatch.setattr(core.paths, "get_user_data_dir", lambda: user_dir)
    monkeypatch.setattr(core.paths, "get_app_dir", lambda: tmp_path / "app")
    monkeypatch.setenv("JARVIS_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("MIC_DEVICE", raising=False)
    monkeypatch.delenv("EDGE_VOICE", raising=False)
    monkeypatch.setattr(sd, "query_devices", lambda *a, **kw: devices)
    monkeypatch.setattr(ui_setup, "set_windows_autostart", lambda enable: True)
    return ui_setup.SetupWizardDialog()


def test_мастер_сохраняет_голос_и_микрофон(tmp_path, monkeypatch):
    """Было: «Светлана» не сохранялась вовсе, микрофон — только до перезапуска."""
    from core import settings
    mics = [{"name": "Микрофон ноутбука", "max_input_channels": 2, "hostapi": 0},
            {"name": "Гарнитура USB", "max_input_channels": 1, "hostapi": 0}]
    w = _wizard(tmp_path, monkeypatch, mics)
    w.edit_gemini.setText("AIzaSy-test-key-1234567890")
    w.combo_edge.setCurrentIndex(w.combo_edge.findData("ru-RU-SvetlanaNeural"))
    w.combo_mic.setCurrentIndex(w.combo_mic.findData("Гарнитура USB"))
    w._save_and_start()

    assert settings.get("edge_voice") == "ru-RU-SvetlanaNeural"
    assert settings.get("mic") == "Гарнитура USB"
    assert settings.get("voice") == "gemini"

    # Повторное открытие мастера показывает сохранённое.
    again = _wizard(tmp_path, monkeypatch, mics)
    assert again.combo_edge.currentData() == "ru-RU-SvetlanaNeural"
    assert again.combo_mic.currentData() == "Гарнитура USB"


def test_мастер_fish_без_ключа_не_включает_fish(tmp_path, monkeypatch):
    from core import settings
    w = _wizard(tmp_path, monkeypatch, [])
    w.edit_gemini.setText("AIzaSy-test-key-1234567890")
    w.rb_fish.setChecked(True)
    w._save_and_start()
    assert settings.get("voice") == "gemini"


def test_глаз_показывает_ключ_с_первого_нажатия(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QLineEdit
    w = _wizard(tmp_path, monkeypatch, [])
    w.btn_toggle_key.click()
    assert w.edit_gemini.echoMode() == QLineEdit.EchoMode.Normal
    w.btn_toggle_key.click()
    assert w.edit_gemini.echoMode() == QLineEdit.EchoMode.Password
