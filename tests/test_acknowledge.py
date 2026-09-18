"""Долгий инструмент не должен работать в тишине.

Поиск в сети, разбор PDF, пересказ ролика и запуск браузера занимают секунды.
Раньше всё это время Джарвис молчал: снаружи пауза после вопроса неотличима от
«не расслышал», и человек повторял вопрос, получая вторую такую же паузу.

Здесь проверяется, что короткая фраза звучит ровно там, где нужна: перед
медленным вызовом, один раз, не обещая результата — и НЕ звучит там, где она
была бы лишним голосом поверх ответа.
"""

import asyncio
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import acknowledge


@pytest.fixture(autouse=True)
def _чистое_чередование():
    acknowledge.reset()
    yield
    acknowledge.reset()


# ─── Кому фраза положена, а кому нет ─────────────────────────────────────────

@pytest.mark.parametrize("инструмент", [
    "web_search", "morning_briefing", "look_at_screen",
    "look_at_camera", "file_processor",
])
def test_медленный_инструмент_получает_фразу(инструмент):
    assert acknowledge.phrase(инструмент, {})


@pytest.mark.parametrize("инструмент", [
    "weather", "calendar", "files", "game_launcher", "open_app",
    "save_to_memory", "recall_memory", "window_control", "obsidian",
    "computer_control", "os_reminder", "set_mode", "translation",
])
def test_быстрый_инструмент_молчит(инструмент):
    """Подтверждение к действию на двести миллисекунд — не забота, а помеха:
    фраза наложится на ответ, который и так уже готов."""
    assert acknowledge.phrase(инструмент, {}) is None


def test_незнакомый_инструмент_молчит():
    assert acknowledge.phrase("плагин_которого_нет", {"action": "делай"}) is None


def test_фраза_зависит_от_действия():
    """`youtube_video` может открыть ссылку за мгновение, а может минуту
    разбирать субтитры. Разница в параметре, а не в названии."""
    assert acknowledge.phrase("youtube_video", {"action": "play"}) is None
    assert acknowledge.phrase("youtube_video", {"action": "get_info"}) is None
    assert acknowledge.phrase("youtube_video", {"action": "summarize"})


def test_действие_узнаётся_в_любом_регистре_и_по_синониму():
    assert acknowledge.phrase("movie_player", {"action": " PLAY "})
    assert acknowledge.phrase("movie_player", {"action": "включить"})
    assert acknowledge.phrase("movie_player", {"action": "pause"}) is None


def test_нестроковое_действие_не_роняет_подбор():
    """Параметры приходят от модели: там бывает что угодно, вплоть до числа."""
    assert acknowledge.phrase("youtube_video", {"action": 5}) is None
    assert acknowledge.phrase("web_search", None)
    assert acknowledge.phrase("", {}) is None


# ─── Браузер: долго ровно один раз ───────────────────────────────────────────

def test_браузер_подтверждается_только_пока_не_открыт(monkeypatch):
    """Chromium поднимается секунды, а клик по открытой странице — доли.
    Говорить «поднимаю браузер» на каждый клик значит мешать работе."""
    monkeypatch.setattr(acknowledge, "_браузер_уже_открыт", lambda: False)
    assert acknowledge.phrase("browser", {"action": "click"})

    monkeypatch.setattr(acknowledge, "_браузер_уже_открыт", lambda: True)
    assert acknowledge.phrase("browser", {"action": "click"}) is None


def test_открытие_ссылки_в_обычном_браузере_молчит(monkeypatch):
    """`go_to` уходит в системный браузер и возвращается сразу."""
    monkeypatch.setattr(acknowledge, "_браузер_уже_открыт", lambda: False)
    assert acknowledge.phrase("browser", {"action": "go_to"}) is None


