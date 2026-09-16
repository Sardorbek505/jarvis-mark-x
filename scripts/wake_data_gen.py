"""Синтетический датасет для обучения wake-word «Джарвис».

Позитивы: Silero TTS (5 русских голосов) × варианты фразы × темп/высота/шум.
Негативы: те же голоса на похожих и случайных словах, шум, музыка.
Всё — 16 кГц int16 моно, клипы по 1.5 с, слово в конце окна (так модель
срабатывает в момент, когда слово договорено).

Запуск: python scripts/wake_data_gen.py [--out data/wake] [--positives 1500] [--negatives 3000]
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # не подменять объект: импорт из другого скрипта закрывал его
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("wake-data")

SR = 16000
CLIP_SEC = 1.5
CLIP = int(SR * CLIP_SEC)

POSITIVE_PHRASES = ["Джарвис", "Джарвис!", "Джарвис,", "Эй, Джарвис", "Джарвис.", "джарвис"]

# Похожие по звучанию и просто частые слова — чтобы модель не ловила «почти»
NEGATIVE_WORDS = [
    "сервис", "жалюзи", "Дарвин", "джаз", "Ярвис", "Марвин", "Джон", "жарко", "парус",
    "Джордж", "дарить", "джем", "Джек", "Джереми", "Джаред", "жарить", "заварка",
    "царь", "Барвиха", "Ларвис", "нарвись", "джинсы", "Джонни", "Джессика", "жар",
    "привет", "спасибо", "пожалуйста", "хорошо", "давай", "громче", "тише", "пауза",
    "включи", "выключи", "музыка", "фильм", "погода", "который час", "окей", "слушай",
    "да", "нет", "ладно", "понятно", "конечно", "секунду", "минуту", "смотри",
    "телефон", "компьютер", "экран", "новости", "завтра", "сегодня", "вечером",
    "как дела", "что такое", "расскажи", "покажи", "найди", "открой", "закрой",
    "сколько", "почему", "где", "когда", "кто", "зачем", "куда", "откуда",
    "Алиса", "Сири", "Маруся", "Гугл", "Салют", "Олег", "Джарвин", "Джарлис",
]


def _tts_model():
    import torch
    torch.set_num_threads(4)
    model, _ = torch.hub.load(repo_or_dir="snakers4/silero-models", model="silero_tts",
                              language="ru", speaker="v4_ru", trust_repo=True)
    return model


def _synth(model, text: str, speaker: str) -> np.ndarray:
    """Silero отдаёт 8/24/48 кГц — берём 24 и приводим к 16."""
    import torch
    with torch.no_grad():
        audio = model.apply_tts(text=text, speaker=speaker, sample_rate=24000)
    x = audio.numpy().astype(np.float32)
    n = int(len(x) * SR / 24000)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _resample(x: np.ndarray, factor: float) -> np.ndarray:
    """Темп и высота вместе (как у разных людей): factor>1 — быстрее и выше."""
    n = int(len(x) / factor)
    idx = np.linspace(0, len(x) - 1, n)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


def _noise(n: int, kind: str, rng: np.random.Generator) -> np.ndarray:
    white = rng.standard_normal(n).astype(np.float32)
    if kind == "white":
        return white
    # розовый/коричневый — через кумулятивный фильтр
    pink = np.cumsum(white)
    pink -= np.linspace(pink[0], pink[-1], n)
    pink /= (np.abs(pink).max() + 1e-6)
    return pink.astype(np.float32)


def _augment(word: np.ndarray, rng: np.random.Generator, noise_bank: list[np.ndarray]) -> np.ndarray:
    x = word.copy()
    x = _resample(x, rng.uniform(0.85, 1.18))
    x *= rng.uniform(0.25, 1.0)
    # слово — в конец окна, перед ним тишина/фон
    if len(x) > CLIP:
        x = x[-CLIP:]
    out = np.zeros(CLIP, dtype=np.float32)
    tail_gap = int(rng.uniform(0.0, 0.12) * SR)
    end = CLIP - tail_gap
    start = max(0, end - len(x))
    out[start:end] = x[: end - start]
    # фон: шум или кусок другого клипа
    snr_db = rng.uniform(5, 30)
    if noise_bank and rng.random() < 0.6:
        src = noise_bank[rng.integers(len(noise_bank))]
        if len(src) >= CLIP:
            off = rng.integers(0, len(src) - CLIP + 1)
            bg = src[off:off + CLIP]
        else:
            bg = np.resize(src, CLIP)
    else:
        bg = _noise(CLIP, "pink" if rng.random() < 0.5 else "white", rng)
    sig_p = np.mean(out ** 2) + 1e-9
    bg_p = np.mean(bg ** 2) + 1e-9
    bg = bg * np.sqrt(sig_p / bg_p / (10 ** (snr_db / 10)))
    out = out + bg
    # простая реверберация иногда
    if rng.random() < 0.3:
        delay = int(rng.uniform(0.02, 0.06) * SR)
        echo = np.zeros_like(out)
        echo[delay:] = out[:-delay] * rng.uniform(0.2, 0.45)
        out = out + echo
    peak = np.abs(out).max() + 1e-6
    return (out / peak * rng.uniform(0.3, 0.95)).astype(np.float32)


def _to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(x * 32767, -32768, 32767).astype(np.int16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/wake")
    ap.add_argument("--positives", type=int, default=1500)
    ap.add_argument("--negatives", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    out = Path(args.out)
    (out / "pos").mkdir(parents=True, exist_ok=True)
    (out / "neg").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    random.seed(args.seed)

    log.info("Загружаю Silero TTS…")
    model = _tts_model()
    speakers = ["aidar", "baya", "kseniya", "xenia", "eugene"]

    # Базовые записи слова и негативов — синтезируем один раз, дальше аугментации
    log.info("Синтез базовых клипов…")
    pos_base = []
    for sp in speakers:
        for phrase in POSITIVE_PHRASES:
            try:
                pos_base.append(_synth(model, phrase, sp))
            except Exception as e:
                log.warning("TTS %s/%s: %s", sp, phrase, e)
    neg_base = []
    for sp in speakers:
        for w in NEGATIVE_WORDS:
            try:
                neg_base.append(_synth(model, w, sp))
            except Exception as e:
                log.warning("TTS %s/%s: %s", sp, w, e)
    log.info("База: %d позитивов, %d негативов", len(pos_base), len(neg_base))

    # Банк фона: негативная речь (для «бормотания» на фоне)
    noise_bank = [n for n in neg_base if len(n) > SR // 2]

    log.info("Аугментация позитивов…")
    for i in range(args.positives):
        word = pos_base[rng.integers(len(pos_base))]
        clip = _augment(word, rng, noise_bank)
        np.save(out / "pos" / f"pos_{i:05d}.npy", _to_int16(clip))
    log.info("Аугментация негативов…")
    for i in range(args.negatives):
        r = rng.random()
        if r < 0.7:
            word = neg_base[rng.integers(len(neg_base))]
            clip = _augment(word, rng, noise_bank)
        elif r < 0.85:
            clip = _noise(CLIP, "pink" if rng.random() < 0.5 else "white", rng) * rng.uniform(0.05, 0.6)
        else:
            # два негативных слова подряд — «речь»
            a = neg_base[rng.integers(len(neg_base))]
            b = neg_base[rng.integers(len(neg_base))]
            clip = _augment(np.concatenate([a, b]), rng, noise_bank)
        np.save(out / "neg" / f"neg_{i:05d}.npy", _to_int16(clip))
    log.info("Готово: %s", out)


if __name__ == "__main__":
    main()
