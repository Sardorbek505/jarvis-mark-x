"""Capture the real PC app (PyQt6) for the JarvisAd video.

1. Stills of app pages (Commands, Study, About me, Contacts, Settings) with
   demo data -> public/jarvis-ad/pc/<page>.png
2. The feature tour as a live HUD session, recorded frame by frame ->
   public/jarvis-ad/pc/hud.mp4: each feature of src/JarvisAd/timeline.json
   (scripts/jarvis_ad/storyboard.json) is listened to, "thought" about (the
   orb takes the tool's shape), answered with the app's own result card and
   spoken.
   The app's clock is replaced by a virtual one that advances exactly 1/30 s
   per frame, so every video frame is a real frame of the app's own animation.
   State changes, voice level (from the ad's voice-over), subtitles and result
   cards go through the app's public API (set_state, set_level, show_card...),
   with cards built by core.result_card — the same path main.py uses.

Run from video/ after scripts/jarvis_ad/build.py:
    python scripts/capture/capture_pc.py
Needs: requirements-dev.txt of the repo (PyQt6, psutil...), libegl1, libportaudio2.
Fonts: Segoe UI / Consolas are Windows-only; see fonts.py for the stand-ins.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEO = HERE.parents[1]
OUT = VIDEO / "public" / "jarvis-ad" / "pc"
TIMELINE = VIDEO / "src" / "JarvisAd" / "timeline.json"
CARDS = VIDEO / "src" / "JarvisAd" / "hudCards.json"
W, H = 1280, 800          # logical window size
SCALE = 1.5               # -> 1920 x 1200 frames

sys.path.insert(0, str(HERE))
from demo_data import SRC, env, seed, source_copy  # noqa: E402
from fonts import fonts_conf  # noqa: E402


class VirtualClock:
    """Stands in for the `time` module inside ui.py: monotonic time is ours."""

    def __init__(self):
        import time as real
        self._real = real
        self.t = 1000.0

    def monotonic(self):
        return self.t

    perf_counter = monotonic

    def time(self):
        return self._real.time()

    def __getattr__(self, name):
        return getattr(self._real, name)


def run() -> None:
    seed()
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    import ui as jarvis_ui
    from core.result_card import build_card

    clock = VirtualClock()
    jarvis_ui.time = clock
    win = jarvis_ui.JarvisUI(str(SRC / "face.png"))
    win.resize(W, H)
    win.show()
    hud = win._hud
    hud._tmr.stop()           # we step the animation ourselves
    hud._last = clock.t

    def pump(n=3):
        for _ in range(n):
            app.processEvents()

    def frame() -> bytes:
        clock.t += 1 / 30
        hud._step()
        pump()
        img = win.grab().toImage().convertToFormat(QImage.Format.Format_RGB888)
        ptr = img.constBits()
        ptr.setsize(img.sizeInBytes())
        stride = img.bytesPerLine()
        raw = bytes(ptr)
        row = img.width() * 3
        return b"".join(raw[y * stride: y * stride + row] for y in range(img.height()))

    OUT.mkdir(parents=True, exist_ok=True)

    # ── stills ──
    for key in ("commands", "study", "about", "contacts", "settings"):
        win.show_page(key)
        for _ in range(20):
            frame()
        win.grab().save(str(OUT / f"{key}.png"))
        print("  still", key)
    win.show_page("home")

    # ── live session: the feature tour, driven by the timeline ──
    tl = json.loads(TIMELINE.read_text(encoding="utf-8"))
    level = tl["voiceLevel"]
    seed_forecast(tl["forecast"])

    def card(tool, args, result):
        c = build_card(tool, args or {}, result)
        if c:
            win.show_card(c["title"], c["address"], c["body"], b"", c.get("extra", ""))

    def clear_card():
        hud._card = None  # no public "hide" — a card would otherwise linger 14 s into the next feature

    from collections import defaultdict
    events: dict[int, list] = defaultdict(list)
    at = lambda f, fn: events[round(f)].append(fn)  # noqa: E731
    listen: set[int] = set()
    speaking: set[int] = set()

    at(0, lambda: win.set_state("ИНИЦИАЛИЗАЦИЯ"))
    at(48, lambda: win.set_state("ОЖИДАЕТ"))
    for sec in tl["sections"]:
        t0, m = sec["from"], sec["marks"]
        a, b = t0 + m["speak"], t0 + m["speak"] + sec["voFrames"]
        if sec["kind"] == "feature":
            at(t0, clear_card)
            at(t0 + m["phrase"], lambda: win.set_state("СЛУШАЕТ"))
            listen.update(range(t0 + m["phrase"] + 3, t0 + m["think"] - 2))
            at(t0 + m["think"], lambda tool=sec["tool"]: (win.set_state("ДУМАЕТ"), win.lock_on(tool)))
            at(t0 + m["card"], lambda sec=sec: card(sec["tool"], sec["args"], sec["result"]))
        elif sec["kind"] == "outro":
            at(t0, clear_card)
        at(a, lambda text=sec["reply"]: (win.set_state("ГОВОРИТ"), win.set_subtitle(text)))
        at(b + 6, lambda: win.set_state("ОЖИДАЕТ"))
        speaking.update(range(a, b))
    # where each feature's result card sits in the recording (the ad lifts it out in close-up)
    card_probe = {s["from"] + s["marks"]["card"] + 24: s["id"] for s in tl["sections"] if s["kind"] == "feature"}
    cards: dict[str, list[int]] = {}

    total = tl["durationInFrames"]
    img = win.grab().toImage()
    fw, fh = img.width(), img.height()
    ffmpeg = _ffmpeg()
    proc = subprocess.Popen(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{fw}x{fh}",
         "-r", "30", "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "21", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(OUT / "hud.mp4")],
        stdin=subprocess.PIPE)
    import math
    for f in range(total):
        for fn in events.get(f, ()):
            fn()
        if f in listen:  # the user talking: a lively mic envelope
            win.set_level(0.35 + 0.3 * abs(math.sin(f * 0.9)) * abs(math.sin(f * 0.23)))
        elif f in speaking:
            win.set_level(level[f])
        data = frame()
        if f in card_probe and (box := card_box(data, fw, fh)):
            cards[card_probe[f]] = box
        proc.stdin.write(data)
        if f % 150 == 0:
            print(f"  hud {f}/{total}")
    proc.stdin.close()
    proc.wait()
    print(f"  hud.mp4 {fw}x{fh}, {total} frames")
    CARDS.write_text(json.dumps(cards, indent=1) + "\n")
    print(f"  {len(cards)} result cards measured -> {CARDS.name}")


def card_box(rgb: bytes, w: int, h: int) -> list[int] | None:
    """Bounding box [x, y, w, h] of the result card in the right part of a HUD frame."""
    import numpy as np
    a = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3).mean(axis=2)
    x0, y0 = int(w * 0.6), int(h * 0.09)  # right of the orb, below the app's header bar
    m = a[y0:, x0:] > 40
    rows, cols = np.where(m.sum(1) > w * 0.1)[0], np.where(m.sum(0) > h * 0.05)[0]
    if not len(rows) or not len(cols):
        return None
    pad = 10
    return [int(cols.min() + x0 - pad), int(rows.min() + y0 - pad),
            int(cols.max() - cols.min() + 2 * pad), int(rows.max() - rows.min() + 2 * pad)]


def seed_forecast(fc: dict) -> None:
    """The weather card draws from actions.weather.last_forecast — fill it as a real lookup would."""
    from actions.weather import last_forecast, weather_kind
    days = [{k: v for k, v in d.items() if k != "code"} | {"kind": weather_kind(d["code"])} for d in fc["days"]]
    last_forecast[fc["city"].lower()] = {k: v for k, v in fc.items() if k not in ("code", "days")} | {
        "kind": weather_kind(fc["code"]), "days": days}


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which("ffmpeg") or "ffmpeg"


if __name__ == "__main__":
    if os.environ.get("JARVIS_CAPTURE_CHILD") != "1":
        # re-run in an isolated, headless environment
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-capture-"))
        e = env(tmp, fonts_conf(VIDEO / ".cache" / "fonts"), SCALE)
        e["JARVIS_CAPTURE_CHILD"] = "1"
        src = source_copy(tmp / "src")
        e["JARVIS_SRC"] = e["PYTHONPATH"] = str(src)
        (tmp / "appdata").mkdir()
        e["APPDATA"] = e["HOME"] = str(tmp / "appdata")
        subprocess.run([sys.executable, __file__], env=e, cwd=src, check=True)
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        run()
