"""Экран «Настройки»: микрофон, динамик, чувствительность, голос, обращение,
камера, автозапуск, брифинг, капсула.

Всё сохраняется сразу (core/settings.py) и применяется без перезапуска:
Джарвис переоткрывает микрофон/динамик между фразами, порог и обращение
меняются на лету. Полоска громкости показывает, что слышит выбранный
микрофон, а отметка на ней — порог: всё тише отметки Джарвис не услышит.
"""
from __future__ import annotations

import logging
import math
import sys
import threading

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                             QSlider, QVBoxLayout, QWidget)

from core import settings as S
from ui import C
from ui_icons import qicon
from ui_kit import STYLE, IconBadge, Toggle, _cap, _label, _line

logger = logging.getLogger(__name__)

EXTRA = f"""
QLabel#name {{ color: {C.WHITE}; font-weight: 600; }}
QLabel#saved {{ color: {C.GREEN}; font-size: 12px; }}
QSlider::groove:horizontal {{ height: 4px; background: {C.BORDER_B}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {C.PRI_DIM}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {C.PRI}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
"""

VOICES = [("fish", "Fish Audio — голос JARVIS из фильмов"), ("gemini", "Gemini — быстрее, встроенный голос")]
EDGE_VOICES = [("ru-RU-DmitryNeural", "Дмитрий (мужской)"), ("ru-RU-SvetlanaNeural", "Светлана (женский)")]
WAKE = [("wake_word", "Только когда зовут «Джарвис»"), ("always_on", "Отвечать на всё, без имени")]
AWAKE = [(15, "15 секунд"), (30, "30 секунд"), (60, "1 минуту"), (120, "2 минуты")]
CAMERAS = [(i, f"Камера {i + 1}" + (" — обычно встроенная" if i == 0 else "")) for i in range(4)]
# Порог RMS: ниже — речь не считается. Ползунок — «чувствительность» (выше = тише слышит).
TH_MIN, TH_MAX = 40, 700


def threshold_to_slider(th: float) -> int:
    """Логарифмическая шкала: внизу порога разница заметнее."""
    th = min(TH_MAX, max(TH_MIN, th))
    return round(100 - 100 * math.log(th / TH_MIN) / math.log(TH_MAX / TH_MIN))


def slider_to_threshold(v: int) -> int:
    return round(TH_MIN * (TH_MAX / TH_MIN) ** ((100 - v) / 100))


def sensitivity_word(v: int) -> str:
    return "очень высокая" if v >= 85 else "высокая" if v >= 60 else "средняя" if v >= 35 else "низкая"


class LevelMeter(QWidget):
    """Громкость микрофона и отметка порога (в той же шкале RMS)."""

    FULL = 4000.0                           # как _MIC_FULL_SCALE в main.py

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.level, self.peak, self.threshold = 0.0, 0.0, 150.0

    def _x(self, rms: float) -> float:
        # Корень — тихая речь не жмётся к левому краю.
        return (self.width() - 2) * min(1.0, math.sqrt(max(0.0, rms) / self.FULL)) + 1

    def set_level(self, rms: float):
        self.level = rms
        self.peak = max(rms, self.peak * 0.93)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 6.5, -0.5, -6.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C.BORDER))
        p.drawRoundedRect(r, 4, 4)
        heard = self.level >= self.threshold
        p.setBrush(QColor(C.GREEN if heard else C.PRI_DIM))
        p.drawRoundedRect(QRectF(r.x(), r.y(), self._x(self.level) - r.x(), r.height()), 4, 4)
        p.setPen(QPen(QColor(C.TEXT_MED), 1.5))
        px = self._x(self.peak)
        p.drawLine(int(px), int(r.top()), int(px), int(r.bottom()))
        tx = self._x(self.threshold)                       # порог: тише — не услышу
        p.setPen(QPen(QColor(C.ACC), 2))
        p.drawLine(int(tx), 1, int(tx), self.height() - 1)


