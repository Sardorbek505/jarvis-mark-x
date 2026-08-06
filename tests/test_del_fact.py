"""Удаление одного факта должно удалять его насовсем.

Досье пополняется автоматически, и ошибочный факт туда попасть может —
измерено на живой модели. Убрать его точечно было нечем: /forget стирал
всю память целиком. И одного DELETE из facts мало: тот же текст лежит в
эмбеддингах, откуда поиск по смыслу вернул бы его прямо в промпт.
"""
import pytest

from telegram_bot import memory_rag

UID = 555_000_011


@pytest.mark.asyncio
async def test_удаляет_названный_факт(mem):
    for f in ("Возраст: 21", "Работает в BEK STYLE", "Есть брат"):
        await mem.add_fact(UID, f)

    gone = await mem.del_fact(UID, 2)

    assert gone == "Работает в BEK STYLE"
    assert await mem.get_facts(UID) == ["Возраст: 21", "Есть брат"]


@pytest.mark.asyncio
async def test_нумерация_с_единицы(mem):
    await mem.add_fact(UID, "первый")
    await mem.add_fact(UID, "второй")
    assert await mem.del_fact(UID, 1) == "первый"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, 99])
async def test_чужой_номер_ничего_не_трогает(mem, bad):
    await mem.add_fact(UID, "единственный")
    assert await mem.del_fact(UID, bad) == ""
    assert await mem.get_facts(UID) == ["единственный"]


@pytest.mark.asyncio
async def test_удалённый_факт_не_возвращается_поиском(mem):
    """Эмбеддинг с тем же текстом должен уйти вместе с фактом."""
    fact = "Работает в BEK STYLE"
    await mem.add_fact(UID, fact)
    await mem.add_embedding(UID, "fact", fact, [0.1] * 8)
    assert await mem.has_embedding(UID, fact)

    await mem.del_fact(UID, 1)

    assert not await mem.has_embedding(UID, fact)
    assert all(r["text"] != fact for r in await mem.all_embeddings(UID))


@pytest.mark.asyncio
async def test_кэш_векторов_сбрасывается(mem):
    """Иначе стёртое всплывёт из памяти процесса, а не из базы."""
    fact = "Любит джаз"
    await mem.add_fact(UID, fact)
    await mem.add_embedding(UID, "fact", fact, [0.2] * 8)
    memory_rag._VECS[UID] = await mem.all_embeddings(UID)   # прогрели кэш

    await mem.del_fact(UID, 1)

    assert UID not in memory_rag._VECS
