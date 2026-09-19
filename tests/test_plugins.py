"""Плагины: один файл в plugins/ — и ассистент умеет новое.

Продолжение реестра действий, а не второй загрузчик: механика у них одна, и
разводить два значило бы чинить потом обоих. Разница только в имени
переменной (`PLUGIN` вместо `TOOL`), папке и возможности выключить плагин, не
удаляя файл.

Проверяется главным образом то, чего плагин НЕ должен мочь: подменить штатный
инструмент и уронить запуск своей ошибкой.
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

from core.action_loader import discover_actions

_счётчик = itertools.count()

_ОБРАЗЕЦ = '''
    def run(parameters, player=None):
        return f"сделал {parameters.get('что', '')}"

    PLUGIN = {
        "name": "kettle",
        "description": "Ставит чайник.",
        "parameters": {"type": "OBJECT", "properties": {"что": {"type": "STRING"}}},
        "handler": run,
    }
'''


@pytest.fixture
def положить(tmp_path, monkeypatch):
    """Кладёт файл в папку плагинов и собирает реестр."""
    monkeypatch.syspath_prepend(str(tmp_path))
    пакет_имя = f"plugbox{next(_счётчик)}"
    пакет = tmp_path / пакет_имя
    пакет.mkdir()
    (пакет / "__init__.py").touch()

    def сделать(имя, текст, **kw):
        (пакет / f"{имя}.py").write_text(textwrap.dedent(текст), encoding="utf-8")
        importlib.invalidate_caches()
        return discover_actions(пакет, package=пакет_имя, attribute="PLUGIN", **kw)
    return сделать


# ─── Загрузка ─────────────────────────────────────────────────────────────────

def test_положил_файл_и_работает(положить):
    реестр = положить("kettle", _ОБРАЗЕЦ)

    assert реестр.has("kettle")
    assert реестр.run("kettle", {"что": "чай"}) == "сделал чай"


def test_объявление_уходит_модели(положить):
    объявления = положить("kettle", _ОБРАЗЕЦ).get_tool_declarations()

    assert объявления[0]["name"] == "kettle"
    assert "handler" not in объявления[0]


def test_переменная_TOOL_в_плагинах_не_считается(положить):
    """Иначе файл, скопированный из actions/, тихо стал бы плагином."""
    реестр = положить("kettle", _ОБРАЗЕЦ.replace("PLUGIN = {", "TOOL = {"))
    assert len(реестр) == 0


def test_шаблон_не_превращается_в_инструмент(положить):
    """Файлы с подчёркиванием пропускаются — иначе образец в поставке
    оказался бы способностью, которой ассистент хвастается зря."""
    реестр = положить("_template", _ОБРАЗЕЦ)
    assert len(реестр) == 0


def test_настоящий_шаблон_проекта_не_грузится():
    import main

    assert not main._PLUGINS.has("my_plugin")


# ─── Чего плагин не может ─────────────────────────────────────────────────────

def test_плагин_не_подменяет_штатный_инструмент(положить, caplog):
    """Иначе чужой файл незаметно перехватил бы «выключи компьютер»."""
    with caplog.at_level(logging.WARNING):
        реестр = положить(
            "kettle",
            _ОБРАЗЕЦ.replace('"kettle"', '"computer_control"'),
            reserved_names={"computer_control"},
        )

    assert len(реестр) == 0
    assert any("конфликтует" in з.getMessage() for з in caplog.records)


def test_имена_штатных_инструментов_заняты_для_плагинов():
    """Обратная сторона: main.py обязан передать занятые имена в загрузчик,
    иначе проверка выше защищает только тест."""
    import ast

    исходник = (_BASE / "main.py").read_text(encoding="utf-8")
    дерево = ast.parse(исходник)

    вызов = next(
        узел for узел in ast.walk(дерево)
        if isinstance(узел, ast.Call)
        and getattr(узел.func, "id", "") == "discover_actions"
        and any(k.arg == "attribute" for k in узел.keywords)
    )
    занятые = next(k.value for k in вызов.keywords if k.arg == "reserved_names")
    текст = ast.unparse(занятые)

    assert "_INLINE_NAMES" in текст, "встроенные имена должны быть заняты"
    assert "_ACTIONS.names()" in текст, "имена действий тоже"


def test_битый_плагин_не_роняет_запуск(положить, caplog):
    положить("kettle", _ОБРАЗЕЦ)
    with caplog.at_level(logging.WARNING):
        реестр = положить("сломанный", "это( не питон\n")

    assert реестр.has("kettle"), "живой плагин должен остаться"
    assert any("не загрузился" in з.getMessage() for з in caplog.records)


def test_упавший_плагин_отвечает_а_не_бросает(положить):
    реестр = положить("kettle", _ОБРАЗЕЦ.replace(
        'return f"сделал {parameters.get(\'что\', \'\')}"',
        'raise RuntimeError("чайник сгорел")'))

    ответ = реестр.run("kettle", {})

    assert "не сработал" in ответ and "чайник сгорел" in ответ


def test_кириллическое_имя_отклоняется(положить, caplog):
    """Gemini принимает только латиницу — плагин с русским именем не вызовут."""
    with caplog.at_level(logging.WARNING):
        реестр = положить("kettle", _ОБРАЗЕЦ.replace('"kettle"', '"чайник"'))

    assert len(реестр) == 0
    assert any("недопустимое имя" in з.getMessage() for з in caplog.records)


def test_плагин_без_описания_не_берут(положить):
    """Описание читает модель: без него она не поймёт, когда вызывать."""
    реестр = положить("kettle", _ОБРАЗЕЦ.replace('"description": "Ставит чайник.",',
                                                 '"description": "",'))
    assert len(реестр) == 0


# ─── Выключение ───────────────────────────────────────────────────────────────

def test_выключенный_плагин_не_загружается(положить):
    """Не «загружается и молчит»: модель не должна видеть способность, которой
    человек её лишил, — иначе будет предлагать и упираться в «неизвестный»."""
    реестр = положить("kettle", _ОБРАЗЕЦ, enabled=lambda имя: имя != "kettle")

    assert len(реестр) == 0


def test_включённый_загружается(положить):
    реестр = положить("kettle", _ОБРАЗЕЦ, enabled=lambda имя: True)
    assert реестр.has("kettle")


def test_список_выключенных_читается_из_настроек(monkeypatch):
    """Прежний ключ `plugins_disabled` продолжает работать: у кого-то он уже
    прописан, и молча перестать его читать — значит вернуть человеку
    плагин, который он выключил."""
    import main
    from core import paths

    monkeypatch.setattr(paths, "load_api_keys",
                        lambda: {"plugins_disabled": ["kettle", " radio "]})

    assert main._tool_enabled("kettle") is False
    assert main._tool_enabled("radio") is False, "пробелы не должны мешать"
    assert main._tool_enabled("другой") is True


def test_выключать_можно_и_штатные_инструменты(monkeypatch):
    """35 объявлений уходят в промпт на каждом подключении. Тому, у кого нет
    ни Spotify, ни игр, всё это оплачивается токенами и сбивает выбор."""
    import main
    from core import paths

    monkeypatch.setattr(paths, "load_api_keys",
                        lambda: {"tools_disabled": ["music_player", "game_launcher"]})

    assert main._tool_enabled("music_player") is False
    assert main._tool_enabled("game_launcher") is False
    assert main._tool_enabled("weather") is True


@pytest.mark.parametrize("имя", sorted(
    __import__("core.settings", fromlist=["x"]).НЕВЫКЛЮЧАЕМЫЕ))
def test_несущее_выключить_нельзя(monkeypatch, имя):
    """Без памяти ассистент забывает человека, без отмены сделанное нечем
    вернуть, без shutdown_jarvis его не закрыть голосом."""
    import main
    from core import paths

    monkeypatch.setattr(paths, "load_api_keys", lambda: {"tools_disabled": [имя]})

    assert main._tool_enabled(имя) is True


def test_битые_настройки_не_выключают_всё(monkeypatch):
    import main
    from core import paths

    def падает():
        raise OSError("файл настроек недоступен")

    monkeypatch.setattr(paths, "load_api_keys", падает)

    assert main._tool_enabled("kettle") is True, "молча остаться без плагинов — хуже"


# ─── Связь с остальным ────────────────────────────────────────────────────────

def test_плагины_попадают_в_общий_список_инструментов():
    import main

    объявлено = {t["name"] for t in main.TOOLS}
    ожидается = ({t["name"] for t in main.INLINE_TOOLS}
                 | set(main._ACTIONS.names()) | set(main._PLUGINS.names()))

    assert объявлено == ожидается


def test_плагины_видны_в_описании_способностей(monkeypatch):
    """Иначе промпт обещает одно, а инструменты умеют другое."""
    import main
    from core.action_loader import ActionRecord, ActionRegistry

    запись = ActionRecord(
        name="kettle", module="plugins.kettle",
        declaration={"name": "kettle", "description": "Ставит чайник.",
                     "parameters": {"type": "OBJECT", "properties": {}}},
        handler=lambda parameters, player=None: "ок",
    )
    monkeypatch.setattr(main, "_PLUGINS", ActionRegistry({"kettle": запись}))

    assert "kettle — Ставит чайник." in main._describe_capabilities()


def test_диспетчер_знает_про_плагины():
    """Объявленный плагин без ветки в диспетчере отвечал бы «неизвестный»."""
    import ast

    исходник = (_BASE / "main.py").read_text(encoding="utf-8")
    assert "_PLUGINS.has(name)" in исходник
    assert "_PLUGINS.run(" in исходник
    ast.parse(исходник)


def test_папка_плагинов_есть_в_поставке():
    """Без папки первый же плагин пришлось бы класть неизвестно куда."""
    assert (_BASE / "plugins" / "__init__.py").exists()
    assert (_BASE / "plugins" / "_template.py").exists()


def test_шаблон_объясняет_ограничения():
    текст = (_BASE / "plugins" / "_template.py").read_text(encoding="utf-8")

    assert "латиниц" in текст, "иначе первый плагин назовут по-русски"
    assert "МОДЕЛЬ" in текст, "описание пишут для модели, а не для человека"
    assert "plugins_disabled" in текст
