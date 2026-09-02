"""JARVIS Mark X — Автономный тестовый стенд акустического тракта (Benchmark).

Гоняет связку AEC -> KWS на синтетических сценариях «музыка + ключевое слово»
и меряет:
  1. Реальную детекцию слова (Recall)
  2. Задержку срабатывания от начала слова до подтверждения (мс)
  3. Подавление эха динамиков (AEC ERLE, дБ)
  4. Ложные срабатывания на одной музыке (False Alarm)
  5. Стоимость обработки в процентах от реального времени

Про честность метрик — важное:

  Прошлая версия стенда писала `wake_detected = wake_result or voice_pct > 0`,
  то есть проставляла успех везде, где в сценарии вообще была «речь», не глядя
  на ответ детектора. Плюс «речью» служили три синусоиды 180/700/1600 Гц —
  на таком сигнале модель hey_jarvis выдаёт 0.000 и не сработает никогда.
  В отчёте стояло 100% Recall при фактических 0 из 8.

  Теперь ключевое слово синтезируется офлайновым системным TTS (SAPI/pyttsx3)
  английским голосом — модель обучена на английском «hey jarvis» — и сигнал
  прогоняется чанками по 1280 семплов, как в проде. Если синтез недоступен,
  строка Recall помечается как «не измерено»: выдумывать её стенд не будет.
"""

import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

# Добавление корня проекта в path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.aec_pipeline import AECPipeline          # noqa: E402
from core.wake_detector import WakeWordDetector2Stage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark")

# Стенд снимает задержку без cooldown, поэтому детектор подтверждает слово
# на каждом чанке подряд. В отчёте это шум — глушим его до предупреждений.
logging.getLogger("jarvis-kws").setLevel(logging.WARNING)

SAMPLE_RATE = 16000
CHUNK = 1280                      # 80 мс — рабочий чанк openwakeword
ECHO_DELAY_SEC = 0.04             # задержка динамик -> микрофон
ECHO_GAIN = 0.5                   # затухание эха по пути до микрофона
LEAD_IN_SEC = 1.0                 # тишина перед словом: фильтру нужно сойтись

WAKE_CACHE = BASE_DIR / "logs" / "benchmark" / "wake_hey_jarvis.wav"


# ─── Тестовые сигналы ─────────────────────────────────────────────────────────
def synth_wake_word() -> Optional[np.ndarray]:
    """Реальное «Hey Jarvis» офлайновым системным TTS, 16 кГц mono int16.

    Русские голоса модель не пробивают (замерено 0.19 против 0.999 у английских),
    поэтому берём именно английский голос.
    """
    if WAKE_CACHE.exists():
        try:
            import soundfile as sf
            audio, sr = sf.read(str(WAKE_CACHE), dtype="float32", always_2d=True)
            return _to_16k_int16(audio.mean(axis=1), sr)
        except Exception as exc:
            logger.debug("Кэш ключевого слова не прочитался: %s", exc)

    try:
        import pyttsx3
        import soundfile as sf
    except Exception as exc:
        logger.warning("Синтез ключевого слова недоступен (%s)", exc)
        return None

    try:
        engine = pyttsx3.init()
        english = [v for v in engine.getProperty("voices")
                   if "english" in (v.name or "").lower()]
        if not english:
            logger.warning("Английский системный голос не найден — Recall не измерить")
            return None

        engine.setProperty("voice", english[0].id)
        engine.setProperty("rate", 160)
        WAKE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        engine.save_to_file("Hey Jarvis", str(WAKE_CACHE))
        engine.runAndWait()

        audio, sr = sf.read(str(WAKE_CACHE), dtype="float32", always_2d=True)
        return _to_16k_int16(audio.mean(axis=1), sr)
    except Exception as exc:
        logger.warning("Синтез ключевого слова не удался: %s", exc)
        return None


