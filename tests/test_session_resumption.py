"""Возобновление сессии должно переживать реконнект.

`session_resumption=SessionResumptionConfig()` стоял в конфиге с самого
начала, а хендл, который сервер присылает в ответ, никто не читал. Значит,
каждый реконнект — обрыв сети, смена голоса — начинал ПУСТУЮ сессию: за
«бесконечный разговор» мы платили и не получали его.

Проверяется весь круг: хендл ловится из ответа, возвращается в конфиг при
следующем подключении и сбрасывается, если сервер его не принял.
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

    def write_log(self, text):
        self.логи.append(text)

    def set_state(self, state):
        pass


class _Обрыв(Exception):
    """Конец потока ответов — иначе `while True` в приёме не кончится."""


class _Сессия:
    """Отдаёт заданные ответы, затем обрывается."""

    def __init__(self, ответы):
        self._ответы = ответы
        self._отдано = False

    def receive(self):
        async def поток():
            if self._отдано:
                raise _Обрыв
            self._отдано = True
            for r in self._ответы:
                yield r
            raise _Обрыв
        return поток()


def _ответ(**поля):
    основа = {"data": None, "server_content": None, "tool_call": None,
              "session_resumption_update": None}
    основа.update(поля)
    return SimpleNamespace(**основа)


def _джарвис() -> jarvis_main.Jarvis:
    """Экземпляр без __init__: тесту не нужны ни микрофон, ни телеграм-бот."""
    j = jarvis_main.Jarvis.__new__(jarvis_main.Jarvis)
    j.ui = _Окно()
    j._resume_handle = None
    return j


@pytest.mark.asyncio
async def test_хендл_подхватывается_из_ответа():
    j = _джарвис()
    j.session = _Сессия([
        _ответ(session_resumption_update=SimpleNamespace(resumable=True, new_handle="ручка-1")),
    ])

    with pytest.raises(_Обрыв):
        await j._receive_audio()

    assert j._resume_handle == "ручка-1"


@pytest.mark.asyncio
async def test_берётся_последний_хендл():
    """Сервер переиздаёт хендл по ходу разговора — годен самый свежий."""
    j = _джарвис()
    j.session = _Сессия([
        _ответ(session_resumption_update=SimpleNamespace(resumable=True, new_handle="ручка-1")),
        _ответ(session_resumption_update=SimpleNamespace(resumable=True, new_handle="ручка-2")),
    ])

    with pytest.raises(_Обрыв):
        await j._receive_audio()

    assert j._resume_handle == "ручка-2"


@pytest.mark.asyncio
async def test_невозобновляемое_обновление_игнорируется():
    j = _джарвис()
    j.session = _Сессия([
        _ответ(session_resumption_update=SimpleNamespace(resumable=False, new_handle="ручка")),
    ])

    with pytest.raises(_Обрыв):
        await j._receive_audio()

    assert j._resume_handle is None


def test_хендл_уходит_обратно_в_конфиг(monkeypatch):
    """Смысл всей затеи: при следующем подключении сервер должен получить его."""
    j = _джарвис()
    j._resume_handle = "ручка-1"
    j.user_profile = SimpleNamespace(format_for_prompt=lambda: "")
    monkeypatch.setattr(jarvis_main, "load_memory", dict)
    monkeypatch.setattr(jarvis_main, "format_memory_for_prompt", lambda m: "")
    monkeypatch.setattr(jarvis_main, "_load_system_prompt", lambda: "промпт")
    monkeypatch.setattr(jarvis_main, "get_current_mode", lambda: {"mode": "normal"})

    config = j._build_config()

    assert config.session_resumption.handle == "ручка-1"


def test_отклонённый_хендл_сбрасывается_один_раз():
    j = _джарвис()
    j._resume_handle = "протухшая-ручка"

    сброшено = j._drop_stale_handle(Exception("NOT_FOUND: resumption handle"), "протухшая-ручка")

    assert сброшено is True
    assert j._resume_handle is None
    assert any("новую сессию" in s for s in j.ui.логи)


def test_обрыв_связи_хендл_не_портит():
    """Иначе первая же потеря пакета стоила бы разговора."""
    j = _джарвис()
    j._resume_handle = "живая-ручка"

    сброшено = j._drop_stale_handle(ConnectionResetError("соединение сброшено"), "живая-ручка")

    assert сброшено is False
    assert j._resume_handle == "живая-ручка"


def test_без_хендла_сбрасывать_нечего():
    j = _джарвис()
    assert j._drop_stale_handle(Exception("INVALID_ARGUMENT"), None) is False


def test_уже_обновлённый_хендл_не_трогаем():
    """Пока мы падали, приёмник мог получить свежий — он ни в чём не виноват."""
    j = _джарвис()
    j._resume_handle = "новая-ручка"

    сброшено = j._drop_stale_handle(Exception("NOT_FOUND: handle"), "старая-ручка")

    assert сброшено is False
    assert j._resume_handle == "новая-ручка"
