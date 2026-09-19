"""Переезд на другую машину — и ключи, которые не должны уехать вместе с ним.

Джарвис накапливает то, что заново не наберёшь: факты о человеке, записную
книжку, темы слежения, профиль привычек. Лежит это в четырёх разных местах и
при переустановке системы пропадает целиком, молча.

Главное, что здесь проверяется, — не перенос, а две вещи вокруг него: что
действующие ключи API не уезжают в файл, который человек положит в облако, и
что загрузка на живой машине не стирает то, что там уже нажито.
"""

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))


def _загрузить_скрипт():
    путь = _BASE / "scripts" / "transfer.py"
    спец = importlib.util.spec_from_file_location("_transfer", путь)
    модуль = importlib.util.module_from_spec(спец)
    sys.modules["_transfer"] = модуль
    спец.loader.exec_module(модуль)
    return модуль


@pytest.fixture(scope="module")
def tr():
    return _загрузить_скрипт()


@pytest.fixture
def машина(tr, tmp_path, monkeypatch):
    """Четыре файла Джарвиса в tmp_path вместо настоящих."""
    пути = {
        "memory":   tmp_path / "memory.json",
        "contacts": tmp_path / "contacts.json",
        "watch":    tmp_path / "topics.json",
        "profile":  tmp_path / "profile.json",
        "settings": tmp_path / "api_keys.json",
    }
    monkeypatch.setattr(tr, "_пути", lambda: пути)

    def записать(имя, данные):
        пути[имя].write_text(json.dumps(данные, ensure_ascii=False), encoding="utf-8")

    def прочитать(имя):
        return json.loads(пути[имя].read_text(encoding="utf-8"))

    return type("Машина", (), {"пути": пути, "записать": staticmethod(записать),
                               "прочитать": staticmethod(прочитать)})


def _наполнить(машина):
    машина.записать("memory", {"identity": {"имя": {"value": "Сардорбек"}}})
    машина.записать("contacts", {"мама": "79991234567"})
    машина.записать("watch", [{"тема": "выход игры", "интервал": 30}])
    машина.записать("profile", {"предпочтения": {"кофе": "без сахара"}})
    машина.записать("settings", {"gemini_api_key": "СЕКРЕТ",
                                 "vad_silence_ms": 400})


# ─── Ключи ───────────────────────────────────────────────────────────────────

def test_ключи_не_попадают_в_файл_переезда(tr, машина, tmp_path):
    """Файл переезда человек кладёт в облако и забывает в загрузках. Там
    лежал бы действующий ключ, по которому выставляют счета."""
    _наполнить(машина)
    архив = tmp_path / "backup.zip"

    assert tr.выгрузить(архив, с_ключами=False) == 0

    with zipfile.ZipFile(архив) as z:
        настройки = json.loads(z.read("settings.json").decode("utf-8"))
    assert "gemini_api_key" not in настройки
    assert настройки["vad_silence_ms"] == 400


def test_ключи_уезжают_только_по_прямой_просьбе(tr, машина, tmp_path, capsys):
    _наполнить(машина)
    архив = tmp_path / "backup.zip"

    tr.выгрузить(архив, с_ключами=True)

    with zipfile.ZipFile(архив) as z:
        assert json.loads(z.read("settings.json").decode("utf-8"))["gemini_api_key"] == "СЕКРЕТ"
    # И об этом сказано вслух: про такое нельзя писать мелким шрифтом.
    assert "ВНИМАНИЕ" in capsys.readouterr().out


def test_чужой_ключ_не_затирает_свой_даже_поверх(tr, машина, tmp_path):
    """Иначе перенос настроек тембра лишает человека доступа к Gemini."""
    _наполнить(машина)
    архив = tmp_path / "backup.zip"
    tr.выгрузить(архив, с_ключами=True)

    машина.записать("settings", {"gemini_api_key": "МОЙ-КЛЮЧ"})
    tr.загрузить(архив, поверх=True)

    assert машина.прочитать("settings")["gemini_api_key"] == "МОЙ-КЛЮЧ"


# ─── Перенос ─────────────────────────────────────────────────────────────────