class MicProbe:
    """Слушает выбранный микрофон, пока экран открыт (свой поток, не мешает Джарвису)."""

    def __init__(self, on_level):
        self.on_level = on_level
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, device_name: str):
        self.stop()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(device_name, self._stop), daemon=True,
                                        name="settings-mic")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self, name: str, stop: threading.Event):
        try:
            import numpy as np
            import sounddevice as sd
            dev = S.find_device(name, "input") if name else None

            def cb(indata, frames, t, status):
                if not stop.is_set():                  # экран закрыт — окну больше не шлём
                    self.on_level(float(np.sqrt(np.mean(indata.astype(np.float32) ** 2))))
            with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1024, device=dev,
                                callback=cb):
                while not stop.wait(0.1):
                    pass
        except Exception as exc:
            logger.debug("Проверка микрофона: %s", exc)


def play_test(device_name: str) -> str:
    """Короткий двойной сигнал на выбранном динамике. → "" или текст ошибки."""
    try:
        import numpy as np
        import sounddevice as sd
        rate = 24000
        t = np.arange(int(rate * 0.18)) / rate
        env = np.minimum(1, np.minimum(t, t[::-1]) * 40)
        tone = lambda f: 0.25 * np.sin(2 * np.pi * f * t) * env  # noqa: E731
        sig = np.concatenate([tone(880), np.zeros(int(rate * 0.06)), tone(1320)]).astype(np.float32)
        dev = S.find_device(device_name, "output") if device_name else None
        sd.play(sig, rate, device=dev, blocking=True)
        return ""
    except Exception as exc:
        return str(exc)


