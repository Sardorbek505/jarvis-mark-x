"""
Действие: продвинутое управление кино-плеером через VK Видео (vkvideo.ru).

Поддерживает запуск фильмов на vkvideo.ru и управление воспроизведением:
  Space  — пауза/продолжить
  F      — полный экран
  ←  →   — перемотка ±10 сек
  ↑  ↓   — громкость плеера
  Esc    — выход из полного экрана
  Ctrl+W — закрыть вкладку
"""

import platform
import subprocess
import time
import logging

from actions.browser_control import browser_control
from actions.computer_settings import computer_settings
from actions.keyboard import send_key as _send_key

_logger = logging.getLogger(__name__)
_OS = platform.system()

# ─── Опциональная зависимость pyautogui ───────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = False
    _HAS_PYAUTOGUI = True
except Exception:
    _HAS_PYAUTOGUI = False




_VIDEO_TITLE_HINTS = ("vk видео", "vk video", "vkvideo", "youtube", "rutube", "кинопоиск",
                      "kinopoisk", "ivi", "okko", "netflix", "смотреть", "фильм", "сериал")
_BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "browser.exe", "opera.exe", "brave.exe")


def _video_window():
    """Окно браузера, где идёт видео: по заголовку вкладки, иначе любой браузер."""
    from core import win_apps
    wins = [w for w in win_apps.list_windows() if w.exe in _BROWSERS or not w.exe]
    for w in wins:
        if any(h in w.title.lower() for h in _VIDEO_TITLE_HINTS):
            return w
    return None


def _focus_movie_player() -> bool:
    """Вывести вперёд окно с видео. Раньше подходило окно с «vk» в названии —
    хоть какое, и клавиши улетали не туда."""
    if _OS != "Windows":
        return False
    w = _video_window()
    if not w:
        return False
    from core import win_apps
    return win_apps.focus(w)


def _video_in_front() -> bool:
    """Впереди действительно вкладка с видео? Ctrl+W закрывает то, что впереди:
    раньше при промахе фокуса это была вкладка VS Code или нужная страница."""
    from core import win_apps
    fg = win_apps.foreground()
    return bool(fg and any(h in fg.title.lower() for h in _VIDEO_TITLE_HINTS))


def _send_hotkey_ctrl_w() -> bool:
    """Закрыть текущую вкладку браузера (Ctrl+W)."""
    if _HAS_PYAUTOGUI:
        try:
            pyautogui.hotkey("ctrl", "w")
            return True
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    if _OS == "Windows":
        try:
            cmd = "(New-Object -ComObject WScript.Shell).SendKeys('^w')"
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                capture_output=True, timeout=5, creationflags=0x08000000,
            )
            return result.returncode == 0
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    return False


# ─── Действия плеера ──────────────────────────────────────────────────────────
_PROVIDER_NAMES = {"vk": "VK Видео", "kinopoisk": "Кинопоиске", "ivi": "IVI",
                   "okko": "Okko", "youtube": "YouTube"}
_ORDER = ("vk", "kinopoisk", "ivi", "okko")


def _play(title: str, player=None, provider: str = "auto") -> str:
    """Найти страницу фильма у провайдера и открыть её — там плеер.

    Раньше открывался поиск VK Видео, а потом жались Enter и Space: Enter на
    странице поиска ничего не открывает, Space прокручивает страницу — фильм
    не запускался, а ответ был «приятного просмотра»."""
    from core import media_session, web_find
    title = (title or "").strip()
    if not title:
        return "Назовите фильм, сэр."
    order = (provider,) if provider in _PROVIDER_NAMES else _ORDER
    if player:
        player.write_log(f"SYS: 🎬 ищу «{title}»")

    for prov in order:
        try:
            url = web_find.film_page(title, prov)
        except Exception as exc:
            _logger.debug("Поиск фильма (%s): %s", prov, exc)
            url = None
        if not url:
            continue
        browser_control({"action": "go_to", "url": url}, player=player)
        where = _PROVIDER_NAMES[prov]
        np = media_session.wait_for("", timeout=6, playing=True) if _OS == "Windows" else None
        # Играть могла и музыка в Spotify — «включил» только про браузер.
        if np and np.playing and any(b in np.app.lower() for b in
                                     ("chrome", "edge", "firefox", "yandex", "opera", "brave", "browser")):
            return f"Включил «{title}» на {where}."
        return f"Открыл «{title}» на {where}. Если не запустился сам — нажмите Play (или скажите «полный экран»)."

    prov = order[0]
    browser_control({"action": "go_to", "url": web_find.search_page(title, prov)}, player=player)
    return f"Точной страницы «{title}» не нашёл — открыл поиск на {_PROVIDER_NAMES[prov]}."


