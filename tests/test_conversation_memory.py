"""Память разговора: Джарвис не теряет нить после переподключения и
перезапуска, сам собирает факты о человеке, умеет вспоминать и забывать."""
import json
import time

import pytest

import memory.conversation as conv
import memory.memory_manager as mm


@pytest.fixture(autouse=True)
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "_MEMORY_FILE", tmp_path / "data.json")
    monkeypatch.setattr(conv, "DIALOG_FILE", tmp_path / "dialog.jsonl")
    monkeypatch.setattr(conv, "EPISODES_FILE", tmp_path / "episodes.jsonl")
    monkeypatch.setattr(conv, "_collector", None)
    return tmp_path


def test_new_session_gets_the_conversation_tail():
    now = time.time()
    conv.log_turn("user", "Джарвис, у меня завтра экзамен по физике", ts=now - 120)
    conv.log_turn("jarvis", "Удачи, сэр. Повторим формулы?", ts=now - 118)
    conv.log_turn("user", "[СИСТЕМА: служебное]", ts=now - 60)       # не пишется
    conv.log_turn("user", "Да, давай с кинематики", ts=now - 30)
    fresh = conv.prompt_context(resuming=False)
    assert "[НЕДАВНИЙ РАЗГОВОР" in fresh and "экзамен по физике" in fresh and "кинематики" in fresh
    assert "служебное" not in fresh
    assert fresh.index("экзамен") < fresh.index("кинематики")        # по порядку
    # Сессию возобновили — сервер помнит сам, хвост не дублируем.
    assert "НЕДАВНИЙ" not in conv.prompt_context(resuming=True)


def test_tail_is_bounded_and_old_talk_is_left_to_summaries():
    now = time.time()
    conv.log_turn("user", "очень старое", ts=now - 2 * 86400)
    for i in range(60):
        conv.log_turn("user", f"реплика {i} " + "слово " * 30, ts=now - 600 + i)
    out = conv.format_recent(now)
    assert "очень старое" not in out
    assert len(out) <= conv.RECENT_CHARS + 200 and "реплика 59" in out


def _facts(**kw):
    return {"facts": [], "remove": [], "summary": "", **kw}


def test_collector_extracts_facts_and_summary_after_conversation():
    now = [time.time()]
    seen = []

    def llm(prompt):
        seen.append(prompt)
        return _facts(facts=[{"category": "identity", "key": "имя", "value": "Сардор"},
                             {"category": "relationships", "key": "брат", "value": "Азиз, учится в Ташкенте"},
                             {"category": "weird", "key": "любимая игра", "value": "CS2"}],
                      summary="Сардор рассказал о брате Азизе и любимой игре.")
    c = conv.Collector(llm=llm, clock=lambda: now[0], state_file=conv.DIALOG_FILE.parent / "st.json")
    conv.log_turn("user", "Меня зовут Сардор, брат Азиз учится в Ташкенте", ts=now[0])
    conv.log_turn("jarvis", "Приятно познакомиться, Сардор.", ts=now[0] + 1)
    assert c.run_once() == "нечего"                     # разговор ещё идёт
    now[0] += conv.IDLE_SEC + 5
    assert c.run_once() == "+3 −0"
    assert "Меня зовут Сардор" in seen[0] and "(пока ничего)" in seen[0]
    mem = mm.load_memory()
    assert mem["identity"]["имя"]["value"] == "Сардор"
    assert "Азиз" in mem["relationships"]["брат"]["value"]
    assert mem["notes"]["любимая_игра"]["value"] == "CS2"   # неизвестная категория → заметки
    assert "брате Азизе" in conv.format_episodes()
    assert c.run_once() == "нечего"                     # второй раз то же не разбираем
    c2 = conv.Collector(llm=llm, clock=lambda: now[0], state_file=conv.DIALOG_FILE.parent / "st.json")
    assert c2.run_once() == "нечего"                    # и после перезапуска тоже


def test_outdated_fact_is_replaced():
    mm.update_memory({"identity": {"город": "Ташкент"}})
    conv.apply_extraction(_facts(remove=[{"category": "identity", "key": "город"}],
                                 facts=[{"category": "identity", "key": "город", "value": "Самарканд"}]))
    assert mm.load_memory()["identity"]["город"]["value"] == "Самарканд"


