"""JARVIS Mark X — Тесты гонок Gemini Live, арбитража владения и жизненного цикла команд.

Покрывает тестовую матрицу (Tests A through K):
  Test A: Double tool call prevention (Gemini tool_call + local router race)
  Test B: Multi-turn STT-only during COLLECTING_PARAMETERS
  Test C: Cancel during executor (late completion returns IGNORED without secondary TTS)
  Test D: New command during TTS
  Test E: Follow-up timeout (12s) vs Command TTL (60s)
  Test F: Delayed Gemini response (Gemini delayed output arrives after local action completed -> discarded)
  Test G: Natural phrasing variations (cardinals, ordinals, self-corrections, word order)
  Test H: Mixed cancel ("Не надо. Какая погода завтра?")
  Test I: Buffer isolation & memory limit (1MB cap, clear on route)
  Test J: Generation interruption & stale dropping
  Test K: Media player fallback does not duplicate browser windows if page_opened is True
"""

from unittest.mock import MagicMock, patch

import pytest

from actions.movie_player import MediaExecutionResult
from core.command_context import (
    CommandContext,
    CommandState,
    PendingGeminiTurn,
)
from core.command_orchestrator import CommandOrchestrator, RoutingDecision
from core.media_executor import MediaExecutor
from core.slot_filler import SlotFiller


# ── Test A: Double Tool Call Prevention ────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_gemini_tool_call_actually_executes():
    """
    Ход, который локальный контроллер НЕ забрал, принадлежит Gemini, и её
    tool_call должен реально исполниться. Раньше здесь стояла безусловная
    блокировка: модель получала «выполняется через системный контроллер», а
    действие не выполнял никто. Двойное исполнение отсекается в цикле приёма
    (ход с routed_to == "LOCAL" до _execute_tool не доходит).
    """
    from main import Jarvis

    with patch.object(Jarvis, "__init__", lambda self: None):
        jarvis = Jarvis()
        jarvis.ui = MagicMock()
        jarvis.ui.muted = False
        jarvis.user_profile = MagicMock()
        jarvis.proactive_engine = MagicMock()

        fc = MagicMock()
        fc.id = "call_123"
        fc.name = "movie_player"
        fc.args = {"action": "play", "title": "Интерстеллар"}

        with patch("main.movie_player", return_value="Включаю «Интерстеллар», сэр.") as mp:
            resp = await jarvis._execute_tool(fc)

        mp.assert_called_once()
        assert resp.id == "call_123"
        assert resp.response["result"] == "Включаю «Интерстеллар», сэр."


# ── Test B: Multi-Turn STT-Only during COLLECTING_PARAMETERS ──────────────────
def test_b_multi_turn_stt_only_during_collecting():
    """
    Проверяет, что во время сбора параметров (COLLECTING_PARAMETERS) оркестратор
    находится в активном состоянии сбора, и все внешние вызовы строго изолированы.
    """
    orch = CommandOrchestrator()
    mock_player = MagicMock()

    # Пользователь инициирует команду с недостающим слотом
    res1 = orch.process_user_text("Включи сериал", player=mock_player)
    assert res1.decision == RoutingDecision.CONSUMED_CLARIFICATION
    assert orch.has_active_command() is True
    assert orch.active_command.state == CommandState.COLLECTING_PARAMETERS
    assert orch.active_command.target_slot == "title"

    # Ответ пользователя на вопрос
    res2 = orch.process_user_text("Менталист", player=mock_player)
    assert res2.decision == RoutingDecision.CONSUMED_CLARIFICATION
    assert orch.active_command.target_slot == "season"

    # Ответ с сезоном
    with patch.object(MediaExecutor, "execute", return_value=(True, "Включаю сериал")):
        res3 = orch.process_user_text("второй", player=mock_player)
        assert res3.decision == RoutingDecision.CONSUMED_ACTION
        assert orch.has_active_command() is False


