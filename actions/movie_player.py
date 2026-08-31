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
import urllib.parse
import urllib.request
import logging

from actions.browser_control import browser_control
from actions.computer_settings import computer_settings

_logger = logging.getLogger(__name__)
_OS = platform.system()

# ─── Опциональная зависимость pyautogui ───────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = False
    _HAS_PYAUTOGUI = True
except Exception:
    _HAS_PYAUTOGUI = False


# ─── Карта клавиш ─────────────────────────────────────────────────────────────
_KEY_MAP = {
    "space":      ("space",      " "),
    "f":          ("f",          "f"),
    "right":      ("right",      "{RIGHT}"),
    "left":       ("left",       "{LEFT}"),
    "up":         ("up",         "{UP}"),
    "down":       ("down",       "{DOWN}"),
    "escape":     ("escape",     "{ESC}"),
    "enter":      ("enter",      "{ENTER}"),
}


def _focus_movie_player() -> bool:
    """Активирует окно браузера с плеером VK Видео."""
    try:
        import pygetwindow as gw
        browser_hints = ("vk video", "vk", "chrome", "edge", "firefox", "opera", "yandex", "brave")
        for hint in browser_hints:
            for win in gw.getAllWindows():
                if hint in (win.title or "").lower():
                    try:
                        if win.isMinimized:
                            win.restore()
                        win.activate()
                        time.sleep(0.1)
                        return True
                    except Exception:
                        pass
    except Exception:
        pass
    return False


def _send_key(key: str) -> bool:
    """Отправляет одиночную клавишу в активное окно."""
    if key not in _KEY_MAP:
        return False

    py_key, ps_key = _KEY_MAP[key]

    if _HAS_PYAUTOGUI:
        try:
            pyautogui.press(py_key)
            return True
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    if _OS == "Windows":
        try:
            cmd = f"(New-Object -ComObject WScript.Shell).SendKeys('{ps_key}')"
            result = subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True, timeout=3
            )
            return result.returncode == 0
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    return False


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
                ["powershell", "-Command", cmd],
                capture_output=True, timeout=3
            )
            return result.returncode == 0
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    return False


# ─── Действия плеера ──────────────────────────────────────────────────────────
def _play(title: str, player=None) -> str:
    """
    Открыть фильм на vkvideo.ru и запустить воспроизведение.
    """
    title = (title or "").strip()
    if not title:
        return "Назовите фильм, сэр."

    if player:
        player.write_log(f"SYS: 🎬 VK Видео — запуск «{title}»")

    encoded = urllib.parse.quote(f"{title} фильм")
    url = f"https://vkvideo.ru/?q={encoded}&section=search"

    try:
        browser_control({"action": "go_to", "url": url}, player=player)
        time.sleep(2.5)

        _focus_movie_player()
        time.sleep(0.3)

        # Переход на первое видео и старт воспроизведения
        _send_key("enter")
        time.sleep(1.0)
        _send_key("space")

        return f"Включаю фильм «{title}» на VK Видео, приятного просмотра, сэр."
    except Exception as e:
        _logger.error("VK Video open error: %s", e)
        return "Не удалось открыть фильм на VK Видео, сэр."


def _pause_resume(player=None) -> str:
    """Space — переключение пауза/воспроизведение."""
    _focus_movie_player()
    if _send_key("space"):
        if player:
            player.write_log("SYS: ⏯ Пауза/Воспроизведение")
        return "Готово, сэр."
    return "Не могу управлять плеером, сэр."


def _fullscreen(player=None) -> str:
    """F — полный экран в VK Video / YouTube."""
    _focus_movie_player()
    if _send_key("f"):
        if player:
            player.write_log("SYS: ⛶ Полный экран")
        return "Полный экран, сэр."
    return "Не могу включить полный экран, сэр."


def _seek_forward(player=None) -> str:
    """Стрелка вправо — вперёд 10 секунд."""
    _focus_movie_player()
    if _send_key("right"):
        if player:
            player.write_log("SYS: ⏩ Вперёд 10 сек")
        return "Перематываю вперёд, сэр."
    return "Не получилось перемотать, сэр."


def _seek_back(player=None) -> str:
    """Стрелка влево — назад 10 секунд."""
    _focus_movie_player()
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

    if _send_hotkey_ctrl_w():
        if player:
            player.write_log("SYS: ✕ Закрыт режим фильма")
        return "Закрываю фильм, сэр."

    return "Выхожу из режима фильма, сэр."


def _volume(direction: str, player=None) -> str:
    """Громкость в плеере и системная громкость."""
    _focus_movie_player()
    _send_key("up" if direction == "up" else "down")
    action = "увеличить громкость" if direction == "up" else "уменьшить громкость"
    return computer_settings(
        {"action": action, "value": "10"},
        player=player,
    )


# ─── Публичная точка входа ────────────────────────────────────────────────────
def movie_player(parameters: dict, player=None) -> str:
    """
    Главная точка входа для tool 'movie_player'.

    parameters:
        action: play | pause | resume | fullscreen | seek_forward |
                seek_back | volume_up | volume_down | exit
        title:  название фильма для action=play
    """
    action = (parameters.get("action") or "").strip().lower()
    title = (parameters.get("title") or "").strip()

    # ── Воспроизведение нового фильма ─────────────────────────────────────────
    if action in ("play", "start", "запустить", "включить"):
        return _play(title, player)

    # ── Управление текущим воспроизведением ───────────────────────────────────
    elif action in ("pause", "resume", "toggle", "пауза", "продолжай"):
        return _pause_resume(player)

    elif action in ("fullscreen", "full_screen", "полный_экран"):
        return _fullscreen(player)

    elif action in ("seek_forward", "forward", "вперёд", "вперед"):
        return _seek_forward(player)

    elif action in ("seek_back", "back", "rewind", "назад"):
        return _seek_back(player)

    elif action in ("volume_up", "louder", "громче"):
        return _volume("up", player)

    elif action in ("volume_down", "quieter", "тише"):
        return _volume("down", player)

    elif action in ("exit", "close", "stop", "выход", "закрыть"):
        return _exit_movie(player)

    return f"Не понял команду плеера: «{action}»."
