"""Тесты на честные результаты исполнения (Truthful Outcomes) в FastCommandRouter."""

from core.fast_command_router import (
    FastCommandRouter,
    ExecutionStatus,
    CommandCategory,
)


def test_truthful_outcome_media_key_failure(monkeypatch):
    """Когда отправка media-клавиши возвращает False, статус должен быть FAILED, а не SUCCESS."""
    monkeypatch.setattr("actions.music_player._send_media_key", lambda action: False)

    res = FastCommandRouter.match_and_execute("Джарвис, пауза")
    assert res.handled is True
    assert res.status == ExecutionStatus.FAILED
    assert res.category == CommandCategory.LOCAL_CONTEXT_DEPENDENT
    assert "не удалось" in res.text.lower()


def test_truthful_outcome_media_key_success(monkeypatch):
    """Когда отправка media-клавиши успешна, статус SUCCESS."""
    monkeypatch.setattr("actions.music_player._send_media_key", lambda action: True)

    res = FastCommandRouter.match_and_execute("Джарвис, следующий трек")
    assert res.handled is True
    assert res.status == ExecutionStatus.SUCCESS
    assert res.category == CommandCategory.LOCAL_CONTEXT_DEPENDENT
    assert "следующий трек" in res.text.lower()


def test_truthful_outcome_player_unavailable(monkeypatch):
    """Когда видеоплеер не найден, возвращается статус UNAVAILABLE с честным ответом."""
    monkeypatch.setattr(
        "actions.movie_player.movie_player",
        lambda params, player=None: "Не удалось переключить режим: окно плеера не найдено",
    )

    res = FastCommandRouter.match_and_execute("Джарвис, на весь экран")
    assert res.handled is True
    assert res.status == ExecutionStatus.UNAVAILABLE
    assert res.category == CommandCategory.LOCAL_CONTEXT_DEPENDENT
    assert "не найдено" in res.text.lower()


def test_truthful_outcome_volume_error(monkeypatch):
    """Когда настройка громкости завершается ошибкой, статус FAILED."""
    monkeypatch.setattr(
        "actions.computer_settings.computer_settings",
        lambda params, player=None: "Ошибка регулировки громкости: устройство занято",
    )

    res = FastCommandRouter.match_and_execute("Джарвис, сделай потише")
    assert res.handled is True
    assert res.status == ExecutionStatus.FAILED
    assert res.category == CommandCategory.LOCAL_SAFE
    assert "не удалось" in res.text.lower() or "ошибка" in res.text.lower()


def test_truthful_outcome_now_playing_nothing(monkeypatch):
    """Когда ничего не играет, статус UNAVAILABLE."""
    monkeypatch.setattr(
        "core.media_session_manager.MediaSessionManager.get_now_playing_speech",
        lambda: "Сэр, в данный момент ничего не играет.",
    )

    res = FastCommandRouter.match_and_execute("Джарвис, что сейчас играет?")
    assert res.handled is True
    assert res.status == ExecutionStatus.UNAVAILABLE
    assert res.category == CommandCategory.LOCAL_CONTEXT_DEPENDENT


def test_truthful_outcome_not_applicable():
    """Фразы, не являющиеся детерминированными быстрыми командами, имеют статус NOT_APPLICABLE."""
    res = FastCommandRouter.match_and_execute("Джарвис, расскажи квантовую теорию поля")
    assert res.handled is False
    assert res.status == ExecutionStatus.NOT_APPLICABLE
    assert res.category == CommandCategory.LLM_REQUIRED


# ─── Тесты на честные результаты сценариев (Routines) ─────────────────────────

def test_truthful_routine_morning_partial_failure(monkeypatch):
    """Если часть шагов утреннего сценария падает (погода), статус PARTIAL_SUCCESS с честным текстом."""
    from core.routines_engine import RoutinesEngine, RoutineStatus

    monkeypatch.setattr("actions.weather.weather_action", lambda *a, **kw: "Не удалось получить данные о погоде")
    monkeypatch.setattr("actions.calendar.get_todays_schedule", lambda: "Встреча в 11:00")
    monkeypatch.setattr("actions.music_player.music_player", lambda *a, **kw: "Музыка играет")

    res = RoutinesEngine.execute("morning")
    assert res.status == RoutineStatus.PARTIAL_SUCCESS
    assert "погода" in res.failed_steps
    assert "календарь" in res.successful_steps
    assert "музыка" in res.successful_steps
    assert "Данные о погоде временно недоступны" in res
    assert "Встреча в 11:00" in res


def test_truthful_routine_work_partial_failure(monkeypatch):
    """Если музыка не запустилась, рабочий режим возвращает PARTIAL_SUCCESS с пояснением."""
    from core.routines_engine import RoutinesEngine, RoutineStatus

    monkeypatch.setattr("actions.modes.set_mode", lambda *a, **kw: "Режим активирован")
    monkeypatch.setattr("actions.music_player.music_player", lambda *a, **kw: "Не удалось подключиться к Spotify")

    res = RoutinesEngine.execute("work")
    assert res.status == RoutineStatus.PARTIAL_SUCCESS
    assert "режим" in res.successful_steps
    assert "музыка" in res.failed_steps
    assert "частично" in res.lower()
    assert "не удалось запустить фоновую музыку" in res.lower()


def test_truthful_routine_unknown():
    """Неизвестный сценарий возвращает FAILED."""
    from core.routines_engine import RoutinesEngine, RoutineStatus

    res = RoutinesEngine.execute("unknown_routine_xyz")
    assert res.status == RoutineStatus.FAILED
    assert "не найден" in res.lower()