class SettingsDialog(QDialog):
    _level = pyqtSignal(float)
    _tested = pyqtSignal(str)

    def __init__(self, parent=None, probe: bool = True):
        super().__init__(parent)
        self.setWindowTitle("ДЖАРВИС — настройки")
        self.setStyleSheet(STYLE + EXTRA)
        self.resize(880, 760)
        self.values = S.load()
        self._probe_on = probe
        self.probe = MicProbe(self._level.emit)
        self._tested.connect(self._on_tested)
        self.destroyed.connect(self.probe.stop)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(_line())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        canvas = QWidget()
        canvas.setObjectName("canvas")
        outer = QHBoxLayout(canvas)
        outer.setContentsMargins(0, 0, 0, 0)
        col_w = QWidget()
        col_w.setMaximumWidth(760)
        self.col = QVBoxLayout(col_w)
        self.col.setContentsMargins(28, 22, 28, 28)
        self.col.setSpacing(10)
        self._build()
        self.col.addStretch(1)
        outer.addStretch(1)
        outer.addWidget(col_w, 100)
        outer.addStretch(1)
        scroll.setWidget(canvas)
        root.addWidget(scroll, 1)
        self._saved_tmr = QTimer(self)
        self._saved_tmr.setSingleShot(True)
        self._saved_tmr.timeout.connect(self.saved.clear)

    # ── вид ─────────────────────────────────────────────────────────────────
    def _header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(14)
        lay.addWidget(IconBadge("gear", 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(_label("ДЖАРВИС", "brand"))
        col.addWidget(_label("Настройки", "h1", wrap=False))
        lay.addLayout(col)
        lay.addStretch(1)
        self.saved = _label("", "saved", wrap=False)
        lay.addWidget(self.saved)
        return w

    def _section(self, icon: str, title: str) -> QVBoxLayout:
        self.col.addSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(qicon(icon, 13, C.PRI).pixmap(13, 13))
        head.addWidget(ic)
        head.addWidget(_cap(title))
        head.addStretch(1)
        self.col.addLayout(head)
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(10)
        self.col.addWidget(card)
        return lay

    def _row(self, lay: QVBoxLayout, name: str, hint: str, widget: QWidget, stretch_widget: bool = False):
        if lay.count():
            lay.addWidget(_line())
        row = QHBoxLayout()
        row.setSpacing(16)
        txt = QVBoxLayout()
        txt.setSpacing(1)
        txt.addWidget(_label(name, "name"))
        if hint:
            txt.addWidget(_label(hint, "hint"))
        row.addLayout(txt, 1)
        row.addWidget(widget, 1 if stretch_widget else 0)
        lay.addLayout(row)

    def _combo(self, items: list[tuple], current, key: str, width: int = 300) -> QComboBox:
        cb = QComboBox()
        cb.setFixedWidth(width)
        for value, text in items:
            cb.addItem(text, value)
        i = cb.findData(current)
        cb.setCurrentIndex(i if i >= 0 else 0)
        cb.setProperty("setting", key)
        # Только методы, не lambda: удалят экран — Qt сам разорвёт связи
        # (lambda жила бы дальше и трогала мёртвое окно — падение программы).
        cb.currentIndexChanged.connect(self._combo_changed)
        return cb

    def _combo_changed(self, _i: int):
        cb = self.sender()
        self.save(cb.property("setting"), cb.currentData())

    def _toggle_changed(self, on: bool):
        self.save(self.sender().property("setting"), on)

    def _toggle(self, key: str, on: bool) -> Toggle:
        t = Toggle()
        t.setChecked(bool(on))
        t.setProperty("setting", key)
        t.toggled.connect(self._toggle_changed)
        return t

    def _build(self):
        v = self.values
        # Звук
        lay = self._section("mic", "Микрофон")
        mics = S.devices("input")
        cur = v["mic"] if v["mic"] in mics or not v["mic"] else v["mic"]
        items = [("", "Автовыбор (рекомендуется)")] + [(n, n) for n in mics]
        if cur and cur not in mics:
            items.append((cur, f"{cur} (сейчас не подключён)"))
        self.mic = self._combo(items, cur, "mic", 340)
        self.mic.currentIndexChanged.connect(self._restart_probe)
        self._row(lay, "Микрофон", "Откуда Джарвис слушает. Номера устройств не нужны — запомню по имени.", self.mic)
        self.meter = LevelMeter()
        self.meter.threshold = float(v["mic_threshold"])
        # Связь с методом виджета (не lambda): удалят окно — Qt сам её разорвёт.
        self._level.connect(self.meter.set_level)
        box = QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(self.meter)
        self.meter_hint = _label("Скажите что-нибудь: зелёная полоска — слышу, оранжевая черта — порог.", "hint")
        box.addWidget(self.meter_hint)
        wrap = QWidget()
        wrap.setLayout(box)
        box.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_line())
        lay.addWidget(wrap)
        self.sens = QSlider(Qt.Orientation.Horizontal)
        self.sens.setRange(0, 100)
        self.sens.setFixedWidth(260)
        self.sens.setValue(threshold_to_slider(float(v["mic_threshold"])))
        self.sens_label = _label("", "hint", wrap=False)
        self.sens.valueChanged.connect(self._on_sens)
        self.sens.sliderReleased.connect(self._sens_released)
        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(2)
        sl.addWidget(self.sens)
        sl.addWidget(self.sens_label, 0, Qt.AlignmentFlag.AlignRight)
        self._on_sens(self.sens.value())
        self._row(lay, "Чувствительность", "Слышит шорохи и чужую речь — убавьте; не слышит вас — прибавьте.", sw)
        self.ignore = self._toggle("ignore_speakers", v["ignore_speakers"])
        self._row(lay, "Не слушать, пока играет музыка или фильм",
                  "Иначе звук из колонок Джарвис примет за вашу речь.", self.ignore)

        lay = self._section("volume", "Динамик")
        outs = S.devices("output")
        spk = v["speaker"]
        items = [("", "Системный по умолчанию")] + [(n, n) for n in outs]
        if spk and spk not in outs:
            items.append((spk, f"{spk} (сейчас не подключён)"))
        self.spk = self._combo(items, spk, "speaker", 340)
        self._row(lay, "Куда говорит Джарвис", "Смена — со следующей фразы, без перезапуска.", self.spk)
        self.test_btn = QPushButton("  Проверить звук")
        self.test_btn.setIcon(qicon("volume", 13, C.TEXT_MED))
        self.test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_btn.clicked.connect(self._test_speaker)
        self._row(lay, "Проверка", "Короткий сигнал в выбранном динамике.", self.test_btn)

        lay = self._section("speak", "Голос")
        self.voice = self._combo(VOICES, v["voice"], "voice", 340)
        self._row(lay, "Голос Джарвиса", "Fish нужен ключ (экран «Ключи»); без него — Gemini.", self.voice)
        self.edge = self._combo(EDGE_VOICES, v["edge_voice"], "edge_voice", 340)
        self._row(lay, "Запасной голос", "Если Fish недоступен или нет связи — бесплатный голос Microsoft.",
                  self.edge)

        lay = self._section("mic", "Обращение")
        self.wake = self._combo(WAKE, v["wake_mode"], "wake_mode", 340)
        self._row(lay, "Когда отвечать", "«Без имени» — отвечает на любую речь в комнате.", self.wake)
        self.awake = self._combo(AWAKE, int(v["awake_sec"]), "awake_sec", 340)
        self._row(lay, "Разговор без имени после ответа",
                  "Столько после ответа можно продолжать, не говоря «Джарвис».", self.awake)

        lay = self._section("eye", "Камера")
        self.camera = self._combo(CAMERAS, int(v["camera"]), "camera", 340)
        self._row(lay, "Какой камерой смотреть", "Для «посмотри на меня», «что у меня в руке».", self.camera)

        lay = self._section("gear", "Общее")
        self.autostart = Toggle()
        self.autostart.setChecked(_autostart_enabled())
        self.autostart.setEnabled(sys.platform == "win32")
        self.autostart.toggled.connect(self._set_autostart)
        self._row(lay, "Запускать вместе с Windows", "Джарвис сам стартует после включения ПК.", self.autostart)
        self.briefing = self._toggle("briefing", v["briefing"])
        self._row(lay, "Утренний брифинг", "Погода, дела, пары и матч клуба — при первом «Джарвис» утром.",
                  self.briefing)
        self.island = self._toggle("island", v["island"])
        self._row(lay, "Капсула сверху экрана", "Когда окно свёрнуто. Применится после перезапуска Джарвиса.",
                  self.island)
        self.anims = self._toggle("animations", v["animations"])
        self._row(lay, "Анимации", "Плавные переходы, подсветка, волны от нажатий. Выключите на слабом ПК.",
                  self.anims)

    # ── действия ────────────────────────────────────────────────────────────
    def save(self, key: str, value):
        try:
            S.set(key, value)
        except Exception as exc:
            logger.warning("Настройка %s: %s", key, exc)
            self.saved.setText("Не сохранилось")
            return
        self.values[key] = value
        note = " — после перезапуска" if S.BY_KEY[key].restart else ""
        self.saved.setText("✓ Сохранено" + note)
        self._saved_tmr.start(2500)
        if key == "mic_threshold":
            self.meter.threshold = float(value)
            self.meter.update()

    def _sens_released(self):
        self.save("mic_threshold", slider_to_threshold(self.sens.value()))

    def _on_sens(self, v: int):
        self.sens_label.setText(f"{sensitivity_word(v)} · порог {slider_to_threshold(v)}")
        self.meter.threshold = float(slider_to_threshold(v))
        self.meter.update()

    def _test_speaker(self):
        self.test_btn.setEnabled(False)
        name = self.spk.currentData() or ""
        threading.Thread(target=lambda: self._tested.emit(play_test(name)), daemon=True,
                         name="settings-speaker").start()

    def _on_tested(self, err: str):
        self.test_btn.setEnabled(True)
        self.saved.setText("Не играет: " + err[:60] if err else "✓ Прозвучало?")
        self._saved_tmr.start(4000)

    def _set_autostart(self, on: bool):
        try:
            from ui_setup import set_windows_autostart
            ok = set_windows_autostart(on)
        except Exception as exc:
            logger.warning("Автозапуск: %s", exc)
            ok = False
        self.saved.setText("✓ Сохранено" if ok else "Не получилось изменить автозапуск")
        self._saved_tmr.start(2500)

    def _restart_probe(self, *_):
        if self._probe_on and self.isVisible():
            self.probe.start(self.mic.currentData() or "")

    def showEvent(self, ev):
        super().showEvent(ev)
        self._restart_probe()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.probe.stop()


def _autostart_enabled() -> bool:
    try:
        from ui_setup import is_windows_autostart_enabled
        return bool(is_windows_autostart_enabled())
    except Exception:
        return False
