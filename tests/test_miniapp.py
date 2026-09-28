"""Мобильный Джарвис (Telegram Mini App): сервер и само приложение.

Сервер — через настоящий FastAPI и MemoryStore на SQLite: приложение
открывается с историей, переписка сохраняется (она же — общая память с ПК),
в сводке видно, что Джарвис знает, и неверный факт можно убрать.

Приложение — в настоящем Chrome/Chromium (если есть) с подменённым
WebSocket: грузится без ошибок, шар рисуется, фигура меняется по теме,
вкладки открываются, без связи сообщение ждёт в очереди."""
import base64
import http.server
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
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
window.__study = { has: true, updated: '2026-09-28 08:00', now_next: 'Следующая — Матан в 08:30 (через 30 мин), ауд. 301.', parity: 'нечётная', has_lessons: true, pc_online: true,
  week: [0,1,2,3,4,5,6].map(i => ({ day: ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][i], date: (28 + i) + '.09', today: i === 0,
    lessons: i === 0 ? [{ start: '08:30', end: '10:00', subject: 'Матан', room: '301', kind: 'практика', teacher: '' }] : [] })),
  tasks: [{ id: 't1', title: 'старое', subject: '', kind: 'домашка', done: false, due: 'просрочено на 8 дней', group: 'overdue' },
          { id: 't2', title: 'реферат', subject: 'Матан', kind: 'домашка', done: false, due: 'послезавтра', group: 'week' }] };
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
    if (m.type === 'pc_macros') this._emit({ type: 'pc_macros', items: [{ name: 'Режим стрима', phrase: 'включи стрим', confirm: false }, { name: 'Выключить всё', phrase: '', when: 'по будням в 23:00', confirm: true }] });
    if (m.type === 'pc_macro') this._emit(m.name === 'Выключить всё' && !m.confirmed
      ? { type: 'pc_macro_result', name: m.name, need_confirm: true, ok: false, text: 'Выполнить «Выключить всё»?' }
      : { type: 'pc_macro_result', name: m.name, ok: true, text: '✅ «' + m.name + '» — выполнено' });
    if (m.type === 'get_data' && m.view === 'study') this._emit({ type: 'data', view: 'study', payload: window.__study });
    else if (m.type === 'get_data') this._emit({ type: 'data', view: m.view, payload: { name: 'Сардор', about: { facts: ['брат: Азиз'] }, tasks: [], reminders: [], habits: [],
      me: { has: true, known: 1, total: 2, groups: [{ title: 'Кто вы', questions: [{ key: 'name', label: 'Имя', value: 'Сардор' }, { key: 'city', label: 'Город', value: '' }] }] },
      calls: { has: true, calls: [{ who: 'Азиз', when: '2026-09-28 18:02', min: 1, result: 'Поговорили 1 мин.', lines: [{ who: 'Джарвис', text: 'Ужин готов' }, { who: 'Азиз', text: 'Иду, буду через 10 минут' }] }] },
      abilities: [{ title: 'Учёба', phrases: ['какие пары завтра'] }], football: window.__fb || { has: false }, pc_online: true } });
    if (m.type === 'football_watch') this._emit({ type: 'pc_edit_result', ok: true, text: 'Включил матч на Кинопоиске.' });
    if (m.type === 'study_done' || m.type === 'study_add' || m.type === 'about_answer') this._emit({ type: 'pc_edit_result', ok: true, text: 'Отметил' });
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


def test_pc_tab_shows_own_commands_and_confirms_locked_ones(page):
    t = page
    t.eval("switchTab('pc')")
    assert t.wait("document.querySelectorAll('#pc-macros .macro').length === 2", timeout=5)
    assert t.eval("document.querySelector('#pc-macros .macro small').textContent") == "«включи стрим»"
    t.eval("document.querySelectorAll('#pc-macros .macro')[0].click()")
    assert t.wait("[...document.querySelectorAll('#messages .msg.bot')].some(m => m.textContent.includes('Режим стрима» — выполнено'))", timeout=5)
    # команда с замком — сначала вопрос; «да» → тот же запуск с confirmed
    t.eval("window.confirm = () => true; document.querySelectorAll('#pc-macros .macro')[1].click()")
    assert t.wait("window.__sent.some(m => m.type === 'pc_macro' && m.name === 'Выключить всё' && m.confirmed)", timeout=5)
    assert json.loads(_js(t, "window.__errors")) == []


