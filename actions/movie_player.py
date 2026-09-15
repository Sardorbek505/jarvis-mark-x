"""JARVIS Mark X — Адаптер плеера фильмов и видео (movie_player).

Сохраняет внешний контракт Gemini Live Tool Call:
  movie_player(parameters, player=None)

Запуск контента и пауза/стоп идут через единый MediaOrchestrator. Полный экран
и перемотка сначала пробуются через активную сессию оркестратора (мост в
страницу), а если её нет — клавишами в найденном окне браузера. Если окна с
видео нет, об этом говорится честно: раньше здесь стояли заглушки, и Джарвис
рапортовал «перемотал», ничего не нажав.
"""

import logging
import platform
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from actions.keyboard import send_key as _send_key
from core.media.orchestrator import get_media_orchestrator

logger = logging.getLogger(__name__)
_OS = platform.system()

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    _HAS_PYAUTOGUI = True
except Exception:
    pyautogui = None
    _HAS_PYAUTOGUI = False

# Одно нажатие стрелки в плеерах VK Видео и YouTube — 5 секунд.
_SEEK_STEP_SEC = 5.0
_DEFAULT_SEEK_SEC = 10.0
# Полоса таймлайна в полноэкранном плеере — у нижнего края экрана.
_TIMELINE_OFFSET_PX = 45
_NO_WINDOW = "Не нашёл окно с видео, сэр. Сначала включите фильм."

_MEDIA_TITLE_HINTS = ("youtube", "ютуб", "kinopoisk", "кинопоиск", "vk", "видео", "video")
_BROWSER_HINTS = _MEDIA_TITLE_HINTS + ("yandex", "chrome", "edge", "firefox", "opera", "brave")
_MIN_WINDOW_PX = 300


@dataclass
class MediaExecutionResult:
    success: bool = True
    message: str = ""
    page_opened: bool = False
    provider: str = "auto"
    request: Optional[Any] = None
    confidence: float = 1.0
    missing_fields: list = field(default_factory=list)
    needs_clarification: bool = False


# ─── Поиск и фокус окна с плеером ─────────────────────────────────────────────
def _find_browser_window():
    """Ищет окно браузера с плеером; окна с медиасервисом в заголовке — первыми."""
    if _OS != "Windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []

        def enum_cb(hwnd, _lp):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.lower()
            if any(hint in title for hint in _BROWSER_HINTS):
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                if rect.right - rect.left > _MIN_WINDOW_PX and rect.bottom - rect.top > _MIN_WINDOW_PX:
                    found.append((hwnd, title))
            return True

        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(enum_cb)
        user32.EnumWindows(proc, 0)
        for hwnd, title in found:
            if any(k in title for k in _MEDIA_TITLE_HINTS):
                return hwnd
        return found[0][0] if found else None
    except Exception as exc:
        logger.debug("Поиск окна браузера не удался: %s", exc)
        return None


def _focus_movie_player() -> bool:
    """Выводит окно браузера с плеером на передний план. False — окна нет."""
    hwnd = _find_browser_window()
    if not hwnd:
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        return True
    except Exception as exc:
        logger.debug("Не удалось активировать окно плеера: %s", exc)
        return False


def _press(key: str, times: int = 1) -> bool:
    """Нажимает клавишу N раз: pyautogui, а без него — общий send_key."""
    for _ in range(times):
        if _HAS_PYAUTOGUI:
            pyautogui.press(key)
        elif not _send_key(key):
            return False
        time.sleep(0.015)
    return True


def _bridge_handled(result: str) -> bool:
    """Оркестратор отвечает «Нет активного…», когда управлять ему нечем."""
    low = (result or "").lower()
    return bool(result) and not low.startswith("нет активного") and "не поддерживается" not in low


# ─── Действия с открытым плеером ──────────────────────────────────────────────
def _fullscreen(player=None) -> str:
    res = get_media_orchestrator().enter_fullscreen()
    if _bridge_handled(res):
        return res
    if not _focus_movie_player():
        return _NO_WINDOW
    if not _send_key("f"):
        return "Не удалось нажать клавишу полного экрана, сэр."
    if player and hasattr(player, "write_log"):
        player.write_log("SYS: ⛶ Полный экран")
    return "Развернул фильм на полный экран, сэр."


