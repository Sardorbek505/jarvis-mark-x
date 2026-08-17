"""Слышит ли Джарвис вас вообще — и каким входом.

Запуск:  python scripts/mic_check.py

Пять секунд на каждое устройство: говорите обычным голосом. Скрипт покажет
громкость и сразу скажет, пройдёт ли такой звук порог MIC_RMS_THRESHOLD, ниже
которого кадры в облако не уходят вовсе.

Появился после 18.08.2026: Джарвис три минуты не слышал ни слова, и понять,
виноват ли выбранный вход или порог, было нечем.
"""
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from main import MIC_RMS_THRESHOLD, SEND_SAMPLE_RATE, _pick_input_device  # noqa: E402

SECONDS = 5.0


def измерить(index: int, name: str) -> float:
    print(f"\n  {name}")
    print(f"  ГОВОРИТЕ {SECONDS:.0f} секунд обычным голосом…", flush=True)
    rec = sd.rec(int(SECONDS * SEND_SAMPLE_RATE), samplerate=SEND_SAMPLE_RATE,
                 channels=1, dtype="int16", device=index)
    for _ in range(int(SECONDS)):
        time.sleep(1)
        print("   .", end="", flush=True)
    sd.wait()
    print()

    a = rec.astype(np.float32).ravel()
    # Громкость считаем по самой звучной секунде, а не по всей записи: паузы
    # между словами занижают среднее и делают говорящего «тихим».
    окно = SEND_SAMPLE_RATE
    куски = [a[i:i + окно] for i in range(0, max(1, len(a) - окно), окно // 2)]
    пик_rms = max(float(np.sqrt((k ** 2).mean())) for k in куски) if куски else 0.0

    вердикт = "ПРОЙДЁТ порог" if пик_rms >= MIC_RMS_THRESHOLD else "НЕ пройдёт порог"
    print(f"  громкость речи RMS={пик_rms:7.0f} | пик={int(np.abs(a).max()):6d} "
          f"| порог={MIC_RMS_THRESHOLD:.0f} → {вердикт}")
    return пик_rms


def main() -> int:
    выбран = _pick_input_device()
    устройства = [(i, d) for i, d in enumerate(sd.query_devices())
                  if d["max_input_channels"] > 0 and d["hostapi"] == 0]

    print("=" * 64)
    print("Проверка микрофона. Джарвис сейчас слушал бы вход:",
          "системный по умолчанию" if выбран is None
          else f"{выбран} — {sd.query_devices()[выбран]['name']}")
    print("=" * 64)

    результаты = []
    for i, d in устройства:
        метка = " ← выбран Джарвисом" if i == выбран else ""
        результаты.append((i, d["name"], измерить(i, f"[{i}] {d['name']}{метка}")))

    print("\n" + "=" * 64)
    рабочие = [(i, n, v) for i, n, v in результаты if v >= MIC_RMS_THRESHOLD]
    if not рабочие:
        print("НИ ОДИН вход не дал громкости выше порога.")
        print("Проверьте: не отключён ли микрофон в Windows, есть ли у него")
        print("разрешение для приложений, не убрана ли громкость записи в ноль.")
        return 1

    лучший = max(рабочие, key=lambda r: r[2])
    if лучший[0] == выбран:
        print(f"Всё в порядке: Джарвис слушает «{лучший[1]}», и он вас слышит.")
    else:
        print(f"Джарвис слушает НЕ ТОТ вход. Лучший — [{лучший[0]}] «{лучший[1]}».")
        print(f"Исправить:  set MIC_DEVICE={лучший[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
