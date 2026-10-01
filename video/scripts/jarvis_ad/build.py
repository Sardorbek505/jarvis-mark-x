"""Build the audio, timeline and image assets for the JarvisAd composition.

Pipeline (deterministic — same inputs give the same files):
  1. Voice-over: each script line is synthesized offline with Kokoro-82M
     (Apache-2.0, voice `bm_george`), then given a light "AI assistant"
     treatment (EQ, compression, short plate reverb, stereo doubling).
  2. Timeline: every scene lasts lead-in + line + tail, rounded UP to whole
     beats of a 100 BPM grid (1 beat = 18 frames at 30 fps), so every cut
     lands on the beat.
  3. Score: an original D-minor cue synthesized here with numpy — pad, pulse
     bass, kick/hat from the "voice" scene on, riser into the end card.
  4. SFX: Mixkit (Sound Effects Free License) files, fetched from the
     video-shotcraft repo at a pinned commit.
  5. Mix: music ducked under the voice, peak-limited, written as
     public/jarvis-ad/mix.wav and encoded to mix.mp3 with Remotion's ffmpeg.
  6. Fonts into public/jarvis-ad/fonts/. Product footage comes from
     scripts/capture/ (real PC app + Telegram Mini App), not from here.

Run from video/:  python scripts/jarvis_ad/build.py
Requirements:     pip install -r scripts/jarvis_ad/requirements.txt
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

VIDEO = Path(__file__).resolve().parents[2]
REPO = VIDEO.parent
CACHE = VIDEO / ".cache" / "jarvis-ad"
PUBLIC = VIDEO / "public" / "jarvis-ad"
TIMELINE = VIDEO / "src" / "JarvisAd" / "timeline.json"

SR = 48000
FPS = 30
BPM = 100
BEAT_S = 60 / BPM  # 0.6 s
BEAT_F = round(BEAT_S * FPS)  # 18 frames
assert abs(BEAT_S * FPS - BEAT_F) < 1e-9

KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"]
VOICE = "bm_george"

SFX_BASE = ("https://raw.githubusercontent.com/louiseliu/hyperFrames-video-shotcraft/"
            "1df77f1ab080323558f70a5fb880fad0f88f87fc/assets/audio/sfx/")

# id, line spoken, lead-in before the line (s), tail after it (s)
SCENES = [
    ("boot", "Good evening. Allow me to introduce myself.", 2.4, 0.5),
    ("title", "I am Jarvis. Mark Ten.", 0.45, 0.9),
    ("voice", "Speak naturally, and I answer instantly. You can even interrupt me.", 0.35, 0.5),
    ("vision", "I see your screen, read your code, and find the bug before you do.", 0.35, 0.5),
    ("memory", "I remember what matters. Your notes, your plans, your preferences.", 0.35, 0.5),
    ("control", "Open an app. Turn it down. Send it to your phone. Consider it done.", 0.35, 0.7),
    ("end", "Jarvis, Mark Ten. Your personal A.I. for Windows. Free, and ready when you are.", 0.9, 2.2),
]


# ── helpers ──────────────────────────────────────────────────────────────────

def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"  downloading {url}")
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.rename(dest)
    return dest


def load(path: Path) -> np.ndarray:
    """Any audio file -> float32 stereo at SR."""
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    if sr != SR:
        g = math.gcd(sr, SR)
        data = signal.resample_poly(data, SR // g, sr // g, axis=0).astype(np.float32)
    return data


def place(bus: np.ndarray, clip: np.ndarray, at_s: float, gain_db: float = 0.0) -> None:
    start = int(round(at_s * SR))
    if start < 0:
        clip, start = clip[-start:], 0
    end = min(len(bus), start + len(clip))
    if end > start:
        bus[start:end] += clip[: end - start] * (10 ** (gain_db / 20))


def peak_time(clip: np.ndarray) -> float:
    """Where a clip's energy peaks — used to land an SFX hit on a frame."""
    env = np.abs(clip).max(axis=1)
    win = int(0.01 * SR)
    env = np.convolve(env, np.ones(win) / win, mode="same")
    return float(np.argmax(env)) / SR


