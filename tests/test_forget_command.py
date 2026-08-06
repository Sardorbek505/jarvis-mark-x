"""Разбор аргумента /forget: цена ошибки — вся память пользователя.

`/forget <номер>` убирает один факт, `/forget все` стирает досье целиком и
необратимо. Перепутанные ветки означают либо «не могу удалить», либо
стёртую по ошибке память, поэтому разбор закреплён тестами.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_bot import bot as bot_mod


class _Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class _FakeMemory:
    def __init__(self, facts):
        self._facts = list(facts)
        self.cleared = False
        self.deleted = []

    async def get_facts(self, uid):
        return list(self._facts)

    async def del_fact(self, uid, index):
        if not 1 <= index <= len(self._facts):
            return ""
        gone = self._facts.pop(index - 1)
        self.deleted.append(gone)
        return gone

    async def clear(self, uid):
        self.cleared = True
        self._facts = []


@pytest.fixture
def wired(monkeypatch):
    mem = _FakeMemory(["Возраст: 21", "Работает в BEK STYLE", "Есть брат"])
    monkeypatch.setattr(bot_mod, "memory", mem)
    monkeypatch.setattr(bot_mod, "gemini",
                        SimpleNamespace(clear_history=lambda uid: None))
    monkeypatch.setattr(bot_mod, "_is_authorized", lambda update: True)
    msg = _Message()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1),
                             effective_message=msg)
    return mem, update, msg


async def _forget(update, args):
    await bot_mod.cmd_forget(update, SimpleNamespace(args=args))


@pytest.mark.asyncio
async def test_номер_убирает_один_факт(wired):
    mem, update, msg = wired
    await _forget(update, ["2"])
    assert mem.deleted == ["Работает в BEK STYLE"]
    assert mem.cleared is False
    assert "Работает в BEK STYLE" in msg.replies[0]


@pytest.mark.asyncio
async def test_несуществующий_номер_ничего_не_стирает(wired):
    mem, update, msg = wired
    await _forget(update, ["99"])
    assert mem.deleted == []
    assert mem.cleared is False
    assert "/facts" in msg.replies[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["все", "всё", "all", "ВСЕ"])
async def test_полное_стирание_только_явным_словом(wired, word):
    mem, update, _ = wired
    await _forget(update, [word])
    assert mem.cleared is True


@pytest.mark.asyncio
async def test_голое_forget_больше_не_стирает_память(wired):
    """Операция необратима — набрать её случайно было слишком легко."""
    mem, update, msg = wired
    await _forget(update, [])
    assert mem.cleared is False
    assert mem.deleted == []
    assert "/facts" in msg.replies[0]


@pytest.mark.asyncio
async def test_мусорный_аргумент_не_стирает_память(wired):
    mem, update, _ = wired
    await _forget(update, ["всё-таки", "не", "надо"])
    assert mem.cleared is False
    assert mem.deleted == []


@pytest.mark.asyncio
async def test_facts_нумерует_с_единицы(wired):
    _, update, msg = wired
    await bot_mod.cmd_facts(update, SimpleNamespace(args=[]))
    out = msg.replies[0]
    assert "1. Возраст: 21" in out
    assert "3. Есть брат" in out
