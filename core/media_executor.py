"""JARVIS Mark X — Детерминированный исполнитель медиа (Media Executor & Fallback).

Отвечает за:
  1. Разрешение типа контента (фильм / сериал / видео)
  2. Формирование естественного поискового запроса с учётом сезона и серии
  3. Делегирование в movie_player
  4. Многоуровневый graceful fallback при недоступности прямого запуска
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict, Tuple

logger = logging.getLogger("jarvis-media-executor")


class MediaExecutor:
    """Исполнитель медиа-команд с детерминированным fallback."""

    @classmethod
    def execute(cls, slots: Dict[str, Any], player=None) -> Tuple[bool, str]:
        """
        Исполняет команду воспроизведения медиа.

        Возвращает (success: bool, user_message: str).
        """
        title = (slots.get("title") or "").strip()
        if not title:
            return False, "Назовите фильм или сериал, сэр."

        slots.get("media_type")
        season = slots.get("season")
        episode = slots.get("episode")
        platform = slots.get("platform") or "auto"

        # Формирование естественного поискового запроса
        # Если указан сезон, открываем сезон или его выдачу — без насильственного запуска серии 1
        query_parts = [title]
        if season is not None:
            query_parts.append(f"{season} сезон")
        if episode is not None:
            query_parts.append(f"{episode} серия")

        search_query = " ".join(query_parts)
        logger.info(
            "[EXECUTOR] play_media: title='%s', season=%s, episode=%s, platform='%s', query='%s'",
            title, season, episode, platform, search_query,
        )

        # 1. Попытка основного воспроизведения через movie_player
        try:
            from actions.movie_player import movie_player
            resp = movie_player(
                {
                    "action": "play",
                    "title": search_query,
                    "platform": platform,
                    "season": season,
                    "episode": episode,
                },
                player=player,
            )

            # Проверяем структурированный или строковый результат
            page_opened = getattr(resp, "page_opened", False)
            success = getattr(resp, "success", None)
            if success is True or page_opened:
                return True, str(resp)

            if resp and "не удалось открыть" not in str(resp).lower() and "ошибка" not in str(resp).lower():
                return True, str(resp)

            logger.warning("[EXECUTOR] Основной плеер вернул сбой: '%s' (page_opened=%s). Запуск Fallback.", resp, page_opened)

        except Exception as e:
            logger.error("[EXECUTOR] Исключение в movie_player: %s", e)

        # 2. Graceful Fallback: открытие поисковой страницы в браузере
        return cls._execute_fallback(search_query, platform, player=player)

    @classmethod
    def _execute_fallback(cls, search_query: str, platform: str, player=None) -> Tuple[bool, str]:
        """
        Запасной вариант (Fallback): открытие поиска нужного контента в браузере.
        """
        logger.info("[FALLBACK] Открытие поисковой выдачи для «%s»", search_query)
        encoded = urllib.parse.quote(search_query)

        if platform == "youtube" or any(k in search_query.lower() for k in ("клип", "трейлер", "обзор")):
            url = f"https://www.youtube.com/results?search_query={encoded}"
            service_name = "YouTube"
        elif platform == "kinopoisk":
            url = f"https://www.kinopoisk.ru/index.php?kp_query={encoded}"
            service_name = "Кинопоиске"
        else:
            url = f"https://vkvideo.ru/?q={encoded}&section=search"
            service_name = "VK Видео"

        try:
            from actions.browser_control import browser_control
            browser_control({"action": "go_to", "url": url}, player=player)
            msg = f"Прямой запуск недоступен, сэр. Открыл поиск «{search_query}» на {service_name}."
            if player and hasattr(player, "write_log"):
                player.write_log(f"SYS: 🎬 Fallback — открыт {service_name}: {search_query}")
            return True, msg
        except Exception as e:
            logger.error("[FALLBACK] Ошибка fallback в браузере: %s", e)

        # 3. Крайний fallback: системный web_search
        try:
            from actions.web_search import web_search
            web_search({"query": search_query}, player=player)
            return True, f"Открыл поиск по запросу «{search_query}», сэр."
        except Exception as e:
            logger.error("[FALLBACK] Ошибка web_search: %s", e)
            return False, f"Не удалось воспроизвести или найти «{search_query}», сэр."
