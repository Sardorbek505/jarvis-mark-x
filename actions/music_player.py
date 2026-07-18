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
    UI automation для поиска трека в Spotify когда API недоступен.
    
    Процесс:
    1. Открыть Spotify
    2. Фокусировать окно
    3. Ввести запрос в поиск (Ctrl+L или Ctrl+E)
    4. Подождать результаты
    5. Нажать Enter на первый результат
    """
    if not _HAS_PYAUTOGUI:
        return False
    
    try:
        if player:
            player.write_log(f"SYS: 🤖 UI automation для поиска: {query}")
        
        # Открываем Spotify если не открыт
        if not _is_spotify_installed():
            if player:
                player.write_log("SYS: ⚠ Spotify не установлен")
            return False
        
        # Открываем Spotify
        _open_spotify_uri("spotify:")
        time.sleep(2.0)
        
        # Фокусируем окно
        _focus_spotify_window()
        time.sleep(0.5)
        
        # Открываем поиск (Ctrl+L или Ctrl+E)
        pyautogui.hotkey('ctrl', 'l')
        time.sleep(0.3)
        
        # Вводим запрос
        pyautogui.write(query, interval=0.01)
        time.sleep(0.5)
        
        # Нажимаем Enter
        pyautogui.press('enter')
        time.sleep(2.0)
        
        # Выбираем первый результат (Down + Enter)
        pyautogui.press('down')
        time.sleep(0.3)
        pyautogui.press('enter')
        time.sleep(1.0)
        
        if player:
            player.write_log("SYS: ✓ UI automation завершена")
        
        return True
    except Exception as e:
        if player:
            player.write_log(f"SYS: ✗ UI automation error: {e}")
        print(f"[Music] UI automation error: {e}")
        return False


def _keyboard_play_first_result() -> bool:
    """
    После открытия spotify:search:X пытается запустить первый трек
    через клавиатурную навигацию: Tab → Down → Enter.

    Улучшенная версия с pyautogui для надёжности.
    """
    if not _HAS_PYAUTOGUI:
        return False
    
    try:
        # Ждём загрузки страницы поиска
        time.sleep(2.5)
        
        # Навигация: Tab (первый элемент) → Down (выбор первого результата) → Enter
        pyautogui.press('tab')
        time.sleep(0.3)
        pyautogui.press('down')
        time.sleep(0.3)
        pyautogui.press('enter')
        
        return True
    except Exception as e:
        print(f"[Music] Keyboard navigation error: {e}")
        return False


# ─── Действия плеера ──────────────────────────────────────────────────────────
def _play(query: str = "", playlist_url: str = "", player=None) -> str:
    """
    Запускает музыку только через Spotify.

    Стратегия (по приоритету):
      1. Если URL плейлиста → spotify:playlist:ID → открывается плейлист
      2. Если запрос трека:
         a. Spotify Web API → получаем spotify:track:ID → автоплей в десктопе
         b. Если API недоступен или ничего не нашёл → UI automation
         c. Если UI automation не сработал → честно сказать "трек не найден"
      3. Если пустой запрос → просто открыть Spotify

    Priority Rule: Явный запрос трека всегда переопределяет историю/очередь.
    """
    if player:
        if query:
            player.write_log(f"SYS: 🎵 Spotify — поиск «{query}»")
        elif playlist_url:
            player.write_log("SYS: 🎵 Spotify — плейлист")
        else:
            player.write_log("SYS: 🎵 Spotify — запуск")

    # Проверяем, установлен ли Spotify
    if not _is_spotify_installed():
        if player:
            player.write_log("SYS: ⚠ Spotify не установлен, использую веб-версию")
        # Прямо открываем веб-версию
        if query:
            encoded = urllib.parse.quote(query)
            url = f"https://open.spotify.com/search/{encoded}"
        elif playlist_url:
            url = playlist_url
        else:
            url = "https://open.spotify.com"
        try:
            browser_control({"action": "go_to", "url": url}, player=player)
            return f"Открыл Spotify в браузере, сэр."
        except Exception:
            return "Spotify не установлен и не удалось открыть браузер, сэр."

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
        # Сначала пробуем через API
        track_uri = _spotify_search_track_uri(query)
        if track_uri:
            if player:
                player.write_log(f"SYS: ✓ API нашёл трек: {track_uri}")
            # Открываем точный трек URI
            if player:
                player.write_log("SYS: → Открываю точный URI...")
            if _open_spotify_uri(track_uri):
                if player:
                    player.write_log("SYS: ✓ URI открыт")
                time.sleep(2.0)
                _focus_spotify_window()
                # Открытие spotify:track: URI переходит на трек, но НЕ играет его.
                # Жмём play, как в ветках плейлиста и пустого запроса, иначе
                # Джарвис говорит «включил», а музыка стоит.
                _send_media_key("playpause")
                return f"Включаю «{query}» в Spotify, сэр."
            else:
                if player:
                    player.write_log("SYS: ✗ Не удалось открыть URI")
        
        # API недоступен или ничего не нашёл → пробуем UI automation
        if player:
            player.write_log("SYS: API недоступен или ничего не найдено → UI automation")
        
        if _ui_automation_search(query, player):
            return f"Открыл «{query}» в Spotify, сэр."
        else:
            return "Сэр, трек не найден."

    # ── Шаг 3: Пустой запрос ──────────────────────────────────────────────
    if player:
        player.write_log("SYS: → Открываю Spotify (пустой)")
    spotify_opened = _open_spotify_uri("spotify:")
    if not spotify_opened:
        try:
            browser_control(
                {"action": "go_to", "url": "https://open.spotify.com"},
                player=player,
            )
            spotify_opened = True
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    if not spotify_opened:
        return "Не удалось открыть Spotify, сэр."

    time.sleep(2.0)
    _focus_spotify_window()
    _send_media_key("playpause")
    return "Spotify открыт и воспроизводится, сэр."


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
