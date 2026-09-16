"""JARVIS Mark X — Контекстный экстрактор слотов и интентов (Slot Filler).

Обеспечивает детерминированное извлечение сущностей, числительных,
платформ, команд отмены и контекстных коротких ответов («первый», «второй», «да», «нет»).
Используется оркестратором для детерминированного наполнения слотов.
"""

from __future__ import annotations

import re

from core.wake_names import WAKE_NAME_PATTERN
from typing import Any, Dict, Optional, Tuple

_ORDINALS: Dict[str, int] = {
    "первый": 1, "первая": 1, "первое": 1, "первом": 1, "первую": 1, "1": 1, "1-й": 1, "1й": 1,
    "второй": 2, "вторая": 2, "второе": 2, "втором": 2, "вторую": 2, "2": 2, "2-й": 2, "2й": 2,
    "третий": 3, "третья": 3, "третье": 3, "третьем": 3, "третью": 3, "3": 3, "3-й": 3, "3й": 3,
    "четвертый": 4, "четвёртый": 4, "четвертая": 4, "четвёртая": 4, "четвертое": 4, "четвёртое": 4, "4": 4, "4-й": 4, "4й": 4,
    "пятый": 5, "пятая": 5, "пятое": 5, "5": 5, "5-й": 5, "5й": 5,
    "шестой": 6, "шестая": 6, "шестое": 6, "6": 6, "6-й": 6, "6й": 6,
    "седьмой": 7, "седьмая": 7, "седьмое": 7, "7": 7, "7-й": 7, "7й": 7,
    "восьмой": 8, "восьмая": 8, "восьмое": 8, "8": 8, "8-й": 8, "8й": 8,
    "девятый": 9, "девятая": 9, "девятое": 9, "9": 9, "9-й": 9, "9й": 9,
    "десятый": 10, "десятая": 10, "десятое": 10, "10": 10, "10-й": 10, "10й": 10,
    "одиннадцатый": 11, "11": 11, "двенадцатый": 12, "12": 12,
    "последний": -1, "последняя": -1, "последнее": -1,
    # Кардинальные числительные:
    "один": 1, "одна": 1, "одно": 1,
    "два": 2, "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
}

_CANCEL_PATTERNS = [
    r"^(?:отмена|отмени|отбой|не\s+надо|не\s+нужно|забудь|стоп|хватит|закройся)$",
    r"^(?:не\s+хочу|передумал|отмени\s+команду|сброс)$",
]

_WAKE_PREFIX_RE = re.compile(
    r"^(?:эй\s+)?" + WAKE_NAME_PATTERN + r"[,\s!\.\?]*",
    re.IGNORECASE | re.UNICODE,
)


def normalize_text(text: str) -> str:
    """Очищает текст от обращения и лишней пунктуации."""
    t = (text or "").strip().lower()
    t = _WAKE_PREFIX_RE.sub("", t).strip()
    t = re.sub(r"[\.,!\?]+$", "", t).strip()
    return t


def strip_genitive(word: str) -> str:
    """Убирает родительный падеж для типичных названий (Менталиста -> Менталист)."""
    w = word.strip()
    if len(w) > 4 and w.endswith("а") and w[-2] not in "аеёиоуыэюяьъ":
        return w[:-1]
    return w


def extract_platform(text: str) -> Tuple[Optional[str], str]:
    """Извлекает платформу из текста и возвращает очищенный текст."""
    t = text
    platform = None
    if re.search(r"(?i)\b(?:на\s+|в\s+)?(?:ютубе?|youtube)\b", t):
        platform = "youtube"
        t = re.sub(r"(?i)\b(?:на\s+|в\s+)?(?:ютубе?|youtube)\b", "", t).strip()
    elif re.search(r"(?i)\b(?:на\s+|в\s+)?кинопоиске?\b", t):
        platform = "kinopoisk"
        t = re.sub(r"(?i)\b(?:на\s+|в\s+)?кинопоиске?\b", "", t).strip()
    elif re.search(r"(?i)\b(?:на\s+|в\s+)?(?:вк\s+видео|vk\s+video|vk|вк)\b", t):
        platform = "vkvideo"
        t = re.sub(r"(?i)\b(?:на\s+|в\s+)?(?:вк\s+видео|vk\s+video|vk|вк)\b", "", t).strip()
    return platform, t


