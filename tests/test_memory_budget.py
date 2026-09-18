"""Память не должна расти в системной инструкции линейно по числу фактов.

`format_memory_for_prompt` вываливала в промпт ВСЮ память целиком, и каждый
реконнект — а их за вечер несколько — тащил простыню, за которую платят
токенами и задержкой первого слова.

Здесь проверяются три свойства: в промпт едет ядро, ключи остального видны в
индексе (иначе модель не может искать то, о чём не знает), а сам поиск
находит факт, не попавший в промпт.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from memory import memory_manager as mm


@pytest.fixture(autouse=True)
def свой_файл(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "_MEMORY_FILE", tmp_path / "data.json")


def _много_фактов(сколько: int) -> None:
    for i in range(сколько):
        mm.update_memory({"preferences": {f"предпочтение_{i}": {"value": f"значение {i}"}}})


def test_промпт_не_растёт_вместе_с_памятью():
    mm.update_memory({"identity": {"name": {"value": "Сардорбек"}}})
    _много_фактов(120)

    текст = mm.format_memory_for_prompt(mm.load_memory())

    assert len(текст) < mm.PROMPT_BUDGET_CHARS * 2.5, (
        f"ядро расползлось: {len(текст)} символов на 121 факте"
    )


def test_личность_попадает_в_промпт_целиком():
    mm.update_memory({"identity": {
        "name": {"value": "Сардорбек"},
        "city": {"value": "Ташкент"},
    }})
    _много_фактов(120)

    текст = mm.format_memory_for_prompt(mm.load_memory())

    assert "Сардорбек" in текст, "без имени ассистент бесполезен, сколько бы он ни помнил"
    assert "Ташкент" in текст


def test_ключи_невлезших_фактов_видны_в_индексе():
    """Модель не может найти то, о чём не знает."""
    mm.update_memory({"identity": {"name": {"value": "Сардорбек"}}})
    _много_фактов(120)

    текст = mm.format_memory_for_prompt(mm.load_memory())

    assert "[ТАКЖЕ ПОМНЮ]" in текст
    assert "recall_memory" in текст, "индекс обязан говорить, чем его читать"


def test_индекс_перемешивает_категории():
    """Отсортированный так же, как ядро, он вытолкнул бы редкую категорию."""
    for i in range(60):
        mm.update_memory({"preferences": {f"еда_{i}": {"value": f"блюдо {i}"}}})
    mm.update_memory({"relationships": {"aziza_sister": {"value": "старшая сестра"}}})
    for i in range(60):
        mm.update_memory({"preferences": {f"музыка_{i}": {"value": f"группа {i}"}}})

    текст = mm.format_memory_for_prompt(mm.load_memory())

    assert "aziza_sister" in текст, (
        "единственная запись о родственнике утонула среди предпочтений — "
        "ровно то, ради чего индекс и существует"
    )


def test_поиск_находит_то_чего_нет_в_промпте():
    mm.update_memory({"identity": {"name": {"value": "Сардорбек"}}})
    _много_фактов(120)
    mm.update_memory({"relationships": {"aziza_sister": {"value": "старшая сестра"}}})

    найдено = mm.search_memory("aziza")

    assert найдено, "локальный поиск обязан доставать то, что не влезло в бюджет"
    assert найдено[0]["value"] == "старшая сестра"


def test_поиск_по_значению_а_не_только_по_ключу():
    mm.update_memory({"projects": {"jarvis": {"value": "голосовой ассистент на Gemini"}}})
    найдено = mm.search_memory("ассистент")
    assert найдено and найдено[0]["key"] == "jarvis"


def test_пустой_запрос_отдаёт_самое_свежее():
    mm.update_memory({"notes": {"старое": {"value": "давно"}}})
    mm.update_memory({"notes": {"новое": {"value": "только что"}}})

    найдено = mm.search_memory("")

    assert найдено[0]["key"] == "новое"


def test_поиск_без_совпадений_не_выдумывает():
    mm.update_memory({"notes": {"кофе": {"value": "без сахара"}}})
    assert mm.search_memory("квантовая хромодинамика") == []
    assert "ничего" in mm.format_search_results([])


def test_забыть_удаляет_факт():
    mm.update_memory({"notes": {"лишнее": {"value": "удалить"}}})
    assert mm.forget("лишнее") is True
    assert mm.search_memory("лишнее") == []
    assert mm.forget("лишнее") is False


def test_старый_формат_без_времени_читается():
    """В файле могут лежать записи, сохранённые до появления отметки времени."""
    mm.atomic_write_json(mm._MEMORY_FILE, {"identity": {"name": "Сардорбек"}})

    текст = mm.format_memory_for_prompt(mm.load_memory())
    записи = mm.all_entries()

    assert "Сардорбек" in текст
    assert записи[0]["value"] == "Сардорбек"
    assert записи[0]["at"] == 0.0


def test_пустая_память_не_даёт_пустой_заголовок():
    assert mm.format_memory_for_prompt({}) == ""
    assert mm.format_memory_for_prompt({"notes": {}}) == ""


def test_предохранитель_молчит_на_обычном_объёме():
    _много_фактов(50)
    assert mm.over_limit() is False
