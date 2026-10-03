"""Одна память: что бот узнал в Telegram, лежит там же, где факты ПК.

Раньше факты бота жили отдельно (shared.json), шли в промпт вторым блоком
и не были видны в «Обо мне»; удалить их с ПК было нельзя."""
import asyncio

import memory.conversation as conv
import memory.memory_manager as mm
import memory.shared as shared
from telegram_bot import shared_memory

UID = 42


def _link(monkeypatch, store):
    monkeypatch.setattr(shared, "_config", lambda: ("https://bot.example", "t"))
    loop = asyncio.get_running_loop()

    def post(url, body, headers):
        return 200, asyncio.run_coroutine_threadsafe(shared_memory.apply_sync(store, UID, body), loop).result(20)
    return post


async def sync_(post):
    return await asyncio.to_thread(shared.sync_all, post=post)


def _outbox_ops():
    return shared._read(shared.OUTBOX_FILE, {}).get("ops", [])


async def test_bot_fact_lands_in_pc_memory_and_is_not_sent_back(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    await sync_(post)
    await mem.add_fact(UID, "город: Шымкент")
    await mem.add_fact(UID, "Любит гулять по вечерам")
    await sync_(post)
    facts = mm.all_facts()
    assert ("identity", "город", "Шымкент") in facts
    assert ("preferences", "Любит гулять по вечерам", "") in facts      # категория — по словам
    assert _outbox_ops() == []                               # обратно на сервер не ушло
    assert await mem.get_facts(UID) == ["город: Шымкент", "Любит гулять по вечерам"]


async def test_each_fact_reaches_prompt_once(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    mm.update_memory({"identity": {"имя": "Сардор"}})
    await sync_(post)
    await mem.add_fact(UID, "любимый клуб: Барселона")
    await sync_(post)
    prompt = mm.format_memory_for_prompt(mm.load_memory()) + conv.prompt_context(resuming=True)
    assert prompt.count("Сардор") == 1 and prompt.count("Барселона") == 1
    assert "ОБЩАЯ ПАМЯТЬ" not in prompt


async def test_deleted_in_bot_disappears_on_pc(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    for i in range(6):
        mm.update_memory({"identity": {f"факт_{i}": str(i)}})
    await mem.add_fact(UID, "собака: Рекс")
    await sync_(post)
    await sync_(post)
    assert any(k == "собака" and v == "Рекс" for _c, k, v in mm.all_facts())
    await mem.del_fact_text(UID, "собака: Рекс")
    await sync_(post)
    assert not any(k == "собака" for _c, k, _v in mm.all_facts())


async def test_forgotten_on_pc_is_gone_everywhere_and_does_not_return(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    await sync_(post)
    await mem.add_fact(UID, "Любит гулять по вечерам")
    await sync_(post)
    assert mm.forget("preferences", "Любит гулять по вечерам")
    await sync_(post)
    await sync_(post)
    assert await mem.get_facts(UID) == []
    assert mm.all_facts() == []


async def test_value_changed_in_bot_updates_pc_but_unsent_pc_change_wins(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    mm.update_memory({"identity": {"город": "Ташкент"}})
    await sync_(post)
    await mem.add_fact(UID, "город: Шымкент")                 # бот узнал новое значение
    await sync_(post)
    assert ("identity", "город", "Шымкент") in mm.all_facts()
    # своё изменение ещё в очереди — серверное старое его не перетирает
    monkeypatch.setattr(shared, "_post", lambda *a: (_ for _ in ()).throw(OSError("нет сети")))
    mm.update_memory({"identity": {"город": "Алматы"}})
    assert mm.put_from_server("город", "Шымкент", pending={"город"}) is False
    assert ("identity", "город", "Алматы") in mm.all_facts()
    await sync_(post)
    assert "город: Алматы" in await mem.get_facts(UID)


def test_category_is_guessed_by_key():
    assert mm.guess_category("брат") == "relationships"
    assert mm.guess_category("день рождения") == "dates"
    assert mm.guess_category("любимая музыка") == "preferences"
    assert mm.guess_category("что-то") == "notes"
    assert shared.split_fact("город: Шымкент") == ("город", "Шымкент")
    assert shared.split_fact("Был у врача. Сказал: всё хорошо") == ("Был у врача. Сказал: всё хорошо", "")
    assert shared.fact_text("Любит гулять", "") == "Любит гулять"


def test_user_profile_keeps_facts_in_memory_not_its_own_file(tmp_path):
    import json

    from core.user_profile import UserProfile
    (tmp_path / "config").mkdir()
    old = {"identity": {"name": "Сардор", "city": "Шымкент", "creator": "Sardarbek"},
           "preferences": {"favorite_music": "рэп", "music_genres": ["lo-fi", "рэп"], "break_duration": 15},
           "context": {"current_activity": None, "last_activity": None, "last_emotion": None,
                       "session_start": None, "interaction_count": 0},
           "history": {"recent_movies": [], "recent_music": [], "recent_commands": []}}
    (tmp_path / "config" / "user_profile.json").write_text(json.dumps(old), encoding="utf-8")
    up = UserProfile(tmp_path, memory=mm)                    # старый файл → память, один раз
    facts = mm.all_facts()
    assert ("identity", "name", "Сардор") in facts and ("identity", "city", "Шымкент") in facts
    assert ("preferences", "music_genres", "lo-fi, рэп") in facts
    saved = json.loads((tmp_path / "config" / "user_profile.json").read_text(encoding="utf-8"))
    assert saved["identity"]["name"] is None and saved["preferences"]["favorite_music"] is None
    assert saved["preferences"]["break_duration"] == 15     # настройка — не факт, остаётся

    up.update_preference("favorite_movie", "Интерстеллар")
    assert up.get_preference("favorite_movie") == "Интерстеллар"
    assert ("preferences", "favorite_movie", "Интерстеллар") in mm.all_facts()
    prompt = up.format_for_prompt()
    assert "Сардор" not in prompt and "Интерстеллар" not in prompt and "Sardarbek" in prompt


async def test_pending_delete_is_not_resurrected_by_server_copy(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    await sync_(post)
    await mem.add_fact(UID, "собака: Рекс")
    await sync_(post)
    assert mm.forget("relationships", "собака") or mm.forget("notes", "собака")
    # удаление ещё не дошло до сервера, а там старая копия
    shared._merge_server_facts(["собака: Рекс"], shared._read(shared.SHARED_FILE, {}))
    assert not any(k == "собака" for _c, k, _v in mm.all_facts())
    await sync_(post)
    assert await mem.get_facts(UID) == []
