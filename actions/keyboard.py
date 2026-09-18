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


# ─── Сочетания клавиш ─────────────────────────────────────────────────────────
# Одиночной клавиши хватало плеерам, но не управлению компьютером: копировать,
# переключать вкладки и менять масштаб — это аккорды. Уровни доставки те же и в
# том же порядке, чтобы поведение не расходилось между двумя функциями.

# Модификаторы для WScript.Shell SendKeys: свой язык, не совпадающий ни с чем.
_SENDKEYS_MODS = {"ctrl": "^", "alt": "%", "shift": "+", "win": "^{ESC}"}

# Клавиши, которые в SendKeys пишутся в фигурных скобках.
_SENDKEYS_SPECIAL = {
    "left": "{LEFT}", "right": "{RIGHT}", "up": "{UP}", "down": "{DOWN}",
    "home": "{HOME}", "end": "{END}", "pgup": "{PGUP}", "pgdn": "{PGDN}",
    "enter": "{ENTER}", "escape": "{ESC}", "tab": "{TAB}", "space": " ",
    "delete": "{DEL}", "backspace": "{BS}",
    "f5": "{F5}", "f11": "{F11}", "f4": "{F4}",
}


def _to_sendkeys(keys: list[str]) -> str | None:
    """Аккорд в язык SendKeys: «ctrl+shift+t» → «^+t». None — не выразить."""
    модификаторы = ""
    клавиша = None
    for часть in keys:
        часть = часть.strip().lower()
        if часть in _SENDKEYS_MODS:
            if часть == "win":
                return None          # Win в SendKeys не выражается
            модификаторы += _SENDKEYS_MODS[часть]
        elif часть in _SENDKEYS_SPECIAL:
            клавиша = _SENDKEYS_SPECIAL[часть]
        elif len(часть) == 1:
            клавиша = часть
        else:
            return None
    return f"{модификаторы}{клавиша}" if клавиша else None


def send_combo(combo: str) -> bool:
    """Отправляет сочетание вида «ctrl+c», «ctrl+shift+t», «alt+left».

    True — отправлено. False означает «не смог», и вызывающий обязан сказать
    об этом вслух: молча не нажатая комбинация выглядит как «Джарвис меня не
    послушался»."""
    части = [ч.strip().lower() for ч in str(combo).split("+") if ч.strip()]
    if not части:
        return False

    if _HAS_PYAUTOGUI:
        try:
            pyautogui.hotkey(*части)
            return True
        except Exception as exc:
            _logger.debug("pyautogui не отправил %s: %s", combo, exc)

    if _OS == "Windows":
        строка = _to_sendkeys(части)
        if строка is None:
            return False
        try:
            cmd = f"(New-Object -ComObject WScript.Shell).SendKeys('{строка}')"
            итог = subprocess.run(["powershell", "-Command", cmd],
                                  capture_output=True, timeout=_SENDKEYS_TIMEOUT_SEC)
            return итог.returncode == 0
        except Exception as exc:
            _logger.debug("SendKeys не отправил %s: %s", combo, exc)

    return False


def type_text(text: str) -> bool:
    """Печатает текст в активное окно."""
    text = str(text)
    if not text:
        return False
    if _HAS_PYAUTOGUI:
        try:
            pyautogui.typewrite(text, interval=0.01)
            return True
        except Exception as exc:
            _logger.debug("pyautogui не напечатал текст: %s", exc)
    if _OS == "Windows":
        try:
            # Символы, которые в SendKeys значат модификаторы, экранируются
            # фигурными скобками — иначе «+» превратится в Shift.
            экранированный = "".join(
                "{" + c + "}" if c in "+^%~(){}[]" else c for c in text
            )
            cmd = f"(New-Object -ComObject WScript.Shell).SendKeys('{экранированный}')"
            итог = subprocess.run(["powershell", "-Command", cmd],
                                  capture_output=True, timeout=_SENDKEYS_TIMEOUT_SEC)
            return итог.returncode == 0
        except Exception as exc:
            _logger.debug("SendKeys не напечатал текст: %s", exc)
    return False