def test_study_tab_and_dashboard_cards_from_pc(page):
    """Учёба с ПК: «следующая пара», неделя, задачи по срокам с галочками.
    Сводка: «Обо мне» правится на месте, звонки раскрываются с расшифровкой,
    «Что умею» отправляет фразу Джарвису."""
    t = page
    t.eval("switchTab('study')")
    assert t.wait("document.querySelector('#study-body .now-next') !== null", timeout=5)
    assert t.eval("document.querySelectorAll('#study-body .day-chip').length") == 7
    assert t.eval("document.querySelector('#study-body .lesson .title').textContent") == "Матан"
    assert t.eval("document.querySelector('#study-body .section-label.warn').textContent").startswith("Просрочено")
    t.eval("document.querySelector('[data-day=\"1\"]').click()")
    assert "Пар нет" in t.eval("document.getElementById('study-body').textContent")
    t.eval("document.querySelector('[data-study-done=\"t2\"]').click()")
    assert t.wait("window.__sent.some(m => m.type === 'study_done' && m.id === 't2')", timeout=5)
    t.eval("document.getElementById('study-in').value = 'эссе'; document.getElementById('study-due').value = 'в пятницу'; studyAdd()")
    assert t.wait("window.__sent.some(m => m.type === 'study_add' && m.title === 'эссе' && m.due === 'в пятницу')", timeout=5)

    t.eval("switchTab('dashboard')")
    assert t.wait("document.querySelector('.me-row') !== null", timeout=5)
    t.eval("document.querySelector('[data-me=\"city\"]').click()")
    t.eval("const i = document.querySelector('.me-input'); i.value = 'Ташкент'; "
           "i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}))")
    assert t.wait("window.__sent.some(m => m.type === 'about_answer' && m.key === 'city' && m.value === 'Ташкент')", timeout=5)
    t.eval("document.querySelector('details.call summary').click()")
    assert "Иду, буду через 10 минут" in t.eval("document.querySelector('details.call').textContent")
    assert t.eval("document.querySelector('details.call').open")
    t.eval("document.querySelector('[data-try]').click()")
    assert t.wait("window.__sent.some(m => m.type === 'text' && m.text === 'какие пары завтра')", timeout=5)
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


_CREST = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'%3E"
          "%3Ccircle cx='5' cy='5' r='5' fill='%23ffcc00'/%3E%3C/svg%3E")


def _fb_payload(state="pre", hs="", as_="", detail="", minutes=95):
    when = (datetime.now().astimezone() + timedelta(minutes=minutes)).isoformat(timespec="minutes")
    m = {"id": "2", "when": when, "home": "Real Madrid", "away": "Barcelona", "hs": hs, "as": as_, "state": state,
         "detail": detail, "league": "LaLiga", "home_abbr": "RMA", "away_abbr": "BAR", "home_color": "ffffff",
         "away_color": "004d98", "home_logo": _CREST, "away_logo": "http://127.0.0.1:9/нет.png"}
    return {"has": True, "updated": "2026-09-28 20:55", "name": "Реал Мадрид", "team": "Real Madrid", "next": m,
            "results": [dict(m, id="1", away="Sevilla", hs="3", state="post", res="В", **{"as": "1"}),
                        dict(m, id="0", home="Getafe", away="Real Madrid", hs="1", state="post", res="Н", **{"as": "1"})],
            "upcoming": [dict(m, id="3", home="Liverpool", away="Real Madrid", league="UEFA Champions League")],
            "news": [{"title": "Реал подписал контракт с защитником", "link": "https://example.com/n1", "source": "Sports.ru"}]}


