"""Стенд эхоподавления: пишет синхронно микрофон и опорный сигнал колонок.

Играет файл через колонки и сохраняет data/aec/<имя>_{mic,ref}.npy (16 кГц int16).
Нужен, чтобы сравнивать алгоритмы AEC на одной и той же записи.
"""
import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.audio_capture import AudioCaptureEngine  # noqa: E402


def load_audio(path: str, sr: int = 48000) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "quiet", "-i", path, "-f", "s16le", "-ac", "2", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).reshape(-1, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--name", default="music")
    ap.add_argument("--sec", type=float, default=20.0)
    ap.add_argument("--gain", type=float, default=0.6)
    args = ap.parse_args()

    mic, ref = [], []
    lock = threading.Lock()

    def on_frame(m: bytes, r: bytes, ts: float):
        with lock:
            mic.append(m)
            ref.append(r)

    eng = AudioCaptureEngine(on_frame=on_frame)
    eng.start()
    time.sleep(1.0)
    audio = load_audio(args.audio)[: int(48000 * args.sec)]
    sd.play((audio * args.gain).astype(np.int16), 48000, blocking=True)
    time.sleep(0.5)
    eng.stop()
    out = Path("data/aec")
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / f"{args.name}_mic.npy", np.frombuffer(b"".join(mic), np.int16))
    np.save(out / f"{args.name}_ref.npy", np.frombuffer(b"".join(ref), np.int16))
    print("кадров", len(mic), "байт в кадре", len(mic[0]) if mic else 0)


if __name__ == "__main__":
    main()
