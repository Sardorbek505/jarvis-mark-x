"""Факт о пользователе берётся только из слов пользователя.

В извлечение фактов подаётся весь обмен — и реплика ассистента тоже. Пока
промпт не различал авторство, догадки ассистента оседали в досье как истина.
Замер до правки: на «привет, как дела?» с ответом «как ваш проект в BEK
STYLE?» извлекалось «Пользователь работает в компании BEK STYLE» — факт,
которого пользователь не говорил. Дальше он попадал в каждый промпт, и
ассистент строил на нём новые догадки.

Поведение модели детерминированно не проверить, поэтому здесь закреплён
контракт запроса: авторство размечено и правило источника сформулировано.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_bot.gemini_client import GeminiClient


class _CapturingModels:
    """Подменяет вызов модели и запоминает отправленный промпт."""

    def __init__(self):
        self.prompt = ""

    def generate_content(self, model=None, contents=None, config=None):
        self.prompt = contents
        return SimpleNamespace(text="[]")


@pytest.fixture
def captured(monkeypatch):
    monkeypatch.setattr(GeminiClient, "__init__", lambda self, *a, **kw: None)
    client = GeminiClient()
    models = _CapturingModels()
    client._client = SimpleNamespace(models=models)
    client._model = "gemini-2.5-flash"
    return client, models


@pytest.mark.asyncio
async def test_авторство_реплик_размечено(captured):
    client, models = captured
    await client.extract_facts("мне 21", "Понял, сэр.")
    assert "Пользователь: мне 21" in models.prompt
    assert "JARVIS: Понял, сэр." in models.prompt


@pytest.mark.asyncio
async def test_правило_источника_есть_в_запросе(captured):
    """Без этой формулировки догадки ассистента снова станут «фактами»."""
    client, models = captured
    await client.extract_facts("привет", "Как ваш проект в BEK STYLE?")
    p = models.prompt
    assert "ТОЛЬКО из слов пользователя" in p
    assert "JARVIS" in p and "источником НЕ является" in p
    assert "не подтвердил" in p


def _dialogue(prompt: str) -> str:
    """Только секция диалога: слово «JARVIS» есть и в тексте самого правила."""
    return prompt.split("Диалог:", 1)[1]


@pytest.mark.asyncio
async def test_реплика_ассистента_не_обязательна(captured):
    client, models = captured
    await client.extract_facts("мне 21", "")
    assert "JARVIS:" not in _dialogue(models.prompt)
    assert "Пользователь: мне 21" in models.prompt


@pytest.mark.asyncio
async def test_пустой_ответ_модели_не_ломает_разбор(captured):
    client, _ = captured
    assert await client.extract_facts("привет", "здравствуйте") == []
