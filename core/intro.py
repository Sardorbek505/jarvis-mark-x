"""Интро Джарвиса (два хлопка): сценарий, реплики с субтитрами и звук.

Как в образце владельца: экран гаснет, голос Джарвиса говорит, внизу — его
слова субтитрами (слово за словом), в центре загорается шар в золотом кольце,
от него расходится сеть — большие узлы с иконками и подписями, тонкие дуги
по всему экрану. Лица нет. Сценарий:

  0,0  экран гаснет — спуск питания, щелчок реле
  0,9  «Проверка систем.» — тишина, по краям рисуется HUD
  2,6  шар заряжается — нарастающий гул, кольцо дорисовывается
  4,0  удар — вспышка, ударная волна до краёв экрана
  4,3  «Подключаю модули.» — узлы выходят по одному (всё быстрее), каждый — щелчок
  7,4  сеть оживает — дуги по всему экрану, луч проверки обегает узлы
  8,6  «<приветствие>, сэр. Все системы в норме.» — финальный удар и аккорд
  ...  сеть складывается, шар улетает наверх к капсуле

Звук синтезируется здесь, без файлов: всегда совпадает с картинкой.
"""
from __future__ import annotations

import numpy as np

RATE = 24000

T_DARK, T_BOOT, T_CHARGE, T_HIT, T_NODES, T_NET, T_FINAL, T_FADE, T_END = (
    0.0, 0.9, 2.6, 4.0, 4.3, 7.4, 8.6, 11.6, 12.4)

# Реплики Джарвиса: (с какой секунды, текст). Последняя — приветствие (greeting).
LINE_BOOT = (T_BOOT, "Проверка систем.")
LINE_NODES = (T_NODES, "Подключаю модули.")
T_VOICE = T_FINAL + 0.15

# Узлы сети: (подпись, иконка). Порядок — по часовой стрелке сверху.
NODES = (("Музыка", "music"), ("Звонки", "phone"), ("Память", "memory"), ("Календарь", "calendar"),
         ("Погода", "weather"), ("Интернет", "globe"), ("Telegram", "telegram"), ("Экран", "vision"),
         ("YouTube", "video"), ("Учёба", "study"))
CHECKS = ("Микрофон", "Голос", "Слово «Джарвис»", "Gemini", "Память", "Telegram")


def node_times() -> list[float]:
    """Узлы выходят всё быстрее — разгон, а не метроном."""
    out, t, gap = [], T_NODES + 0.35, 0.36
    for _ in NODES:
        out.append(round(t, 3))
        t += gap
        gap = max(0.14, gap * 0.84)
    return out


def word_times(text: str, start: float, dur: float) -> list[tuple[str, float]]:
    """Субтитры слово за словом: когда загорается каждое слово (по длине слов)."""
    words = text.split()
    total = sum(len(w) + 2 for w in words) or 1
    out, t = [], start
    for w in words:
        out.append((w, t))
        t += dur * (len(w) + 2) / total
    return out


def greeting(checks: dict | None = None, hour: int | None = None) -> str:
    """Что скажет Джарвис в конце: приветствие по времени суток и итог проверки."""
    if hour is None:
        from datetime import datetime
        hour = datetime.now().hour
    hello = ("Доброй ночи" if hour < 5 else "Доброе утро" if hour < 12
             else "Добрый день" if hour < 18 else "Добрый вечер" if hour < 23 else "Доброй ночи")
    failed = [name for name in CHECKS if (checks or {}).get(name) is False]
    if not failed:
        state = "Все системы в норме."
    elif len(failed) == 1:
        state = f"Всё работает, кроме {_genitive(failed[0])}."
    else:
        state = "Всё работает, кроме " + ", ".join(_genitive(n) for n in failed[:-1]) + \
                f" и {_genitive(failed[-1])}."
    return f"{hello}, сэр. {state}"


def lines(checks: dict | None = None, hour: int | None = None) -> list[tuple[float, str]]:
    return [LINE_BOOT, LINE_NODES, (T_VOICE, greeting(checks, hour))]


def prewarm_texts() -> list[str]:
    """Что озвучить заранее (в кэш), чтобы первая реплика звучала сразу после хлопков."""
    return [LINE_BOOT[1], LINE_NODES[1]] + [greeting({}, h) for h in (3, 8, 14, 20)]


def _genitive(name: str) -> str:
    return {"Микрофон": "микрофона", "Голос": "голоса", "Слово «Джарвис»": "слова «Джарвис»",
            "Память": "памяти"}.get(name, name)


# ── звук ─────────────────────────────────────────────────────────────────────

def _t(sec: float) -> np.ndarray:
    return np.arange(int(sec * RATE)) / RATE


def _add(buf: np.ndarray, at: float, sig: np.ndarray):
    i = int(at * RATE)
    j = min(len(buf), i + len(sig))
    if j > i >= 0:
        buf[i:j] += sig[: j - i]


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / RATE
    return np.clip(t / max(attack, 1e-4), 0, 1) * np.exp(-np.maximum(t - attack, 0) / decay)


