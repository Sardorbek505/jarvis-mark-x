"""Build the audio and timeline for the JarvisAd feature tour.

Everything follows scripts/jarvis_ad/storyboard.json — one section per
feature of the app, each a beat-locked slot (120 BPM, 1 beat = 15 frames):

  1. Timeline: sections with marks inside each feature — the user's phrase,
     Jarvis "thinking" (HUD shape), the result card, Jarvis replying.
  2. Voice-over: Jarvis's replies from vo/ru/<section>.mp3 (fish_vo.py — the
     app's own Fish Audio voice). Missing files are fine: the section stays
     silent and the HUD breathes to a placeholder envelope. A reply longer
     than its slot stretches the section by whole beats.
  3. Score: an original D-minor cue synthesized with numpy — pad and drone
     under the intro, kick/clap/bass groove through the features, riser into
     the end card.
  4. SFX by role (whoosh, type, think, card, slam…): the owner's packs in
     sfx/<role>/ (not in git — licensed packs), rotated per feature, with
     signature sounds in sfx/card-<feature>/; Mixkit (pinned commit of the
     video-shotcraft repo) fills any role without files.
  5. Mix: music ducked under the voice, limited, written to
     public/jarvis-ad/mix.mp3; timeline to src/JarvisAd/timeline.json.

Run from video/:  python scripts/jarvis_ad/build.py
Then:             python scripts/capture/capture_pc.py   (HUD follows the timeline)
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

HERE = Path(__file__).resolve().parent
VIDEO = HERE.parents[1]
REPO = VIDEO.parent
CACHE = VIDEO / ".cache" / "jarvis-ad"
PUBLIC = VIDEO / "public" / "jarvis-ad"
TIMELINE = VIDEO / "src" / "JarvisAd" / "timeline.json"
STORY = json.loads((HERE / "storyboard.json").read_text(encoding="utf-8"))
VO_DIR = HERE / "vo" / "ru"
SFX_DIR = HERE / "sfx"

SR = 48000
FPS = 30
BPM = STORY["bpm"]
BEAT_S = 60 / BPM
BEAT_F = round(BEAT_S * FPS)
assert abs(BEAT_S * FPS - BEAT_F) < 1e-9, "beats must land on whole frames"

SFX_BASE = ("https://raw.githubusercontent.com/louiseliu/hyperFrames-video-shotcraft/"
            "1df77f1ab080323558f70a5fb880fad0f88f87fc/assets/audio/sfx/")
# role -> default Mixkit file. Own sounds go in sfx/<role>/ (variants) or
# sfx/<role>-<feature id>/ (one feature's signature) next to this script and win.
SFX = {
    "hum": "scifi/tech-hum-futuristic.mp3",
    "boot": "data/power-up-electronic.mp3",
    "slam": "impact/bass-hit-futuristic.mp3",
    "whoosh": "data/whoosh-electric.mp3",
    "type": "scifi/scifi-click.mp3",
    "think": "data/data-scan.mp3",
    "card": "scifi/hitech-bleep.mp3",
    "tap": "scifi/scifi-click.mp3",
    "lock": "mech/lock-digital.mp3",
    "ignite": "impact/impact-cine-big.mp3",
    "aura": "light/light-aura.mp3",
}


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


# ── 1. sections and voice-over ───────────────────────────────────────────────

def sections() -> list[dict]:
    out = []
    for kind in ("intro", "features", "outro"):
        for i, sec in enumerate(STORY[kind]):
            out.append({**sec, "kind": "feature" if kind == "features" else kind,
                        **({"index": i + 1} if kind == "features" else {})})
    return out


def load_vo() -> dict[str, np.ndarray]:
    """Jarvis's replies that exist as files (dry, trimmed); others stay silent."""
    vo = {}
    for sec in sections():
        path = next(iter(sorted(VO_DIR.glob(f"{sec['id']}.*"))), None)
        if path:
            vo[sec["id"]] = _trim(load(path).mean(axis=1))
    return vo


def _trim(mono: np.ndarray) -> np.ndarray:
    idx = np.flatnonzero(np.abs(mono) > 0.01)
    return mono[max(0, idx[0] - 480): idx[-1] + 2400] if len(idx) else mono


def treat_voice(mono: np.ndarray, light: bool = True) -> np.ndarray:
    x = filt(mono, butter("highpass", 90))
    if light:  # a voice that already has its character: just clean, even out, a touch of room
        x = compress(x[:, None].repeat(2, axis=1), threshold_db=-20, ratio=2.5)
        x = reverb(x, 0.9, wet=0.08, seed=3)
        return x / np.abs(x).max() * 0.89
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

def marks(sec: dict, dur: int) -> dict:
    """Frames inside a section where things happen (relative to its start)."""
    if sec["kind"] == "feature":
        think = round(dur * 0.36)
        return {"phrase": 4, "think": think, "card": think + 12, "speak": think + 16}
    return {"boot": {"speak": 3 * BEAT_F}, "title": {"speak": 10},
            "phone": {"speak": BEAT_F}, "end": {"speak": 2 * BEAT_F}}.get(sec["id"], {"speak": 10})


