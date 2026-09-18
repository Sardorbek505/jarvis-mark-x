"""Обои по брошенной картинке — и отмена, которая возвращает именно прежние.

Смысл этого инструмента в одной связке: человек бросает картинку в окно и
говорит «поставь на обои», не называя пути. Проверяется она — и то, что
отмена не подменяет прежние обои догадкой.

Системные вызовы (SystemParametersInfoW, osascript, gsettings) здесь
подменены: в контейнере нет ни рабочего стола, ни этих команд. Проверяется
логика вокруг них — что именно и когда вызывается.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from actions import desktop as dt
from core import undo


class _Окно:
    def __init__(self, брошенный=""):
        self.current_file = брошенный
        self.лог: list[str] = []

    def write_log(self, текст):
        self.лог.append(текст)


@pytest.fixture
def стол(monkeypatch):
    """Система, которая принимает обои и помнит, какие стоят."""
    состояние = {"обои": None, "поставлено": [], "свёрнуто": 0}

    monkeypatch.setattr(dt, "_текущие_обои", lambda: состояние["обои"])

    def _поставить(путь):
        состояние["поставлено"].append(str(путь))
        состояние["обои"] = str(путь)
        return True

    monkeypatch.setattr(dt, "_поставить_обои", _поставить)
    undo.clear()
    yield состояние
    undo.clear()


@pytest.fixture
def картинка(tmp_path):
    файл = tmp_path / "закат.jpg"
    файл.write_bytes(b"\xff\xd8\xff")
    return файл


# ─── Обои ────────────────────────────────────────────────────────────────────

def test_брошенная_картинка_ставится_без_пути(стол, картинка):
    """Ради этого всё и затевалось: бросил в окно — сказал «на обои»."""
    окно = _Окно(брошенный=str(картинка))

    ответ = dt.desktop({"action": "wallpaper"}, player=окно)

    assert стол["поставлено"] == [str(картинка.resolve())]
    assert "закат.jpg" in ответ


def test_без_картинки_инструмент_переспрашивает(стол):
    ответ = dt.desktop({"action": "wallpaper"}, player=_Окно())
    assert стол["поставлено"] == []
    assert "?" in ответ


def test_не_картинку_обоями_не_ставят(стол, tmp_path):
    документ = tmp_path / "договор.pdf"
    документ.write_bytes(b"%PDF")

    ответ = dt.desktop({"action": "wallpaper", "path": str(документ)})

    assert стол["поставлено"] == []
    assert "не картинка" in ответ


def test_несуществующий_путь_говорится_прямо(стол, tmp_path):
    ответ = dt.desktop({"action": "wallpaper", "path": str(tmp_path / "нет.jpg")})
    assert стол["поставлено"] == []
    assert "нет по пути" in ответ


def test_отказ_системы_не_выдаётся_за_успех(стол, картинка, monkeypatch):
    monkeypatch.setattr(dt, "_поставить_обои", lambda путь: False)

    ответ = dt.desktop({"action": "wallpaper", "path": str(картинка)})

    assert "не вышло" in ответ


# ─── Отмена ──────────────────────────────────────────────────────────────────

def test_отмена_возвращает_именно_прежние_обои(стол, картинка, tmp_path):
    прежние = tmp_path / "горы.png"
    прежние.write_bytes(b"\x89PNG")
    стол["обои"] = str(прежние)

    dt.desktop({"action": "wallpaper", "path": str(картинка)})
    undo.undo_last()

    assert стол["поставлено"][-1] == str(прежние)


def test_без_прежних_обоев_отмена_не_обещается(стол, картинка):
    """Отмена, ставящая наугад выбранную картинку, хуже её отсутствия.
    Но и молчать нельзя: человек рассчитывает на «отмени»."""
    стол["обои"] = None

    ответ = dt.desktop({"action": "wallpaper", "path": str(картинка)})

    assert undo.can_undo() is False
    assert "вернуть" in ответ.lower()


def test_исчезнувшие_прежние_обои_не_попадают_в_отмену(стол, картинка, tmp_path):
    """Система назвала путь, а файла там уже нет: вернуть нечего."""
    стол["обои"] = str(tmp_path / "удалённые.jpg")

    dt.desktop({"action": "wallpaper", "path": str(картинка)})

    assert undo.can_undo() is False


def test_повтор_тех_же_обоев_не_плодит_отмену(стол, картинка):
    стол["обои"] = str(картинка.resolve())

    dt.desktop({"action": "wallpaper", "path": str(картинка)})

    assert undo.can_undo() is False


# ─── Свернуть всё ────────────────────────────────────────────────────────────

def test_неотправленное_сочетание_не_выдаётся_за_сделанное(monkeypatch):
    """Молча не нажатая комбинация выглядит как «Джарвис меня не послушался»."""
    monkeypatch.setattr(dt, "_OS", "Windows")
    import actions.keyboard as kb
    monkeypatch.setattr(kb, "send_combo", lambda combo: False)

    ответ = dt.desktop({"action": "show"})

    assert "не вышло" in ответ


def test_отсутствие_wmctrl_объясняется(monkeypatch):
    """В Linux общего способа свернуть окна нет. Отправить сочетание в
    пустоту и отчитаться о сделанном — худшее из решений."""
    monkeypatch.setattr(dt, "_OS", "Linux")
    monkeypatch.setattr(dt.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))

    ответ = dt.desktop({"action": "show"})

    assert "wmctrl" in ответ
