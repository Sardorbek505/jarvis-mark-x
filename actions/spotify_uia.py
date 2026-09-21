"""Запуск трека в десктопном Spotify через UI Automation.

Зачем: «поставь MiyaGi — Люби меня» открывал `spotify:track:ID` и жал
media-клавишу play. Клавиша не включает открытую страницу — она продолжает
прошлый контекст, то есть плейлист владельца. Кнопка «Слушать» на странице
трека запускает сам трек, а после него Spotify сам подбирает похожие песни
(автовоспроизведение) — как у Алисы «включи песню».

Web API (`PUT /me/player/play`) требует OAuth владельца; у нас только Client
Credentials, поэтому нажимаем кнопку окна. Фокус окна не отнимаем: Invoke
работает и с фоновым окном.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from typing import Optional

_logger = logging.getLogger(__name__)

# Имена кнопок в русском и английском интерфейсе Spotify
PLAY_NAMES = ("слушать", "play")
PAUSE_NAMES = ("пауза", "pause")
PAGE_MENU_PREFIXES = ("открыть контекстное меню: ", "more options for ")

PAGE_WAIT_SEC = 6.0
POLL_SEC = 0.25

_UIA_BUTTON_CONTROL_TYPE = 50000
_UIA_CONTROL_TYPE_PROPERTY = 30003
_UIA_INVOKE_PATTERN = 10000
_TREE_SCOPE_DESCENDANTS = 4
_SW_SHOWNOACTIVATE = 4
_SW_SHOWMINNOACTIVE = 7


def find_spotify_hwnd() -> Optional[int]:
    """Главное окно Spotify.exe. Заголовок ненадёжен: во время игры он «Артист - Трек»."""
    try:
        import psutil
    except ImportError:
        return None
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "spotify.exe"}
    if not pids:
        return None
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def _uia():
    import comtypes
    import comtypes.client

    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # поток уже инициализирован
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation

    return comtypes.client.CreateObject(CUIAutomation._reg_clsid_, interface=IUIAutomation)


def _buttons(uia, hwnd: int) -> list[tuple[str, int, object]]:
    """(имя в нижнем регистре, площадь, элемент) всех кнопок окна."""
    root = uia.ElementFromHandle(hwnd)
    cond = uia.CreatePropertyCondition(_UIA_CONTROL_TYPE_PROPERTY, _UIA_BUTTON_CONTROL_TYPE)
    arr = root.FindAll(_TREE_SCOPE_DESCENDANTS, cond)
    out = []
    for i in range(arr.Length):
        el = arr.GetElement(i)
        try:
            name = (el.CurrentName or "").strip().lower()
            r = el.CurrentBoundingRectangle
            area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
        except Exception:
            continue
        out.append((name, area, el))
    return out


def pick_page_play_button(buttons: list[tuple[str, int, object]], track_name: str):
    """Кнопка «Слушать» страницы трека или None, если страница ещё не открылась.

    Страница узнаётся по кнопке меню в шапке «Открыть контекстное меню: <трек>» —
    без этой проверки можно нажать «Слушать» ПРЕДЫДУЩЕЙ страницы (плейлиста).
    Кнопка страницы — самая крупная «Слушать» (у плеера внизу она меньше).
    Возвращает ("playing", None), если трек уже играет (на кнопке «Пауза»).
    """
    title = (track_name or "").strip().lower()
    if not title:
        return None
    on_page = any(name == prefix + title for name, _, _ in buttons for prefix in PAGE_MENU_PREFIXES)
    if not on_page:
        return None
    plays = [b for b in buttons if b[0] in PLAY_NAMES]
    pauses = [b for b in buttons if b[0] in PAUSE_NAMES]
    biggest_play = max(plays, key=lambda b: b[1], default=None)
    biggest_pause = max(pauses, key=lambda b: b[1], default=None)
    if biggest_pause and (not biggest_play or biggest_pause[1] > biggest_play[1]):
        return ("playing", None)
    if biggest_play:
        return ("play", biggest_play[2])
    return None


def play_open_track_page(track_name: str, timeout: float = PAGE_WAIT_SEC) -> bool:
    """Ждёт открытия страницы трека и нажимает её «Слушать». True — трек запущен."""
    hwnd = find_spotify_hwnd()
    if not hwnd:
        _logger.warning("Spotify UIA: окно Spotify не найдено")
        return False
    try:
        uia = _uia()
        from comtypes.gen.UIAutomationClient import IUIAutomationInvokePattern
    except Exception as exc:
        _logger.warning("Spotify UIA недоступна: %s", exc)
        return False

    # Свёрнутое окно Chromium не перерисовывает: страница трека в дереве
    # доступности не появляется. Разворачиваем без фокуса, потом сворачиваем.
    user32 = ctypes.windll.user32
    was_minimized = bool(user32.IsIconic(hwnd))
    if was_minimized:
        user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
    try:
        return _wait_and_press(uia, hwnd, track_name, timeout, IUIAutomationInvokePattern)
    finally:
        if was_minimized:
            time.sleep(0.3)
            user32.ShowWindow(hwnd, _SW_SHOWMINNOACTIVE)


def _wait_and_press(uia, hwnd: int, track_name: str, timeout: float, invoke_iface) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            picked = pick_page_play_button(_buttons(uia, hwnd), track_name)
            if picked and picked[0] == "playing":
                return True
            if picked:
                pattern = picked[1].GetCurrentPattern(_UIA_INVOKE_PATTERN)
                pattern.QueryInterface(invoke_iface).Invoke()
                _logger.info("Spotify UIA: «%s» запущен кнопкой страницы трека", track_name)
                return True
        except Exception as exc:
            _logger.debug("Spotify UIA: попытка не удалась: %s", exc)
        time.sleep(POLL_SEC)
    _logger.warning("Spotify UIA: страница «%s» не открылась за %.1f с", track_name, timeout)
    return False
