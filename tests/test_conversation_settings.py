"""Три числа, которые решают, каким Джарвис кажется в разговоре.

Жалобы на голосового ассистента бывают ровно двух видов — «перебивает» и
«долго молчит», — и это одна и та же ручка, повёрнутая не туда. Подобрана она
была один раз, под одну комнату и один микрофон, и жила в переменной среды,
то есть для человека не существовала вовсе.

Самое важное здесь — не окно настроек, а связка: окно VAD и хвост тишины
микрофона нельзя двигать порознь. Если хвост короче окна, Gemini не дожидается
тишины и НЕ ОТВЕЧАЕТ ВОВСЕ, а связать это с ползунком «пусть не перебивает»
человек не сможет никогда.
"""

import json
import os
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import settings as conv


@pytest.fixture
def конфиг(tmp_path, monkeypatch):
    файл = tmp_path / "api_keys.json"
    monkeypatch.setattr(conv, "_файл", lambda: файл)
    monkeypatch.setattr(conv, "_прочитать",
                        lambda: json.loads(файл.read_text(encoding="utf-8"))
                        if файл.exists() else {})
    for описание in conv.ОПИСАНИЯ:
        if описание.переменная:
            monkeypatch.delenv(описание.переменная, raising=False)
    return файл


# ─── Значения и границы ──────────────────────────────────────────────────────

def test_без_файла_берутся_проверенные_значения(конфиг):
    assert conv.get("vad_silence_ms") == 220
    assert conv.get("thinking_budget") == 0
    assert conv.get("show_latency") is False


def test_сохранённое_читается_обратно(конфиг):
    conv.set("vad_silence_ms", 500)
    assert conv.get("vad_silence_ms") == 500
    assert json.loads(конфиг.read_text(encoding="utf-8"))["vad_silence_ms"] == 500


def test_невозможное_значение_прижимается_к_границе(конфиг):
    """Ноль миллисекунд тишины означает, что Джарвис перебьёт на первом же
    вдохе, а десять секунд — что ответа не дождаться."""
    conv.set("vad_silence_ms", 0)
    assert conv.get("vad_silence_ms") == conv.описание("vad_silence_ms").минимум

    conv.set("vad_silence_ms", 999999)
    assert conv.get("vad_silence_ms") == conv.описание("vad_silence_ms").максимум


def test_мусор_вместо_числа_не_ломает_настройку(конфиг):
    конфиг.write_text(json.dumps({"vad_silence_ms": "побыстрее"}), encoding="utf-8")
    assert conv.get("vad_silence_ms") == 220


def test_чужие_ключи_в_файле_сохраняются(конфиг):
    """Настройки лежат в одном файле с ключами API. Перезаписать его целиком
    значило бы отобрать у человека доступ к Gemini заодно с настройкой."""
    конфиг.write_text(json.dumps({"gemini_api_key": "секрет"}), encoding="utf-8")

    conv.set("vad_silence_ms", 400)

    данные = json.loads(конфиг.read_text(encoding="utf-8"))
    assert данные["gemini_api_key"] == "секрет"
    assert данные["vad_silence_ms"] == 400


def test_испорченный_файл_не_отменяет_сохранение(конфиг):
    конфиг.write_text("{это не json", encoding="utf-8")
    assert conv.set("vad_silence_ms", 300) is True


def test_переменная_среды_перебивает_файл(конфиг, monkeypatch):
    """Ею пользуются, когда надо проверить одно число на один запуск."""
    conv.set("vad_silence_ms", 400)
    monkeypatch.setenv("VAD_SILENCE_MS", "900")

    assert conv.get("vad_silence_ms") == 900


def test_несуществующая_настройка_не_пишется(конфиг):
    assert conv.set("цвет_реактора", "красный") is False
    assert conv.get("цвет_реактора") is None


def test_у_каждой_настройки_есть_пояснение():
    """Настройка, смысл которой надо где-то прочитать, не будет тронута
    никогда."""
    for описание in conv.ОПИСАНИЯ:
        assert len(описание.пояснение) > 40, описание.ключ
        assert описание.подпись


# ─── Связка с хвостом микрофона ──────────────────────────────────────────────

def test_длинное_окно_тянет_хвост_за_собой():
    import main as jarvis_main

    было = jarvis_main.MIC_HANGOVER_MS
    try:
        jarvis_main._sync_hangover(900)
        assert jarvis_main.MIC_HANGOVER_MS >= 1800
    finally:
        jarvis_main._sync_hangover(jarvis_main._HANGOVER_BASE_MS // 2)


def test_короткое_окно_не_опускает_хвост_ниже_проверенного():
    """На 450 мс замерено, что ответ вообще приходит. Ниже — не экономия."""
    import main as jarvis_main

    jarvis_main._sync_hangover(50)
    assert jarvis_main.MIC_HANGOVER_MS == jarvis_main._HANGOVER_BASE_MS


def test_кадры_хвоста_считаются_заново():
    import main as jarvis_main

    было = jarvis_main.MIC_HANGOVER_FRAMES
    try:
        jarvis_main._sync_hangover(1000)
        assert jarvis_main.MIC_HANGOVER_FRAMES > было
    finally:
        jarvis_main._sync_hangover(jarvis_main._HANGOVER_BASE_MS // 2)


# ─── Окно ────────────────────────────────────────────────────────────────────

def test_кнопка_настроек_есть_в_окне():
    """Кнопка без обработчика — это кнопка, о которой узнают нажатием."""
    from ui import MainWindow
    assert callable(getattr(MainWindow, "_open_conversation_settings", None))


@pytest.fixture
def qt():
    # Ссылку надо держать: собранный сборщиком мусора QApplication роняет
    # процесс целиком, а не проваливает тест.
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_окно_сохраняет_то_что_показывает(qt, конфиг, monkeypatch):
    from PyQt6.QtWidgets import QDialog, QPushButton, QSpinBox, QWidget
    from ui import MainWindow

    поймано = {}
    monkeypatch.setattr(QDialog, "exec", lambda self: поймано.setdefault("окно", self))

    class _Хозяин(QWidget):
        _open_conversation_settings = MainWindow._open_conversation_settings

        def write_log(self, текст):
            pass

    _Хозяин()._open_conversation_settings()
    окно = поймано["окно"]

    счётчики = окно.findChildren(QSpinBox)
    assert счётчики, "числовые настройки не показаны"
    счётчики[0].setValue(640)

    сохранить = next(b for b in окно.findChildren(QPushButton)
                     if b.text() == "СОХРАНИТЬ")
    сохранить.click()

    assert conv.get("vad_silence_ms") == 640
