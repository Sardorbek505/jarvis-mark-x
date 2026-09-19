"""Разбор вызова инструмента: что где происходит.

`_execute_tool` была на 311 строк и держала в себе сразу всё: подтверждение
необратимого, мгновенную фразу, диспетчер десяти встроенных инструментов,
реестр действий, плагины и обновление профиля пользователя. Ошибка в любой
из этих частей требовала прочитать остальные пять.

Части разнесены, и теперь у каждой можно спросить поведение отдельно —
именно это здесь и проверяется. Заодно сюда попало то, чего не проверял
никто: что неизвестное имя не выдаётся за успех и что «посмотрел погоду»
не записывается человеку в занятия.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

import main as jarvis_main


class _Окно:
    muted = False

    def __init__(self):
        self.логи: list[str] = []
        self.состояния: list[str] = []

    def write_log(self, текст):
        self.логи.append(текст)

    def set_state(self, состояние):
        self.состояния.append(состояние)

    def lock_on(self, имя):
        pass


class _Профиль:
    def __init__(self):
        self.занятие = None
        self.история: list[tuple[str, str]] = []
        self._контекст = {"last_emotion": "neutral"}

    def update_context(self, **поля):
        self._контекст.update(поля)
        if "activity" in поля:
            self.занятие = поля["activity"]

    def get_context(self):
        return dict(self._контекст)

    def add_to_history(self, ключ, значение):
        self.история.append((ключ, значение))


class _Прогноз:
    def __init__(self):
        self.записи: list[tuple[str, dict]] = []

    def record_action(self, имя, контекст):
        self.записи.append((имя, контекст))


def _джарвис() -> jarvis_main.Jarvis:
    j = jarvis_main.Jarvis.__new__(jarvis_main.Jarvis)
    j.ui = _Окно()
    j.user_profile = _Профиль()
    j.proactive_engine = _Прогноз()
    return j


# ─── Чем человек занят ───────────────────────────────────────────────────────

def test_фильм_попадает_в_занятие_и_в_историю():
    j = _джарвис()

    j._note_activity("movie_player", {"action": "play", "title": "Интерстеллар"})

    assert j.user_profile.занятие == "watching_movie: Интерстеллар"
    assert j.user_profile.история == [("recent_movies", "Интерстеллар")]


def test_музыка_попадает_в_занятие_и_в_историю():
    j = _джарвис()

    j._note_activity("music_player", {"action": "play", "query": "Radiohead"})

    assert j.user_profile.занятие == "listening_music: Radiohead"
    assert j.user_profile.история == [("recent_music", "Radiohead")]


def test_пауза_фильма_не_начинает_просмотр():
    """«Поставь на паузу» — не «смотрит фильм»: названия там нет вовсе."""
    j = _джарвис()

    j._note_activity("movie_player", {"action": "pause"})

    assert j.user_profile.занятие == "movie_player"
    assert j.user_profile.история == []


def test_включение_без_названия_не_пишется_в_историю():
    """Пустая строка в списке «недавно смотрел» хуже пустого списка."""
    j = _джарвис()

    j._note_activity("music_player", {"action": "play", "query": ""})

    assert j.user_profile.история == []


def test_режим_становится_занятием():
    j = _джарвис()
    j._note_activity("set_mode", {"mode": "работа"})
    assert j.user_profile.занятие == "mode_работа"


@pytest.mark.parametrize("инструмент", ["weather", "files", "web_search",
                                        "recall_memory", "clipboard"])
def test_обычные_инструменты_занятием_не_считаются(инструмент):
    """«Посмотрел погоду» — не деятельность, и засорять ею профиль значит
    обесценить те три записи, которые что-то значат."""
    j = _джарвис()

    j._note_activity(инструмент, {"action": "play"})

    assert j.user_profile.занятие is None
    assert j.proactive_engine.записи == []


def test_занятие_попадает_в_прогноз_со_временем_и_режимом():
    j = _джарвис()

    j._note_activity("set_mode", {"mode": "отдых"})

    имя, контекст = j.proactive_engine.записи[0]
    assert имя == "set_mode"
    assert set(контекст) == {"time", "emotion", "mode"}


# ─── Диспетчер встроенных инструментов ───────────────────────────────────────

@pytest.mark.asyncio
async def test_чужое_имя_возвращает_признак_не_моё():
    """Пустая строка или None значили бы «инструмент отработал и промолчал»,
    и `files` никогда не получил бы управления."""
    j = _джарвис()

    итог = await j._run_inline_tool("files", {"action": "list"})

    assert итог is jarvis_main._НЕ_ИНЛАЙН


@pytest.mark.asyncio
async def test_отмена_без_истории_отвечает_словами():
    from core import undo

    undo.clear()
    j = _джарвис()

    итог = await j._run_inline_tool("undo", {"action": "list"})

    assert "Отменять нечего" in итог


@pytest.mark.asyncio
async def test_встроенный_инструмент_не_путается_с_действием():
    """`recall_memory` объявлен встроенным, `files` — в реестре действий.
    Перепутать их значит либо потерять инструмент, либо выполнить не тот."""
    j = _джарвис()

    свой = await j._run_inline_tool("recall_memory", {"query": "ничего"})
    чужой = await j._run_inline_tool("game_launcher", {"action": "list"})

    assert свой is not jarvis_main._НЕ_ИНЛАЙН
    assert чужой is jarvis_main._НЕ_ИНЛАЙН


# ─── Целиком ─────────────────────────────────────────────────────────────────

def _вызов(имя, **аргументы):
    return SimpleNamespace(id="1", name=имя, args=аргументы)


@pytest.mark.asyncio
async def test_неизвестное_имя_не_выдаётся_за_успех(monkeypatch):
    """Модель может назвать инструмент, которого нет (выключённый плагин,
    старое имя). «Готово» в ответ на это — худшее, что можно сказать."""
    j = _джарвис()
    monkeypatch.setattr(jarvis_main, "get_voice_provider", lambda: "gemini")

    ответ = await j._execute_tool(_вызов("несуществующий_инструмент"))

    assert "Неизвестный инструмент" in ответ.response["result"]


@pytest.mark.asyncio
async def test_упавший_инструмент_не_роняет_ход(monkeypatch):
    """Исключение внутри инструмента должно стать ответом модели, а не
    оборвать приём: оборванный приём — это разрыв сессии."""
    j = _джарвис()
    j.speak_error = lambda имя, беда: None
    monkeypatch.setattr(jarvis_main, "get_voice_provider", lambda: "gemini")

    async def _взорвать(name, args):
        raise RuntimeError("внутри всё сломалось")

    j._run_inline_tool = _взорвать

    ответ = await j._execute_tool(_вызов("web_search", query="что-нибудь"))

    assert "Ошибка инструмента" in ответ.response["result"]


@pytest.mark.asyncio
async def test_после_инструмента_окно_возвращается_к_прослушиванию(monkeypatch):
    j = _джарвис()
    monkeypatch.setattr(jarvis_main, "get_voice_provider", lambda: "gemini")

    async def _ответ(name, args):
        return "готово"

    j._run_inline_tool = _ответ

    await j._execute_tool(_вызов("recall_memory", query="х"))

    assert j.ui.состояния[0] == "THINKING"
    assert j.ui.состояния[-1] == "LISTENING"
