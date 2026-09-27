"""Ключи и подключения: проверка каждого сервиса настоящим запросом (здесь —
подменённым), ответы человеческими словами, маска, сохранение и окно."""
import json
import os
import time

import pytest

from core import keys as K


class Net:
    """Подмена _http: адрес → (код, тело); запоминает, куда ходили."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, headers=None, data=None, method=None):
        self.calls.append(url)
        for part, resp in self.routes.items():
            if part in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return 404, ""


GOOD_GEMINI = "AIzaSy" + "A" * 33


@pytest.mark.parametrize("code,body,state,text", [
    (200, "{}", "ok", "Ключ работает."),
    (400, "API_KEY_INVALID", "bad", "неверный"),
    (429, "RESOURCE_EXHAUSTED", "warn", "квота"),
    (403, "", "bad", "заблокирован"),
])
def test_gemini(monkeypatch, code, body, state, text):
    monkeypatch.setattr(K, "_http", Net({"generativelanguage": (code, body)}))
    st, msg = K.check_gemini({"gemini_api_key": GOOD_GEMINI})
    assert st == state and text in msg


def test_gemini_shape_checked_before_network(monkeypatch):
    net = Net({})
    monkeypatch.setattr(K, "_http", net)
    assert K.check_gemini({"gemini_api_key": "sk-123"})[0] == "bad" and net.calls == []


def test_no_network_is_a_warning_not_an_error(monkeypatch):
    monkeypatch.setattr(K, "_http", Net({"generativelanguage": OSError("offline")}))
    st, msg = K.check_gemini({"gemini_api_key": GOOD_GEMINI})
    assert st == "warn" and "нет связи" in msg


def test_fish(monkeypatch):
    monkeypatch.setattr(K, "_http", Net({"api-credit": (200, json.dumps({"credit": "12.5"}))}))
    assert K.check_fish({"fish_api_key": "f" * 32}) == ("ok", "Ключ работает. Баланс: 12.5.")
    monkeypatch.setattr(K, "_http", Net({"api-credit": (401, "")}))
    assert K.check_fish({"fish_api_key": "f" * 32})[0] == "bad"
    monkeypatch.setattr(K, "_http", Net({"api-credit": (404, ""), "/model/": (200, "{}")}))
    assert K.check_fish({"fish_api_key": "f" * 32})[0] == "ok"      # старый адрес — проверка голосом


def test_spotify(monkeypatch):
    from actions import spotify_premium
    monkeypatch.setattr(spotify_premium, "ready", lambda: False)
    v = {"spotify_client_id": "a" * 32, "spotify_client_secret": "b" * 32}
    monkeypatch.setattr(K, "_http", Net({"accounts.spotify.com": (200, "{}")}))
    st, msg = K.check_spotify(v)
    assert st == "warn" and "Войти в Spotify" in msg
    monkeypatch.setattr(spotify_premium, "ready", lambda: True)
    assert K.check_spotify(v)[0] == "ok"
    monkeypatch.setattr(K, "_http", Net({"accounts.spotify.com": (400, "invalid_client")}))
    assert K.check_spotify(v)[0] == "bad"
    assert K.check_spotify({"spotify_client_id": "short", "spotify_client_secret": "x"})[0] == "bad"


def test_telegram(monkeypatch):
    token = "123456789:" + "A" * 35
    monkeypatch.setattr(K, "_http", Net({"getMe": (200, json.dumps({"result": {"username": "jarvis_bot"}}))}))
    assert K.check_telegram({"telegram_bot_token": token, "telegram_allowed_users": "42"}) == \
        ("ok", "Бот @jarvis_bot работает.")
    st, msg = K.check_telegram({"telegram_bot_token": token, "telegram_allowed_users": ""})
    assert st == "warn" and "Telegram ID" in msg
    assert K.check_telegram({"telegram_bot_token": token, "telegram_allowed_users": "@me"})[0] == "bad"
    assert K.check_telegram({"telegram_bot_token": "bad"})[0] == "bad"


def test_caller(monkeypatch):
    from core import tg_call
    monkeypatch.setattr(tg_call, "ready", lambda: "Аккаунт не подключён")
    v = {"telethon_api_id": "123456", "telethon_api_hash": "0123456789abcdef0123456789abcdef"}
    st, msg = K.check_caller(v)
    assert st == "warn" and "Войти по QR" in msg
    monkeypatch.setattr(tg_call, "ready", lambda: "")
    assert K.check_caller(v)[0] == "ok"
    assert K.check_caller({"telethon_api_id": "abc", "telethon_api_hash": ""})[0] == "bad"


def test_pc_link_and_groq(monkeypatch):
    monkeypatch.setattr(K, "_http", Net({"hf.space": (200, "ok"), "groq": (401, "")}))
    assert K.check_pc_link({"pc_link_url": "wss://me.hf.space", "pc_link_token": "long-secret-123"})[0] == "ok"
    assert K.check_pc_link({"pc_link_url": "me.hf.space", "pc_link_token": "long-secret-123"})[0] == "bad"
    assert K.check_groq({"groq_api_key": "gsk_" + "x" * 20})[0] == "bad"


def test_status_partial_missing_and_check_all(monkeypatch):
    s = K.BY_ID["spotify"]
    assert K.status(s, {}) == "missing"
    assert K.status(s, {"spotify_client_id": "a" * 32}) == "partial"
    assert K.check("spotify", {"spotify_client_id": "a" * 32}) == ("bad", "Не хватает: Client Secret.")
    assert K.check("gemini", {})[0] == "missing"
    monkeypatch.setattr(K, "_http", Net({"generativelanguage": (200, "{}")}))
    res = K.check_all({"gemini_api_key": GOOD_GEMINI})
    assert set(res) == {"gemini"} and res["gemini"][0] == "ok"      # незаданные необязательные — не трогаем


def test_every_service_is_described():
    for s in K.SERVICES:
        assert s.title and s.gives and s.steps and s.fields and s.check


def test_mask_and_save_never_log_secrets(monkeypatch, caplog):
    assert K.mask("") == "" and K.mask("12345") == "••••" and K.mask("AIzaSyABCDEF1234") == "••••1234"
    saved = {}
    import core.paths
    monkeypatch.setattr(core.paths, "save_api_keys", lambda d: saved.update(d) or True)
    caplog.set_level("INFO")
    secret = "gsk_supersecretvalue9876"
    assert K.save_values({"groq_api_key": secret, "telegram_allowed_users": "42, 7",
                          "telethon_api_id": "123456"})
    assert saved == {"groq_api_key": secret, "telegram_allowed_users": [42, 7], "telethon_api_id": 123456}
    assert secret not in caplog.text and "••••9876" in caplog.text


def test_keys_window(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QLineEdit
    app = QApplication.instance() or QApplication([])
    store = {"gemini_api_key": ""}
    monkeypatch.setattr(K, "load_values", lambda: dict(store))
    monkeypatch.setattr(K, "save_values", lambda d: store.update(d) or True)
    monkeypatch.setattr(K, "check", lambda sid, v: ("ok", "Ключ работает.") if v.get("gemini_api_key")
                        else ("missing", "Не задан."))
    import ui_keys
    dlg = ui_keys.KeysDialog()
    try:
        card = dlg.cards["gemini"]
        assert card.body.isVisible() or card.body.isVisibleTo(dlg)    # обязательный и пустой — открыт сразу
        assert "0 из" in dlg.summary.text()
        edit = card.edits["gemini_api_key"]
        assert edit.echoMode() == QLineEdit.EchoMode.Password          # ключ скрыт
        edit.setText(GOOD_GEMINI)
        card.save_and_check()
        for _ in range(200):
            app.processEvents()
            if card.state == "ok":
                break
            time.sleep(0.01)
        assert store["gemini_api_key"] == GOOD_GEMINI and card.state == "ok"
        assert "Работает" in card.pill.text() and card.msg.text() == "Ключ работает."
        assert "1 из" in dlg.summary.text()
        assert not dlg.cards["fish"].body.isVisibleTo(dlg)             # необязательные — свёрнуты
        dlg.repaint()
    finally:
        dlg.close()
