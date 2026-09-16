"""Запись голоса владельца для обучения wake-word «Джарвис».

Без клавиатуры: сигнал → говорите → пауза → следующий сигнал. Клипы по 2 с,
16 кГц int16, сохраняются в data/wake/user_pos (слово) и data/wake/user_neg
(фразы без слова). Запуск: python scripts/record_wake_samples.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # не подменять объект: импорт из другого скрипта закрывал его

SR = 16000
CLIP_SEC = 2.0
OUT = Path(__file__).resolve().parents[1] / "data" / "wake"

POS_PROMPTS = [
    "обычно", "обычно", "обычно", "чуть тише", "чуть тише", "громче", "громче",
    "быстро", "быстро", "медленно, с растяжкой", "с вопросом: «Джарвис?»",
    "как зовёте с другого конца комнаты", "отвернувшись от ноутбука",
    "обычно", "обычно", "шёпотом-полушёпотом", "устало", "весело",
    "«Эй, Джарвис»", "«Эй, Джарвис»", "обычно", "обычно", "чуть в сторону",
    "с расстояния 2 метра", "обычно",
]
NEG_PROMPTS = [
    "любую фразу БЕЗ имени, например «какая сегодня погода»",
    "«сервис»", "«жалюзи»", "«Дарвин»", "«включи музыку»",
    "посчитайте вслух от одного до пяти", "«Алиса, привет»", "любое предложение о своих делах",
]


def _beep(freq=880, ms=120):
    t = np.linspace(0, ms / 1000, int(SR * ms / 1000), False)
    tone = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sd.play(tone, SR)
    sd.wait()


def _record(seconds: float) -> np.ndarray:
    audio = sd.rec(int(SR * seconds), samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    return audio.reshape(-1)


def _session(prompts, folder: Path, what: str):
    folder.mkdir(parents=True, exist_ok=True)
    existing = len(list(folder.glob("*.npy")))
    print(f"\n=== {what}: {len(prompts)} записей, каждая {CLIP_SEC:.0f} с ===")
    for i, hint in enumerate(prompts, 1):
        print(f"[{i}/{len(prompts)}] {hint} — после сигнала", flush=True)
        time.sleep(1.2)
        _beep()
        clip = _record(CLIP_SEC)
        rms = float(np.sqrt(np.mean(clip.astype(np.float32) ** 2)))
        np.save(folder / f"user_{existing + i:03d}.npy", clip)
        print(f"      записано (уровень {rms:.0f}){'  — ТИХО, ближе к микрофону' if rms < 150 else ''}", flush=True)
        time.sleep(0.4)
    _beep(660, 200)


NEG_MORE = [
    "«какая сегодня погода»", "«включи фильм гладиатор»", "«сделай громче»", "«поставь на паузу»",
    "«который час»", "«закрой видео»", "«сервис»", "«жалюзи»", "«Дарвин»", "«джаз»",
    "«Ярослав»", "«Марвин»", "«жарко сегодня»", "«привет, как дела»", "«спасибо, хорошо»",
    "«Алиса, включи свет»", "«Сири, позвони маме»", "«я завтра поеду в город»",
    "«давай посмотрим что-нибудь»", "«не знаю, посмотрим»", "«ладно, потом»",
    "«где мои ключи»", "«что там по новостям»", "«окей, гугл»", "«Джон, ты здесь»",
    "«джем и джинсы»", "«Марвис»", "«Дарвис»", "«Джарлин»", "«сервиз»",
]


def main():
    print("Микрофон:", sd.query_devices(kind="input")["name"])
    if "--neg-only" in sys.argv:
        print("Только фразы БЕЗ слова «Джарвис». Говорите после сигнала.")
        time.sleep(2)
        _session(NEG_MORE, OUT / "user_neg", "Без имени (дополнительно)")
        print("\nГотово. Записи в", OUT)
        return
    print("Говорите «Джарвис» после каждого сигнала так, как подсказано.")
    time.sleep(2)
    _session(POS_PROMPTS, OUT / "user_pos", "Слово «Джарвис»")
    print("\nТеперь — фразы БЕЗ слова «Джарвис» (негативы).")
    time.sleep(2)
    _session(NEG_PROMPTS, OUT / "user_neg", "Без имени")
    print("\nГотово. Записи в", OUT)


if __name__ == "__main__":
    main()
