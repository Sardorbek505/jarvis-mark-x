"""Брифинг обязан знать, как зовут человека, если он это говорил.

`_load_memory()` читала `memory/data.json` и брала `data.get("name")`, хотя
файл устроен как `{категория: {ключ: {"value": ...}}}` — имя лежит в
`identity/name`. Значит, условие не срабатывало никогда: брифинг всегда
здоровался «сэр» и всегда определял город по IP, даже когда город был назван
вслух и сохранён.
"""

import json
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# Не `from actions import morning_briefing`: пакет реэкспортирует функцию с
# тем же именем, и импорт вернул бы её вместо модуля.
from importlib import import_module

mb = import_module("actions.morning_briefing")


@pytest.fixture
def память(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "_BASE", tmp_path)
    (tmp_path / "memory").mkdir()

    def записать(содержимое):
        (tmp_path / "memory" / "data.json").write_text(
            json.dumps(содержимое, ensure_ascii=False), encoding="utf-8")
    return записать


def test_имя_и_город_берутся_из_памяти(память):
    память({"identity": {
        "name": {"value": "Сардорбек"},
        "city": {"value": "Ташкент"},
    }})

    данные = mb._load_memory()

    assert данные["name"] == "Сардорбек"
    assert данные["city"] == "Ташкент"


def test_старый_плоский_формат_тоже_читается(память):
    """Записи могли лечь до появления обёртки со временем."""
    память({"identity": {"name": "Сардорбек"}})
    assert mb._load_memory()["name"] == "Сардорбек"


def test_без_города_поле_не_появляется(память):
    """Пустой город отключил бы автоопределение по IP и вернул бы «Москву»."""
    память({"identity": {"name": "Сардорбек"}})

    данные = mb._load_memory()

    assert "city" not in данные


def test_пустая_память_даёт_обращение_по_умолчанию(память):
    память({})
    assert mb._load_memory() == {"name": "сэр"}


def test_битый_файл_не_роняет_брифинг(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "_BASE", tmp_path)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "data.json").write_text("{не json", encoding="utf-8")

    assert mb._load_memory() == {"name": "сэр"}


def test_посторонние_категории_не_мешают(память):
    память({
        "preferences": {"name": {"value": "не то имя"}},
        "identity": {"name": {"value": "Сардорбек"}},
    })

    assert mb._load_memory()["name"] == "Сардорбек"
