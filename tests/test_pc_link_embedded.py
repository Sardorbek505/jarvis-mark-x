"""Мост «ПК ↔ сервер бота» внутри Джарвиса: установленный JARVIS.exe был для
телефона «офлайн» — мост жил только отдельным процессом из папки с исходниками."""
import socket
from types import SimpleNamespace

import pytest

from telegram_bot import pc_server as ps


_orig_claim = ps._claim_singleton


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Свой свободный порт-замок: не драться с настоящим мостом на 47821."""
    monkeypatch.setattr(ps, "_EMBEDDED", False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setattr(ps, "_claim_singleton", lambda: _orig_claim(port))
    return port


CFG = SimpleNamespace(pc_link_url="https://jarvis.hf.space", pc_link_token="секрет-связи")


def test_bridge_starts_inside_jarvis(monkeypatch, _fresh):
    called = {}

    async def fake_run(url, token):
        called.update(url=url, token=token)
    monkeypatch.setattr(ps, "run_client", fake_run)
    t = ps.start_in_background(CFG)
    assert t is not None
    t.join(2)
    assert called == {"url": CFG.pc_link_url, "token": CFG.pc_link_token}
    assert "уже запущен" in ps._launch_jarvis()["text"]           # «Запустить Джарвиса» — он и так работает


def test_no_second_bridge_when_separate_client_runs(monkeypatch, _fresh):
    busy = _orig_claim(_fresh)                                       # отдельный pc_server держит замок
    try:
        monkeypatch.setattr(ps, "run_client", lambda *a: pytest.fail("второй мост"))
        assert ps.start_in_background(CFG) is None and not ps._EMBEDDED
    finally:
        busy.close()


def test_without_link_settings_bridge_is_off(monkeypatch):
    monkeypatch.setattr(ps, "run_client", lambda *a: pytest.fail("без настроек не звоним"))
    assert ps.start_in_background(SimpleNamespace(pc_link_url="https://x.hf.space", pc_link_token="")) is None
    assert ps.start_in_background(SimpleNamespace(pc_link_url="", pc_link_token="t")) is None


def test_jarvis_starts_bridge_unless_disabled(monkeypatch):
    import main
    got = []
    monkeypatch.setattr(ps, "start_in_background", lambda: got.append(1) or "поток")
    monkeypatch.setenv("JARVIS_PC_LINK", "1")
    assert main.Jarvis._start_pc_link(None) == "поток"
    monkeypatch.setenv("JARVIS_PC_LINK", "0")
    assert main.Jarvis._start_pc_link(None) is None and got == [1]


def test_bridge_really_connects_and_answers(monkeypatch):
    """Настоящий WebSocket: мост в фоновом потоке подключается к /pc-link с токеном
    и отвечает на команду с телефона."""
    import json
    import threading

    import websockets
    from websockets.sync.server import serve

    got = {}
    done = threading.Event()

    def handler(ws):
        got["path"] = ws.request.path
        ws.send(json.dumps({"type": "command", "req_id": "r1", "text": "ping-test"}))
        for raw in ws:
            got["reply"] = json.loads(raw)
            done.set()
            break

    async def fake_execute(text):
        return {"text": f"понял: {text}", "image_b64": None}

    monkeypatch.setattr(ps, "_execute", fake_execute)
    with serve(handler, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        t = ps.start_in_background(SimpleNamespace(pc_link_url=f"http://127.0.0.1:{port}",
                                                   pc_link_token="секрет"))
        assert t is not None
        assert done.wait(10), "мост не подключился / не ответил"
        server.shutdown()
    assert got["path"].startswith("/pc-link?token=")
    assert got["reply"]["req_id"] == "r1" and got["reply"]["text"] == "понял: ping-test"
    assert websockets