def estimate_frames(text: str) -> int:
    return math.ceil(len(text) * 0.065 * FPS)  # ~15 characters a second


def build_timeline(vo: dict[str, np.ndarray]) -> dict:
    out, cursor = [], 0
    for sec in sections():
        beats = sec["beats"]
        vo_frames = math.ceil(len(vo[sec["id"]]) / SR * FPS) if sec["id"] in vo else estimate_frames(sec["reply"])
        while True:  # stretch by whole beats until the reply fits before the next cut
            dur = beats * BEAT_F
            m = marks(sec, dur)
            if m["speak"] + vo_frames + 6 <= dur:  # estimated length too, so real VO barely re-times
                break
            beats += 1
        if beats != sec["beats"]:
            print(f"  {sec['id']}: reply needs {beats} beats, not {sec['beats']}")
        out.append({k: v for k, v in sec.items() if k not in ("args", "result")}
                   | {"from": cursor, "durationInFrames": dur, "beats": beats, "marks": m,
                      "voFrames": vo_frames, "hasVoice": sec["id"] in vo,
                      "tool": sec.get("tool"), "args": sec.get("args"), "result": sec.get("result")})
        cursor += dur
    return {"fps": FPS, "bpm": BPM, "beatFrames": BEAT_F, "durationInFrames": cursor, "lang": "ru",
            "ui": STORY["ui"], "featureCount": len(STORY["features"]), "sections": out,
            "forecast": STORY["forecast"]}


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
    sc = {s["id"]: s for s in tl["sections"]}
    t_of = lambda f: f / FPS  # noqa: E731
    groove_start = t_of(next(s for s in tl["sections"] if s["kind"] == "feature")["from"])
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

    # groove: pulse bass (8ths), kick on beats, clap on 2/4, hat on off-beats — through the features
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
        # clap on 2 and 4
        if b % 2 == 1:
            cn = int(0.12 * SR)
            clap = filt(rng.standard_normal(cn).astype(np.float32), butter("bandpass", [900, 5000]))
            clap *= np.exp(-np.arange(cn) / SR * 30)
            place(music, (clap[:, None] * [0.16, 0.14]).astype(np.float32), t)
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
    place(music, (boom[:, None] * [0.6, 0.6]).astype(np.float32), end_start + BEAT_S)

    music = reverb(music, 2.4, wet=0.22, seed=5)[:n]
    return music, kicks


# ── 4. sfx ───────────────────────────────────────────────────────────────────

def sfx(role: str, k: int = 0, feature: str = "") -> np.ndarray:
    """A sound for a role, peak-normalized. Own files win: sfx/<role>-<feature>/ (one
    feature's signature sound), then sfx/<role>/ (variants, rotated by k so 15 cuts
    don't all sound the same), then the Mixkit default."""
    for folder in ([SFX_DIR / f"{role}-{feature}"] if feature else []) + [SFX_DIR / role]:
        files = sorted(f for f in folder.glob("*") if f.suffix.lower() in (".wav", ".mp3", ".flac", ".ogg")) \
            if folder.is_dir() else []
        if files:
            clip = load(files[k % len(files)])
            break
    else:
        if role not in SFX:
            return np.zeros((1, 2), dtype=np.float32)
        clip = load(fetch(SFX_BASE + SFX[role], CACHE / "sfx" / SFX[role]))
    return (clip / (np.abs(clip).max() or 1) * 0.7).astype(np.float32)


def lay_sfx(bus: np.ndarray, tl: dict) -> list[dict]:
    cues = []

    def at(role: str, t: float, gain: float, hit: bool = False, trim: float | None = None,
           k: int = 0, feature: str = ""):
        clip = sfx(role, k, feature)
        if trim:
            n = min(len(clip), int(trim * SR))
            clip = clip[:n] * adsr(n, 0.0, min(0.3, trim / 2))[:, None]
        offset = peak_time(clip) if hit else 0.0
        place(bus, clip, t - offset, gain)
        cues.append({"sfx": role, "frame": round(t * FPS)})

    sc = {s["id"]: s for s in tl["sections"]}
    s = lambda sid: sc[sid]["from"] / FPS  # noqa: E731
    at("ambience", 0.0, -20, trim=s("title") + 0.5)
    at("boot", 0.8, -8, hit=True)                       # the app window lands
    at("glitch", s("title"), -14, trim=0.6)
    at("slam", s("title") + 0.2, -5, hit=True)          # TitleScene HIT
    for sec in tl["sections"]:
        if sec["kind"] != "feature":
            continue
        t0, m, k = sec["from"] / FPS, sec["marks"], sec["index"] - 1
        at("whoosh", t0, -9, hit=True, k=k)
        at("slide", t0 + m["phrase"] / FPS, -15, k=k)
        at("type", t0 + (m["phrase"] + 3) / FPS, -17, trim=0.9, k=k)
        at("think", t0 + m["think"] / FPS, -14, trim=0.7, k=k)
        at("card", t0 + m["card"] / FPS, -8, trim=1.6, k=k, feature=sec["id"])
    ph = sc["phone"]
    at("whoosh", s("phone"), -9, hit=True, k=7)
    for n, f in enumerate(phone_taps(ph)):
        at("tap", (ph["from"] + f) / FPS, -10, k=n)
    ignite = s("end") + BEAT_S                          # EndScene IGNITE = one beat in
    at("riser", ignite, -10, hit=True)                  # its peak lands on the ignition
    at("lock", s("end") - 0.35, -12)
    at("ignite", ignite, -5, hit=True)
    at("aura", ignite + 0.2, -15)
    at("cta", s("end") + (BEAT_F + 78) / FPS, -12)      # EndScene: CTA at IGNITE + 38 + 40
    return cues