def clean_hesitation_and_correction(text: str) -> str:
    """
    Обрабатывает заминки и исправления:
    «Менталист... хотя нет, Шерлок» -> «Шерлок»
    «Менталист, нет давай Шерлок» -> «Шерлок»
    """
    t = text.strip()
    t = re.sub(r"^(?:ээ+|мм+|ну|короче|так)[,\s]+", "", t, flags=re.IGNORECASE)
    m_fix = re.search(r"(?:хотя\s+нет|нет[,\s]+(?:лучше|давай)?|или\s+лучше)[,\s]+(.+)$", t, re.IGNORECASE)
    if m_fix:
        return m_fix.group(1).strip()
    return t


class SlotFiller:
    """Универсальный экстрактор слотов и интентов."""

    @classmethod
    def is_cancellation(cls, text: str) -> bool:
        """Проверяет, является ли фраза явной отменой текущего действия."""
        clean = normalize_text(text)
        if not clean:
            return False
        for p in _CANCEL_PATTERNS:
            if re.match(p, clean, re.IGNORECASE):
                return True
        return False

    @classmethod
    def extract_interruption_or_cancellation(cls, text: str) -> Tuple[bool, Optional[str]]:
        """
        Проверяет, содержит ли фраза отмену, за которой следует новая команда.
        Например: «Не надо. Какая погода завтра?» -> (True, 'какая погода завтра').
        """
        clean = normalize_text(text)
        if not clean:
            return False, None

        if cls.is_cancellation(clean):
            return True, None

        m = re.match(
            r"^(?:отмена|не\s+надо|не\s+нужно|забудь|стоп|хватит)[,\.\s]+(.+)$",
            clean,
            re.IGNORECASE,
        )
        if m:
            remaining = m.group(1).strip()
            return True, remaining

        return False, None

    @classmethod
    def parse_ordinal(cls, text: str) -> Optional[int]:
        """Извлекает число или порядковый номер из короткого ответа с поддержкой исправлений."""
        clean = normalize_text(text)
        clean = clean_hesitation_and_correction(clean)

        # Исправление числительного: "не первый, [а] второй", "не 1, а 2"
        m_fix = re.search(r"не\s+(?:[^\s,]+)[,\s]+(?:а\s+)?([^\s,]+)", clean, re.IGNORECASE)
        if m_fix:
            candidate = m_fix.group(1).strip()
            candidate_clean = re.sub(r"(?i)\b(?:сезон|сезона|серии|серия|серию)\b", "", candidate).strip()
            val = _ORDINALS.get(candidate_clean.lower())
            if val is not None:
                return val
            if candidate_clean.isdigit():
                return int(candidate_clean)

        clean = re.sub(r"(?i)\b(?:сезон|сезона|серии|серия|серию)\b", "", clean).strip()
        if clean in _ORDINALS:
            return _ORDINALS[clean]
        m = re.search(r"\b(\d+)\b", clean)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        return None

    @classmethod
    def extract_contextual_slots(cls, text: str, target_slot: Optional[str]) -> Dict[str, Any]:
        """
        Интерпретирует короткий ответ пользователя относительно ожидаемого слота.
        Например:
          target_slot='season', text='первый' -> {'season': 1}
          target_slot='season', text='не первый, второй' -> {'season': 2}
          target_slot='title', text='Менталист' -> {'title': 'Менталист'}
        """
        clean = normalize_text(text)
        if not clean:
            return {}
        clean = clean_hesitation_and_correction(clean)

        extracted: Dict[str, Any] = {}

        # 1. Платформа
        plat, clean = extract_platform(clean)
        if plat:
            extracted["platform"] = plat

        # 2. Ожидаемый целевой слот
        if target_slot == "season":
            num = cls.parse_ordinal(clean)
            if num is not None:
                extracted["season"] = num
                return extracted
            m = re.search(r"(\d+)\s*(?:сезон)?", clean)
            if m:
                extracted["season"] = int(m.group(1))
                return extracted
            m_s = re.search(r"сезон\s+([a-zа-я0-9\-]+)", clean, re.IGNORECASE)
            if m_s:
                s_word = m_s.group(1).lower()
                if s_word in _ORDINALS:
                    extracted["season"] = _ORDINALS[s_word]
                    return extracted
                if s_word.isdigit():
                    extracted["season"] = int(s_word)
                    return extracted

        elif target_slot == "episode":
            num = cls.parse_ordinal(clean)
            if num is not None:
                extracted["episode"] = num
                return extracted
            m = re.search(r"(\d+)\s*(?:сери[яию])?", clean)
            if m:
                extracted["episode"] = int(m.group(1))
                return extracted

        elif target_slot == "title":
            title_part = clean

            # Очистка фраз намерения (например "хочу посмотреть сериал Шерлок", "включи фильм", "давай посмотрим")
            title_part = re.sub(
                r"^(?:(?:хочу|давай|можешь|пожалуйста)?\s*(?:посмотреть|включить|поставить|глянуть|запустить)?\s*(?:мне)?\s*(?:сериал|фильм|кино|мультфильм|аниме|видео)?)\s*",
                "",
                title_part,
                flags=re.IGNORECASE,
            ).strip()

            # 1. Числовой сезон: "сезон 1", "1 сезон"
            m_num = re.search(r"\b(?:сезон\s*(\d+)|(\d+)\s*сезон)\b", title_part, re.IGNORECASE)
            if m_num:
                s_val = int(m_num.group(1) or m_num.group(2))
                extracted["season"] = s_val
                title_part = re.sub(r"\b(?:сезон\s*\d+|\d+\s*сезон)\b", "", title_part, flags=re.IGNORECASE).strip()
            else:
                # 2. Словесный сезон: "сезон первый", "первый сезон", "сезон один", "третий сезон"
                m_word1 = re.search(r"\bсезон\s+([а-яa-z0-9\-]+)\b", title_part, re.IGNORECASE)
                if m_word1 and m_word1.group(1).lower() in _ORDINALS:
                    extracted["season"] = _ORDINALS[m_word1.group(1).lower()]
                    title_part = re.sub(r"\bсезон\s+" + re.escape(m_word1.group(1)) + r"\b", "", title_part, flags=re.IGNORECASE).strip()
                else:
                    m_word2 = re.search(r"\b([а-яa-z0-9\-]+)\s+сезон\b", title_part, re.IGNORECASE)
                    if m_word2 and m_word2.group(1).lower() in _ORDINALS:
                        extracted["season"] = _ORDINALS[m_word2.group(1).lower()]
                        title_part = re.sub(r"\b" + re.escape(m_word2.group(1)) + r"\s+сезон\b", "", title_part, flags=re.IGNORECASE).strip()

            title_part = re.sub(r"^(?:сериал|фильм|кино|видео)\s+", "", title_part, flags=re.IGNORECASE).strip()
            title_part = strip_genitive(title_part)
            if title_part:
                extracted["title"] = title_part.capitalize()
            return extracted

        # Если target_slot не указан, но в тексте есть явный номер сезона
        m_s = re.search(r"\b(\d+)\s*сезон\b", clean, re.IGNORECASE)
        if m_s:
            extracted["season"] = int(m_s.group(1))
        else:
            num = cls.parse_ordinal(clean)
            if num is not None:
                extracted["number"] = num

        return extracted

    @classmethod
    def detect_new_intent(cls, text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Детерминированно определяет намерение (intent) и начальные слоты из пользовательской реплики.
        Возвращает только локальные действия (LOCAL_ACTION_INTENTS):
          - open_app
          - play_media
          - play_music
          - sleep_timer
        Любые диалоговые реплики возвращают None.
        """
        clean = normalize_text(text)
        if not clean:
            return None
        clean = clean_hesitation_and_correction(clean)

        # ─── 1. open_app ─────────────────────────────────────────────────────
        app_match = re.match(r"^(?:открой|запусти)\s+(?:приложение\s+)?([a-zа-я0-9\s_\-\.\\\/:\"'\(\)]+)$", clean, re.IGNORECASE)
        if app_match:
            target_app = app_match.group(1).strip().strip("\"'")
            if not any(target_app.lower().startswith(w) for w in ("сериал", "фильм", "кино", "музык", "песн", "трек", "видео")):
                return "open_app", {"app_name": target_app}

        # ─── 2. play_media ───────────────────────────────────────────────────
        # "включи сериал менталист", "включи фильм интерстеллар", "поставь кино", "хочу посмотреть сериал..."
        media_match = re.match(
            r"^(?:включи|поставь|запусти|открой|найди|посмотрим|глянем|хочу\s+посмотреть|давай\s+посмотрим)\s+(сериал|фильм|кино|мультфильм|аниме|видео)?\s*(.*)$",
            clean,
            re.IGNORECASE,
        )
        if media_match:
            verb_prefix = clean.split()[0].lower() if clean else ""
            media_type_raw = (media_match.group(1) or "").lower()
            remainder = (media_match.group(2) or "").strip()

            if verb_prefix in ("открой", "запусти") and not media_type_raw:
                return None

            media_type = None
            if media_type_raw in ("сериал", "аниме"):
                media_type = "series"
            elif media_type_raw in ("фильм", "кино", "мультфильм"):
                media_type = "movie"
            elif media_type_raw == "видео":
                media_type = "video"

            # Музыкальные запросы
            if remainder.startswith("музык") or remainder.startswith("песн") or remainder.startswith("трек"):
                query = re.sub(r"^(?:музыку|песню|трек)\s*", "", remainder).strip()
                return "play_music", {"query": query or "любимые треки"}

            slots: Dict[str, Any] = {}
            if media_type:
                slots["media_type"] = media_type

            # Платформа
            plat, remainder = extract_platform(remainder)
            if plat:
                slots["platform"] = plat

            # Порядковый/числовой сезон: "первый сезон", "сезон 1", "сезон один"
            m_pre = re.match(r"^(?:(?:сезон\s*(\d+)|(\d+)\s*сезон)|([а-яa-z0-9\-]+)\s+сезон)\s+(.+)$", remainder, re.IGNORECASE)
            if m_pre:
                s_val = None
                if m_pre.group(1) or m_pre.group(2):
                    s_val = int(m_pre.group(1) or m_pre.group(2))
                elif m_pre.group(3):
                    w = m_pre.group(3).lower()
                    s_val = _ORDINALS.get(w)
                if s_val is not None:
                    slots["season"] = s_val
                    remainder = m_pre.group(4).strip()
            else:
                m_season = re.search(r"\b(?:сезон\s*(\d+)|(\d+)\s*сезон)\b", remainder, re.IGNORECASE)
                if m_season:
                    slots["season"] = int(m_season.group(1) or m_season.group(2))
                    remainder = re.sub(r"\b(?:сезон\s*\d+|\d+\s*сезон)\b", "", remainder, flags=re.IGNORECASE).strip()
                else:
                    for ord_word, s_val in _ORDINALS.items():
                        if f"{ord_word} сезон" in remainder:
                            slots["season"] = s_val
                            remainder = remainder.replace(f"{ord_word} сезон", "").strip()
                            break
                        elif f"сезон {ord_word}" in remainder:
                            slots["season"] = s_val
                            remainder = remainder.replace(f"сезон {ord_word}", "").strip()
                            break

            title = remainder.strip()
            title = strip_genitive(title)
            if title:
                slots["title"] = title.capitalize()
            else:
                slots["title"] = None

            # «Включи X» без слова фильм/сериал/кино — это может быть и песня,
            # и фильм; локально этого не угадать («включи Linkin Park» уходил
            # в поиск фильма). Такие фразы отдаём Gemini — у неё есть оба
            # инструмента. Глаголы просмотра однозначны и остаются локальными.
            is_watch_verb = bool(re.match(r"^(?:посмотрим|глянем|хочу\s+посмотреть|давай\s+посмотрим)\b", clean))
            if media_type or (title and is_watch_verb):
                return "play_media", slots

        # ─── 3. sleep_timer ──────────────────────────────────────────────────
        timer_match = re.match(r"^(?:таймер\s+сна|через\s+(\d+)\s+минут\s+(?:буду\s+)?спать)$", clean, re.IGNORECASE)
        if timer_match:
            mins = timer_match.group(1) if timer_match.groups() and timer_match.group(1) else None
            return "sleep_timer", {"action": "set", "duration_minutes": int(mins) if mins else None}

        return None
