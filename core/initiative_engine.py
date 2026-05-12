"""
Движок инициативности
Принимает решения о том, когда проявлять инициативу и какие действия предпринимать
"""

import random
from typing import Dict, Optional, List
from datetime import datetime


class InitiativeEngine:
    """
    Движок инициативности для ДЖАРВИС
    Решает когда проявлять инициативу и что предложить
    """

    # Пороги уверенности для инициативы
    CONFIDENCE_THRESHOLD = 0.5

    # Вероятность инициативы (чтобы не надоедать)
    INITIATIVE_PROBABILITY = 0.3  # 30% шанс проявить инициативу при возможности

    # Минимальное количество взаимодействий перед инициативой
    MIN_INTERACTIONS = 3

    def __init__(self):
        self.initiative_count = 0
        self.last_initiative_time = None

    def should_show_initiative(
        self,
        emotion_result: Dict,
        user_profile: Dict,
        current_mode: str = "normal"
    ) -> Optional[str]:
        """
        Решает, стоит ли проявлять инициативу

        Args:
            emotion_result: Результат анализа эмоций
            user_profile: Профиль пользователя
            current_mode: Текущий режим

        Returns:
            Строка с инициативным предложением или None
        """
        # Не проявляем инициативу в режимах работы/учебы (чтобы не отвлекать)
        if current_mode in ["work", "study"]:
            return None

        # Проверяем уверенность эмоции
        confidence = emotion_result.get("confidence", 0.0)
        if confidence < self.CONFIDENCE_THRESHOLD:
            return None

        # Проверяем количество взаимодействий
        context = user_profile.get("context", {})
        interaction_count = context.get("interaction_count", 0)
        if interaction_count < self.MIN_INTERACTIONS:
            return None

        # Проверяем вероятность (чтобы не надоедать)
        if random.random() > self.INITIATIVE_PROBABILITY:
            return None

        # Проверяем время с последней инициативы (минимум 10 минут)
        if self.last_initiative_time:
            last_time = datetime.fromisoformat(self.last_initiative_time)
            if (datetime.now() - last_time).seconds < 600:
                return None

        # Генерируем инициативное действие
        initiative = self._generate_initiative(emotion_result, user_profile)
        if initiative:
            self.initiative_count += 1
            self.last_initiative_time = datetime.now().isoformat()
            return initiative

        return None

    def _generate_initiative(self, emotion_result: Dict, user_profile: Dict) -> Optional[str]:
        """Генерирует инициативное предложение на основе эмоций и профиля"""
        emotion = emotion_result.get("emotion", "neutral")
        preferences = user_profile.get("preferences", {})

        initiatives = []

        if emotion == "sad":
            favorite_comedy = preferences.get("favorite_comedy")
            if favorite_comedy:
                initiatives.append(f"Сэр, я вижу вам грустно. Может, включить {favorite_comedy}?")
            initiatives.append("Сэр, мне кажется вам грустно. Может, включить КВН для поднятия настроения?")
            initiatives.append("Сэр, хочу поднять вам настроение. Может, поставить что-то весёлое?")

        elif emotion == "bored":
            favorite_movie = preferences.get("favorite_movie")
            if favorite_movie:
                initiatives.append(f"Сэр, вам скучно. Может, посмотреть {favorite_movie}?")
            initiatives.append("Сэр, вам скучно. Может, посмотреть фильм?")
            initiatives.append("Сэр, чем заняться? Может, включу музыку?")

        elif emotion == "stressed":
            initiatives.append("Сэр, я вижу вы в стрессе. Может, сделать перерыв?")
            initiatives.append("Сэр, давайте передохнём минут 10. Включить спокойную музыку?")
            initiatives.append("Сэр, у вас много дел. Может, я помогу расставить приоритеты?")

        elif emotion == "tired":
            initiatives.append("Сэр, вы выглядите уставшим. Может, отдохнуть?")
            initiatives.append("Сэр, пора спать? Может, выключить всё и лечь?")
            initiatives.append("Сэр, сил нет? Может, включу что-то спокойное и вы отдохнёте?")

        elif emotion == "happy":
            initiatives.append("Сэр, я вижу у вас отличное настроение! Рад за вас!")
            initiatives.append("Сэр, вам хорошо! Может, продолжим в том же духе?")

        elif emotion == "neutral" and random.random() < 0.1:
            # Иногда проявляем инициативу даже при нейтральной эмоции
            initiatives.append("Сэр, чем могу помочь?")
            initiatives.append("Сэр, есть идеи для вечера?")

        if initiatives:
            return random.choice(initiatives)

        return None

    def get_proactive_suggestion(self, user_profile: Dict, current_time: datetime) -> Optional[str]:
        """
        Генерирует проактивное предложение на основе времени и контекста

        Args:
            user_profile: Профиль пользователя
            current_time: Текущее время

        Returns:
            Строка с проактивным предложением или None
        """
        hour = current_time.hour
        context = user_profile.get("context", {})
        current_activity = context.get("current_activity")

        # Утро (6-10)
        if 6 <= hour < 10:
            if not current_activity:
                return "Сэр, доброе утро! Чем займёмся сегодня?"

        # День (10-18)
        elif 10 <= hour < 18:
            if not current_activity:
                return "Сэр, как проходит день? Чем помочь?"

        # Вечер (18-22)
        elif 18 <= hour < 22:
            if not current_activity:
                preferences = user_profile.get("preferences", {})
                if preferences.get("favorite_movie"):
                    return f"Сэр, вечер! Может, посмотреть {preferences['favorite_movie']}?"
                return "Сэр, вечер! Чем заняться? Фильм или музыка?"

        # Ночь (22-6)
        elif 22 <= hour or hour < 6:
            if not current_activity:
                return "Сэр, поздно. Может, пора спать?"

        return None

    def should_learn_preference(self, command: str, action: str) -> Optional[Dict]:
        """
        Определяет, стоит ли выучить предпочтение из команды

        Args:
            command: Команда пользователя
            action: Выполненное действие

        Returns:
            Dict с preference_type и value или None
        """
        command_lower = command.lower()

        # Определяем типы предпочтений
        if "люблю" in command_lower and "комедию" in command_lower:
            # Извлекаем название комедии
            words = command_lower.split()
            if "люблю" in words:
                idx = words.index("люблю")
                if idx + 2 < len(words):
                    comedy_name = " ".join(words[idx+2:idx+4])
                    return {"type": "favorite_comedy", "value": comedy_name}

        elif "люблю" in command_lower and "музыку" in command_lower:
            words = command_lower.split()
            if "люблю" in words:
                idx = words.index("люблю")
                if idx + 2 < len(words):
                    music_name = " ".join(words[idx+2:idx+4])
                    return {"type": "favorite_music", "value": music_name}

        elif "люблю" in command_lower and "фильм" in command_lower:
            words = command_lower.split()
            if "люблю" in words:
                idx = words.index("люблю")
                if idx + 2 < len(words):
                    movie_name = " ".join(words[idx+2:idx+4])
                    return {"type": "favorite_movie", "value": movie_name}

        return None


# Тестирование
if __name__ == "__main__":
    engine = InitiativeEngine()

    # Тест эмоций
    emotion_result = {
        "emotion": "sad",
        "confidence": 0.7,
        "detected_emotions": {"sad": 2},
        "suggested_actions": ["Включить КВН"]
    }

    user_profile = {
        "preferences": {
            "favorite_comedy": "КВН",
            "favorite_music": "jazz"
        },
        "context": {
            "interaction_count": 5
        }
    }

    # Проверяем инициативу несколько раз
    print("Проверка инициативы (10 попыток):")
    for i in range(10):
        initiative = engine.should_show_initiative(emotion_result, user_profile)
        print(f"  {i+1}. {initiative or 'Нет инициативы'}")

    # Тест проактивных предложений
    print("\nПроактивные предложения по времени:")
    for hour in [8, 14, 20, 23]:
        time = datetime.now().replace(hour=hour)
        suggestion = engine.get_proactive_suggestion(user_profile, time)
        print(f"  {hour}:00 — {suggestion or 'Нет'}")
