"""Уведомления о ПК: без ленты онлайн/офлайн на каждом мигании связи."""

import asyncio

import pytest

from telegram_bot.pc_notice import PCStatusNotifier


def _стенд(offline_after=0.05):
    отправлено = []
    состояние = {"on": False}

    async def notify(text):
        отправлено.append(text)
    n = PCStatusNotifier(notify, lambda: состояние["on"], offline_after=offline_after)
    return n, отправлено, состояние


@pytest.mark.asyncio
async def test_подключение_после_перезапуска_бота_молчит():
    n, отправлено, _ = _стенд()
    await n.on_change(True)
    assert отправлено == []


@pytest.mark.asyncio
async def test_короткий_разрыв_не_виден():
    n, отправлено, состояние = _стенд()
    await n.on_change(False)
    await asyncio.sleep(0.01)
    состояние["on"] = True
    await n.on_change(True)
    await asyncio.sleep(0.1)
    assert отправлено == []


@pytest.mark.asyncio
async def test_долгая_пропажа_и_возвращение_сообщаются_по_разу():
    n, отправлено, состояние = _стенд()
    await n.on_change(False)
    await n.on_change(False)          # повторный разрыв не заводит второй отсчёт
    await asyncio.sleep(0.1)
    assert len(отправлено) == 1 and "офлайн" in отправлено[0]

    состояние["on"] = True
    await n.on_change(True)
    assert len(отправлено) == 2 and "снова онлайн" in отправлено[1]

    await n.on_change(True)           # ещё одно подключение — тишина
    assert len(отправлено) == 2
