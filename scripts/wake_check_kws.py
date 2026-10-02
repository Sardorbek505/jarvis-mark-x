"""Проверка слова «Джарвис» детектором sherpa-onnx на настоящем звуке (CI, job wake-word).

Голос Edge (мужской и женский, обычный темп, быстрее и медленнее) говорит
фразы с именем и без. Каждую прогоняем через тот же KwsWake, что работает в
Джарвисе, с настоящей русской моделью — кусками по 64 мс, как с микрофона,
громко и тихо (у владельца микрофон ASUS с ИИ-шумодавом даёт RMS ~200–600).

Запуск: python scripts/wake_check_kws.py   (модель скачает сама в models/)
Нужны: sherpa-onnx, edge-tts, ffmpeg (или imageio-ffmpeg).
"""
import importlib.util
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Без `import core…`: core/__init__.py тянет половину Джарвиса.
K = _load("wake_kws", ROOT / "core" / "wake_kws.py")
V = _load("wake_check_vosk", ROOT / "scripts" / "wake_check_vosk.py")

RATES = ["+0%", "+15%", "-15%"]
NEAR_MISS = V.WITHOUT_NAME + ["Джордж, привет", "Жар в доме не спадает", "Чарльз Дарвин", "Дарья, иди сюда"]


def _detect(model: Path, pcm: bytes, volume: float) -> bool:
    x = np.frombuffer(pcm, "<i2").astype(np.float64)
    x = x / max(1.0, float(np.sqrt(np.mean(x * x)))) * volume
    pad = np.zeros(K.RATE // 2)
    x = np.clip(np.concatenate([pad, x, pad, pad]), -32768, 32767).astype("<i2").tobytes()
    hit = threading.Event()
    w = K.KwsWake(lambda _t: hit.set(), spotter_factory=lambda: K.create_spotter(model))
    assert w.start()
    step = 1024 * 2
    for i in range(0, len(x), step):
        w.feed(x[i:i + step])
        time.sleep(0.002)
    deadline = time.monotonic() + 3
    while not hit.is_set() and time.monotonic() < deadline and not w._q.empty():
        time.sleep(0.05)
    time.sleep(0.3)
    w.stop()
    return hit.is_set()


def main():
    model = ROOT / "models" / K.MODEL_DIRNAME
    if K.find_model() is None and not all((model / f).is_file() for f in K.FILES.values()):
        K.download(model)
    hits = total = false = negatives = 0
    with tempfile.TemporaryDirectory() as tmp:
        for voice in V.VOICES:
            for rate in RATES:
                for text in V.WITH_NAME:
                    pcm = V._say(text, voice, tmp, rate)
                    for volume in (3000, 300):
                        ok = _detect(model, pcm, volume)
                        hits += ok
                        total += 1
                        if not ok:
                            print(f"  пропуск: {voice} {rate} RMS {volume}: «{text}»")
            for text in NEAR_MISS:
                pcm = V._say(text, voice, tmp)
                fired = _detect(model, pcm, 3000)
                false += fired
                negatives += 1
                if fired:
                    print(f"  ЛОЖНОЕ: {voice}: «{text}»")
    print(f"sherpa-onnx «Джарвис»: узнал {hits}/{total}, ложных {false}/{negatives}")
    if hits < 0.85 * total or false > 1:
        sys.exit(1)


if __name__ == "__main__":
    main()
