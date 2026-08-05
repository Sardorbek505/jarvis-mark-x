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
import random
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets

from telegram_bot import keywords

logger = logging.getLogger(__name__)

# ── Reconnect policy ───────────────────────────────────────────────────────────
# The cloud side can be down for weeks. Fixed 5s retries produced 46k identical
# log lines (7 MB) during one such outage — hence backoff + repeat suppression.
_RECONNECT_MIN_SEC = 5.0
_RECONNECT_MAX_SEC = 300.0
_RECONNECT_FACTOR = 1.6
_RECONNECT_JITTER = 0.2      # ±20%, so restarts don't sync up on the server
_LOG_EVERY = 20              # repeat an unchanged reason only every Nth try

_LOG_PATH = Path(__file__).resolve().parent.parent / "jarvis_pc_server.log"
_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUPS = 3

# ── Keyword tables ─────────────────────────────────────────────────────────────

_KW = {
    "camera": [
        "камер", "веб-камер", "вебкам", "webcam", "camera",
        "что рядом", "что вокруг", "что там происходит", "посмотри вокруг",
        "сфоткай", "снимок с камеры", "фото с камеры",
    ],
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
    except Exception as exc:
        # Молчаливая подмена города = «погода не та» без объяснения причины.
        logger.warning("Город из конфига не прочитался (%s) — беру Шымкент", exc)
        _CITY_CACHE = "Шымкент"
    return _CITY_CACHE


# ── Command execution ──────────────────────────────────────────────────────────

async def _execute(text: str) -> dict:
    tl = text.lower().strip()

    try:
        # Keyboard buttons (checked early so "разблокируй" isn't caught by the
        # lock/"заблокируй" block). Enter / unlock-sequence.
        if any(k in tl for k in ("разблокир", "unlock", "открой блокировку", "сними блокировку")):
            return _do_unlock()
        if tl in ("enter", "ввод", "интер") or any(
                k in tl for k in ("нажми enter", "нажать enter", "нажми интер", "клавиша enter")):
            return _do_press_enter()

        # System volume via media keys (reliable, independent of the music player).
        if tl in ("громче", "погромче", "сделай громче", "сделай погромче",
                  "прибавь громкость", "volume up"):
            return _do_volume(+1)
        if tl in ("тише", "потише", "сделай тише", "сделай потише",
                  "убавь громкость", "volume down"):
            return _do_volume(-1)

        # Launch the desktop JARVIS app (main.py) on this PC. Must be checked
        # BEFORE the open_app block, otherwise "запусти ..." is caught there.
        if any(k in tl for k in (
            "запусти джарвис", "запусти jarvis", "открой джарвис", "открой jarvis",
            "включи джарвис", "launch jarvis", "джарвис на пк", "десктоп джарвис",
            "запусти ассистент", "open jarvis",
        )):
            return _launch_jarvis()

        # Camera first — "снимок с камеры" must not be caught by screenshot
        if keywords.matches(tl, _KW["camera"]):
            return await _do_camera()

        if keywords.matches(tl, _KW["system"]):
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

        if keywords.matches(tl, _KW["music"]):
            params = _parse_music(tl)
            # play/pause/next/prev/stop/volume работают БЕЗ Spotify Web API —
            # через media-клавиши (Win32 keybd_event) и Spotify URI. Web API
            # используется только для точного поиска трека, если креды есть.
            if params["action"] in ("shuffle", "now_playing"):
                from actions.spotify_controller import spotify_player
                return _r(await asyncio.to_thread(spotify_player, params) or "Выполнено")
            from actions.music_player import music_player
            return _r(await asyncio.to_thread(music_player, params) or "Выполнено")

        if keywords.matches(tl, _KW["weather"]):
            from actions.weather import weather_action
            city = _extract_after(tl, ["в ", "in ", "погода ", "погоду в "]) or _default_city()
            return _r(await asyncio.to_thread(weather_action, {"city": city}) or "Погода получена")

        if keywords.matches(tl, _KW["app"]):
            from actions.open_app import open_app
            app_name = _extract_after(tl, ["открой ", "запусти ", "open ", "закрой ", "close "])
            return _r(await asyncio.to_thread(open_app, {"app_name": app_name or text}) or "Выполнено")

        if keywords.matches(tl, _KW["search"]):
            from actions.web_search import web_search
            query = _extract_after(tl, ["найди ", "поищи ", "search ", "find ", "найти "]) or text
            return _r(await asyncio.to_thread(web_search, {"query": query}) or "Поиск запущен")

        if keywords.matches(tl, _KW["window"]):
            from actions.window_control import window_control
            return _r(await asyncio.to_thread(window_control, _parse_window(tl)) or "Выполнено")

        if keywords.matches(tl, _KW["calendar"]):
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

    except ModuleNotFoundError as e:
        logger.error(f"PC execute missing module: {e}")
        pkg = (e.name or "").split(".")[0]
        return _r(
            f"⚠ Для этой команды на ПК не хватает модуля «{pkg}». "
            f"Установи в консоли компьютера: `pip install {pkg}`"
        )
    except Exception as e:
        logger.error(f"PC execute error: {e}")
        return _r(f"Ошибка при выполнении: {e}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _r(text: str, image_b64: str = None) -> dict:
    return {"text": text, "image_b64": image_b64}


# ── Keyboard input (Windows SendInput) ───────────────────────────────────────
# NOTE: input from this user-session process reaches the ACTIVE desktop only.
# Windows isolates the secure lock screen (Winlogon desktop), so these keystrokes
# CANNOT type into a real password lock screen — that needs RustDesk (its service
# operates at the secure desktop). They work fine on the awake/unlocked desktop.

_VK_ENTER = 0x0D
_VK_SPACE = 0x20
_UNLOCK_FILE = Path(__file__).resolve().parent.parent / "config" / "unlock_secret.txt"


def _read_unlock_password() -> str:
    try:
        if _UNLOCK_FILE.exists():
            return _UNLOCK_FILE.read_text(encoding="utf-8").strip() or "э"
    except Exception as exc:
        logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    return "э"


def _send_input(wVk: int = 0, wScan: int = 0, flags: int = 0):
    import ctypes
    PUL = ctypes.POINTER(ctypes.c_ulong)

    class _KBD(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", PUL)]

    class _U(ctypes.Union):
        _fields_ = [("ki", _KBD)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

    extra = ctypes.c_ulong(0)
    ki = _KBD(wVk, wScan, flags, 0, ctypes.pointer(extra))
    inp = _INPUT(1, _U(ki=ki))  # type 1 = INPUT_KEYBOARD
    ctypes.windll.user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))


def _press_vk(vk: int):
    _send_input(wVk=vk)                 # key down
    _send_input(wVk=vk, flags=0x0002)   # key up (KEYEVENTF_KEYUP)


def _type_char(ch: str):
    code = ord(ch)
    _send_input(wScan=code, flags=0x0004)          # KEYEVENTF_UNICODE down
    _send_input(wScan=code, flags=0x0004 | 0x0002)  # up


def _do_press_enter() -> dict:
    try:
        _press_vk(_VK_ENTER)
        return _r("⏎ Enter нажат.")
    except Exception as e:
        return _r(f"❌ Не смог нажать Enter: {e}")


def _do_volume(direction: int, steps: int = 3) -> dict:
    """System volume via media keys — always works (unlike player-only volume)."""
    vk = 0xAF if direction > 0 else 0xAE  # VK_VOLUME_UP / VK_VOLUME_DOWN
    try:
        for _ in range(steps):
            _press_vk(vk)
        return _r("🔊 Громче" if direction > 0 else "🔉 Тише")
    except Exception as e:
        return _r(f"❌ Громкость: {e}")


def _do_unlock() -> dict:
    """Wake the screen (Space) → type the unlock password → Enter.
    Works only if the screen is NOT on the secure Windows lock (see note above)."""
    import time
    try:
        pw = _read_unlock_password()
        _press_vk(_VK_SPACE)
        time.sleep(0.4)
        for ch in pw:
            _type_char(ch)
            time.sleep(0.03)
        time.sleep(0.2)
        _press_vk(_VK_ENTER)
        return _r("🔓 Отправил: пробел → пароль → Enter. Если это был защищённый "
                  "локскрин Windows — ввод туда не проходит (используй RustDesk).")
    except Exception as e:
        return _r(f"❌ Не смог отправить разблокировку: {e}")


def _launch_jarvis() -> dict:
    """Start the desktop JARVIS GUI (main.py) on this PC, in the user's session."""
    import subprocess
    base = Path(__file__).resolve().parent.parent
    main_py = base / "main.py"
    if not main_py.exists():
        return _r("❌ main.py не найден на ПК — не могу запустить десктопного JARVIS.")
    # Don't launch a second copy if it's already running.
    try:
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            cl = p.info.get("cmdline") or []
            if any("main.py" in str(x) for x in cl):
                return _r("🤖 Десктопный JARVIS уже запущен на ПК.")
    except Exception as exc:
        logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    try:
        # Use python.exe (NOT pythonw — it dies when the app writes to a missing
        # stdout) and redirect output to a log so a missing console can't crash it.
        # CREATE_NO_WINDOW hides the console; the Qt GUI window still shows.
        log_path = base / "logs"
        log_path.mkdir(exist_ok=True)
        logf = open(log_path / "desktop_jarvis.log", "a", encoding="utf-8")
        kwargs = {"cwd": str(base), "stdout": logf, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            # DETACHED_PROCESS = fully independent of pc_server (survives its restart)
            # and no console; stdout is redirected so a missing console can't crash it.
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([sys.executable, str(main_py)], **kwargs)
        logger.info("Launched desktop JARVIS (main.py)")
        return _r("🤖 Запускаю десктопного JARVIS на ПК — окно сейчас откроется.")
    except Exception as e:
        logger.error(f"launch jarvis failed: {e}")
        return _r(f"❌ Не смог запустить JARVIS: {e}")


async def _do_screenshot() -> dict:
    from actions.computer_settings import computer_settings
    msg = await asyncio.to_thread(computer_settings, {"action": "screenshot"})
    path = msg.split(":", 1)[-1].strip() if ":" in msg else None
    if path and os.path.exists(path):
        image_b64 = _encode_image(path)
        if image_b64:
            return {"text": "Скриншот ✅", "image_b64": image_b64}
    return _r(msg)


async def _do_camera() -> dict:
    from actions.camera import camera_snapshot
    msg = await asyncio.to_thread(camera_snapshot, {})
    path = msg.split(":", 1)[-1].strip() if msg.startswith("Снимок с камеры:") else None
    if path and os.path.exists(path):
        image_b64 = _encode_image(path)
        try:
            os.remove(path)  # clean up temp file
        except OSError:
            pass
        if image_b64:
            return {"text": "📷 Снимок с камеры", "image_b64": image_b64}
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
            # Без Pillow снимок уедет исходным PNG — в разы тяжелее, дольше
            # грузится в Telegram и может упереться в лимит размера.
            logger.warning("Pillow не установлен — отправляю снимок без сжатия")
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
        except Exception as exc:
            logger.debug("Подавлено исключение: %s", exc, exc_info=True)
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

_gemini = None


def _get_gemini():
    """Lazy GeminiClient for voice synthesis (uses the PC's own config key)."""
    global _gemini
    if _gemini is None:
        try:
            from telegram_bot.config import load as load_config
            from telegram_bot.gemini_client import GeminiClient
            c = load_config(require_bot=False)
            if c.gemini_api_key:
                _gemini = GeminiClient(c.gemini_api_key, c.gemini_model)
        except Exception as e:
            logger.warning(f"Gemini init (voice) failed: {e}")
    return _gemini


async def _handle_userbot(msg: dict) -> dict:
    """Deliver an outbound message via the Telethon userbot."""
    from telegram_bot import pc_userbot
    target = msg.get("target", "")
    text = msg.get("text", "")
    as_voice = bool(msg.get("as_voice"))
    res = await pc_userbot.send_message(
        target, text, as_voice, gemini=_get_gemini() if as_voice else None
    )
    if res.get("ok"):
        note = res.get("error")
        return {"text": "✅ Отправлено" + (f" ({note})" if note else "")}
    return {"text": f"❌ Не отправлено: {res.get('error')}"}


async def _handle(ws, msg: dict):
    if msg.get("action") == "send_telegram":
        result = await _handle_userbot(msg)
    else:
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

    delay = _RECONNECT_MIN_SEC
    fails = 0          # consecutive failures
    last_reason = ""   # to avoid logging the same line thousands of times

    while True:
        try:
            async with websockets.connect(
                uri, ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024
            ) as ws:
                if fails:
                    logger.info(f"Связь восстановлена после {fails} неудачных попыток.")
                delay, fails, last_reason = _RECONNECT_MIN_SEC, 0, ""
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
            fails += 1
            reason = str(e)
            # The server can be down for weeks (dead deploy, quota). Back off so
            # we neither hammer it nor write 46k identical lines into the log:
            # report a new reason at once, a repeated one every _LOG_EVERY tries.
            # Первый обрыв живой сессии — почти всегда не авария, а разрыв со
            # стороны инфраструктуры (HF рвёт WebSocket раз в ~10 минут).
            # Возвращаемся немедленно: каждая секунда здесь — секунда, когда
            # команда с телефона не доедет до компьютера.
            sleep_for = 0.0 if fails == 1 else \
                delay * (1 + random.uniform(-_RECONNECT_JITTER, _RECONNECT_JITTER))
            if reason != last_reason or fails % _LOG_EVERY == 0:
                last_reason = reason
                logger.warning(
                    f"Отключено: {reason}. Попытка №{fails}, "
                    f"следующая через {round(sleep_for)} с…"
                )
            await asyncio.sleep(sleep_for)
            delay = min(delay * _RECONNECT_FACTOR, _RECONNECT_MAX_SEC)


if __name__ == "__main__":
    # Python owns the log file so it can rotate it (start_pc.bat must NOT
    # redirect here too — an open handle blocks rotation on Windows).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                _LOG_PATH, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS,
                encoding="utf-8",
            ),
        ],
    )
    from telegram_bot.config import load as load_config
    cfg = load_config(require_bot=False)
    try:
        asyncio.run(run_client(cfg.pc_link_url, cfg.pc_link_token))
    except KeyboardInterrupt:
        logger.info("PC client stopped.")
