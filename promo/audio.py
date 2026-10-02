"""Звук ролика: эффекты — родные функции Джарвиса, подложка — синтез здесь же.

Эффекты берутся из core/intro.py (щелчок реле, свист, удар, блип — звуки интро
«два хлопка») и core/sounds.py («готово» — две ноты после простой команды).
Подложка — тихий эмбиент-пэд в ре миноре без барабанов: под голосом не спорит.

    python promo/audio.py --seconds 180
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "promo" / "build" / "audio"
RATE = 44100


def save(name: str, x: np.ndarray, rate: int = RATE):
    x = np.asarray(x, dtype=np.float64)
    peak = float(np.max(np.abs(x))) or 1.0
    x = x / peak * 0.89
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as f:
        f.setnchannels(1 if pcm.ndim == 1 else 2)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())


def sfx():
    from core import intro as S
    from core.sounds import done_pcm
    rng = np.random.default_rng(11)
    pad = lambda x, sec=0.25: np.concatenate([x, np.zeros(int(S.RATE * sec))])  # noqa: E731
    save("whoosh", S._reverb(pad(S._whoosh(rng, 0.55, 300.0, 2600.0, 0.5)), 0.2), S.RATE)
    save("impact", S._reverb(pad(S._impact(rng, 0.7), 0.6), 0.3), S.RATE)
    save("click", pad(S._click(rng, 0.5, 0.7), 0.1), S.RATE)
    save("blip", S._reverb(pad(S._blip(1320.0, 0.12, 30.0, 0.4)), 0.25), S.RATE)
    done = np.frombuffer(done_pcm(24000, 0.5), dtype=np.int16).astype(np.float64) / 32767
    save("done", done, 24000)


def note(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def bed(seconds: float):
    """Пэд: Dm9 → B♭maj7 → Fmaj7 → C(add9), по 8 с, мягкая атака, медленный фильтр."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    out = np.zeros((n, 2))
    chords = [[50, 57, 60, 64, 65], [46, 53, 57, 60, 62], [41, 53, 57, 60, 64], [48, 55, 59, 62, 67]]
    bar = 8.0
    rng = np.random.default_rng(3)
    for i in range(int(seconds // bar) + 1):
        c = chords[i % len(chords)]
        s0, s1 = int(i * bar * RATE), min(n, int((i + 1) * bar * RATE + 2.5 * RATE))
        if s0 >= n:
            break
        tt = t[s0:s1] - i * bar
        env = np.minimum(1, tt / 2.2) * np.clip((bar + 2.5 - tt) / 2.5, 0, 1)
        for m in c:
            for det, pan in ((-0.07, 0.3), (0.07, 0.7)):
                f = note(m + det)
                ph = rng.uniform(0, 2 * np.pi)
                v = (np.sin(2 * np.pi * f * tt + ph) + 0.35 * np.sin(4 * np.pi * f * tt + ph)
                     + 0.12 * np.sin(6 * np.pi * f * tt + ph)) * env * (0.5 if m < 48 else 0.32)
                out[s0:s1, 0] += v * (1 - pan)
                out[s0:s1, 1] += v * pan
        # редкие «искры» — высокие ноты аккорда, как индикаторы HUD
        for k in range(3):
            at = s0 + int(rng.uniform(1.0, bar - 1.0) * RATE)
            m = c[rng.integers(2, len(c))] + 24
            ln = int(1.6 * RATE)
            if at + ln < n:
                tk = np.arange(ln) / RATE
                v = np.sin(2 * np.pi * note(m) * tk) * np.exp(-tk * 3.2) * 0.18
                out[at:at + ln, k % 2] += v
                out[at:at + ln, 1 - k % 2] += v * 0.5
    # пульс суб-баса раз в такт: держит темп, не мешает голосу
    for i in range(int(seconds // (bar / 2))):
        s0 = int(i * bar / 2 * RATE)
        ln = int(1.8 * RATE)
        if s0 + ln >= n:
            break
        tk = np.arange(ln) / RATE
        root = chords[int(i // 2) % len(chords)][0] - 12
        v = np.sin(2 * np.pi * note(root) * tk) * np.exp(-tk * 2.0) * np.minimum(1, tk / 0.02) * 0.45
        out[s0:s0 + ln] += v[:, None]
    # мягкий низкочастотный фильтр (скользящее среднее) и плавные края
    k = 6
    out = np.stack([np.convolve(out[:, ch], np.ones(k) / k, mode="same") for ch in range(2)], axis=1)
    fade = np.minimum(1, np.minimum(t / 3.0, (seconds - t) / 4.0))[:, None]
    save("bed", out * fade)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=180.0)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    sfx()
    bed(a.seconds)
    print("audio ->", OUT)


if __name__ == "__main__":
    main()
