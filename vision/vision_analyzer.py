"""
Анализ изображения через Gemini Vision API.
Использует gemini-2.5-flash (мультимодальный, быстрый, недорогой).
"""

from google import genai
from google.genai import types

# Модель для зрения. Live API использует другую модель — это отдельный вызов.
VISION_MODEL = "gemini-2.5-flash"

# ─── Контекст-зависимые вступления ────────────────────────────────────────────
CONTEXT_INTROS = {
    "figma":        "Перед тобой макет в Figma. Оцени его как старший продакт-дизайнер.",
    "browser":      "Перед тобой веб-страница в браузере. Оцени UX и конверсионный потенциал.",
    "code":         "Перед тобой редактор кода. Прокомментируй организацию и читаемость.",
    "presentation": "Перед тобой слайд презентации. Оцени визуальный вес и иерархию.",
    "design":       "Перед тобой дизайн-проект. Дай экспертную обратную связь.",
    "video":        "Перед тобой видео-проект. Оцени композицию и таймлайн.",
    "spreadsheet":  "Перед тобой таблица. Оцени читаемость и структуру данных.",
    "unknown":      "Перед тобой экран пользователя. Дай профессиональный комментарий.",
}

# ─── Фокус анализа ────────────────────────────────────────────────────────────
FOCUS_INSTRUCTIONS = {
    "design":        "Оцени: spacing, alignment, иерархия, типографика, контраст, баланс, whitespace.",
    "ux":            "Оцени: ясность навигации, видимость CTA, фрикции, читаемость, мобильная адаптация.",
    "premium":       "Оцени уровень премиальности: современность, доверие, enterprise vs стартап-качество.",
    "conversion":    "Оцени конверсионный потенциал: видимость CTA, доверие, скорость к ценности.",
    "spacing":       "Сфокусируйся ТОЛЬКО на отступах: padding, margin, gap, breathing room.",
    "typography":    "Сфокусируйся ТОЛЬКО на типографике: иерархия, размеры, line-height, шрифты.",
    "colors":        "Сфокусируйся ТОЛЬКО на цветах: палитра, контраст, акценты, доступность.",
    "accessibility": "Сфокусируйся на доступности: контраст, размеры, читаемость, фокус.",
    "general":       "Дай общую профессиональную оценку — что хорошо, что улучшить.",
}


def _build_prompt(context: str, focus: str, window_title: str) -> str:
    """Собирает умный промпт под контекст и фокус анализа."""
    intro = CONTEXT_INTROS.get(context, CONTEXT_INTROS["unknown"])
    instruction = FOCUS_INSTRUCTIONS.get(focus, FOCUS_INSTRUCTIONS["general"])

    title_line = f"Контекст окна: {window_title}\n" if window_title else ""

    return (
        "Ты ДЖАРВИС — премиум дизайн-консультант уровня senior product designer. "
        f"{intro}\n\n"
        f"Задача: {instruction}\n\n"
        "Правила ответа (СТРОГО):\n"
        "- Отвечай ТОЛЬКО на русском языке\n"
        "- Обращайся 'сэр'\n"
        "- Максимум 2-3 коротких предложения\n"
        "- Конкретные рекомендации, без воды\n"
        "- Уверенный, профессиональный тон\n"
        "- Сначала сильная сторона, потом главное улучшение\n"
        "- НЕ описывай что видишь — давай ОЦЕНКУ и СОВЕТ\n"
        "- Никаких 'возможно', 'может быть' — говори твёрдо\n\n"
        f"{title_line}"
    )


def analyze_image(
    image_bytes: bytes,
    api_key: str,
    context: str = "unknown",
    focus: str = "general",
    window_title: str = "",
) -> str:
    """
    Отправляет изображение в Gemini Vision и возвращает текст-обзор.

    Бросает исключение при проблемах с API ключом — для graceful shutdown в main.
    Иначе возвращает строку с ошибкой или результатом.
    """
    try:
        client = genai.Client(api_key=api_key)
        prompt = _build_prompt(context, focus, window_title)

        response = client.models.generate_content(
            model=VISION_MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=200,
            ),
        )

        text = (response.text or "").strip()
        return text if text else "Не удалось получить анализ, сэр."

    except Exception as e:
        err = str(e).lower()
        # Ошибки ключа — пробрасываем наверх для остановки реконнекта
        if any(k in err for k in ["api key expired", "api_key_invalid",
                                   "invalid api key", "api key not found"]):
            raise
        # Прочие ошибки — мягкое сообщение
        return f"Сбой модуля Vision: {str(e)[:100]}"
