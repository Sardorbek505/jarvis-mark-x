"""Синтез саундтрека для промо-ролика (чистый Python, без зависимостей).

Бодрый поп-бит 124 BPM: бочка, клэп, хэты, бас с сайдчейном, аккордовые стабы
и «вжухи» на ключевых сменах сцен. Результат: promo/soundtrack.wav
"""
import array
import math
import random
import wave
from pathlib import Path

SR = 44100
DUR = 23.8
BPM = 124
BEAT = 60 / BPM
N = int(SR * DUR)
OUT = Path(__file__).with_name("soundtrack.wav")

random.seed(7)
L = array.array("f", [0.0]) * N
R = array.array("f", [0.0]) * N


def add(buf_l, buf_r, start, samples, pan=0.0, gain=1.0):
    i0 = int(start * SR)
    gl, gr = gain * (1 - max(pan, 0)), gain * (1 + min(pan, 0))
    for j, v in enumerate(samples):
        k = i0 + j
        if k >= N:
            break
        if k >= 0:
            buf_l[k] += v * gl
            buf_r[k] += v * gr


def kick():
    n = int(0.32 * SR)
    ph, out = 0.0, []
    for i in range(n):
        t = i / SR
        f = 45 + 110 * math.exp(-t * 32)
        ph += 2 * math.pi * f / SR
        out.append(math.sin(ph) * math.exp(-t * 9) * 0.95)
    return out


def clap():
    n = int(0.22 * SR)
    out = []
    for i in range(n):
        t = i / SR
        env = sum(math.exp(-(t - d) * 60) for d in (0, 0.011, 0.022) if t >= d) * 0.4 + math.exp(-t * 14) * 0.25
        out.append((random.random() * 2 - 1) * env)
    # simple high-pass
    prev, hp = 0.0, []
    for v in out:
        hp.append(v - prev * 0.6)
        prev = v
    return hp


def hat(open_=False):
    n = int((0.16 if open_ else 0.05) * SR)
    dec = 22 if open_ else 90
    prev, out = 0.0, []
    for i in range(n):
        v = random.random() * 2 - 1
        out.append((v - prev) * 0.5 * math.exp(-i / SR * dec))
        prev = v
    return out


def pluck(freqs, length=0.28, bright=1.0):
    n = int(length * SR)
    out = []
    for i in range(n):
        t = i / SR
        env = math.exp(-t * 11) * min(1, t * 400)
        s = 0.0
        for f in freqs:
            for d in (-0.004, 0.004):  # detuned saw pair
                ph = (t * f * (1 + d)) % 1.0
                s += (2 * ph - 1)
        cut = math.exp(-t * 6 * bright)
        out.append(s / (len(freqs) * 2) * env * (0.35 + 0.65 * cut) * 0.5)
    # 1-pole low-pass to tame the saw
    y, lp = 0.0, []
    for v in out:
        y += (v - y) * 0.28
        lp.append(y)
    return lp


def bass(f, length):
    n = int(length * SR)
    out = []
    for i in range(n):
        t = i / SR
        env = min(1, t * 200) * math.exp(-t * 3)
        v = math.sin(2 * math.pi * f * t) + 0.35 * math.sin(4 * math.pi * f * t)
        out.append(math.tanh(v * 1.4) * env * 0.5)
    return out


def whoosh(length=0.45, up=True):
    n = int(length * SR)
    y, out = 0.0, []
    for i in range(n):
        p = i / n
        a = (p if up else 1 - p)
        env = math.sin(math.pi * p) ** 2
        y += ((random.random() * 2 - 1) - y) * (0.02 + 0.5 * a)
        out.append(y * env * 0.9)
    return out


def midi(m):
    return 440 * 2 ** ((m - 69) / 12)


# F major-ish pop loop: F - Am - Dm - Bb  (4 bars each 1 chord)
CHORDS = [[65, 69, 72], [64, 69, 72], [62, 65, 69], [62, 65, 70]]
ROOTS = [41, 45, 38, 46]

K, CL, HC, HO = kick(), clap(), hat(), hat(True)
beats = int(DUR / BEAT) + 1
DROP_OUT = (19.35, 19.8)  # short break before the end card

for b in range(beats):
    t = b * BEAT
    if t >= DUR - 0.6:
        break
    in_break = DROP_OUT[0] <= t < DROP_OUT[1]
    intro = t < 0.9
    bar = b // 4
    chord = CHORDS[bar % 4]
    if not intro and not in_break:
        add(L, R, t, K, gain=0.9)
        if b % 2 == 1:
            add(L, R, t, CL, gain=0.55)
        add(L, R, t + BEAT / 2, HC, pan=0.3, gain=0.35)
        if b % 4 == 3:
            add(L, R, t + BEAT * 0.75, HO, pan=-0.3, gain=0.25)
        # bass: offbeat eighths with sidechain feel
        add(L, R, t + BEAT / 2, bass(midi(ROOTS[bar % 4]), BEAT / 2 * 0.95), gain=0.55)
    # chord stabs on a syncopated pattern
    for off in ((0.0, 0.75, 1.5) if b % 2 == 0 else (0.5,)):
        if in_break and off > 0:
            continue
        add(L, R, t + off * BEAT, pluck([midi(m) for m in chord]), pan=(-0.2 if off else 0.2), gain=0.5 if not intro else 0.35)

# whooshes on the big cuts + a riser into the end card
for cut in (0.9, 1.9, 3.7, 5.6, 7.8, 10.2, 12.4, 14.6, 16.8):
    add(L, R, cut - 0.3, whoosh(0.4), gain=0.35)
add(L, R, DROP_OUT[0] - 0.2, whoosh(0.85), gain=0.55)
# final hit + sustained chord on the logo
add(L, R, 19.8, K, gain=1.0)
add(L, R, 19.8, pluck([midi(m) for m in (53, 65, 69, 72, 77)], length=3.2, bright=0.25), gain=0.9)

# master: fade out, soft clip, normalize
peak = 0.0
for i in range(N):
    t = i / SR
    f = min(1.0, (DUR - 0.15 - t) / 2.2) if t > DUR - 2.35 else 1.0
    f = max(f, 0.0)
    L[i] = math.tanh(L[i] * 0.9) * f
    R[i] = math.tanh(R[i] * 0.9) * f
    peak = max(peak, abs(L[i]), abs(R[i]))
g = 0.89 / peak if peak else 1
pcm = array.array("h", (int(v * g * 32767) for pair in zip(L, R) for v in pair))
with wave.open(str(OUT), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
print("wrote", OUT)