def _lp(x: np.ndarray, n: int) -> np.ndarray:
    return np.convolve(x, np.ones(n) / n, "same")


def _reverb(x: np.ndarray, mix: float = 0.25) -> np.ndarray:
    """Хвост комнаты: несколько затухающих отражений — удар не обрывается сухо."""
    out = x.copy()
    for d, g in ((0.031, 0.5), (0.047, 0.42), (0.067, 0.36), (0.089, 0.3), (0.113, 0.25)):
        k = int(d * RATE)
        y = x.copy()
        for rep in range(1, 9):                       # гребёнка без питоновского цикла по отсчётам
            sh = k * rep
            if sh >= len(y):
                break
            y[sh:] += (g ** rep) * x[:-sh]
        out += mix * y / 5
    return out


def _click(rng, vol: float, bright: float = 0.6) -> np.ndarray:
    n = int(0.03 * RATE)
    x = rng.normal(0, 1, n) * _env(n, 0.0004, 0.004)
    tone = np.sin(2 * np.pi * 3200 * _t(0.03)) * _env(n, 0.0005, 0.006)
    return (np.diff(x, prepend=0.0) * bright + tone * (1 - bright)) * vol


def _blip(hz: float, dur: float, decay: float, vol: float) -> np.ndarray:
    t = _t(dur)
    tone = (np.sin(2 * np.pi * hz * t) + 0.35 * np.sin(2 * np.pi * hz * 2.01 * t)
            + 0.12 * np.sin(2 * np.pi * hz * 3 * t))
    return tone * _env(len(t), 0.002, decay) * vol


def _whoosh(rng, dur: float, f0: float, f1: float, vol: float) -> np.ndarray:
    """Шум через «окно» частот от f0 к f1 — пролёт."""
    n = int(dur * RATE)
    x = rng.normal(0, 1, n)
    out = np.zeros(n)
    hop = 480
    for s in range(0, n, hop):
        f = f0 + (f1 - f0) * (s / n)
        k = max(1, int(RATE / max(f, 60) / 2))
        seg = x[s:s + hop * 2]
        if len(seg) > 1:
            out[s:s + len(seg)] += _lp(seg, k) * np.hanning(len(seg))
    shape = np.sin(np.pi * np.arange(n) / n) ** 1.5
    peak = np.max(np.abs(out)) or 1.0
    return out / peak * shape * vol


def _impact(rng, vol: float, sub: float = 48.0) -> np.ndarray:
    t = _t(2.4)
    f = sub + 90 * np.exp(-t / 0.04)
    boom = np.sin(2 * np.pi * np.cumsum(f) / RATE) * _env(len(t), 0.001, 0.55)
    crack = np.zeros(len(t))
    n = int(0.3 * RATE)
    crack[:n] = np.diff(rng.normal(0, 1, n), prepend=0.0) * _env(n, 0.0003, 0.05) * 0.6
    body = _lp(rng.normal(0, 1, len(t)), 6) * _env(len(t), 0.002, 0.35) * 0.35
    return _reverb(boom + crack + body, 0.5) * vol


