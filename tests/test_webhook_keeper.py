"""scripts/webhook_keeper.py: вебхук — на сервер пользователя и с тем же
секретом, что ждёт render_app.py.

Было: без *.hf.space в конфиге вебхук ставился на Space автора, а секретом
шёл токен без «:» — сервер такой давно не принимает и отвечал 403 на всё.
"""
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("webhook_keeper", ROOT / "scripts" / "webhook_keeper.py")
wk = importlib.util.module_from_spec(_spec)
sys.modules["webhook_keeper"] = wk
_spec.loader.exec_module(wk)


@pytest.mark.parametrize("cfg, expected", [
    ({"miniapp_url": "https://me-jarvis.hf.space/app"}, "https://me-jarvis.hf.space/telegram-webhook"),
    ({"pc_link_url": "wss://my-bot.duckdns.org/pc-link"}, "https://my-bot.duckdns.org/telegram-webhook"),
    ({"miniapp_url": "", "pc_link_url": "wss://vps.example.com/pc-link"},
     "https://vps.example.com/telegram-webhook"),
])
def test_адрес_вебхука_с_сервера_пользователя(cfg, expected):
    assert wk.webhook_url(cfg) == expected


def test_без_адреса_не_подставляет_чужой_сервер():
    with pytest.raises(SystemExit):
        wk.webhook_url({})


def test_секрет_как_у_сервера():
    token = "123456:ABC-def"
    assert wk.webhook_secret(token) == hashlib.sha256(b"jarvis-webhook:" + token.encode()).hexdigest()
    assert wk.webhook_secret(token, "my secret!") == "mysecret"
    # Формула та же, что в render_app.py (сам модуль тянет бота и сеть).
    assert 'hashlib.sha256(b"jarvis-webhook:" + cfg.telegram_token.encode())' in \
        (ROOT / "telegram_bot" / "render_app.py").read_text(encoding="utf-8")
