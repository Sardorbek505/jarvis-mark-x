"""Подтверждение необратимого действия обязан выдавать человек, а не модель.

Прежний гейт считал подтверждением ПОВТОРНЫЙ вызов того же инструмента в
течение 90 секунд. Модели было велено переспросить пользователя вслух, но
ничто не проверяло, что человек ответил: два вызова подряд модель делает сама.
Здесь проверяется главное свойство нового гейта — токен нельзя получить иначе,
как через `resolve()`, а туда ведёт только нажатие кнопки в окне.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import confirm


class _Окно:
    """Минимальный интерфейс: помнит, что показывали, и что ушло в сессию."""

    def __init__(self):
        self.показано: list[tuple[str, str]] = []
        self.скрыто = 0
        self.в_сессию: list[str] = []
        self.лог: list[str] = []

    def show(self, title, detail):
        self.показано.append((title, detail))

    def hide(self):
        self.скрыто += 1

    def notify(self, text):
        self.в_сессию.append(text)


@pytest.fixture
def окно():
    confirm.reset()
    w = _Окно()
    confirm.bind(show=w.show, hide=w.hide, notify=w.notify, log=w.лог.append)
    yield w
    confirm.reset()
    confirm.bind(show=None, hide=None)
    confirm._show_cb = None


def test_без_интерфейса_гейт_недоступен():
    confirm.reset()
    confirm._show_cb = None
    assert confirm.available() is False
    # И просить не о чем: пустая строка означает «подтвердить негде».
    assert confirm.request("computer_control/shutdown", "Выключение") == ""


def test_запрос_показывает_баннер_и_ничего_не_выполняет(окно):
    ответ = confirm.request("computer_control/shutdown", "Выключение компьютера", "action=shutdown")

    assert окно.показано == [("Выключение компьютера", "action=shutdown")]
    assert "ПОДТВЕРЖДЕНИЕ" in ответ
    # Токена нет — значит, вызывающий код действие не выполнит.
    assert confirm.consume("computer_control/shutdown") is False


def test_модель_не_может_выдать_себе_токен_повторным_запросом(окно):
    """Ровно та дыра, которая была в прежнем гейте."""
    for _ in range(5):
        confirm.request("computer_control/shutdown", "Выключение компьютера")
        assert confirm.consume("computer_control/shutdown") is False


def test_нажатие_подтвердить_выдаёт_токен_ровно_один_раз(окно):
    confirm.request("computer_control/shutdown", "Выключение компьютера")
    confirm.resolve(True)

    assert окно.скрыто == 1
    assert confirm.consume("computer_control/shutdown") is True
    # Второе необратимое действие требует своего нажатия.
    assert confirm.consume("computer_control/shutdown") is False


def test_отмена_токена_не_даёт(окно):
    confirm.request("files/delete", "Удаление файла")
    confirm.resolve(False)

    assert confirm.consume("files/delete") is False
    assert окно.в_сессию, "модель должна узнать об отказе"
    assert "ОТМЕНА" in окно.в_сессию[-1]


def test_решение_человека_уходит_в_сессию(окно):
    confirm.request("computer_control/restart", "Перезагрузка компьютера")
    confirm.resolve(True)

    assert len(окно.в_сессию) == 1
    assert "ПОДТВЕРДИТЬ" in окно.в_сессию[0]


def test_токен_не_подходит_к_другому_действию(окно):
    confirm.request("computer_control/restart", "Перезагрузка компьютера")
    confirm.resolve(True)

    assert confirm.consume("computer_control/shutdown") is False
    assert confirm.consume("files/delete") is False
    assert confirm.consume("computer_control/restart") is True


def test_протухший_токен_не_срабатывает(окно, monkeypatch):
    confirm.request("computer_control/shutdown", "Выключение компьютера")
    confirm.resolve(True)

    настоящее_время = confirm.time.time
    monkeypatch.setattr(
        confirm.time, "time",
        lambda: настоящее_время() + confirm.TOKEN_TTL_SEC + 1,
    )
    assert confirm.consume("computer_control/shutdown") is False


def test_второй_баннер_не_перекрывает_первый(окно):
    confirm.request("computer_control/shutdown", "Выключение компьютера")
    ответ = confirm.request("files/delete", "Удаление файла")

    assert "ЗАНЯТО" in ответ
    assert len(окно.показано) == 1, "нельзя подменять то, что человек уже читает"
    # И подтверждение первого не выдаёт токен второму.
    confirm.resolve(True)
    assert confirm.consume("files/delete") is False
    assert confirm.consume("computer_control/shutdown") is True


def test_ожидание_протухает(окно, monkeypatch):
    confirm.request("computer_control/shutdown", "Выключение компьютера")
    assert confirm.pending_title() == "Выключение компьютера"

    настоящее_время = confirm.time.time
    monkeypatch.setattr(
        confirm.time, "time",
        lambda: настоящее_время() + confirm.PENDING_TTL_SEC + 1,
    )
    assert confirm.pending_title() == ""


def test_сорванный_показ_не_оставляет_ожидание():
    confirm.reset()

    def падает(title, detail):
        raise RuntimeError("окно закрылось")

    confirm.bind(show=падает, hide=lambda: None)
    ответ = confirm.request("computer_control/shutdown", "Выключение компьютера")

    assert ответ == "", "нет баннера — нет и разрешения выполнять"
    assert confirm.pending_title() == ""
    confirm.reset()


# ─── Гейт внутри Jarvis: что именно получает модель ───────────────────────────

import main as jarvis_main


class _ОкноJarvis(_Окно):
    muted = False
    supports_confirm = True

    def set_state(self, state):
        pass

    def write_log(self, text):
        self.лог.append(text)


def _джарвис(окно):
    j = jarvis_main.Jarvis.__new__(jarvis_main.Jarvis)
    j.ui = окно
    j._pending_destructive = None
    return j


@pytest.fixture
def джарвис_с_окном():
    confirm.reset()
    окно = _ОкноJarvis()
    confirm.bind(show=окно.show, hide=окно.hide, notify=окно.notify, log=окно.write_log)
    yield _джарвис(окно), окно
    confirm.reset()
    confirm._show_cb = None


def test_выключение_без_нажатия_не_проходит(джарвис_с_окном):
    j, окно = джарвис_с_окном
    ответ = j._confirm_destructive("computer_control/shutdown", "computer_control",
                                   {"action": "shutdown"})

    assert ответ is not None, "None означало бы «выполняй» — а кнопку никто не нажимал"
    assert окно.показано[0][0] == "Выключение компьютера"


def test_после_нажатия_действие_пропускается(джарвис_с_окном):
    j, _ = джарвис_с_окном
    j._confirm_destructive("computer_control/shutdown", "computer_control", {"action": "shutdown"})
    confirm.resolve(True)

    assert j._confirm_destructive("computer_control/shutdown", "computer_control",
                                  {"action": "shutdown"}) is None
    # И ровно один раз: следующий вызов снова упирается в баннер.
    assert j._confirm_destructive("computer_control/shutdown", "computer_control",
                                  {"action": "shutdown"}) is not None


def test_запасной_гейт_работает_без_окна():
    """Headless: нажать негде, но и молча выключать машину нельзя."""
    confirm.reset()
    confirm._show_cb = None
    j = _джарвис(_ОкноJarvis())

    первый = j._confirm_destructive("files/delete", "files", {"action": "delete"})
    второй = j._confirm_destructive("files/delete", "files", {"action": "delete"})

    assert первый is not None, "первый вызов обязан упереться в подтверждение"
    assert второй is None, "повторный вызов в окне 90 секунд — прежнее правило"


def test_поле_запасного_гейта_создаётся_в_init():
    """`self._pending_destructive` читался, но нигде не создавался: первое же
    «выключи компьютер» падало с AttributeError прямо в приёмном цикле — то
    есть гейт не защищал, а ронял сессию."""
    import ast

    дерево = ast.parse((_BASE / "main.py").read_text(encoding="utf-8"))
    def _цели(узел):
        if isinstance(узел, ast.Assign):
            return узел.targets
        if isinstance(узел, ast.AnnAssign):      # self._x: T | None = None
            return [узел.target]
        return []

    присвоено = {
        цель.attr
        for функция in ast.walk(дерево)
        if isinstance(функция, ast.FunctionDef) and функция.name == "__init__"
        for вложенный in ast.walk(функция)
        for цель in _цели(вложенный)
        if isinstance(цель, ast.Attribute) and isinstance(цель.value, ast.Name)
        and цель.value.id == "self"
    }

    assert "_pending_destructive" in присвоено
    assert "_resume_handle" in присвоено