def test_collector_survives_bad_llm_and_keeps_turns_for_later():
    now = time.time()
    conv.log_turn("user", "Я люблю плов", ts=now - 1000)

    def broken(prompt):
        raise RuntimeError("квота")
    c = conv.Collector(llm=broken, clock=time.time, state_file=conv.DIALOG_FILE.parent / "st.json")
    assert c.run_once().startswith("ошибка")
    c.llm = lambda p: _facts(facts=[{"category": "preferences", "key": "еда", "value": "плов"}])
    assert c.run_once() == "+1 −0"                     # не потеряли


def test_recall_and_forget():
    mm.update_memory({"relationships": {"брат": "Азиз"}, "preferences": {"музыка": "Macan, Miyagi"}})
    conv.add_episode("Обсуждали поездку в Самарканд на выходных.")
    conv.log_turn("user", "напомни, что я обещал купить маме лекарство")
    assert "Азиз" in conv.recall("брат")
    assert "Самарканд" in conv.recall("поездка в Самарканд")
    assert "лекарство" in conv.recall("лекарство маме")
    everything = conv.recall("")
    assert "Азиз" in everything and "Macan" in everything
    assert "музыка" in conv.forget_about("музыка")
    assert "Macan" not in conv.recall("")
    assert "нет фактов" in conv.forget_about("космос")


def test_broken_line_does_not_kill_the_log():
    conv.log_turn("user", "первая")
    with open(conv.DIALOG_FILE, "a", encoding="utf-8") as f:
        f.write("{битая строка\n")
    conv.log_turn("user", "вторая")
    assert [r["text"] for r in conv._read_jsonl(conv.DIALOG_FILE)] == ["первая", "вторая"]


def test_extract_prompt_lists_known_facts():
    p = conv.build_extract_prompt([("identity", "имя", "Сардор")], [{"role": "user", "text": "привет", "ts": 1}])
    assert "identity/имя: Сардор" in p and "Вы: привет" in p and "health" in p
    json.loads('{"facts": []}')

def test_quota_error_pauses_instead_of_hammering_gemini():
    """В живом логе 111 одинаковых предупреждений подряд: разбор падал с 429,
    done_ts не двигался, и сборщик каждые 20 с снова бил в исчерпанную квоту.
    Gemini сам сообщает, через сколько повторять, — ждём столько."""
    now = [time.time()]
    calls = []

    def quota(prompt):
        calls.append(1)
        raise RuntimeError("429 RESOURCE_EXHAUSTED ... Please retry in 24.57893355s.")

    conv.log_turn("user", "Я люблю плов", ts=now[0] - 1000)
    c = conv.Collector(llm=quota, clock=lambda: now[0],
                       state_file=conv.DIALOG_FILE.parent / "st.json")

    assert c.run_once().startswith("ошибка")
    assert len(calls) == 1

    assert c.run_once() == "ждём квоту"          # второй раз Gemini не трогаем
    assert len(calls) == 1

    now[0] += 30                                  # пауза, которую назвал Gemini, прошла
    c.llm = lambda p: _facts(facts=[{"category": "preferences", "key": "еда", "value": "плов"}])
    assert c.run_once() == "+1 −0"                # реплики не потеряны


def test_ordinary_error_is_retried_at_once():
    """Пауза — только на исчерпанную квоту: обычный сбой надо пробовать снова."""
    now = time.time()
    conv.log_turn("user", "Я люблю плов", ts=now - 1000)
    c = conv.Collector(llm=lambda p: (_ for _ in ()).throw(RuntimeError("сеть моргнула")),
                       clock=time.time, state_file=conv.DIALOG_FILE.parent / "st.json")

    assert c.run_once().startswith("ошибка")
    c.llm = lambda p: _facts(facts=[{"category": "preferences", "key": "еда", "value": "плов"}])
    assert c.run_once() == "+1 −0"


def test_number_429_in_an_unrelated_error_is_not_a_quota():
    """«429» само по себе ничего не значит: оно бывает в id, счётчиках и
    времени. Принять такое за квоту — тихо остановить память на минуту."""
    assert conv._quota_pause(RuntimeError("не разобрал 429 реплик")) == 0.0
    assert conv._quota_pause(RuntimeError("spotify:track:429abc не найден")) == 0.0
    assert conv._quota_pause(RuntimeError("таймаут через 4290 мс")) == 0.0


def test_real_quota_refusals_are_recognised():
    assert conv._quota_pause(RuntimeError("429 RESOURCE_EXHAUSTED. quota")) > 0
    assert conv._quota_pause(RuntimeError("RESOURCE_EXHAUSTED")) > 0
    assert conv._quota_pause(RuntimeError("429 Too Many Requests")) > 0
    assert conv._quota_pause(RuntimeError("429: You exceeded your current quota")) > 0
