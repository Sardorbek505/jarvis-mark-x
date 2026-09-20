"""Пока не позвали — наружу не уходит ничего.

Микрофон открыт всегда, и каждый громкий кадр уезжает в Gemini: разговор в
комнате, телевизор и чужие слова оплачиваются и покидают машину, хотя
обращались не к ассистенту.

Самое важное здесь — не то, что шлюз запирает, а то, что он НЕ запирает
намертво: детектор обучен на английском «hey Jarvis», и запертый вход без
рабочего ключа означает ассистента, который не отвечает никогда.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import wake_gate
from core.wake_gate import Шлюз


class _Часы:
    def __init__(self):
        self.сейчас = 500.0

    def __call__(self):
        return self.сейчас

    def через(self, секунд):
        self.сейчас += секунд


@pytest.fixture
def часы():
    return _Часы()


# ─── Выключенный шлюз ────────────────────────────────────────────────────────

def test_выключенный_шлюз_пускает_всё(часы):
    """Умолчание: слушаем как раньше. Детектор обучен на английском
    «hey Jarvis», и до проверки на живом голосе запирать за ним весь вход
    значит рискнуть оглохнуть."""
    шлюз = Шлюз(требовать_имя=False, часы=часы)

    assert шлюз.бодрствует is True
    часы.через(10000)
    assert шлюз.бодрствует is True


def test_умолчание_настройки_выключено():
    from core import settings

    assert settings.описание("wake_required").по_умолчанию is False


def test_в_пояснении_названа_причина_осторожности():
    """Человек включает это, читая одну строчку в окне. Там и должно быть
    сказано, что проверить сначала."""
    from core import settings

    пояснение = settings.описание("wake_required").пояснение.lower()

    assert "hey jarvis" in пояснение
    assert "ctrl+d" in пояснение


# ─── Включённый шлюз ─────────────────────────────────────────────────────────

def test_включённый_шлюз_спит_до_имени(часы):
    шлюз = Шлюз(требовать_имя=True, часы=часы)
    assert шлюз.бодрствует is False


def test_имя_будит(часы):
    шлюз = Шлюз(требовать_имя=True, часы=часы)

    проснулся = шлюз.разбудить("имя")

    assert проснулся is True
    assert шлюз.бодрствует is True
    assert шлюз.разбудил == "имя"


def test_второе_имя_подряд_не_считается_пробуждением(часы):
    """На пробуждение положен сигнал, на продление — нет. Иначе Джарвис
    звенел бы на каждую фразу разговора."""
    шлюз = Шлюз(требовать_имя=True, часы=часы)
    шлюз.разбудить("имя")

    assert шлюз.разбудить("имя") is False


def test_засыпает_по_тишине(часы):
    шлюз = Шлюз(требовать_имя=True, часы=часы)
    шлюз.разбудить("имя")

    часы.через(wake_gate.ТИШИНА_ДО_СНА_СЕК + 1)

    assert шлюз.бодрствует is False


def test_речь_продлевает_бодрствование(часы):
    """Разговор не должен обрываться на полуслове из-за того, что человек
    задумался на восемь секунд."""
    шлюз = Шлюз(требовать_имя=True, часы=часы)
    шлюз.разбудить("имя")

    for _ in range(5):
        часы.через(wake_gate.ТИШИНА_ДО_СНА_СЕК - 1)
        шлюз.слышна_речь()

    assert шлюз.бодрствует is True


def test_речь_во_сне_не_будит(часы):
    """Иначе телевизор держал бы шлюз открытым вечно — то есть шлюза бы не
    было вовсе."""
    шлюз = Шлюз(требовать_имя=True, часы=часы)

    шлюз.слышна_речь()

    assert шлюз.бодрствует is False


def test_осталось_показывает_сколько_до_сна(часы):
    шлюз = Шлюз(требовать_имя=True, часы=часы)
    шлюз.разбудить("имя")
    часы.через(4)

    assert шлюз.осталось() == pytest.approx(wake_gate.ТИШИНА_ДО_СНА_СЕК - 4)


def test_уснуть_можно_по_команде(часы):
    шлюз = Шлюз(требовать_имя=True, часы=часы)
    шлюз.разбудить("имя")
    шлюз.уснуть()

    assert шлюз.бодрствует is False
    assert шлюз.разбудил == ""


# ─── Проводка в Джарвисе ─────────────────────────────────────────────────────

import main as jarvis_main  # noqa: E402
from core.barge_in import Перебивание  # noqa: E402


class _Окно:
    muted = False

    def __init__(self):
        self.логи: list[str] = []

    def write_log(self, текст):
        self.логи.append(текст)

    def set_state(self, состояние):
        pass


class _Детектор:
    def __init__(self, слышит=False):
        self.слышит = слышит
        self.threshold_stage1 = 0.0
        self.threshold_stage2 = 0.0
        self.кадры = 0

    def process_pcm(self, pcm):
        self.кадры += 1
        return self.слышит


class _Кадр:
    @staticmethod
    def tobytes():
        return b"\x00\x01" * 640


def _джарвис(требовать_имя=True):
    j = jarvis_main.Jarvis.__new__(jarvis_main.Jarvis)
    j.ui = _Окно()
    j._gate = Шлюз(требовать_имя=требовать_имя)
    j._barge = Перебивание()
    j._wake_detector = _Детектор()
    j._name_hits = 0
    j._wake_chimes = 0
    j._loop = None
    return j


def test_без_детектора_шлюз_не_запирает_вход():
    """Запертый вход без рабочего ключа — это ассистент, который не
    отвечает никогда. Лучше слушать лишнее, чем не слышать вовсе."""
    j = _джарвис()
    j._wake_detector = None

    assert j._hear_name_while_asleep(_Кадр()) is True


def test_сломанный_детектор_тоже_не_запирает():
    j = _джарвис()

    class _Битый:
        threshold_stage1 = 0.0
        threshold_stage2 = 0.0

        def process_pcm(self, pcm):
            raise RuntimeError("модель не загрузилась")

    j._wake_detector = _Битый()

    assert j._hear_name_while_asleep(_Кадр()) is True


def test_молчащий_детектор_держит_шлюз_закрытым():
    j = _джарвис()
    j._wake_detector = _Детектор(слышит=False)

    assert j._hear_name_while_asleep(_Кадр()) is False
    assert j._gate.бодрствует is False


def test_услышанное_имя_будит_и_звенит(monkeypatch):
    звонки = []
    monkeypatch.setattr("core.wakeword.play_activation_chime",
                        lambda: звонки.append(1))
    j = _джарвис()

    j._wake_from_audio_thread("имя")

    assert j._gate.бодрствует is True
    assert j._name_hits == 1
    assert звонки == [1]


def test_продление_не_звенит(monkeypatch):
    """Звук, а не фраза, — и только на переход изо сна. Иначе он звенел бы
    на каждую реплику разговора."""
    звонки = []
    monkeypatch.setattr("core.wakeword.play_activation_chime",
                        lambda: звонки.append(1))
    j = _джарвис()

    j._wake_from_audio_thread("имя")
    j._wake_from_audio_thread("имя")

    assert len(звонки) == 1
    assert j._name_hits == 2            # слышали дважды, разбудили один раз


def test_сон_виден_в_диагностике():
    """Со шлюзом «он меня не слышит» чаще всего означает «он спит».
    Догадываться об этом по поведению нельзя."""
    from core import diagnostics

    j = _джарвис()
    разделы = diagnostics.собрать(j)
    слух = next(р for р in разделы if р.имя == "СЛУХ")
    строка = next(с for с in слух.строки if с.ключ == "шлюз имени")

    assert "СПИТ" in строка.значение
    assert строка.тревога is True


def test_бодрствование_показывает_остаток():
    from core import diagnostics

    j = _джарвис()
    j._gate.разбудить("имя")

    слух = next(р for р in diagnostics.собрать(j) if р.имя == "СЛУХ")
    строка = next(с for с in слух.строки if с.ключ == "шлюз имени")

    assert "уснёт через" in строка.значение
    assert "имя" in строка.значение


def test_выключенный_шлюз_не_засоряет_панель():
    """Строки про то, чего человек не включал, только мешают читать."""
    from core import diagnostics

    j = _джарвис(требовать_имя=False)
    слух = next(р for р in diagnostics.собрать(j) if р.имя == "СЛУХ")

    assert all(с.ключ != "шлюз имени" for с in слух.строки)
