"""Инструмент — это один файл, и реестр обязан это гарантировать.

Раньше добавление инструмента стоило правки трёх мест в main.py: объявления в
списке, ветки в цепочке `if/elif` на три сотни строк и импорта наверху. Забыть
одно из трёх легко, а последствия разные и неочевидные: объявленный без ветки
инструмент модель вызывает, и он отвечает «неизвестный инструмент»;
реализованный без объявления не вызывается никогда и выглядит как «модель им
не пользуется».

Главный тест здесь — последний: всё, что обещано модели, должно быть исполнимо.
"""

import importlib
import itertools
import logging
import sys
import textwrap
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core.action_loader import ActionRegistry, discover_actions


# Свой пакет на каждый тест: Python кэширует загруженные модули в sys.modules,
# и второй тест, положивший другой код под тем же именем, получил бы первый.
_счётчик = itertools.count()


def _модуль(tmp_path, пакет_имя: str, имя: str, текст: str) -> Path:
    пакет = tmp_path / пакет_имя
    пакет.mkdir(exist_ok=True)
    (пакет / "__init__.py").touch()
    (пакет / f"{имя}.py").write_text(textwrap.dedent(текст), encoding="utf-8")
    importlib.invalidate_caches()
    return пакет


@pytest.fixture
def загрузить(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    пакет_имя = f"probes{next(_счётчик)}"

    def сделать(имя, текст, **kw):
        пакет = _модуль(tmp_path, пакет_имя, имя, текст)
        return discover_actions(пакет, package=пакет_имя, **kw)
    return сделать


_ГОДНЫЙ = '''
    def обработчик(parameters, player=None):
        return f"сделано: {parameters.get('что', '')}"

    TOOL = {
        "name": "probe",
        "description": "Делает пробное действие.",
        "parameters": {"type": "OBJECT", "properties": {"что": {"type": "STRING"}}},
        "handler": обработчик,
    }
'''


def test_находит_объявленный_инструмент(загрузить):
    реестр = загрузить("probe", _ГОДНЫЙ)

    assert реестр.has("probe")
    assert реестр.names() == ["probe"]
    assert реестр.run("probe", {"что": "дело"}) == "сделано: дело"


def test_объявление_отдаётся_без_обработчика(загрузить):
    """В Gemini уходит схема, а не питоновская функция."""
    объявления = загрузить("probe", _ГОДНЫЙ).get_tool_declarations()

    assert len(объявления) == 1
    assert set(объявления[0]) == {"name", "description", "parameters"}


def test_модуль_без_TOOL_просто_не_инструмент(загрузить):
    реестр = загрузить("вспомогательный", "СЛУЖЕБНОЕ = 1\n")
    assert len(реестр) == 0


def test_упавший_модуль_не_мешает_остальным(tmp_path, monkeypatch, caplog):
    """Живой ассистент без одного инструмента полезнее, чем не запустившийся."""
    monkeypatch.syspath_prepend(str(tmp_path))
    пакет_имя = f"probes{next(_счётчик)}"
    пакет = _модуль(tmp_path, пакет_имя, "probe", _ГОДНЫЙ)
    (пакет / "битый.py").write_text("это( не питон\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        реестр = discover_actions(пакет, package=пакет_имя)

    assert реестр.names() == ["probe"]
    assert any("битый" in з.getMessage() for з in caplog.records)


@pytest.mark.parametrize("поле,значение,причина", [
    ("name", "не латиница", "недопустимое имя"),   # Gemini принимает только [a-zA-Z_]
    ("name", "", "недопустимое имя"),
    ("description", "   ", "пустое описание"),
    ("parameters", {"type": "OBJECT"}, "properties"),
    ("handler", "не функция", "handler"),
])
def test_кривое_объявление_отклоняется(загрузить, caplog, поле, значение, причина):
    текст = _ГОДНЫЙ.replace(
        {"name": '"name": "probe",',
         "description": '"description": "Делает пробное действие.",',
         "parameters": '"parameters": {"type": "OBJECT", "properties": {"что": {"type": "STRING"}}},',
         "handler": '"handler": обработчик,'}[поле],
        f'"{поле}": {значение!r},',
    )
    with caplog.at_level(logging.WARNING):
        реестр = загрузить("probe", текст)

    assert len(реестр) == 0
    assert any(причина in з.getMessage() for з in caplog.records)


def test_имя_встроенного_инструмента_не_перехватывается(загрузить, caplog):
    """Два обработчика на одно имя — это «сработает какой-то», и по логу не
    понять какой."""
    with caplog.at_level(logging.WARNING):
        реестр = загрузить("probe", _ГОДНЫЙ, reserved_names={"probe"})

    assert len(реестр) == 0
    assert any("конфликтует" in з.getMessage() for з in caplog.records)


def test_упавший_обработчик_не_роняет_голосовой_ход(загрузить):
    текст = _ГОДНЫЙ.replace('return f"сделано: {parameters.get(\'что\', \'\')}"',
                            'raise RuntimeError("диск отвалился")')
    реестр = загрузить("probe", текст)

    ответ = реестр.run("probe", {})

    assert "не сработал" in ответ and "диск отвалился" in ответ


def test_обработчик_без_player_вызывается_позиционно(загрузить):
    """TypeError ИЗНУТРИ обработчика не должен выглядеть как чужая сигнатура."""
    текст = '''
        def обработчик(parameters):
            return "без окна"

        TOOL = {
            "name": "probe",
            "description": "Проба.",
            "parameters": {"type": "OBJECT", "properties": {}},
            "handler": обработчик,
        }
    '''
    реестр = загрузить("probe", текст)
    assert реестр.run("probe", {}, player=object()) == "без окна"


def test_внутренний_TypeError_не_прячется(загрузить):
    текст = '''
        def обработчик(parameters, player=None):
            return None + 1

        TOOL = {
            "name": "probe",
            "description": "Проба.",
            "parameters": {"type": "OBJECT", "properties": {}},
            "handler": обработчик,
        }
    '''
    ответ = загрузить("probe", текст).run("probe", {})
    assert "не сработал" in ответ


def test_пустой_ответ_обработчика_превращается_в_готово(загрузить):
    текст = _ГОДНЫЙ.replace('return f"сделано: {parameters.get(\'что\', \'\')}"', "return None")
    assert загрузить("probe", текст).run("probe", {}) == "Готово."


def test_неизвестное_имя_не_бросает_исключение():
    assert "Неизвестный" in ActionRegistry().run("такого-нет", {})


def test_описание_способностей_берётся_из_объявлений(загрузить):
    строки = загрузить("probe", _ГОДНЫЙ).describe()
    assert строки == ["probe — Делает пробное действие."]


# ─── Сверка с настоящим main.py ───────────────────────────────────────────────

def test_каждый_объявленный_инструмент_исполним():
    """Главный инвариант: что обещано модели, то и выполнится.

    Объявление без обработчика модель честно вызывает — и получает «неизвестный
    инструмент» вместо результата. Поймать это иначе можно только голосом и
    только случайно."""
    import main

    объявлено = {t["name"] for t in main.TOOLS}
    встроено = {t["name"] for t in main.INLINE_TOOLS}
    из_реестра = set(main._ACTIONS.names())

    assert объявлено == встроено | из_реестра
    assert not (встроено & из_реестра), "имя занято дважды — сработает случайное"


def test_встроенные_инструменты_разобраны_в_диспетчере():
    """Обратная сторона: встроенный инструмент должен иметь свою ветку."""
    import ast

    исходник = (_BASE / "main.py").read_text(encoding="utf-8")
    имена_в_ветках = {
        ast.literal_eval(узел.comparators[0])
        for узел in ast.walk(ast.parse(исходник))
        if isinstance(узел, ast.Compare)
        and isinstance(узел.left, ast.Name) and узел.left.id == "name"
        and isinstance(узел.ops[0], ast.Eq)
        and isinstance(узел.comparators[0], ast.Constant)
    }
    # look_at_screen / look_at_camera разбираются одной веткой через `in`,
    # поэтому сверяем остальные.
    одиночные = {"save_to_memory", "recall_memory", "undo", "shutdown_jarvis",
                 "switch_voice", "sleep_timer", "team_collaboration", "translation"}

    assert одиночные <= имена_в_ветках, (
        "встроенный инструмент объявлен, но не разобран: "
        f"{sorted(одиночные - имена_в_ветках)}"
    )


def test_реестр_нашёл_все_действия_проекта():
    """Модуль с TOOL, который перестал находиться, — это молча пропавшая
    способность: ассистент просто больше её не предлагает."""
    import main

    assert len(main._ACTIONS) >= 14, (
        f"действий стало меньше: {main._ACTIONS.names()}"
    )
    for обязательный in ("weather", "web_search", "files", "open_app", "computer_control"):
        assert main._ACTIONS.has(обязательный), обязательный


# ─── Самоописание в промпте ───────────────────────────────────────────────────

def test_описание_способностей_собрано_из_реестра():
    """Отдельный перечень умений пришлось бы править руками, и он разошёлся бы
    с кодом в первый же месяц: инструмент удалили — ассистент его обещает."""
    import main

    текст = main._describe_capabilities()

    for имя in main._ACTIONS.names():
        assert имя in текст, f"{имя} потерялся в описании способностей"
    for встроенный in ("recall_memory", "undo", "look_at_screen"):
        assert встроенный in текст


def test_границы_названы_явно():
    """Не зная границ, модель придумывает недостающее и уверенно рапортует."""
    import main

    limits = main._LIMITS.lower()

    assert "один снимок" in limits, "зрение — не постоянное наблюдение"
    assert "только на этой машине" in limits
    assert "не выполнено" in limits, "подтверждение нельзя выдавать за сделанное"


def test_фигурная_скобка_в_промпте_не_роняет_запуск():
    """В промпте 27 КБ живого текста — str.format уронил бы старт на KeyError."""
    import main

    итог = main._render_prompt(
        "Пример: {не_токен} и {tools} в одном тексте {",
        {"tools": "СПИСОК"},
    )

    assert "СПИСОК" in итог
    assert "{не_токен}" in итог, "чужие скобки должны остаться как есть"


def test_блоки_дописываются_если_места_в_шаблоне_нет(monkeypatch):
    """Файл промпта писали до появления этих блоков — молча остаться без
    описания собственных границ хуже, чем поставить его не там."""
    from types import SimpleNamespace

    import main

    monkeypatch.setattr(main, "_load_system_prompt", lambda: "Персона без токенов.")
    monkeypatch.setattr(main, "load_memory", dict)
    monkeypatch.setattr(main, "format_memory_for_prompt", lambda m: "")
    monkeypatch.setattr(main, "get_current_mode", lambda: {"mode": "normal"})

    j = main.Jarvis.__new__(main.Jarvis)
    j._resume_handle = None
    j.user_profile = SimpleNamespace(format_for_prompt=lambda: "")

    инструкция = j._build_config().system_instruction

    assert "ЧТО ТЫ УМЕЕШЬ" in инструкция
    assert "ЧЕГО ТЫ НЕ УМЕЕШЬ" in инструкция
    assert "web_search" in инструкция, "способности обязаны быть перечислены"


def test_токен_в_шаблоне_не_дублирует_блоки(monkeypatch):
    """Если место в промпте есть — подставляем туда и НЕ дописываем второй раз."""
    from types import SimpleNamespace

    import main

    monkeypatch.setattr(main, "_load_system_prompt",
                        lambda: "Ты умеешь:\n{tools}\nНе умеешь:\n{limits}")
    monkeypatch.setattr(main, "load_memory", dict)
    monkeypatch.setattr(main, "format_memory_for_prompt", lambda m: "")
    monkeypatch.setattr(main, "get_current_mode", lambda: {"mode": "normal"})

    j = main.Jarvis.__new__(main.Jarvis)
    j._resume_handle = None
    j.user_profile = SimpleNamespace(format_for_prompt=lambda: "")

    инструкция = j._build_config().system_instruction

    assert "{tools}" not in инструкция
    assert "ЧТО ТЫ УМЕЕШЬ" not in инструкция, "блок подставлен — дописывать нечего"
    assert инструкция.count("web_search") == 1