def test_нажитое_переезжает_целиком(tr, машина, tmp_path):
    _наполнить(машина)
    архив = tmp_path / "backup.zip"
    tr.выгрузить(архив, с_ключами=False)

    for имя in ("memory", "contacts", "watch", "profile"):
        машина.пути[имя].unlink()

    assert tr.загрузить(архив, поверх=False) == 0

    assert машина.прочитать("contacts") == {"мама": "79991234567"}
    assert машина.прочитать("memory")["identity"]["имя"]["value"] == "Сардорбек"
    assert машина.прочитать("watch")[0]["тема"] == "выход игры"


def test_загрузка_дополняет_а_не_стирает(tr, машина, tmp_path):
    """Человек успел поговорить с Джарвисом на новом компьютере, прежде чем
    вспомнил про перенос. Стереть это — худший исход."""
    _наполнить(машина)
    архив = tmp_path / "backup.zip"
    tr.выгрузить(архив, с_ключами=False)

    машина.записать("contacts", {"Дима": "79990000000"})
    tr.загрузить(архив, поверх=False)

    assert машина.прочитать("contacts") == {"Дима": "79990000000",
                                            "мама": "79991234567"}


def test_своё_главнее_при_совпадении_ключа(tr, машина, tmp_path):
    """Номер, записанный на этой машине, новее того, что в архиве."""
    _наполнить(машина)
    архив = tmp_path / "backup.zip"
    tr.выгрузить(архив, с_ключами=False)

    машина.записать("contacts", {"мама": "79995555555"})
    tr.загрузить(архив, поверх=False)

    assert машина.прочитать("contacts")["мама"] == "79995555555"


def test_поверх_заменяет_целиком(tr, машина, tmp_path):
    _наполнить(машина)
    архив = tmp_path / "backup.zip"
    tr.выгрузить(архив, с_ключами=False)

    машина.записать("contacts", {"Дима": "79990000000"})
    tr.загрузить(архив, поверх=True)

    assert машина.прочитать("contacts") == {"мама": "79991234567"}


def test_темы_слежения_не_двоятся(tr, машина, tmp_path):
    """Список после двух загрузок подряд не должен вырасти вдвое."""
    _наполнить(машина)
    архив = tmp_path / "backup.zip"
    tr.выгрузить(архив, с_ключами=False)

    tr.загрузить(архив, поверх=False)
    tr.загрузить(архив, поверх=False)

    assert len(машина.прочитать("watch")) == 1


# ─── Когда что-то не так ─────────────────────────────────────────────────────

def test_пустая_машина_не_создаёт_пустой_архив(tr, машина, tmp_path, capsys):
    архив = tmp_path / "backup.zip"

    assert tr.выгрузить(архив, с_ключами=False) == 1
    assert not архив.exists()
    assert "нечего" in capsys.readouterr().out


def test_чужой_архив_отвергается(tr, машина, tmp_path, capsys):
    """ZIP из загрузок, который человек перепутал с переносом."""
    чужой = tmp_path / "фотки.zip"
    with zipfile.ZipFile(чужой, "w") as z:
        z.writestr("фото.jpg", "не json")

    assert tr.загрузить(чужой, поверх=False) == 1
    assert "не файл переезда" in capsys.readouterr().out


def test_отсутствующий_файл_говорится_прямо(tr, машина, tmp_path, capsys):
    assert tr.загрузить(tmp_path / "нет.zip", поверх=False) == 1
    assert "Файла нет" in capsys.readouterr().out


def test_битый_файл_не_ломает_выгрузку_остального(tr, машина, tmp_path, capsys):
    """Один испорченный файл не повод отказать в переносе трёх целых."""
    _наполнить(машина)
    машина.пути["profile"].write_text("{это не json", encoding="utf-8")
    архив = tmp_path / "backup.zip"

    assert tr.выгрузить(архив, с_ключами=False) == 0

    with zipfile.ZipFile(архив) as z:
        внутри = set(z.namelist())
    assert "memory.json" in внутри
    assert "profile.json" not in внутри


def test_напоминания_и_разговоры_не_переносятся(tr):
    """Напоминания живут задачами планировщика ОС: журнал без самих задач —
    это список напоминаний, которые не сработают."""
    assert "reminders" not in tr.ВЕЩИ
    assert "sessions" not in tr.ВЕЩИ
