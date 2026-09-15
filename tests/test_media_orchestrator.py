"""JARVIS Mark X — Тесты медиа-оркестратора, сессий, провайдеров и контроля безопасности."""


from core.media.controllers.base import BaseMediaController
from core.media.controllers.browser import BrowserMediaController
from core.media.controllers.spotify import SpotifyMediaController
from core.media.models import (
    MediaCapabilities,
    MediaRequest,
    MediaSession,
    MediaState,
    MediaType,
    parse_media_request,
)
from core.media.orchestrator import get_media_orchestrator
from core.media.router import ProviderRouter
from core.media.state import get_media_tracker
from core.media.bridge.server import BrowserBridgeServer
from actions.movie_player import movie_player
from actions.music_player import music_player


class MockTestController(BaseMediaController):
    def __init__(self):
        super().__init__(capabilities=MediaCapabilities(
            play_pause=True,
            seek_relative=True,
            seek_percent=True,
            volume_control=True,
            set_player_volume=True,
            set_system_volume=True,
            fullscreen=True,
        ))
        self.played = False
        self.paused = False
        self.stopped = False
        self.closed = False
        self.relative_seek_sec = 0.0
        self.percent_seek = 0.0
        self.volume_level = 100
        self.fullscreen_on = False

    def play(self) -> str:
        self.played = True
        return "Played"

    def pause(self) -> str:
        self.paused = True
        return "Paused"

    def stop(self) -> str:
        self.stopped = True
        return "Stopped"

    def close(self) -> str:
        self.closed = True
        return "Closed"

    def seek_relative(self, seconds: float) -> str:
        self.relative_seek_sec += seconds
        return f"Seek {seconds}"

    def seek_percent(self, percent: float) -> str:
        self.percent_seek = percent
        return f"Seek {percent}%"

    def set_volume(self, percent: int) -> str:
        self.volume_level = percent
        return f"Vol {percent}"

    def enter_fullscreen(self) -> str:
        self.fullscreen_on = True
        return "Fullscreen ON"

    def exit_fullscreen(self) -> str:
        self.fullscreen_on = False
        return "Fullscreen OFF"


def test_media_request_parsing():
    # 1. Фильм
    req1 = parse_media_request("Включи фильм Интерстеллар на VK")
    assert req1.media_type == MediaType.MOVIE
    assert "интерстеллар" in req1.title.lower()

    # 2. Сериал + сезон + серия
    req2 = parse_media_request("Включи 2 сезон 3 серию Очень странных дел")
    assert req2.media_type == MediaType.SERIES
    assert req2.season == 2
    assert req2.episode == 3
    assert "очень странных дел" in req2.title.lower()

    # 3. Музыка
    req3 = parse_media_request("Включи песню Queen Bohemian Rhapsody")
    assert req3.media_type == MediaType.MUSIC
    assert "queen" in req3.title.lower()


def test_media_session_tracker():
    tracker = get_media_tracker()
    tracker.clear_active_session()

    ctrl = MockTestController()
    session = MediaSession(
        media_type=MediaType.MOVIE,
        title="Тестовый Фильм",
        provider="vkvideo",
        status=MediaState.PLAYING,
        controller=ctrl,
    )
    tracker.set_active_session(session)

    active = tracker.get_active_session()
    assert active is not None
    assert active.title == "Тестовый Фильм"
    assert active.provider == "vkvideo"

    tracker.update_session_state(status=MediaState.PAUSED, volume=75)
    assert active.status == MediaState.PAUSED
    assert active.volume == 75

    tracker.clear_active_session()
    assert tracker.get_active_session() is None


def test_provider_router_fallback():
    router = ProviderRouter()
    req = MediaRequest(media_type=MediaType.MOVIE, title="Интерстеллар", provider="auto")
    res = router.resolve_and_open(req)
    assert res.success is True
    assert res.controller is not None


def test_orchestrator_playback_controls():
    orchestrator = get_media_orchestrator()
    orchestrator.tracker.clear_active_session()

    mock_ctrl = MockTestController()
    session = MediaSession(
        media_type=MediaType.MOVIE,
        title="Интерстеллар",
        provider="vkvideo",
        status=MediaState.PLAYING,
        controller=mock_ctrl,
        capabilities=mock_ctrl.capabilities,
    )
    orchestrator.tracker.set_active_session(session)

    orchestrator.pause()
    assert mock_ctrl.paused is True

    orchestrator.seek_relative(30)
    assert mock_ctrl.relative_seek_sec == 30.0

    orchestrator.seek_relative(-15)
    assert mock_ctrl.relative_seek_sec == 15.0

    orchestrator.seek_percent(50)
    assert mock_ctrl.percent_seek == 50.0

    orchestrator.set_volume(70)
    assert mock_ctrl.volume_level == 70

    orchestrator.enter_fullscreen()
    assert mock_ctrl.fullscreen_on is True

    # stop() ставит на паузу/сбрасывает, но СОХРАНЯЕТ сессию
    orchestrator.stop()
    assert mock_ctrl.stopped is True
    assert orchestrator.tracker.get_active_session() is not None
    assert orchestrator.tracker.get_active_session().status == MediaState.STOPPED

    # close() ЗАКРЫВАЕТ плеер и ОЧИЩАЕТ сессию
    orchestrator.close()
    assert mock_ctrl.closed is True
    assert orchestrator.tracker.get_active_session() is None