# ── Test C: Cancel during Executor (Late completion returns IGNORED) ───────────
def test_c_cancel_during_executor_silent_completion():
    """
    Проверяет: если пользователь отменил команду во время долгого исполнения,
    пользователю сообщается об отмене ровно один раз, а завершившийся позже
    исполнитель возвращает RoutingDecision.IGNORED без повторного TTS.
    """
    orch = CommandOrchestrator()
    mock_player = MagicMock()

    # Создаём готовую команду
    cmd = CommandContext(
        intent="play_media",
        slots={"title": "Интерстеллар", "media_type": "movie"},
        required_slots=["title"],
    )
    cmd.state = CommandState.EXECUTING
    orch._active_command = cmd
    orch._executing_command_id = cmd.id

    # Пользователь говорит "Отмена" во время выполнения
    cancel_res = orch.process_user_text("Отмена", player=mock_player)
    assert cancel_res.decision == RoutingDecision.CONSUMED_CANCEL
    assert cancel_res.prompt_to_user == "Отменено, сэр."
    assert orch.has_active_command() is False

    # Теперь имитируем, что долгое фоновое исполнение наконец завершилось
    # Оно вызывает _execute_command для отменённой команды
    late_res = orch._execute_command(cmd, player=mock_player)
    assert late_res.decision == RoutingDecision.IGNORED
    assert late_res.prompt_to_user is None


# ── Test D: New Command during TTS ────────────────────────────────────────────
def test_d_new_command_during_tts():
    """
    Проверяет, что при поступлении новой команды счётчик turn_id и идентификаторы
    обновляются, а устаревший колбэк от предыдущей команды помечается как stale.
    """
    orch = CommandOrchestrator()

    # Шаг 1: первая команда
    res1 = orch.process_user_text("Включи сериал")
    old_cmd_id = res1.command_id
    old_turn_id = res1.turn_id
    assert orch.is_stale_callback(old_cmd_id, old_turn_id) is False

    # Шаг 2: пользователь прерывает новой командой "Открой блокнот"
    with patch("actions.open_app.open_app", return_value="Открыл блокнот, сэр."):
        res2 = orch.process_user_text("Открой блокнот")
        assert res2.decision == RoutingDecision.CONSUMED_ACTION
        assert res2.command_id != old_cmd_id

    # Колбэк от старой команды теперь признан устаревшим
    assert orch.is_stale_callback(old_cmd_id, old_turn_id) is True


# ── Test E: Follow-Up Window Timeout vs Command TTL ───────────────────────────
def test_e_followup_window_vs_command_ttl():
    """
    Проверяет разделение короткого follow-up окна микрофона (12 сек)
    и долгого Command TTL (60 сек).
    """
    orch = CommandOrchestrator()
    orch.process_user_text("Включи сериал")
    cmd = orch.active_command
    assert cmd is not None

    start_time = cmd.created_at

    # Прошло 15 секунд (follow-up микрофона истёк, но TTL ещё жив)
    assert orch.has_active_command(now=start_time + 15.0) is True

    # Прошло 45 секунд — команда всё ещё валидна
    assert orch.has_active_command(now=start_time + 45.0) is True

    # Прошло 65 секунд — TTL команды (60с) истёк
    assert orch.has_active_command(now=start_time + 65.0) is False


# ── Test F: Delayed Gemini Response Discarded ─────────────────────────────────
def test_f_delayed_gemini_response_discarded():
    """
    Проверяет, что PendingGeminiTurn после арбитража в LOCAL сбрасывает данные
    и помечается как routed_to='LOCAL', блокируя запоздалый вывод Gemini.
    """
    pt = PendingGeminiTurn(utterance_id="utt_1", generation_id=1)
    pt.add_audio(b"gemini_speech_chunk_1")
    pt.add_text("Сейчас включу...")

    assert len(pt.audio_chunks) == 1
    assert len(pt.output_text_chunks) == 1

    # Арбитраж принял решение LOCAL
    pt.arbitrated = True
    pt.routed_to = "LOCAL"
    pt.clear()

    assert len(pt.audio_chunks) == 0
    assert len(pt.output_text_chunks) == 0
    assert pt.routed_to == "LOCAL"


# ── Test G: Natural Phrasing Variations ───────────────────────────────────────
@pytest.mark.parametrize(
    "phrase,expected_title,expected_season,expected_platform",
    [
        ("Менталист первый сезон", "Менталист", 1, None),
        ("Менталист сезон один", "Менталист", 1, None),
        ("первый сезон Менталиста", "Менталист", 1, None),
        ("Менталист на ютубе первый сезон", "Менталист", 1, "youtube"),
        ("на кинопоиске второй сезон Интерстеллара", "Интерстеллар", 2, "kinopoisk"),
        ("хочу посмотреть сериал Шерлок третий сезон", "Шерлок", 3, None),
    ],
)
def test_g_natural_phrasing_variations(phrase, expected_title, expected_season, expected_platform):
    """
    Проверяет корректное извлечение слотов из естественных вариаций порядка слов.
    """
    # Проверка через контекстное извлечение для title
    slots = SlotFiller.extract_contextual_slots(phrase, target_slot="title")
    assert slots.get("title") == expected_title
    if expected_season:
        assert slots.get("season") == expected_season
    if expected_platform:
        assert slots.get("platform") == expected_platform


