"""JARVIS Mark X — Системный менеджер медиа-сессий (Windows GSMTC).

Интеграция с GlobalSystemMediaTransportControlsSessionManager (WinRT API).
Позволяет считывать метаданные текущего трека (название, исполнитель, альбом, статус)
из любого поддерживающего медиа-интерфейс приложения на Windows 10/11:
  - Spotify (десктопный и веб)
  - Google Chrome / Microsoft Edge / Firefox (YouTube, VK Видео, Кинопоиск, Яндекс Музыка)
  - Десктопные плееры (Яндекс Музыка, VLC, AIMP, Windows Media Player)
"""

import asyncio
import logging
import platform
import re
from typing import Any, Dict

logger = logging.getLogger("jarvis-media-session")

_OS = platform.system()

# Названия приложений для красивого вывода
_APP_NAMES = {
    "spotify": "Spotify",
    "chrome": "Chrome",
    "msedge": "Edge",
    "edge": "Edge",
    "firefox": "Firefox",
    "opera": "Opera",
    "brave": "Brave",
    "yandex": "Яндекс",
    "yandexmusic": "Яндекс Музыке",
    "vlc": "VLC",
    "aimp": "AIMP",
    "foobar2000": "Foobar",
}


def _clean_app_name(raw_id: str) -> str:
    """Преобразует source_app_user_model_id в понятное имя приложения."""
    if not raw_id:
        return ""
    low = raw_id.lower()
    for k in sorted(_APP_NAMES.keys(), key=len, reverse=True):
        if k in low:
            return _APP_NAMES[k]
    # Убираем .exe и лишние пути
    clean = re.sub(r"\.exe$", "", raw_id.split("\\")[-1], flags=re.IGNORECASE)
    return clean.capitalize() if clean else ""


class MediaSessionManager:
    """Менеджер опроса Windows GSMTC медиа-сессий."""

    @classmethod
    async def get_current_media_info_async(cls) -> Dict[str, Any]:
        """
        Асинхронно получает метаданные текущего воспроизведения.
        """
        if _OS != "Windows":
            return {"active": False, "reason": "not_windows"}

        try:
            import winrt.windows.media.control as wmc
        except ImportError:
            logger.debug("winrt.windows.media.control не установлен")
            return {"active": False, "reason": "winrt_not_available"}

        try:
            mgr = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
            if not mgr:
                return {"active": False, "reason": "manager_unavailable"}

            session = mgr.get_current_session()
            # Если текущая сессия не определена, ищем среди всех сессий ту, которая играет или на паузе
            if not session:
                sessions = mgr.get_sessions()
                for s in sessions:
                    try:
                        pb = s.get_playback_info()
                        if pb and pb.playback_status in (
                            wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING,
                            wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PAUSED,
                        ):
                            session = s
                            break
                    except Exception:
                        continue

            if not session:
                return {"active": False, "reason": "no_active_session"}

            # Получаем статус воспроизведения
            status_str = "unknown"
            try:
                pb = session.get_playback_info()
                if pb:
                    st = pb.playback_status
                    if st == wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                        status_str = "playing"
                    elif st == wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PAUSED:
                        status_str = "paused"
                    elif st == wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.STOPPED:
                        status_str = "stopped"
            except Exception as e:
                logger.debug("Playback info error: %s", e)

            # Получаем медиа-свойства (название, автор, альбом)
            props = await session.try_get_media_properties_async()
            if not props:
                return {"active": False, "reason": "properties_unavailable"}

            title = (props.title or "").strip()
            artist = (props.artist or "").strip()
            album_title = (props.album_title or "").strip()
            album_artist = (props.album_artist or "").strip()
            app_id = session.source_app_user_model_id or ""
            app_name = _clean_app_name(app_id)

            if not title and not artist:
                return {"active": False, "reason": "empty_metadata"}

            return {
                "active": True,
                "title": title,
                "artist": artist or album_artist,
                "album": album_title,
                "status": status_str,
                "app_name": app_name,
                "app_id": app_id,
            }
        except Exception as exc:
            logger.error("GSMTC query error: %s", exc)
            return {"active": False, "reason": str(exc)}

    @classmethod
    def get_current_media_info_sync(cls) -> Dict[str, Any]:
        """
        Синхронная обертка над get_current_media_info_async.
        Безопасно выполняется из любого потока или существующего event loop.
        """
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # Если уже внутри цикла событий, выполняем через concurrent.futures в отдельном потоке
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(lambda: asyncio.run(cls.get_current_media_info_async()))
                    return future.result(timeout=1.5)
            else:
                return asyncio.run(cls.get_current_media_info_async())
        except Exception as e:
            logger.debug("Media info sync wrapper error: %s", e)
            return {"active": False, "reason": str(e)}

    @classmethod
    def get_now_playing_speech(cls) -> str:
        """
        Формирует естественный ответ Джарвиса для озвучивания и вывода в лог.
        """
        info = cls.get_current_media_info_sync()
        if not info or not info.get("active"):
            return "Сейчас ничего не воспроизводится, сэр."

        title = info.get("title", "")
        artist = info.get("artist", "")
        app_name = info.get("app_name", "")
        status = info.get("status", "playing")

        app_suffix = f" в {app_name}" if app_name else ""

        if status == "paused":
            if artist and title:
                return f"На паузе стоит {artist} — «{title}»{app_suffix}, сэр."
            elif title:
                return f"На паузе стоит «{title}»{app_suffix}, сэр."
            else:
                return f"Воспроизведение приостановлено{app_suffix}, сэр."

        # status == "playing" или неизвестно
        if artist and title:
            return f"Сейчас играет {artist} — «{title}»{app_suffix}, сэр."
        elif title:
            return f"Сейчас играет «{title}»{app_suffix}, сэр."
        elif artist:
            return f"Сейчас играет трек исполнителя {artist}{app_suffix}, сэр."

        return "Сейчас что-то играет, но название трека не определено, сэр."


