"""Что Джарвис обо мне помнит — и как это забыть, не зная имени факта.

Долгосрочная память копилась молча: ассистент записывал факты сам, а увидеть
их можно было только в `memory/data.json`, куда человек не полезет. Забыть
отдельный факт голосом было можно — но лишь если помнить, КАК он назван,
а именно этого человек и не знает.

`all_entries()` и `forget()` были написаны ещё под индекс памяти и ждали
применения. Здесь проверяется панель, которая их наконец показывает.
"""

import json
import os
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# Плагин платформы выбирается при создании QApplication, а не при импорте, так
# что поставить это здесь достаточно — и без этого тест падал бы на машине
# сборки, где нет экрана.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import memory.memory_manager as mm  # noqa: E402


# ─── Отбор: почему не `_score` ───────────────────────────────────────────────

def _факт(категория, ключ, значение):
    return {"category": категория, "key": ключ, "value": значение, "at": 0.0}


def test_пустой_запрос_ничего_не_прячет():
    записи = [_факт("identity", "имя", "Сардорбек")]
    assert mm.filter_entries(записи, "") == записи
    assert mm.filter_entries(записи, "   ") == записи


def test_отбор_смотрит_в_ключ_значение_и_категорию():
    записи = [
        _факт("identity", "имя", "Сардорбек"),
        _факт("preferences", "кофе", "без сахара"),
        _факт("notes", "машина", "заменить масло"),
    ]
    assert len(mm.filter_entries(записи, "кофе")) == 1
    assert len(mm.filter_entries(записи, "сахар")) == 1
    assert len(mm.filter_entries(записи, "notes")) == 1
    assert mm.filter_entries(записи, "щщщ") == []


def test_отбор_не_ранжирует_а_сужает():
    """Человек, набирающий в поле фильтра, видит список и сужает его буква за
    буквой. Взвешивание слов в этот момент выглядит как пропажа строк."""
    записи = [
        _факт("notes", "молоко", "купить"),
        _факт("notes", "заметка", "молоко закончилось"),
    ]
    assert len(mm.filter_entries(записи, "молоко")) == 2


def test_регистр_не_имеет_значения():
    записи = [_факт("identity", "Имя", "Сардорбек")]
    assert len(mm.filter_entries(записи, "СаРдОр")) == 1


# ─── Сама панель ─────────────────────────────────────────────────────────────

@pytest.fixture
def qt():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def память(tmp_path, monkeypatch):
    файл = tmp_path / "data.json"
    monkeypatch.setattr(mm, "_MEMORY_FILE", файл)

    def записать(данные: dict):
        файл.write_text(json.dumps(данные, ensure_ascii=False), encoding="utf-8")

    записать({
        "identity":    {"имя": {"value": "Сардорбек", "at": 3}},
        "preferences": {"кофе": {"value": "без сахара", "at": 2}},
        "notes":       {"машина": {"value": "заменить масло", "at": 1}},
    })
    return записать


@pytest.fixture
def панель(qt, память, monkeypatch):
    """Открывает панель и отдаёт диалог, не входя в цикл событий."""
    from PyQt6.QtWidgets import QDialog, QWidget
    from ui import MainWindow

    поймано = {}
    monkeypatch.setattr(QDialog, "exec", lambda self: поймано.setdefault("диалог", self))

    class _Хозяин(QWidget):
        _open_memory_panel = MainWindow._open_memory_panel

        def __init__(self):
            super().__init__()
            self.лог: list[str] = []

        def write_log(self, текст):
            self.лог.append(текст)

    хозяин = _Хозяин()
    хозяин._open_memory_panel()
    окно = поймано["диалог"]
    окно.хозяин = хозяин
    return окно


def _строки(окно) -> list[str]:
    from PyQt6.QtWidgets import QLabel
    # Только живые: убранные из раскладки строки отвязываются от родителя
    # сразу, не дожидаясь прохода цикла событий.
    return [w.text() for w in окно.findChildren(QLabel)
            if w.parent() is not None and ": " in w.text()
            and not w.text().startswith(("ФАКТОВ", "НАЙДЕНО"))]


def _итог(окно) -> str:
    from PyQt6.QtWidgets import QLabel
    return next(w.text() for w in окно.findChildren(QLabel)
                if w.text().startswith(("ФАКТОВ", "НАЙДЕНО", "НИЧЕГО", "ПАМЯТЬ")))


def _поле(окно):
    from PyQt6.QtWidgets import QLineEdit
    return окно.findChild(QLineEdit)


def _крестики(окно):
    from PyQt6.QtWidgets import QPushButton
    return [b for b in окно.findChildren(QPushButton)
            if b.text() == "✕" and b.parent() is not None]


def test_панель_показывает_все_факты(панель):
    строки = _строки(панель)
    assert "имя: Сардорбек" in строки
    assert "кофе: без сахара" in строки
    assert "машина: заменить масло" in строки
    assert _итог(панель) == "ФАКТОВ: 3"


def test_поиск_сужает_список_на_лету(панель):
    _поле(панель).setText("кофе")

    assert _строки(панель) == ["кофе: без сахара"]
    assert _итог(панель) == "НАЙДЕНО: 1 ИЗ 3"


def test_ненайденное_отличается_от_пустой_памяти(панель):
    """«Пусто» и «не нашлось» — разные новости: в первом случае искать
    нечего, во втором стоит стереть запрос."""
    _поле(панель).setText("щщщ")
    assert _итог(панель) == "НИЧЕГО НЕ НАЙДЕНО"


def test_пустая_память_говорит_об_этом(qt, память, monkeypatch, панель):
    память({})
    # Пробел, а не пустая строка: поле уже пусто, и setText("") не вызвал бы
    # перерисовку вовсе. Фильтр пробел и пустоту не различает.
    _поле(панель).setText(" ")
    assert _итог(панель) == "ПАМЯТЬ ПУСТА"
    assert _строки(панель) == []


def test_крестик_забывает_факт_насовсем(панель):
    _поле(панель).setText("кофе")
    _крестики(панель)[0].click()

    assert [з["key"] for з in mm.all_entries()] == ["имя", "машина"]
    assert any("забыл" in с for с in панель.хозяин.лог)


def test_после_забывания_список_перерисовывается(панель):
    _поле(панель).setText("кофе")
    _крестики(панель)[0].click()

    assert _строки(панель) == []
    assert _итог(панель) == "НИЧЕГО НЕ НАЙДЕНО"


def test_одноимённый_ключ_в_чужой_категории_остаётся(qt, память, панель):
    """Один и тот же ключ живёт в двух категориях. Забыть чужое молча —
    хуже, чем не забыть ничего, поэтому категория передаётся обязательно."""
    память({
        "notes":       {"машина": {"value": "заменить масло", "at": 2}},
        "preferences": {"машина": {"value": "люблю механику", "at": 1}},
    })
    _поле(панель).setText("масло")
    _крестики(панель)[0].click()

    осталось = {(з["category"], з["key"]) for з in mm.all_entries()}
    assert осталось == {("preferences", "машина")}


def test_кнопка_памяти_есть_в_окне():
    """Кнопка без обработчика — это кнопка, о которой узнают нажатием."""
    from ui import MainWindow
    assert callable(getattr(MainWindow, "_open_memory_panel", None))
