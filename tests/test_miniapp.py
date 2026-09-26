"""Мобильный Джарвис (Telegram Mini App): сервер и само приложение.

Сервер — через настоящий FastAPI и MemoryStore на SQLite: приложение
открывается с историей, переписка сохраняется (она же — общая память с ПК),
в сводке видно, что Джарвис знает, и неверный факт можно убрать.

Приложение — в настоящем Chrome/Chromium (если есть) с подменённым
WebSocket: грузится без ошибок, шар рисуется, фигура меняется по теме,
вкладки открываются, без связи сообщение ждёт в очереди."""
import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from core import browser_cdp as cdp
from telegram_bot import miniapp_server as ms

APP = Path(__file__).resolve().parent.parent / "telegram_bot" / "miniapp"
UID = 7


@pytest.fixture
def client(mem, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(ms, "_memory", mem)
    monkeypatch.setattr(ms, "_bridge", None)
    monkeypatch.setattr(ms, "_cfg", lambda: type("C", (), {"allowed_user_ids": [UID], "telegram_token": "t"})())
    monkeypatch.setattr(ms, "verify_init_data", lambda data, token: UID if data == "ok" else None)

    class Gemini:
        async def chat(self, uid, text):
            return "Готово, сэр. [[SEND]]брат | привет[[/SEND]]"

    monkeypatch.setattr(ms, "_gemini", Gemini())
    return TestClient(ms.app)


def _drain(ws, until):
    for _ in range(20):
        m = ws.receive_json()
        if m["type"] == until:
            return m
    raise AssertionError(f"не дождался {until}")


async def test_chat_is_saved_and_comes_back_on_reopen(client, mem):
    with client.websocket_connect("/ws?init_data=ok") as ws:
        assert _drain(ws, "history")["messages"] == []
        ws.send_json({"type": "text", "text": "Запомни, я люблю плов", "tts": False})
        reply = _drain(ws, "text")
        assert reply["text"] == "Готово, сэр."                    # служебный блок не показан
    hist = await mem.recent_messages(UID, 10)
    assert [(m["role"], m["text"]) for m in hist] == [("user", "Запомни, я люблю плов"), ("model", "Готово, сэр.")]
    with client.websocket_connect("/ws?init_data=ok") as ws:  # открыли снова — разговор на месте
        msgs = _drain(ws, "history")["messages"]
    assert msgs == [{"role": "user", "text": "Запомни, я люблю плов"}, {"role": "bot", "text": "Готово, сэр."}]


def test_server_serves_every_file_the_page_loads(client):
    """Сервер отдаёт файлы по списку маршрутов: забытый маршрут = 404 в бою
    (так чуть не потерялся orb.js)."""
    import re
    html = client.get("/").text
    local = [u.split("?")[0] for u in re.findall(r'(?:src|href)="([^"]+)"', html) if "://" not in u]
    assert "orb.js" in local and "app.js" in local
    for path in local + ["worklet.js"]:
        r = client.get("/" + path)
        assert r.status_code == 200 and r.content, path
        assert r.content == (APP / path).read_bytes(), path


def test_stranger_is_rejected(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?init_data=forged") as ws:
            ws.receive_json()


async def test_dashboard_shows_memory_and_fact_can_be_forgotten(client, mem):
    await mem.add_fact(UID, "брат: Азиз")
    await mem.add_fact(UID, "любит плов")
    await mem.add_pc_message(UID, "pc_user", "у меня завтра экзамен")
    await mem.add_pc_message(UID, "pc_episode", "Готовились к экзамену.")
    with client.websocket_connect("/ws?init_data=ok") as ws:
        ws.send_json({"type": "get_data", "view": "dashboard"})
        p = _drain(ws, "data")["payload"]
        assert p["about"]["facts"] == ["любит плов", "брат: Азиз"]   # новые сверху
        assert p["pc_voice"] == [{"who": "Вы", "text": "у меня завтра экзамен"}]
        assert p["pc_episodes"] == ["Готовились к экзамену."]
        ws.send_json({"type": "fact_delete", "text": "брат: Азиз"})
        p = _drain(ws, "data")["payload"]
        assert p["about"]["facts"] == ["любит плов"]
    assert await mem.get_facts(UID) == ["любит плов"]


# ── само приложение в браузере ──────────────────────────────────────────────

def test_js_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node")
    for f in ("app.js", "orb.js", "worklet.js"):
        subprocess.run([node, "--check", str(APP / f)], check=True)


_FAKE_WS = r"""
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(String(e.message)));
window.__sent = [];
window.__online = true;
class FakeWS {
  constructor() {
    this.readyState = 0; window.__ws = this;
    setTimeout(() => {
      if (!window.__online) { this.readyState = 3; this.onclose && this.onclose(); return; }
      this.readyState = 1; this.onopen && this.onopen();
      this._emit({ type: 'pc_status', online: true });
      this._emit({ type: 'history', messages: [{ role: 'user', text: 'Привет' }, { role: 'bot', text: '**Здравствуйте**, сэр' }] });
    }, 30);
  }
  _emit(o) { this.onmessage && this.onmessage({ data: JSON.stringify(o) }); }
  send(s) {
    const m = JSON.parse(s); window.__sent.push(m);
    if (m.type === 'text') { this._emit({ type: 'thinking' }); setTimeout(() => { this._emit({ type: 'text', text: 'В Ташкенте +18' }); this._emit({ type: 'tts_failed' }); }, 50); }
    if (m.type === 'get_data') this._emit({ type: 'data', view: m.view, payload: { name: 'Сардор', about: { facts: ['брат: Азиз'] }, tasks: [], reminders: [], habits: [] } });
  }
  close() { this.readyState = 3; }
}
FakeWS.OPEN = 1; FakeWS.CLOSING = 2;
window.WebSocket = FakeWS;
window.Telegram = undefined;
"""


def _chrome():
    for c in (os.getenv("JARVIS_BROWSER"), shutil.which("google-chrome"), shutil.which("chromium"),
              "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"):
        if c and os.path.isfile(c):
            return c
    return None


@pytest.fixture
def page(tmp_path, monkeypatch):
    if not _chrome():
        pytest.skip("нет Chrome/Chromium")
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(APP), **k)  # noqa: E731
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(cdp, "PORT", 9900 + os.getpid() % 90)
    monkeypatch.setattr(cdp, "_tab", None)
    monkeypatch.setattr(cdp, "_profile_dir", lambda: str(tmp_path / "profile"))
    monkeypatch.setenv("JARVIS_BROWSER", _chrome())
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    monkeypatch.setenv("JARVIS_BROWSER_ARGS", "--headless=new --mute-audio --window-size=390,844"
                       + (" --no-sandbox" if root else ""))
    try:
        assert cdp.ensure_browser(), cdp.browser_log()[-2000:]
        t = cdp.tab()
        t.call("Page.enable")
        t.call("Page.addScriptToEvaluateOnNewDocument", source=_FAKE_WS)
        # telegram-web-app.js снаружи недоступен в CI — не ждём его
        t.call("Network.enable")
        t.call("Network.setBlockedURLs", urls=["*telegram.org*"])
        t.navigate(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
        assert t.wait("document.readyState === 'complete' && !!window.JarvisOrb", timeout=15)
        yield t
    finally:
        try:
            if cdp._proc:
                cdp._proc.terminate()
        finally:
            srv.shutdown()


def _js(t, expr):
    return t.eval(f"JSON.stringify({expr})")


def test_app_loads_draws_orb_and_changes_shape(page):
    t = page
    assert t.wait("document.querySelectorAll('#messages .msg').length >= 2", timeout=5)
    assert json.loads(_js(t, "window.__errors")) == []
    assert t.eval("document.querySelector('#messages .msg.bot b').textContent") == "Здравствуйте"
    # шар действительно рисуется: на холсте есть непрозрачные точки
    lit = t.eval("""(() => { const c = document.getElementById('orb-canvas');
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 60) n++; return n; })()""")
    assert lit > 500
    t.eval("say('Какая погода в Ташкенте?')")
    assert t.eval("orbView.orb.shape") == "globe"                  # фигура по теме запроса
    assert t.wait("[...document.querySelectorAll('#messages .msg.bot')].some(m => m.textContent.includes('+18'))", timeout=5)
    sent = json.loads(_js(t, "window.__sent.filter(m => m.type === 'text')"))
    assert sent[-1]["text"] == "Какая погода в Ташкенте?"


def test_tabs_open_and_memory_card_shows(page):
    t = page
    t.eval("switchTab('dashboard')")
    assert t.wait("document.getElementById('dash-body').textContent.includes('брат: Азиз')", timeout=5)
    for tab in ("tasks", "habits", "pc", "chat"):
        t.eval(f"switchTab('{tab}')")
        assert t.eval(f"document.getElementById('view-{tab}').classList.contains('active')")
    assert t.eval("!document.getElementById('view-pc').classList.contains('offline')")   # ПК онлайн
    assert json.loads(_js(t, "window.__errors")) == []


def test_offline_message_waits_in_queue(page):
    t = page
    assert t.wait("document.querySelectorAll('#messages .msg').length >= 2", timeout=5)
    t.eval("window.__online = false; window.__ws.readyState = 3; window.__ws.onclose()")   # связь пропала
    t.eval("say('Напомни купить хлеб')")
    assert t.eval("document.querySelector('#messages .msg.user.pending') !== null")
    assert t.eval("document.getElementById('state-pill').textContent.includes('НЕТ СВЯЗИ')")
    t.eval("window.__online = true; connect()")                        # вернулась
    assert t.wait("window.__sent.some(m => m.text === 'Напомни купить хлеб')", timeout=5)
    assert t.eval("document.querySelector('#messages .msg.user.pending') === null")
