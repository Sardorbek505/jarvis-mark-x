"""Capture the real Telegram Mini App (telegram_bot/miniapp) for the JarvisAd video.

The page is the app's own HTML/JS/CSS. Its WebSocket is answered by this
script with payloads built by the bot server's own code
(telegram_bot.miniapp_server._build_view) over a throwaway SQLite memory store
seeded with demo data — and with the PC snapshot produced by core.pc_snapshot
from the same demo PC data, i.e. the real PC -> phone sync path.

Output: public/jarvis-ad/phone/<tab>.png at 393x852 CSS px, 3x (iPhone 15).
Run from video/:  python scripts/capture/capture_phone.py
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEO = HERE.parents[1]
OUT = VIDEO / "public" / "jarvis-ad" / "phone"
UID = 4242
CHROMIUM = os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium")

sys.path.insert(0, str(HERE))
from demo_data import DEMO_NOW, DEMO_TZ, SRC, env, seed, source_copy  # noqa: E402
from fonts import fonts_conf  # noqa: E402

CHAT = [
    ("user", "Джарвис, что у меня завтра?"),
    ("bot", "Завтра две пары: английский в 8:30 и физика в 10:10. И лабораторная по Python — сдать до среды."),
    ("user", "Напомни в 20:00 сесть за реферат"),
    ("bot", "Готово, сэр. В 20:00 напомню про реферат по рядам Фурье."),
]


async def build_payloads(tmp: Path) -> dict:
    from core import pc_snapshot
    from telegram_bot import memory_store, miniapp_server as srv, pc_views, user_context, weather

    memory_store._SQLITE_PATH = tmp / "memory.db"
    os.environ.pop("DATABASE_URL", None)
    store = memory_store.MemoryStore()
    await store.init()
    await store.ensure_loaded(UID)

    from zoneinfo import ZoneInfo
    tz = ZoneInfo(DEMO_TZ)
    user_context.local_now = lambda uid, default_tz=DEMO_TZ: DEMO_NOW.replace(tzinfo=tz)
    now = DEMO_NOW
    day = lambda d: (now + timedelta(days=d)).strftime("%Y-%m-%d")  # noqa: E731
    later = lambda h: (now + timedelta(hours=h)).replace(minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731
    for title, due in [("Лабораторная №3 — парсер", day(2)), ("Купить подарок маме", later(2)),
                       ("Созвон с командой проекта", later(4)), ("Записаться в спортзал", None)]:
        await store.add_task(UID, title, due)
    for title, done_days in [("Спорт", [0, 1, 2, 3, 4]), ("Чтение 20 минут", [0, 1, 2]),
                             ("2 литра воды", [1, 2, 3]), ("Английский 15 минут", [0, 1, 2, 3, 4, 5, 6])]:
        h = await store.add_habit(UID, title)
        for d in done_days:
            await store.toggle_habit(UID, h["id"], day(-d))
    for text, days, hm in [("Сесть за реферат", 0, (20, 0)), ("Позвонить маме", 1, (18, 30))]:
        local = (now + timedelta(days=days)).replace(hour=hm[0], minute=hm[1], tzinfo=tz)
        due = local.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        await store.add_reminder(UID, text, due)
    await store.set_profile_field(UID, "name", "Алекс")
    user_context.update(UID, city="Ташкент")
    snaps = {k: v[1] for k, v in pc_snapshot.collect().items()}
    for snap in snaps.values():  # "synced from PC" stamp at demo time, not capture time
        snap["at"] = (DEMO_NOW - timedelta(minutes=2)).isoformat(timespec="seconds")
    await pc_views.store_snapshots(store, UID, snaps)

    async def fake_weather(city):  # no network in captures
        return "+24°, ясно"

    class Bridge:
        connected = True

    weather.for_city = fake_weather
    srv._memory, srv._bridge = store, Bridge()
    views = {v: await srv._build_view(UID, v) for v in ("dashboard", "tasks", "study", "habits")}
    await store.close()

    from telegram_bot import pc_macros
    return {"views": views, "macros": pc_macros.list_items()}


def serve(root: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = lambda *a: http.server.SimpleHTTPRequestHandler(*a, directory=str(root))  # noqa: E731
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def shoot(data: dict) -> None:
    from playwright.sync_api import sync_playwright

    httpd, port = serve(SRC / "telegram_bot" / "miniapp")
    OUT.mkdir(parents=True, exist_ok=True)

    def on_ws(ws):
        def reply(raw):
            msg = json.loads(raw)
            if msg.get("type") == "get_data":
                v = msg.get("view", "dashboard")
                ws.send(json.dumps({"type": "data", "view": v, "payload": data["views"].get(v, {})}))
            elif msg.get("type") == "pc_macros":
                ws.send(json.dumps({"type": "pc_macros", "items": data["macros"]}))
        ws.on_message(reply)
        ws.send(json.dumps({"type": "pc_status", "online": True}))
        ws.send(json.dumps({"type": "history", "messages": [{"role": r, "text": t} for r, t in CHAT]}))

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM, args=["--disable-web-security"])
        ctx = browser.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=3,
                                  is_mobile=False, has_touch=True, locale="ru-RU", color_scheme="dark",
                                  timezone_id=DEMO_TZ)
        ctx.route("https://telegram.org/**", lambda r: r.fulfill(status=200, body="", content_type="text/javascript"))
        ctx.route("https://api.bigdatacloud.net/**", lambda r: r.abort())
        page = ctx.new_page()
        from zoneinfo import ZoneInfo
        page.clock.set_fixed_time(DEMO_NOW.replace(tzinfo=ZoneInfo(DEMO_TZ)))
        page.route_web_socket("**/ws*", on_ws)
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "chat.png"))
        print("  phone chat")
        for tab in ("dashboard", "tasks", "study", "habits", "pc"):
            page.click(f'button.tab[data-tab="{tab}"]')
            page.wait_for_timeout(2200)  # tab cascade + count-up settle
            page.screenshot(path=str(OUT / f"{tab}.png"))
            print("  phone", tab)
        browser.close()
    httpd.shutdown()


def run() -> None:
    seed()
    tmp = Path(os.environ["APPDATA"])
    data = asyncio.run(build_payloads(tmp))
    debug = VIDEO / ".cache" / "jarvis-ad" / "phone-payloads.json"  # what the page was fed, for checking
    debug.parent.mkdir(parents=True, exist_ok=True)
    debug.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    shoot(data)


if __name__ == "__main__":
    if os.environ.get("JARVIS_CAPTURE_CHILD") != "1":
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-phone-"))
        e = env(tmp, fonts_conf(VIDEO / ".cache" / "fonts"))
        e["JARVIS_CAPTURE_CHILD"] = "1"
        src = source_copy(tmp / "src")
        e["JARVIS_SRC"] = e["PYTHONPATH"] = str(src)
        (tmp / "appdata").mkdir()
        e["APPDATA"] = e["HOME"] = str(tmp / "appdata")
        subprocess.run([sys.executable, __file__], env=e, cwd=src, check=True)
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        run()
