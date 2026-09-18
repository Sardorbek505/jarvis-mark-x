"""Микрофон выбирает человек, а не эвристика по названиям.

Раньше устройство ввода угадывалось по подстрокам («headset», «realtek»,
«наушник»), а устройство вывода не выбиралось вовсе — всегда системное
по умолчанию, которое в Windows меняется само при подключении гарнитуры.
Пока угадывало — работало; когда не угадало, у человека не было ни списка,
ни настройки, ни даже способа узнать, какое устройство взято.

Настоящих звуковых устройств тут нет — проверяется отбор, хранение по имени
и поведение при пропаже устройства.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import audio_devices as ad


def _устройство(имя, индекс, api=0, вход=1, выход=0):
    return {"name": имя, "max_input_channels": вход,
            "max_output_channels": выход, "hostapi": api}


@pytest.fixture
def звуковая_карта(monkeypatch):
    """Подменяет sounddevice: список устройств и список host API."""
    состояние = {"devices": [], "apis": [{"name": "MME"}, {"name": "Windows DirectSound"}]}

    class _sd:
        @staticmethod
        def query_devices():
            return состояние["devices"]

        @staticmethod
        def query_hostapis():
            return состояние["apis"]

    monkeypatch.setitem(sys.modules, "sounddevice", _sd)
    return состояние


# ─── Отбор списка ─────────────────────────────────────────────────────────────

def test_один_host_api_на_направление(звуковая_карта):
    """query_devices отдаёт запись на КАЖДУЮ пару устройство × host API: один
    микрофон появляется четырежды. Это не выбор, а викторина."""
    звуковая_карта["devices"] = [
        _устройство("Микрофон (Realtek)", 0, api=0),
        _устройство("Микрофон (Realtek)", 1, api=1),
        _устройство("Гарнитура (Bluetooth)", 2, api=0),
        _устройство("Гарнитура (Bluetooth)", 3, api=1),
    ]

    список = ad.list_devices("input")

    assert len(список) == 2
    assert {d["name"] for d in список} == {"Микрофон (Realtek)", "Гарнитура (Bluetooth)"}


def test_псевдоустройства_отбрасываются(звуковая_карта):
    """«Sound Mapper» значит всего лишь «возьми то, что по умолчанию» — это и
    есть поведение без выбора."""
    звуковая_карта["devices"] = [
        _устройство("Microsoft Sound Mapper - Input", 0),
        _устройство("Основной звуковой драйвер", 1),
        _устройство("Микрофон (Realtek)", 2),
    ]

    список = ad.list_devices("input")

    assert [d["name"] for d in список] == ["Микрофон (Realtek)"]


def test_дубли_по_имени_снимаются(звуковая_карта):
    звуковая_карта["devices"] = [
        _устройство("Микрофон", 0), _устройство("Микрофон", 1), _устройство("Микрофон", 2),
    ]
    assert len(ad.list_devices("input")) == 1


def test_вход_и_выход_не_смешиваются(звуковая_карта):
    звуковая_карта["devices"] = [
        _устройство("Микрофон", 0, вход=1, выход=0),
        _устройство("Колонки", 1, вход=0, выход=2),
    ]

    assert [d["name"] for d in ad.list_devices("input")] == ["Микрофон"]
    assert [d["name"] for d in ad.list_devices("output")] == ["Колонки"]


def test_без_звуковой_подсистемы_список_пуст(monkeypatch):
    """Так бывает в контейнере и на сервере — это не повод падать."""
    class _sd:
        @staticmethod
        def query_devices():
            raise OSError("PortAudio library not found")

        @staticmethod
        def query_hostapis():
            return []

    monkeypatch.setitem(sys.modules, "sounddevice", _sd)
    assert ad.list_devices("input") == []


# ─── Поиск по имени ───────────────────────────────────────────────────────────

def test_устройство_находится_по_точному_имени(звуковая_карта):
    звуковая_карта["devices"] = [_устройство("Микрофон", 0), _устройство("Гарнитура", 1)]
    assert ad.resolve("Гарнитура", "input") == 1


def test_имя_ищется_без_учёта_регистра(звуковая_карта):
    звуковая_карта["devices"] = [_устройство("Микрофон (Realtek)", 0)]
    assert ad.resolve("микрофон (realtek)", "input") == 0


def test_подросшее_имя_драйвера_всё_равно_находится(звуковая_карта):
    """После обновления драйвера имя обрастает мелочами вроде «(2- …)»."""
    звуковая_карта["devices"] = [_устройство("Микрофон (2- Realtek High Definition)", 0)]
    assert ad.resolve("Микрофон (2- Realtek", "input") == 0


def test_пропавшее_устройство_даёт_системное_по_умолчанию(звуковая_карта, caplog):
    """Молчаливый откат на другое устройство неотличим от поломки."""
    import logging

    звуковая_карта["devices"] = [_устройство("Микрофон", 0)]

    with caplog.at_level(logging.WARNING):
        индекс = ad.resolve("Гарнитура, которую унесли", "input")

    assert индекс is None
    assert any("не найдено" in з.getMessage() for з in caplog.records)


def test_пустое_имя_означает_отсутствие_выбора(звуковая_карта):
    assert ad.resolve("", "input") is None
    assert ad.resolve("   ", "input") is None


# ─── Хранение выбора ──────────────────────────────────────────────────────────

@pytest.fixture
def конфиг(monkeypatch):
    """Настройки в памяти вместо файла пользователя."""
    хранилище = {}
    from core import paths

    monkeypatch.setattr(paths, "load_api_keys", lambda: dict(хранилище))
    monkeypatch.setattr(paths, "save_api_keys", lambda d: хранилище.update(d))
    return хранилище


def test_выбор_сохраняется_по_имени_а_не_по_индексу(конфиг):
    """Индексы сдвигаются при подключении наушников: сохранённый индекс
    назавтра указывает на другое устройство, молча."""
    ad.save_name("Гарнитура (Bluetooth)", "input")

    assert конфиг["input_device"] == "Гарнитура (Bluetooth)"
    assert ad.saved_name("input") == "Гарнитура (Bluetooth)"


def test_вход_и_выход_хранятся_раздельно(конфиг):
    ad.save_name("Микрофон", "input")
    ad.save_name("Колонки", "output")

    assert ad.saved_name("input") == "Микрофон"
    assert ad.saved_name("output") == "Колонки"


def test_пустое_имя_возвращает_к_системному(конфиг):
    ad.save_name("Микрофон", "input")
    ad.save_name("", "input")

    assert ad.saved_name("input") == ""
    assert ad.chosen_index("input") is None


def test_выбранный_индекс_собирается_из_имени(конфиг, звуковая_карта):
    звуковая_карта["devices"] = [_устройство("Микрофон", 0), _устройство("Гарнитура", 1)]
    ad.save_name("Гарнитура", "input")

    assert ad.chosen_index("input") == 1


def test_главное_окно_умеет_открыть_выбор():
    """Кнопка без обработчика — это кнопка, о которой узнают нажатием."""
    from ui import MainWindow

    assert callable(getattr(MainWindow, "_open_audio_picker", None))


def test_выбор_человека_идёт_раньше_эвристики(конфиг, звуковая_карта, monkeypatch):
    """Иначе настройка есть, а толку нет: угадайка перебьёт выбор."""
    import main

    звуковая_карта["devices"] = [_устройство("Микрофон", 0), _устройство("Гарнитура", 1)]
    ad.save_name("Гарнитура", "input")

    assert main._pick_input_device() == 1