def _seek(direction: str, seconds: float, minutes: float, player=None) -> str:
    total = float(seconds or 0) + float(minutes or 0) * 60
    if total <= 0:
        total = _DEFAULT_SEEK_SEC
    if not _focus_movie_player():
        return _NO_WINDOW
    presses = max(1, round(total / _SEEK_STEP_SEC))
    if not _press("right" if direction == "forward" else "left", presses):
        return "Не удалось перемотать: клавиши не дошли до плеера, сэр."
    where = "вперёд" if direction == "forward" else "назад"
    amount = f"{int(minutes)} мин." if minutes and float(minutes) > 0 else f"{int(total)} сек."
    msg = f"Перемотал {where} на {amount}, сэр."
    if player and hasattr(player, "write_log"):
        player.write_log(f"SYS: ⏩ {msg}")
    return msg


def _position_ratio(position: str) -> float:
    pos = (position or "").strip().lower()
    if any(k in pos for k in ("начал", "сначала")):
        return 0.02
    if any(k in pos for k in ("конец", "конц", "финал")):
        return 0.98
    if any(k in pos for k in ("середин", "половин")):
        return 0.5
    m = re.search(r"(\d+)", pos)
    if m:
        val = float(m.group(1))
        return max(0.01, min(0.99, val / 100.0 if val > 1 else val))
    return 0.5


def _seek_to_position(position: str, player=None) -> str:
    ratio = _position_ratio(position)
    res = get_media_orchestrator().seek_percent(ratio * 100)
    if _bridge_handled(res):
        return res
    if not _HAS_PYAUTOGUI:
        return "Перемотка на позицию недоступна без pyautogui, сэр."
    if not _focus_movie_player():
        return _NO_WINDOW
    screen_w, screen_h = pyautogui.size()
    pyautogui.click(int(screen_w * ratio), screen_h - _TIMELINE_OFFSET_PX)
    msg = f"Перемотал на {int(round(ratio * 100))}% фильма, сэр."
    if player and hasattr(player, "write_log"):
        player.write_log(f"SYS: ⏱ {msg}")
    return msg


def _log(player, text: str) -> None:
    if player and hasattr(player, "write_log"):
        player.write_log(text)


# ─── Публичная точка входа ────────────────────────────────────────────────────
def movie_player(parameters: dict, player=None) -> str:
    """
    Главная точка входа для Gemini Live tool 'movie_player'.

    parameters:
        action:   play | pause | resume | stop | close | fullscreen | seek_forward |
                  seek_back | seek_to | volume_up | volume_down
        title:    название фильма для action=play
        seconds:  секунды для перемотки
        minutes:  минуты для перемотки
        position: позиция фильма ('начало', 'середина', '50%')
    """
    action = (parameters.get("action") or "").strip().lower()
    orchestrator = get_media_orchestrator()

    if action in ("play", "start", "запустить", "включить"):
        title = (parameters.get("title") or parameters.get("query") or "").strip()
        if not title and not parameters.get("url"):
            return "Назовите фильм или трек, сэр."
        res = orchestrator.play_media(title, parameters)
        _log(player, f"SYS: 🎬 {res}")
        return res

    if action in ("pause", "пауза"):
        res = orchestrator.pause()
        _log(player, f"SYS: ⏯ {res}")
        return res

    if action in ("resume", "продолжай", "старт"):
        res = orchestrator.play()
        _log(player, f"SYS: ⏯ {res}")
        return res

    if action in ("toggle", "переключить"):
        return orchestrator.toggle_playback()

    if action in ("stop", "стоп", "остановить"):
        res = orchestrator.stop()
        _log(player, f"SYS: ⏹ {res}")
        return res

    if action in ("close", "exit", "закрой", "закрыть", "выход"):
        res = orchestrator.close()
        _log(player, f"SYS: ✕ {res}")
        return res

    if action in ("fullscreen", "full_screen", "полный_экран", "развернуть"):
        return _fullscreen(player)

    if action in ("seek_forward", "forward", "вперёд", "вперед", "дальше"):
        return _seek("forward", parameters.get("seconds"), parameters.get("minutes"), player)

    if action in ("seek_back", "back", "rewind", "назад", "отмотай"):
        return _seek("back", parameters.get("seconds"), parameters.get("minutes"), player)

    if action in ("seek_to", "seek_position", "jump_to", "position", "перейти"):
        return _seek_to_position(str(parameters.get("position") or parameters.get("timecode") or ""), player)

    if action in ("volume_up", "louder", "громче"):
        return orchestrator.volume_up(10)

    if action in ("volume_down", "quieter", "тише"):
        return orchestrator.volume_down(10)

    return f"Не понял команду плеера: «{action}»."
