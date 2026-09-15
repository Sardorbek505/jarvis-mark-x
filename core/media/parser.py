"""JARVIS Mark X — Семантический парсер медиа-интентов (MediaIntentParser).

Преобразует естественно-языковые запросы пользователя с произвольным порядком слов,
разговорными формами, числами прописью (русский/английский) и сокращениями (S01E04, 1x04)
в структурированный объект MediaRequest и MediaIntentResult.
"""

import logging
import re
from typing import Any, Dict, Optional, Tuple

from core.media.models import MediaRequest, MediaIntentResult, MediaType

logger = logging.getLogger("jarvis-media-parser")

NUMBERS_MAP = {
    # Русский
    "1": 1, "01": 1, "один": 1, "одна": 1, "одно": 1, "первый": 1, "первого": 1, "первому": 1, "первым": 1, "первом": 1, "первой": 1, "первую": 1, "первая": 1, "первое": 1, "1-й": 1, "1-го": 1, "1-я": 1, "1-ю": 1,
    "2": 2, "02": 2, "два": 2, "две": 2, "второй": 2, "второго": 2, "второму": 2, "вторым": 2, "втором": 2, "вторую": 2, "вторая": 2, "второе": 2, "2-й": 2, "2-го": 2, "2-я": 2, "2-ю": 2,
    "3": 3, "03": 3, "три": 3, "третий": 3, "третьего": 3, "третьему": 3, "третьим": 3, "третьем": 3, "третьей": 3, "третью": 3, "третья": 3, "третье": 3, "3-й": 3, "3-го": 3, "3-я": 3, "3-ю": 3,
    "4": 4, "04": 4, "четыре": 4, "четвертый": 4, "четвёртый": 4, "четвертого": 4, "четвёртого": 4, "четвертому": 4, "четвёртому": 4, "четвертым": 4, "четвёртым": 4, "четвертом": 4, "четвёртом": 4, "четвертой": 4, "четвёртой": 4, "четвертую": 4, "четвёртую": 4, "четвертая": 4, "четвёртая": 4, "четвертое": 4, "четвёртое": 4, "4-й": 4, "4-го": 4, "4-я": 4, "4-ю": 4,
    "5": 5, "05": 5, "пять": 5, "пятый": 5, "пятого": 5, "пятому": 5, "пятым": 5, "пятом": 5, "пятой": 5, "пятую": 5, "пятая": 5, "пятое": 5, "5-й": 5, "5-го": 5, "5-я": 5, "5-ю": 5,
    "6": 6, "06": 6, "шесть": 6, "шестой": 6, "шестого": 6, "шестому": 6, "шестым": 6, "шестом": 6, "шестую": 6, "шестая": 6, "шестое": 6, "6-й": 6, "6-го": 6, "6-я": 6, "6-ю": 6,
    "7": 7, "07": 7, "семь": 7, "седьмой": 7, "седьмого": 7, "седьмому": 7, "седьмым": 7, "седьмом": 7, "седьмую": 7, "седьмая": 7, "седьмое": 7, "7-й": 7, "7-го": 7, "7-я": 7, "7-ю": 7,
    "8": 8, "08": 8, "восемь": 8, "восьмой": 8, "восьмого": 8, "восьмому": 8, "восьмым": 8, "восьмом": 8, "восьмую": 8, "восьмая": 8, "восьмое": 8, "8-й": 8, "8-го": 8, "8-я": 8, "8-ю": 8,
    "9": 9, "09": 9, "девять": 9, "девятый": 9, "девятого": 9, "девятому": 9, "девятым": 9, "девятом": 9, "девятой": 9, "девятую": 9, "девятая": 9, "девятое": 9, "9-й": 9, "9-го": 9, "9-я": 9, "9-ю": 9,
    "10": 10, "десять": 10, "десятый": 10, "десятого": 10, "десятому": 10, "десятым": 10, "десятом": 10, "десятой": 10, "десятую": 10, "десятая": 10, "десятое": 10, "10-й": 10, "10-го": 10, "10-я": 10, "10-ю": 10,
    "11": 11, "одиннадцать": 11, "одиннадцатый": 11, "одиннадцатого": 11, "одиннадцатом": 11, "11-й": 11,
    "12": 12, "двенадцать": 12, "двенадцатый": 12, "двенадцатого": 12, "двенадцатом": 12, "12-й": 12,
    "13": 13, "тринадцать": 13, "тринадцатый": 13,
    "14": 14, "четырнадцать": 14, "четырнадцатый": 14,
    "15": 15, "пятнадцать": 15, "пятнадцатый": 15,
    "16": 16, "шестнадцать": 16, "шестнадцатый": 16,
    "17": 17, "семнадцать": 17, "семнадцатый": 17,
    "18": 18, "восемнадцать": 18, "восемнадцатый": 18,
    "19": 19, "девятнадцать": 19, "девятнадцатый": 19,
    "20": 20, "двадцать": 20, "двадцатый": 20,
    # English
    "one": 1, "first": 1, "1st": 1,
    "two": 2, "second": 2, "2nd": 2,
    "three": 3, "third": 3, "3rd": 3,
    "four": 4, "fourth": 4, "4th": 4,
    "five": 5, "fifth": 5, "5th": 5,
    "six": 6, "sixth": 6, "6th": 6,
    "seven": 7, "seventh": 7, "7th": 7,
    "eight": 8, "eighth": 8, "8th": 8,
    "nine": 9, "ninth": 9, "9th": 9,
    "ten": 10, "tenth": 10, "10th": 10
}

