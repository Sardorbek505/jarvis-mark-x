"""Запасные модели: один мозг (промпт + история), разные «языки»."""

import json

import httpx
import pytest

from telegram_bot import fallback_llm
from telegram_bot.fallback_llm import FallbackChain, Provider, providers, to_messages


@pytest.fixture(autouse=True)
def _без_конфига(monkeypatch):
    monkeypatch.setattr(fallback_llm, "_from_config", lambda: {})
    for name in ("GROQ", "OPENROUTER", "MISTRAL"):
        monkeypatch.delenv(f"{name}_API_KEY", raising=False)
        monkeypatch.delenv(f"{name}_MODEL", raising=False)


def _подменить_http(monkeypatch, handler):
    """httpx без сети: каждый запрос уходит в handler."""
    real = httpx.AsyncClient

    def client(**kw):
        return real(transport=httpx.MockTransport(handler), **kw)
    monkeypatch.setattr(httpx, "AsyncClient", client)


def _ответ(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def test_в_цепочке_только_провайдеры_с_ключом(monkeypatch):
    assert providers() == []
    monkeypatch.setenv("MISTRAL_API_KEY", "m-key")
    monkeypatch.setenv("GROQ_API_KEY", "g-key")
    monkeypatch.setenv("GROQ_MODEL", "llama-custom")
    names = [(p.name, p.model) for p in providers()]
    assert names == [("groq", "llama-custom"), ("mistral", "mistral-small-latest")]


def test_шаблонный_ключ_из_примера_не_считается(monkeypatch):
    monkeypatch.setattr(fallback_llm, "_from_config",
                        lambda: {"groq_api_key": "your_groq_api_key_here"})
    assert providers() == []


def test_история_переводится_с_тем_же_промптом():
    contents = [
        {"role": "user", "parts": [{"text": "Привет"}]},
        {"role": "model", "parts": [{"text": "Добрый вечер, сэр."}]},
        {"role": "user", "parts": [{"text": "Как дела?"}]},
    ]
    assert to_messages(contents, "Ты Джарвис") == [
        {"role": "system", "content": "Ты Джарвис"},
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Добрый вечер, сэр."},
        {"role": "user", "content": "Как дела?"},
    ]


def test_голос_и_картинки_запасной_модели_не_отдаются():
    assert to_messages([{"role": "user", "parts": [{"inline_data": {"data": b"x"}}]}]) is None
    assert to_messages([object()]) is None


@pytest.mark.asyncio
async def test_лимит_у_первого_отвечает_следующий(monkeypatch):
    вызовы = []

    def handler(request):
        вызовы.append(request.url.host)
        if request.url.host == "api.groq.com":
            return httpx.Response(429, json={"error": "rate limit"})
        body = json.loads(request.content)
        assert body["messages"][0] == {"role": "system", "content": "Ты Джарвис"}
        return _ответ("Слушаю, сэр.")
    _подменить_http(monkeypatch, handler)

    chain = FallbackChain([
        Provider("groq", "https://api.groq.com/openai/v1", "k1", "m1"),
        Provider("mistral", "https://api.mistral.ai/v1", "k2", "m2"),
    ])
    assert await chain.complete("Джарвис?", "Ты Джарвис") == "Слушаю, сэр."
    assert await chain.complete("Ещё раз", "Ты Джарвис") == "Слушаю, сэр."
    # Упёршийся в лимит отдыхает: во второй раз к нему не ходили.
    assert вызовы == ["api.groq.com", "api.mistral.ai", "api.mistral.ai"]


@pytest.mark.asyncio
async def test_бот_переходит_на_запасную_при_квоте_gemini(monkeypatch):
    from telegram_bot.gemini_client import GeminiClient

    monkeypatch.setenv("GROQ_API_KEY", "g-key")
    _подменить_http(monkeypatch, lambda request: _ответ("Готово, сэр."))

    client = GeminiClient(api_key="test", model="gemini-2.5-flash")
    обращения_к_gemini = []

    def квота_кончилась(**kw):
        обращения_к_gemini.append(kw["model"])
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota")
    monkeypatch.setattr(client._client.models, "generate_content", квота_кончилась)

    assert await client.chat(1, "поставь задачу") == "Готово, сэр."
    первый_раз = len(обращения_к_gemini)
    assert первый_раз > 0

    # Gemini отдыхает — следующий ответ сразу от запасной, без ожидания.
    assert await client.chat(1, "и ещё одну") == "Готово, сэр."
    assert len(обращения_к_gemini) == первый_раз
    # История общая: запасная модель видит весь разговор.
    assert [m["role"] for m in client._history_for(1)] == ["user", "model", "user", "model"]


@pytest.mark.asyncio
async def test_без_запасных_поведение_прежнее(monkeypatch):
    from telegram_bot.gemini_client import GeminiClient

    client = GeminiClient(api_key="test")

    def падает(**kw):
        raise RuntimeError("500 internal")
    monkeypatch.setattr(client._client.models, "generate_content", падает)

    assert "недоступен" in await client.chat(1, "привет")


@pytest.mark.parametrize("err, hint", [
    ("403 PERMISSION_DENIED. Your API key was reported as leaked.", "заблокирован"),
    ("400 API key not valid. Please pass a valid API key.", "заблокирован"),
    ("429 RESOURCE_EXHAUSTED", "Лимит"),
    ("500 internal", "недоступен"),
])
def test_бот_говорит_причину_отказа(err, hint):
    from telegram_bot.gemini_client import _unavailable_message
    assert hint in _unavailable_message(RuntimeError(err))
