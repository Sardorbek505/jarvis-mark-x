"""Слово «Джарвис» офлайн — sherpa-onnx keyword spotter, без ключей и обучения.

Почему не Vosk: в маленькой русской модели Vosk слова «джарвис» нет, она
слышит «из» или «джордж» — детектор то молчал, то срабатывал от чужих слов.
Porcupine хорош, но нужен ключ и файл слова из консоли Picovoice.

Здесь — русская потоковая модель (Zipformer small от Vosk, 28 МБ): слово
задаётся текстом по слогам модели, а не обучением. Первая версия стояла на
английской модели KWS (GigaSpeech) — та слышала русское «Джарвис» как JARVIS
через раз, а у владельца (микрофон ASUS с ИИ-шумодавом) не узнала ни разу.
Русская на той же проверке: 15 из 15 «Джарвис» (громко, тихо, в сильном шуме),
0 ложных из 12 похожих фраз («Джордж», «Чарльз Дарвин», «жар в доме») и 0 за
35 с обычной речи без имени.

Модель скачивается один раз в <папка данных>/models/kws-jarvis-ru; в
установщик она кладётся при сборке.

Интерфейс — как у core/wake_vosk.LocalWake: start() → bool, feed(pcm 16 кГц
int16), stop(); при срабатывании зовёт on_wake(текст).
"""
from __future__ import annotations

import io
import logging
import os
import queue
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

MODEL_DIRNAME = "kws-jarvis-ru"
ARCHIVE_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
               "sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16.tar.bz2")
FILES = {
    "encoder": "encoder.int8.onnx",
    "decoder": "decoder.onnx",
    "joiner": "joiner.int8.onnx",
    "tokens": "tokens.txt",
}
# Слоги BPE модели (sentencepiece bpe.model) — заранее, чтобы на ПК не нужен был sentencepiece.
KEYWORDS = (
    "▁д жа р ви с @ДЖАРВИС",
    "▁д же р ви с @ДЖЕРВИС",
    "▁д жа р ве с @ДЖАРВЕС",
    "▁д жа р ви з @ДЖАРВИЗ",
)
THRESHOLD = float(os.getenv("JARVIS_KWS_THRESHOLD", "0.25"))  # ниже — чувствительнее
SCORE = float(os.getenv("JARVIS_KWS_SCORE", "1.0"))           # выше — слову легче победить
COOLDOWN_SEC = 1.5
RATE = 16000


def available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("sherpa_onnx") is not None
    except Exception:
        return False


def _candidates() -> list[Path]:
    out = []
    if getattr(sys, "frozen", False):
        out += [Path(getattr(sys, "_MEIPASS", "")) / "models" / MODEL_DIRNAME,
                Path(sys.executable).parent / "models" / MODEL_DIRNAME]
    try:
        from core.paths import get_data_root
        out.append(Path(get_data_root()) / "models" / MODEL_DIRNAME)
    except Exception:
        pass
    out.append(Path(__file__).resolve().parent.parent / "models" / MODEL_DIRNAME)
    return out


def find_model() -> Path | None:
    for d in _candidates():
        if all((d / f).is_file() for f in FILES.values()):
            return d
    return None


def download(dest: Path | None = None, fetch=None) -> Path:
    """Скачать архив модели и оставить только нужные 4 файла (28 МБ)."""
    if dest is None:
        from core.paths import get_data_root
        dest = Path(get_data_root()) / "models" / MODEL_DIRNAME
    if fetch is None:
        import urllib.request

        def fetch(url):
            with urllib.request.urlopen(url, timeout=180) as r:
                return r.read()
    data = fetch(ARCHIVE_URL)
    dest.mkdir(parents=True, exist_ok=True)
    want = set(FILES.values())
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tar:
        for m in tar.getmembers():
            name = Path(m.name).name
            if m.isfile() and name in want:
                f = tar.extractfile(m)
                if f is not None:
                    part = dest / (name + ".part")
                    part.write_bytes(f.read())
                    part.replace(dest / name)
    missing = [n for n in want if not (dest / n).is_file()]
    if missing:
        raise RuntimeError(f"в архиве нет {', '.join(missing)}")
    logger.info("Модель слова «Джарвис» (sherpa-onnx) скачана: %s", dest)
    return dest


def create_spotter(model: Path):
    import sherpa_onnx
    kw = model / "keywords-jarvis.txt"
    kw.write_text("\n".join(KEYWORDS) + "\n", encoding="utf-8")
    return sherpa_onnx.KeywordSpotter(
        tokens=str(model / FILES["tokens"]), encoder=str(model / FILES["encoder"]),
        decoder=str(model / FILES["decoder"]), joiner=str(model / FILES["joiner"]),
        num_threads=1, keywords_file=str(kw), keywords_score=SCORE, keywords_threshold=THRESHOLD,
        max_active_paths=4, provider="cpu")


class KwsWake:
    def __init__(self, on_wake: Callable[[str], None], spotter_factory: Callable | None = None):
        self._on_wake = on_wake
        self._factory = spotter_factory            # для тестов
        self._q: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._cooldown_until = 0.0
        self._kws = None
        self.ready = False
        self.last_heard = ""

    def start(self) -> bool:
        try:
            if self._factory is not None:
                self._kws = self._factory()
            else:
                model = find_model()
                if model is None or not available():
                    return False
                self._kws = create_spotter(model)
        except Exception as exc:
            logger.warning("Детектор слова (sherpa-onnx) не запустился: %s", exc)
            return False
        # ready — до старта потока: упади он сразу, его ready=False не затрётся.
        self.ready = True
        threading.Thread(target=self._run, daemon=True, name="wake-kws").start()
        logger.info("Слово «Джарвис» слушает sherpa-onnx (порог %.2f, вес %.1f)", THRESHOLD, SCORE)
        return True

    def stop(self):
        self._stop.set()
        self.ready = False

    def feed(self, pcm: bytes):
        if not self.ready:
            return
        try:
            self._q.put_nowait(pcm)
        except queue.Full:
            pass                                    # отстаём — старый звук не ждём

    def _run(self):
        # Поток детектора не должен умирать молча: раньше ошибка тут уходила в
        # stderr, которого у оконного exe нет, и «Джарвис» просто переставал работать.
        try:
            self._loop()
        except Exception:
            logger.exception("Детектор слова (sherpa-onnx) упал")
            self.ready = False

    def _loop(self):
        stream = self._kws.create_stream()
        started, peak, reported = time.monotonic(), 0.0, False
        while not self._stop.is_set():
            try:
                pcm = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if not reported:
                # Один раз в журнал: доходит ли звук и какой громкости. «Не слышит
                # имя» при тишине здесь — это микрофон, а не детектор.
                peak = max(peak, float(np.sqrt(np.mean(x * x))) * 32768 if len(x) else 0.0)
                if time.monotonic() - started > 20:
                    logger.info("Детектор слова: звук идёт, громкость до RMS %.0f", peak)
                    reported = True
            stream.accept_waveform(RATE, x)
            while self._kws.is_ready(stream):
                self._kws.decode_stream(stream)
                hit = self._kws.get_result(stream)
                if not hit:
                    continue
                self._kws.reset_stream(stream)
                now = time.monotonic()
                if now < self._cooldown_until:
                    continue
                self._cooldown_until = now + COOLDOWN_SEC
                self.last_heard = str(hit)
                logger.info("Услышал «Джарвис» (sherpa-onnx: %s)", hit)
                try:
                    self._on_wake("джарвис")
                except Exception as exc:
                    logger.warning("Обработчик слова: %s", exc)
