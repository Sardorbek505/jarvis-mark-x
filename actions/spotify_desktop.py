"""Десктопный Spotify: media-клавиши, URI, окно, поиск через Web API и UI.

Вынесено из старого actions/music_player.py (тот стал адаптером над
MediaOrchestrator). Этими функциями пользуются core/media/providers/spotify.py,
core/fast_command_router.py и core/routines_engine.py — раньше на их месте
стояли лямбды-заглушки «для тестов», и Джарвис отвечал «Воспроизведение
Spotify запущено, сэр», ничего не включив.
"""

import base64
import json
import logging
import os
import platform
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

_OS = platform.system()

_BASE = Path(__file__).resolve().parent.parent
try:
    from core.paths import get_config_path
    _API_CONFIG = get_config_path("api_keys.json")
except Exception:
    _API_CONFIG = _BASE / "config" / "api_keys.json"

# Кэш Spotify access token (срок 1 час)
_SPOTIFY_TOKEN: Optional[str] = None
_SPOTIFY_TOKEN_EXPIRES: float = 0.0

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
                r"%APPDATA%\Spotify\Spotify.exe",
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


def _is_spotify_running() -> bool:
    """Проверяет, запущен ли реальный процесс Spotify в операционной системе."""
    try:
        if _OS == "Windows":
            import subprocess
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq Spotify.exe", "/NH"],
                text=True, errors="ignore"
            )
            return "Spotify.exe" in out
        elif _OS == "Darwin":
            import subprocess
            out = subprocess.check_output(["pgrep", "-x", "Spotify"], text=True, errors="ignore")
            return bool(out.strip())
        else:
            import subprocess
            out = subprocess.check_output(["pgrep", "-x", "spotify"], text=True, errors="ignore")
            return bool(out.strip())
    except Exception:
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
        _logger.warning("Невалидный Spotify URI: %.80s", uri)
        return False
    try:
        if _OS == "Windows":
            # Метод 0: локальный spotify_cli
            cli = os.path.expandvars(r"%APPDATA%\Spotify\spotify_cli.exe")
            if os.path.exists(cli):
                try:
                    res = subprocess.run([cli, "play", uri], capture_output=True, timeout=5)
                    if res.returncode == 0:
                        return True
                except Exception:
                    pass

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
    found = _spotify_search_track(query)
    return found[0] if found else None


def _spotify_search_track(query: str) -> Optional[tuple[str, str]]:
    """(URI, название) трека: по названию узнаём открывшуюся страницу трека."""
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
                        return item.get("uri"), item.get("name", "")
                return items[0].get("uri"), items[0].get("name", "")
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
        pyautogui.press("enter")
        time.sleep(1.2)

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


