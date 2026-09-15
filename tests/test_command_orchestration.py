"""Comprehensive unit tests for JARVIS Mark X Command Orchestration.

Covers:
  - Scenario 1: Multi-turn slot accumulation («Включи сериал» -> «Менталист» -> «Первый»)
  - Scenario 2: Title known at start, season requested («Включи сериал Менталист» -> «Второй»)
  - Scenario 3: Short contextual answers (ordinals, numbers, platforms)
  - Scenario 4: Interruption by new intent («Не надо. Какая погода завтра?»)
  - Scenario 5: Cancellation («отмена», «не надо», «забудь», «стоп»)
  - Scenario 6: One-shot direct commands («Открой Telegram»)
  - Single ownership & no double execution contract
  - Decoupled Command TTL vs Microphone window
  - Natural season/episode handling (no forced episode 1)
  - Stale async callback protection (command_id / turn_id)
  - Cancellation during execution
  - MediaExecutor fallback
"""

import time
from unittest.mock import MagicMock, patch


from core.command_context import CommandContext, CommandState
from core.command_orchestrator import CommandOrchestrator, RoutingDecision
from core.media_executor import MediaExecutor
from core.slot_filler import SlotFiller


# ─── Тест Сценария 1: Многошаговое накопление слотов ──────────────────────────
def test_scenario_1_multi_turn_slot_accumulation():
    """
    User: Включи сериал
    Jarvis: Какой сериал включить, сэр?
    User: Менталист
    Jarvis: Какой сезон, сэр?
    User: Первый
    Expected: play_media с title="Менталист 1 сезон", season=1
    """
    orch = CommandOrchestrator()
    mock_player = MagicMock()

    with patch("core.media_executor.MediaExecutor.execute", return_value=(True, "Включаю сериал")) as mock_exec:
        # Шаг 1: «Включи сериал»
        res1 = orch.process_user_text("Включи сериал", player=mock_player)
        assert res1.decision == RoutingDecision.CONSUMED_CLARIFICATION
        assert "сериал" in res1.prompt_to_user.lower()
        assert orch.has_active_command() is True
        assert orch.active_command.slots.get("media_type") == "series"
        assert orch.active_command.target_slot == "title"
        cmd_id = res1.command_id

        # Шаг 2: «Менталист» (короткий ответ — название)
        res2 = orch.process_user_text("Менталист", player=mock_player)
        assert res2.decision == RoutingDecision.CONSUMED_CLARIFICATION
        assert "сезон" in res2.prompt_to_user.lower()
        assert orch.active_command.slots.get("title") == "Менталист"
        assert orch.active_command.target_slot == "season"
        assert res2.command_id == cmd_id
        assert res2.turn_id > res1.turn_id

        # Шаг 3: «Первый» (короткий ответ — номер сезона)
        res3 = orch.process_user_text("Первый", player=mock_player)
        assert res3.decision == RoutingDecision.CONSUMED_ACTION
        assert mock_exec.called
        slots_passed = mock_exec.call_args[0][0]
        assert slots_passed.get("title") == "Менталист"
        assert slots_passed.get("season") == 1
        assert orch.has_active_command() is False  # Очищено после завершения


# ─── Тест Сценария 2: Название известно, уточняется сезон ─────────────────────
def test_scenario_2_title_known_season_asked():
    """
    User: Включи сериал Менталист
    Jarvis: Какой сезон, сэр?
    User: Второй
    Expected: season=2, title="Менталист 2 сезон"
    """
    orch = CommandOrchestrator()
    mock_player = MagicMock()

    with patch("core.media_executor.MediaExecutor.execute", return_value=(True, "Включаю")) as mock_exec:
        # Шаг 1
        res1 = orch.process_user_text("Включи сериал Менталист", player=mock_player)
        assert res1.decision == RoutingDecision.CONSUMED_CLARIFICATION
        assert "сезон" in res1.prompt_to_user.lower()
        assert orch.active_command.slots.get("title") == "Менталист"

        # Шаг 2: «Второй»
        res2 = orch.process_user_text("Второй", player=mock_player)
        assert res2.decision == RoutingDecision.CONSUMED_ACTION
        slots_passed = mock_exec.call_args[0][0]
        assert slots_passed.get("season") == 2
        assert slots_passed.get("title") == "Менталист"


