"""Интро Джарвиса (два хлопка): общий сценарий для картинки (ui_intro.py) и звука.

Звук синтезируется здесь же, без файлов: так он всегда совпадает со сценарием
по времени, а установщик не растёт. Сценарий:

  0,0  экран гаснет — низкий «спуск» питания и щелчок
  0,4  сверху заполняется полоса-сканер — тихий тик на каждое деление
  2,0  загорается кольцо — нарастающий гул
  3,2  удар — сабвуфер, вспышка; внутри кольца оживает шар
  3,4  от кольца расходятся узлы-умения (иконки) — по ноте на каждый, под ними — пэд
  5,6  слева — проверка систем: иконки загораются зелёным, тик на каждую
  6,6  шар собирается в лицо Джарвиса, оно машет — финальный аккорд и колокольчик
  7,8  картинка тает, к 8,4 — обычный Джарвис
"""
from __future__ import annotations

import numpy as np

RATE = 24000
TITLE = "СИСТЕМНАЯ ПРОВЕРКА"
FINAL = "ДЖАРВИС — ОНЛАЙН"

T_DARK, T_TYPE, T_RING, T_HIT, T_NODES, T_CHECKS, T_FINAL, T_FADE, T_END = (
    0.0, 0.4, 2.0, 3.2, 3.4, 5.6, 6.6, 7.8, 8.4)
TYPE_STEP = 0.08                     # секунд на букву
NODE_STEP = 0.2                      # между узлами
CHECK_STEP = 0.16                    # между строками проверки

NODES = ("Музыка", "Звонки", "Память", "Календарь", "Погода", "Браузер",
         "Telegram", "Экран", "YouTube", "Файлы")
CHECKS = ("Микрофон", "Голос", "Слово «Джарвис»", "Gemini", "Память", "Telegram")
# Без текста на экране: у каждого узла и строки проверки — иконка (ui_icons.draw_icon;
# «sun» и «calendar» рисует сам ui_intro).
NODE_ICONS = {"Музыка": "note", "Звонки": "phone", "Память": "book", "Календарь": "calendar",
              "Погода": "sun", "Браузер": "globe", "Telegram": "plane", "Экран": "eye",
              "YouTube": "play", "Файлы": "copy"}
CHECK_ICONS = {"Микрофон": "mic", "Голос": "speak", "Слово «Джарвис»": "spark", "Gemini": "bolt",
               "Память": "book", "Telegram": "plane"}

# Нота на каждый узел — пентатоника ля минор, вверх по кругу.
_NODE_HZ = (440.0, 523.25, 587.33, 659.25, 783.99, 880.0, 1046.5, 1174.66, 1318.51, 1567.98)


def type_times() -> list[float]:
    return [T_TYPE + i * TYPE_STEP for i, ch in enumerate(TITLE) if ch != " "]


def node_times() -> list[float]:
    return [T_NODES + i * NODE_STEP for i in range(len(NODES))]


def check_times() -> list[float]:
    return [T_CHECKS + i * CHECK_STEP for i in range(len(CHECKS))]


def _t(sec: float) -> np.ndarray:
    return np.arange(int(sec * RATE)) / RATE


def _add(buf: np.ndarray, at: float, sig: np.ndarray):
    i = int(at * RATE)
    j = min(len(buf), i + len(sig))
    if j > i:
        buf[i:j] += sig[: j - i]


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / RATE
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-np.maximum(t - attack, 0) / decay)


def _blip(hz: float, dur: float, decay: float, vol: float) -> np.ndarray:
    t = _t(dur)
    tone = np.sin(2 * np.pi * hz * t) + 0.3 * np.sin(2 * np.pi * hz * 2 * t)
    return tone * _env(len(t), 0.003, decay) * vol


def _noise(dur: float, rng, hi: bool = True) -> np.ndarray:
    x = rng.normal(0, 1, int(dur * RATE))
    return np.diff(x, prepend=0.0) * 0.5 if hi else np.convolve(x, np.ones(8) / 8, "same")