def _pause_resume(player=None, cmd: str = "toggle") -> str:
    """Пауза / продолжить — командой медиа-сессии браузера (Chromium отдаёт
    видео в медиа-сессии Windows), а не Space в то, что впереди."""
    from core import media_session
    if media_session.command(cmd):
        if player:
            player.write_log(f"SYS: ⏯ {cmd}")
        return {"pause": "Пауза.", "play": "Продолжаю."}.get(cmd, "Готово.")
    if _focus_movie_player() and _send_key("space"):
        return "Готово."
    return "Не вижу, где идёт фильм, сэр."


def _fullscreen(player=None) -> str:  # клавиши — только окну с видео
    """F — полный экран в VK Video / YouTube."""
    if not _focus_movie_player():
        return "Не вижу окна с фильмом, сэр."
    if _send_key("f"):
        if player:
            player.write_log("SYS: ⛶ Полный экран")
        return "Полный экран, сэр."
    return "Не могу включить полный экран, сэр."


def _seek_forward(player=None) -> str:  # клавиши — только окну с видео
    """Стрелка вправо — вперёд 10 секунд."""
    if not _focus_movie_player():
        return "Не вижу окна с фильмом, сэр."
    if _send_key("right"):
        if player:
            player.write_log("SYS: ⏩ Вперёд 10 сек")
        return "Перематываю вперёд, сэр."
    return "Не получилось перемотать, сэр."


def _seek_back(player=None) -> str:  # клавиши — только окну с видео
    """Стрелка влево — назад 10 секунд."""
    if not _focus_movie_player():
        return "Не вижу окна с фильмом, сэр."
    if _send_key("left"):
        if player:
            player.write_log("SYS: ⏪ Назад 10 сек")
        return "Перематываю назад, сэр."
    return "Не получилось перемотать, сэр."


def _exit_movie(player=None) -> str:
    """Выход из режима фильма: Esc → Ctrl+W."""
    _focus_movie_player()
    _send_key("escape")
    time.sleep(0.15)

    if not _video_in_front():
        return "Не вижу окна с фильмом впереди — вкладку не закрываю, сэр."
    if _send_hotkey_ctrl_w():
        if player:
            player.write_log("SYS: ✕ Закрыт режим фильма")
        return "Закрываю фильм, сэр."

    return "Выхожу из режима фильма, сэр."


def _volume(direction: str, player=None) -> str:
    """Системная громкость. Раньше ещё жалась стрелка в плеере — в браузере
    она прокручивала страницу, а не меняла звук."""
    return computer_settings({"action": f"volume_{direction}", "value": "10"}, player=player)


# ─── Публичная точка входа ────────────────────────────────────────────────────
def movie_player(parameters: dict, player=None) -> str:
    """Фильмы — VK Видео в окне Джарвиса: найти, открыть, запустить, полный
    экран. Остальные действия — управление тем, что идёт (video_player)."""
    from actions import video_player
    action = (parameters.get("action") or "").strip().lower()
    title = (parameters.get("title") or parameters.get("query") or "").strip()
    if action in ("play", "start", "запустить", "включить"):
        return video_player.play_film(title, player)
    res = video_player.control({"volume_up": "volume_up", "volume_down": "volume_down"}
                               .get(action, action), parameters.get("value"))
    return res or "Сейчас в окне Джарвиса ничего не идёт, сэр."
