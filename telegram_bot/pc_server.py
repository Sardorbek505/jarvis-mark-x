"""
Desktop JARVIS — connects OUT to your Render/VPS server and executes commands.

Because it dials out, it works behind a home router (NAT) with no port-forwarding.

Run on your PC:
    python -m telegram_bot.pc_server
or double-click  scripts\\start_pc.bat
"""
import asyncio
import base64
import io
import json
import logging
import os
import platform
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets

logger = logging.getLogger(__name__)

# ── Keyword tables ─────────────────────────────────────────────────────────────

_KW = {
    "system": [
        "скриншот", "screenshot", "снимок экрана",
        "заблокируй экран", "заблокировать экран", "lock screen", "lock pc",
        "выключи компьютер", "выключи пк", "shutdown пк", "shutdown компьютер",
        "перезагрузи компьютер", "перезагрузи пк", "restart пк",
        "системная информация", "системная громкость", "sysinfo",
        "батарея", "battery", "заряд аккумулятора",
    ],
    "music": [
        "play", "stop", "pause", "next", "prev", "volume",
        "включи", "выключи", "стоп", "пауза", "следующий",
        "поставь", "запусти", "воспроизведи", "играй", "трек", "песн",
        "переключи", "отключи музыку", "замолчи", "хватит музыку",
        "громче", "тише", "громкость",
    ],
    "weather": ["weather", "погод", "температур"],
    "app": ["open", "открой", "запусти приложение", "закрой приложение"],
    "search": ["search", "find", "найди", "поищи", "поиск", "найти"],
    "window": [
        "сверни", "свернуть", "minimize", "закрой окно", "закрыть окно",
        "рабочий стол", "show desktop", "разверни", "maximize",
        "snap left", "snap right", "alt tab", "переключи окно",
        "проводник", "диспетчер задач", "task manager",
    ],
    "calendar": [
        "календарь", "calendar", "событие", "расписание",
        "встреча", "что сегодня", "планы",
    ],
    "briefing": [
        "брифинг", "briefing", "сводка", "утренний брифинг",
    ],
}


# ── Location ────────────────────────────────────────────────────────────────────

_CITY_CACHE = None


def _default_city() -> str:
    """Determine the user's current city — works wherever the PC is.

    1. IP-based geolocation (auto, follows a travelling laptop).
    2. Fallback to config default_city.
    """
    global _CITY_CACHE
    if _CITY_CACHE:
        return _CITY_CACHE
    try:
        import urllib.request
        with urllib.request.urlopen(
            "http://ip-api.com/json/?fields=city&lang=ru", timeout=4
        ) as r:
            city = json.loads(r.read().decode()).get("city")
            if city:
                _CITY_CACHE = city
                logger.info(f"Определён город по IP: {city}")
                return city
    except Exception as e:
        logger.debug(f"IP geolocation failed: {e}")
    try:
        from telegram_bot.config import load as load_config
        _CITY_CACHE = load_config(require_bot=False).default_city or "Шымкент"
    except Exception:
        _CITY_CACHE = "Шымкент"
    return _CITY_CACHE


# ── Command execution ──────────────────────────────────────────────────────────

