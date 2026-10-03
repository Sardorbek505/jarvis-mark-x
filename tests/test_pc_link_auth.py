"""Связь ПК ↔ сервер: токен не в логах, «+» в токене не ломает вход, заголовок принимается."""
import logging

from telegram_bot import miniapp_server as ms, pc_server


def test_log_mask_hides_pc_token_and_init_data():
    rec = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                            '%s - "WebSocket %s" [accepted]', ("1.2.3.4:5", "/pc-link?token=ab+c/d=&x=1"), None)
    ms._MaskSecrets().filter(rec)
    assert "ab+c" not in rec.getMessage() and "token=***" in rec.getMessage()
    rec = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "/ws?init_data=query_id%3DAAH", (), None)
    ms._MaskSecrets().filter(rec)
    assert "AAH" not in rec.getMessage()


def test_mask_is_installed_on_uvicorn_loggers():
    assert any(isinstance(f, ms._MaskSecrets) for f in logging.getLogger("uvicorn.error").filters)


def test_auth_header_matches_websockets_version(monkeypatch):
    monkeypatch.setattr(pc_server.websockets, "__version__", "13.1")
    assert pc_server._auth_header("t") == {"extra_headers": {"Authorization": "Bearer t"}}
    monkeypatch.setattr(pc_server.websockets, "__version__", "16.0")
    assert pc_server._auth_header("t") == {"additional_headers": {"Authorization": "Bearer t"}}


def test_token_with_plus_survives_the_query_and_header_wins(monkeypatch):
    from urllib.parse import quote

    from fastapi.testclient import TestClient
    from starlette.datastructures import QueryParams
    token = "ab+c/d=="
    assert QueryParams("token=" + quote(token, safe="")).get("token") == token
    monkeypatch.setattr(ms, "_pc_link_token", lambda: token)
    monkeypatch.setattr(ms, "_bridge", None)
    with TestClient(ms.app) as c:
        with c.websocket_connect("/pc-link", headers={"Authorization": f"Bearer {token}"}) as ws:
            ws.close()
        with c.websocket_connect("/pc-link?token=" + quote(token, safe="")) as ws:   # старый ПК
            ws.close()


def test_non_ascii_token_goes_only_in_query():
    assert pc_server._auth_header("секрет") == {}