def test_g_corrections_and_cardinals():
    """
    Проверяет обработку исправлений («не первый, второй») и заминок («Менталист... хотя нет, Шерлок»).
    """
    # 1. Исправление числительного
    ord1 = SlotFiller.parse_ordinal("не первый, второй")
    assert ord1 == 2

    ord2 = SlotFiller.parse_ordinal("не 1, а 3")
    assert ord2 == 3

    ord3 = SlotFiller.parse_ordinal("сезон один")
    assert ord3 == 1

    # 2. Исправление названия
    slots = SlotFiller.extract_contextual_slots("Менталист... хотя нет, Шерлок", target_slot="title")
    assert slots.get("title") == "Шерлок"


# ── Test H: Mixed Cancel ──────────────────────────────────────────────────────
def test_h_mixed_cancel_routing():
    """
    Проверяет сценарий составной отмены: «Не надо. Какая погода завтра?»
    Команда play_media отменяется, а остаток «какая погода завтра» маршрутизируется в NEEDS_LLM.
    """
    orch = CommandOrchestrator()
    mock_player = MagicMock()

    # Шаг 1: начали сериал
    orch.process_user_text("Включи сериал", player=mock_player)
    assert orch.has_active_command() is True

    # Шаг 2: составная отмена с диалоговым вопросом
    res = orch.process_user_text("Не надо. Какая погода завтра?", player=mock_player)
    assert res.decision == RoutingDecision.NEEDS_LLM
    assert res.cleaned_text == "какая погода завтра"
    assert orch.has_active_command() is False


# ── Test I: Buffer Isolation & Memory Limit ───────────────────────────────────
def test_i_buffer_isolation_and_ram_cap():
    """
    Проверяет ограничение размера буфера карантина (1 МБ) и изоляцию сессий.
    """
    pt = PendingGeminiTurn(utterance_id="utt_test", generation_id=1, max_bytes=1024)

    # Заполняем данными до лимита
    ok1 = pt.add_audio(b"x" * 500)
    assert ok1 is True
    assert pt.total_audio_bytes == 500

    ok2 = pt.add_audio(b"x" * 500)
    assert ok2 is True
    assert pt.total_audio_bytes == 1000

    # Превышение лимита должно быть отклонено
    ok3 = pt.add_audio(b"x" * 100)
    assert ok3 is False
    assert pt.total_audio_bytes == 1000

    # Очистка освобождает память
    pt.clear()
    assert pt.total_audio_bytes == 0
    assert len(pt.audio_chunks) == 0


# ── Test J: Generation Interruption ───────────────────────────────────────────
def test_j_generation_interruption():
    """
    Проверяет, что при смене generation_id устаревшие реплики синтеза игнорируются.
    """
    from main import Jarvis

    with patch.object(Jarvis, "__init__", lambda self: None):
        jarvis = Jarvis()
        jarvis._speaking_lock = MagicMock()
        jarvis.audio_in_queue = MagicMock()
        jarvis.ui = MagicMock()
        jarvis._current_generation_id = 1
        jarvis._active_speech_generation_id = 1
        jarvis._is_speaking = True
        jarvis._active_synth_tasks = 1
        jarvis._speech_prefetch = None

        # Прерывание речи
        jarvis.interrupt_speech(reason="user_barge_in")

        assert jarvis._current_generation_id == 2
        assert jarvis._active_speech_generation_id == 2
        assert jarvis._is_speaking is False


# ── Test K: Media Fallback Duplication Prevention ──────────────────────────────
def test_k_media_player_fallback_no_duplication():
    """
    Проверяет, что если провайдер успешно открыл страницу (page_opened=True),
    MediaExecutor не запускает вторичный браузер через fallback.
    """
    with patch("actions.movie_player.movie_player") as mock_movie:
        # Провайдер вернул результат с page_opened=True
        mock_movie.return_value = MediaExecutionResult(
            success=True,
            message="Открываю в браузере",
            page_opened=True,
            provider="kinopoisk",
        )

        with patch.object(MediaExecutor, "_execute_fallback") as mock_fallback:
            success, msg = MediaExecutor.execute({"title": "Интерстеллар"})

            assert success is True
            assert mock_fallback.called is False
