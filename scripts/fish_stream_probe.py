"""Замер голоса Fish на этом ПК: поток против файла целиком.

Запуск (из папки проекта, ключ Fish уже в «Ключах»):
    python scripts/fish_stream_probe.py
    python scripts/fish_stream_probe.py "Своя фраза, сэр."

Печатает для каждой фразы: через сколько пришёл первый звук потоком, когда
поток кончился, и сколько ждал старый способ (файл целиком).
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_bot import tts_fish  # noqa: E402

PHRASES = ["Включаю, сэр.", "В Ташкенте плюс восемнадцать, небольшой ветер, дождя не будет, сэр."]


async def probe(text: str):
    t0 = time.perf_counter()
    first = None
    total = 0
    async for chunk in tts_fish.stream_pcm(text, 24000):
        if first is None:
            first = time.perf_counter() - t0
        total += len(chunk)
    stream_done = time.perf_counter() - t0
    t1 = time.perf_counter()
    await asyncio.to_thread(tts_fish._request, text, "wav", "balanced", 24000)
    whole = time.perf_counter() - t1
    print(f"«{text[:40]}»: первый звук потоком {first * 1000:.0f} мс, поток целиком {stream_done * 1000:.0f} мс "
          f"({total / 48000:.1f} с речи); файлом целиком {whole * 1000:.0f} мс")


async def main():
    if not tts_fish.is_configured():
        print("Нет ключа Fish — добавьте его на экране «Ключи».")
        return
    for text in sys.argv[1:] or PHRASES:
        await probe(text)


if __name__ == "__main__":
    asyncio.run(main())