def render(seed: int = 7) -> np.ndarray:
    """Весь звук интро: float32, −1…1, моно, RATE Гц."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int((T_END + 0.6) * RATE))

    # 0,0 — питание уходит: спуск 140→40 Гц и щелчок реле
    t = _t(0.7)
    f = 140 * (40 / 140) ** (t / 0.7)
    _add(out, T_DARK, np.sin(2 * np.pi * np.cumsum(f) / RATE) * _env(len(t), 0.01, 0.35) * 0.45)
    _add(out, T_DARK, _noise(0.03, rng) * _env(int(0.03 * RATE), 0.0005, 0.006) * 0.6)

    # 0,4 — печать заголовка
    for k, at in enumerate(type_times()):
        _add(out, at, _blip(2400 + 180 * (k % 3), 0.04, 0.008, 0.10))

    # 2,0 — гул: пила и шум, растут к удару
    dur = T_HIT - T_RING
    t = _t(dur)
    f = 70 * (320 / 70) ** (t / dur) ** 1.6
    ph = 2 * np.pi * np.cumsum(f) / RATE
    saw = 2 * ((ph / (2 * np.pi)) % 1.0) - 1
    rise = (t / dur) ** 2.2
    hum = (0.18 * np.sin(ph) + 0.06 * saw + 0.08 * _noise(dur, rng)[: len(t)]) * rise
    _add(out, T_RING, hum)

    # 3,2 — удар: сабвуфер с «падением» высоты + хлопок шума + хвост
    t = _t(1.6)
    f = 55 + 70 * np.exp(-t / 0.05)
    boom = np.sin(2 * np.pi * np.cumsum(f) / RATE) * _env(len(t), 0.002, 0.45) * 0.9
    crack = _noise(0.25, rng) * _env(int(0.25 * RATE), 0.0005, 0.04) * 0.5
    tail = _noise(1.6, rng, hi=False)[: len(t)] * _env(len(t), 0.01, 0.6) * 0.25
    _add(out, T_HIT, boom + tail)
    _add(out, T_HIT, crack)

    # 3,4 — пэд под узлами: ля минор с ноной, медленно вспухает и держится до финала
    dur = T_FINAL - T_NODES + 0.4
    t = _t(dur)
    pad = sum(np.sin(2 * np.pi * hz * t + k) for k, hz in enumerate((110.0, 164.81, 220.0, 246.94, 329.63)))
    pad *= np.clip(t / 1.2, 0, 1) * np.clip((dur - t) / 0.5, 0, 1) * 0.045
    _add(out, T_NODES, pad)
    for at, hz in zip(node_times(), _NODE_HZ):
        _add(out, at, _blip(hz, 0.5, 0.12, 0.16))

    # 5,6 — проверка систем: сухие двойные тики
    for at in check_times():
        _add(out, at, _blip(1800, 0.03, 0.006, 0.12))
        _add(out, at + 0.04, _blip(2600, 0.03, 0.006, 0.09))

    # 6,6 — финал: аккорд-стаб + колокольчик (негармоничные обертоны)
    t = _t(2.2)
    stab = sum(np.sin(2 * np.pi * hz * t) for hz in (220.0, 277.18, 329.63, 440.0, 554.37))
    _add(out, T_FINAL, stab * _env(len(t), 0.004, 0.5) * 0.12)
    bell = sum(a * np.sin(2 * np.pi * 1318.5 * r * t) for r, a in ((1, 1), (2.76, 0.5), (5.4, 0.25), (8.93, 0.12)))
    _add(out, T_FINAL + 0.05, bell * _env(len(t), 0.002, 0.7) * 0.10)
    _add(out, T_FINAL, np.sin(2 * np.pi * 55 * t) * _env(len(t), 0.003, 0.4) * 0.5)

    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.89).astype(np.float32)


def pcm16(volume: float = 0.6, rate: int = RATE) -> bytes:
    """Звук интро в формате колонок Джарвиса: int16, моно."""
    x = render()
    if rate != RATE:
        n = int(len(x) * rate / RATE)
        x = np.interp(np.arange(n) * RATE / rate, np.arange(len(x)), x)
    return (np.clip(x * volume, -1, 1) * 32767).astype("<i2").tobytes()
