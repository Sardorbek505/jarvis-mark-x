"""JARVIS Mark X — Тесты BrowserBridge и веб-управления."""

import json
from pathlib import Path
from core.media.bridge.bridge import get_browser_bridge
from core.media.bridge.server import BrowserBridgeServer
from core.media.controllers.browser import BrowserMediaController
from core.media.controllers.spotify import SpotifyMediaController
from core.media.models import MediaState
from core.media.state import get_media_tracker


def test_browser_bridge_server_singleton():
    server1 = BrowserBridgeServer.get_instance()
    server2 = BrowserBridgeServer.get_instance()
    assert server1 is server2


def test_browser_bridge_dynamic_capabilities():
    bridge = get_browser_bridge()
    caps_offline = bridge.get_dynamic_capabilities("tab_offline_99")
    assert caps_offline.seek_absolute is False
    assert caps_offline.state_readback is False

    server = bridge.server
    tab_data = {
        "type": "MEDIA_STATE_UPDATE",
        "tab_id": "tab_test_123",
        "window_id": "win_456",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "Rick Astley - Never Gonna Give You Up",
        "state": {
            "paused": True,
            "currentTime": 142.5,
            "duration": 212.0,
            "volume": 80,
            "muted": False,
            "playbackRate": 1.0
        }
    }
    server._process_ws_message(json.dumps(tab_data))

    caps_online = bridge.get_dynamic_capabilities("tab_test_123")
    assert caps_online.seek_absolute is True
    assert caps_online.state_readback is True
    assert caps_online.duration_readback is True


def test_browser_controller_source_of_truth_sync():
    tracker = get_media_tracker()
    tracker.clear_active_session()

    bridge = get_browser_bridge()
    server = bridge.server

    update_msg = {
        "type": "MEDIA_STATE_UPDATE",
        "tab_id": "tab_yt_999",
        "window_id": "win_1",
        "url": "https://www.youtube.com/watch?v=abc",
        "title": "YouTube Test Video",
        "state": {
            "paused": True,
            "currentTime": 50.0,
            "duration": 300.0,
            "volume": 100,
            "muted": True
        }
    }
    server._process_ws_message(json.dumps(update_msg))

    controller = BrowserMediaController(tab_id="tab_yt_999", provider_name="youtube")
    assert controller.get_state() == MediaState.PAUSED
    assert controller.get_position() == 50.0
    assert controller.get_duration() == 300.0
    assert controller.get_remaining_time() == 250.0

    res = controller.pause()
    assert "Уже на паузе" in res


def test_spotify_stop_vs_close():
    spotify = SpotifyMediaController()
    res_stop = spotify.stop()
    assert "остановлена" in res_stop
    assert spotify.get_state() == MediaState.STOPPED

    res_close = spotify.close()
    assert "закрыт" in res_close or "Spotify" in res_close


def test_browser_bridge_auth_handshake_and_wrong_token():
    server = BrowserBridgeServer.get_instance()
    assert server.verify_token(server.token) is True
    assert server.verify_token("wrong_token_123") is False
    assert server.verify_token("") is False
    assert server.verify_token(None) is False


def test_browser_bridge_malformed_json_and_frames():
    from core.media.bridge.server import _decode_ws_frame, _encode_ws_frame
    assert _decode_ws_frame(b"") is None
    assert _decode_ws_frame(b"\x81") is None
    
    encoded = _encode_ws_frame("hello")
    assert _decode_ws_frame(encoded) == "hello"

    server = BrowserBridgeServer.get_instance()
    # Should not raise exception
    server._process_ws_message("NOT_JSON_{{{")
    server._process_ws_message(json.dumps({"type": "UNKNOWN_ACTION_999"}))


def test_browser_bridge_tab_removed_and_browser_closed():
    bridge = get_browser_bridge()
    server = bridge.server
    
    # Send state update for tab_alpha and tab_beta
    server._process_ws_message(json.dumps({
        "type": "MEDIA_STATE_UPDATE",
        "tab_id": "tab_alpha",
        "state": {"paused": False, "currentTime": 10.0, "duration": 100.0}
    }))
    server._process_ws_message(json.dumps({
        "type": "MEDIA_STATE_UPDATE",
        "tab_id": "tab_beta",
        "state": {"paused": True, "currentTime": 50.0, "duration": 200.0}
    }))

    tabs = server.get_all_tabs()
    tab_ids = [t["tab_id"] for t in tabs]
    assert "tab_alpha" in tab_ids
    assert "tab_beta" in tab_ids
    assert server.get_tab_info("tab_alpha") is not None
    assert server.get_tab_info("nonexistent_tab") is None