# ─── Тест Сценария 3: Извлечение коротких ответов ─────────────────────────────
def test_scenario_3_short_contextual_answers():
    # Порядковые числительные
    assert SlotFiller.parse_ordinal("первый") == 1
    assert SlotFiller.parse_ordinal("второй") == 2
    assert SlotFiller.parse_ordinal("третий") == 3
    assert SlotFiller.parse_ordinal("5") == 5

    # Слоты относительно target_slot
    slots_season = SlotFiller.extract_contextual_slots("первый сезон", target_slot="season")
    assert slots_season.get("season") == 1

    slots_ep = SlotFiller.extract_contextual_slots("3 серия", target_slot="episode")
    assert slots_ep.get("episode") == 3

    slots_plat = SlotFiller.extract_contextual_slots("на ютубе", target_slot=None)
    assert slots_plat.get("platform") == "youtube"


# ─── Тест Сценария 4: Прерывание новой командой ───────────────────────────────
def test_scenario_4_interruption_by_new_intent():
    """
    Jarvis ожидает сезон.
    User: Не надо. Какая погода завтра?
    Expected: cancel play_media -> routes to NEEDS_LLM with cleaned_text='какая погода завтра'
    """
    orch = CommandOrchestrator()
    mock_player = MagicMock()

    # Шаг 1: начали сериал
    orch.process_user_text("Включи сериал", player=mock_player)
    assert orch.active_command.intent == "play_media"

    # Шаг 2: перебивание диалоговым вопросом о погоде
    res2 = orch.process_user_text("Не надо. Какая погода завтра?", player=mock_player)
    assert res2.decision == RoutingDecision.NEEDS_LLM
    assert res2.cleaned_text == "какая погода завтра"
    assert orch.has_active_command() is False



# ─── Тест Сценария 5: Отмена команды ──────────────────────────────────────────
def test_scenario_5_cancellation():
    orch = CommandOrchestrator()
    orch.process_user_text("Включи сериал")
    assert orch.has_active_command() is True

    for cancel_word in ["отмена", "не надо", "забудь", "стоп"]:
        orch.process_user_text("Включи сериал")
        res = orch.process_user_text(cancel_word)
        assert res.decision == RoutingDecision.CONSUMED_CANCEL
        assert orch.has_active_command() is False


# ─── Тест Сценария 6: Одношаговая команда ─────────────────────────────────────
def test_scenario_6_direct_one_shot_command():
    """
    User: Открой Telegram
    Expected: немедленное выполнение без дополнительных вопросов
    """
    orch = CommandOrchestrator()
    with patch("actions.open_app.open_app", return_value="Telegram открыт") as mock_open:
        res = orch.process_user_text("Открой Telegram")
        assert res.decision == RoutingDecision.CONSUMED_ACTION
        assert mock_open.called
        assert orch.has_active_command() is False


# ─── Тест: Routing Contract и исключение Double Execution ─────────────────────
def test_routing_contract_single_ownership():
    orch = CommandOrchestrator()

    # Действие потребляется оркестратором
    with patch("actions.open_app.open_app", return_value="ok"):
        res_action = orch.process_user_text("Открой Блокнот")
        assert res_action.decision == RoutingDecision.CONSUMED_ACTION

    # Общая разговорная фраза не потребляется и передаётся в Gemini
    res_chat = orch.process_user_text("Расскажи интересную историю про космос")
    assert res_chat.decision == RoutingDecision.NEEDS_LLM