def test_football_card_on_phone(page):
    """Карточка клуба в «Сводке»: эмблемы (сломанная — буквами), отсчёт до
    матча, живой счёт с точкой LIVE, на гол — цифра подпрыгивает и салют,
    «Смотреть на ПК», форма В/Н/П, новости открываются."""
    t = page
    t.eval(f"window.__fb = {json.dumps(_fb_payload())}")
    t.eval("switchTab('dashboard')")
    assert t.wait("document.querySelector('.fb-match') !== null", timeout=5)
    assert t.eval("document.querySelector('.fb-count').textContent").startswith("через 1 ч 3")
    assert t.eval("document.querySelectorAll('.fb-res').length") == 2
    assert t.eval("[...document.querySelectorAll('.fb-res')].map(e => e.textContent).join('')") == "НВ"   # по порядку
    assert t.wait("document.querySelectorAll('.fb-row .fb-crest img').length === 1", timeout=5)          # сломанная ушла
    assert t.eval("getComputedStyle(document.querySelectorAll('.fb-row .fb-crest b')[1]).display") != "none"
    assert t.eval("document.querySelector('[data-fbwatch]')") is None      # до матча больше часа — кнопки нет
    shot = os.getenv("JARVIS_SHOT_DIR")
    if shot:
        Path(shot, "phone_pre.png").write_bytes(base64.b64decode(t.call("Page.captureScreenshot")["data"]))
    # матч идёт
    t.eval(f"window.__fb = {json.dumps(_fb_payload('in', '0', '0', '12' + chr(39), -12))}; "
           "send({type: 'get_data', view: 'dashboard'})")
    assert t.wait("document.querySelector('.fb-live') !== null", timeout=5)
    assert t.eval("document.querySelector('.fb-badge').textContent") == "LIVE"
    assert t.eval("document.querySelector('.fb-score span.pop')") is None
    # гол
    t.eval(f"window.__fb = {json.dumps(_fb_payload('in', '1', '0', '23' + chr(39), -23))}; "
           "send({type: 'get_data', view: 'dashboard'})")
    assert t.wait("document.querySelector('.fb-score span.pop') !== null", timeout=5)
    assert t.eval("document.querySelectorAll('.fb-confetti i').length") == 18
    assert t.eval("document.querySelector('.fb-goal').textContent") == "ГОЛ!"
    if shot:
        time.sleep(0.35)
        Path(shot, "phone_goal.png").write_bytes(base64.b64decode(t.call("Page.captureScreenshot")["data"]))
    t.eval("document.querySelector('[data-fbwatch]').click()")
    assert t.wait("window.__sent.some(m => m.type === 'football_watch')", timeout=5)
    t.eval("window.__opened = []; window.open = u => window.__opened.push(u); document.querySelector('[data-link]').click()")
    assert json.loads(_js(t, "window.__opened")) == ["https://example.com/n1"]
    assert json.loads(_js(t, "window.__errors")) == []


def test_tab_glider_and_cascade(page):
    """Подсветка вкладки переезжает к выбранной, карточки выплывают каскадом
    только при смене вкладки (обновления данных их не перезапускают)."""
    t = page
    t.eval("switchTab('dashboard')")
    assert t.wait("document.querySelector('#dash-body.enter') !== null", timeout=5)
    shot = os.getenv("JARVIS_SHOT_DIR")
    if shot:
        time.sleep(0.12)
        Path(shot, "phone_cascade.png").write_bytes(base64.b64decode(t.call("Page.captureScreenshot")["data"]))
    assert t.wait("(() => { const g = document.getElementById('tab-glider'), a = document.querySelector('.tab.active');"
                  " return g.getBoundingClientRect().left - a.getBoundingClientRect().left < 2; })()", timeout=3)
    assert t.wait("!document.querySelector('#dash-body.enter')", timeout=3)            # каскад закончился
    t.eval("send({type: 'get_data', view: 'dashboard'})")                               # обновление данных
    time.sleep(0.2)
    assert t.eval("document.querySelector('#dash-body.enter') === null")                # без повторного каскада
    assert json.loads(_js(t, "window.__errors")) == []
