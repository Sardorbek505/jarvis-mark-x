"""Управление тем, что играет, через медиа-сессии Windows (как кнопки в
всплывашке громкости Windows 10/11). Работает для Spotify Free и Premium,
для браузера с YouTube / VK Видео — без Web API и без ключей.

Раньше пауза и «дальше» жались медиа-клавишей Play/Pause: это переключатель,
и «пауза» на уже стоящей музыке её запускала; клавишу ловило то, что
последним играло (вкладка браузера вместо Spotify), а ответ всегда был
«Готово». Здесь — явные команды нужной сессии и честный результат:
что играет, играет ли.

Нужны пакеты winrt-* (pywinrt). Нет их — запасной путь: медиа-клавиши из
процесса (без PowerShell).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_PLAYING, _PAUSED = 4, 5            # GlobalSystemMediaTransportControlsSessionPlaybackStatus


@dataclass
class NowPlaying:
    app: str
    title: str
    artist: str
    playing: bool


def _run(coro):
    """Инструменты зовутся из пула потоков — там своего цикла нет."""
    try:
        return asyncio.run(coro)
    except RuntimeError:                         # уже внутри цикла (тесты)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


async def _manager():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager)
    return await Manager.request_async()


def _pick(mgr, app: str | None):
    sessions = list(mgr.get_sessions())
    if app:
        want = app.lower()
        for s in sessions:
            if want in (s.source_app_user_model_id or "").lower():
                return s
        return None
    return mgr.get_current_session() or (sessions[0] if sessions else None)


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.media.control  # noqa: F401
        return True
    except Exception:
        return False


async def _now(app: str | None) -> NowPlaying | None:
    s = _pick(await _manager(), app)
    if s is None:
        return None
    props = await s.try_get_media_properties_async()
    status = s.get_playback_info().playback_status
    return NowPlaying(app=s.source_app_user_model_id or "", title=props.title or "",
                      artist=props.artist or "", playing=int(status) == _PLAYING)


def now_playing(app: str | None = None) -> NowPlaying | None:
    """Что играет (или стоит на паузе) в приложении app ('spotify') / в текущей сессии."""
    if not available():
        return None
    try:
        return _run(_now(app))
    except Exception as exc:
        logger.debug("Медиа-сессия: %s", exc)
        return None


async def _command(cmd: str, app: str | None) -> bool:
    s = _pick(await _manager(), app)
    if s is None:
        return False
    fn = {"play": s.try_play_async, "pause": s.try_pause_async,
          "next": s.try_skip_next_async, "previous": s.try_skip_previous_async,
          "toggle": s.try_toggle_play_pause_async, "stop": s.try_stop_async}[cmd]
    return bool(await fn())


def command(cmd: str, app: str | None = None) -> bool:
    """play / pause / next / previous / toggle / stop. True — сессия приняла.
    Нет winrt — медиа-клавиша (для play/pause — переключатель)."""
    if available():
        try:
            return _run(_command(cmd, app))
        except Exception as exc:
            logger.debug("Медиа-команда %s: %s", cmd, exc)
    return _media_key(cmd)


def wait_for(app: str, timeout: float = 8.0, playing: bool | None = None) -> NowPlaying | None:
    """Ждать, пока у приложения появится сессия (и, если задано, начнёт играть)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        np = now_playing(app)
        if np and (playing is None or np.playing == playing):
            return np
        time.sleep(0.4)
    return now_playing(app)


def _media_key(cmd: str) -> bool:
    vk = {"play": 0xB3, "pause": 0xB3, "toggle": 0xB3, "next": 0xB0,
          "previous": 0xB1, "stop": 0xB2}.get(cmd)
    if vk is None or sys.platform != "win32":
        return False
    import ctypes
    ctypes.windll.user32.keybd_event(vk, 0, 1, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 3, 0)
    return True


def app_volume(process: str, mode: str, step: int = 10, target: int | None = None) -> int | None:
    """Громкость одного приложения (Spotify), а не всей системы. % или None."""
    if sys.platform != "win32":
        return None
    import comtypes
    try:
        comtypes.CoInitialize()
    except OSError:
        pass
    try:
        from pycaw.pycaw import AudioUtilities
        for sess in AudioUtilities.GetAllSessions():
            if sess.Process and sess.Process.name().lower() == process.lower():
                vol = sess.SimpleAudioVolume
                now = round(vol.GetMasterVolume() * 100)
                new = (target if mode == "set" and target is not None
                       else min(100, now + step) if mode == "up" else max(0, now - step))
                vol.SetMasterVolume(new / 100, None)
                return new
        return None
    finally:
        try:
            comtypes.CoUninitialize()
        except OSError:
            pass