def test_stop_vs_close_intent_and_execution():
    """Проверка разделения stop() vs close() и парсинга интентов."""
    req_stop = parse_media_request("останови фильм")
    assert req_stop.action == "stop"

    req_close = parse_media_request("закрой фильм")
    assert req_close.action == "close"

    orchestrator = get_media_orchestrator()
    orchestrator.tracker.clear_active_session()

    ctrl = MockTestController()
    session = MediaSession(
        media_type=MediaType.MOVIE,
        title="Интерстеллар",
        provider="vkvideo",
        status=MediaState.PLAYING,
        controller=ctrl,
        capabilities=ctrl.capabilities,
    )
    orchestrator.tracker.set_active_session(session)

    res_stop = orchestrator.stop()
    assert "Stopped" in res_stop
    assert orchestrator.tracker.get_active_session() is not None

    res_close = orchestrator.close()
    assert "Closed" in res_close
    assert orchestrator.tracker.get_active_session() is None


def test_spotify_volume_capabilities():
    """Проверка разделения player vs system volume для Spotify."""
    spotify = SpotifyMediaController()
    assert spotify.capabilities.set_player_volume is False
    assert spotify.capabilities.set_system_volume is True

    res_player_vol = spotify.set_player_volume(60)
    assert "не поддерживается" in res_player_vol.lower()


def test_browser_bridge_token_security():
    """Проверка безопасности токена аутентификации браузерного моста."""
    server = BrowserBridgeServer.get_instance()
    token = server.token

    assert isinstance(token, str)
    assert len(token) >= 32
    assert server.verify_token(token) is True
    assert server.verify_token("invalid_token_123") is False
    assert server.verify_token("") is False


def test_gemini_tool_adapters_compatibility():
    play_res = movie_player({"action": "play", "title": "Матрица"})
    assert isinstance(play_res, str)

    pause_res = movie_player({"action": "pause"})
    assert isinstance(pause_res, str)

    seek_res = movie_player({"action": "seek_forward", "seconds": "30"})
    assert isinstance(seek_res, str)

    music_res = music_player({"action": "play", "query": "Queen"})
    assert isinstance(music_res, str)

    stop_res = music_player({"action": "stop"})
    assert isinstance(stop_res, str)


def test_search_penalty_scoring():
    from core.media.providers.vk import VKVideoProvider
    vk = VKVideoProvider()
    req = MediaRequest(media_type=MediaType.MOVIE, title="Интерстеллар")

    score_full = vk.score_candidate("Интерстеллар (2014) смотреть онлайн", req)
    score_trailer = vk.score_candidate("Интерстеллар — Официальный трейлер", req)

    assert score_full > score_trailer
    assert score_full - score_trailer >= 80.0


def test_window_focus_safety(monkeypatch):
    """Нет окна нужного провайдера — клавиша не уходит ни в какое другое окно.

    Без подмены проверка ходила по реальному рабочему столу: при фиктивном
    hwnd контроллер находил живой браузер разработчика и жал в нём пробел.
    """
    ctrl = BrowserMediaController(window_handle=9999999)
    monkeypatch.setattr(ctrl, "is_window_valid_and_targeted", lambda hwnd: False)
    sent = []
    monkeypatch.setattr("actions.keyboard.send_key", lambda key: sent.append(key) or True)

    res = ctrl._send_key_safe("space")

    assert res is False
    assert sent == [], "клавиша ушла в чужое окно"


def test_unsupported_capability_safety():
    orchestrator = get_media_orchestrator()
    orchestrator.tracker.clear_active_session()

    ctrl = MockTestController()
    ctrl.capabilities.seek_relative = False

    session = MediaSession(
        media_type=MediaType.MOVIE,
        title="Тест",
        provider="vkvideo",
        status=MediaState.PLAYING,
        controller=ctrl,
        capabilities=ctrl.capabilities,
    )
    orchestrator.tracker.set_active_session(session)

    res = orchestrator.seek_relative(30)
    assert "не поддерживается" in res.lower()