"""Synthesize Jarvis's replies for the ad (storyboard.json) with Fish Audio — the same voice and
model the JARVIS app speaks with (telegram_bot/tts_fish.py).

Standard library only, so it runs anywhere Python does — e.g. on the owner's
PC, where the app's Fish key already lives:

    python video/scripts/jarvis_ad/fish_vo.py

Key:   FISH_API_KEY, else "fish_api_key" in %APPDATA%/JARVIS/api_keys.json
       or config/api_keys.json (where the app's «Ключи» screen saves it).
Voice: FISH_VOICE_ID / "fish_voice_id", else the app's default Russian Jarvis.
Model: FISH_MODEL / "fish_model", else the app's default.

Writes video/scripts/jarvis_ad/vo/ru/<section>.mp3 — commit them; build.py
places each at its section's "speak" mark.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = HERE / "vo" / "ru"

DEFAULT_VOICE = "680d74fbef69419f87cfc70f092a1451"  # app default: «русский Джарвис»
DEFAULT_MODEL = "s2.1-pro-free"


def _config() -> dict:
    cfg: dict = {}
    for path in (REPO / "config" / "api_keys.json",
                 Path(os.getenv("APPDATA") or Path.home() / ".config") / "JARVIS" / "api_keys.json"):
        try:
            cfg.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if v})
        except (OSError, ValueError):
            pass
    return cfg


def settings() -> tuple[str, str, str]:
    cfg = _config()
    key = (os.getenv("FISH_API_KEY") or cfg.get("fish_api_key") or "").strip()
    voice = (os.getenv("FISH_VOICE_ID") or cfg.get("fish_voice_id") or DEFAULT_VOICE).strip()
    model = (os.getenv("FISH_MODEL") or cfg.get("fish_model") or DEFAULT_MODEL).strip()
    return key, voice, model


def synth(text: str, key: str, voice: str, model: str) -> bytes:
    body = json.dumps({"text": text, "reference_id": voice, "format": "mp3",
                       "mp3_bitrate": 192, "latency": "normal"}).encode("utf-8")
    req = urllib.request.Request("https://api.fish.audio/v1/tts", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "model": model, "User-Agent": "JARVIS-ad/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main() -> int:
    key, voice, model = settings()
    if not key:
        print("Нет ключа Fish: задайте FISH_API_KEY или впишите его в окне «Ключи» Джарвиса.")
        return 1
    story = json.loads((HERE / "storyboard.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for scene in story["intro"] + story["features"] + story["outro"]:
        text = scene["reply"]
        try:
            audio = synth(text, key, voice, model)
        except urllib.error.HTTPError as e:
            print(f"Fish ответил {e.code}: {e.read()[:300]!r}")
            return 1
        (OUT / f"{scene['id']}.mp3").write_bytes(audio)
        print(f"  {scene['id']:8s} {len(audio) // 1024:4d} КБ  {text}")
    print(f"Готово: {OUT}. Голос {voice}, модель {model}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
