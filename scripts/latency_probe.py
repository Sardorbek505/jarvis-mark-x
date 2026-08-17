"""Замер задержки голосового круга без человека у микрофона.

Зачем это есть. Цифры «слышит / отвечает / звучит» до сих пор снимались только
живым голосом: кто-то должен был сесть и говорить в микрофон. Такой замер
невоспроизводим — после каждой правки его нельзя прогнать заново и сравнить,
а без сравнения оптимизация превращается в гадание.

Здесь подменены только края: реплики синтезирует офлайн-голос Windows
(Microsoft Irina, ru-RU — без квот и без сети), ответ модели уходит в никуда.
Gemini при этом НАСТОЯЩИЙ, поэтому меряется ровно то, что тратит облако.

Важно про эмуляцию микрофона: в паузах между фразами кадры продолжают идти,
просто тихие. Настоящая звуковая карта именно такова — ровный поток независимо
от того, говорит человек или нет. Стенд, который в паузе не отдаёт ничего,
не вызывает микрофонный гейт с его хвостом тишины и меряет несуществующее
поведение.

Запуск:
    python scripts/latency_probe.py
    MIC_HANGOVER_MS=1600 python scripts/latency_probe.py   # подбор хвоста
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# Джарвис читает это на импорте, поэтому ставим до него.
os.environ.setdefault("JARVIS_HEADLESS", "1")
os.environ.setdefault("JARVIS_LATENCY", "1")
os.environ.setdefault("MIC_IGNORE_SPEAKERS", "0")   # своих динамиков тут нет

import main as jarvis_main                    # noqa: E402
from core.headless_ui import HeadlessUI       # noqa: E402

_VOICE_DIR = _BASE / "logs" / "latency_voice"
_VOICE = "Microsoft Irina Desktop"

# Реплики стенда. Короткие и разные по типу: болтовня, вопрос под инструмент,
# простой факт — чтобы вклад инструментов был виден отдельно от раздумий.
_PHRASES = {
    "hello": "Джарвис, как дела?",
    "weather": "Джарвис, какая сегодня погода в Шымкенте?",
    "time": "Джарвис, который сейчас час?",
}

# Пауза между репликами: дольше, чем окно молчания VAD, иначе следующая фраза
# склеится с предыдущей в один ход и замер будет не про то.
_GAP_SEC = 6.0


def _synthesize(text: str, path: Path) -> Path:
    """Синтез фразы в 16 кГц моно int16 — ровно формат микрофона Джарвиса.

    Через SAPI, а не через облачный TTS: у облачного свои квоты и своя сеть,
    и то и другое сделало бы замер плавающим.
    """
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
        "16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
        "[System.Speech.AudioFormat.AudioChannel]::Mono); "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SelectVoice('{_VOICE}'); "
        f"$s.SetOutputToWaveFile('{path}', $f); "
        f"$s.Speak('{text}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script],
                   check=True, capture_output=True)
    return path


def _load_frames(path: Path) -> list[np.ndarray]:
    """Режет WAV на кадры того же размера, какими сыплет sounddevice."""
    with wave.open(str(path)) as w:
        assert w.getframerate() == jarvis_main.SEND_SAMPLE_RATE, "не та частота"
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, "не тот формат"
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)

    size = jarvis_main.CHUNK_SIZE
    pad = (-len(raw)) % size
    raw = np.concatenate([raw, np.zeros(pad, dtype=np.int16)])
    return [raw[i:i + size].reshape(-1, 1) for i in range(0, len(raw), size)]


class _FakeMic:
    """Замена sd.InputStream: отдаёт заготовленные кадры в реальном темпе.

    Темп важен: если высыпать файл мгновенно, Gemini получит четыре секунды
    речи за миллисекунды, и замеренная задержка не будет иметь отношения к
    разговору живьём.
    """

    _FRAME_SEC = jarvis_main.CHUNK_SIZE / jarvis_main.SEND_SAMPLE_RATE

    def __init__(self, turns: list[list[np.ndarray]], **kwargs):
        self._turns = turns
        self._callback = kwargs.get("callback")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        return False

    def _emit(self, frame):
        self._callback(frame, len(frame), None, None)
        time.sleep(self._FRAME_SEC)

    def _run(self):
        silence = np.zeros((jarvis_main.CHUNK_SIZE, 1), dtype=np.int16)
        for frames in self._turns:
            if self._stop.is_set():
                return
            for frame in frames:
                if self._stop.is_set():
                    return
                self._emit(frame)
            # В паузе кадры продолжают идти — тихие. Настоящая звуковая карта
            # ведёт себя именно так: она сыплет ровным потоком независимо от
            # того, говорит человек или молчит. Если в паузе не отдавать
            # ничего, гейт с его хвостом тишины просто не вызывается, и стенд
            # меряет несуществующее поведение (на этом я и обжёгся).
            deadline = time.monotonic() + _GAP_SEC
            while time.monotonic() < deadline and not self._stop.is_set():
                self._emit(silence)


class _NullOutput:
    """Динамики стенду не нужны, а сломанное устройство роняло прошлый прогон."""

    def __init__(self):
        self.written = 0

    def write(self, chunk):
        self.written += len(chunk)

    def stop(self):
        pass

    def close(self):
        pass


async def _probe(timeout: float) -> str:
    turns = []
    for name, text in _PHRASES.items():
        turns.append(_load_frames(_synthesize(text, _VOICE_DIR / f"{name}.wav")))

    ui = HeadlessUI()
    jarvis = jarvis_main.Jarvis(ui)
    out = _NullOutput()
    jarvis._open_output = lambda: out

    jarvis_main.sd.InputStream = lambda **kw: _FakeMic(turns, **kw)
    jarvis_main._pick_input_device = lambda: None

    runner = asyncio.create_task(jarvis.run())
    deadline = asyncio.get_event_loop().time() + timeout
    try:
        while asyncio.get_event_loop().time() < deadline:
            if runner.done():
                runner.result()          # пробрасываем настоящую причину падения
                break
            if jarvis._latency._stats["answered"].count >= len(turns):
                break
            await asyncio.sleep(0.2)
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)

    return jarvis._latency.summary()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    print(f"[СТЕНД] Реплик: {len(_PHRASES)}, "
          f"хвост тишины: {jarvis_main.MIC_HANGOVER_MS} мс "
          f"({jarvis_main.MIC_HANGOVER_FRAMES} кадров), "
          f"окно VAD: {jarvis_main._VAD_SILENCE_MS} мс")
    print(asyncio.run(_probe(args.timeout)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