def _to_16k_int16(mono: np.ndarray, sr: int) -> np.ndarray:
    if sr != SAMPLE_RATE:
        from math import gcd
        g = gcd(int(sr), SAMPLE_RATE)
        try:
            from scipy.signal import resample_poly
            mono = resample_poly(mono, SAMPLE_RATE // g, int(sr) // g)
        except Exception:
            idx = np.linspace(0, len(mono) - 1, int(len(mono) * SAMPLE_RATE / sr))
            mono = np.interp(idx, np.arange(len(mono)), mono)
    return np.clip(mono * 32767, -32768, 32767).astype(np.int16)


def synth_music(n_samples: int, level: float, seed: int = 7) -> np.ndarray:
    """Программный материал: гармоники + широкополосная подложка + огибающая.

    Три чистые синусоиды, как раньше, — нереалистично лёгкая задача для AEC:
    тон вычитается почти идеально при любом выравнивании. Реальная музыка
    широкополосна, поэтому шумовая составляющая здесь обязательна.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples) / SAMPLE_RATE

    tonal = (np.sin(2 * np.pi * 220 * t) * 0.40
             + np.sin(2 * np.pi * 440 * t) * 0.30
             + np.sin(2 * np.pi * 880 * t) * 0.18)
    broadband = rng.normal(0, 0.35, n_samples)
    envelope = 0.75 + 0.25 * np.sin(2 * np.pi * 1.7 * t)     # «дыхание» трека

    mix = (0.5 * tonal + 0.5 * broadband) * envelope * level
    return np.clip(mix * 32767, -32768, 32767).astype(np.int16)


def build_scene(wake: Optional[np.ndarray], music_level: float,
                wake_gain: float) -> Tuple[np.ndarray, np.ndarray, int]:
    """Собирает поток микрофона и опорный поток колонок.

    Returns: (mic_int16, ref_int16, индекс семпла, где начинается слово или -1)
    """
    lead = int(SAMPLE_RATE * LEAD_IN_SEC)
    wake_len = len(wake) if wake is not None else 0
    total = lead + wake_len + SAMPLE_RATE          # хвост в 1 с

    ref = synth_music(total, music_level) if music_level > 0 else np.zeros(total, np.int16)

    # Эхо: та же музыка, но позже и тише — так её слышит микрофон.
    delay = int(SAMPLE_RATE * ECHO_DELAY_SEC)
    echo = np.zeros(total, np.float32)
    if delay < total:
        echo[delay:] = ref[:total - delay].astype(np.float32) * ECHO_GAIN

    mic = echo.copy()
    wake_at = -1
    if wake is not None and wake_gain > 0:
        wake_at = lead
        mic[lead:lead + wake_len] += wake.astype(np.float32) * wake_gain

    return (np.clip(mic, -32768, 32767).astype(np.int16), ref, wake_at)


# ─── Прогон одного сценария ───────────────────────────────────────────────────
def run_scene(mic: np.ndarray, ref: np.ndarray, wake_at: int) -> dict:
    """Гонит поток чанками через AEC и детектор, как в проде."""
    aec = AECPipeline()
    detector = WakeWordDetector2Stage(cooldown_sec=0.0)

    erle_values: List[float] = []
    detected_at = -1
    cpu_start = time.process_time()

    for start in range(0, len(mic) - CHUNK + 1, CHUNK):
        stop = start + CHUNK
        clean, erle = aec.process_frame(mic[start:stop].tobytes(), ref[start:stop].tobytes())
        if erle > 0.0:
            erle_values.append(erle)
        if detector.process_pcm(clean) and detected_at < 0:
            detected_at = stop            # слово подтверждено к концу этого чанка

    cpu_sec = time.process_time() - cpu_start
    audio_sec = len(mic) / SAMPLE_RATE

    latency_ms = None
    if detected_at >= 0 and wake_at >= 0:
        latency_ms = round((detected_at - wake_at) / SAMPLE_RATE * 1000.0, 1)

    return {
        "detected": detected_at >= 0,
        "latency_ms": latency_ms,
        "erle_db": round(float(np.mean(erle_values)), 2) if erle_values else 0.0,
        "realtime_percent": round(cpu_sec / audio_sec * 100.0, 1),
    }


def run_benchmark():
    """Запуск полного тестового стенда."""
    print("\n" + "=" * 78)
    print("      JARVIS MARK X — ТЕСТОВЫЙ СТЕНД АКУСТИКИ И VOICE TRIGGER ENGINE")
    print("=" * 78)

    wake = synth_wake_word()
    if wake is None:
        print("\n[!] Ключевое слово синтезировать нечем — Recall НЕ ИЗМЕРЯЕТСЯ.")
        print("    Ставится 'n/a'. Метрики AEC и стоимости обработки остаются валидными.")
    else:
        print("\n[i] Ключевое слово: реальный синтез «Hey Jarvis», %.2f с" % (len(wake) / SAMPLE_RATE))

    test_matrix = [
        {"scenario": "Тишина (Silence)",                     "music": 0.0,  "wake": 1.0,  "mode": "normal"},
        {"scenario": "Музыка 20% + Обычная речь",            "music": 0.20, "wake": 1.0,  "mode": "normal"},
        {"scenario": "Музыка 40% + Обычная речь",            "music": 0.40, "wake": 1.0,  "mode": "normal"},
        {"scenario": "Музыка 60% + Обычная речь",            "music": 0.60, "wake": 1.0,  "mode": "normal"},
        {"scenario": "Музыка 80% + Обычная речь",            "music": 0.80, "wake": 1.0,  "mode": "normal"},
        {"scenario": "Музыка 40% + Тихая речь",              "music": 0.40, "wake": 0.35, "mode": "quiet"},
        {"scenario": "Музыка 60% + Шёпот",                   "music": 0.60, "wake": 0.20, "mode": "whisper"},
        {"scenario": "Музыка 80% + Шёпот (Hard)",            "music": 0.80, "wake": 0.15, "mode": "whisper"},
        {"scenario": "Только громкая музыка (False Alarm)",  "music": 0.80, "wake": 0.0,  "mode": "none"},
    ]

    results = []
    header = "{:<36} | {:<7} | {:<8} | {:<9} | {:<8} | {:<7}"
    print("\n" + header.format("Сценарий", "Музыка", "Детект", "Задержка", "ERLE dB", "CPU %"))
    print("-" * 92)

    for test in test_matrix:
        expects_wake = test["wake"] > 0
        scene_wake = wake if expects_wake else None
        mic, ref, wake_at = build_scene(scene_wake, test["music"], test["wake"])
        outcome = run_scene(mic, ref, wake_at)

        if wake is None and expects_wake:
            detected = None                       # нечем проверять — так и пишем
            detected_txt = "n/a"
        else:
            detected = outcome["detected"]
            detected_txt = "ДА" if detected else "НЕТ"

        false_alarm = bool(outcome["detected"]) if not expects_wake else False

        results.append({
            "timestamp": datetime.now().isoformat(),
            "scenario": test["scenario"],
            "music_level_percent": int(test["music"] * 100),
            "speech_mode": test["mode"],
            "wake_detected": detected,
            "wake_latency_ms": outcome["latency_ms"],
            "aec_erle_db": outcome["erle_db"],
            "false_alarm": false_alarm,
            "realtime_percent": outcome["realtime_percent"],
        })

        print(header.format(
            test["scenario"][:36],
            f"{int(test['music'] * 100)}%",
            detected_txt,
            "—" if outcome["latency_ms"] is None else f"{outcome['latency_ms']:.0f} мс",
            f"{outcome['erle_db']:.1f}",
            f"{outcome['realtime_percent']:.1f}%",
        ))

    print("-" * 92)
    _print_summary(results, wake is not None)

    json_path = BASE_DIR / "benchmark_results.json"
    csv_path = BASE_DIR / "benchmark_results.csv"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    print(f"\n  JSON отчет: {json_path}")
    print(f"  CSV отчет:  {csv_path}")
    print("=" * 78 + "\n")


def _print_summary(results: List[dict], recall_measured: bool):
    wake_rows = [r for r in results if r["speech_mode"] != "none"]
    alarm_rows = [r for r in results if r["speech_mode"] == "none"]

    if recall_measured:
        hits = sum(1 for r in wake_rows if r["wake_detected"])
        print("  Recall:        %d / %d (%.0f%%)" % (hits, len(wake_rows), 100.0 * hits / max(1, len(wake_rows))))
        lat = [r["wake_latency_ms"] for r in wake_rows if r["wake_latency_ms"] is not None]
        if lat:
            print("  Задержка:      медиана %.0f мс, максимум %.0f мс" % (float(np.median(lat)), max(lat)))
    else:
        print("  Recall:        не измерен (нет офлайнового синтеза ключевого слова)")

    print("  Ложные:        %d / %d" % (sum(1 for r in alarm_rows if r["false_alarm"]), len(alarm_rows)))
    erle = [r["aec_erle_db"] for r in results if r["aec_erle_db"] > 0]
    if erle:
        print("  AEC ERLE:      медиана %.1f дБ" % float(np.median(erle)))
    print("  Обработка:     %.1f%% реального времени (пик)"
          % max(r["realtime_percent"] for r in results))


if __name__ == "__main__":
    run_benchmark()
