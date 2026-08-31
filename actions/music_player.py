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

import base64
import json
import os
import platform
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from actions.computer_settings import computer_settings
from actions.browser_control import browser_control

import logging

_logger = logging.getLogger(__name__)

_OS = platform.system()

# ─── Пути ─────────────────────────────────────────────────────────────────────
_BASE = Path(__file__).resolve().parent.parent
_API_CONFIG = _BASE / "config" / "api_keys.json"

# Кэш Spotify access token (срок 1 час)
_SPOTIFY_TOKEN: Optional[str] = None
_SPOTIFY_TOKEN_EXPIRES: float = 0.0

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
            except Exception as exc:
                _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

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
def _is_spotify_installed() -> bool:
    """Проверяет, установлен ли Spotify на системе."""
    try:
        if _OS == "Windows":
            # Проверяем реестр или путь к Spotify
            import winreg
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Spotify"
                )
                winreg.CloseKey(key)
                return True
            except Exception as exc:
                _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
            # Проверяем общий путь установки
            spotify_paths = [
                r"C:\Program Files\Spotify\Spotify.exe",
                r"C:\Program Files (x86)\Spotify\Spotify.exe",
                r"%LOCALAPPDATA%\Spotify\Spotify.exe",
            ]
            for path in spotify_paths:
                expanded = os.path.expandvars(path)
                if os.path.exists(expanded):
                    return True
        elif _OS == "Darwin":
            return os.path.exists("/Applications/Spotify.app")
        else:
            # Linux - проверяем common paths
            for path in ["/usr/bin/spotify", "/usr/local/bin/spotify"]:
                if os.path.exists(path):
                    return True
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    return False


def _is_valid_spotify_uri(uri: str) -> bool:
    """
    Принимаем только spotify: URI или https://open.spotify.com/...
    URI не должны содержать управляющих символов (защита от инъекции).
    """
    if not uri or not isinstance(uri, str):
        return False
    # Никаких управляющих символов
    if any(c in uri for c in ('"', "'", "\n", "\r", "\t", "&", "|", ";", "`", "$")):
        return False
    low = uri.lower().strip()
    return low.startswith(("spotify:", "https://open.spotify.com/", "http://open.spotify.com/"))


