"""
Действие: запуск установленных игр по названию.

ЗАЧЕМ ОТДЕЛЬНО ОТ open_app
    `open_app` ищет программу по PATH и по имени исполняемого файла. Игры туда
    не попадают: «Ведьмак 3» — это `witcher3.exe` где-то в
    `D:\\SteamLibrary\\steamapps\\common\\The Witcher 3`, и ни PATH, ни меню
    «Пуск» о нём не знают. Сказать «включи ведьмака» было нельзя.

ОТКУДА БЕРЁТСЯ СПИСОК
    Steam держит на каждую установленную игру файл `appmanifest_<id>.acf` в
    папке библиотеки, а список самих библиотек — в `libraryfolders.vdf`.
    Epic кладёт по JSON-файлу на игру в `Manifests`. Оба формата читаются с
    диска, без сети и без входа в аккаунт: список установленного — это то,
    что лежит на дисках, а не то, что числится за учёткой.

КАК ЗАПУСКАЕТСЯ
    Через ссылку самого лаунчера — `steam://rungameid/<id>` и
    `com.epicgames.launcher://apps/<имя>?action=launch`. Не напрямую через
    .exe: игре нужен запущенный лаунчер (античит, облачные сохранения,
    активация), и запуск в обход него — это в лучшем случае ошибка при старте,
    в худшем потерянный прогресс.

ЧЕГО ЗДЕСЬ НЕТ
    Ни обновления, ни установки, ни «закрыть игру». Обновление — это гигабайты
    по чужой воле, а закрытие игры процессом означает несохранённый прогресс:
    ни то, ни другое не стоит делать по фразе, услышанной в комнате, где
    работает телевизор.
"""

import json
import logging
import os
import platform
import re
import subprocess
from pathlib import Path

_logger = logging.getLogger(__name__)

_OS = platform.system()

# Служебные записи Steam: инструменты сборки, среды выполнения и прочее, что
# лежит рядом с играми, но игрой не является. Показывать их человеку —
# засорять список тем, что он всё равно не запустит.
_НЕ_ИГРЫ = (
    "steamworks common redistributables", "steam linux runtime",
    "proton", "steamvr", "steam controller", "soundtrack",
)

# Совпадение названия, ниже которого это уже не «похоже», а «наугад».
_ПОРОГ_СХОЖЕСТИ = 60


# ─── Где стоит Steam ──────────────────────────────────────────────────────────

def _steam_root() -> Path | None:
    """Папка Steam или None. Реестр на Windows, обычные места на прочих."""
    if _OS == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as ключ:
                путь = Path(winreg.QueryValueEx(ключ, "SteamPath")[0])
                if путь.exists():
                    return путь
        except Exception as exc:
            _logger.debug("Steam в реестре не найден: %s", exc)

    кандидаты = [
        Path.home() / ".steam" / "steam",
        Path.home() / ".local" / "share" / "Steam",
        Path.home() / "Library" / "Application Support" / "Steam",
        Path("C:/Program Files (x86)/Steam"),
    ]
    return next((п for п in кандидаты if (п / "steamapps").is_dir()), None)


_ПУТЬ_БИБЛИОТЕКИ = re.compile(r'"path"\s*"([^"]+)"')


def _steam_libraries(root: Path) -> list[Path]:
    """Папки библиотек Steam. Игры часто лежат на другом диске, и одной
    папкой установки дело не ограничивается."""
    библиотеки = [root / "steamapps"]

    файл = root / "steamapps" / "libraryfolders.vdf"
    try:
        текст = файл.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        _logger.debug("libraryfolders.vdf не прочитан: %s", exc)
        return библиотеки

    for сырой in _ПУТЬ_БИБЛИОТЕКИ.findall(текст):
        папка = Path(сырой.replace("\\\\", "\\")) / "steamapps"
        if папка.is_dir() and папка not in библиотеки:
            библиотеки.append(папка)
    return библиотеки


_ПОЛЕ_ACF = re.compile(r'"(appid|name)"\s*"([^"]*)"', re.IGNORECASE)


def _steam_games(root: Path | None = None) -> list[dict]:
    """Установленные игры Steam: имя, идентификатор, ссылка запуска."""
    root = root or _steam_root()
    if root is None:
        return []

    игры = []
    for библиотека in _steam_libraries(root):
        for манифест in sorted(библиотека.glob("appmanifest_*.acf")):
            try:
                текст = манифест.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                _logger.debug("Манифест %s не прочитан: %s", манифест.name, exc)
                continue

            поля = {к.lower(): з for к, з in _ПОЛЕ_ACF.findall(текст)}
            имя, appid = поля.get("name", "").strip(), поля.get("appid", "").strip()
            if not имя or not appid:
                continue
            if any(мусор in имя.lower() for мусор in _НЕ_ИГРЫ):
                continue

            игры.append({
                "name": имя,
                "id": appid,
                "launcher": "steam",
                "uri": f"steam://rungameid/{appid}",
            })
    return игры


# ─── Epic ─────────────────────────────────────────────────────────────────────

def _epic_manifests_dir() -> Path | None:
    кандидаты = [
        Path("C:/ProgramData/Epic/EpicGamesLauncher/Data/Manifests"),
        Path.home() / ".config" / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests",
    ]
    return next((п for п in кандидаты if п.is_dir()), None)


