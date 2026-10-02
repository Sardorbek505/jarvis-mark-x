"""Озвучка ролика: каждая реплика сценария → promo/build/vo/<id>.wav + durations.json.

    python promo/voice.py                 # Fish Audio — голос Джарвиса (ключ из config/api_keys.json или FISH_API_KEY)
    python promo/voice.py --engine rhvoice  # черновой голос, только чтобы выставить тайминг (Linux, RHVoice)

Fish вызывается тем же кодом, что и в Джарвисе (telegram_bot/tts_fish.py): тот же
reference_id, та же модель — голос в ролике совпадает с голосом программы.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "promo"))

from scenes import INTRO_LINES, SCENES, spoken  # noqa: E402

OUT = ROOT / "promo" / "build" / "vo"
RATE = 44100


def lines() -> list[tuple[str, str]]:
    out = [(f"intro{i}", text) for i, (_at, text) in enumerate(INTRO_LINES)]
    return out + [(s["id"], s["vo"]) for s in SCENES]


def fish(text: str, path: Path):
    from telegram_bot import tts_fish
    if not tts_fish.is_configured():
        sys.exit("Нет ключа Fish: впишите его в окне «Ключи» Джарвиса или задайте FISH_API_KEY.")
    data = tts_fish._request(text, fmt="wav", latency="normal", sample_rate=RATE)
    path.write_bytes(data)


def rhvoice(text: str, path: Path):
    raw = path.with_suffix(".raw.wav")
    subprocess.run(["RHVoice-test", "-p", "aleksandr", "-o", str(raw)], input=text.encode(), check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-ar", str(RATE), "-ac", "1", str(path)],
                   check=True)
    raw.unlink()


def trim_and_measure(path: Path) -> float:
    """Тишину по краям — прочь (иначе субтитры опаздывают), громкость — к -16 LUFS."""
    tmp = path.with_suffix(".tmp.wav")
    flt = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02,areverse,"
           "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,"
           "loudnorm=I=-16:TP=-1.5:LRA=7")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-af", flt, "-ar", str(RATE), "-ac", "1",
                    str(tmp)], check=True)
    tmp.replace(path)
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=("fish", "rhvoice"), default="fish")
    ap.add_argument("--only", nargs="*", help="id реплик, которые переозвучить")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    meta_path = OUT / "durations.json"
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
    synth = fish if a.engine == "fish" else rhvoice
    for key, text in lines():
        if a.only and key not in a.only:
            continue
        path = OUT / f"{key}.wav"
        synth(spoken(text), path)
        meta[key] = {"sec": round(trim_and_measure(path), 3), "engine": a.engine, "text": text}
        print(f"{key:10s} {meta[key]['sec']:6.2f} s  {text}")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
