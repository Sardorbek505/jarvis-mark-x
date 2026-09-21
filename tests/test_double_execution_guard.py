"""Ложное пробуждение между расшифровками не должно исполнять команду дважды.

Живой прогон 17.09.2026, «Джарвис, поставь фильм Железный человек»:

    10:49:32 LocalSTT: «джарвис поставь фильм железный человек вторую часть»
    10:49:32 Fast-Path: 🎬 Запуск фильма           ← первый запуск
    10:49:32 Wake Word: [NN CONFIRMED]             ← ложное срабатывание
    10:49:33 Арбитраж реплики: 'Чарльз, поставь фильм "Железный человек…"'
    10:49:33 Fast-Path: 🎬 Запуск фильма           ← второй запуск того же
    10:49:34 VK launch aborted before fullscreen

Локальная расшифровка готова через ~0.1 с, облачная — на 3–5 с позже. От
повторного исполнения защищал флаг `arbitrated`, но он живёт на объекте хода,
а `_on_wake_spotted` пересоздаёт объект при каждом пробуждении.
"""

import asyncio
from unittest.mock import patch

import pytest

from core.headless_ui import HeadlessUI
from main import Jarvis


@pytest.fixture
def jarvis_app():
    ui = HeadlessUI()
    with patch("core.hotkey_manager.GlobalHotkeyManager"), \
         patch("core.wake_detector.WakeWordDetector2Stage"), \
         patch("main.Jarvis._start_telegram_bot", return_value=None):
        j = Jarvis(ui)
        j.audio_in_queue = asyncio.Queue()
        j.out_queue = asyncio.Queue()
        return j


def test_wake_word_does_not_resurrect_an_executed_turn(jarvis_app):
    """Ложное пробуждение после локального исполнения не воскрешает ход."""
    j = jarvis_app
    j._begin_new_utterance()

    j._mark_cloud_turn_settled()      # команда исполнена по локальной расшифровке
    j._on_wake_spotted()              # ложное срабатывание подменило объект хода

    assert j._pending_gemini_turn.arbitrated is False, "ход действительно подменён"
    assert j._consume_settled_cloud_turn() is True, "расшифровка исполнилась бы второй раз"


def test_each_settled_turn_is_consumed_only_once(jarvis_app):
    """Запись гасит ровно один ход — следующая настоящая команда пройдёт."""
    j = jarvis_app

    j._mark_cloud_turn_settled()

    assert j._consume_settled_cloud_turn() is True
    assert j._consume_settled_cloud_turn() is False, "погашен лишний ход — команда потеряна"


def test_two_local_commands_settle_two_turns(jarvis_app):
    """Две быстрые команды подряд — гасятся обе их расшифровки, не больше."""
    j = jarvis_app

    j._mark_cloud_turn_settled()
    j._mark_cloud_turn_settled()

    assert [j._consume_settled_cloud_turn() for _ in range(3)] == [True, True, False]


def test_untouched_turn_is_arbitrated_normally(jarvis_app):
    """Без локального исполнения гасить нечего — обычная реплика идёт в арбитраж."""
    assert jarvis_app._consume_settled_cloud_turn() is False


def test_stale_record_expires_instead_of_eating_a_command(jarvis_app, monkeypatch):
    """Ход, который так и не закрылся, не должен глушить будущую команду."""
    import main as main_mod

    j = jarvis_app
    clock = [1_000.0]
    monkeypatch.setattr(main_mod.time, "monotonic", lambda: clock[0])

    j._mark_cloud_turn_settled()
    clock[0] += j.CLOUD_TURN_SETTLED_TTL_SEC + 1.0

    assert j._consume_settled_cloud_turn() is False


def test_record_still_valid_inside_ttl(jarvis_app, monkeypatch):
    """Расшифровка Gemini приходит через 3–5 с — запись обязана дожить."""
    import main as main_mod

    j = jarvis_app
    clock = [1_000.0]
    monkeypatch.setattr(main_mod.time, "monotonic", lambda: clock[0])

    j._mark_cloud_turn_settled()
    clock[0] += 5.0

    assert j._consume_settled_cloud_turn() is True


def test_reconnect_forgets_turns_of_the_dead_session(jarvis_app):
    """Ходы оборванной сессии не закроются — записи надо забыть."""
    j = jarvis_app
    j._mark_cloud_turn_settled()
    j._mark_cloud_turn_settled()

    j._forget_settled_cloud_turns()

    assert j._consume_settled_cloud_turn() is False


# ── Сценарий целиком: локальная расшифровка, ложное пробуждение, облачная ────

@pytest.mark.asyncio
async def test_false_wake_between_transcripts_does_not_replay_the_command(jarvis_app):
    """Тот самый случай 17.09: фильм не должен запускаться второй раз."""
    j = jarvis_app
    j._loop = asyncio.get_running_loop()
    j._begin_new_utterance()
    j._pending_gemini_turn.addressed = True
    j._wake_active_until = 9e9  # шлюз открыт: реплика адресована Джарвису

    launches = []

    def _fake_fast(text, player=None, **kwargs):
        launches.append(text)
        from core.fast_command_router import ExecutionStatus, FastCommandResult
        return FastCommandResult(
            handled=True, text="Включаю", is_action=True, status=ExecutionStatus.SUCCESS
        )

    with patch("core.fast_command_router.FastCommandRouter.match_and_execute", side_effect=_fake_fast):
        # 1. Локальная расшифровка Vosk — команда исполняется
        assert await j._arbitrate_turn(
            "джарвис поставь фильм железный человек", [], [], local_only=True
        ) is True
        assert len(launches) == 1

        # 2. Ложное срабатывание слова подменяет объект хода
        j._on_wake_spotted()
        assert j._pending_gemini_turn.arbitrated is False

        # 3. Облачная расшифровка той же реплики приходит через 3-5 с
        handled = await j._arbitrate_turn(
            'Чарльз, поставь фильм "Железный человек, вторую часть".', [], []
        )

    assert handled is True, "ход Gemini должен быть погашен"
    assert len(launches) == 1, f"фильм запущен {len(launches)} раза вместо одного"
    assert j._pending_gemini_turn.routed_to == "DISCARDED"


@pytest.mark.asyncio
async def test_next_real_command_still_reaches_the_router(jarvis_app):
    """Гашение одноразовое: следующая настоящая команда обязана исполниться."""
    j = jarvis_app
    j._loop = asyncio.get_running_loop()
    j._begin_new_utterance()
    j._pending_gemini_turn.addressed = True
    j._wake_active_until = 9e9

    launches = []

    def _fake_fast(text, player=None, **kwargs):
        launches.append(text)
        from core.fast_command_router import ExecutionStatus, FastCommandResult
        return FastCommandResult(
            handled=True, text="Готово", is_action=True, status=ExecutionStatus.SUCCESS
        )

    with patch("core.fast_command_router.FastCommandRouter.match_and_execute", side_effect=_fake_fast):
        await j._arbitrate_turn("джарвис пауза", [], [], local_only=True)
        j._on_wake_spotted()
        await j._arbitrate_turn("Джарвис, пауза.", [], [])          # эхо — гасится
        assert len(launches) == 1

        j._begin_new_utterance()
        j._pending_gemini_turn.addressed = True
        j._wake_active_until = 9e9
        await j._arbitrate_turn("Джарвис, сделай громче.", [], [])  # новая команда

    assert len(launches) == 2, "вторая команда потеряна вместе с эхом"
