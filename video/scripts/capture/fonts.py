"""Open stand-ins for the Windows UI fonts the app asks for (Segoe UI, Consolas,
Courier New), so headless captures on Linux look like the app on Windows.
All have Cyrillic: Noto Sans, JetBrains Mono, Inter (SIL OFL, from google/fonts).
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

_BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
_FILES = {
    "NotoSans.ttf": _BASE + "notosans/NotoSans%5Bwdth%2Cwght%5D.ttf",
    "JetBrainsMono.ttf": _BASE + "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
    "Inter.ttf": _BASE + "inter/Inter%5Bopsz%2Cwght%5D.ttf",
}
_ALIASES = {"Segoe UI": "Noto Sans", "Segoe UI Semibold": "Noto Sans",
            "Consolas": "JetBrains Mono", "Courier New": "JetBrains Mono",
            # the Mini App sets no font: on iPhone that is SF — Inter has close metrics
            "sans-serif": "Inter", "system-ui": "Inter", "-apple-system": "Inter"}


def fonts_conf(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    for name, url in _FILES.items():
        if not (cache / name).exists():
            urllib.request.urlretrieve(url, cache / name)
    aliases = "".join(
        f'<alias binding="same"><family>{a}</family><prefer><family>{b}</family></prefer></alias>'
        for a, b in _ALIASES.items())
    conf = cache / "fonts.conf"
    conf.write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        '<include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
        f"<dir>{cache}</dir>{aliases}</fontconfig>")
    return conf
