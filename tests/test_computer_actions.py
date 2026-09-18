"""Управление компьютером: 43 действия вместо девяти, и опечатка — не тупик.

Модели предлагалось девять имён, а всё остальное она должна была уложить в
свободное описание, которое разбиралось поиском подстрок. «Закрой вкладку»,
«верни масштаб», «пролистай вниз» не попадали никуда и получали «Действие не
распознано» — то есть ассистент отказывался от команды, которую понял бы любой
человек.

Клавиши здесь никто не нажимает: проверяется разбор имён, выбор аккорда и
поведение, когда нажать некому.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from actions import computer_settings as cs


@pytest.fixture
def клавиатура(monkeypatch):
    """Ловит отправленные аккорды вместо настоящих нажатий."""
    нажато = []
    from actions import keyboard

    monkeypatch.setattr(keyboard, "send_combo", lambda c: нажато.append(c) or True)
    monkeypatch.setattr(keyboard, "type_text", lambda t: нажато.append(("текст", t)) or True)
    return нажато


@pytest.fixture
def глухая_клавиатура(monkeypatch):
    """Ни pyautogui, ни SendKeys — нажимать некому."""
    from actions import keyboard

    monkeypatch.setattr(keyboard, "send_combo", lambda c: False)
    monkeypatch.setattr(keyboard, "type_text", lambda t: False)


# ─── Разбор имени действия ────────────────────────────────────────────────────

def test_действий_стало_заметно_больше():
    assert len(cs.ALL_ACTIONS) >= 40, "ради девяти имён всё это не затевалось"


@pytest.mark.parametrize("имя", ["copy", "paste", "new_tab", "zoom_in",
                                 "scroll_down", "go_back", "refresh", "type_text"])
def test_точное_имя_принимается(имя):
    действие, беда = cs._разобрать_действие(имя)
    assert действие == имя and not беда


@pytest.mark.parametrize("опечатка,ожидание", [
    ("zom_in", "zoom_in"),
    ("scrol_down", "scroll_down"),
    ("selct_all", "select_all"),
    ("new tab", "new_tab"),
    ("NEW_TAB", "new_tab"),
    ("go-back", "go_back"),
])
def test_опечатка_чинится_локально(опечатка, ожидание):
    """difflib за микросекунды — без второго обращения к модели, которое
    стоило бы полного круга по сети."""
    assert cs._разобрать_действие(опечатка)[0] == ожидание


@pytest.mark.parametrize("синоним,ожидание", [
    ("back", "go_back"), ("reload", "refresh"), ("page_down", "scroll_down"),
    ("назад", "go_back"), ("копировать", "copy"), ("скриншот", "screenshot"),
])
def test_синонимы_и_русские_имена(синоним, ожидание):
    """Похожесть строк не помогает: «назад» и «go_back» не похожи ничем."""
    assert cs._разобрать_действие(синоним)[0] == ожидание


def test_чужое_слово_не_превращается_наугад():
    """Порог difflib подобран так, чтобы «музыка» не стала случайным действием."""
    действие, беда = cs._разобрать_действие("музыка")

    assert действие == ""
    assert "не знаю действия" in беда


def test_отказ_называет_настоящие_имена():
    """Тупик без вариантов хуже ошибки: следующий вызов должен попасть."""
    _, беда = cs._разобрать_действие("абракадабра")

    assert "Похоже на:" in беда
    названные = [и for и in cs.ALL_ACTIONS if и in беда]
    assert названные, "в подсказке должны быть настоящие имена"


def test_пустое_имя_не_разбирается():
    assert cs._разобрать_действие("")[0] == ""


# ─── Выполнение ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("действие,аккорд", [
    ("copy", "ctrl+c"),
    ("paste", "ctrl+v"),
    ("select_all", "ctrl+a"),
    ("new_tab", "ctrl+t"),
    ("reopen_tab", "ctrl+shift+t"),
    ("go_back", "alt+left"),
    ("zoom_reset", "ctrl+0"),
    ("scroll_bottom", "ctrl+end"),
    ("full_screen", "f11"),
])
def test_действие_отправляет_свой_аккорд(клавиатура, действие, аккорд):
    ответ = cs._computer_control_tool({"action": действие})

    assert клавиатура == [аккорд]
    assert "Готово" in ответ


def test_опечатка_доходит_до_выполнения(клавиатура):
    cs._computer_control_tool({"action": "zom_in"})
    assert клавиатура == ["ctrl+="]


def test_ввод_текста(клавиатура):
    cs._computer_control_tool({"action": "type_text", "value": "привет"})
    assert клавиатура == [("текст", "привет")]


def test_ввод_без_текста_переспрашивают(клавиатура):
    assert "Что напечатать" in cs._computer_control_tool({"action": "type_text"})
    assert клавиатура == []


def test_некому_нажимать_говорится_вслух(глухая_клавиатура):
    """Молча не нажатая комбинация выглядит как «Джарвис не послушался»."""
    ответ = cs._computer_control_tool({"action": "copy"})

    assert "Не смог" in ответ
    assert "ctrl+c" in ответ


def test_громкость_идёт_прежним_путём(monkeypatch):
    вызовы = []
    monkeypatch.setattr(cs, "computer_settings",
                        lambda p, player=None: вызовы.append(p) or "ок")

    cs._computer_control_tool({"action": "volume_up"})

    assert вызовы and вызовы[0]["action"] == "увеличить громкость"
    assert вызовы[0]["value"] == "10", "шаг по умолчанию должен подставляться"


def test_тёмная_тема_только_windows(monkeypatch):
    monkeypatch.setattr(cs, "_OS", "Linux")
    assert "только в Windows" in cs._computer_control_tool({"action": "dark_mode"})


# ─── Аккорды в язык SendKeys ──────────────────────────────────────────────────

@pytest.mark.parametrize("аккорд,ожидание", [
    (["ctrl", "c"], "^c"),
    (["ctrl", "shift", "t"], "^+t"),
    (["alt", "left"], "%{LEFT}"),
    (["f5"], "{F5}"),
])
def test_перевод_в_sendkeys(аккорд, ожидание):
    from actions.keyboard import _to_sendkeys

    assert _to_sendkeys(аккорд) == ожидание


def test_невыразимый_аккорд_честно_отказывает():
    """Win в SendKeys не выражается — лучше сказать «не смог», чем нажать не то."""
    from actions.keyboard import _to_sendkeys

    assert _to_sendkeys(["win", "d"]) is None


# ─── Объявление ───────────────────────────────────────────────────────────────

def test_все_имена_названы_модели():
    """Модель выбирает из списка, а не сочиняет: это и убирает «не распознано»."""
    описание = cs.TOOL["parameters"]["properties"]["action"]["description"]

    пропущены = [и for и in cs.ALL_ACTIONS if и not in описание]
    assert not пропущены, f"не названы модели: {пропущены}"


def test_объявление_предупреждает_про_подтверждение():
    описание = cs.TOOL["description"]

    assert "НЕ выполнено" in описание, "модель не должна рапортовать за кнопку"
    assert "undo" in описание
