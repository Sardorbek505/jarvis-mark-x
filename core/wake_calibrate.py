"""Калибровка слова «Джарвис» на голосе владельца.

`JARVIS.exe --wake-calibrate` (или `python main.py --wake-calibrate`):
8 раз сказать «Джарвис» и несколько обычных фраз. Vosk слушает, как он слышит
имя именно у этого человека и этим микрофоном, и сохраняет варианты в
wake_aliases.json. После этого слово «Джарвис» слушается прямо на компьютере
(до него звук никуда не уходит) — см. core/wake_vosk.py.

Сама наука — в wake_vosk.calibrate; здесь только запись и окно.
"""
from __future__ import annotations

import logging

from core import wake_vosk

logger = logging.getLogger(__name__)

NAME_TAKES = 8
NEGATIVE_PHRASES = [
    "Какая сегодня погода?",
    "Включи музыку погромче",
    "Сегодня очень жарко",
    "Открой браузер",
    "Дарвин написал книгу",
]
TAKE_SEC = 2.2


def steps() -> list[tuple[str, bool]]:
    """(что сказать, это имя?) — по порядку."""
    return [("Джарвис", True)] * NAME_TAKES + [(p, False) for p in NEGATIVE_PHRASES]


def run_calibration(name_pcms: list[bytes], negative_pcms: list[bytes], model=None) -> dict:
    """Выучить, сохранить и вернуть отчёт. Нет вариантов — файл не трогаем."""
    if model is None:
        import vosk
        vosk.SetLogLevel(-1)
        model_dir = wake_vosk.find_model_dir()
        if not model_dir:
            raise FileNotFoundError(f"нет модели models/{wake_vosk.MODEL_DIRNAME}")
        model = vosk.Model(str(model_dir))
    report = wake_vosk.calibrate(model, name_pcms, negative_pcms)
    if report["aliases"]:
        wake_vosk.save_aliases(report["aliases"], {k: v for k, v in report.items() if k != "aliases"})
    return report


def describe(report: dict) -> str:
    if not report.get("aliases"):
        return ("Не получилось: Vosk ни разу не услышал имя одинаково. Говорите «Джарвис» чётко, "
                "в обычную громкость, рядом с микрофоном — и попробуйте ещё раз.")
    return (f"Готово. Ваше «Джарвис» Vosk слышит как: {', '.join('«' + a + '»' for a in report['aliases'])}.\n"
            f"Проверка на ваших же записях: имя узнано {report.get('hits', 0)} из {report['names']}, "
            f"ложных срабатываний {report.get('false', 0)} из {report['negatives']}.\n"
            "Перезапустите Джарвиса — слово будет слушаться прямо на компьютере.")


def _record(sec: float, device=None) -> bytes:
    import sounddevice as sd
    data = sd.rec(int(sec * wake_vosk.SAMPLE_RATE), samplerate=wake_vosk.SAMPLE_RATE, channels=1,
                  dtype="int16", device=device)
    sd.wait()
    return data.tobytes()


def run_console(device=None) -> int:
    names, negs = [], []
    for i, (say, is_name) in enumerate(steps(), 1):
        input(f"[{i}/{len(steps())}] Нажмите Enter и скажите: «{say}» ")
        (names if is_name else negs).append(_record(TAKE_SEC, device))
    print(describe(run_calibration(names, negs)))
    return 0


def run_gui(device=None) -> int:
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtWidgets import (QApplication, QDialog, QLabel, QMessageBox, QProgressBar, QPushButton,
                                 QVBoxLayout)
    app = QApplication.instance() or QApplication([])
    plan = steps()
    names: list[bytes] = []
    negs: list[bytes] = []

    dlg = QDialog()
    dlg.setWindowTitle("ДЖАРВИС — обучение слова «Джарвис»")
    lay = QVBoxLayout(dlg)
    hint = QLabel()
    hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    hint.setWordWrap(True)
    bar = QProgressBar()
    bar.setMaximum(len(plan))
    btn = QPushButton("Записать")
    for w in (hint, bar, btn):
        lay.addWidget(w)
    dlg.resize(420, 170)

    def show_step():
        i = len(names) + len(negs)
        say, is_name = plan[i]
        what = "имя" if is_name else "обычную фразу (без имени)"
        hint.setText(f"Шаг {i + 1} из {len(plan)}: скажите {what}\n\n«{say}»\n\n"
                     "Нажмите «Записать» и сразу говорите — запись 2 секунды.")
        bar.setValue(i)
        btn.setEnabled(True)
        btn.setText("Записать")

    def record():
        import sounddevice as sd
        btn.setEnabled(False)
        btn.setText("Говорите…")
        frames = sd.rec(int(TAKE_SEC * wake_vosk.SAMPLE_RATE), samplerate=wake_vosk.SAMPLE_RATE,
                        channels=1, dtype="int16", device=device)

        def done():
            sd.wait()
            i = len(names) + len(negs)
            (names if plan[i][1] else negs).append(frames.tobytes())
            if len(names) + len(negs) < len(plan):
                show_step()
            else:
                dlg.accept()
        QTimer.singleShot(int(TAKE_SEC * 1000) + 100, done)

    btn.clicked.connect(record)
    show_step()
    if not dlg.exec():
        return 1
    hint.setText("Анализирую…")
    app.processEvents()
    try:
        report = run_calibration(names, negs)
        msg = describe(report)
    except Exception as exc:
        logger.exception("Калибровка")
        report, msg = {}, f"Калибровка не удалась: {type(exc).__name__}: {exc}"
    QMessageBox.information(None, "ДЖАРВИС — слово «Джарвис»", msg)
    return 0 if report.get("aliases") else 1


if __name__ == "__main__":
    raise SystemExit(run_console())