def test_сломанный_браузерный_модуль_считается_закрытым(monkeypatch):
    """Playwright может быть не установлен. Это не повод падать при подборе
    фразы — и не повод считать браузер открытым."""
    import builtins

    настоящий = builtins.__import__

    def взорвать(имя, *args, **kwargs):
        if имя == "core.browser_session" or имя.endswith("browser_session"):
            raise ImportError("нет playwright")
        return настоящий(имя, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", взорвать)
    monkeypatch.delitem(sys.modules, "core.browser_session", raising=False)
    assert acknowledge._браузер_уже_открыт() is False


# ─── Что именно говорится ────────────────────────────────────────────────────

def _все_фразы() -> list[str]:
    собрано = list(acknowledge._БРАУЗЕР_ФРАЗЫ)
    for по_действиям in acknowledge._ФРАЗЫ.values():
        for варианты in по_действиям.values():
            собрано.extend(варианты)
    return собрано


_ОБЕЩАНИЯ = (
    "готово", "нашёл", "нашел", "сделал", "включил", "открыл",
    "прочитал", "запустил", "посмотрел", "собрал", "поставил",
)


@pytest.mark.parametrize("фраза", _все_фразы())
def test_фраза_не_обещает_результата(фраза):
    """«Нашёл» до окончания поиска — враньё, которое через три секунды
    опровергнет сам же ответ. Подтверждение говорит о начале, не о конце."""
    низкая = фраза.lower()
    for обещание in _ОБЕЩАНИЯ:
        assert обещание not in низкая, f"«{фраза}» обещает сделанное"


@pytest.mark.parametrize("фраза", _все_фразы())
def test_фраза_коротка_и_закончена(фраза):
    """Длинное подтверждение само становится паузой: пока оно звучит,
    настоящий ответ ждёт своей очереди в том же потоке звука."""
    assert len(фраза) <= 40, f"«{фраза}» длиннее одного вдоха"
    assert фраза[-1] in ".!?", f"«{фраза}» без конца предложения"


def test_варианты_чередуются_и_не_повторяются_подряд():
    """Одна и та же фраза тридцатый раз за день звучит как автоответчик."""
    сказанное = [acknowledge.phrase("web_search", {}) for _ in range(6)]
    assert len(set(сказанное)) > 1
    assert all(a != b for a, b in zip(сказанное, сказанное[1:]))


def test_чередование_у_каждого_инструмента_своё():
    """Иначе поиск «съедал» бы очередь у чтения файлов и оба звучали бы
    вторым вариантом через раз."""
    первый_поиск = acknowledge.phrase("web_search", {})
    acknowledge.phrase("file_processor", {})
    acknowledge.phrase("file_processor", {})
    assert acknowledge.phrase("web_search", {}) != первый_поиск


def test_правило_для_промпта_запрещает_повтор():
    """Модель не слышит того, что система произнесла за неё, и без прямого
    запрета начинает ответ такой же фразой — человек слышит её дважды."""
    правило = acknowledge.PROMPT_RULE.lower()
    assert "секунду" in правило
    assert "не начинай" in правило


# ─── Как это встроено в ход разговора ────────────────────────────────────────

import main as jarvis_main  # noqa: E402  — после sys.path


class _Окно:
    muted = False

    def __init__(self):
        self.логи: list[str] = []

    def write_log(self, text):
        self.логи.append(text)

    def set_state(self, state):
        pass


def _джарвис(monkeypatch, голос="fish"):
    j = jarvis_main.Jarvis.__new__(jarvis_main.Jarvis)
    j.ui = _Окно()
    j.сказанное = []

    async def _речь(text, *, метрики=True):
        j.сказанное.append((text, метрики))

    j._speak_fish = _речь
    monkeypatch.setattr(jarvis_main, "get_voice_provider", lambda: голос)
    return j


@pytest.mark.asyncio
async def test_подтверждение_звучит_и_не_закрывает_замер(monkeypatch):
    """Фраза — не ответ. Закрыть ею ход значило бы записать в статистику
    полсекунды вместо настоящих четырёх и перестать замечать медленное."""
    j = _джарвис(monkeypatch)
    j._acknowledge("web_search", {"query": "погода"}, False)
    await asyncio.sleep(0)

    assert j.сказанное == [("Секунду, ищу.", False)]
    assert any("Секунду, ищу." in s for s in j.ui.логи)


@pytest.mark.asyncio
async def test_модель_уже_заговорила_подтверждение_молчит(monkeypatch):
    """Текст, сказанный моделью до вызова, прозвучит вместе с ответом.
    Подтверждение поверх него — второе «сейчас посмотрю» подряд."""
    j = _джарвис(monkeypatch)
    j._acknowledge("web_search", {}, True)
    await asyncio.sleep(0)

    assert j.сказанное == []


@pytest.mark.asyncio
async def test_с_голосом_gemini_подтверждения_нет(monkeypatch):
    """Подмешивать чужой голос к голосу самой модели ради полутора секунд
    хуже, чем эти полторы секунды подождать."""
    j = _джарвис(monkeypatch, голос="gemini")
    j._acknowledge("web_search", {}, False)
    await asyncio.sleep(0)

    assert j.сказанное == []


@pytest.mark.asyncio
async def test_срыв_подтверждения_не_мешает_инструменту(monkeypatch):
    """Подтверждение — удобство. Его поломка не должна стоить вызова,
    ради которого всё затевалось."""
    j = _джарвис(monkeypatch)
    monkeypatch.setattr(acknowledge, "phrase",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ой")))

    j._acknowledge("web_search", {}, False)   # не должно поднять исключение
    await asyncio.sleep(0)
    assert j.сказанное == []


# ─── Проводка: откуда берётся «модель уже заговорила» ────────────────────────

from types import SimpleNamespace  # noqa: E402


class _Обрыв(Exception):
    """Конец потока ответов — иначе `while True` в приёме не кончится."""


class _Сессия:
    def __init__(self, ответы):
        self._ответы = ответы
        self.ответы_инструментов = []

    def receive(self):
        async def поток():
            for r in self._ответы:
                yield r
            raise _Обрыв
        return поток()

    async def send_tool_response(self, function_responses):
        self.ответы_инструментов.append(function_responses)


def _ответ(**поля):
    основа = {"data": None, "server_content": None, "tool_call": None,
              "session_resumption_update": None}
    основа.update(поля)
    return SimpleNamespace(**основа)


def _содержимое(**поля):
    основа = {"output_transcription": None, "input_transcription": None,
              "turn_complete": False}
    основа.update(поля)
    return SimpleNamespace(**основа)


async def _прогнать(monkeypatch, ответы) -> list[bool]:
    """Гоняет цикл приёма и возвращает, что досталось каждому вызову."""
    j = jarvis_main.Jarvis.__new__(jarvis_main.Jarvis)
    j.ui = _Окно()
    j.ui.lock_on = lambda имя: None
    j._latency = SimpleNamespace(
        mark_transcript=lambda: None, mark_answer_audio=lambda: None,
        mark_turn_complete=lambda: None, add_tool=lambda *a: None,
    )
    j._resume_handle = None
    j._turn_done_event = None
    j.session = _Сессия(ответы)

    досталось: list[bool] = []

    async def _вызов(fc, *, уже_сказано=False):
        досталось.append(уже_сказано)
        return SimpleNamespace(id=fc.id, name=fc.name)

    j._execute_tool = _вызов
    monkeypatch.setattr(jarvis_main, "get_voice_provider", lambda: "fish")

    with pytest.raises(_Обрыв):
        await j._receive_audio()
    return досталось


@pytest.mark.asyncio
async def test_вызов_без_предисловия_считается_молчаливым(monkeypatch):
    вызов = SimpleNamespace(id="1", name="web_search", args={})
    досталось = await _прогнать(monkeypatch, [
        _ответ(tool_call=SimpleNamespace(function_calls=[вызов])),
    ])
    assert досталось == [False]


@pytest.mark.asyncio
async def test_сказанное_до_вызова_доходит_до_подтверждения(monkeypatch):
    """Ради этого признака подтверждение и молчит: иначе человек услышит
    «Секунду, ищу», а следом — «Сейчас посмотрю» от самой модели."""
    вызов = SimpleNamespace(id="1", name="web_search", args={})
    досталось = await _прогнать(monkeypatch, [
        _ответ(server_content=_содержимое(
            output_transcription=SimpleNamespace(text="Сейчас посмотрю"))),
        _ответ(tool_call=SimpleNamespace(function_calls=[вызов])),
    ])
    assert досталось == [True]


@pytest.mark.asyncio
async def test_пробелы_не_считаются_сказанным(monkeypatch):
    """Расшифровка приходит кусками и начинается с пробела — пустой звук
    не должен выдавать себя за произнесённую фразу."""
    вызов = SimpleNamespace(id="1", name="web_search", args={})
    досталось = await _прогнать(monkeypatch, [
        _ответ(server_content=_содержимое(
            output_transcription=SimpleNamespace(text="   "))),
        _ответ(tool_call=SimpleNamespace(function_calls=[вызов])),
    ])
    assert досталось == [False]
