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
import glob
import json
import os
import platform
import subprocess
import time
import re
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
            # Версия из Microsoft Store: ни ключа HKCU\Software\Spotify, ни
            # папки в Program Files. Значение имеет одно — зарегистрирован ли
            # протокол spotify:, потому что трек мы открываем именно им.
            try:
                key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                                     r"spotify\shell\open\command")
                winreg.CloseKey(key)
                return True
            except Exception as exc:
                _logger.debug("Протокол spotify: не зарегистрирован: %s", exc)
            if glob.glob(os.path.expandvars(
                    r"%LOCALAPPDATA%\Packages\SpotifyAB.SpotifyMusic*")):
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
        import ctypes
        import pygetwindow as gw
        user32 = ctypes.windll.user32
        for win in gw.getAllWindows():
            if "spotify" in (win.title or "").lower():
                hwnd = win._hWnd
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                else:
                    user32.ShowWindow(hwnd, 5)  # SW_SHOW
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.15)
                return True
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    return False


def _foreground_title() -> str:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:
        return ""


def _spotify_in_front() -> bool:
    """Клавиши шлём, только если впереди именно Spotify.

    Windows часто не даёт фоновому процессу вывести окно вперёд, и раньше
    Ctrl+A, Ctrl+V (запрос) и Enter уходили в то, что было открыто:
    заменяли текст в документе или отправляли запрос сообщением в Telegram.
    """
    return "spotify" in _foreground_title().lower()


# ─── Spotify Web API (для надёжного запуска треков) ───────────────────────────
def _get_spotify_credentials() -> Optional[tuple]:
    """(client_id, client_secret) из ключей Джарвиса, если есть."""
    try:
        from core.paths import load_api_keys
        keys = load_api_keys()
    except Exception:
        return None
    cid = (keys.get("spotify_client_id") or "").strip()
    sec = (keys.get("spotify_client_secret") or "").strip()
    return (cid, sec) if cid and sec else None


def _get_spotify_token() -> Optional[str]:
    """
    Получает Spotify access token через Client Credentials flow.
    Кэширует токен на час (он валиден 1 час).
    """
    global _SPOTIFY_TOKEN, _SPOTIFY_TOKEN_EXPIRES

    if _SPOTIFY_TOKEN and time.time() < _SPOTIFY_TOKEN_EXPIRES:
        return _SPOTIFY_TOKEN

    creds = _get_spotify_credentials()
    if not creds:
        return None

    cid, secret = creds
    try:
        auth_bytes = f"{cid}:{secret}".encode("utf-8")
        auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
        req = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        token = data.get("access_token")
        if token:
            _SPOTIFY_TOKEN = token
            _SPOTIFY_TOKEN_EXPIRES = time.time() + data.get("expires_in", 3600) - 60
            return token
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    return None


def _spotify_search_track_uri(query: str) -> Optional[str]:
    """
    Ищет трек через Spotify Web API и возвращает его URI (spotify:track:ID).
    """
    token = _get_spotify_token()
    if not token:
        return None

    query_variants = [
        query,
        query.replace("ё", "е"),
        query.replace("й", "i"),
        query.split()[0] if " " in query else query,
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
                for item in items:
                    track_name = item.get("name", "").lower()
                    if variant.lower() in track_name or track_name in variant.lower():
                        return item.get("uri")
                return items[0].get("uri")
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
            continue

    return None


def _ui_automation_search(query: str, player=None) -> bool:
    """
    Надёжный поиск и запуск трека в Spotify через URI навигацию, буфер и хоткеи.
    """
    if not _HAS_PYAUTOGUI:
        return False

    try:
        if player:
            player.write_log(f"SYS: 🎵 Поиск в Spotify: {query}")

        # Стратегия 1: Прямой переход на результаты поиска через spotify:search:
        search_uri = f"spotify:search:{urllib.parse.quote(query)}"
        _open_spotify_uri(search_uri)
        time.sleep(1.8)

        _focus_spotify_window()
        time.sleep(0.3)
        if not _spotify_in_front():
            _logger.warning("Spotify не на переднем плане — клавиши не отправляю")
            return False

        # Стратегия 2: Поиск через активное поле (Ctrl+K и Ctrl+L)
        pyautogui.hotkey("ctrl", "k")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)

        try:
            import pyperclip
            pyperclip.copy(query)
            pyautogui.hotkey("ctrl", "v")
        except Exception:
            safe = query.replace("'", "''")
            subprocess.run(["powershell", "-Command", f"Set-Clipboard -Value '{safe}'"],
                           capture_output=True, timeout=2)
            pyautogui.hotkey("ctrl", "v")

        time.sleep(0.4)
        if not _spotify_in_front():
            return False
        pyautogui.press("enter")
        time.sleep(1.2)
        if not _spotify_in_front():
            return False

        # Воспроизведение: нажать Enter / Space / Media Play
        pyautogui.press("enter")
        time.sleep(0.3)
        pyautogui.press("down")
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(0.3)
        pyautogui.press("space")
        _send_media_key("playpause")

        if player:
            player.write_log("SYS: ✓ Трек в Spotify запущен")

        return True
    except Exception as e:
        if player:
            player.write_log(f"SYS: ✗ Spotify UI error: {e}")
        return False


