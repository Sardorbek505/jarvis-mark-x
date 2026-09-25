"""Команды с телефона на ПК: ничего опасного по случайной фразе."""

import pytest

from telegram_bot import pc_server


@pytest.mark.asyncio
async def test_phrase_about_phone_does_not_type_pc_password(monkeypatch):
    typed = []
    monkeypatch.setattr(pc_server, "_do_unlock", lambda: typed.append(1) or {"text": "ok"})
    await pc_server._execute("не могу разблокировать экран телефона")
    assert typed == [], "пароль ПК напечатан в активное окно"
    await pc_server._execute("разблокируй")
    assert typed == [1]


@pytest.mark.asyncio
async def test_shutdown_needs_confirmation_and_ignores_delay(monkeypatch):
    calls = []
    import actions.computer_settings as cs
    monkeypatch.setattr(cs, "computer_settings", lambda p: calls.append(p) or "done")
    pc_server._pending_power.clear()

    first = await pc_server._execute("выключи пк")
    assert calls == [] and "точно" in first["text"]
    later = await pc_server._execute("выключи пк через час")
    assert calls == [] and "Отложенное" in later["text"]
    await pc_server._execute("точно выключи пк")
    assert calls == [{"action": "shutdown"}]


def test_known_app_beats_music_and_weather_city():
    assert pc_server._known_app("запусти телеграм") == "телеграм"
    assert pc_server._known_app("запусти любэ") is None
    assert pc_server._weather_city("погода сейчас") is None
    assert pc_server._weather_city("погода в ташкенте") == "ташкенте"


def test_window_minimize_with_target_is_not_activate():
    assert pc_server._parse_window("сверни хром") == {"action": "minimize", "target": "хром"}
