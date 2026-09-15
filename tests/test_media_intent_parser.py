"""JARVIS Mark X — Adversarial Natural Language Test Suite (MediaIntentParser).

Полная проверка семантического понимания естественно-языковых запросов медиасистемы:
1. 50+ естественных формулировок одного и того же медиа-запроса (Во все тяжкие / Breaking Bad S1E4)
2. Чистота извлечения названия (Title Extraction)
3. Защита названий, содержащих цифры (Titles with Numbers: 1899, 13 причин почему, Одиннадцать)
4. Неоднозначные запросы (Ambiguous Input & Clarification)
5. Явное переопределение провайдера (Provider Override)
6. Контекстуальное продолжение диалога (Contextual Follow-up & Next Episode)
7. Семантический fallback с валидацией Gemini LLM
"""

import pytest
from core.media.models import MediaType
from core.media.parser import MediaIntentParser


# ─── 1. NATURAL LANGUAGE ADVERSARIAL TEST (54 FORMULATIONS) ─────────────────

ADVERSARIAL_PHRASES = [
    # 1-10: Из прямого требования пользователя
    "Поставь четвертую из первого сезона Во все тяжкие",
    "Давай Во все тяжкие, в первом сезоне четвертую",
    "Хочу четвертую серию, первый сезон, Во все тяжкие",
    "Во все тяжкие, ну где первый сезон, четвертая серия",
    "Поставь мне Breaking Bad, первую часть, четвертый эпизод",
    "Джарвис, короче Во все тяжкие первый сезон, давай четвертую",
    "Врубай четвертую в первом сезоне Breaking Bad",
    "Первый сезон Breaking Bad, эпизод номер четыре",
    "Breaking Bad season one episode four",
    "Во все тяжкие one season episode four",

    # 11-20: Стандартные и инвертированные словесные конструкции
    "Включи Во все тяжкие 1 сезон 4 серия",
    "Включи 4 серию первого сезона Во все тяжкие",
    "Во все тяжкие первый сезон четвёртая серия",
    "Давай Breaking Bad S1 E4",
    "Врубай Во все тяжкие сезон один серия четыре",
    "S01E04 Breaking Bad",
    "1x04 Во все тяжкие",
    "Поставь 4 серию 1 сезона фильма Во все тяжкие",
    "Во все тяжкие S1E04",
    "Запусти сериал Во все тяжкие 1-й сезон 4-я серия",

    # 21-30: Разговорные формы, жаргон, синонимы серии/эпизода
    "Найди 4 эпизод первого сезона Во все тяжкие",
    "Во все тяжкие серия 4 сезон 1",
    "четвёртая серия 1 сезона Во все тяжкие",
    "Включи первый сезон серии 4 Во все тяжкие",
    "Во все тяжкие 1x04",
    "Открой 1 сезон 4 серию Во все тяжкие",
    "Во все тяжкие сезон 1 серия четыре",
    "Давай 4 серия первый сезон Во все тяжкие",
    "Врубай S1E4 Во все тяжкие",
    "Покажи первый сезон четвертую серию Во все тяжкие",

    # 31-40: Разные склонения, слова-паразиты, смешение языков
    "Включи 4-ю серию 1-го сезона Во все тяжкие",
    "Слушай Джарвис вруби Во все тяжкие сезон 1 эпизод 4",
    "Эй Джарвис поставь сериал Во все тяжкие первый сезон 4 серия",
    "Давай 1-й сезон 4-й эпизод Во все тяжкие",
    "Пожалуйста включи четвертую серию из сезона 1 Во все тяжкие",
    "Во все тяжкие 1 сезон эпизод 4",
    "Breaking Bad 1st season 4th episode",
    "Breaking Bad s1e4",
    "включи во все тяжкие сезон 1 серия 4",
    "поставь мне пожалуйста во все тяжкие первый сезон четвертая серия",

    # 41-54: Без пунктуации, короткие формы, сокращения, варианты регистра
    "Во все тяжкие 1 сезон 4 серия",
    "Breaking Bad season 1 episode 4",
    "1 сезон 4 серия Во все тяжкие",
    "первый сезон 4 серия Во все тяжкие",
    "4 серия 1 сезон Во все тяжкие",
    "Во все тяжкие 4 серия 1 сезон",
    "Во все тяжкие 1 сз 4 сер",
    "во все тяжкие первый сезон эпизод 4",
    "во все тяжкие сезон 1 выпуск 4",
    "Давай во все тяжкие 1 сезон 4 эпизод",
    "Поставь во все тяжкие 1st season 4th episode",
    "Во все тяжкие s01 e04",
    "во все тяжкие s 1 e 4",
    "во все тяжкие 1x4",
]


