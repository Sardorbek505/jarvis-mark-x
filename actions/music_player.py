"""
Действие: продвинутое управление Spotify.

Решает проблему "Spotify не запускается" через несколько слоёв:
  1. Spotify URI scheme (spotify:, spotify:search:X, spotify:playlist:ID)
     — Windows автоматически открывает десктопный Spotify
  2. Web fallback (open.spotify.com) если десктопный не установлен
  3. Media keys (VK_MEDIA_PLAY_PAUSE и др) для управления воспроизведением
     — работают глобально, не нужно фокусировать окно

Поддерживаемые команды:
  play <query>    — поиск и запуск через Spotify
  pause / resume  — пауза/продолжить (media key)
  next / prev     — следующий / предыдущий трек
  stop            — остановить
  volume_up/down  — системная громкость
"""

import os
import platform
import subprocess
import sys
import time
import urllib.parse
from typing import Optional

from actions.computer_settings import computer_settings
from actions.browser_control import browser_control

_OS = platform.system()

# ─── Опциональная зависимость pyautogui ───────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = False
    _HAS_PYAUTOGUI = True
except Exception:
    _HAS_PYAUTOGUI = False


# ─── Windows Virtual-Key codes (для ctypes fallback без зависимостей) ─────────
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP       = 0xB2

KEYEVENTF_EXTENDEDKEY = 0x01
KEYEVENTF_KEYUP       = 0x02


# ─── Отправка media-клавиш ────────────────────────────────────────────────────
def _send_media_key_windows(vk_code: int) -> bool:
    """Отправка media-клавиши через Win32 keybd_event (без зависимостей)."""
    try:
        import ctypes
        ctypes.windll.user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY, 0)
        ctypes.windll.user32.keybd_event(
            vk_code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0
        )
        return True
    except Exception:
        return False


def _send_media_key(action: str) -> bool:
    """
    Кросс-платформенная отправка media-клавиш.
    action: playpause | next | prev | stop
    """
    # Уровень 1: pyautogui (быстро, кросс-платформенно)
    if _HAS_PYAUTOGUI:
        py_map = {
            "playpause": "playpause",
            "next":      "nexttrack",
            "prev":      "prevtrack",
            "stop":      "stop",
        }
        py_key = py_map.get(action)
        if py_key:
            try:
                pyautogui.press(py_key)
                return True
            except Exception:
                pass

    # Уровень 2: Windows Win32 API (без зависимостей)
    if _OS == "Windows":
        vk_map = {
            "playpause": VK_MEDIA_PLAY_PAUSE,
            "next":      VK_MEDIA_NEXT_TRACK,
            "prev":      VK_MEDIA_PREV_TRACK,
            "stop":      VK_MEDIA_STOP,
        }
        vk = vk_map.get(action)
        if vk:
            return _send_media_key_windows(vk)

    return False


