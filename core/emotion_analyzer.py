"""
Анализатор эмоций пользователя
Определяет эмоциональное состояние по тексту и контексту
"""

from typing import Dict, Optional

# Выключатель анализа эмоций. False = Джарвис не угадывает настроение и не
# комментирует его; всё, что зависит от эмоций (инициативные реплики,
# адаптация напоминаний), работает в нейтральном режиме. Чтобы вернуть —
# поставь True. Отключено по решению владельца (2026-07): мешало, не помогало.
_EMOTION_ENABLED = False

_NEUTRAL = {"emotion": "neutral", "confidence": 0.0, "detected_emotions": {}, "suggested_actions": []}


class EmotionAnalyzer:
    """Анализирует эмоции пользователя для инициативных действий"""

    # Ключевые слова для эмоций
    EMOTION_KEYWORDS = {
        "sad": [
            "грустно", "грусть", "печаль", "плохо", "ужасно", "тоска",
            "депрессия", "скучно", "одиноко", "один", "грустный", "печальный",
            "разочарован", "разочарование", "устал", "усталость", "выдохся",
            "не хочу", "нет сил", "ничего не хочу", "как всё плохо",
            "мне грустно", "мне плохо", "мне тоскливо"
        ],
        "happy": [
            "рад", "счастлив", "счастье", "отлично", "супер", "круто",
            "классно", "замечательно", "прекрасно", "восторг", "восторжен",
            "хорошо", "хорош", "душевно", "приятно", "удовлетворен",
            "мне рад", "мне хорошо", "мне приятно", "весело", "смешно"
        ],
        "stressed": [
            "стресс", "нерв", "нервный", "нервничаю", "волнуюсь", "волнение",
            "тревога", "беспокойство", "паника", "сумбур", "хаос",
            "много дел", "не успеваю", "дедлайн", "давит", "давление",
            "устал от работы", "завал", "не успеваю ничего"
        ],
        "bored": [
            "скучно", "скука", "не знаю что делать", "чем заняться",
            "нет дел", "ничего не делаю", "пусто", "ничего интересного",
            "заняться нечем", "делать нечего"
        ],
        "tired": [
            "устал", "усталость", "выдохся", "нет сил", "измотан",
            "сонный", "хочу спать", "надо спать", "пора спать",
            "сил нет", "истощен", "обессилен"
        ],
        "angry": [
            "зл", "злой", "злюсь", "разозлился", "разозлён", "злость",
            "бесит", "раздражает", "раздражен", "раздражён", "ненавижу",
            "не выношу", "не терплю", "злюсь на", "злой на"
        ]
    }

    # Инициативные действия по эмоциям
    EMOTION_ACTIONS = {
        "sad": [
            "Включить что-то весёлое (КВН, комедии, stand-up)",
            "Предложить посмотреть любимый фильм",
            "Поставить любимую музыку для поднятия настроения",
            "Предложить прогуляться или сделать что-то активное",
            "Рассказать что-то позитивное"
        ],
        "happy": [
            "Сохранить этот момент в память",
            "Предложить что-то для продолжения настроения",
            "Поделиться радостью"
        ],
        "stressed": [
            "Предложить сделать перерыв",
            "Включить расслабляющую музыку",
            "Предложить дыхательные упражнения",
            "Помочь расставить приоритеты дел"
        ],
        "bored": [
            "Предложить посмотреть фильм",
            "Включить музыку",
            "Предложить поиграть",
            "Предложить что-то почитать",
            "Предложить посмотреть видео на YouTube"
        ],
        "tired": [
            "Предложить отдохнуть",
            "Включить спокойную музыку",
            "Предложить лечь спать",
            "Отключить лишние уведомления"
        ],
        "angry": [
            "Предложить успокоиться",
            "Включить спокойную музыку",
            "Предложить сделать перерыв",
            "Предложить физическую активность"
        ]
    }

    @classmethod
    def analyze(cls, text: str) -> Dict[str, any]:
        """
        Анализирует эмоции в тексте

        Args:
            text: Текст пользователя

        Returns:
            Dict с полями:
            - emotion: основная эмоция (sad, happy, stressed, bored, tired, angry, neutral)
            - confidence: уверенность (0.0-1.0)
            - detected_emotions: список всех найденных эмоций с уверенностью
            - suggested_actions: список инициативных действий
        """
        if not _EMOTION_ENABLED:
            return dict(_NEUTRAL)

        text_lower = text.lower()

        detected = {}
        for emotion, keywords in cls.EMOTION_KEYWORDS.items():
            count = sum(1 for keyword in keywords if keyword in text_lower)
            if count > 0:
                detected[emotion] = count

        if not detected:
            return {
                "emotion": "neutral",
                "confidence": 0.0,
                "detected_emotions": {},
                "suggested_actions": []
            }

        # Находим основную эмоцию (с максимальным количеством совпадений)
        main_emotion = max(detected, key=detected.get)
        max_count = detected[main_emotion]
        confidence = min(0.9, max_count * 0.3)  # Чем больше совпадений, тем выше уверенность

        suggested = cls.EMOTION_ACTIONS.get(main_emotion, [])

        return {
            "emotion": main_emotion,
            "confidence": confidence,
            "detected_emotions": detected,
            "suggested_actions": suggested
        }

    @classmethod
    def detect_initiative_opportunity(cls, text: str, user_profile: Dict) -> Optional[str]:
        """
        Определяет, стоит ли проявить инициативу

        Args:
            text: Текст пользователя
            user_profile: Профиль пользователя с предпочтениями

        Returns:
            Строка с предложением инициативного действия или None
        """
        emotion_result = cls.analyze(text)
        emotion = emotion_result["emotion"]
        confidence = emotion_result["confidence"]

        # Проявляем инициативу только если уверенность > 0.5
        if confidence < 0.5:
            return None

        # Получаем предпочтения пользователя
        preferences = user_profile.get("preferences", {})
        favorite_comedy = preferences.get("favorite_comedy")
        favorite_music = preferences.get("favorite_music")
        favorite_movie = preferences.get("favorite_movie")

        # Генерируем инициативное действие
        # Джарвис не ставит диагноз вслух: «я вижу, вам грустно» — не его
        # реплика. Он предлагает конкретное действие и оставляет выбор.
        # Тон сверяется с core/prompt.txt.
        if emotion == "sad":
            if favorite_comedy:
                return f"Могу поставить {favorite_comedy}, сэр. Помогало."
            else:
                return "Вечер выдался так себе, сэр. Включить что-нибудь лёгкое?"

        elif emotion == "bored":
            if favorite_movie:
                return f"{favorite_movie} всё ещё не досмотрен, сэр."
            else:
                return "Тишина, сэр. Фильм или музыка?"

        elif emotion == "stressed":
            if favorite_music:
                return f"Могу включить {favorite_music}, сэр, и отложить остальное."
            return "Дел много, сэр. Разобрать по важности?"

        elif emotion == "tired":
            if favorite_music:
                return f"День был длинный, сэр. Поставить {favorite_music}?"
            return "Час поздний, сэр. Погасить всё?"

        return None


# Тестирование
if __name__ == "__main__":
    test_texts = [
        "Мне грустно",
        "Мне так скучно, не знаю что делать",
        "У меня стресс, не успеваю ничего",
        "Я устал, нет сил",
        "Всё отлично!",
        "Открой Chrome"
    ]

    for text in test_texts:
        result = EmotionAnalyzer.analyze(text)
        print(f"\nТекст: {text}")
        print(f"Эмоция: {result['emotion']} (уверенность: {result['confidence']:.2f})")
        print(f"Действия: {result['suggested_actions']}")