# ─── Тест: Разделение Command TTL и Microphone Window ─────────────────────────
def test_command_ttl_decoupled_from_mic_window():
    orch = CommandOrchestrator()
    # Старт команды
    orch.process_user_text("Включи сериал")
    cmd = orch.active_command
    assert cmd is not None

    # Прошло 15 секунд (follow-up микрофона истёк, но TTL 60с ещё жив)
    now_15s = time.time() + 15.0
    assert cmd.is_expired(now=now_15s) is False
    assert orch.has_active_command() is True

    # Пользователь через 15 секунд произносит название — команда продолжается!
    with patch("core.media_executor.MediaExecutor.execute", return_value=(True, "ok")):
        orch.process_user_text("Менталист")
        assert orch.active_command.slots.get("title") == "Менталист"

    # Прошло 70 секунд с последней активности — TTL истёк
    now_70s = time.time() + 70.0
    assert orch.active_command.is_expired(now=now_70s) is True
    assert orch.has_active_command(now=now_70s) is False


# ─── Тест: Естественное поведение с сезонами (без принудительной серии 1) ──────
def test_natural_season_behavior():
    with patch("actions.movie_player.movie_player", return_value="Включаю 1 сезон") as mock_player:
        # Сезон указан, серия нет -> ищется сезон целиком
        success, msg = MediaExecutor.execute({"title": "Менталист", "season": 1})
        assert success is True
        args_passed = mock_player.call_args[0][0]
        assert "Менталист 1 сезон" in args_passed["title"]
        assert "серия" not in args_passed["title"]

        # Сезон и серия указаны -> ищется конкретная серия
        MediaExecutor.execute({"title": "Менталист", "season": 1, "episode": 2})
        args_passed2 = mock_player.call_args[0][0]
        assert "Менталист 1 сезон 2 серия" in args_passed2["title"]


# ─── Тест: Защита от Stale Callbacks (turn_id / command_id) ────────────────────
def test_stale_callback_protection():
    orch = CommandOrchestrator()

    # Шаг 1: Команда А
    res1 = orch.process_user_text("Включи сериал")
    cmd_a_id = res1.command_id
    turn_1 = res1.turn_id

    # Колбэк от команды А сейчас актуален
    assert orch.is_stale_callback(cmd_a_id, turn_1) is False

    # Пользователь начинает команду Б (или делает следующий шаг)
    res2 = orch.process_user_text("Менталист")
    turn_2 = res2.turn_id

    # Старый колбэк от turn 1 стал stale!
    assert orch.is_stale_callback(cmd_a_id, turn_1) is True
    # Новый колбэк актуален
    assert orch.is_stale_callback(cmd_a_id, turn_2) is False

    # Пользователь отменил команду
    orch.cancel_active_command()
    # Любой колбэк от старой команды стал stale
    assert orch.is_stale_callback(cmd_a_id, turn_2) is True


# ─── Тест: Отмена во время выполнения (cancellation during execution) ─────────
def test_cancellation_during_execution():
    orch = CommandOrchestrator()

    cmd = CommandContext(intent="open_app", slots={"app_name": "chrome"})
    orch._active_command = cmd
    orch._executing_command_id = cmd.id
    cmd.state = CommandState.EXECUTING

    # Пользователь отменяет во время выполнения
    orch.cancel_active_command("stop_during_exec")
    assert cmd.state == CommandState.CANCELLED
    assert orch.has_active_command() is False


# ─── Тест: Graceful Fallback в MediaExecutor ──────────────────────────────────
def test_media_executor_graceful_fallback():
    # Симулируем отказ основного movie_player
    with patch("actions.movie_player.movie_player", return_value="Не удалось открыть видео"), \
         patch("actions.browser_control.browser_control") as mock_browser:

        success, msg = MediaExecutor.execute({"title": "Менталист", "season": 1})
        assert success is True
        assert "Прямой запуск недоступен" in msg
        assert mock_browser.called
        url_called = mock_browser.call_args[0][0]["url"]
        assert "vkvideo.ru" in url_called or "youtube" in url_called