def _epic_games(папка: Path | None = None) -> list[dict]:
    """Установленные игры Epic. Манифесты — обычный JSON, по файлу на игру."""
    папка = папка or _epic_manifests_dir()
    if папка is None:
        return []

    игры = []
    for файл in sorted(папка.glob("*.item")):
        try:
            данные = json.loads(файл.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            _logger.debug("Манифест %s не разобран: %s", файл.name, exc)
            continue

        имя = str(данные.get("DisplayName", "")).strip()
        код = str(данные.get("AppName", "")).strip()
        if not имя or not код:
            continue

        игры.append({
            "name": имя,
            "id": код,
            "launcher": "epic",
            "uri": f"com.epicgames.launcher://apps/{код}?action=launch&silent=true",
        })
    return игры


# ─── Поиск по названию ────────────────────────────────────────────────────────

def installed_games() -> list[dict]:
    """Всё установленное, из обоих лаунчеров, без дублей по названию."""
    все = _steam_games() + _epic_games()
    видели, итог = set(), []
    for игра in все:
        ключ = игра["name"].lower()
        if ключ in видели:
            continue
        видели.add(ключ)
        итог.append(игра)
    return sorted(итог, key=lambda и: и["name"].lower())


def _найти(запрос: str, игры: list[dict]) -> tuple[dict | None, list[dict]]:
    """Игра по названию и список похожих, если точного совпадения нет.

    Человек говорит «ведьмака», а в манифесте «The Witcher 3: Wild Hunt» —
    точное сравнение здесь бесполезно."""
    запрос = (запрос or "").strip().lower()
    if not запрос or not игры:
        return None, []

    for игра in игры:
        if игра["name"].lower() == запрос:
            return игра, []
    # Вхождение подстроки надёжнее нечёткого сравнения: «дота» в «Dota 2»
    # находится точно, а по буквам эти строки похожи мало.
    вхождения = [и for и in игры if запрос in и["name"].lower()]
    if len(вхождения) == 1:
        return вхождения[0], []
    if вхождения:
        return None, вхождения[:5]

    try:
        from rapidfuzz import process

        похожие = process.extract(
            запрос, [и["name"] for и in игры], limit=5, score_cutoff=_ПОРОГ_СХОЖЕСТИ)
    except ImportError:
        return None, []

    if not похожие:
        return None, []
    по_имени = {и["name"]: и for и in игры}
    отобранные = [по_имени[имя] for имя, _, _ in похожие]
    # Уверенное совпадение — только если первое заметно лучше второго.
    if len(похожие) == 1 or похожие[0][1] - похожие[1][1] >= 15:
        return отобранные[0], []
    return None, отобранные


# ─── Запуск ───────────────────────────────────────────────────────────────────

def _открыть(uri: str) -> bool:
    """Открывает ссылку лаунчера средствами системы."""
    try:
        if _OS == "Windows":
            os.startfile(uri)
        elif _OS == "Darwin":
            subprocess.Popen(["open", uri])
        else:
            subprocess.Popen(["xdg-open", uri],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as exc:
        _logger.warning("Ссылка %s не открылась: %s", uri, exc)
        return False


def _запустить(запрос: str, player=None) -> str:
    игры = installed_games()
    if not игры:
        return ("Не нашёл ни одной установленной игры, сэр. "
                "Проверьте, что Steam или Epic установлены на этой машине.")

    if not запрос:
        return f"Какую игру запустить, сэр? Установлено {len(игры)}."

    игра, похожие = _найти(запрос, игры)
    if игра is None:
        if похожие:
            имена = ", ".join(и["name"] for и in похожие)
            return f"Уточните, сэр: {имена}?"
        return (f"Игры «{запрос}» среди установленных нет, сэр. "
                f"Скажите «список игр», и я перечислю.")

    if not _открыть(игра["uri"]):
        return f"Не смог передать запуск в {игра['launcher'].title()}, сэр."

    if player:
        player.write_log(f"GAME: {игра['name']} ({игра['launcher']})")
    # «Передал в лаунчер», а не «запустил»: дальше решает лаунчер — обновление,
    # античит, вход в аккаунт. Обещать запуск за него мы не можем.
    return f"Запускаю {игра['name']} — дальше дело за {игра['launcher'].title()}."


def _список(player=None) -> str:
    игры = installed_games()
    if not игры:
        return "Установленных игр не нашёл, сэр."

    имена = [и["name"] for и in игры]
    if player:
        player.write_log(f"GAME: установлено {len(игры)}")

    if len(имена) <= 12:
        return f"Установлено {len(игры)}: " + ", ".join(имена) + "."
    return (f"Установлено {len(игры)}. Вот часть: " + ", ".join(имена[:12])
            + f" — и ещё {len(игры) - 12}.")


def game_launcher(parameters: dict, player=None) -> str:
    параметры = parameters or {}
    действие = str(параметры.get("action", "play")).strip().lower()
    название = str(параметры.get("game", "")).strip()

    if действие in ("list", "список"):
        return _список(player)
    return _запустить(название, player)


# ─── Объявление для реестра действий ──────────────────────────────────────────
TOOL = {
    "name": "game_launcher",
    "description": (
        "Запускает установленную игру по названию через Steam или Epic Games "
        "(action=play, game=название) и перечисляет установленное (action=list). "
        "Вызывай, когда пользователь говорит: включи игру, запусти Ведьмака, "
        "давай поиграем в такую-то, какие игры установлены. "
        "Название можно говорить как угодно — я подберу по списку установленного. "
        "Для обычных программ используй open_app: игры лежат не там, где программы, "
        "и наоборот."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "play (по умолчанию) — запустить | list — перечислить установленные",
            },
            "game": {
                "type": "STRING",
                "description": "Название игры, как его назвал пользователь",
            },
        },
        "required": [],
    },
    "handler": game_launcher,
}