def render(seed: int = 11) -> np.ndarray:
    """Все эффекты интро (без голоса): float32, −1…1, моно, RATE Гц."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int((T_END + 0.8) * RATE))

    # 0,0 — экран схлопывается: «пиу» вниз и щелчок реле
    t = _t(0.5)
    f = 1800 * (60 / 1800) ** (t / 0.5)
    _add(out, 0.0, np.sin(2 * np.pi * np.cumsum(f) / RATE) * _env(len(t), 0.003, 0.18) * 0.30)
    _add(out, 0.42, _click(rng, 0.9, 0.9))
    _add(out, 0.42, _blip(90, 0.25, 0.08, 0.35))

    # 0,9 — тихий гул комнаты, тики HUD по краям
    hum_t = _t(T_CHARGE - T_BOOT)
    _add(out, T_BOOT, (np.sin(2 * np.pi * 55 * hum_t) * 0.04 + _lp(rng.normal(0, 1, len(hum_t)), 40) * 0.05)
         * np.clip(hum_t / 0.6, 0, 1))
    for k, at in enumerate((1.1, 1.22, 1.3, 1.55, 1.62, 1.9, 2.05, 2.3)):
        _add(out, at, _click(rng, 0.18 + 0.05 * (k % 2), 0.4))

    # 2,6 — заряд: свист конденсатора вверх + саб нарастает, перед ударом — «вдох»
    dur = T_HIT - T_CHARGE
    t = _t(dur)
    f = 180 * (2600 / 180) ** ((t / dur) ** 1.8)
    whine = np.sin(2 * np.pi * np.cumsum(f * (1 + 0.004 * np.sin(2 * np.pi * 7 * t))) / RATE)
    rise = (t / dur) ** 2.4
    sub = np.sin(2 * np.pi * 42 * t)
    gate = np.clip((T_HIT - 0.1 - (T_CHARGE + t)) / 0.05, 0, 1)        # обрыв за 0,1 с до удара
    _add(out, T_CHARGE, (whine * 0.10 + sub * 0.35) * rise * gate)
    _add(out, T_HIT - 1.2, _whoosh(rng, 1.15, 300, 6000, 0.35))

    # 4,0 — удар
    _add(out, T_HIT, _impact(rng, 0.95))

    # 4,3 — узлы: на каждый — чёткий щелчок и нота, восходящая
    notes = (523.25, 587.33, 659.25, 783.99, 880.0, 987.77, 1046.5, 1174.66, 1318.51, 1567.98)
    for at, hz in zip(node_times(), notes):
        _add(out, at - 0.06, _whoosh(rng, 0.18, 2000, 7000, 0.10))
        _add(out, at, _click(rng, 0.45, 0.7))
        _add(out, at + 0.01, _blip(hz, 0.45, 0.10, 0.14))

    # пэд под сетью — тёплый, медленно вспухает
    dur = T_FADE - T_NODES
    t = _t(dur)
    pad = sum(np.sin(2 * np.pi * hz * t + k) * (1 + 0.15 * np.sin(2 * np.pi * 0.3 * t + k))
              for k, hz in enumerate((110.0, 164.81, 220.0, 277.18, 329.63)))
    _add(out, T_NODES, pad * np.clip(t / 2.0, 0, 1) * np.clip((dur - t) / 1.0, 0, 1) * 0.035)

    # 7,4 — сеть оживает: пролёт и россыпь тихих искр
    _add(out, T_NET, _whoosh(rng, 0.9, 500, 9000, 0.22))
    for k in range(14):
        at = T_NET + 0.15 + k * 0.07 + rng.uniform(0, 0.03)
        _add(out, at, _blip(2000 + 260 * (k % 5), 0.08, 0.02, 0.05))

    # 8,6 — финал: второй удар (мягче) и аккорд с блеском
    _add(out, T_FINAL, _impact(rng, 0.55, 55.0))
    t = _t(3.0)
    chord = sum(np.sin(2 * np.pi * hz * t) for hz in (220.0, 277.18, 329.63, 440.0, 554.37, 659.25))
    _add(out, T_FINAL, _reverb(chord * _env(len(t), 0.01, 0.9) * 0.06, 0.4))
    bell = sum(a * np.sin(2 * np.pi * 1760 * r * t) for r, a in ((1, 1), (2.76, 0.45), (5.4, 0.2)))
    _add(out, T_FINAL + 0.05, bell * _env(len(t), 0.002, 0.6) * 0.05)

    # конец — сеть складывается: пролёт вниз
    _add(out, T_FADE - 0.2, _whoosh(rng, 0.8, 6000, 400, 0.18))

    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.89).astype(np.float32)


def pcm16(volume: float = 0.6, rate: int = RATE) -> bytes:
    """Звук интро в формате колонок Джарвиса: int16, моно."""
    x = render()
    if rate != RATE:
        n = int(len(x) * rate / RATE)
        x = np.interp(np.arange(n) * RATE / rate, np.arange(len(x)), x)
    return (np.clip(x * volume, -1, 1) * 32767).astype("<i2").tobytes()


def mix_voice(intro_pcm: bytes, voice_pcm: bytes, rate: int, at: float = T_VOICE, duck: float = 0.35) -> bytes:
    """Голос поверх звука интро с момента at: музыка под голосом приглушается
    (плавно, без щелчков), а если голос длиннее — звук удлиняется."""
    a = np.frombuffer(intro_pcm, "<i2").astype(np.float32)
    v = np.frombuffer(voice_pcm[: len(voice_pcm) // 2 * 2], "<i2").astype(np.float32)
    i = int(at * rate)
    n = max(len(a), i + len(v))
    out = np.zeros(n, dtype=np.float32)
    out[: len(a)] = a
    gain = np.ones(n, dtype=np.float32)
    ramp = int(0.15 * rate)
    lo, hi = i, i + len(v)
    gain[lo:hi] = np.minimum(gain[lo:hi], duck)
    gain[max(0, lo - ramp):lo] = np.minimum(gain[max(0, lo - ramp):lo],
                                            np.linspace(1, duck, min(ramp, lo), dtype=np.float32))
    tail = min(ramp, n - hi)
    gain[hi:hi + tail] = np.minimum(gain[hi:hi + tail], np.linspace(duck, 1, tail, dtype=np.float32))
    out *= gain
    out[lo:hi] += v
    return np.clip(out, -32768, 32767).astype("<i2").tobytes()


def envelope(pcm: bytes, rate: int, step: float = 0.03) -> list[float]:
    """Громкость голоса кусочками по step с (0…1) — для волны и пульса шара."""
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], "<i2").astype(np.float32)
    n = max(1, int(step * rate))
    rms = [float(np.sqrt(np.mean(x[i:i + n] ** 2))) for i in range(0, len(x), n)] or [0.0]
    top = max(rms) or 1.0
    return [min(1.0, r / top) for r in rms]
