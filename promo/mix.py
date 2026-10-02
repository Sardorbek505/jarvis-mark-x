"""Звук ролика и финальная сборка: голос + интро + подложка + эффекты → jarvis_reels.mp4.

Подложка приглушается под голосом (ducking), звук интро — тоже, как в самом Джарвисе
(core/intro.mix_voice, duck 0.35). Эффекты стоят ровно в кадрах событий: свист — смена
сцены, щелчок — заголовок, «готово» — появление карточки результата.
"""
from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

PROMO = Path(__file__).resolve().parent
sys.path.insert(0, str(PROMO))

from build import CARD_AT, OUTRO_MARK  # noqa: E402
from scenes import INTRO_LINES, INTRO_SEC  # noqa: E402
from timeline import BUILD, VO_AT, durations, plan, total  # noqa: E402

RATE = 44100
AUD = BUILD / "audio"


def load(path: Path) -> np.ndarray:
    """WAV → float стерео 44.1 кГц."""
    with wave.open(str(path)) as w:
        ch, sr, n = w.getnchannels(), w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64) / 32768
    x = x.reshape(-1, ch)
    if ch == 1:
        x = np.repeat(x, 2, axis=1)
    if sr != RATE:
        t_old = np.arange(len(x)) / sr
        t_new = np.arange(int(len(x) * RATE / sr)) / RATE
        x = np.stack([np.interp(t_new, t_old, x[:, c]) for c in range(2)], axis=1)
    return x


def put(buf: np.ndarray, x: np.ndarray, at: float, gain: float = 1.0):
    s = int(at * RATE)
    if s >= len(buf):
        return
    e = min(len(buf), s + len(x))
    buf[s:e] += x[:e - s] * gain


def gain_curve(n: int, spans: list[tuple[float, float]], high: float, low: float, ramp: float = 0.25) -> np.ndarray:
    """1 → low внутри spans (с плавными краями), high снаружи."""
    g = np.full(n, high)
    t = np.arange(n) / RATE
    for a, b in spans:
        k = np.clip(np.minimum((t - (a - ramp)) / ramp, ((b + ramp) - t) / ramp), 0, 1)
        g = np.minimum(g, high - (high - low) * k)
    return g


def main():
    T = total()
    n = int((T + 0.5) * RATE)
    voice = np.zeros((n, 2))
    fx = np.zeros((n, 2))
    d = durations()
    spans = []
    for i, (at, _text) in enumerate(INTRO_LINES):
        put(voice, load(BUILD / "vo" / f"intro{i}.wav"), at)
        spans.append((at, at + d[f"intro{i}"]["sec"]))
    P = plan()
    for s in P:
        a = s["start"] + VO_AT
        put(voice, load(BUILD / "vo" / f"{s['id']}.wav"), a)
        spans.append((a, a + s["vo_sec"]))

    # интро «два хлопка» — свой звук, под голосом приглушён как в программе
    intro = load(AUD / "intro_sfx.wav")
    put(fx, intro * gain_curve(len(intro), [sp for sp in spans if sp[0] < INTRO_SEC], 1.0, 0.35)[:, None], 0.0, 0.9)

    sfx = {k: load(AUD / f"{k}.wav") for k in ("whoosh", "impact", "click", "blip", "done")}
    for s in P:
        S, L, k = s["start"], s["sec"], s["kind"]
        put(fx, sfx["whoosh"], S - 0.12, 0.16)
        if "head" in s:
            put(fx, sfx["click"], S + (1.9 if k == "hook" else 0.5), 0.22)
        if k in ("orb", "multi"):
            if k == "orb":
                put(fx, sfx["done"], S + CARD_AT + 0.15, 0.30)
            else:
                for j in range(3):
                    put(fx, sfx["done"], S + j * (L + 0.3) / 3 + 0.5 + 0.8, 0.26)
        if s["id"] == "study":
            put(fx, sfx["done"], S + 3.6, 0.28)
        if k == "states":
            text, vo = s["vo"], s["vo_sec"]
            for w in ("слушаю", "думаю", "отвечаю"):
                put(fx, sfx["blip"], S + VO_AT + vo * text.find(w) / len(text), 0.18)
        if k == "title":
            put(fx, sfx["impact"], S, 0.55)
        if k == "outro":
            put(fx, sfx["impact"], S + OUTRO_MARK, 0.45)

    bed_path = AUD / "bed.wav"
    bed = load(bed_path)
    if len(bed) < n:
        subprocess.run([sys.executable, str(PROMO / "audio.py"), "--seconds", str(T + 2)], check=True)
        bed = load(bed_path)
    bed = bed[:n]
    bed_g = gain_curve(n, spans, 0.30, 0.11, 0.35)
    t = np.arange(n) / RATE
    bed_g *= np.clip((t - (INTRO_SEC - 1.5)) / 2.0, 0, 1)            # подложка входит после интро
    bed_g *= np.clip((T - t) / 2.5, 0, 1)
    mix = voice * 1.0 + fx + bed * bed_g[:, None]
    mix /= max(1.0, float(np.max(np.abs(mix))) / 0.95)
    raw = BUILD / "mix_raw.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())
    out = BUILD / "jarvis_reels.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(BUILD / "video_silent.mp4"), "-i", str(raw),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.0:LRA=9",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out)],
                   check=True)
    print("final:", out)


if __name__ == "__main__":
    main()