# ── Адресное управление приложением через системные медиа-сессии ──────────────
#
# Media-клавиша play/pause — переключатель и уходит «кому-то»; Web API Spotify
# без Premium отвечает 403. Windows же знает каждую сессию по имени процесса и
# умеет командовать именно ей.

_CONTROL_ACTIONS = ("pause", "play", "stop", "next", "previous", "toggle")


async def control_app_async(app_substring: str, action: str) -> bool:
    """`action` ∈ pause/play/stop/next/previous/toggle для сессии, чей
    source_app_user_model_id содержит `app_substring` (например «spotify»)."""
    if _OS != "Windows" or action not in _CONTROL_ACTIONS:
        return False
    try:
        import winrt.windows.media.control as wmc
    except ImportError:
        return False
    try:
        mgr = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
        if not mgr:
            return False
        needle = app_substring.lower()
        for session in mgr.get_sessions():
            app_id = (session.source_app_user_model_id or "").lower()
            if needle not in app_id:
                continue
            if action == "pause":
                return bool(await session.try_pause_async())
            if action == "play":
                return bool(await session.try_play_async())
            if action == "stop":
                return bool(await session.try_stop_async())
            if action == "next":
                return bool(await session.try_skip_next_async())
            if action == "previous":
                return bool(await session.try_skip_previous_async())
            return bool(await session.try_toggle_play_pause_async())
    except Exception as e:
        logger.debug("GSMTC control %s/%s: %s", app_substring, action, e)
    return False


def control_app(app_substring: str, action: str, timeout: float = 2.0) -> bool:
    """Синхронная обёртка: безопасна и из потока с работающим циклом событий."""
    import concurrent.futures

    def _run():
        return asyncio.run(control_app_async(app_substring, action))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return _run()
        except Exception as e:
            logger.debug("GSMTC control sync: %s", e)
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        try:
            return pool.submit(_run).result(timeout=timeout)
        except Exception as e:
            logger.debug("GSMTC control threaded: %s", e)
            return False