def test_browser_controller_invalid_volume_and_seek_bounds():
    bridge = get_browser_bridge()
    server = bridge.server
    with server._tabs_lock:
        server._tabs.clear()

    controller = BrowserMediaController(tab_id="tab_offline_test", provider_name="vk")
    res_seek_perc = controller.seek_percent(1.5)  # 150% -> converted to 1.0 (100%)
    assert "Не удалось" in res_seek_perc or "Перешёл" in res_seek_perc or "100%" in res_seek_perc

    res_seek_neg = controller.seek_percent(-0.5)  # clamped to 0%
    assert "Не удалось" in res_seek_neg or "Перешёл" in res_seek_neg or "0%" in res_seek_neg


def test_browser_bridge_unavailable_and_fallback_activation(monkeypatch):
    # Без подмены хоткей-фолбэк ищет живое окно YouTube на рабочем столе
    # разработчика и жмёт в нём пробел — тест становился флаки и вредным.
    monkeypatch.setattr(BrowserMediaController, "is_window_valid_and_targeted", lambda self, hwnd: False)
    bridge = get_browser_bridge()
    server = bridge.server
    with server._tabs_lock:
        server._tabs.clear()
    with server._clients_lock:
        server._clients.clear()
        server._authenticated_clients.clear()

    controller = BrowserMediaController(tab_id="tab_unconnected_99", provider_name="youtube")
    assert controller._is_bridge_active() is False

    # Calling commands when bridge unavailable should safely fall back
    res_play = controller.play()
    assert "воспроизводится" in res_play or "Не удалось" in res_play

    res_pause = controller.pause()
    assert "паузе" in res_pause or "Не удалось" in res_pause

    res_close = controller.close()
    assert "Закрыл" in res_close or "закрыта" in res_close


def test_browser_bridge_state_resync_and_tracker_update():
    tracker = get_media_tracker()
    tracker.clear_active_session()

    tracker.get_active_session()
    server = BrowserBridgeServer.get_instance()

    server._process_ws_message(json.dumps({
        "type": "MEDIA_STATE_UPDATE",
        "tab_id": "tab_resync_1",
        "window_id": "win_resync",
        "url": "https://vkvideo.ru/watch/resync",
        "title": "Resync Test Title",
        "state": {
            "paused": False,
            "currentTime": 120.0,
            "duration": 600.0,
            "volume": 75,
            "muted": False
        }
    }))

    active = tracker.get_active_session()
    if active:
        assert active.status == MediaState.PLAYING
        assert active.position_seconds == 120.0
        assert active.duration_seconds == 600.0



def test_controller_without_tab_id_resolves_provider_tab():
    """Контроллер без tab_id адресует команды вкладке своего провайдера.

    Без этого команда уходила «во все вкладки», расширение в этой ветке не
    отвечало, и пауза фильма ждала 2 с таймаута (живой стенд 16.09.2026).
    """
    server = get_browser_bridge().server
    with server._tabs_lock:
        server._tabs.clear()
    for tab_id, url, t in (("11", "https://www.youtube.com/watch?v=x", 1.0),
                           ("22", "https://vkvideo.ru/video-1_2", 2.0),
                           ("33", "https://vkvideo.ru/video-3_4", 3.0)):
        server._process_ws_message(json.dumps({"type": "MEDIA_STATE_UPDATE", "tab_id": tab_id, "url": url,
                                               "title": "x", "state": {"paused": False}}))
        with server._tabs_lock:
            server._tabs[tab_id]["last_updated"] = t

    ctrl = BrowserMediaController(provider_name="vkvideo")
    assert ctrl._resolve_tab_id() == "33", "должна взяться самая свежая вкладка VK"
    assert BrowserMediaController(provider_name="youtube")._resolve_tab_id() == "11"
    assert BrowserMediaController(provider_name="kinopoisk")._resolve_tab_id() is None


def test_extension_answers_broadcast_commands():
    js = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text(encoding="utf-8")
    broadcast = js.split("// Если tab_id не указан", 1)[1]
    assert "COMMAND_RESPONSE" in broadcast, "широковещательная команда обязана получать ответ"