# ─── Открытие Spotify ─────────────────────────────────────────────────────────
def _open_spotify_uri(uri: str) -> bool:
    """
    Открыть Spotify URI (spotify:, spotify:search:..., spotify:playlist:...).
    Windows автоматически роутит к десктопному приложению если установлено.
    """
    try:
        if _OS == "Windows":
            os.startfile(uri)
            return True
        elif _OS == "Darwin":
            subprocess.Popen(["open", uri])
            return True
        else:
            subprocess.Popen(
                ["xdg-open", uri],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
    except Exception:
        return False


def _https_to_spotify_uri(url: str) -> Optional[str]:
    """
    Конвертирует https://open.spotify.com/playlist/ID в spotify:playlist:ID.
    Возвращает None если не получилось.
    """
    if not url or "spotify.com" not in url:
        return None
    try:
        if "/playlist/" in url:
            pid = url.split("/playlist/")[-1].split("?")[0].split("/")[0]
            return f"spotify:playlist:{pid}"
        if "/album/" in url:
            aid = url.split("/album/")[-1].split("?")[0].split("/")[0]
            return f"spotify:album:{aid}"
        if "/track/" in url:
            tid = url.split("/track/")[-1].split("?")[0].split("/")[0]
            return f"spotify:track:{tid}"
        if "/artist/" in url:
            arid = url.split("/artist/")[-1].split("?")[0].split("/")[0]
            return f"spotify:artist:{arid}"
    except Exception:
        pass
    return None


def _focus_spotify_window() -> bool:
    """Активирует окно Spotify (если открыто) — для надёжной отправки клавиш."""
    try:
        import pygetwindow as gw
        for win in gw.getAllWindows():
            if "spotify" in (win.title or "").lower():
                try:
                    win.activate()
                    return True
                except Exception:
                    # На Windows иногда нужно сначала minimize+restore
                    try:
                        win.minimize()
                        time.sleep(0.1)
                        win.restore()
                        return True
                    except Exception:
                        pass
    except Exception:
        pass
    return False


# ─── Действия плеера ──────────────────────────────────────────────────────────
def _play(query: str = "", playlist_url: str = "", player=None) -> str:
    """
    Запускает Spotify и начинает воспроизведение.

    Стратегия:
      1. Если есть playlist_url — конвертируем в spotify: URI
      2. Если есть query — spotify:search:X
      3. Иначе — просто spotify:
      4. Ждём загрузки (2.5 сек)
      5. Активируем окно Spotify (если открыто)
      6. Отправляем media play/pause key — запускаем воспроизведение
      7. Если ничего не сработало — fallback на open.spotify.com в браузере
    """
    # Лог
    if player:
        if query:
            player.write_log(f"SYS: 🎵 Spotify — поиск «{query}»")
        elif playlist_url:
            player.write_log(f"SYS: 🎵 Spotify — плейлист")
        else:
            player.write_log("SYS: 🎵 Spotify — запуск")

    # Шаг 1-3: Открыть Spotify
    spotify_opened = False

    if playlist_url:
        # Сначала пробуем URI scheme (десктопный Spotify)
        uri = _https_to_spotify_uri(playlist_url)
        if uri:
            spotify_opened = _open_spotify_uri(uri)

        # Если URI не сработал — открываем HTTPS (web Spotify)
        if not spotify_opened:
            try:
                browser_control({"action": "go_to", "url": playlist_url}, player=player)
                spotify_opened = True
            except Exception:
                pass

    elif query:
        # Поиск через URI
        encoded = urllib.parse.quote(query)
        uri = f"spotify:search:{encoded}"
        spotify_opened = _open_spotify_uri(uri)

        # Web fallback
        if not spotify_opened:
            web_url = f"https://open.spotify.com/search/{encoded}"
            try:
                browser_control({"action": "go_to", "url": web_url}, player=player)
                spotify_opened = True
            except Exception:
                pass
    else:
        # Просто открыть Spotify
        spotify_opened = _open_spotify_uri("spotify:")
        if not spotify_opened:
            try:
                browser_control(
                    {"action": "go_to", "url": "https://open.spotify.com"},
                    player=player,
                )
                spotify_opened = True
            except Exception:
                pass

    if not spotify_opened:
        return "Не удалось открыть Spotify, сэр."

    # Шаг 4: Подождать загрузки
    time.sleep(2.5)

    # Шаг 5: Активировать окно Spotify (для надёжности)
    _focus_spotify_window()
    time.sleep(0.3)

    # Шаг 6: Запустить воспроизведение
    _send_media_key("playpause")

    # Ответ
    if query:
        return f"Включаю «{query}» в Spotify, сэр."
    if playlist_url:
        return "Плейлист запущен, сэр."
    return "Spotify готов, сэр."


def _pause_resume(player=None) -> str:
    if _send_media_key("playpause"):
        if player:
            player.write_log("SYS: ⏯ Music: pause/resume")
        return "Готово."
    return "Не получилось переключить, сэр."


def _next_track(player=None) -> str:
    if _send_media_key("next"):
        if player:
            player.write_log("SYS: ⏭ Следующий трек")
        return "Следующий трек."
    return "Не получилось переключить, сэр."


def _prev_track(player=None) -> str:
    if _send_media_key("prev"):
        if player:
            player.write_log("SYS: ⏮ Предыдущий трек")
        return "Предыдущий трек."
    return "Не получилось переключить, сэр."


def _stop_music(player=None) -> str:
    """Stop = pause (media stop часто не реагирует в Spotify)."""
    # Сначала пытаемся stop
    if _send_media_key("stop"):
        if player:
            player.write_log("SYS: ⏹ Stop")
        return "Музыка остановлена."
    # Fallback: pause через playpause
    if _send_media_key("playpause"):
        return "Музыка на паузе."
    return "Не получилось остановить, сэр."


def _volume(direction: str, player=None) -> str:
    action = "увеличить громкость" if direction == "up" else "уменьшить громкость"
    return computer_settings(
        {"action": action, "value": "10"},
        player=player,
    )


# ─── Публичная точка входа ────────────────────────────────────────────────────
def music_player(parameters: dict, player=None) -> str:
    """
    Главная точка входа для tool 'music_player'.

    parameters:
        action:       play | pause | resume | next | prev | stop |
                      volume_up | volume_down
        query:        что играть для action=play (название трека / исполнителя / жанра)
        playlist_url: URL плейлиста для action=play (опционально)
    """
    action = (parameters.get("action") or "").strip().lower()
    query = (parameters.get("query") or "").strip()
    playlist_url = (parameters.get("playlist_url") or "").strip()

    if action in ("play", "start", "включить", "запустить"):
        return _play(query, playlist_url, player)

    elif action in ("pause", "resume", "toggle", "пауза", "продолжай"):
        return _pause_resume(player)

    elif action in ("next", "next_track", "skip", "следующий"):
        return _next_track(player)

    elif action in ("prev", "previous", "prev_track", "предыдущий"):
        return _prev_track(player)

    elif action in ("stop", "стоп", "остановить"):
        return _stop_music(player)

    elif action in ("volume_up", "louder", "громче"):
        return _volume("up", player)

    elif action in ("volume_down", "quieter", "тише"):
        return _volume("down", player)

    return f"Не понял команду: «{action}»."
