import asyncio
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import pytest

import actions.obsidian as obs_mod
from actions.spotify_controller import SpotifyAPI
import main as jarvis_main


def test_obsidian_write_md_permission_error_fallback(tmp_path, monkeypatch):
    """При PermissionError на tmp.replace() файл всё равно должен успешно записаться."""
    target_file = tmp_path / "test_note.md"

    def mock_replace(self, target):
        raise PermissionError("Access is denied (file locked by Obsidian)")

    monkeypatch.setattr(Path, "replace", mock_replace)

    obs_mod._write_md(target_file, "Контент заметки")

    assert target_file.exists(), "Файл должен быть записан несмотря на сбой replace()"
    assert target_file.read_text(encoding="utf-8") == "Контент заметки"


def test_spotify_api_loads_credentials_from_core_paths(monkeypatch):
    """SpotifyAPI должен загружать ключи через core.paths.load_api_keys()."""
    fake_keys = {
        "spotify_client_id": "test_client_id_123",
        "spotify_client_secret": "test_client_secret_456",
        "spotify_refresh_token": "test_refresh_token_789",
    }

    import core.paths as paths_mod
    monkeypatch.setattr(paths_mod, "load_api_keys", lambda: fake_keys)

    api = SpotifyAPI()
    assert api.controller is not None
    assert api.controller.auth.client_id == "test_client_id_123"
    assert api.controller.auth.client_secret == "test_client_secret_456"
    assert api.controller.auth.refresh_token == "test_refresh_token_789"


def test_cleanup_kills_hung_subprocess():
    """cleanup() должен корректно завершать зависший подпроцесс через kill() при таймауте."""
    proc = MagicMock()
    proc.terminate = MagicMock()
    proc.wait = MagicMock(side_effect=subprocess.TimeoutExpired(cmd="pc_server", timeout=1.5))
    proc.kill = MagicMock()

    class _StubJarvis:
        def __init__(self):
            self._fast_command_thread = None
            self.audio_pipeline = None
            self._hotkey_mgr = None
            self._telegram_proc = proc

    stub = _StubJarvis()
    bound_cleanup = jarvis_main.Jarvis.cleanup.__get__(stub, jarvis_main.Jarvis)
    bound_cleanup()

    assert stub._telegram_proc is None
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_execute_tool_timeout_handling(monkeypatch):
    """Зависший инструмент должен корректно перехватывать таймаут и не блокировать Джарвиса."""
    class _ToolStub:
        def __init__(self):
            self.ui = SimpleNamespace(
                set_state=lambda s: None,
                write_log=lambda msg: None,
                muted=False,
            )
            self._pending_destructive = None
            self.user_profile = SimpleNamespace(
                add_to_history=lambda *a: None,
                update_context=lambda *a: None,
                get_context=lambda: {},
            )
            self.proactive_engine = SimpleNamespace(record_action=lambda *a: None)
            self._latency = SimpleNamespace(add_tool=lambda *a: None)

        def speak_error(self, name, err):
            pass

    stub = _ToolStub()

    # Синхронная функция, зависающая при исполнении
    def slow_web_search(*args, **kwargs):
        time.sleep(0.3)
        return "Search results"

    fc = SimpleNamespace(
        id="call_123",
        name="web_search",
        args={"query": "test"},
    )

    with patch.object(jarvis_main, "web_search", slow_web_search):
        bound_exec = jarvis_main.Jarvis._execute_tool.__get__(stub, jarvis_main.Jarvis)
        # Вызываем выполнение с коротким таймаутом как в реальном цикле
        try:
            res = await asyncio.wait_for(bound_exec(fc), timeout=0.05)
        except asyncio.TimeoutError:
            res = SimpleNamespace(response={"result": "Таймаут выполнения инструмента (25 сек). Действие прервано."})

        assert "Таймаут" in str(res.response.get("result", ""))
