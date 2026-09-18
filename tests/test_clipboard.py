"""Буфер читается по просьбе — и никогда сам по себе.

Соседний проект показывает панель при каждом копировании. Красиво, и мы
этого сознательно не делаем: следить за буфером значит читать всё, что
человек копирует, включая пароли из менеджера паролей и коды из банковских
сообщений. Ассистент, который видит это постоянно, отличается от того,
который видит это по просьбе, ровно тем, что первому нельзя доверять буфер.

Системные команды здесь подменены: в контейнере нет ни X11, ни Wayland.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import clipboard
from actions.clipboard_tool import clipboard_tool


class _Окно:
    def __init__(self):
        self.лог: list[str] = []

    def write_log(self, текст):
        self.лог.append(текст)


@pytest.fixture
def буфер(monkeypatch):
    """Linux с xclip: самый обычный случай и единственный проверяемый здесь."""
    состояние = {"текст": "", "команды": []}

    monkeypatch.setattr(clipboard, "_OS", "Linux")
    monkeypatch.setattr(clipboard.shutil, "which",
                        lambda имя: f"/usr/bin/{имя}" if имя == "xclip" else None)

    def _run(команда, **kwargs):
        состояние["команды"].append(команда)
        if "-out" in команда:
            return subprocess.CompletedProcess(команда, 0, состояние["текст"], "")
        состояние["текст"] = kwargs.get("input", "")
        return subprocess.CompletedProcess(команда, 0, "", "")

    monkeypatch.setattr(clipboard.subprocess, "run", _run)
    return состояние


# ─── Чтение ──────────────────────────────────────────────────────────────────

def test_скопированное_возвращается_целиком(буфер):
    """Модель переводит и пересказывает сама — ей нужен текст, а не выжимка."""
    буфер["текст"] = "The quick brown fox jumps over the lazy dog."

    ответ = clipboard_tool({"action": "read"})

    assert "The quick brown fox jumps over the lazy dog." in ответ


def test_пустой_буфер_отличается_от_недоступного(буфер):
    буфер["текст"] = "   "
    assert "пусто" in clipboard_tool({"action": "read"})


def test_недоступный_буфер_говорит_чего_не_хватает(monkeypatch):
    """«Ничего не произошло» — худший из возможных ответов: человек не
    узнает, что доставить."""
    monkeypatch.setattr(clipboard, "_OS", "Linux")
    monkeypatch.setattr(clipboard.shutil, "which", lambda имя: None)

    ответ = clipboard_tool({"action": "read"})

    assert "xclip" in ответ


def test_огромный_буфер_обрезается(буфер):
    """Туда попадает и мегабайтная таблица. Уносить её целиком в модель —
    это и деньги, и переполненный контекст."""
    буфер["текст"] = "я" * 50_000

    assert len(clipboard.read()) == clipboard.ПРЕДЕЛ


def test_прочитанное_попадает_в_лог_укороченным(буфер):
    буфер["текст"] = "очень длинная строка " * 40
    окно = _Окно()

    clipboard_tool({"action": "read"}, player=окно)

    assert окно.лог and окно.лог[0].endswith("…")


# ─── Запись ──────────────────────────────────────────────────────────────────

def test_текст_кладётся_в_буфер(буфер):
    ответ = clipboard_tool({"action": "write", "text": "+7 999 123-45-67"})

    assert буфер["текст"] == "+7 999 123-45-67"
    assert "скопировал" in ответ.lower()


def test_пустая_запись_переспрашивает(буфер):
    ответ = clipboard_tool({"action": "write", "text": "  "})

    assert буфер["команды"] == []
    assert "?" in ответ


def test_отказ_команды_не_выдаётся_за_успех(буфер, monkeypatch):
    monkeypatch.setattr(
        clipboard.subprocess, "run",
        lambda команда, **k: subprocess.CompletedProcess(команда, 1, "", "нет дисплея"))

    ответ = clipboard_tool({"action": "write", "text": "что-то"})

    assert "не вышло" in ответ


# ─── Выбор инструмента под систему ───────────────────────────────────────────

def test_wayland_идёт_раньше_x11(monkeypatch):
    """В сеансе Wayland xclip работает только с XWayland — то есть мимо
    настоящего буфера. Обратный порядок означал бы тихую пропажу текста."""
    monkeypatch.setattr(clipboard.shutil, "which", lambda имя: f"/usr/bin/{имя}")

    assert clipboard._первая_доступная(clipboard._LINUX_ЧТЕНИЕ)[0] == "wl-paste"
    assert clipboard._первая_доступная(clipboard._LINUX_ЗАПИСЬ)[0] == "wl-copy"


def test_без_единого_средства_возвращается_none(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda имя: None)
    assert clipboard._первая_доступная(clipboard._LINUX_ЧТЕНИЕ) is None


def test_macos_пользуется_своими_командами(monkeypatch):
    вызовы = []
    monkeypatch.setattr(clipboard, "_OS", "Darwin")
    monkeypatch.setattr(
        clipboard.subprocess, "run",
        lambda команда, **k: вызовы.append(команда) or
        subprocess.CompletedProcess(команда, 0, "текст", ""))

    clipboard.read()
    clipboard.write("что-то")

    assert вызовы == [["pbpaste"], ["pbcopy"]]
