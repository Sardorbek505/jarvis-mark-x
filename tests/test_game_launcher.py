"""Запуск игр голосом: «включи ведьмака».

`open_app` ищет программу по PATH и по имени исполняемого файла — игры туда не
попадают. «Ведьмак 3» это `witcher3.exe` где-то в `D:\\SteamLibrary\\steamapps\\
common\\The Witcher 3`, и ни PATH, ни меню «Пуск» о нём не знают.

Ни Steam, ни Epic здесь не запускаются: проверяется разбор их манифестов на
настоящих по форме файлах, подбор названия и то, что ответ не обещает больше,
чем сделано.
"""

import json
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from actions import game_launcher as gl

# Форма настоящего appmanifest_*.acf — по ней и писался разбор.
_МАНИФЕСТ_STEAM = '''"AppState"
{{
	"appid"		"{appid}"
	"Universe"		"1"
	"name"		"{name}"
	"StateFlags"		"4"
	"installdir"		"{name}"
	"LastUpdated"		"1737000000"
	"SizeOnDisk"		"51000000000"
}}
'''


def _steam(tmp_path, игры, библиотеки=()):
    """Собирает папку Steam с манифестами. Возвращает корень."""
    корень = tmp_path / "Steam"
    (корень / "steamapps").mkdir(parents=True)

    for appid, имя in игры:
        (корень / "steamapps" / f"appmanifest_{appid}.acf").write_text(
            _МАНИФЕСТ_STEAM.format(appid=appid, name=имя), encoding="utf-8")

    if библиотеки:
        строки = "".join(
            f'\t\t"path"\t\t"{str(п).replace(chr(92), chr(92) * 2)}"\n'
            for п in библиотеки)
        (корень / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n\t"0"\n\t{\n' + строки + "\t}\n}\n", encoding="utf-8")
    return корень


def _epic(tmp_path, игры):
    папка = tmp_path / "Manifests"
    папка.mkdir(parents=True)
    for код, имя in игры:
        (папка / f"{код}.item").write_text(json.dumps({
            "DisplayName": имя, "AppName": код,
            "LaunchExecutable": f"{имя}/game.exe", "InstallLocation": f"C:/Games/{имя}",
        }), encoding="utf-8")
    return папка


# ─── Разбор Steam ─────────────────────────────────────────────────────────────

def test_читает_установленные_игры(tmp_path):
    корень = _steam(tmp_path, [("292030", "The Witcher 3: Wild Hunt"), ("570", "Dota 2")])

    игры = gl._steam_games(корень)

    assert {и["name"] for и in игры} == {"The Witcher 3: Wild Hunt", "Dota 2"}
    assert all(и["launcher"] == "steam" for и in игры)


def test_ссылка_запуска_через_лаунчер(tmp_path):
    """Не через .exe: игре нужен запущенный лаунчер — античит, сохранения,
    активация. Запуск в обход — ошибка при старте или потерянный прогресс."""
    корень = _steam(tmp_path, [("292030", "The Witcher 3")])

    assert gl._steam_games(корень)[0]["uri"] == "steam://rungameid/292030"


def test_игры_со_второго_диска_тоже_видны(tmp_path):
    """Одной папкой установки дело не ограничивается: библиотеки часто на
    другом диске, и без разбора libraryfolders.vdf половина списка пропадёт."""
    вторая = tmp_path / "D_Games"
    (вторая / "steamapps").mkdir(parents=True)
    (вторая / "steamapps" / "appmanifest_570.acf").write_text(
        _МАНИФЕСТ_STEAM.format(appid="570", name="Dota 2"), encoding="utf-8")

    корень = _steam(tmp_path, [("292030", "The Witcher 3")], библиотеки=[вторая])

    assert {и["name"] for и in gl._steam_games(корень)} == {"The Witcher 3", "Dota 2"}


def test_служебные_записи_не_показываются(tmp_path):
    """Steamworks Common Redistributables человек всё равно не запустит."""
    корень = _steam(tmp_path, [
        ("228980", "Steamworks Common Redistributables"),
        ("1493710", "Proton Experimental"),
        ("570", "Dota 2"),
    ])

    assert [и["name"] for и in gl._steam_games(корень)] == ["Dota 2"]


def test_битый_манифест_не_роняет_список(tmp_path):
    корень = _steam(tmp_path, [("570", "Dota 2")])
    (корень / "steamapps" / "appmanifest_666.acf").write_text("мусор", encoding="utf-8")

    assert [и["name"] for и in gl._steam_games(корень)] == ["Dota 2"]


def test_без_steam_список_пуст(tmp_path):
    assert gl._steam_games(tmp_path / "нет-такой-папки") == []


# ─── Разбор Epic ──────────────────────────────────────────────────────────────

def test_читает_игры_epic(tmp_path):
    папка = _epic(tmp_path, [("Fortnite", "Fortnite"), ("Crab", "Control")])

    игры = gl._epic_games(папка)

    assert {и["name"] for и in игры} == {"Fortnite", "Control"}
    assert all(и["launcher"] == "epic" for и in игры)


def test_ссылка_epic_ведёт_в_лаунчер(tmp_path):
    папка = _epic(tmp_path, [("Crab", "Control")])

    assert gl._epic_games(папка)[0]["uri"].startswith("com.epicgames.launcher://apps/Crab")


def test_битый_json_не_роняет_список(tmp_path):
    папка = _epic(tmp_path, [("Crab", "Control")])
    (папка / "сломанный.item").write_text("{не json", encoding="utf-8")

    assert [и["name"] for и in gl._epic_games(папка)] == ["Control"]


# ─── Подбор названия ──────────────────────────────────────────────────────────

_ИГРЫ = [
    {"name": "The Witcher 3: Wild Hunt", "id": "292030", "launcher": "steam", "uri": "u1"},
    {"name": "Dota 2", "id": "570", "launcher": "steam", "uri": "u2"},
    {"name": "Counter-Strike 2", "id": "730", "launcher": "steam", "uri": "u3"},
    {"name": "Control", "id": "Crab", "launcher": "epic", "uri": "u4"},
]


@pytest.mark.parametrize("сказано,ожидание", [
    ("The Witcher 3: Wild Hunt", "The Witcher 3: Wild Hunt"),
    ("witcher", "The Witcher 3: Wild Hunt"),
    ("dota", "Dota 2"),
    ("DOTA 2", "Dota 2"),
    ("control", "Control"),
])
def test_название_подбирается_как_сказали(сказано, ожидание):
    """Человек говорит «дота», а в манифесте «Dota 2»."""
    игра, _ = gl._найти(сказано, _ИГРЫ)

    assert игра is not None and игра["name"] == ожидание


def test_опечатка_чинится_нечётким_сравнением():
    игра, _ = gl._найти("countr strike", _ИГРЫ)

    assert игра is not None and "Counter" in игра["name"]


def test_кириллица_против_латиницы_не_ищется():
    """«Контр страйк» русскими буквами не совпадёт с «Counter-Strike» ни по
    буквам, ни по подстроке — транслитерация это отдельная задача, и делать
    вид, что она решена, хуже, чем честно переспросить."""
    игра, похожие = gl._найти("контер страйк", _ИГРЫ)

    assert игра is None and похожие == []


def test_неоднозначность_превращается_в_уточнение():
    """Два подходящих — не повод запускать наугад: не та ошибка, которую
    человек простит."""
    игры = _ИГРЫ + [{"name": "Dota Underlords", "id": "1046930",
                     "launcher": "steam", "uri": "u5"}]

    игра, похожие = gl._найти("dota", игры)

    assert игра is None
    assert len(похожие) == 2


def test_чужое_слово_не_запускает_ничего():
    игра, похожие = gl._найти("квантовая хромодинамика", _ИГРЫ)

    assert игра is None and похожие == []


# ─── Ответы ───────────────────────────────────────────────────────────────────

@pytest.fixture
def библиотека(monkeypatch):
    monkeypatch.setattr(gl, "installed_games", lambda: list(_ИГРЫ))
    открыто = []
    monkeypatch.setattr(gl, "_открыть", lambda uri: открыто.append(uri) or True)
    return открыто


def test_запуск_передаёт_ссылку_лаунчеру(библиотека):
    ответ = gl.game_launcher({"game": "dota"})

    assert библиотека == ["u2"]
    assert "Dota 2" in ответ


def test_ответ_не_обещает_больше_чем_сделано(библиотека):
    """Дальше решает лаунчер: обновление, античит, вход в аккаунт. Обещать
    запуск за него мы не можем."""
    ответ = gl.game_launcher({"game": "dota"})

    assert "дальше дело за" in ответ.lower()


def test_неоднозначность_переспрашивают(monkeypatch):
    игры = _ИГРЫ + [{"name": "Dota Underlords", "id": "1", "launcher": "steam", "uri": "u5"}]
    monkeypatch.setattr(gl, "installed_games", lambda: игры)
    monkeypatch.setattr(gl, "_открыть", lambda uri: pytest.fail("запустили наугад"))

    ответ = gl.game_launcher({"game": "dota"})

    assert "Уточните" in ответ
    assert "Dota 2" in ответ and "Dota Underlords" in ответ


def test_ненайденная_игра_подсказывает_список(библиотека):
    ответ = gl.game_launcher({"game": "майнкрафт"})

    assert "нет" in ответ
    assert "список игр" in ответ
    assert библиотека == [], "ничего запускать не должны"


def test_без_названия_переспрашивают(библиотека):
    assert "Какую игру" in gl.game_launcher({})


def test_список_перечисляет_установленное(библиотека):
    ответ = gl.game_launcher({"action": "list"})

    assert "Dota 2" in ответ and "Control" in ответ
    assert "4" in ответ


def test_длинный_список_не_зачитывается_целиком(monkeypatch):
    """Сорок названий подряд вслух никто не дослушает."""
    много = [{"name": f"Игра {i}", "id": str(i), "launcher": "steam", "uri": f"u{i}"}
             for i in range(40)]
    monkeypatch.setattr(gl, "installed_games", lambda: много)

    ответ = gl.game_launcher({"action": "list"})

    assert "40" in ответ
    assert "и ещё" in ответ


def test_без_лаунчеров_говорится_прямо(monkeypatch):
    monkeypatch.setattr(gl, "installed_games", list)

    ответ = gl.game_launcher({"game": "что угодно"})

    assert "не нашёл" in ответ.lower()
    assert "Steam" in ответ


def test_несработавший_запуск_не_выдаётся_за_успех(monkeypatch):
    monkeypatch.setattr(gl, "installed_games", lambda: list(_ИГРЫ))
    monkeypatch.setattr(gl, "_открыть", lambda uri: False)

    assert "Не смог" in gl.game_launcher({"game": "dota"})


# ─── Связь с остальным ────────────────────────────────────────────────────────

def test_дубли_между_лаунчерами_снимаются(monkeypatch):
    """Одна игра может числиться и в Steam, и в Epic."""
    monkeypatch.setattr(gl, "_steam_games",
                        lambda root=None: [{"name": "Control", "id": "1",
                                            "launcher": "steam", "uri": "s"}])
    monkeypatch.setattr(gl, "_epic_games",
                        lambda папка=None: [{"name": "Control", "id": "Crab",
                                             "launcher": "epic", "uri": "e"}])

    assert len(gl.installed_games()) == 1


def test_инструмент_подхвачен_реестром():
    import main

    assert main._ACTIONS.has("game_launcher")


def test_описание_разводит_с_open_app():
    """Иначе модель будет пытаться запускать игры через open_app и не найдёт."""
    описание = gl.TOOL["description"]

    assert "open_app" in описание


def test_обновление_и_закрытие_не_предлагаются():
    """Обновление — гигабайты по чужой воле, закрытие процессом —
    несохранённый прогресс. Ни то, ни другое по фразе из комнаты.

    Проверяем ИМЕНА действий, а не подстроки в тексте: слово «установленные»
    в описании режима list законно, а действие `install` — нет."""
    имена = set(gl.TOOL["parameters"]["properties"]["action"]["description"]
                .replace("|", " ").split())

    for опасное in ("update", "install", "uninstall", "close", "stop", "kill", "delete"):
        assert опасное not in имена, опасное

    # И сам обработчик таких действий не знает: неизвестное имя уходит в запуск.
    assert "обнов" not in gl.TOOL["description"].lower()
