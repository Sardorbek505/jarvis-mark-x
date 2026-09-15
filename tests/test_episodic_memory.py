"""Unit tests for EpisodicMemory hybrid RAG and Fast-Path memory commands."""

from unittest.mock import patch
from core.episodic_memory import EpisodicMemory
from core.fast_command_router import FastCommandRouter


def test_episodic_memory_save_and_recall(tmp_path):
    # Тестируем сохранение факта
    save_res = EpisodicMemory.save_fact("любимый кофе — двойной эспрессо без сахара")
    assert "Запомнил" in save_res
    assert "двойной эспрессо" in save_res

    # Тестируем извлечение факта (гибридный поиск)
    recall_res = EpisodicMemory.recall("какой мой любимый кофе?")
    assert "двойной эспрессо" in recall_res


def test_episodic_memory_profile_summary():
    summary = EpisodicMemory.get_profile_summary()
    assert isinstance(summary, str)
    assert len(summary) > 10


def test_episodic_memory_deduplication():
    # Первое сохранение
    res1 = EpisodicMemory.save_fact("уникальный тестовый факт номер 77123")
    assert "Запомнил" in res1

    # Повторное сохранение того же факта
    res2 = EpisodicMemory.save_fact("уникальный тестовый факт номер 77123")
    assert "уже есть в памяти" in res2 or "Запомнил" in res2

    # Проверяем в БД, что запись не продублировалась
    conn = EpisodicMemory._get_db()
    assert conn is not None
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM facts WHERE fact = ?", ("уникальный тестовый факт номер 77123",))
    count = cur.fetchone()[0]
    conn.close()
    assert count == 1



def test_fast_router_save_fact():
    with patch("core.episodic_memory.EpisodicMemory.save_fact", return_value="Запомнил, сэр: «пароль от сейфа 4492».") as mock_save, \
         patch("core.fast_command_router._trigger_action_feedback"):

        handled, resp = FastCommandRouter.match_and_execute("Джарвис, запомни, что пароль от сейфа 4492")
        assert handled is True
        assert "4492" in resp
        mock_save.assert_called_once()


def test_fast_router_recall_fact():
    with patch("core.episodic_memory.EpisodicMemory.recall", return_value="Вот что я нашёл в вашей памяти, сэр:\n• пароль от сейфа: 4492") as mock_recall:

        handled, resp = FastCommandRouter.match_and_execute("вспомни пароль от сейфа")
        assert handled is True
        assert "4492" in resp
        mock_recall.assert_called_once()


def test_fast_router_what_do_you_know():
    with patch("core.episodic_memory.EpisodicMemory.get_profile_summary", return_value="Пользователь: Sardarbek\nСохранённые факты: ...") as mock_prof:

        handled, resp = FastCommandRouter.match_and_execute("что ты обо мне знаешь?")
        assert handled is True
        assert "Sardarbek" in resp
        mock_prof.assert_called_once()
