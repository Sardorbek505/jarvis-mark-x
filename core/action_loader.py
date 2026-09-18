"""
Реестр самоописывающихся действий.

ЗАЧЕМ
    Добавление инструмента стоило правки трёх мест в самом большом файле
    проекта: объявление в списке `TOOLS`, ветка в цепочке `if/elif` на три
    сотни строк и импорт наверху `main.py`. Забыть одно из трёх легко, а
    последствия у всех трёх разные и неочевидные: объявленный без ветки
    инструмент модель вызывает, и он отвечает «неизвестный инструмент»;
    реализованный без объявления не вызывается никогда и выглядит как
    «модель не хочет им пользоваться».

    Теперь инструмент — это один файл. Он объявляет себя сам:

        TOOL = {
            "name": "weather",
            "description": "Сообщает погоду...",   # это читает модель
            "parameters": {...},                    # схема аргументов
            "handler": weather_action,              # handler(parameters, player)
        }

    Реестр находит такие файлы при запуске, проверяет их и отдаёт `main.py`
    готовые объявления. Ни список, ни цепочка ветвлений больше не нужны.

ЧТО СЮДА НЕ ПЕРЕЕХАЛО
    Инструменты, вплетённые в состояние живой сессии: память, отмена,
    переключение голоса, выключение, зрение с дозагрузкой кадра в тот же ход.
    Им нужен не `player`, а сам объект сессии, и притворяться, что они такие
    же, как «открой приложение», значило бы усложнить реестр ради пяти
    исключений. Они остаются в `main.py` и перечислены там явно.

ИЗОЛЯЦИЯ
    Упавший при импорте модуль не мешает остальным: он пропускается с записью
    в лог. Живой ассистент без одного инструмента полезнее, чем ассистент,
    не запустившийся из-за опечатки в редко используемом файле.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("jarvis.actions")

# Имя инструмента, как его принимает Gemini.
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")

# Файлы, которые заведомо не инструменты.
_SKIP_PREFIXES = ("_", ".")


@dataclass
class ActionRecord:
    name: str
    module: str
    declaration: dict
    handler: Callable[..., Any]
    # Принимает ли обработчик `player`. Выясняется один раз при загрузке:
    # определять это по TypeError на вызове нельзя — TypeError, поднятый
    # ВНУТРИ обработчика, неотличим от несовпадения сигнатуры, и ошибка в
    # инструменте молча превращалась бы в повторный вызов с другими
    # аргументами.
    takes_player: bool = True


class ActionRegistry:
    """Что нашли, то и умеем. Список способностей ассистента — это он."""

    def __init__(self, actions: dict[str, ActionRecord] | None = None):
        self._actions: dict[str, ActionRecord] = actions or {}

    def __len__(self) -> int:
        return len(self._actions)

    def names(self) -> list[str]:
        return sorted(self._actions)

    def has(self, name: str) -> bool:
        return name in self._actions

    def get_tool_declarations(self) -> list[dict]:
        """Объявления для Gemini — в порядке имён, чтобы промпт не менялся
        от случайного порядка файлов на диске."""
        return [self._actions[n].declaration for n in self.names()]

    def describe(self) -> list[str]:
        """По строке на способность — для блока «что ты умеешь» в промпте.

        Берётся из живого реестра, а не из отдельного списка: список пришлось
        бы править вручную, и он разошёлся бы с кодом в первый же месяц."""
        out = []
        for name in self.names():
            описание = self._actions[name].declaration.get("description", "")
            первое = описание.split(".")[0].strip()
            out.append(f"{name} — {первое}." if первое else name)
        return out

    def run(self, name: str, parameters: dict, player=None) -> str:
        """Выполняет действие. Исключение обработчика превращается в ответ:
        упавший инструмент не должен ронять голосовой ход."""
        record = self._actions.get(name)
        if record is None:
            return f"Неизвестный инструмент: {name}"
        try:
            if record.takes_player:
                результат = record.handler(parameters or {}, player=player)
            else:
                результат = record.handler(parameters or {})
        except Exception as exc:
            return _ошибка(name, exc)
        return str(результат) if результат else "Готово."


def _takes_player(handler: Callable) -> bool:
    """Умеет ли обработчик принять окно. Обёртки без сигнатуры (C-функции,
    хитрые декораторы) считаем умеющими — как большинство действий."""
    try:
        параметры = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return True
    if "player" in параметры:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in параметры.values())


def _ошибка(name: str, exc: Exception) -> str:
    logger.exception("Инструмент %s сорвался", name)
    return f"Инструмент {name} не сработал: {exc}"


def _validate(tool: Any, module_name: str) -> str:
    """Пустая строка — объявление годно, иначе причина отказа."""
    if not isinstance(tool, dict):
        return "TOOL должен быть словарём"

    name = tool.get("name", "")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return f"недопустимое имя {name!r}"

    if not str(tool.get("description", "")).strip():
        return "пустое описание — модель не поймёт, когда вызывать инструмент"

    params = tool.get("parameters")
    if not isinstance(params, dict) or "properties" not in params:
        return "parameters должен быть схемой с ключом properties"

    if not callable(tool.get("handler")):
        return "handler не вызывается"

    return ""


def discover_actions(
    actions_dir: Path,
    reserved_names: set[str] | None = None,
    package: str = "actions",
) -> ActionRegistry:
    """Собирает реестр из `actions/*.py` с объявлением `TOOL`.

    `reserved_names` — имена, занятые встроенными инструментами `main.py`.
    Совпадение отклоняется: два обработчика на одно имя означают, что
    сработает случайный, и понять, какой именно, по логу будет нельзя."""
    reserved = reserved_names or set()
    found: dict[str, ActionRecord] = {}

    for path in sorted(actions_dir.glob("*.py")):
        if path.name.startswith(_SKIP_PREFIXES):
            continue

        module_name = f"{package}.{path.stem}"
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            logger.warning("Модуль %s не загрузился (%s) — пропускаю", module_name, exc)
            continue

        tool = getattr(module, "TOOL", None)
        if tool is None:
            continue

        причина = _validate(tool, module_name)
        if причина:
            logger.warning("Объявление в %s отклонено: %s", module_name, причина)
            continue

        name = tool["name"]
        if name in reserved:
            logger.warning(
                "Инструмент %s из %s конфликтует со встроенным — пропускаю",
                name, module_name,
            )
            continue
        if name in found:
            logger.warning(
                "Инструмент %s объявлен дважды (%s и %s) — оставляю первый",
                name, found[name].module, module_name,
            )
            continue

        declaration = {
            "name": name,
            "description": tool["description"],
            "parameters": tool["parameters"],
        }
        found[name] = ActionRecord(
            name=name, module=module_name,
            declaration=declaration, handler=tool["handler"],
            takes_player=_takes_player(tool["handler"]),
        )

    logger.info("Действий загружено: %d (%s)", len(found), ", ".join(sorted(found)))
    return ActionRegistry(found)
