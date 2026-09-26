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
import re
import sys
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Команды, после которых на экране появляется окно, — его и показываем.
# Музыки тут нет: Spotify обычно играет в фоне, карточка плеера красивее.
_SHOT_TOOLS = {"browser", "open_app", "movie_player", "window_control"}
_TITLES = {
    "web_search": "Поиск", "browser": "Браузер", "weather": "Погода",
    "open_app": "Приложение", "music_player": "Музыка", "movie_player": "Фильм",
    "youtube_player": "YouTube", "video_control": "Видео",
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
    for key in ("url", "query", "city", "app_name", "title", "name", "text", "path"):
        val = args.get(key)
        if val:
            address = str(val)
            break
    if name == "web_search" and address:
        address = f"поиск: {address}"
    elif name == "weather" and address:
        address = f"погода: {address}"
    address = address or f"jarvis://{name}"

    try:
        data = _card_data(name, args, str(result or ""))
    except Exception as exc:                  # карточка — украшение, не повод падать
        logger.debug("Данные карточки %s: %s", name, exc)
        data = None
    extra = json.dumps(data, ensure_ascii=False) if data else ""
    # Адрес без понятного аргумента — по смыслу карточки, а не служебное
    # «volume_up» / «set» / «get_events».
    if address.startswith("jarvis://") or name in ("computer_control", "sleep_timer"):
        kind = (data or {}).get("card")
        address = {
            "meter": (data or {}).get("label", "").lower(),
            "timer": "таймер сна",
            "memory": "память",
            "message": "telegram",
        }.get(kind) or {"calendar": "календарь", "files": "файлы", "obsidian": "заметки",
                        "morning_briefing": "утренний брифинг",
                        "computer_control": "компьютер"}.get(name) or address

    return {
        "title": _TITLES.get(name, name.replace("_", " ").capitalize()),
        "address": address[:90],
        "body": body,
        "want_shot": name in _SHOT_TOOLS and sys.platform == "win32",
        "extra": extra,
    }


_FAIL_RE = re.compile(r"^(сэр,\s*)?(ошибка|не удалось|не получилось|не понял|не нашёл|"
                      r"не найден|укажите|нет связи|произошла ошибка)", re.I)
_MEDIA_STATUS = {
    "play": "Играет", "pause": "Пауза", "resume": "Играет", "stop": "Остановлено",
    "next": "Следующий трек", "previous": "Предыдущий трек", "prev": "Предыдущий трек",
    "volume": "Громкость", "shuffle": "Вперемешку", "playlist": "Плейлист",
}


def _items(text: str, sep: str | None = None, limit: int = 5) -> list[str]:
    parts = text.split(sep) if sep else text.splitlines()
    out = []
    for part in parts:
        # маркер списка («•», «-», «1.», «2)») — но не время «10:00» в начале
        part = re.sub(r"^\s*(?:[•\-*–—·]+|\d{1,2}[.)](?!\d))\s*", "", part).strip()
        if part:
            out.append(part if len(part) <= 150 else part[:150].rsplit(" ", 1)[0] + "…")
    return out[:limit]


def _card_data(name: str, args: dict, result: str) -> dict | None:
    """Данные для особой карточки. None — обычная текстовая."""
    text = " ".join(result.split())
    if _FAIL_RE.match(text):
        return {"card": "error", "text": text}

    if name == "weather":
        from actions.weather import last_forecast
        fc = last_forecast.get(str(args.get("city", "")).strip().lower())
        return {"card": "weather", **fc} if fc else None

    if name == "web_search":
        m = re.match(r"По запросу «(.+?)»:\s*(.*)", text)
        if m:
            items = _items(m.group(2), " | ")
            if items:
                return {"card": "list", "heading": "Найдено", "items": items}
        return None

    if name in ("calendar", "files", "obsidian"):
        items = _items(result)
        if len(items) >= 2:
            heading = {"calendar": "События", "files": "Файлы", "obsidian": "Заметки"}[name]
            return {"card": "list", "heading": heading, "items": items}
        return None

    if name == "youtube_player":
        m = re.search(r"«(.+?)»", text)
        title = m.group(1) if m else str(args.get("query") or args.get("channel") or "YouTube")
        return {"card": "media", "media": "film", "title": title,
                "status": "YouTube" + (" · последнее видео" if args.get("action") == "latest" else ""),
                "playing": text.startswith("Включил")}

    if name in ("music_player", "movie_player"):
        action = str(args.get("action", "play")).lower()
        title = str(args.get("query") or args.get("title") or "").strip()
        if not title:
            m = re.search(r"«(.+?)»", text)
            title = m.group(1) if m else ("Музыка" if name == "music_player" else "Фильм")
        return {"card": "media", "media": "music" if name == "music_player" else "film",
                "title": title, "status": _MEDIA_STATUS.get(action, text[:60]),
                "playing": action not in ("pause", "stop")}

    if name == "computer_control":
        action = str(args.get("action", "")).lower()
        kind = "volume" if ("volume" in action or action == "mute") else \
            "brightness" if "bright" in action else None
        if kind:
            val = args.get("value")
            m = re.search(r"(\d{1,3})\s*%", text)
            level = 0 if action == "mute" else int(val) if str(val or "").isdigit() else \
                int(m.group(1)) if m else None
            return {"card": "meter", "meter": kind, "level": level,
                    "label": "Громкость" if kind == "volume" else "Яркость"}
        return None

    if name == "sleep_timer":
        action = str(args.get("action", "")).lower()
        total = None
        if action == "set":
            try:
                total = float(args.get("duration_minutes")) * 60
            except (TypeError, ValueError):
                total = None
        m = re.search(r"осталось\s+(?:(\d+)\s*мин\s*)?(\d+)\s*сек", text)
        if m:
            total = int(m.group(1) or 0) * 60 + int(m.group(2))
        if total:
            return {"card": "timer", "due": time.time() + total, "total": total,
                    "label": "До выключения"}
        return None

    if name == "translation" and args.get("text"):
        return {"card": "translate", "src": str(args["text"])[:200], "dst": text[:300],
                "lang": str(args.get("target_language") or "").capitalize()}

    if name == "send_to_telegram":
        body = str(args.get("text") or "").strip()
        if args.get("send_screenshot") and not body:
            body = "📷 Снимок экрана"
        return {"card": "message", "text": body[:220] or text[:220], "to": "Telegram"}

    if name == "save_to_memory":
        key, val = str(args.get("key") or "").strip(), str(args.get("value") or "").strip()
        return {"card": "memory", "key": key, "value": val or text[:160],
                "category": str(args.get("category") or "")}

    if name in ("open_app", "browser"):
        label = str(args.get("app_name") or "").strip()
        if not label and args.get("url"):
            label = urlparse(str(args["url"])).netloc.removeprefix("www.") or str(args["url"])
        label = label or str(args.get("query") or args.get("browser") or "Браузер")
        return {"card": "app", "name": label, "status": text[:80]}
    return None


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
