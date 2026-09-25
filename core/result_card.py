"""Карточка результата рядом с шаром — «что Джарвис сейчас сделал».

Как на референсном видео: открыл сайт — рядом всплывает окошко с этим
сайтом; нашёл ответ — окошко с найденным. Здесь только данные для карточки
и снимок окна, рисует её HUD (ui.py).

Снимок берётся с окна на переднем плане: браузер, плеер, Spotify — то, что
команда только что открыла. Собственное окно Джарвиса не снимаем: в карточке
был бы сам Джарвис.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Команды, после которых на экране появляется окно, — его и показываем.
_SHOT_TOOLS = {"browser", "open_app", "movie_player", "music_player", "window_control"}
_TITLES = {
    "web_search": "Поиск", "browser": "Браузер", "weather": "Погода",
    "open_app": "Приложение", "music_player": "Музыка", "movie_player": "Фильм",
    "translation": "Перевод", "calendar": "Календарь", "files": "Файлы",
    "obsidian": "Заметки", "morning_briefing": "Брифинг", "look_at_screen": "Экран",
    "look_at_camera": "Камера", "send_to_telegram": "Telegram",
    "computer_control": "Компьютер", "window_control": "Окна",
    "save_to_memory": "Память", "sleep_timer": "Таймер",
}
# Служебное — карточка тут только мешала бы.
_SKIP = {"set_mode", "shutdown_jarvis", "switch_voice", "team_collaboration"}
_BODY_MAX = 420


def build_card(name: str, args: dict | None, result) -> dict | None:
    """{'title', 'address', 'body', 'want_shot'} или None, если показывать нечего."""
    if name in _SKIP:
        return None
    args = dict(args or {})
    if isinstance(result, dict):
        result = result.get("result", result)
    body = " ".join(str(result or "").split())
    if len(body) > _BODY_MAX:
        body = body[:_BODY_MAX].rsplit(" ", 1)[0] + "…"

    address = ""
    for key in ("url", "query", "city", "app_name", "name", "text", "path", "action"):
        val = args.get(key)
        if val:
            address = str(val)
            break
    if name == "web_search" and address:
        address = f"поиск: {address}"
    elif name == "weather" and address:
        address = f"погода: {address}"
    address = address or f"jarvis://{name}"

    extra = ""
    if name == "weather":
        # Прогноз целиком — HUD рисует из него карточку погоды со значками.
        try:
            from actions.weather import last_forecast
            fc = last_forecast.get(str(args.get("city", "")).strip().lower())
            if fc:
                extra = json.dumps({"card": "weather", **fc}, ensure_ascii=False)
        except Exception as exc:
            logger.debug("Прогноз для карточки: %s", exc)

    return {
        "title": _TITLES.get(name, name.replace("_", " ").capitalize()),
        "address": address[:90],
        "body": body,
        "want_shot": name in _SHOT_TOOLS and sys.platform == "win32",
        "extra": extra,
    }


def capture_foreground_png(max_w: int = 900) -> bytes | None:
    """PNG окна на переднем плане (только Windows). None — снимать нечего."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd or user32.IsIconic(hwnd):
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return None                       # это наше окно
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < 200 or h < 150:
            return None

        import mss
        from PIL import Image

        with mss.mss() as sct:
            raw = sct.grab({"left": rect.left, "top": rect.top, "width": w, "height": h})
        img = Image.frombytes("RGB", raw.size, raw.rgb)
        if img.width > max_w:
            img = img.resize((max_w, int(img.height * max_w / img.width)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        logger.debug("Снимок окна не вышел: %s", exc)
        return None
