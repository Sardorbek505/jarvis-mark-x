"""JARVIS Mark X — Тесты роутинга провайдеров (ProviderRouter).

Проверяет:
  1. Default provider для movie / series / episode — VK Video (https://vkvideo.ru/)
  2. Явное указание Кинопоиск / YouTube / VK с приоритетом над default
  3. ОТСУТСТВИЕ silent fallback: если фильм не найден на VK Video, не открывает YouTube/Kinopoisk.
"""

from unittest.mock import patch

from core.media.models import MediaType, parse_media_request
from core.media.providers.base import ProviderResult
from core.media.router import MEDIA_PROVIDER_DEFAULTS, ProviderRouter


def test_provider_router_defaults_mapping():
    assert MEDIA_PROVIDER_DEFAULTS["movie"] == "vk"
    assert MEDIA_PROVIDER_DEFAULTS["series"] == "vk"
    assert MEDIA_PROVIDER_DEFAULTS["episode"] == "vk"
    assert MEDIA_PROVIDER_DEFAULTS["music"] == "spotify"
    assert MEDIA_PROVIDER_DEFAULTS["video"] == "youtube"


def test_movie_default_routing_vk():
    router = ProviderRouter()
    req = parse_media_request("Включи Интерстеллар")
    assert req.media_type == MediaType.MOVIE
    assert req.title == "Интерстеллар"

    with patch.object(router.providers["vkvideo"], "search_and_open") as mock_vk:
        mock_vk.return_value = ProviderResult(
            success=True, url="https://vkvideo.ru/video123", message="VK Video", controller=None, provider="vk"
        )
        res = router.resolve_and_open(req)
        assert res.success is True
        assert res.provider == "vk"
        mock_vk.assert_called_once()


def test_series_default_routing_vk():
    router = ProviderRouter()
    req = parse_media_request("Включи 2 сезон 3 серию Очень странных дел")
    assert req.media_type == MediaType.SERIES
    assert req.season == 2
    assert req.episode == 3

    with patch.object(router.providers["vkvideo"], "search_and_open") as mock_vk:
        mock_vk.return_value = ProviderResult(
            success=True, url="https://vkvideo.ru/video456", message="VK Video", controller=None, provider="vk"
        )
        res = router.resolve_and_open(req)
        assert res.success is True
        mock_vk.assert_called_once()


def test_explicit_provider_kinopoisk():
    router = ProviderRouter()
    req = parse_media_request("Включи Интерстеллар на Кинопоиске")
    assert req.provider == "kinopoisk"

    with patch.object(router.providers["kinopoisk"], "search_and_open") as mock_kp, \
         patch.object(router.providers["vkvideo"], "search_and_open") as mock_vk:
        mock_kp.return_value = ProviderResult(
            success=True, url="https://kinopoisk.ru/1", message="Kinopoisk", controller=None, provider="kinopoisk"
        )
        res = router.resolve_and_open(req)
        assert res.success is True
        assert res.provider == "kinopoisk"
        mock_kp.assert_called_once()
        mock_vk.assert_not_called()


def test_explicit_provider_youtube():
    router = ProviderRouter()
    req = parse_media_request("Включи Интерстеллар через YouTube")
    assert req.provider == "youtube"

    with patch.object(router.providers["youtube"], "search_and_open") as mock_yt, \
         patch.object(router.providers["vkvideo"], "search_and_open") as mock_vk:
        mock_yt.return_value = ProviderResult(
            success=True, url="https://youtube.com/watch?v=1", message="YouTube", controller=None, provider="youtube"
        )
        res = router.resolve_and_open(req)
        assert res.success is True
        assert res.provider == "youtube"
        mock_yt.assert_called_once()
        mock_vk.assert_not_called()


def test_no_silent_fallback_when_vk_fails():
    """Если фильм не найден на VK Video, не открывать автоматически YouTube или Кинопоиск."""
    router = ProviderRouter()
    req = parse_media_request("Включи НесуществующийФильм")
    assert req.media_type in (MediaType.MOVIE, MediaType.UNKNOWN)

    with patch.object(router.providers["vkvideo"], "search_and_open") as mock_vk, \
         patch.object(router.providers["youtube"], "search_and_open") as mock_yt, \
         patch.object(router.providers["kinopoisk"], "search_and_open") as mock_kp:

        mock_vk.return_value = ProviderResult(
            success=False, url=None, message="Фильм не найден", controller=None, provider="vk"
        )

        res = router.resolve_and_open(req)
        assert res.success is False
        assert "vk" in res.message.lower()
        mock_vk.assert_called_once()
        # Проверяем что фаллбэки на YouTube и Кинопоиск НЕ вызывались!
        mock_yt.assert_not_called()
        mock_kp.assert_not_called()


def test_music_routing_queen_default_spotify():
    """'Включи Queen' должно маршрутизироваться в Spotify по умолчанию."""
    router = ProviderRouter()
    req = parse_media_request("Включи Queen")
    assert req.media_type == MediaType.MUSIC
    assert req.provider == "spotify"

    with patch.object(router.providers["spotify"], "search_and_open") as mock_spot:
        mock_spot.return_value = ProviderResult(
            success=True, url="spotify:track:1", message="Spotify", controller=None, provider="spotify"
        )
        res = router.resolve_and_open(req)
        assert res.success is True
        assert res.provider == "spotify"
        mock_spot.assert_called_once()


