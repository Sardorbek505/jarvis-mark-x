"""Тайминг ролика — один на съёмку, вёрстку и звук. Источник: длины реплик (voice.py)."""
from __future__ import annotations

import json
from pathlib import Path

from scenes import INTRO_SEC, SCENES

BUILD = Path(__file__).resolve().parent / "build"
VO_AT = 0.8        # реплика начинается после появления проблемы и картинки
TAIL = 1.1         # пауза после реплики — «запятая» перед следующей сценой
MIN_SEC = 4.5


def durations() -> dict:
    return json.loads((BUILD / "vo" / "durations.json").read_text("utf-8"))


def plan() -> list[dict]:
    """[{**scene, start, sec, vo_sec}] — абсолютное время от начала ролика."""
    d = durations()
    t, out = INTRO_SEC, []
    for s in SCENES:
        vo = d[s["id"]]["sec"]
        sec = round(max(MIN_SEC, VO_AT + vo + TAIL), 2)
        out.append({**s, "start": round(t, 3), "sec": sec, "vo_sec": vo})
        t += sec
    return out


def total() -> float:
    p = plan()
    return round(p[-1]["start"] + p[-1]["sec"], 3)
