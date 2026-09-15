"""Метрики ключевого слова на русском: ложные срабатывания и отклик.

Запуск:  python scripts/wake_metrics.py [секунд_шума]

Зачем. До сих пор о качестве KWS не было ни одной цифры: комментарии обещали
«точность 99%+ без ложных срабатываний», а единственный бенчмарк гонял
openWakeWord на синтетическом АНГЛИЙСКОМ голосе. Для русского («Джарвис»
ловит грамматика Vosk) не измерялось ничего.

Скрипт делает две вещи:

  1. Ложные срабатывания (FAR). Пишет фон вашей комнаты и прогоняет его через
     тот же детектор, что стоит в бою. Любое срабатывание здесь — ложное:
     ключевого слова в записи нет по построению.

  2. Отклик. Синтезирует «Джарвис» через edge-tts (сеть, но без ключей),
     прогоняет покадрово и замеряет, через сколько миллисекунд от начала слова
     детектор его подтвердил.

Дополнительно показывает, сколько ложных срабатываний отсекает вето по речи
(нейросетевой VAD): грамматика из ~25 слов декодирует бытовой шум в ближайшее
слово, и без вето это уходит в реальное пробуждение.
"""

import asyncio
import sys
import time
from pathlib import Path

import numpy as np

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

SAMPLE_RATE = 16000
FRAME = 1024                       # 64 мс — рабочий кадр конвейера
FRAME_MS = FRAME / SAMPLE_RATE * 1000


def записать_фон(секунд: float) -> np.ndarray:
    """Пишет фон комнаты с того же входа, что слушает Джарвис."""
    import sounddevice as sd
    from main import _pick_input_device

    device = _pick_input_device()
    print(f"Пишу фон комнаты {секунд:.0f} с — молчите, ключевое слово не произносите…")
    buf = sd.rec(int(секунд * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                 channels=1, dtype="int16", device=device)
    sd.wait()
    return buf.reshape(-1)


async def синтезировать_слово(текст: str) -> np.ndarray | None:
    """Синтезирует фразу русским голосом Edge-TTS и приводит к 16 кГц mono."""
    try:
        from telegram_bot import tts_edge
        pcm = await tts_edge.speak_pcm(текст, sample_rate=SAMPLE_RATE)
    except Exception as e:
        print(f"  синтез недоступен: {e}")
        return None
    if not pcm:
        return None
    return np.frombuffer(pcm, dtype=np.int16)


def прогнать(сигнал: np.ndarray, вето: bool):
    """Гоняет сигнал через детектор.

    Возвращает (срабатываний, кадр_срабатывания, миллисекунд_расчёта).
    Загрузка моделей в замер НЕ входит: она разовая и к нагрузке в бою
    отношения не имеет — иначе получаются бессмысленные «50% ядра».
    """
    from core.wake_detector import WakeWordDetector2Stage
    from core.endpointing import SileroEndpointer

    попадания = []
    детектор = WakeWordDetector2Stage(
        on_wake=lambda score: попадания.append(score),
        enable_spotterless=False,
    )
    ep = SileroEndpointer() if вето else None
    окно = []
    первый = -1

    расчёт = time.perf_counter()
    for i in range(0, len(сигнал) - FRAME + 1, FRAME):
        кадр = сигнал[i:i + FRAME].tobytes()
        было = len(попадания)
        if ep is not None:
            окно.append(ep.speech_probability(кадр))
            окно[:] = окно[-8:]
        детектор.process_pcm(кадр)
        if len(попадания) > было:
            if ep is not None and (not окно or max(окно) < 0.35):
                попадания.pop()          # вето: речи в окне не было
                continue
            if первый < 0:
                первый = i + FRAME
    return len(попадания), первый, (time.perf_counter() - расчёт) * 1000


def main():
    секунд = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0

    фон = записать_фон(секунд)
    rms = np.sqrt(np.mean(фон.astype(np.float32) ** 2))
    минут = len(фон) / SAMPLE_RATE / 60
    print(f"  записано {len(фон)/SAMPLE_RATE:.0f} с, RMS фона {rms:.0f}\n")

    if rms > 400:
        print("  ВНИМАНИЕ: это не тишина. В комнате играет звук или идёт речь,")
        print("  поэтому срабатывания ниже нельзя считать ложными по построению.")
        print("  Для честной цифры FAR закройте плееры и повторите в тишине.\n")

    без_вето, _, ms_без = прогнать(фон, вето=False)
    с_вето, _, ms_с = прогнать(фон, вето=True)

    print("ЛОЖНЫЕ СРАБАТЫВАНИЯ на фоне комнаты")
    print(f"  без вето по речи: {без_вето}  ({без_вето/минут:.1f} в минуту)")
    print(f"  с вето по речи:   {с_вето}  ({с_вето/минут:.1f} в минуту)")
    if без_вето:
        print(f"  отсечено вето: {без_вето - с_вето} из {без_вето}")
    сек = len(фон) / SAMPLE_RATE
    print(f"  нагрузка детектора: {ms_без/10/сек:.1f}% реального времени "
          f"(с вето и Silero — {ms_с/10/сек:.1f}%)")
    print()

    print("ОТКЛИК на слово «Джарвис»")
    слово = asyncio.run(синтезировать_слово("Джарвис"))
    if слово is None:
        print("  синтез недоступен — отклик не замерен")
        return
    тишина = np.zeros(SAMPLE_RATE // 2, dtype=np.int16)
    сигнал = np.concatenate([тишина, слово, тишина])
    начало = len(тишина)

    попаданий, кадр, _ = прогнать(сигнал, вето=True)

    if попаданий == 0:
        print("  НЕ РАСПОЗНАНО — это пропуск (FRR), а не задержка")
    else:
        задержка = (кадр - начало) / SAMPLE_RATE * 1000
        длина = len(слово) / SAMPLE_RATE * 1000
        print(f"  распознано за {задержка:.0f} мс от начала слова "
              f"(само слово звучит {длина:.0f} мс, квант кадра {FRAME_MS:.0f} мс)")
        print(f"  запас после конца слова: {задержка - длина:+.0f} мс")


if __name__ == "__main__":
    main()
