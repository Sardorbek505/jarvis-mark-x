"""Промах шлюза ПК должен быть незаметен, а не отвечать «Не понял команду».

На ПК сообщение отправляет эвристика по ключевым словам. Она ошибается по
самой своей природе: «громкость голоса у неё приятная» — не команда, но
слово «громкость» в ней есть, и лексикой это не различить (замерено:
2 ложных срабатывания из 12 обычных фраз остаются даже после сужения
ключей). Поэтому ПК сообщает признаком, что команду не узнал, и разговор
продолжается обычным путём.

Отдельно: про недоступный ПК говорим ВСЕГДА. Была ли это настоящая
команда, знает только сам ПК, а он офлайн — промолчать значит оставить
человека думать, что просьбу проигнорировали.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_bot import bot as bot_mod


class _Chat:
    async def send_action(self, action):
        pass


class _Message:
    def __init__(self):
        self.replies = []
        self.chat = _Chat()

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class _Bridge:
    def __init__(self, result, connected=True):
        self.connected = connected
        self._result = result
        self.calls = 0

    async def send_command_full(self, command, user_id, timeout=25.0):
        self.calls += 1
        return self._result


@pytest.fixture
def msg():
    return _Message()


async def _run(msg, bridge, *, quiet):
    return await bot_mod._run_pc(msg, "громкость голоса у неё приятная", 1,
                                 quiet_if_unknown=quiet)


@pytest.mark.asyncio
async def test_непонятая_команда_не_отвечает_пользователю(monkeypatch, msg):
    br = _Bridge({"text": "Не понял команду: «…»", "unknown": True})
    monkeypatch.setattr(bot_mod, "bridge", br)
    handled = await _run(msg, br, quiet=True)
    assert handled is False, "разговор должен продолжиться"
    assert msg.replies == [], "человек не должен видеть «Не понял команду»"


@pytest.mark.asyncio
async def test_настоящий_ответ_пк_доходит(monkeypatch, msg):
    br = _Bridge({"text": "🔊 Громкость 40%", "image_b64": None})
    monkeypatch.setattr(bot_mod, "bridge", br)
    handled = await _run(msg, br, quiet=True)
    assert handled is True
    assert msg.replies and "Громкость" in msg.replies[0]


@pytest.mark.asyncio
async def test_явная_команда_pc_видит_не_понял(monkeypatch, msg):
    """У /pc <команда> человек спросил ПК напрямую — ответ ему нужен."""
    br = _Bridge({"text": "Не понял команду: «…»", "unknown": True})
    monkeypatch.setattr(bot_mod, "bridge", br)
    handled = await _run(msg, br, quiet=False)
    assert handled is True
    assert msg.replies and "Не понял" in msg.replies[0]


@pytest.mark.asyncio
async def test_про_офлайн_говорим_всегда(monkeypatch, msg):
    """Иначе просьба про скриншот выглядит проигнорированной."""
    br = _Bridge(None, connected=False)
    monkeypatch.setattr(bot_mod, "bridge", br)
    handled = await _run(msg, br, quiet=True)
    assert handled is True
    assert msg.replies, "офлайн ПК нельзя замалчивать"
    assert br.calls == 0


@pytest.mark.asyncio
async def test_старый_клиент_без_признака_ведёт_себя_как_раньше(monkeypatch, msg):
    """Пока на ПК прежняя сборка, признака нет — поведение прежнее."""
    br = _Bridge({"text": "Не понял команду: «…»"})
    monkeypatch.setattr(bot_mod, "bridge", br)
    handled = await _run(msg, br, quiet=True)
    assert handled is True
    assert msg.replies
