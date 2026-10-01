"""Capture the real PC app (PyQt6) for the JarvisAd video.

1. Stills of app pages (Commands, Study, About me, Contacts, Settings) with
   demo data -> public/jarvis-ad/pc/<page>.png
2. A live HUD session recorded frame by frame -> public/jarvis-ad/pc/hud.mp4.
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

    # ── live session ──
    tl = json.loads(TIMELINE.read_text())
    level = tl["voiceLevel"]
    sc = {s["id"]: s for s in tl["scenes"]}
    vo = {sid: (s["from"] + s["vo"]["from"], s["from"] + s["vo"]["from"] + s["vo"]["durationInFrames"], s["vo"]["text"])
          for sid, s in sc.items()}

    def card(tool, args, result):
        c = build_card(tool, args, result)
        win.show_card(c["title"], c["address"], c["body"], b"", c.get("extra", ""))

    from collections import defaultdict
    events: dict[int, list] = defaultdict(list)
    at = lambda f, fn: events[round(f)].append(fn)  # noqa: E731

    ctrl, step = sc["control"]["from"], sc["control"]["durationInFrames"] / 4
    at(0, lambda: win.set_state("ИНИЦИАЛИЗАЦИЯ"))
    at(48, lambda: win.set_state("ОЖИДАЕТ"))
    for a, b, text in vo.values():
        at(a, lambda text=text: (win.set_state("ГОВОРИТ"), win.set_subtitle(text)))
        at(b + 6, lambda: win.set_state("ОЖИДАЕТ"))
    at(vo["title"][1] + 8, lambda: win.set_state("СЛУШАЕТ"))
    at(sc["vision"]["from"], lambda: (win.set_state("ДУМАЕТ"), win.lock_on("look_at_screen")))
    at(vo["vision"][0] + 70, lambda: card(
        "look_at_screen", {},
        "main.py, строка 42: max() от пустого списка — добавь проверку «if not items: return None»."))
    at(sc["memory"]["from"] + 20, lambda: card(
        "save_to_memory", {"key": "Дедлайн", "value": "реферат по матану — в пятницу"}, "Запомнил"))
    at(ctrl, lambda: (win.set_state("ДУМАЕТ"), win.lock_on("computer_control")))
    at(ctrl + step - 4, lambda: card("open_app", {"app_name": "VS Code"}, "Открыл Visual Studio Code"))
    at(ctrl + 2 * step - 4, lambda: card("computer_control", {"action": "volume_set", "value": 20}, "Громкость: 20%"))
    at(ctrl + 3 * step - 4, lambda: card("send_to_telegram", {"text": "Скриншот экрана"}, "Отправил в Telegram"))

    total = tl["durationInFrames"]
    listen = range(vo["title"][1] + 8, sc["voice"]["from"] + sc["voice"]["vo"]["from"])
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
        elif any(a <= f < b for a, b, _ in vo.values()):
            win.set_level(level[f])
        proc.stdin.write(frame())
        if f % 150 == 0:
            print(f"  hud {f}/{total}")
    proc.stdin.close()
    proc.wait()
    print(f"  hud.mp4 {fw}x{fh}, {total} frames")


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
