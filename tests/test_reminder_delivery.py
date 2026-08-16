"""Доставка напоминаний: одна на оба входа, с правильным порядком операций.

Цикл существовал двумя копиями — в bot.py и render_app.py. Совпадали строка
в строку, но в этом проекте пути уже расходились именно так (ради чего
появился context_builder), и чинить пришлось бы дважды.

Порядок здесь важнее вида: отметка «доставлено» ставится ПОСЛЕ отправки.
Сегодня на хостинге моргал DNS — упавшая отправка не должна терять
напоминание молча.
"""
import asyncio
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_bot import reminders as rem

LOG = logging.getLogger("test-reminders")


class _Bot:
    def __init__(self, fail_times=0):
        self.sent = []
        self.attempts = 0          # сколько раз цикл вообще пытался отправить
        self._fail = fail_times

    async def send_message(self, chat_id=None, text=None):
        self.attempts += 1
        if self._fail > 0:
            self._fail -= 1
            raise RuntimeError("Temporary failure in name resolution")
        self.sent.append((chat_id, text))


class _Mem:
    def __init__(self, due, mark_fails=False):
        self._due = list(due)
        self.marked = []
        self._mark_fails = mark_fails

    async def get_due_reminders(self, now_iso):
        return [r for r in self._due if r["id"] not in self.marked]

    async def mark_reminder_sent(self, rid):
        if self._mark_fails:
            raise RuntimeError("база недоступна")
        self.marked.append(rid)


async def _one_pass(bot, mem, fails=False, attempts=1, budget=5.0):
    """Крутим цикл, пока он не сделает `attempts` попыток отправки.

    Раньше здесь стоял `sleep(0.05)` при шаге цикла 0.01 — тест верил, что за
    50 мс успеет пройти нужное число оборотов. На занятой машине оборотов
    выходило меньше, и проверка повтора после сбоя падала на ровном месте.
    Ждём не время, а факт: столько попыток, сколько нужно проверке.
    """
    task = asyncio.create_task(rem.delivery_loop(bot, mem, LOG, every=0.01))
    deadline = asyncio.get_event_loop().time() + budget
    while bot.attempts < attempts and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_напоминание_доходит_и_отмечается():
    bot = _Bot()
    mem = _Mem([{"id": 1, "user_id": 7, "text": "позвонить маме"}])
    await _one_pass(bot, mem)
    assert bot.sent and "позвонить маме" in bot.sent[0][1]
    assert mem.marked == [1]


@pytest.mark.asyncio
async def test_упавшая_отправка_не_отмечает_доставленным():
    """Иначе сбой сети тихо съедает напоминание навсегда."""
    bot = _Bot(fail_times=99)
    mem = _Mem([{"id": 1, "user_id": 7, "text": "позвонить маме"}])
    await _one_pass(bot, mem)
    assert bot.sent == []
    assert mem.marked == [], "напоминание должно остаться в очереди"


@pytest.mark.asyncio
async def test_повтор_после_разового_сбоя():
    bot = _Bot(fail_times=1)
    mem = _Mem([{"id": 1, "user_id": 7, "text": "позвонить маме"}])
    await _one_pass(bot, mem, attempts=2)   # первая падает, вторая должна дойти
    assert bot.sent, "второй оборот должен доставить"
    assert mem.marked == [1]


@pytest.mark.asyncio
async def test_сбой_отметки_виден_в_логе(caplog):
    """Доставлено, но не отмечено — иначе то же придёт снова через полминуты."""
    bot = _Bot()
    mem = _Mem([{"id": 1, "user_id": 7, "text": "позвонить маме"}], mark_fails=True)
    with caplog.at_level(logging.ERROR):
        await _one_pass(bot, mem)
    assert bot.sent
    assert any("mark sent" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_цикл_снимается_по_отмене():
    bot, mem = _Bot(), _Mem([])
    task = asyncio.create_task(rem.delivery_loop(bot, mem, LOG, every=0.01))
    await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.sleep(0.02)
    assert task.done()
