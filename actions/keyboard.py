"""Отправка одиночных клавиш в активное окно.

Общий модуль для плееров: и музыке, и кино нужен один и тот же приём —
нажать Space/Enter в окне, которое сейчас в фокусе. Раньше эта функция жила
только в movie_player.py, а music_player.py звал её по имени, не имея
определения, и падал с NameError на основном сценарии Spotify.

Уровни доставки, от быстрого к запасному:
  1. pyautogui  — кроссплатформенно, без создания процессов;
  2. WScript.Shell SendKeys через PowerShell — работает даже там,
     где pyautogui не установлен.
"""

import logging
import platform
import subprocess

_logger = logging.getLogger(__name__)

_OS = platform.system()

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    _HAS_PYAUTOGUI = True
except Exception:
    _HAS_PYAUTOGUI = False


# Имя клавиши -> (имя для pyautogui, код для WScript.Shell SendKeys)
_KEY_MAP = {
    "space":  ("space",  " "),
    "f":      ("f",      "f"),
    "right":  ("right",  "{RIGHT}"),
    "left":   ("left",   "{LEFT}"),
    "up":     ("up",     "{UP}"),
    "down":   ("down",   "{DOWN}"),
    "escape": ("escape", "{ESC}"),
    "enter":  ("enter",  "{ENTER}"),
}

_SENDKEYS_TIMEOUT_SEC = 3


def send_key(key: str) -> bool:
    """Отправляет одиночную клавишу в активное окно. True, если получилось."""
    mapping = _KEY_MAP.get(key)
    if mapping is None:
        _logger.debug("send_key: неизвестная клавиша %r", key)
        return False

    py_key, ps_key = mapping

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
                capture_output=True, timeout=_SENDKEYS_TIMEOUT_SEC,
            )
            return result.returncode == 0
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    return False
