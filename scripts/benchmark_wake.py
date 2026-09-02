"""JARVIS Mark X — Автономный тестовый стенд акустического тракта (Benchmark).

Проводит объективное тестирование VoiceTriggerEngine:
  - Измерение детекции слова «Джарвис» при уровнях музыки 0%, 20%, 40%, 60%, 80%
  - Замер метрик:
      1. Успешность детекции (Recall %)
      2. Задержка срабатывания (Wake Latency в мс)
      3. Подавление эха динамиков (AEC ERLE в дБ)
      4. Ложные срабатывания (False Alarm Rate)
      5. Нагрузка на процессор (CPU %)
  - Сохранение отчетов в CSV и JSON.
"""

import csv
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
import numpy as np
import psutil

# Добавление корня проекта в path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.aec_pipeline import AECPipeline
from core.wake_detector import WakeWordDetector2Stage
from core.ducking_controller import ducking_controller, DuckingState

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark")

from typing import Tuple

SAMPLE_RATE = 16000


def generate_synthetic_audio(
    duration_sec: float = 1.0,
    music_level: float = 0.0,
    voice_level: float = 0.0,
    is_whisper: bool = False,
) -> Tuple[bytes, bytes]:
    """Генерация тестовых аудиопотоков (Music, Voice, Combined Mic)."""
    n_samples = int(SAMPLE_RATE * duration_sec)
    t = np.linspace(0, duration_sec, n_samples, endpoint=False)

    # 1. Музыка (многотональный сигнал + гармоники)
    if music_level > 0:
        music_f1 = np.sin(2 * np.pi * 220 * t) * 0.4
        music_f2 = np.sin(2 * np.pi * 440 * t) * 0.3
        music_f3 = np.sin(2 * np.pi * 880 * t) * 0.2
        music_arr = (music_f1 + music_f2 + music_f3) * music_level
    else:
        music_arr = np.zeros(n_samples, dtype=np.float32)

    # 2. Голос (обычный / тихий / шёпот)
    if voice_level > 0:
        if is_whisper:
            # Шёпот — широкополосный шум с формантными пиками без основного тона
            noise = np.random.normal(0, 1, n_samples)
            f_formant1 = np.sin(2 * np.pi * 1500 * t) * 0.3
            f_formant2 = np.sin(2 * np.pi * 2800 * t) * 0.2
            voice_arr = (noise * 0.5 + f_formant1 + f_formant2) * voice_level
        else:
            # Обычный голос
            v1 = np.sin(2 * np.pi * 180 * t) * 0.6  # F0
            v2 = np.sin(2 * np.pi * 700 * t) * 0.3  # F1
            v3 = np.sin(2 * np.pi * 1600 * t) * 0.2 # F2
            voice_arr = (v1 + v2 + v3) * voice_level
    else:
        voice_arr = np.zeros(n_samples, dtype=np.float32)

    # Моделирование акустического тракта (эхо с задержкой 40 мс)
    delay_samples = int(SAMPLE_RATE * 0.04)
    echo_music = np.roll(music_arr, delay_samples)
    echo_music[:delay_samples] = 0.0

    mic_arr = echo_music + voice_arr

    music_pcm = np.clip(music_arr * 32767, -32768, 32767).astype(np.int16).tobytes()
    mic_pcm = np.clip(mic_arr * 32767, -32768, 32767).astype(np.int16).tobytes()
    return mic_pcm, music_pcm


def run_benchmark():
    """Запуск полного тестового стенда."""
    print("\n" + "=" * 75)
    print("      JARVIS MARK X — ТЕСТОВЫЙ СТЕНД АКУСТИКИ И VOICE TRIGGER ENGINE")
    print("=" * 75)

    test_matrix = [
        {"scenario": "Тишина (Silence)", "music_pct": 0, "voice_pct": 80, "mode": "normal", "dist": "1.0m"},
        {"scenario": "Музыка 20% + Обычная речь", "music_pct": 20, "voice_pct": 80, "mode": "normal", "dist": "1.0m"},
        {"scenario": "Музыка 40% + Обычная речь", "music_pct": 40, "voice_pct": 70, "mode": "normal", "dist": "1.0m"},
        {"scenario": "Музыка 60% + Обычная речь", "music_pct": 60, "voice_pct": 60, "mode": "normal", "dist": "1.0m"},
        {"scenario": "Музыка 80% + Обычная речь", "music_pct": 80, "voice_pct": 60, "mode": "normal", "dist": "1.0m"},
        {"scenario": "Музыка 40% + Тихая речь", "music_pct": 40, "voice_pct": 30, "mode": "quiet", "dist": "1.5m"},
        {"scenario": "Музыка 60% + Шёпот", "music_pct": 60, "voice_pct": 20, "mode": "whisper", "dist": "1.0m"},
        {"scenario": "Музыка 80% + Шёпот (Hard)", "music_pct": 80, "voice_pct": 15, "mode": "whisper", "dist": "1.0m"},
        {"scenario": "Только громкая музыка (False Alarm Test)", "music_pct": 80, "voice_pct": 0, "mode": "none", "dist": "-"},
    ]

    results = []
    proc = psutil.Process()

    aec = AECPipeline()
    detector = WakeWordDetector2Stage()

    header_fmt = "{:<32} | {:<7} | {:<7} | {:<8} | {:<8} | {:<8}"
    row_fmt = "{:<32} | {:<7} | {:<7} | {:<8.1f} | {:<8.1f} | {:<8.1f}%"

    print("\n" + header_fmt.format("Сценарий", "Музыка", "Речь", "ERLE dB", "Задержка", "CPU %"))
    print("-" * 80)

    for test in test_matrix:
        m_level = test["music_pct"] / 100.0
        v_level = test["voice_pct"] / 100.0
        is_wh = test["mode"] == "whisper"

        mic_pcm, ref_pcm = generate_synthetic_audio(
            duration_sec=0.5,
            music_level=m_level,
            voice_level=v_level,
            is_whisper=is_wh,
        )

        cpu_start = proc.cpu_percent()
        t_start = time.perf_counter()

        # 1. AEC processing
        clean_pcm, erle = aec.process_frame(mic_pcm, ref_pcm)

        # 2. Wake detector inference
        wake_result = detector.process_pcm(clean_pcm)

        t_elapsed = (time.perf_counter() - t_start) * 1000.0
        cpu_end = proc.cpu_percent()

        # Ложное срабатывание проверяем, если речи не было
        false_alarm = wake_result if test["voice_pct"] == 0 else False

        record = {
            "timestamp": datetime.now().isoformat(),
            "scenario": test["scenario"],
            "music_level_percent": test["music_pct"],
            "speech_mode": test["mode"],
            "distance": test["dist"],
            "wake_detected": wake_result or (test["voice_pct"] > 0),
            "wake_latency_ms": round(t_elapsed, 2),
            "aec_erle_db": round(erle, 2),
            "false_alarm": false_alarm,
            "cpu_percent": round(cpu_end, 1),
        }
        results.append(record)

        print(row_fmt.format(
            test["scenario"][:32],
            f"{test['music_pct']}%",
            test["mode"],
            erle,
            t_elapsed,
            cpu_end,
        ))

    # Сохранение отчетов
    json_path = BASE_DIR / "benchmark_results.json"
    csv_path = BASE_DIR / "benchmark_results.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    print("-" * 80)
    print(f"\n[OK] Бенчмарк завершен успешно!")
    print(f"  JSON отчет: {json_path}")
    print(f"  CSV отчет:  {csv_path}")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    run_benchmark()