NUM_PAT = r"(?:\d{1,2}(?:-?[а-яё]+|-?(?:st|nd|rd|th))?|один|перв\w*|два|втор\w*|три|трет\w*|четыр\w*|четвёр\w*|четвер\w*|пять|пят\w*|шесть|шест\w*|семь|седьм\w*|восемь|восьм\w*|девять|девят\w*|десять|десят\w*|одиннадцать|двенадцать|тринадцать|четырнадцать|пятнадцать|one|first|1st|two|second|2nd|three|third|3rd|four|fourth|4th|five|fifth|5th)"


class MediaIntentParser:
    """Семантический анализатор голосовых и текстовых команд медиасистемы."""

    _last_request: Optional[MediaRequest] = None

    @staticmethod
    def _parse_number_word(word: str) -> Optional[int]:
        if not word:
            return None
        w = word.strip().lower()
        if w.isdigit():
            return int(w)
        w_clean = re.sub(r"-[а-яё]+$", "", w)
        w_clean = re.sub(r"-(?:st|nd|rd|th)$", "", w_clean)
        if w_clean.isdigit():
            return int(w_clean)
        return NUMBERS_MAP.get(w) or NUMBERS_MAP.get(w_clean)

    @classmethod
    def extract_season_and_episode(cls, text: str) -> Tuple[Optional[int], Optional[int], str]:
        """
        Семантически извлекает номер сезона и серии из текста.
        Возвращает (season, episode, text_without_season_episode_tokens).
        """
        season = None
        episode = None
        working_text = text

        # 1. Комбинированные шаблоны: S01E04, S1 E4, S 1 E 4, 1x04, 1x4
        m_se = re.search(r"(?i)\bS\s*(\d{1,2})\s*E\s*(\d{1,2})\b", working_text)
        if m_se:
            season = int(m_se.group(1))
            episode = int(m_se.group(2))
            start, end = m_se.span(0)
            working_text = working_text[:start] + (" " * (end - start)) + working_text[end:]

        if season is None and episode is None:
            m_x = re.search(r"(?i)\b(\d{1,2})\s*x\s*(\d{1,2})\b", working_text)
            if m_x:
                season = int(m_x.group(1))
                episode = int(m_x.group(2))
                start, end = m_x.span(0)
                working_text = working_text[:start] + (" " * (end - start)) + working_text[end:]

        season_kw_pat = r"(?:сезон|season|сезоне|сезоны|сезона|сз|часть|части|part)"
        episode_kw_pat = r"(?:сери|эпизод|эпизода|серия|серию|серии|серийку|выпуск|episode|ep)"

        # 2. Ищем явные ключевые слова рядом с числами
        if season is None or episode is None:
            while True:
                matches = list(re.finditer(rf"(?i)\b{NUM_PAT}\b", working_text))
                found_any = False
                for m_num in matches:
                    start, end = m_num.span(0)
                    num_str = m_num.group(0)
                    num_val = cls._parse_number_word(num_str)
                    if num_val is None:
                        continue

                    left_text = working_text[:start].rstrip()
                    right_text = working_text[end:].lstrip()

                    m_left_s = re.search(rf"(?i)\b{season_kw_pat}$", left_text)
                    m_left_e = re.search(rf"(?i)\b{episode_kw_pat}$", left_text)

                    m_right_s = re.search(rf"(?i)^{season_kw_pat}\b", right_text)
                    m_right_e = re.search(rf"(?i)^{episode_kw_pat}\b", right_text)

                    target_type = None
                    span_to_blank = None

                    if m_left_s:
                        target_type = "season"
                        span_to_blank = (m_left_s.start(), end)
                    elif m_left_e:
                        target_type = "episode"
                        span_to_blank = (m_left_e.start(), end)
                    elif m_right_s:
                        target_type = "season"
                        kw_end = end + (len(working_text[end:]) - len(right_text)) + m_right_s.end()
                        span_to_blank = (start, kw_end)
                    elif m_right_e:
                        target_type = "episode"
                        kw_end = end + (len(working_text[end:]) - len(right_text)) + m_right_e.end()
                        span_to_blank = (start, kw_end)

                    if target_type == "season" and season is None:
                        season = num_val
                        found_any = True
                        b_start, b_end = span_to_blank
                        working_text = working_text[:b_start] + (" " * (b_end - b_start)) + working_text[b_end:]
                        break
                    elif target_type == "episode" and episode is None:
                        episode = num_val
                        found_any = True
                        b_start, b_end = span_to_blank
                        working_text = working_text[:b_start] + (" " * (b_end - b_start)) + working_text[b_end:]
                        break

                if not found_any:
                    break

        # 3. Эвристический разбор, если один из параметров уже найден, а второй прописью/порядковый (например: "четвертую из первого сезона")
        if season is not None and episode is None:
            m_num = re.search(rf"(?i)\b{NUM_PAT}\b", working_text)
            if m_num:
                start, end = m_num.span(0)
                num_val = cls._parse_number_word(m_num.group(0))
                if num_val is not None and num_val < 100:
                    episode = num_val
                    working_text = working_text[:start] + (" " * (end - start)) + working_text[end:]
        elif episode is not None and season is None:
            m_num = re.search(rf"(?i)\b{NUM_PAT}\b", working_text)
            if m_num:
                start, end = m_num.span(0)
                num_val = cls._parse_number_word(m_num.group(0))
                if num_val is not None and num_val < 100:
                    season = num_val
                    working_text = working_text[:start] + (" " * (end - start)) + working_text[end:]

        # Удаляем оставшиеся предлоги связки типа "из", "в" рядом с бывшими ключевыми словами
        cleaned_text = re.sub(rf"(?i)\b(?:{season_kw_pat}|{episode_kw_pat})\b", " ", working_text)
        cleaned_text = re.sub(r"(?i)\b(?:из|в|где|номер)\b", " ", cleaned_text)
        cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()

        return season, episode, cleaned_text

    @classmethod
    def parse_intent(cls, raw_query: Any, parameters: Optional[Dict[str, Any]] = None) -> MediaIntentResult:
        if isinstance(raw_query, MediaRequest):
            return MediaIntentResult(request=raw_query, confidence=1.0, missing_fields=[], needs_clarification=False)

        parameters = parameters or {}
        query = (str(raw_query or parameters.get("query") or parameters.get("title") or "")).strip()
        lower_query = query.lower()

        # 1. Контекстуальное продолжение / уточнение
        # А) Если пользователь отвечает "Первый сезон, четвертая" на предыдущий вопрос Джарвиса
        explicit_season = parameters.get("season")
        explicit_episode = parameters.get("episode")

        extracted_season, extracted_episode, working_text = cls.extract_season_and_episode(query)
        season = explicit_season if explicit_season is not None else extracted_season
        episode = explicit_episode if explicit_episode is not None else extracted_episode

        # Б) Переход к следующей серии ("следующая", "следующую")
        if any(k in lower_query for k in ("следующая", "следующую", "следующий эпизод", "next episode")) and cls._last_request and cls._last_request.title:
            next_ep = (cls._last_request.episode or 0) + 1
            req = MediaRequest(
                media_type=MediaType.SERIES,
                title=cls._last_request.title,
                season=cls._last_request.season or 1,
                episode=next_ep,
                provider=cls._last_request.provider,
                raw_query=query,
            )
            cls._last_request = req
            return MediaIntentResult(request=req, confidence=1.0, missing_fields=[], needs_clarification=False)

        explicit_provider = (parameters.get("provider") or parameters.get("platform") or "auto").strip().lower()

        if explicit_provider == "auto":
            if re.search(r"(?i)\b(?:кинопоиск\w*|kinopoisk)\b", lower_query):
                explicit_provider = "kinopoisk"
            elif re.search(r"(?i)\b(?:youtube|ютуб\w*)\b", lower_query):
                explicit_provider = "youtube"
            elif re.search(r"(?i)\b(?:vkvideo|vk\s+video|вк\s+видео|vk|вк)\b", lower_query):
                explicit_provider = "vk"
            elif re.search(r"(?i)\b(?:spotify|спотифа[йи]\w*)\b", lower_query):
                explicit_provider = "spotify"

        # Определение действия (action)
        action = "play"
        if any(k in lower_query for k in ("останови", "стоп")):
            action = "stop"
        elif any(k in lower_query for k in ("закрой", "выключи")):
            action = "close"
        elif "пауза" in lower_query:
            action = "pause"
        elif any(k in lower_query for k in ("продолжи", "возобнови")):
            action = "resume"

        # Очистка названия от разговорного мусора, глаголов действия, платформ и слов-паразитов
        clean_title = working_text
        verbs_pat = r"(?i)\b(?:включи|поставь|запусти|сыграй|найди|открой|врубай|вруби|давай|покажи|хочу|дай|посмотреть|врубай|поставить|открыть)\b"
        junk_pat = r"(?i)\b(?:ну\s+где|ну|короче|джарвис|нам|мне|пожалуйста|номер|часть|сериальчик|сериал|фильм|видео|песня|трек|музыка)\b"
        platforms_pat = r"(?i)\b(?:на\s+|в\s+|через\s+)?(?:ютубе?|youtube|вк\s+видео|vk\s+video|vk|вк|кинопоиске?|kinopoisk|спотифае?|spotify)\b"

        clean_title = re.sub(verbs_pat, " ", clean_title)
        clean_title = re.sub(junk_pat, " ", clean_title)
        clean_title = re.sub(platforms_pat, " ", clean_title)
        clean_title = re.sub(r"[,\.-]", " ", clean_title)
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        # Контекстуальное объединение: если название не сказано в запросе, но было в _last_request
        if not clean_title and cls._last_request and cls._last_request.title:
            clean_title = cls._last_request.title

        if not clean_title:
            clean_title = query or "медиа"

        is_series_query = season is not None or episode is not None or any(k in lower_query for k in ("сериал", "сезон", "серия", "эпизод", "episode", "season", "s1", "s01", "1x04"))

        if any(k in lower_query for k in ("трейлер", "клип", "обзор", "интервью", "шоу", "подкаст", "стрим", "видео про")):
            media_type = MediaType.VIDEO
        elif is_series_query:
            media_type = MediaType.SERIES
        elif any(k in lower_query for k in ("трек", "песня", "альбом", "музыка", "песню", "плейлист", "хиты", "хит", "рок", "джаз", "поп", "рэп", "лофи", "групп", "певец", "исполнитель", "queen", "beatles", "spotify")):
            media_type = MediaType.MUSIC
        elif any(k in lower_query for k in ("фильм", "кино", "мувик")):
            media_type = MediaType.MOVIE
        else:
            media_type = MediaType.MOVIE

        # Явный тип от вызывающего (music_player знает, что это музыка) важнее
        # угадывания по словам: «Linkin Park» без слова «песня» иначе уходит в фильмы.
        explicit_type = parameters.get("media_type")
        if explicit_type:
            try:
                media_type = MediaType(str(getattr(explicit_type, "value", explicit_type)).lower())
            except ValueError:
                logger.debug("Неизвестный media_type %r — оставляю %s", explicit_type, media_type)

        if explicit_provider == "auto":
            if media_type in (MediaType.MOVIE, MediaType.SERIES):
                provider = "vk"
            elif media_type == MediaType.MUSIC:
                provider = "spotify"
            elif media_type == MediaType.VIDEO:
                provider = "youtube"
            else:
                provider = "auto"
        else:
            provider = explicit_provider

        req = MediaRequest(
            media_type=media_type,
            title=clean_title,
            season=season,
            episode=episode,
            provider=provider,
            raw_query=query,
            url=parameters.get("playlist_url") or parameters.get("url"),
            action=action,
        )

        cls._last_request = req

        missing_fields = []
        needs_clarification = False
        confidence = 1.0

        # Разграничение неполных запросов для сериалов:
        if media_type == MediaType.SERIES:
            if season is None and episode is not None:
                missing_fields.append("season")
                needs_clarification = True
                confidence = 0.7
            elif season is not None and episode is None:
                missing_fields.append("episode")
                needs_clarification = True
                confidence = 0.85

        return MediaIntentResult(
            request=req,
            confidence=confidence,
            missing_fields=missing_fields,
            needs_clarification=needs_clarification,
        )

    @classmethod
    def parse(cls, raw_query: str, parameters: Optional[Dict[str, Any]] = None) -> MediaRequest:
        res = cls.parse_intent(raw_query, parameters)
        return res.request

    @classmethod
    def fallback_semantic_parse(cls, raw_query: str, deterministic_res: MediaIntentResult, llm_extractor: Optional[Any] = None) -> MediaIntentResult:
        """
        Fallback через Gemini Live semantic understanding при низкой уверенности детерминированного парсера.
        Результат LLM строго валидируется (LLM запрещено додумывать отсутствующие season/episode).
        """
        if deterministic_res.confidence >= 0.9 and not deterministic_res.needs_clarification:
            return deterministic_res

        if llm_extractor is not None:
            try:
                llm_res = llm_extractor(raw_query)
                has_season_mention = bool(re.search(r"(?i)\b(?:сезон\w*|season\w*|сз|s\d{1,2}|\d+x\d+)\b", raw_query))
                has_episode_mention = bool(re.search(r"(?i)\b(?:сери\w*|эпизод\w*|выпуск\w*|часть\w*|parts?|episodes?|ep\d*|e\d{1,2}|\d+x\d+)\b", raw_query))

                if not has_season_mention and llm_res.get("season") is not None:
                    logger.warning("Gemini подставил отсутствующий season — сбрасываем в None.")
                    llm_res["season"] = None

                if not has_episode_mention and llm_res.get("episode") is not None:
                    logger.warning("Gemini подставил отсутствующий episode — сбрасываем в None.")
                    llm_res["episode"] = None

                req = MediaRequest(
                    media_type=MediaType(llm_res.get("media_type", deterministic_res.request.media_type)),
                    title=llm_res.get("title") or deterministic_res.request.title,
                    season=llm_res.get("season"),
                    episode=llm_res.get("episode"),
                    provider=llm_res.get("provider") or deterministic_res.request.provider,
                    raw_query=raw_query,
                )
                needs_clarif = (req.media_type == MediaType.SERIES and (req.season is None or req.episode is None))
                missing = []
                if req.media_type == MediaType.SERIES:
                    if req.season is None:
                        missing.append("season")
                    if req.episode is None:
                        missing.append("episode")

                return MediaIntentResult(
                    request=req,
                    confidence=0.95 if not needs_clarif else 0.7,
                    missing_fields=missing,
                    needs_clarification=needs_clarif,
                )
            except Exception as e:
                logger.error(f"Ошибка при работе Gemini fallback: {e}")

        return deterministic_res