async def _execute(text: str) -> dict:
    tl = text.lower().strip()

    try:
        if any(k in tl for k in _KW["system"]):
            if any(k in tl for k in ["скриншот", "screenshot", "снимок"]):
                return await _do_screenshot()
            if any(k in tl for k in ["заблокируй", "заблокировать", "lock screen", "lock pc"]):
                from actions.computer_settings import computer_settings
                return _r(await asyncio.to_thread(computer_settings, {"action": "lock"}))
            if any(k in tl for k in ["выключи компьютер", "выключи пк", "shutdown пк", "shutdown компьютер"]):
                from actions.computer_settings import computer_settings
                return _r(await asyncio.to_thread(computer_settings, {"action": "shutdown"}))
            if any(k in tl for k in ["перезагрузи", "restart пк"]):
                from actions.computer_settings import computer_settings
                return _r(await asyncio.to_thread(computer_settings, {"action": "перезагруз"}))
            if "системная громкость" in tl:
                m = re.search(r'\d+', tl)
                val = max(0, min(100, int(m.group()) if m else 50))
                from actions.computer_settings import computer_settings
                return _r(await asyncio.to_thread(computer_settings, {"action": "volume", "value": str(val)}))
            if any(k in tl for k in ["системная информация", "sysinfo", "батарея", "battery", "заряд"]):
                return _r(_get_sysinfo())

        if any(k in tl for k in _KW["music"]):
            from actions.spotify_controller import spotify_player
            params = _parse_music(tl)
            return _r(await asyncio.to_thread(spotify_player, params) or "Выполнено")

        if any(k in tl for k in _KW["weather"]):
            from actions.weather import weather_action
            city = _extract_after(tl, ["в ", "in ", "погода ", "погоду в "]) or _default_city()
            return _r(await asyncio.to_thread(weather_action, {"city": city}) or "Погода получена")

        if any(k in tl for k in _KW["app"]):
            from actions.open_app import open_app
            app_name = _extract_after(tl, ["открой ", "запусти ", "open ", "закрой ", "close "])
            return _r(await asyncio.to_thread(open_app, {"app_name": app_name or text}) or "Выполнено")

        if any(k in tl for k in _KW["search"]):
            from actions.web_search import web_search
            query = _extract_after(tl, ["найди ", "поищи ", "search ", "find ", "найти "]) or text
            return _r(await asyncio.to_thread(web_search, {"query": query}) or "Поиск запущен")

        if any(k in tl for k in _KW["window"]):
            from actions.window_control import window_control
            return _r(await asyncio.to_thread(window_control, _parse_window(tl)) or "Выполнено")

        if any(k in tl for k in _KW["calendar"]):
            try:
                from actions.calendar import calendar_action
                return _r(await asyncio.to_thread(calendar_action, _parse_calendar(tl)) or "Готово")
            except Exception as e:
                return _r(f"Календарь недоступен: {e}")

        if any(k in tl for k in _KW["briefing"]):
            try:
                from actions.morning_briefing import morning_briefing
                return _r(await asyncio.to_thread(morning_briefing, {"city": _default_city()}) or "Брифинг недоступен")
            except Exception as e:
                return _r(f"Брифинг недоступен: {e}")

        return _r(f"Не понял команду: «{text}». Напиши /help чтобы увидеть что я умею.")

    except Exception as e:
        logger.error(f"PC execute error: {e}")
        return _r(f"Ошибка: {e}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _r(text: str, image_b64: str = None) -> dict:
    return {"text": text, "image_b64": image_b64}


async def _do_screenshot() -> dict:
    from actions.computer_settings import computer_settings
    msg = await asyncio.to_thread(computer_settings, {"action": "screenshot"})
    path = msg.split(":", 1)[-1].strip() if ":" in msg else None
    if path and os.path.exists(path):
        image_b64 = _encode_image(path)
        if image_b64:
            return {"text": "Скриншот ✅", "image_b64": image_b64}
    return _r(msg)


def _encode_image(path: str):
    try:
        try:
            from PIL import Image
            with Image.open(path) as img:
                if img.width > 1280:
                    ratio = 1280 / img.width
                    img = img.resize((1280, int(img.height * ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=75)
                return base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode()
    except Exception as e:
        logger.error(f"Image encode: {e}")
        return None


def _get_sysinfo() -> str:
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\" if platform.system() == "Windows" else "/")
        lines = [
            "🖥 *Состояние системы*",
            f"CPU: {cpu:.0f}%",
            f"RAM: {mem.percent:.0f}% ({mem.used // 1024**2} МБ / {mem.total // 1024**2} МБ)",
            f"Диск: {disk.percent:.0f}% ({disk.used // 1024**3:.1f} / {disk.total // 1024**3:.1f} ГБ)",
        ]
        try:
            bat = psutil.sensors_battery()
            if bat:
                plug = "⚡ Зарядка" if bat.power_plugged else "🔋"
                lines.append(f"Батарея: {bat.percent:.0f}% {plug}")
        except Exception:
            pass
        return "\n".join(lines)
    except ImportError:
        return "psutil не установлен. Запустите: pip install psutil"
    except Exception as e:
        return f"Ошибка системной информации: {e}"


def _parse_music(tl: str) -> dict:
    if any(k in tl for k in ["stop", "стоп", "выключи", "пауза", "pause", "отключи", "замолчи", "хватит"]):
        return {"action": "pause"}
    if any(k in tl for k in ["next", "следующий", "следующую", "следующая", "skip", "переключи", "дальше", "другую", "другой"]):
        return {"action": "next"}
    if any(k in tl for k in ["prev", "предыдущий", "предыдущую", "назад", "обратно"]):
        return {"action": "prev"}
    if any(k in tl for k in ["громче", "louder", "прибавь"]):
        return {"action": "volume_up"}
    if any(k in tl for k in ["тише", "quieter", "убавь"]):
        return {"action": "volume_down"}
    if any(k in tl for k in ["перемешай", "shuffle", "random"]):
        return {"action": "shuffle"}
    if any(k in tl for k in ["что играет", "какой трек", "now playing"]):
        return {"action": "now_playing"}
    match = re.search(r"(?:play|включи|играй|поставь|запусти|воспроизведи)\s+(.+)", tl)
    query = match.group(1).strip() if match else ""
    if query in ("музыку", "music", "музыка", "трек", "песню", ""):
        query = ""
    return {"action": "play", "query": query}


def _parse_window(tl: str) -> dict:
    if any(k in tl for k in ["сверни все", "свернуть все", "minimize all", "win+m"]):
        return {"action": "minimize_all"}
    if any(k in tl for k in ["рабочий стол", "show desktop", "win+d"]):
        return {"action": "show_desktop"}
    if any(k in tl for k in ["сверни", "свернуть", "minimize"]):
        target = _extract_after(tl, ["сверни ", "свернуть "])
        if target and target not in ("окно", "window"):
            return {"action": "activate", "target": target}
        return {"action": "minimize"}
    if any(k in tl for k in ["разверни", "maximize"]):
        return {"action": "maximize"}
    if any(k in tl for k in ["закрой окно", "закрыть окно", "close window"]):
        return {"action": "close"}
    if any(k in tl for k in ["alt tab", "переключи окно", "switch"]):
        return {"action": "switch"}
    if any(k in tl for k in ["проводник", "explorer"]):
        return {"action": "open_explorer"}
    if any(k in tl for k in ["диспетчер задач", "task manager"]):
        return {"action": "task_manager"}
    return {"action": "minimize_all"}


def _parse_calendar(tl: str) -> dict:
    if any(k in tl for k in ["что сегодня", "расписание", "планы", "сегодня"]):
        return {"action": "today"}
    if any(k in tl for k in ["добавь", "создай", "запланируй", "поставь встречу"]):
        task = _extract_after(tl, ["добавь ", "создай ", "запланируй "]) or tl
        return {"action": "add", "title": task}
    return {"action": "list"}


def _extract_after(tl: str, markers: list) -> str:
    for marker in markers:
        idx = tl.find(marker)
        if idx != -1:
            return tl[idx + len(marker):].strip()
    return ""


# ── WebSocket client — dials out to Render/VPS ─────────────────────────────────

async def _handle(ws, msg: dict):
    result = await _execute(msg.get("text", ""))
    try:
        await ws.send(json.dumps({
            "type": "response",
            "req_id": msg.get("req_id"),
            "text": result.get("text", ""),
            "image_b64": result.get("image_b64"),
            "user_id": msg.get("user_id"),
        }))
    except Exception as e:
        logger.error(f"Send response: {e}")


async def run_client(url: str, token: str):
    if not url:
        logger.error(
            "pc_link_url не задан. Добавь в config/api_keys.json:\n"
            '  "pc_link_url": "wss://ТВОЙ-САЙТ.onrender.com",\n'
            '  "pc_link_token": "ТВОЙ-СЕКРЕТ"'
        )
        return

    base = url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]

    sep = "&" if "?" in base else "?"
    uri = f"{base}/pc-link{sep}token={token}"
    safe_uri = uri.split("token=")[0] + "token=***"
    logger.info(f"Подключаюсь к JARVIS: {safe_uri}")

    while True:
        try:
            async with websockets.connect(
                uri, ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024
            ) as ws:
                logger.info("✅ Подключено. Жду команды с телефона/Telegram…")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("type") == "command":
                        asyncio.create_task(_handle(ws, msg))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Отключено: {e}. Переподключение через 5 секунд…")
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    from telegram_bot.config import load as load_config
    cfg = load_config(require_bot=False)
    try:
        asyncio.run(run_client(cfg.pc_link_url, cfg.pc_link_token))
    except KeyboardInterrupt:
        logger.info("PC client stopped.")
