"""JARVIS Mark X — Адаптер музыкального плеера (music_player).

Сохраняет 100% совместимость с внешним контрактом Gemini Live Tool Call:
  music_player(parameters, player=None)

Внутри транслирует вызовы в единый MediaOrchestrator. Низкоуровневые
десктопные функции Spotify живут в actions/spotify_desktop.py и здесь только
реэкспортируются — на них завязаны core/media, fast_command_router и routines.
"""

import logging
import time  # noqa: F401 — legacy-тесты подменяют music_player.time.sleep

from actions.browser_control import browser_control  # noqa: F401 — реэкспорт
from actions.keyboard import send_key as _send_key  # noqa: F401 — реэкспорт
from actions.spotify_desktop import (  # noqa: F401 — реэкспорт
    _find_youtube_direct_url,
    _focus_spotify_window,
    _https_to_spotify_uri,
    _is_spotify_installed,
    _is_spotify_running,
    _open_spotify_uri,
    _send_media_key,
    _spotify_search_track_uri,
    _ui_automation_search,
)
from core.media.models import MediaType
from core.media.orchestrator import get_media_orchestrator

logger = logging.getLogger(__name__)


def music_player(parameters: dict, player=None) -> str:
    """
    Главная точка входа для Gemini Live tool 'music_player'.

    parameters:
        action:       play | pause | resume | next | prev | stop |
                      volume_up | volume_down | now_playing
        query:        что играть для action=play (название трека / исполнителя / жанра)
        playlist_url: URL плейлиста для action=play (опционально)
    """
    action = (parameters.get("action") or "").strip().lower()
    orchestrator = get_media_orchestrator()

    if action in ("play", "start", "включить", "запустить"):
        query = (parameters.get("query") or parameters.get("title") or "").strip()
        # play_media ждёт СТРОКУ: объект MediaRequest там заново разбирался
        # и становился фильмом — музыка уходила искаться на VK Видео.
        res = orchestrator.play_media(query, {**parameters, "media_type": MediaType.MUSIC.value})
        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: 🎵 {res}")
        return res

    elif action in ("pause", "resume", "toggle", "пауза", "продолжай"):
        res = orchestrator.toggle_playback()
        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: ⏯ {res}")
        return res

    elif action in ("next", "next_track", "skip", "следующий"):
        res = orchestrator.next_track()
        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: ⏭ {res}")
        return res

    elif action in ("prev", "previous", "prev_track", "предыдущий"):
        res = orchestrator.previous_track()
        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: ⏮ {res}")
        return res

    elif action in ("stop", "стоп", "остановить"):
        res = orchestrator.stop()
        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: ⏹ {res}")
        return res

    elif action in ("close", "exit", "закрой", "закрыть"):
        res = orchestrator.close()
        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: ✕ {res}")
        return res

    elif action in ("volume_up", "louder", "громче"):
        return orchestrator.volume_up(10)

    elif action in ("volume_down", "quieter", "тише"):
        return orchestrator.volume_down(10)

    elif action in ("now_playing", "current_track", "what_playing", "что_играет", "трек", "песня"):
        try:
            from core.media_session_manager import MediaSessionManager
            speech = MediaSessionManager.get_now_playing_speech()
            if player and hasattr(player, "write_log"):
                player.write_log(f"SYS: 🎵 {speech}")
            return speech
        except Exception:
            return orchestrator.get_status_summary()

    return f"Не понял команду: «{action}»."


def _play(query: str = "", playlist_url: str = "", player=None) -> str:
    """Legacy-вход «включи музыку»: та же дорога, что у music_player(action=play)."""
    parameters = {"query": query, "playlist_url": playlist_url, "media_type": MediaType.MUSIC.value}
    if playlist_url and not query:
        parameters["url"] = playlist_url
    try:
        return get_media_orchestrator().play_media(query or playlist_url, parameters)
    except Exception as exc:
        logger.warning("music_player._play: %s", exc)
        return "Не удалось воспроизвести трек, сэр."