def _find_youtube_direct_url(query: str) -> Optional[str]:
    """Находит прямую ссылку на видео YouTube с автозапуском."""
    try:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "ru,en;q=0.9",
            }
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            matches = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
            if not matches:
                matches = re.findall(r'/watch\?v=([a-zA-Z0-9_-]{11})', html)
            if matches:
                return f"https://www.youtube.com/watch?v={matches[0]}&autoplay=1"
    except Exception as e:
        _logger.debug("YouTube direct search error: %s", e)
    return None


# ─── Действия плеера ──────────────────────────────────────────────────────────
def _spotify_now():
    from core import media_session
    return media_session.now_playing("spotify")


def _started(before, timeout: float = 8.0):
    """Дождаться, что в Spotify заиграло НОВОЕ (не то, что было до команды),
    и дожать Play, если трек открылся на паузе. None — не заиграло."""
    from core import media_session
    end = time.monotonic() + timeout
    pressed = False
    while time.monotonic() < end:
        np = media_session.now_playing("spotify")
        if np and np.title and (before is None or np.title != before.title or not before.playing):
            if np.playing:
                return np
            if not pressed:
                media_session.command("play", "spotify")
                pressed = True
        time.sleep(0.4)
    np = media_session.now_playing("spotify")
    return np if np and np.playing else None


def _say_track(np) -> str:
    return f"«{np.title}»" + (f" — {np.artist}" if np.artist else "")


