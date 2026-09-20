"""«Почему он меня не слышит» — прибор, а не догадка.

Обе половины голосового круга отказывают МОЛЧА: распознавание, которое вас
игнорирует, и синтез, который не издаёт звука, снаружи выглядят одинаково —
ничего не падает, ничего не мигает, окно живёт как ни в чём не бывало. Каждая
беда в этом круге стоила круга догадок.

Данные для ответа в программе уже были, но лежали в трёх местах и утекали в
лог. Здесь проверяется, что снимок отвечает на конкретные вопросы — и что он
не падает там, где его как раз и открывают: когда что-то сломалось.
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import diagnostics
from core.barge_in import Перебивание
from core.latency import LatencyTracker


class _Очередь:
    def __init__(self, размер=0):
        self._размер = размер

    def qsize(self):
        return self._размер


def _стенд(**поля):
    """Джарвис, каким его видит панель: только поля, ничего живого."""
    основа = dict(
        ui=SimpleNamespace(muted=False),
        _gate_passed_total=1200,
        _gate_totals={"тихо для порога MIC_RMS_THRESHOLD": 4000},
        _last_pass_at=time.time(),
        _last_gate_reason="тихо для порога MIC_RMS_THRESHOLD",
        _is_speaking=False,
        _spoken_count=7,
        _fish_fallbacks=0,
        audio_in_queue=_Очередь(),
        _active_synth_tasks=0,
        _wake_detector=SimpleNamespace(_oww_model=object()),
        _aec=object(),
        _aec_erle=31.4,
        _name_hits=3,
        _barge_count=2,
        _last_barge_refusal="",
        _barge=Перебивание(),
        _latency=LatencyTracker(enabled=False),
        session=object(),
        _resume_handle="ручка",
        _reconnects=0,
    )
    основа.update(поля)
    return SimpleNamespace(**основа)


def _найти(разделы, имя_раздела, ключ):
    раздел = next(р for р in разделы if р.имя == имя_раздела)
    return next((с for с in раздел.строки if с.ключ == ключ), None)


# ─── Отвечает на вопросы, ради которых написан ───────────────────────────────

def test_снимок_состоит_из_всех_разделов():
    имена = [р.имя for р in diagnostics.собрать(_стенд())]
    assert имена == ["СЛУХ", "РЕЧЬ", "ПЕРЕБИВАНИЕ", "ЗАДЕРЖКА", "СВЯЗЬ"]


def test_давно_не_слышал_это_тревога():
    """Главный вопрос к такой машине. «Слышит» и «не слышит» снаружи
    выглядят одинаково — здесь они должны выглядеть по-разному."""
    свежий = _найти(diagnostics.собрать(_стенд()), "СЛУХ", "слышит")
    глухой = _найти(diagnostics.собрать(
        _стенд(_last_pass_at=time.time() - 120)), "СЛУХ", "слышит")

    assert свежий.тревога is False
    assert глухой.тревога is True


def test_ни_разу_не_слышал_тоже_тревога():
    строка = _найти(diagnostics.собрать(_стенд(_last_pass_at=0.0)), "СЛУХ", "слышит")
    assert строка.тревога is True
    assert "ни разу" in строка.значение


def test_выключенный_микрофон_виден_сразу():
    """Ctrl+M нажимается случайно, а выглядит это как «оглох»."""
    разделы = diagnostics.собрать(_стенд(ui=SimpleNamespace(muted=True)))
    строка = _найти(разделы, "СЛУХ", "микрофон")

    assert строка is not None and строка.тревога is True


def test_главная_причина_отказа_названа():
    разделы = diagnostics.собрать(_стенд(_gate_totals={
        "зажмите Ctrl+Space, чтобы говорить": 900,
        "тихо для порога MIC_RMS_THRESHOLD": 100,
    }))
    строка = _найти(разделы, "СЛУХ", "чаще всего отбрасывал")

    assert "Ctrl+Space" in строка.значение
    assert "900" in строка.значение


def test_отклонённое_имя_объясняется():
    """«Я же его позвал, почему он не замолчал» — самый частый вопрос после
    того, как перебивание появилось."""
    разделы = diagnostics.собрать(_стенд(_last_barge_refusal="ответ только начался"))
    строка = _найти(разделы, "ПЕРЕБИВАНИЕ", "последний раз не принял")

    assert строка is not None
    assert "только начался" in строка.значение
    assert строка.тревога is True


def test_незагруженный_детектор_это_тревога():
    """Без него перебить голосом нельзя вовсе, и человек об этом не узнает
    никак — он просто зовёт, а ничего не происходит."""
    разделы = diagnostics.собрать(_стенд(_wake_detector=None))
    строка = _найти(разделы, "ПЕРЕБИВАНИЕ", "детектор имени")

    assert "НЕ ЗАГРУЖЕН" in строка.значение
    assert строка.тревога is True


def test_отсутствие_эхоподавления_названо():
    разделы = diagnostics.собрать(_стенд(_aec=None))
    строка = _найти(разделы, "ПЕРЕБИВАНИЕ", "эхоподавление")

    assert строка.тревога is True
    assert "строже" in строка.значение


def test_уступки_fish_видны():
    """Голос незаметно меняется на другой — человек слышит, что «стал не
    тот», и не знает почему."""
    строка = _найти(diagnostics.собрать(_стенд(_fish_fallbacks=3)),
                    "РЕЧЬ", "Fish уступал Edge")
    assert строка.тревога is True


def test_оборванная_сессия_видна():
    строка = _найти(diagnostics.собрать(_стенд(session=None)), "СВЯЗЬ", "сессия")
    assert строка.тревога is True


def test_медленный_ответ_подсвечивается():
    замер = LatencyTracker(enabled=True)
    замер._stats["answered"].add(4200)

    строка = _найти(diagnostics.собрать(_стенд(_latency=замер)), "ЗАДЕРЖКА", "отвечает")

    assert "4200" in строка.значение
    assert строка.тревога is True


# ─── Не падает там, где его открывают ────────────────────────────────────────

def test_снимок_с_пустого_объекта_не_падает():
    """Панель открывают как раз тогда, когда что-то пошло не так. Упасть в
    этот момент — худшее, что она может сделать."""
    разделы = diagnostics.собрать(SimpleNamespace())

    assert len(разделы) == 5
    assert all(not с.ключ.startswith("сломалось")
               for р in разделы for с in р.строки)


def test_сломанный_раздел_не_уносит_остальные():
    class _Взрывной:
        @property
        def _gate_passed_total(self):
            raise RuntimeError("бум")

    разделы = diagnostics.собрать(_Взрывной())

    assert len(разделы) == 5
    assert any(с.ключ == "сломалось" for р in разделы for с in р.строки)


def test_снимок_печатается_строками():
    """Чтобы «у меня не работает» можно было прислать одним куском, а не
    пересказывать по памяти."""
    текст = diagnostics.текстом(diagnostics.собрать(_стенд(_fish_fallbacks=2)))

    assert "◈ СЛУХ" in текст
    assert "◈ ПЕРЕБИВАНИЕ" in текст
    assert " ! " in текст          # тревожные строки помечены


def test_окно_умеет_показать_панель():
    """Кнопка без обработчика — это кнопка, о которой узнают нажатием."""
    from ui import MainWindow

    assert callable(getattr(MainWindow, "_toggle_diagnostics", None))
    assert callable(getattr(MainWindow, "bind_diagnostics", None))


def test_консольный_режим_не_ломается_от_привязки():
    from core.headless_ui import HeadlessUI

    assert callable(getattr(HeadlessUI, "bind_diagnostics", None))


# ─── Само окно ───────────────────────────────────────────────────────────────

import os  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qt():
    # Ссылку надо держать: собранный сборщиком мусора QApplication роняет
    # процесс целиком, а не проваливает тест.
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def хозяин(qt):
    from PyQt6.QtWidgets import QWidget
    from ui import MainWindow

    class _Хозяин(QWidget):
        _toggle_diagnostics = MainWindow._toggle_diagnostics
        bind_diagnostics = MainWindow.bind_diagnostics

        def __init__(self):
            super().__init__()
            self._diag_source = None
            self.лог: list[str] = []

        def write_log(self, текст):
            self.лог.append(текст)

    return _Хозяин()


def test_панель_показывает_снимок_сразу(хозяин):
    """Показать ДО первой отрисовки: иначе первые полсекунды панель стоит
    пустой — ровно в тот момент, когда на неё смотрят."""
    from PyQt6.QtWidgets import QPlainTextEdit

    хозяин.bind_diagnostics(lambda: diagnostics.собрать(_стенд()))
    хозяин._toggle_diagnostics()

    текст = хозяин._diag_panel.findChild(QPlainTextEdit).toPlainText()

    assert текст.count("◈") == 5
    assert "СЛУХ" in текст


def test_повторное_нажатие_закрывает(хозяин):
    хозяин.bind_diagnostics(lambda: diagnostics.собрать(_стенд()))
    хозяин._toggle_diagnostics()
    хозяин._toggle_diagnostics()

    assert хозяин._diag_panel.isVisible() is False


def test_без_источника_панель_объясняет_а_не_падает(хозяин):
    хозяин._toggle_diagnostics()

    assert getattr(хозяин, "_diag_panel", None) is None
    assert any("недоступна" in с for с in хозяин.лог)


def test_снимок_копируется_одной_кнопкой(qt, хозяин):
    """Чтобы «у меня не работает» можно было прислать одним куском."""
    from PyQt6.QtWidgets import QApplication, QPushButton

    хозяин.bind_diagnostics(lambda: diagnostics.собрать(_стенд()))
    хозяин._toggle_diagnostics()

    кнопка = next(b for b in хозяин._diag_panel.findChildren(QPushButton)
                  if "КОПИР" in b.text())
    кнопка.click()

    assert QApplication.clipboard().text().count("◈") == 5


def test_сломанный_источник_не_роняет_панель(хозяин):
    """Панель открывают, когда что-то уже не так. Упасть в этот момент —
    худшее, что она может сделать."""
    from PyQt6.QtWidgets import QPlainTextEdit

    хозяин.bind_diagnostics(lambda: (_ for _ in ()).throw(RuntimeError("бум")))
    хозяин._toggle_diagnostics()

    текст = хозяин._diag_panel.findChild(QPlainTextEdit).toPlainText()
    assert "не собрался" in текст
