"""Срабатывает ли ключевое слово на ВАШЕМ голосе.

Запуск:  python scripts/wake_check.py

Скрипт слушает микрофон и на каждом чанке печатает уверенность модели
hey_jarvis. Скажите «Джарвис» несколько раз обычным голосом и посмотрите на
пик. Порог первой стадии — 0.38, второй — 0.50.

Зачем это нужно. Модель openwakeword обучена на английском «hey jarvis».
На синтезированной речи замерено:

    «Джарвис» русским голосом      0.004
    «Эй, Джарвис» русским голосом  0.025
    «Jarvis» английским голосом    0.998

Разрыв в двести раз, но синтез — не живой голос, и произносить имя можно
по-разному. Этот скрипт заменяет догадку фактом: если ваш пик выше 0.5,
всё работает и делать ничего не нужно; если держится около нуля — детектор
в проде не сработает никогда, и нужен другой движок для русского.

Что именно ломается при низком пике: когда в колонках громко играет музыка,
микрофонный шлюз в main.py закрыт, и «Джарвис» через это окно — единственный
способ сказать ассистенту «приглуши». Ни на что другое этот детектор в проде
не влияет.
"""
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# ВАЖЕН ПОРЯДОК: onnxruntime до всего, что может подтянуть Qt (см. main.py).
try:
    import onnxruntime  # noqa: F401
except Exception:
    pass

from core.wake_detector import WakeWordDetector2Stage  # noqa: E402

SAMPLE_RATE = 16000
CHUNK = 1280            # 80 мс — рабочий чанк модели
SECONDS = 20.0
BAR_WIDTH = 40


def _bar(score: float) -> str:
    filled = int(round(score * BAR_WIDTH))
    return "#" * filled + "." * (BAR_WIDTH - filled)


def main() -> int:
    detector = WakeWordDetector2Stage(cooldown_sec=0.0)
    if detector._oww_model is None:
        print("\n  [!] ONNX-модель не загрузилась — проверять нечего.")
        print("      Обычно это конфликт DLL с PyQt6; в main.py он лечится")
        print("      прогревом onnxruntime до импорта UI.\n")
        return 2

    print("\n" + "=" * 66)
    print("  ПРОВЕРКА КЛЮЧЕВОГО СЛОВА НА ЖИВОМ ГОЛОСЕ")
    print("=" * 66)
    print("\n  Скажите «Джарвис» 5-6 раз, с паузами, обычным голосом.")
    print("  Потом, для сравнения, пару раз английское «Jarvis».")
    print(f"\n  Слушаю {SECONDS:.0f} секунд. Порог: 0.38 / 0.50\n")

    peak_overall = 0.0
    hits = 0
    deadline = time.time() + SECONDS

    def callback(indata, frames, time_info, status):
        nonlocal peak_overall, hits
        pcm = indata[:, 0].astype(np.int16).tobytes()
        if detector.process_pcm(pcm):
            hits += 1
        score, _ = detector._read_scores()
        peak_overall = max(peak_overall, score)

        rms = float(np.sqrt(np.mean(indata[:, 0].astype(np.float32) ** 2)))
        if score > 0.05 or rms > 300:
            mark = "  <-- СРАБОТАЛО" if score >= 0.5 else ""
            print(f"  {_bar(score)} {score:5.3f}   (громкость {rms:5.0f}){mark}")

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                            blocksize=CHUNK, callback=callback):
            while time.time() < deadline:
                time.sleep(0.1)
    except Exception as exc:
        print(f"\n  [!] Микрофон недоступен: {exc}\n")
        return 1

    print("\n" + "-" * 66)
    print(f"  Пик уверенности: {peak_overall:.3f}     подтверждений: {hits}")
    print("-" * 66)

    if peak_overall >= 0.5:
        print("\n  ВЫВОД: модель ваш голос понимает. Менять ничего не нужно.\n")
    elif peak_overall >= 0.38:
        print("\n  ВЫВОД: на грани — первая стадия проходит, вторая нет.")
        print("  Хватит понизить threshold_stage2 или произносить чётче.\n")
    else:
        print("\n  ВЫВОД: модель ваш голос НЕ понимает.")
        print("  В проде «Джарвис» поверх музыки работать не будет.")
        print("  Нужен движок с русским: vosk (офлайн, бесплатно, уже стоит),")
        print("  Porcupine с русским ключевым словом, либо своя модель")
        print("  openwakeword, обученная на синтезе piper.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