def phone_taps(phone: dict) -> list[int]:
    """Frames (in the phone section) where a finger taps the Mini App — PhoneScene.tsx reads the same."""
    d = phone["durationInFrames"]
    return [round(d * 0.42), round(d * 0.6), round(d * 0.78)]


# ── 5. mix ───────────────────────────────────────────────────────────────────

def mix(tl: dict, vo: dict[str, np.ndarray]) -> np.ndarray:
    music, _ = compose_score(tl)
    n = len(music)
    voice = np.zeros((n, 2), dtype=np.float32)
    for sec in tl["sections"]:
        if sec["id"] in vo:
            place(voice, treat_voice(vo[sec["id"]], light=True), (sec["from"] + sec["marks"]["speak"]) / FPS)

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
    tl["sfxCues"] = lay_sfx(fx, tl)

    bed = music * duck[:, None] * 0.8 + fx * 0.9
    for name, stem in (("voice", voice), ("bed", bed)):  # stems, for checking the balance
        sf.write(CACHE / f"stem-{name}.wav", stem, SR, subtype="PCM_24")
    out = bed + voice
    end = int((tl["durationInFrames"] / FPS) * SR)
    out = out[:end]
    out[-int(0.8 * SR):] *= np.linspace(1, 0, int(0.8 * SR))[:, None] ** 1.5
    out = np.tanh(out * 1.1) / np.tanh(1.1)
    out = out / np.abs(out).max() * 10 ** (-1 / 20)

    # per-frame voice level for the HUD and the visuals: real where there is
    # voice-over, a speech-like placeholder envelope where there is none yet
    spf = SR // FPS
    frames = tl["durationInFrames"]
    rms = np.sqrt((voice[: frames * spf, 0].reshape(frames, spf) ** 2).mean(axis=1))
    level = np.clip(rms / (rms.max() or 1), 0, 1)
    rng = np.random.default_rng(3)
    for sec in tl["sections"]:
        if not sec["hasVoice"]:
            a = sec["from"] + sec["marks"]["speak"]
            k = np.arange(sec["voFrames"])
            env = (0.55 + 0.45 * np.abs(np.sin(k * 0.55 + rng.random() * 3))) * (0.6 + 0.4 * rng.random(len(k)))
            env *= np.minimum(1, np.minimum(k + 1, len(k) - k) / 4)
            level[a:a + len(k)] = np.maximum(level[a:a + len(k)], env[: max(0, min(len(k), frames - a))])
    tl["voiceLevel"] = [round(float(v), 3) for v in level]
    tl["voicePlaceholder"] = not all(s["hasVoice"] for s in tl["sections"])
    return out.astype(np.float32)


# ── 6. fonts ──────────────────────────────────────────────────────────────────

def copy_fonts() -> None:
    dst = PUBLIC / "fonts"
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("Tektur-Regular.ttf", "Tektur-Medium.ttf", "Tektur-OFL.txt"):
        shutil.copy(REPO / "design" / "fonts" / f, dst / f)
    mono = VIDEO / "node_modules" / "@fontsource" / "jetbrains-mono" / "files"
    shutil.copy(mono / "jetbrains-mono-latin-500-normal.woff2", dst / "JetBrainsMono-500.woff2")
    shutil.copy(mono / "jetbrains-mono-cyrillic-500-normal.woff2", dst / "JetBrainsMono-500-cyrillic.woff2")
    shutil.copy(mono.parent / "LICENSE", dst / "JetBrainsMono-OFL.txt")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    vo = load_vo()
    print(f"voice-over: {len(vo)}/{len(sections())} replies from {VO_DIR.relative_to(VIDEO)}"
          + ("" if len(vo) == len(sections()) else " — the rest are silent (run fish_vo.py)"))
    tl = build_timeline(vo)
    print(f"timeline {tl['durationInFrames']} frames ({tl['durationInFrames'] / FPS:.1f}s), "
          f"{tl['featureCount']} features")
    print("score + sfx + mix")
    out = mix(tl, vo)
    wav = CACHE / "mix.wav"
    sf.write(wav, out, SR, subtype="PCM_24")
    subprocess.run(["npx", "remotion", "ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                    "-c:a", "libmp3lame", "-b:a", "256k", str(PUBLIC / "mix.mp3")], cwd=VIDEO, check=True)
    TIMELINE.write_text(json.dumps(tl, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("fonts")
    copy_fonts()
    print("done")


if __name__ == "__main__":
    main()
