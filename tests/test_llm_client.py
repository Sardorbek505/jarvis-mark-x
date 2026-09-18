"""Кончившаяся квота Gemini не должна отнимать пересказ.

Пересказ файла, пересказ ролика и поиск были тремя копиями одного блока: свой
клиент Gemini, своё получение ключа, свой таймаут, своя обработка ошибки.
Замолкали они тоже все сразу — при том, что на машине человека может стоять
локальная модель, которой такая задача вполне по силам.

Здесь проверяется слой, который делает их одним вызовом и даёт очередь
провайдеров. Сети в тестах нет: каждый провайдер подменён.
"""

import json
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import llm_client as llm

# Настоящая проверка живости — до того, как её подменит фикстура. Тот же
# урок, что и с `_ddg_results`: фикстура подменяет функцию раньше, чем тест
# успевает до неё дотянуться.
_НАСТОЯЩАЯ_ЖИВОСТЬ = llm._ollama_жив


@pytest.fixture(autouse=True)
def чисто(monkeypatch):
    llm.reset()
    # Ни настроек, ни переменных среды: тест не должен зависеть от машины.
    monkeypatch.setattr(llm, "_настройки", dict)
    for имя in ("JARVIS_LLM", "JARVIS_LLM_MODEL", "OLLAMA_URL", "OLLAMA_MODEL",
                "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(имя, raising=False)
    monkeypatch.setattr(llm, "_ключ_gemini", lambda: "")
    monkeypatch.setattr(llm, "_ollama_жив", lambda адрес: False)
    yield
    llm.reset()


def _отвечает(monkeypatch, провайдер, ответ):
    monkeypatch.setitem(llm._СПРОСИТЬ, провайдер,
                        lambda *a, **k: ответ)


def _падает(monkeypatch, провайдер, беда="квота кончилась"):
    def _взорвать(*a, **k):
        raise RuntimeError(беда)
    monkeypatch.setitem(llm._СПРОСИТЬ, провайдер, _взорвать)


# ─── Доступность ─────────────────────────────────────────────────────────────

def test_без_ключа_и_без_сервера_модели_нет():
    assert llm.available() is False
    assert llm.ask("перескажи") == ""


def test_ключ_gemini_делает_модель_доступной(monkeypatch):
    monkeypatch.setattr(llm, "_ключ_gemini", lambda: "ключ")
    assert llm.available() is True


def test_живой_ollama_делает_модель_доступной(monkeypatch):
    monkeypatch.setattr(llm, "_ollama_жив", lambda адрес: True)
    assert llm.available() is True


def test_живость_ollama_не_выясняется_на_каждый_вызов(monkeypatch):
    """Иначе каждый пересказ платит таймаутом соединения за выяснение того,
    что уже выяснено секунду назад."""
    попыток = []

    def _проба(запрос, timeout=None):
        попыток.append(1)
        raise OSError("нет сервера")

    monkeypatch.setattr(llm.urllib.request, "urlopen", _проба)

    assert _НАСТОЯЩАЯ_ЖИВОСТЬ("http://localhost:11434") is False
    assert _НАСТОЯЩАЯ_ЖИВОСТЬ("http://localhost:11434") is False

    assert len(попыток) == 1


# ─── Очередь провайдеров ─────────────────────────────────────────────────────

def test_ответ_приходит_от_первого_доступного(monkeypatch):
    monkeypatch.setattr(llm, "_ключ_gemini", lambda: "ключ")
    _отвечает(monkeypatch, "gemini", "вот пересказ")

    assert llm.ask("перескажи") == "вот пересказ"


def test_упавший_провайдер_уступает_следующему(monkeypatch):
    """Ради этого всё и затевалось: квота кончилась — пересказ остаётся."""
    monkeypatch.setattr(llm, "_ключ_gemini", lambda: "ключ")
    monkeypatch.setattr(llm, "_ollama_жив", lambda адрес: True)
    _падает(monkeypatch, "gemini", "RESOURCE_EXHAUSTED")
    _отвечает(monkeypatch, "ollama", "пересказ местной моделью")

    assert llm.ask("перескажи") == "пересказ местной моделью"


def test_пустой_ответ_тоже_уступает_следующему(monkeypatch):
    """Провайдер, вернувший пустоту, ничем не лучше упавшего."""
    monkeypatch.setattr(llm, "_ключ_gemini", lambda: "ключ")
    monkeypatch.setattr(llm, "_ollama_жив", lambda адрес: True)
    _отвечает(monkeypatch, "gemini", "")
    _отвечает(monkeypatch, "ollama", "а вот и ответ")

    assert llm.ask("перескажи") == "а вот и ответ"


def test_отказ_всех_возвращает_пустоту_а_не_исключение(monkeypatch):
    """Упавший пересказ не должен ронять голосовой ход — вызывающий скажет
    об этом вслух сам."""
    monkeypatch.setattr(llm, "_ключ_gemini", lambda: "ключ")
    _падает(monkeypatch, "gemini")

    assert llm.ask("перескажи") == ""


def test_выбранный_провайдер_идёт_первым(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM", "ollama")
    monkeypatch.setattr(llm, "_ключ_gemini", lambda: "ключ")
    monkeypatch.setattr(llm, "_ollama_жив", lambda адрес: True)

    assert llm._порядок()[0] == "ollama"


def test_несуществующий_выбор_игнорируется(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM", "скайнет")
    assert llm.выбранный() == ""


def test_пустой_запрос_никого_не_беспокоит(monkeypatch):
    звонков = []
    monkeypatch.setattr(llm, "_ключ_gemini", lambda: "ключ")
    monkeypatch.setitem(llm._СПРОСИТЬ, "gemini",
                        lambda *a, **k: звонков.append(1) or "ответ")

    assert llm.ask("   ") == ""
    assert звонков == []


# ─── Разбор ответов провайдеров ──────────────────────────────────────────────

def test_ollama_читается_из_поля_message(monkeypatch):
    отправленное = {}

    def _почтой(url, тело, таймаут, заголовки=None):
        отправленное.update({"url": url, "тело": тело})
        return {"message": {"content": "  ответ Ollama  "}}

    monkeypatch.setattr(llm, "_почтой", _почтой)

    ответ = llm._спросить_ollama("вопрос", "ты Джарвис", 300, 0.3, 30)

    assert ответ == "ответ Ollama"
    assert отправленное["url"].endswith("/api/chat")
    assert отправленное["тело"]["stream"] is False
    assert отправленное["тело"]["messages"][0]["role"] == "system"


def test_openai_читается_из_choices(monkeypatch):
    отправленное = {}

    def _почтой(url, тело, таймаут, заголовки=None):
        отправленное.update({"url": url, "заголовки": заголовки or {}})
        return {"choices": [{"message": {"content": "ответ OpenAI"}}]}

    monkeypatch.setattr(llm, "_почтой", _почтой)
    monkeypatch.setenv("OPENAI_API_KEY", "секрет")

    ответ = llm._спросить_openai("вопрос", "", 300, 0.3, 30)

    assert ответ == "ответ OpenAI"
    assert отправленное["url"].endswith("/chat/completions")
    assert отправленное["заголовки"]["Authorization"] == "Bearer секрет"


def test_пустые_choices_не_роняют_разбор(monkeypatch):
    monkeypatch.setattr(llm, "_почтой", lambda *a, **k: {"choices": []})
    assert llm._спросить_openai("вопрос", "", 300, 0.3, 30) == ""


def test_свой_адрес_ollama_уважается(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ферма:11434/")
    assert llm._адрес_ollama() == "http://ферма:11434"


# ─── Что говорится человеку ──────────────────────────────────────────────────

def test_описание_называет_отсутствие_модели_прямо():
    описание = llm.describe()
    assert "нет" in описание.lower()


def test_описание_перечисляет_живых(monkeypatch):
    monkeypatch.setattr(llm, "_ключ_gemini", lambda: "ключ")
    monkeypatch.setattr(llm, "_ollama_жив", lambda адрес: True)

    описание = llm.describe()

    assert "Gemini" in описание and "Ollama" in описание


# ─── Связка с пересказом ─────────────────────────────────────────────────────

def test_пересказ_файла_идёт_через_слой(monkeypatch, tmp_path):
    """Проверяем именно проводку: раньше здесь был свой клиент Gemini."""
    from actions import file_processor as fp

    monkeypatch.setattr(llm, "ask", lambda запрос, **k: "пересказ через слой")

    assert fp._пересказать("текст файла", "", "файл.txt") == "пересказ через слой"


def test_пересказ_ролика_идёт_через_слой(monkeypatch):
    from actions import youtube_video as yt

    monkeypatch.setattr(llm, "ask", lambda запрос, **k: "пересказ ролика")

    assert yt._summarise("расшифровка", "Название") == "пересказ ролика"
