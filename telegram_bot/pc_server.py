"""
Desktop JARVIS WebSocket server — accepts commands from Telegram bot / Mini App.

Run on your PC:
    python -m telegram_bot.pc_server
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
    # Checked FIRST — prevents "выключи пк" from matching music pause
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
        "брифинг", "briefing", "сводка", "утренний брифинг", "что сегодня",
    ],
}


# ── PC Server class ────────────────────────────────────────────────────────────

class PCServer:
    def __init__(self, port: int = 8765):
        self._port = port
        self._clients: set = set()

    async def start(self):
        async with websockets.serve(self._handler, "0.0.0.0", self._port):
            logger.info(f"PC Server started on port {self._port}")
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

    async def notify_all(self, text: str, user_id: int = None):
        msg = json.dumps({"type": "notification", "text": text, "user_id": user_id})
        for ws in list(self._clients):
            try:
                await ws.send(msg)
            except Exception:
                self._clients.discard(ws)

    async def _handler(self, ws):
        self._clients.add(ws)
        logger.info(f"Bot connected: {ws.remote_address}")
        try:
            async for raw in ws:
                await self._process(ws, raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            logger.info(f"Bot disconnected: {ws.remote_address}")

    async def _process(self, ws, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if msg.get("type") != "command":
            return

        text = msg.get("text", "")
        result = await _execute(text)
        await ws.send(json.dumps({
            "type": "response",
            "req_id": msg.get("req_id"),
            "text": result.get("text", ""),
            "image_b64": result.get("image_b64"),
            "user_id": msg.get("user_id"),
        }))


# ── Command execution ──────────────────────────────────────────────────────────

async def _execute(text: str) -> dict:
    tl = text.lower().strip()

    try:
        # ── 1. System commands (checked before music to avoid keyword clashes) ──
        if any(k in tl for k in _KW["system"]):

            # Screenshot
            if any(k in tl for k in ["скриншот", "screenshot", "снимок"]):
                return await _do_screenshot()

            # Lock screen
            if any(k in tl for k in ["заблокируй", "заблокировать", "lock screen", "lock pc"]):
                from actions.computer_settings import computer_settings
                msg = await asyncio.to_thread(computer_settings, {"action": "lock"})
                return _r(msg)

            # Shutdown / restart
            if any(k in tl for k in ["выключи компьютер", "выключи пк", "shutdown пк", "shutdown компьютер"]):
                from actions.computer_settings import computer_settings
                msg = await asyncio.to_thread(computer_settings, {"action": "shutdown"})
                return _r(msg)
            if any(k in tl for k in ["перезагрузи", "restart пк"]):
                from actions.computer_settings import computer_settings
                msg = await asyncio.to_thread(computer_settings, {"action": "перезагруз"})
                return _r(msg)

            # System volume (absolute %)
            if "системная громкость" in tl:
                m = re.search(r'\d+', tl)
                val = int(m.group()) if m else 50
                val = max(0, min(100, val))
                from actions.computer_settings import computer_settings
                msg = await asyncio.to_thread(computer_settings, {"action": "volume", "value": str(val)})
                return _r(msg)

            # System info
            if any(k in tl for k in ["системная информация", "sysinfo", "батарея", "battery", "заряд"]):
                return _r(_get_sysinfo())

        # ── 2. Music / Spotify ────────────────────────────────────────────────
        if any(k in tl for k in _KW["music"]):
            from actions.spotify_controller import spotify_player
            params = _parse_music(tl)
            result = await asyncio.to_thread(spotify_player, params)
            return _r(result or "Выполнено")

        # ── 3. Weather ────────────────────────────────────────────────────────
        if any(k in tl for k in _KW["weather"]):
            from actions.weather import weather_action
            city = _extract_after(tl, ["в ", "in ", "погода ", "погоду в "]) or "Ташкент"
            result = await asyncio.to_thread(weather_action, {"city": city})
            return _r(result or "Погода получена")

        # ── 4. Open app ───────────────────────────────────────────────────────
        if any(k in tl for k in _KW["app"]):
            from actions.open_app import open_app
            app_name = _extract_after(tl, ["открой ", "запусти ", "open ", "закрой ", "close "])
            result = await asyncio.to_thread(open_app, {"app_name": app_name or text})
            return _r(result or "Выполнено")

        # ── 5. Web search ─────────────────────────────────────────────────────
        if any(k in tl for k in _KW["search"]):
            from actions.web_search import web_search
            query = _extract_after(tl, ["найди ", "поищи ", "search ", "find ", "найти "]) or text
            result = await asyncio.to_thread(web_search, {"query": query})
            return _r(result or "Поиск запущен")

        # ── 6. Window control ─────────────────────────────────────────────────
        if any(k in tl for k in _KW["window"]):
            from actions.window_control import window_control
            params = _parse_window(tl)
            result = await asyncio.to_thread(window_control, params)
            return _r(result or "Выполнено")

        # ── 7. Calendar ───────────────────────────────────────────────────────
        if any(k in tl for k in _KW["calendar"]):
            try:
                from actions.calendar import calendar_action
                params = _parse_calendar(tl)
                result = await asyncio.to_thread(calendar_action, params)
                return _r(result or "Готово")
            except Exception as e:
                return _r(f"Календарь недоступен: {e}")

        # ── 8. Morning briefing ───────────────────────────────────────────────
        if any(k in tl for k in _KW["briefing"]):
            try:
                from actions.morning_briefing import morning_briefing
                result = await asyncio.to_thread(morning_briefing, {})
                return _r(result or "Брифинг недоступен")
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
    """Take a screenshot, compress to JPEG and return base64."""
    from actions.computer_settings import computer_settings
    msg = await asyncio.to_thread(computer_settings, {"action": "screenshot"})

    # Extract the saved file path from the result message
    path = None
    if ":" in msg:
        path = msg.split(":", 1)[-1].strip()

    if path and os.path.exists(path):
        image_b64 = _encode_image(path)
        if image_b64:
            return {"text": "Скриншот ✅", "image_b64": image_b64}

    return _r(msg)


def _encode_image(path: str) -> str | None:
    """Read image file, compress to JPEG, return base64 or None."""
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
        disk_path = "C:\\" if platform.system() == "Windows" else "/"
        disk = psutil.disk_usage(disk_path)

        lines = [
            "🖥 *Состояние системы*",
            f"CPU: {cpu:.0f}%",
            f"RAM: {mem.percent:.0f}% ({mem.used // 1024**2} МБ / {mem.total // 1024**2} МБ)",
            f"Диск: {disk.percent:.0f}% ({disk.used // 1024**3:.1f} ГБ / {disk.total // 1024**3:.1f} ГБ)",
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


# ── Singleton ──────────────────────────────────────────────────────────────────

_server: PCServer | None = None


def get_server(port: int = 8765) -> PCServer:
    global _server
    if _server is None:
        _server = PCServer(port)
    return _server


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    from telegram_bot.config import load as load_config
    cfg = load_config()
    try:
        asyncio.run(get_server(cfg.pc_ws_port).start())
    except KeyboardInterrupt:
        logger.info("PC Server stopped.")
