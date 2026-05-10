"""
Действие: продвинутое управление кино-плеером.

Работает с любым видеоплеером в браузере: VK Video, YouTube, Netflix, Twitch.
Использует универсальные клавиши, которые поддерживают все плееры:
  Space — пауза/продолжить
  F     — полный экран
  ←  →  — перемотка ±10 сек
  Esc   — выход из полного экрана
  Ctrl+W — закрыть вкладку (выход из режима)

Для отправки клавиш — двухуровневая стратегия:
  1. pyautogui (если установлен) — быстрый и кросс-платформенный
  2. PowerShell SendKeys (Windows fallback) — без зависимостей

Если ни то ни другое не доступно — graceful degradation,
говорим пользователю что не можем управлять плеером.
"""

import platform
import subprocess
import time
import urllib.parse
from typing import Optional

from actions.browser_control import browser_control
from actions.computer_settings import computer_settings

_OS = platform.system()

# ─── Опциональная зависимость pyautogui ───────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = False  # Не падать если мышь в углу экрана
    _HAS_PYAUTOGUI = True
except Exception:
    _HAS_PYAUTOGUI = False


# ─── Карта клавиш: общее имя → (pyautogui, PowerShell SendKeys) ───────────────
_KEY_MAP = {
    "space":      ("space",      " "),
    "f":          ("f",          "f"),
    "right":      ("right",      "{RIGHT}"),
    "left":       ("left",       "{LEFT}"),
    "up":         ("up",         "{UP}"),
    "down":       ("down",       "{DOWN}"),
    "escape":     ("escape",     "{ESC}"),
}


def _send_key(key: str) -> bool:
    """
    Отправляет одиночную клавишу в активное окно.
    Возвращает True если получилось.
    """
    if key not in _KEY_MAP:
        return False

    py_key, ps_key = _KEY_MAP[key]

    # Попытка 1: pyautogui (быстрая, кросс-платформенная)
    if _HAS_PYAUTOGUI:
        try:
            pyautogui.press(py_key)
            return True
        except Exception:
            pass

    # Попытка 2: PowerShell SendKeys (только Windows)
    if _OS == "Windows":
        try:
            cmd = f"(New-Object -ComObject WScript.Shell).SendKeys('{ps_key}')"
            result = subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True, timeout=3
            )
            return result.returncode == 0
        except Exception:
            pass

    return False


def _send_hotkey_ctrl_w() -> bool:
    """Закрыть текущую вкладку браузера (Ctrl+W)."""
    if _HAS_PYAUTOGUI:
        try:
            pyautogui.hotkey("ctrl", "w")
            return True
        except Exception:
            pass

    if _OS == "Windows":
        try:
            cmd = "(New-Object -ComObject WScript.Shell).SendKeys('^w')"
            result = subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True, timeout=3
            )
            return result.returncode == 0
        except Exception:
            pass

    return False


# ─── Действия плеера ──────────────────────────────────────────────────────────
def _play(title: str, player=None) -> str:
    """Открыть VK Video с поиском фильма."""
    title = (title or "").strip()
    if not title:
        return "Назовите фильм, сэр."

    encoded = urllib.parse.quote(title)
    url = f"https://vkvideo.ru/search?q={encoded}"

    try:
        browser_control({"action": "go_to", "url": url}, player=player)
        if player:
            player.write_log(f"SYS: 🎬 Кино — поиск «{title}»")
        return f"Кинотеатр готов. Ищу «{title}», сэр."
    except Exception:
        return "Не удалось открыть фильм, сэр."


def _pause_resume(player=None) -> str:
    """Space — переключение пауза/воспроизведение."""
    if _send_key("space"):
        if player:
            player.write_log("SYS: ⏯ Пауза/Воспроизведение")
        return "Готово."
    return "Не могу управлять плеером, сэр. Установите pyautogui."


def _fullscreen(player=None) -> str:
    """F — полный экран в YouTube/VK Video."""
    if _send_key("f"):
        if player:
            player.write_log("SYS: ⛶ Полный экран")
        return "Полный экран."
    return "Не могу включить полный экран, сэр."


def _seek_forward(player=None) -> str:
    """Стрелка вправо — вперёд 10 секунд (стандарт YouTube/VK)."""
    if _send_key("right"):
        if player:
            player.write_log("SYS: ⏩ Вперёд 10 сек")
        return "Перематываю вперёд."
    return "Не получилось перемотать, сэр."


def _seek_back(player=None) -> str:
    """Стрелка влево — назад 10 секунд."""
    if _send_key("left"):
        if player:
            player.write_log("SYS: ⏪ Назад 10 сек")
        return "Назад на десять секунд."
    return "Не получилось перемотать, сэр."


def _exit_movie(player=None) -> str:
    """Выход из режима фильма: Esc → Ctrl+W."""
    # Сначала выход из полноэкранного режима
    _send_key("escape")
    time.sleep(0.15)

    # Затем закрытие вкладки
    if _send_hotkey_ctrl_w():
        if player:
            player.write_log("SYS: ✕ Закрыт режим фильма")
        return "Закрываю режим фильма, сэр."

    return "Выхожу из режима фильма, сэр."


def _volume(direction: str, player=None) -> str:
    """Громкость через существующий computer_settings (системная громкость)."""
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
