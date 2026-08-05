"""Маршрутизация команд ПК: ключ ловится по началу слова, не подстрокой.

Регрессия 20.06.2026: «чиСТОПлотный» содержал «стоп», личное сообщение ушло
на компьютер, и Джарвис ответил «🖥 Готово» вместо разговора.
"""
from telegram_bot import keywords

PC_WORDS = ["стоп", "включи", "камер", "скриншот", "открой", "погод"]


def test_keyword_inside_another_word_is_not_a_command():
    # Arrange / Act / Assert — тот самый случай из переписки
    text = "Высокий накаченный обеспеченный и чистоплотный и в машине мерседес"
    assert keywords.matches(text, PC_WORDS) is False


def test_real_commands_still_match():
    for text in ("стоп", "Стоп музыку", "включи музыку", "сделай скриншот",
                 "открой браузер", "какая погода"):
        assert keywords.matches(text, PC_WORDS) is True, text


def test_russian_endings_still_match():
    # морфология: ключ-префикс обязан ловить формы слова
    for text in ("покажи камеру", "фото с камерой", "погода на завтра"):
        assert keywords.matches(text, PC_WORDS) is True, text


def test_other_inner_matches_are_rejected():
    # слово содержит ключ, но не начинается с него
    for text in ("нескучный вечер", "прескриншотный", "заоткрой"):
        assert keywords.matches(text, PC_WORDS) is False, text


def test_empty_text_is_not_a_command():
    assert keywords.matches("", PC_WORDS) is False


# ── снимок с ПК должен доезжать картинкой ────────────────────────────────────

import asyncio
import base64
import sys
import types


class _Msg:
    """Минимальный двойник telegram.Message — фиксирует, чем ответили."""
    def __init__(self):
        self.photos, self.texts = [], []

    async def reply_photo(self, photo, caption=""):
        self.photos.append((photo, caption))

    async def reply_text(self, text, **kw):
        self.texts.append(text)


def _load_reply_helper():
    """Достаёт _reply_pc_result из bot.py без импорта тяжёлых зависимостей.

    Через разбор синтаксиса, а не поиск по соседним именам: первая версия
    вырезала кусок «от _reply_pc_result до _try_pc» и развалилась, как только
    _try_pc удалили как мёртвый код.
    """
    import ast
    src = open("telegram_bot/bot.py", encoding="utf-8").read()
    tree = ast.parse(src)
    mod = types.ModuleType("_helper")
    mod.__dict__.update({"base64": base64, "logger": types.SimpleNamespace(warning=lambda *a: None)})
    # Забираем функцию вместе с её помощниками: одну штуку тащить нельзя —
    # _reply_pc_result зовёт _prefixed, и без него получаем NameError.
    wanted = {"_reply_pc_result", "_prefixed"}
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in wanted:
            exec(compile(ast.get_source_segment(src, node), "bot_helper", "exec"), mod.__dict__)
    return mod._reply_pc_result


def test_screenshot_comes_back_as_a_picture():
    # Arrange — ПК прислал снимок вместе с текстом
    reply = _load_reply_helper()
    msg = _Msg()
    png = base64.b64encode(b"\x89PNG_fake").decode()

    # Act
    asyncio.run(reply(msg, {"text": "Скриншот ✅", "image_b64": png}))

    # Assert — картинка, а не только «🖥 Скриншот ✅»
    assert len(msg.photos) == 1
    assert msg.photos[0][0] == b"\x89PNG_fake"
    assert not msg.texts


def test_plain_answer_stays_text():
    reply = _load_reply_helper()
    msg = _Msg()
    asyncio.run(reply(msg, {"text": "Громкость 50"}))
    assert msg.texts == ["🖥 Громкость 50"] and not msg.photos


def test_no_answer_from_pc_is_reported():
    reply = _load_reply_helper()
    msg = _Msg()
    asyncio.run(reply(msg, None))
    assert msg.texts and "не ответил" in msg.texts[0]


# ── просьба ответить голосом ─────────────────────────────────────────────────

VOICE_WORDS = ["голосом", "вслух", "озвучь", "войсом", "voice"]


def test_voice_request_is_recognised():
    for text in ("отвечай мне голосом", "скажи это вслух", "озвучь ответ",
                 "ответь войсом", "reply with voice"):
        assert keywords.matches(text, VOICE_WORDS) is True, text


def test_ordinary_message_is_not_a_voice_request():
    for text in ("какая погода", "запиши голосовые связки в заметки",
                 "что такое голография"):
        assert keywords.matches(text, VOICE_WORDS) is False, text


def test_pc_icon_is_not_doubled():
    # Arrange — ПК уже прислал свой значок
    reply = _load_reply_helper()
    msg = _Msg()
    asyncio.run(reply(msg, {"text": "🖥 Состояние системы: CPU 39%"}))
    assert msg.texts == ["🖥 Состояние системы: CPU 39%"]   # без второго 🖥


def test_plain_text_still_gets_an_icon():
    reply = _load_reply_helper()
    msg = _Msg()
    asyncio.run(reply(msg, {"text": "Громкость 50"}))
    assert msg.texts == ["🖥 Громкость 50"]
