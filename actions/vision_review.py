"""
Действие: визуальный анализ экрана (Vision Mode).
Захватывает скриншот → отправляет в Gemini Vision → возвращает обзор.

Поддерживает rate limiting (защита от спама запросов).
По умолчанию захватывает только активное окно (privacy first).
"""

import json
import time
from pathlib import Path

from vision.screen_capture import (
    capture_active_window,
    capture_full_screen,
    compress_image,
)
from vision.window_detector import detect_active_window
from vision.vision_analyzer import analyze_image

# ─── Пути и настройки ─────────────────────────────────────────────────────────
_BASE = Path(__file__).resolve().parent.parent
_API_CONFIG = _BASE / "config" / "api_keys.json"

# Rate limiting — не чаще 1 запроса в N секунд
_RATE_LIMIT_SEC = 3.0
_last_capture_time = 0.0


def _get_api_key() -> str:
    with open(_API_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def vision_review(parameters: dict, player=None) -> str:
    """
    Главная точка входа для tool 'vision_review'.

    Параметры:
        focus: design | ux | premium | conversion | spacing |
               typography | colors | accessibility | general
        mode:  active_window (по умолчанию) | full_screen
    """
    global _last_capture_time

    # ── Rate limiting ─────────────────────────────────────────────────────────
    now = time.time()
    elapsed = now - _last_capture_time
    if elapsed < _RATE_LIMIT_SEC:
        wait = round(_RATE_LIMIT_SEC - elapsed, 1)
        return f"Подождите {wait} секунд перед следующим анализом, сэр."
    _last_capture_time = now

    # ── Параметры ─────────────────────────────────────────────────────────────
    focus = (parameters.get("focus") or "general").strip().lower()
    mode = (parameters.get("mode") or "active_window").strip().lower()

    if player:
        player.write_log(f"SYS: 👁 Анализ экрана ({focus})...")

    # ── Захват экрана ─────────────────────────────────────────────────────────
    try:
        if mode == "full_screen":
            img = capture_full_screen()
        else:
            img = capture_active_window()
    except Exception as e:
        return f"Не удалось захватить экран: {str(e)[:100]}"

    if img is None:
        return "Не удалось получить изображение экрана, сэр."

    # ── Определение контекста ─────────────────────────────────────────────────
    context, window_title = detect_active_window()

    # ── Сжатие ────────────────────────────────────────────────────────────────
    try:
        image_bytes = compress_image(img)
    except Exception as e:
        return f"Ошибка сжатия изображения: {str(e)[:100]}"

    # ── Анализ через Vision API ───────────────────────────────────────────────
    try:
        api_key = _get_api_key()
        result = analyze_image(
            image_bytes=image_bytes,
            api_key=api_key,
            context=context,
            focus=focus,
            window_title=window_title,
        )

        if player:
            ctx_label = context if context != "unknown" else "экран"
            player.write_log(f"SYS: ✓ Vision: {ctx_label} / {focus}")

        return result

    except FileNotFoundError:
        return "API ключ не настроен. Откройте config/api_keys.json."
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ["api key expired", "api_key_invalid",
                                   "invalid api key"]):
            return "API ключ Gemini истёк. Обновите ключ в config/api_keys.json."
        return f"Ошибка модуля Vision: {str(e)[:100]}"