def _play(query: str = "", playlist_url: str = "", player=None) -> str:
    """Включить трек / плейлист / продолжить. Отвечает тем, что реально
    заиграло: раньше было «Включаю…», даже когда ничего не началось."""
    from core import media_session, web_find

    if player:
        player.write_log(f"SYS: 🎵 {'«' + query + '»' if query else 'музыка'}")
    installed = _is_spotify_installed()

    # ── Плейлист по ссылке ────────────────────────────────────────────────
    if playlist_url:
        uri = _https_to_spotify_uri(playlist_url)
        if uri and installed:
            before = _spotify_now()
            _open_spotify_uri(uri)
            np = _started(before)
            return f"Включил плейлист: {_say_track(np)}." if np else \
                "Открыл плейлист в Spotify, но воспроизведение не началось — нажмите Play."
        browser_control({"action": "go_to", "url": playlist_url}, player=player)
        return "Открыл плейлист в браузере."

    # ── Без запроса: продолжить ───────────────────────────────────────────
    if not query:
        if not _spotify_now() and installed:
            _open_spotify_uri("spotify:")
            media_session.wait_for("spotify", 10)
        media_session.command("play", "spotify")
        np = media_session.wait_for("spotify", 3, playing=True)
        return f"Играет {_say_track(np)}." if np else "Не получилось запустить музыку, сэр."

    # ── Конкретный трек / исполнитель / настроение ───────────────────────
    if installed:
        uri = _spotify_search_track_uri(query) or web_find.spotify_uri(query, "track") \
            or web_find.spotify_uri(query)
        if uri:
            before = _spotify_now()
            _open_spotify_uri(uri)
            np = _started(before)
            if np:
                return f"Включил {_say_track(np)}."
            return (f"Открыл «{query}» в Spotify, но воспроизведение не началось — "
                    "нажмите Play (или Spotify ещё загружается).")
        # Точной ссылки нет — честно открываем поиск в самом Spotify.
        _open_spotify_uri(f"spotify:search:{urllib.parse.quote(query)}")
        return f"Точный трек не нашёл — открыл поиск «{query}» в Spotify, выберите нужный."

    # ── Spotify не установлен: YouTube ────────────────────────────────────
    url = _find_youtube_direct_url(query)
    try:
        if url:
            browser_control({"action": "go_to", "url": url}, player=player)
            return f"Spotify не установлен — включил «{query}» на YouTube."
        browser_control({"action": "go_to",
                         "url": f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"},
                        player=player)
        return f"Spotify не установлен — открыл поиск «{query}» на YouTube."
    except Exception as exc:
        _logger.warning("YouTube не открылся: %s", exc)
        return "Не удалось открыть музыку: Spotify не установлен, а браузер не запустился."


def _control(cmd: str, player=None) -> str:
    """Пауза / продолжить / дальше / назад — явной командой сессии Spotify
    (или того, что играет сейчас). Раньше это была клавиша-переключатель:
    «пауза» на уже стоящей музыке её запускала."""
    from core import media_session
    app = "spotify" if _spotify_now() else None
    before = media_session.now_playing(app)
    if not media_session.command(cmd, app):
        return "Сейчас ничего не играет, сэр." if before is None else "Не получилось, сэр."
    if player:
        player.write_log(f"SYS: ⏯ {cmd}")
    time.sleep(0.6)
    np = media_session.now_playing(app)
    if cmd == "pause":
        return "Пауза." if not (np and np.playing) else "Не получилось поставить на паузу, сэр."
    if cmd == "play":
        return f"Продолжаю: {_say_track(np)}." if np and np.playing else "Продолжаю."
    if np and np.title:
        return ("Следующий: " if cmd == "next" else "Предыдущий: ") + _say_track(np) + "."
    return "Следующий трек." if cmd == "next" else "Предыдущий трек."


def _now_playing_text() -> str:
    from core import media_session
    np = media_session.now_playing("spotify") or media_session.now_playing()
    if not np or not np.title:
        return "Сейчас ничего не играет, сэр."
    return ("Играет " if np.playing else "На паузе: ") + _say_track(np) + "."


def _volume(direction: str, player=None, value=None) -> str:
    """Громкость самого Spotify; нет его сессии — системная."""
    from core import media_session
    from actions.computer_settings import parse_level
    step = parse_level(value, 10) or 10
    target = parse_level(value) if direction == "set" else None
    try:
        v = media_session.app_volume("spotify.exe", direction, step, target)
    except Exception as exc:
        _logger.debug("Громкость Spotify: %s", exc)
        v = None
    if v is not None:
        return f"Громкость Spotify {v}%."
    return computer_settings({"action": f"volume_{direction}", "value": value or "10"}, player=player)


# ─── Публичная точка входа ────────────────────────────────────────────────────
_ALIASES = {"start": "play", "включить": "play", "запустить": "play", "stop": "pause",
            "пауза": "pause", "стоп": "pause", "остановить": "pause", "продолжай": "resume",
            "continue": "resume", "next_track": "next", "skip": "next", "следующий": "next",
            "prev": "previous", "prev_track": "previous", "предыдущий": "previous",
            "what": "now_playing", "что играет": "now_playing", "louder": "volume_up",
            "громче": "volume_up", "quieter": "volume_down", "тише": "volume_down",
            "volume": "volume_set"}
_CONTROLS = ("pause", "resume", "next", "previous", "volume_up", "volume_down",
             "volume_set", "now_playing", "shuffle")


def music_player(parameters: dict, player=None) -> str:
    """Музыка — Spotify. Premium: прямо через Web API (быстро и точно);
    нет входа или Premium — ссылка на трек + медиа-сессия Windows."""
    from actions import spotify_premium as sp
    action = (parameters.get("action") or "").strip().lower()
    action = _ALIASES.get(action, action)
    query = (parameters.get("query") or "").strip()
    value = parameters.get("value")

    if action == "login":
        return sp.login(player)

    # «Пауза», «продолжи», «громче» — тому, что сейчас реально играет:
    # идёт фильм, а музыка стоит — это про фильм.
    if action in ("pause", "resume", "volume_up", "volume_down", "volume_set"):
        try:
            from actions import video_player
            if video_player.video_playing() and not _music_playing():
                return video_player.control(action, value)
        except Exception as exc:
            _logger.debug("Видео для паузы: %s", exc)

    if action in ("play", "mood"):
        # Музыку просят вместо фильма — фильм на паузу, а не звучать поверх.
        try:
            from actions import video_player
            if video_player.video_playing():
                video_player.control("pause")
        except Exception as exc:
            _logger.debug("Пауза фильма перед музыкой: %s", exc)

    if action in ("play", "mood") and not parameters.get("playlist_url"):
        q = f"{query} плейлист" if action == "mood" and query else query
        res = sp.play(q)
        if res is not None:
            return res
    elif action in _CONTROLS:
        res = sp.control(action, value)
        if res is not None:
            return res
    return _music_player_fallback({**parameters, "action": action}, player)


def _music_playing() -> bool:
    try:
        from actions import spotify_premium as sp
        if sp.ready() and sp.is_premium():
            np = sp.now_playing()
            return bool(np and np["playing"])
    except Exception:
        pass
    np = _spotify_now()
    return bool(np and np.playing)


def _music_player_fallback(parameters: dict, player=None) -> str:
    """action: play | pause | resume | next | previous | stop | now_playing |
    volume_up | volume_down | volume_set | mood; query, value, playlist_url."""
    action = (parameters.get("action") or "").strip().lower()
    query = (parameters.get("query") or "").strip()
    playlist_url = (parameters.get("playlist_url") or "").strip()
    value = parameters.get("value")

    if action in ("play", "start", "включить", "запустить"):
        return _play(query, playlist_url, player)
    if action in ("mood",):
        return _play(f"{query} плейлист" if query else "", "", player)
    if action in ("pause", "stop", "пауза", "стоп", "остановить"):
        return _control("pause", player)
    if action in ("resume", "продолжай", "continue"):
        return _control("play", player)
    if action in ("toggle",):
        return _control("toggle", player)
    if action in ("next", "next_track", "skip", "следующий"):
        return _control("next", player)
    if action in ("prev", "previous", "prev_track", "предыдущий"):
        return _control("previous", player)
    if action in ("now_playing", "what", "что играет"):
        return _now_playing_text()
    if action in ("volume_up", "louder", "громче"):
        return _volume("up", player, value)
    if action in ("volume_down", "quieter", "тише"):
        return _volume("down", player, value)
    if action in ("volume", "volume_set"):
        return _volume("set", player, value)
    return f"Не понял команду: «{action}»."
