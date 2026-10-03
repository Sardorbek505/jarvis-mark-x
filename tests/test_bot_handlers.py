"""Polling (bot.py) и вебхук (render_app) отвечают на одни и те же команды,
и ни один режим не выполняет исправленные (edited) сообщения повторно."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram.ext import CommandHandler

from telegram_bot import bot as bot_mod


class _App:
    def __init__(self):
        self.handlers = []
        self.polling = None

    def add_handler(self, h):
        self.handlers.append(h)

    def run_polling(self, **kw):
        self.polling = kw


def test_every_menu_command_is_answered():
    app = _App()
    bot_mod.register_handlers(app)
    registered = {c for h in app.handlers if isinstance(h, CommandHandler) for c in h.commands}
    menu = {c.command for c in bot_mod._BOT_COMMANDS}
    assert menu <= registered, f"в меню, но без ответа: {sorted(menu - registered)}"


def test_polling_ignores_edited_messages(monkeypatch):
    app = _App()

    class _Builder:
        def __getattr__(self, name):
            return lambda *a, **kw: app if name == "build" else self

    monkeypatch.setattr(bot_mod, "ApplicationBuilder", _Builder)
    monkeypatch.setattr(bot_mod, "load_config", lambda **kw: None)
    bot_mod.main()
    assert app.polling["allowed_updates"] == ["message", "callback_query"]
    assert any(isinstance(h, CommandHandler) and "notes" in h.commands for h in app.handlers)


def test_webhook_uses_the_same_table():
    src = (Path(bot_mod.__file__).parent / "render_app.py").read_text(encoding="utf-8")
    assert "register_handlers(app)" in src and "CommandHandler(" not in src