def test_music_routing_queen_explicit_youtube():
    """'Включи Queen на YouTube' должно маршрутизироваться на YouTube."""
    router = ProviderRouter()
    req = parse_media_request("Включи Queen на YouTube")
    assert req.media_type == MediaType.MUSIC
    assert req.provider == "youtube"

    with patch.object(router.providers["youtube"], "search_and_open") as mock_yt:
        mock_yt.return_value = ProviderResult(
            success=True, url="https://youtube.com/watch?v=1", message="YouTube", controller=None, provider="youtube"
        )
        res = router.resolve_and_open(req)
        assert res.success is True
        assert res.provider == "youtube"
        mock_yt.assert_called_once()


def test_movie_routing_interstellar_vk():
    """'Включи фильм Интерстеллар' должно маршрутизироваться в VK Video."""
    router = ProviderRouter()
    req = parse_media_request("Включи фильм Интерстеллар")
    assert req.media_type == MediaType.MOVIE
    assert req.provider == "vk"

    with patch.object(router.providers["vkvideo"], "search_and_open") as mock_vk:
        mock_vk.return_value = ProviderResult(
            success=True, url="https://vkvideo.ru/video1", message="VK Video", controller=None, provider="vk"
        )
        res = router.resolve_and_open(req)
        assert res.success is True
        assert res.provider == "vk"
        mock_vk.assert_called_once()


def test_daemon_thread_refuses_key_in_wrong_window():
    """Если активное окно — не окно плеера (например VS Code/терминал), нажатие клавиш блокируется."""
    from core.media.controllers.browser import BrowserMediaController
    ctrl = BrowserMediaController(provider_name="vkvideo")

    # Имитируем активное окно другого приложения (VS Code / Terminal)
    with patch.object(ctrl, "is_foreground_safe", return_value=False), \
         patch("actions.keyboard.send_key") as mock_send_key:
        assert ctrl.is_foreground_safe() is False
        res = ctrl._send_key_safe("enter")
        assert res is False
        mock_send_key.assert_not_called()


def test_daemon_thread_aborts_on_session_cancelled():
    """Фоновая задача прерывается немедленно при отмене сессии."""
    from core.media.models import MediaSession
    from core.media.state import get_media_tracker
    from core.media.controllers.browser import BrowserMediaController

    tracker = get_media_tracker()
    ctrl = BrowserMediaController(provider_name="vkvideo")
    session1 = MediaSession(session_id="session-1", title="VK Video 1", controller=ctrl)
    tracker.set_active_session(session1)

    # Симулируем создание новой сессии (переключение пользователем)
    session2 = MediaSession(session_id="session-2", title="Spotify Track")
    tracker.set_active_session(session2)

    assert session1.cancelled is True
    active = tracker.get_active_session()
    assert active.session_id == "session-2"


def test_yandex_browser_window_validation_empty_title():
    """Yandex Browser и Chromium окна с пустым заголовком валидируются корректно."""
    from core.media.controllers.browser import BrowserMediaController
    ctrl = BrowserMediaController(provider_name="vkvideo")

    with patch("ctypes.windll.user32.IsWindow", return_value=True), \
         patch("ctypes.windll.user32.IsWindowVisible", return_value=True), \
         patch("ctypes.windll.user32.GetWindowRect", side_effect=lambda hwnd, r: setattr(r._obj, 'right', 1920) or setattr(r._obj, 'bottom', 1080) or setattr(r._obj, 'left', 0) or setattr(r._obj, 'top', 0)), \
         patch("ctypes.windll.user32.GetClassNameW", side_effect=lambda hwnd, buf, size: setattr(buf, 'value', 'Chrome_Yandex_WidgetWin_1')), \
         patch("ctypes.windll.user32.GetWindowThreadProcessId", side_effect=lambda hwnd, pid: setattr(pid._obj, 'value', 1234)), \
         patch("psutil.Process") as mock_proc, \
         patch("ctypes.windll.user32.GetWindowTextLengthW", return_value=0):
        mock_proc.return_value.name.return_value = "browser.exe"
        assert ctrl.is_window_valid_and_targeted(1050426) is True


def test_non_browser_window_rejected():
    """Приложения типа ChatGPT/VS Code/Terminal не принимаются за окно браузера."""
    from core.media.controllers.browser import BrowserMediaController
    ctrl = BrowserMediaController(provider_name="vkvideo")

    with patch("ctypes.windll.user32.IsWindow", return_value=True), \
         patch("ctypes.windll.user32.IsWindowVisible", return_value=True), \
         patch("ctypes.windll.user32.GetWindowRect", side_effect=lambda hwnd, r: setattr(r._obj, 'right', 1920) or setattr(r._obj, 'bottom', 1080) or setattr(r._obj, 'left', 0) or setattr(r._obj, 'top', 0)), \
         patch("ctypes.windll.user32.GetClassNameW", side_effect=lambda hwnd, buf, size: setattr(buf, 'value', 'Chrome_WidgetWin_1')), \
         patch("ctypes.windll.user32.GetWindowThreadProcessId", side_effect=lambda hwnd, pid: setattr(pid._obj, 'value', 5678)), \
         patch("psutil.Process") as mock_proc:
        mock_proc.return_value.name.return_value = "chatgpt.exe"
        assert ctrl.is_window_valid_and_targeted(67620) is False


