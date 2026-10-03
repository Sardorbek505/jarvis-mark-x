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
    (403, '{"error": {"message": "Gemini API has not been used in project 1", "status": "PERMISSION_DENIED"}}',
     "bad", "выключен Gemini API"),
    (500, "", "warn", "ошибкой 500"),                       # сбой Google — не «ключ неверный»
])
def test_gemini(monkeypatch, code, body, state, text):
    monkeypatch.setattr(K, "_http", Net({"generativelanguage": (code, body)}))
    st, msg = K.check_gemini({"gemini_api_key": GOOD_GEMINI})
    assert st == state and text in msg


def test_gemini_shape_checked_before_network(monkeypatch):
    net = Net({})
    monkeypatch.setattr(K, "_http", net)
    assert K.check_gemini({"gemini_api_key": "sk-123"})[0] == "bad" and net.calls == []


class Seen(Net):
    def __call__(self, url, headers=None, data=None, method=None):
        self.headers = headers or {}
        return super().__call__(url, headers, data, method)


def test_gemini_new_format_key_goes_to_google(monkeypatch):
    """Ключ не вида «AIza…» (новый формат, ~53 символа) раньше отбивался без запроса к Google."""
    net = Seen({"generativelanguage": (200, "{}")})
    monkeypatch.setattr(K, "_http", net)
    key = "AQ.Ab8RN6" + "k" * 44
    assert K.check_gemini({"gemini_api_key": " " + key[:20] + "\u200b\n" + key[20:] + " "}) == ("ok", "Ключ работает.")
    assert net.headers == {"x-goog-api-key": key} and key not in net.calls[0]     # ключ не в адресе


def test_http_sends_own_user_agent(monkeypatch):
    """Cloudflare (Groq) отбивает «Python-urllib» кодом 403 — верный ключ выглядел неверным."""
    got = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_open(req, timeout):
        got.update(req.header_items())
        return Resp()
    monkeypatch.setattr(K.urllib.request, "urlopen", fake_open)
    assert K._http("https://api.groq.com/openai/v1/models", {"Authorization": "Bearer x"}) == (200, "{}")
    assert got["User-agent"].startswith("JARVIS") and got["Authorization"] == "Bearer x"


@pytest.mark.parametrize("code,body,state", [
    (200, "{}", "ok"),
    (401, '{"error": {"code": "invalid_api_key"}}', "bad"),
    (403, "error code: 1010", "warn"),                      # защита сайта, не ключ
    (429, "", "warn"),
])
def test_groq_answers(monkeypatch, code, body, state):
    monkeypatch.setattr(K, "_http", Net({"groq": (code, body)}))
    assert K.check_groq({"groq_api_key": "gsk_" + "x" * 52 + "\n"})[0] == state


def test_keys_saved_without_copy_junk(monkeypatch):
    saved = {}
    monkeypatch.setattr("core.paths.save_api_keys", lambda d: saved.update(d) or True)
    K.save_values({"gemini_api_key": " AIza\u200bSyX \n", "pc_link_token": "мой секрет 1"})
    assert saved == {"gemini_api_key": "AIzaSyX", "pc_link_token": "мой секрет 1"}


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


def test_key_preview_and_wrong_service_hint():
    assert K.preview("AIzaSyD1234567890abcdefghijklmnopqQ7xF") == "AIza••••••Q7xF"
    assert K.kind_of("sk-proj-abcdefghijklmnopqrstuvwx") == "OpenAI"
    assert K.kind_of("sk-ant-api03-abcdefghijklmnopqrstu") == "Anthropic"
    assert "OpenAI" in K.wrong_key_hint("gemini_api_key", "sk-proj-abcdefghijklmnopqrstuvwx")
    assert "AIza" in K.wrong_key_hint("gemini_api_key", "sk-proj-abcdefghijklmnopqrstuvwx")
    assert K.wrong_key_hint("gemini_api_key", GOOD_GEMINI) == ""
    assert K.wrong_key_hint("fish_api_key", "sk-proj-abcdefghijklmnopqrstuvwx") == ""   # вид не однозначный


def test_wrong_key_is_not_saved_and_field_says_why(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    store = {"gemini_api_key": ""}
    monkeypatch.setattr(K, "load_values", lambda: dict(store))
    monkeypatch.setattr(K, "save_values", lambda d: store.update(d) or True)
    monkeypatch.setattr(K, "check", lambda sid, v: ("ok", "Ключ работает."))
    import ui_keys
    dlg = ui_keys.KeysDialog()
    try:
        card = dlg.cards["gemini"]
        assert card.chips["gemini_api_key"].text() == "Нет ключа"
        card.edits["gemini_api_key"].setText("sk-proj-abcdefghijklmnopqrstuvwx")
        card.autosave.flush()
        assert store["gemini_api_key"] == ""                                 # чужой ключ не сохранён
        assert card.chips["gemini_api_key"].text() == "Не тот ключ"
        assert "OpenAI" in card.notes["gemini_api_key"].text()
        card.edits["gemini_api_key"].setText(GOOD_GEMINI)
        card.autosave.flush()
        for _ in range(100):
            app.processEvents()
            if card.state == "ok":
                break
            time.sleep(0.01)
        assert store["gemini_api_key"] == GOOD_GEMINI
        assert card.chips["gemini_api_key"].text() == "✓ Работает"
        assert card.notes["gemini_api_key"].text() == "Сохранён: " + K.preview(GOOD_GEMINI)
    finally:
        dlg.close()