@pytest.mark.parametrize("phrase", ADVERSARIAL_PHRASES)
def test_50_adversarial_phrasings(phrase):
    """Проверка понимания смысла 50+ различных естественных формулировок одной команды."""
    req = MediaIntentParser.parse(phrase)
    assert req.media_type == MediaType.SERIES

    # Название: должно быть "во все тяжкие" или "breaking bad"
    title_lower = req.title.lower()
    assert "во все тяжкие" in title_lower or "breaking bad" in title_lower

    # Сезон и серия должны быть строго 1 и 4
    assert req.season == 1, f"Неверный сезон для '{phrase}': {req.season}"
    assert req.episode == 4, f"Неверный эпизод для '{phrase}': {req.episode}"
    assert req.provider == "vk", f"Провайдер по умолчанию для сериала должен быть vk, получено: {req.provider}"


# ─── 2. TITLE EXTRACTION ───────────────────────────────────────────────────

def test_title_extraction_no_season_episode_tokens():
    """Токены сезона и серии обязаны быть вырезаны из названия."""
    req = MediaIntentParser.parse("Включи 4 серию 1 сезона Во все тяжкие")
    assert req.title == "Во все тяжкие"
    assert "4" not in req.title
    assert "серию" not in req.title
    assert "сезона" not in req.title


# ─── 3. TITLES WITH NUMBERS ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "phrase, expected_title",
    [
        ("Включи фильм 1899", "1899"),
        ("Поставь 13 причин почему 1 сезон 2 серия", "13 причин почему"),
        ("Врубай Одиннадцать 1 сезон 4 серия", "Одиннадцать"),
        ("Запусти 8 миля", "8 миля"),
        ("Поставь 2012", "2012"),
    ]
)
def test_titles_with_numbers(phrase, expected_title):
    """Парсер не должен воспринимать числа из названия фильма/сериала как season/episode."""
    req = MediaIntentParser.parse(phrase)
    assert expected_title.lower() in req.title.lower()
    # 1899 или 2012 не должны попадать в season
    assert req.season != 1899
    assert req.season != 2012


# ─── 4. AMBIGUOUS INPUT & CLARIFICATION ─────────────────────────────────────

def test_ambiguous_input_requires_clarification():
    """Неполный запрос без сезона (серия есть) требует уточнения и не угадывает season=1."""
    res = MediaIntentParser.parse_intent("Включи Во все тяжкие 4 серию")
    assert res.request.media_type == MediaType.SERIES
    assert res.request.episode == 4
    assert res.request.season is None
    assert res.needs_clarification is True
    assert "season" in res.missing_fields
    assert res.confidence < 0.9


# ─── 5. PROVIDER OVERRIDE ──────────────────────────────────────────────────

def test_provider_override_priority():
    """Явное указание платформы пользователем имеет наивысший приоритет."""
    res1 = MediaIntentParser.parse_intent("Во все тяжкие 1 сезон 4 серия")
    assert res1.request.provider == "vk"

    res2 = MediaIntentParser.parse_intent("Во все тяжкие 1 сезон 4 серия на YouTube")
    assert res2.request.provider == "youtube"

    res3 = MediaIntentParser.parse_intent("через Кинопоиск Во все тяжкие первый сезон четвертая серия")
    assert res3.request.provider == "kinopoisk"


# ─── 6. CONTEXTUAL FOLLOW-UP & NEXT EPISODE ─────────────────────────────────

def test_contextual_dialogue_and_continuation():
    """Проверка поддержки контекста диалога и перехода к следующей серии."""
    MediaIntentParser._last_request = None

    # Шаг 1: Запрос без сезона и серии
    res1 = MediaIntentParser.parse_intent("Включи Во все тяжкие")
    assert res1.request.title == "Во все тяжкие"

    # Шаг 2: Уточнение пользователя ("Первый сезон, четвертая")
    res2 = MediaIntentParser.parse_intent("Первый сезон, четвертая")
    assert res2.request.title == "Во все тяжкие"
    assert res2.request.season == 1
    assert res2.request.episode == 4

    # Шаг 3: Команда "следующую"
    res3 = MediaIntentParser.parse_intent("следующую")
    assert res3.request.title == "Во все тяжкие"
    assert res3.request.season == 1
    assert res3.request.episode == 5


# ─── 7. SEMANTIC FALLBACK & LLM VALIDATION ─────────────────────────────────

def test_semantic_fallback_llm_validation():
    """Fallback через LLM не позволяет придумывать несуществующие season/episode."""
    MediaIntentParser._last_request = None
    det_res = MediaIntentParser.parse_intent("Включи Во все тяжкие 4 серию")

    # Имитируем галлюцинацию LLM, пытающейся подставить season=1
    def fake_llm_extractor(raw):
        return {
            "media_type": "series",
            "title": "Во все тяжкие",
            "season": 1,  # Галлюцинация! В raw нет слова сезон
            "episode": 4,
            "provider": "vk"
        }

    validated_res = MediaIntentParser.fallback_semantic_parse("Включи Во все тяжкие 4 серию", det_res, llm_extractor=fake_llm_extractor)
    # Защита должна сбросить сгенерированный season в None
    assert validated_res.request.season is None
    assert validated_res.request.episode == 4
    assert validated_res.needs_clarification is True