def _open_spotify_uri(uri: str) -> bool:
    """
    Открыть Spotify URI (spotify:, spotify:search:..., spotify:playlist:...).
    Windows автоматически роутит к десктопному приложению если установлено.
    """
    if not _is_valid_spotify_uri(uri):
        print(f"[music] ⛔ Невалидный Spotify URI: {uri[:80]}")
        return False
    try:
        if _OS == "Windows":
            # Метод 1: os.startfile (прямой запуск через системную ассоциацию)
            try:
                os.startfile(uri)
                return True
            except Exception as exc:
                _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
            # Метод 2: cmd.exe через список аргументов (shell=False).
            # URI идёт как отдельный аргумент — нет shell-injection.
            try:
                subprocess.Popen(
                    ["cmd.exe", "/c", "start", "", uri],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except Exception as exc:
                _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
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
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
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
                    except Exception as exc:
                        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    return False


# ─── Spotify Web API (для надёжного запуска треков) ───────────────────────────
def _get_spotify_credentials() -> Optional[tuple]:
    """Возвращает (client_id, client_secret) если они есть в api_keys.json."""
    try:
        with open(_API_CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cid = cfg.get("spotify_client_id", "").strip()
        secret = cfg.get("spotify_client_secret", "").strip()
        if cid and secret:
            return (cid, secret)
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    return None


def _get_spotify_token() -> Optional[str]:
    """
    Получает Spotify access token через Client Credentials flow.
    Кэширует токен на час (он валиден 1 час).
    """
    global _SPOTIFY_TOKEN, _SPOTIFY_TOKEN_EXPIRES

    # Возвращаем кэшированный токен если ещё валидный
    if _SPOTIFY_TOKEN and time.time() < _SPOTIFY_TOKEN_EXPIRES:
        return _SPOTIFY_TOKEN

    creds = _get_spotify_credentials()
    if not creds:
        return None

    cid, secret = creds
    auth_str = f"{cid}:{secret}"
    auth_b64 = base64.b64encode(auth_str.encode()).decode()

    try:
        req = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        token = data.get("access_token")
        if token:
            _SPOTIFY_TOKEN = token
            # Токен живёт 3600 сек, обновим чуть раньше для надёжности
            _SPOTIFY_TOKEN_EXPIRES = time.time() + data.get("expires_in", 3600) - 60
            return token
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    return None


def _spotify_search_track_uri(query: str) -> Optional[str]:
    """
    Ищет трек через Spotify Web API и возвращает его URI (spotify:track:ID).
    Пробует несколько вариантов запроса для лучшего результата.
    None если поиск не удался или нет credentials.
    """
    token = _get_spotify_token()
    if not token:
        return None

    # Пробуем разные варианты запроса
    query_variants = [
        query,  # Оригинальный запрос
        query.replace("ё", "е"),  # Замена ё на е
        query.replace("й", "i"),  # Замена й на i для транслитерации
        query.split()[0] if " " in query else query,  # Только первое слово
    ]

    for variant in query_variants:
        try:
            encoded = urllib.parse.quote(variant)
            url = f"https://api.spotify.com/v1/search?q={encoded}&type=track&limit=5"
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())

            items = data.get("tracks", {}).get("items", [])
            if items:
                # Ищем точное совпадение по названию
                for item in items:
                    track_name = item.get("name", "").lower()
                    if variant.lower() in track_name or track_name in variant.lower():
                        return item.get("uri")  # формат: spotify:track:ID
                # Если точного совпадения нет, возвращаем первый результат
                return items[0].get("uri")
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
            continue

    return None


def _ui_automation_search(query: str, player=None) -> bool:
    """
    Надёжный поиск и воспроизведение трека в Spotify через UI и буфер обмена.
    """
    if not _HAS_PYAUTOGUI:
        return False

    try:
        if player:
            player.write_log(f"SYS: 🎵 Поиск в Spotify: {query}")

        if not _open_spotify_uri("spotify:"):
            return False
        time.sleep(1.5)

        _focus_spotify_window()
        time.sleep(0.3)

        # Открываем поиск (Ctrl+L) и очищаем поле
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("backspace")
        time.sleep(0.1)

        # Вставляем запрос через буфер обмена (гарантия поддержки кириллицы/Unicode)
        try:
            import pyperclip
            pyperclip.copy(query)
            pyautogui.hotkey("ctrl", "v")
        except Exception:
            cmd = f"(New-Object -ComObject WScript.Shell).SendKeys(@'{query}'@)"
            subprocess.run(["powershell", "-Command", f"Set-Clipboard -Value '{query}'"], capture_output=True, timeout=2)
            pyautogui.hotkey("ctrl", "v")

        time.sleep(0.4)
        pyautogui.press("enter")
        time.sleep(1.2)

        # Запуск первого найденного трека (стрелка вниз -> Enter / Space)
        pyautogui.press("down")
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(0.4)
        pyautogui.press("space")

        if player:
            player.write_log("SYS: ✓ Трек в Spotify запущен")

        return True
    except Exception as e:
        if player:
            player.write_log(f"SYS: ✗ Spotify UI error: {e}")
        return False


# ─── Действия плеера ──────────────────────────────────────────────────────────
def _play(query: str = "", playlist_url: str = "", player=None) -> str:
    """
    Запускает музыку через Spotify, а при недоступности — через YouTube Music.
    """
    if player:
        if query:
            player.write_log(f"SYS: 🎵 Воспроизведение «{query}»")
        elif playlist_url:
            player.write_log("SYS: 🎵 Плейлист Spotify")
        else:
            player.write_log("SYS: 🎵 Запуск музыки")

    # ── Шаг 1: Обработка плейлиста ────────────────────────────────────────
    if playlist_url:
        uri = _https_to_spotify_uri(playlist_url)
        if uri:
            if player:
                player.write_log(f"SYS: → Открываю плейлист URI: {uri}")
            spotify_opened = _open_spotify_uri(uri)
        if not spotify_opened:
            try:
                browser_control({"action": "go_to", "url": playlist_url}, player=player)
                spotify_opened = True
            except Exception as exc:
                _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

        if not spotify_opened:
            return "Не удалось открыть плейлист, сэр."

        time.sleep(2.0)
        _focus_spotify_window()
        _send_media_key("playpause")
        return "Плейлист открыт и воспроизводится, сэр."

    # ── Шаг 2: Обработка запроса трека ─────────────────────────────────────
    if query:
        # Пробуем через Spotify API
        track_uri = _spotify_search_track_uri(query)
        if track_uri:
            if _open_spotify_uri(track_uri):
                time.sleep(2.0)
                _focus_spotify_window()
                _send_media_key("playpause")
                return f"Включаю «{query}» в Spotify, сэр."

        # Пробуем через UI automation в Spotify
        if _is_spotify_installed() and _ui_automation_search(query, player):
            return f"Включаю «{query}» в Spotify, сэр."

        # Резервный фолбэк: YouTube Music / YouTube
        encoded = urllib.parse.quote(query)
        yt_url = f"https://music.youtube.com/search?q={encoded}"
        try:
            browser_control({"action": "go_to", "url": yt_url}, player=player)
            time.sleep(1.5)
            _send_media_key("playpause")
            return f"Включаю «{query}» в YouTube Music, сэр."
        except Exception:
            return "Не удалось воспроизвести трек, сэр."

    # ── Шаг 3: Пустой запрос (продолжить или запустить Spotify) ──────────────
    if _is_spotify_installed():
        _open_spotify_uri("spotify:")
        time.sleep(1.5)
        _focus_spotify_window()
        _send_media_key("playpause")
        return "Музыка включена, сэр."
    else:
        _send_media_key("playpause")
        return "Воспроизведение, сэр."


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