def butter(kind: str, freq, order: int = 2):
    return signal.butter(order, freq, btype=kind, fs=SR, output="sos")


def filt(x: np.ndarray, sos) -> np.ndarray:
    return signal.sosfilt(sos, x, axis=0).astype(np.float32)


def plate_ir(seconds: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    decay = np.exp(-6.9 * t / seconds)  # -60 dB at `seconds`
    ir = rng.standard_normal((n, 2)) * decay[:, None]
    ir = filt(ir, butter("lowpass", 6500))
    return (ir / np.sqrt((ir ** 2).sum(axis=0))).astype(np.float32)


def reverb(x: np.ndarray, seconds: float, wet: float, seed: int = 7) -> np.ndarray:
    ir = plate_ir(seconds, seed)
    tail = np.stack([signal.fftconvolve(x[:, c], ir[:, c]) for c in range(2)], axis=1)
    out = np.zeros_like(tail)
    out[: len(x)] = x
    return (out + wet * tail).astype(np.float32)


def compress(x: np.ndarray, threshold_db: float, ratio: float, attack=0.005, release=0.12) -> np.ndarray:
    level = np.abs(x).max(axis=1)
    a, r = np.exp(-1 / (attack * SR)), np.exp(-1 / (release * SR))
    env = np.empty_like(level)
    e = 0.0
    for i, v in enumerate(level):  # one-pole follower (short clips only)
        e = a * e + (1 - a) * v if v > e else r * e + (1 - r) * v
        env[i] = e
    db = 20 * np.log10(np.maximum(env, 1e-6))
    gain_db = np.where(db > threshold_db, (threshold_db - db) * (1 - 1 / ratio), 0.0)
    return (x * (10 ** (gain_db / 20))[:, None]).astype(np.float32)


# ── 1. voice-over ────────────────────────────────────────────────────────────

def synth_voice() -> tuple[list[np.ndarray], list[float]]:
    from kokoro_onnx import Kokoro

    model = [fetch(KOKORO_URL + f, CACHE / "kokoro" / f) for f in KOKORO_FILES]
    tts = Kokoro(str(model[0]), str(model[1]))
    lines, spoken = [], []
    for sid, text, *_ in SCENES:
        samples, sr = tts.create(text, voice=VOICE, speed=0.94, lang="en-gb")
        mono = np.asarray(samples, dtype=np.float32)
        g = math.gcd(sr, SR)
        mono = signal.resample_poly(mono, SR // g, sr // g).astype(np.float32)
        # trim Kokoro's leading/trailing silence so timing is exact
        idx = np.flatnonzero(np.abs(mono) > 0.01)
        mono = mono[max(0, idx[0] - 480): idx[-1] + 2400]
        lines.append(treat_voice(mono))
        spoken.append(len(mono) / SR)  # dry length; the reverb tail may ring into the next scene
        print(f"  VO {sid:8s} {len(mono) / SR:5.2f}s  {text}")
    return lines, spoken


def treat_voice(mono: np.ndarray) -> np.ndarray:
    x = filt(mono, butter("highpass", 90))
    # gentle presence lift: add a band-passed copy around 3 kHz
    x = x + 0.25 * filt(x, butter("bandpass", [2200, 4800]))
    x = compress(x[:, None].repeat(2, axis=1), threshold_db=-20, ratio=3)
    # "assistant" doubling: two quiet, slightly delayed and filtered copies, panned
    d1, d2 = int(0.011 * SR), int(0.017 * SR)
    ghost = filt(x[:, 0], butter("bandpass", [400, 5000]))
    x[d1:, 0] += 0.14 * ghost[:-d1]
    x[d2:, 1] += 0.14 * ghost[:-d2]
    x = reverb(x, 1.1, wet=0.16, seed=3)
    return x / np.abs(x).max() * 0.89


# ── 2. timeline ──────────────────────────────────────────────────────────────

def build_timeline(spoken: list[float]) -> dict:
    scenes, cursor = [], 0
    for (sid, text, lead, tail), seconds in zip(SCENES, spoken):
        vo_frames = math.ceil(seconds * FPS)
        need = round(lead * FPS) + vo_frames + round(tail * FPS)
        beats = math.ceil(need / BEAT_F)
        dur = beats * BEAT_F
        scenes.append({
            "id": sid,
            "from": cursor,
            "durationInFrames": dur,
            "beats": beats,
            "vo": {"text": text, "from": round(lead * FPS), "durationInFrames": vo_frames},
        })
        cursor += dur
    return {"fps": FPS, "bpm": BPM, "beatFrames": BEAT_F, "durationInFrames": cursor, "scenes": scenes}


# ── 3. score ─────────────────────────────────────────────────────────────────

def midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def saw(freq: float, n: int, phase: float = 0.0) -> np.ndarray:
    t = np.arange(n) / SR
    return (2 * ((freq * t + phase) % 1.0) - 1).astype(np.float32)


def adsr(n: int, a: float, r: float) -> np.ndarray:
    env = np.ones(n, dtype=np.float32)
    na, nr = min(n, int(a * SR)), min(n, int(r * SR))
    env[:na] = np.linspace(0, 1, na)
    if nr:
        env[-nr:] *= np.linspace(1, 0, nr)
    return env


def compose_score(tl: dict) -> tuple[np.ndarray, list[float]]:
    total_s = tl["durationInFrames"] / FPS + 4.0
    n = int(total_s * SR)
    music = np.zeros((n, 2), dtype=np.float32)
    rng = np.random.default_rng(11)
    sc = {s["id"]: s for s in tl["scenes"]}
    t_of = lambda f: f / FPS  # noqa: E731
    groove_start = t_of(sc["voice"]["from"])
    end_start = t_of(sc["end"]["from"])
    bar = 4 * BEAT_S

    # Dm – Bb – F – C, one chord per bar, voiced low
    chords = [[50, 53, 57, 62], [46, 50, 53, 58], [41, 48, 53, 57], [48, 52, 55, 60]]

    # pad: detuned saws through a slow low-pass, whole piece
    pad = np.zeros(n, dtype=np.float32)
    bars = int(math.ceil(total_s / bar))
    for b in range(bars):
        s0 = int(b * bar * SR)
        seg = min(n - s0, int((bar + 0.4) * SR))
        if seg <= 0:
            break
        chord = chords[b % 4] if b * bar >= groove_start - 1e-6 else chords[0]
        tone = sum(saw(midi(p) * d, seg, rng.random()) for p in chord for d in (0.997, 1.0, 1.004))
        pad[s0:s0 + seg] += tone * adsr(seg, 0.35, 0.4) / 12
    cutoff = np.interp(np.arange(n) / SR, [0, groove_start, end_start - 2, end_start, total_s],
                       [500, 1400, 2200, 900, 700])
    # time-varying low-pass: filter in blocks with the block's cutoff
    out = np.zeros(n, dtype=np.float32)
    block = SR // 10
    zi = None
    for s0 in range(0, n, block):
        sos = butter("lowpass", float(cutoff[s0]), order=2)
        if zi is None or zi.shape[0] != sos.shape[0]:
            zi = signal.sosfilt_zi(sos) * 0
        out[s0:s0 + block], zi = signal.sosfilt(sos, pad[s0:s0 + block], zi=zi)
    pad = out
    music += np.stack([pad, np.roll(pad, int(0.013 * SR))], axis=1) * 0.55

    # sub drone on D, first two scenes (the "boot" bed)
    tt = np.arange(n) / SR
    drone = np.sin(2 * np.pi * midi(38) * tt) * np.interp(tt, [0, 1.5, groove_start, groove_start + 1],
                                                          [0, 0.22, 0.22, 0.0])
    music += drone[:, None].astype(np.float32)

    # groove: pulse bass (8ths), kick on beats, hat on off-beats — voice..control
    kicks = []
    bass_sos = butter("lowpass", 700)
    hat_noise = filt(rng.standard_normal(int(0.05 * SR)).astype(np.float32), butter("highpass", 7000))
    t = groove_start
    while t < end_start - 1e-6:
        b = int(round((t - groove_start) / BEAT_S))
        chord = chords[(int((t + 1e-6) // bar)) % 4]
        # kick
        kn = int(0.4 * SR)
        kt = np.arange(kn) / SR
        f = 45 + 75 * np.exp(-kt * 28)
        kick = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-kt * 9)
        place(music, (kick[:, None] * [0.55, 0.55]).astype(np.float32), t)
        kicks.append(t)
        # hat on the off-beat
        place(music, (hat_noise * np.exp(-np.arange(len(hat_noise)) / SR * 90))[:, None] * [0.05, 0.07],
              t + BEAT_S / 2)
        # bass 8ths
        for k in range(2):
            bn = int(BEAT_S / 2 * SR)
            note = saw(midi(chord[0] - 12), bn) * np.exp(-np.arange(bn) / SR * 7)
            note = filt(note, bass_sos)
            place(music, (note[:, None] * [0.22, 0.22]).astype(np.float32), t + k * BEAT_S / 2)
        # arpeggio pluck on 16ths, every other bar, quietly
        if (b // 4) % 2 == 1:
            for k in range(4):
                pn = int(0.18 * SR)
                p = chord[(k + b) % 4] + 12
                pl = (np.sin(2 * np.pi * midi(p) * np.arange(pn) / SR) * np.exp(-np.arange(pn) / SR * 18))
                place(music, (pl[:, None] * [0.05, 0.04]).astype(np.float32), t + k * BEAT_S / 4)
        t += BEAT_S

    # riser: noise sweep over the last two beats before the end card
    rn = int(2 * BEAT_S * SR)
    rise = rng.standard_normal(rn).astype(np.float32)
    rise = filt(rise, butter("bandpass", [800, 9000])) * np.linspace(0, 1, rn) ** 2.2
    place(music, (rise[:, None] * [0.22, 0.22]).astype(np.float32), end_start - 2 * BEAT_S)

    # low boom on the end-card ignition
    bn = int(3 * SR)
    bt = np.arange(bn) / SR
    boom = np.sin(2 * np.pi * (38 + 30 * np.exp(-bt * 6)) * bt) * np.exp(-bt * 1.6)
    place(music, (boom[:, None] * [0.6, 0.6]).astype(np.float32), end_start + ignite_offset_s())

    music = reverb(music, 2.4, wet=0.22, seed=5)[:n]
    return music, kicks


def ignite_offset_s() -> float:
    """End-card core ignites one beat after the cut (mirrored in EndScene.tsx)."""
    return BEAT_S


# ── 4. sfx ───────────────────────────────────────────────────────────────────

def sfx(name: str) -> np.ndarray:
    return load(fetch(SFX_BASE + name, CACHE / "sfx" / name))


def lay_sfx(bus: np.ndarray, tl: dict) -> list[dict]:
    sc = {s["id"]: s for s in tl["scenes"]}
    cues = []

    def at(name: str, t: float, gain: float, hit: bool = False, trim: float | None = None):
        clip = sfx(name)
        if trim:
            n = int(trim * SR)
            clip = clip[:n] * adsr(min(n, len(clip)), 0.0, 0.3)[:, None]
        offset = peak_time(clip) if hit else 0.0
        place(bus, clip, t - offset, gain)
        cues.append({"sfx": name, "frame": round(t * FPS)})

    s = lambda sid: sc[sid]["from"] / FPS  # noqa: E731
    at("scifi/tech-hum-futuristic.mp3", 0.0, -14, trim=s("title"))
    at("data/power-up-electronic.mp3", 0.2, -9)
    at("impact/bass-hit-futuristic.mp3", s("title") + 0.2, -6, hit=True)
    for sid in ("voice", "vision", "memory", "control"):
        at("data/whoosh-electric.mp3", s(sid), -12, hit=True)
    at("data/data-scan.mp3", s("vision") + 1.0, -12)
    at("light/shimmer-sparkle-sweep.mp3", s("memory") + 0.3, -15)
    ctrl = sc["control"]
    step = ctrl["durationInFrames"] / 4 / FPS
    for k in range(3):  # one tick per command chip (ControlScene.tsx uses the same spacing)
        at("scifi/hitech-bleep.mp3", s("control") + (k + 1) * step, -13)
    at("mech/lock-digital.mp3", s("end") - 0.35, -12)
    at("impact/impact-cine-big.mp3", s("end") + ignite_offset_s(), -7, hit=True)
    at("light/light-aura.mp3", s("end") + ignite_offset_s() + 0.2, -16)
    return cues


# ── 5. mix ───────────────────────────────────────────────────────────────────

def mix(tl: dict, vo: list[np.ndarray]) -> np.ndarray:
    music, _ = compose_score(tl)
    n = len(music)
    voice = np.zeros((n, 2), dtype=np.float32)
    for scene, clip in zip(tl["scenes"], vo):
        place(voice, clip, (scene["from"] + scene["vo"]["from"]) / FPS)

    # duck the music under the voice (-11 dB, 60 ms attack, 350 ms release)
    active = np.convolve((np.abs(voice).max(axis=1) > 0.02).astype(np.float32),
                         np.ones(int(0.25 * SR)), mode="same") > 0
    target = np.where(active, 10 ** (-11 / 20), 1.0).astype(np.float32)
    duck = np.empty_like(target)
    g = 1.0
    down, up = np.exp(-1 / (0.06 * SR)), np.exp(-1 / (0.35 * SR))
    for i, v in enumerate(target):
        c = down if v < g else up
        g = c * g + (1 - c) * v
        duck[i] = g

    fx = np.zeros_like(music)
    cues = lay_sfx(fx, tl)
    tl["sfxCues"] = cues

    bed = music * duck[:, None] * 0.8 + fx * 0.9
    for name, stem in (("voice", voice), ("bed", bed)):  # stems, for checking the balance
        sf.write(CACHE / f"stem-{name}.wav", stem, SR, subtype="PCM_24")
    out = bed + voice
    # fade out after the last frame, then trim
    end = int((tl["durationInFrames"] / FPS) * SR)
    out = out[:end]
    out[-int(0.8 * SR):] *= np.linspace(1, 0, int(0.8 * SR))[:, None] ** 1.5
    # soft limiter, then normalize to -1 dBFS
    out = np.tanh(out * 1.1) / np.tanh(1.1)
    out = out / np.abs(out).max() * 10 ** (-1 / 20)

    # per-frame voice level for audio-reactive visuals
    spf = SR // FPS
    frames = tl["durationInFrames"]
    rms = np.sqrt((voice[: frames * spf, 0].reshape(frames, spf) ** 2).mean(axis=1))
    tl["voiceLevel"] = [round(float(v), 3) for v in np.clip(rms / (rms.max() or 1), 0, 1)]
    return out.astype(np.float32)


# ── 6. fonts ──────────────────────────────────────────────────────────────────

def copy_fonts() -> None:
    dst = PUBLIC / "fonts"
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("Tektur-Regular.ttf", "Tektur-Medium.ttf", "Tektur-OFL.txt"):
        shutil.copy(REPO / "design" / "fonts" / f, dst / f)
    mono = VIDEO / "node_modules" / "@fontsource" / "jetbrains-mono" / "files"
    shutil.copy(mono / "jetbrains-mono-latin-500-normal.woff2", dst / "JetBrainsMono-500.woff2")
    shutil.copy(mono.parent / "LICENSE", dst / "JetBrainsMono-OFL.txt")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    print("voice-over")
    vo, spoken = synth_voice()
    tl = build_timeline(spoken)
    print(f"timeline {tl['durationInFrames']} frames ({tl['durationInFrames'] / FPS:.1f}s)")
    print("score + mix")
    out = mix(tl, vo)
    wav = CACHE / "mix.wav"
    sf.write(wav, out, SR, subtype="PCM_24")
    subprocess.run(["npx", "remotion", "ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                    "-c:a", "libmp3lame", "-b:a", "256k", str(PUBLIC / "mix.mp3")], cwd=VIDEO, check=True)
    TIMELINE.parent.mkdir(parents=True, exist_ok=True)
    TIMELINE.write_text(json.dumps(tl, indent=1) + "\n")
    print("fonts")
    copy_fonts()
    print("done")


if __name__ == "__main__":
    main()
