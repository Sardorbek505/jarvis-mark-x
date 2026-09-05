"""JARVIS Mark X — Быстрый локальный роутер детерминированных команд (Fast-Path).

Архитектура:
  Подобно Megamind (Яндекс.Алиса) и Apple Intelligence (Siri on-device handlers),
  директивные команды управления медиа, звуком и системой не должны отправляться
  в тяжелую облачную LLM с задержкой 1-2 секунды.

  Fast-Path мгновенно (< 2 мс) перехватывает и исполняет команды:
    - Медиа: пауза, воспроизведение, следующий/предыдущий трек
    - Громкость: тише, громче, без звука
    - Видео/Фильмы: полный экран, перемотка вперёд/назад
    - Система: погасить монитор / экран
"""

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger("jarvis-fast-router")

# Шаблоны очистки обращения по имени
_WAKE_PREFIX_RE = re.compile(
    r"^(?:эй\s+)?(?:джарвис|джервис|жарвис|jarvis)[,\s!\.\?]*",
    re.IGNORECASE | re.UNICODE,
)
_PUNCT_RE = re.compile(r"[\.,!\?]+$", re.UNICODE)


def normalize_command_text(text: str) -> str:
    """Очищает строку от имени ассистента и концевой пунктуации."""
    t = (text or "").strip().lower()
    t = _WAKE_PREFIX_RE.sub("", t).strip()
    t = _PUNCT_RE.sub("", t).strip()
    return t


class FastCommandResult(tuple):
    """Результат выполнения быстрой команды. Совместим с распаковкой (handled, text)."""

    def __new__(cls, handled: bool, text: Optional[str] = None, is_action: bool = False):
        return super().__new__(cls, (handled, text))

    def __init__(self, handled: bool, text: Optional[str] = None, is_action: bool = False):
        self.handled = handled
        self.text = text
        self.is_action = is_action


def _trigger_action_feedback():
    """Мягкий звуковой щелчок подтверждения выполнения действия (Success Earcon)."""
    try:
        from core.earcons import play_success_earcon
        play_success_earcon()
    except Exception:
        pass


class FastCommandRouter:
    """Маршрутизатор мгновенных локальных команд."""

    @classmethod
    def match_and_execute(cls, text: str, player=None) -> FastCommandResult:
        """
        Проверяет фразу на соответствие детерминированным быстрым командам.

        Возвращает:
            FastCommandResult(handled, text, is_action)
            (распаковывается как (handled, text) для обратной совместимости).
        """
        clean = normalize_command_text(text)
        if not clean:
            return FastCommandResult(False, None, is_action=False)

        # ── 1. Пауза / Стоп ──────────────────────────────────────────────────
        if re.match(r"^(пауза|стоп|остановись|останови|останови музыку|поставь на паузу|замолчи|тихо|заткнись)$", clean):
            try:
                from actions.music_player import _send_media_key
                _send_media_key("playpause")
                _trigger_action_feedback()
                logger.info("Fast-Path: ⏯ Пауза/Стоп исполнена локально")
                if player:
                    player.write_log("SYS: ⏯ Fast-Path: Пауза")
                return FastCommandResult(True, "Поставил на паузу, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path pause error: %s", e)

        # ── 2. Возобновление / Играй ──────────────────────────────────────────
        if re.match(r"^(продолжи|продолжай|возобнови|играй|запусти музыку|вруби музыку)$", clean):
            try:
                from actions.music_player import _send_media_key
                _send_media_key("playpause")
                _trigger_action_feedback()
                logger.info("Fast-Path: ⏯ Возобновление музыки исполнено локально")
                if player:
                    player.write_log("SYS: ⏯ Fast-Path: Воспроизведение")
                return FastCommandResult(True, "Продолжаю воспроизведение, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path resume error: %s", e)

        # ── 3. Следующий трек ─────────────────────────────────────────────────
        if re.match(r"^(следующий( трек| песню)?|дальше|переключи( трек)?|некст)$", clean):
            try:
                from actions.music_player import _send_media_key
                _send_media_key("next")
                _trigger_action_feedback()
                logger.info("Fast-Path: ⏭ Следующий трек исполнен локально")
                if player:
                    player.write_log("SYS: ⏭ Fast-Path: Следующий трек")
                return FastCommandResult(True, "Включаю следующий трек, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path next track error: %s", e)

        # ── 4. Предыдущий трек ────────────────────────────────────────────────
        if re.match(r"^(предыдущий( трек| песню)?|назад)$", clean):
            try:
                from actions.music_player import _send_media_key
                _send_media_key("prev")
                _trigger_action_feedback()
                logger.info("Fast-Path: ⏮ Предыдущий трек исполнен локально")
                if player:
                    player.write_log("SYS: ⏮ Fast-Path: Предыдущий трек")
                return FastCommandResult(True, "Включаю предыдущий трек, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path prev track error: %s", e)

        # ── 5. Громкость: Тише ────────────────────────────────────────────────
        if re.match(r"^((?:сделай\s+)?(?:по)?тише|убавь(?:\s+(?:звук|громкость))?|приглуши(?:\s+звук)?)$", clean):
            try:
                from actions.computer_settings import computer_settings
                res = computer_settings({"action": "громкость", "description": "тише", "value": "10"}, player=player)
                _trigger_action_feedback()
                logger.info("Fast-Path: 🔉 Громкость уменьшена")
                if player:
                    player.write_log("SYS: 🔉 Fast-Path: Громкость тише")
                return FastCommandResult(True, "Сделал тише, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path volume down error: %s", e)

        # ── 6. Громкость: Громче ──────────────────────────────────────────────
        if re.match(r"^((?:сделай\s+)?(?:по)?громче|прибавь(?:\s+(?:звук|громкость))?|увеличь\s+громкость)$", clean):
            try:
                from actions.computer_settings import computer_settings
                res = computer_settings({"action": "громкость", "description": "громче", "value": "10"}, player=player)
                _trigger_action_feedback()
                logger.info("Fast-Path: 🔊 Громкость увеличена")
                if player:
                    player.write_log("SYS: 🔊 Fast-Path: Громкость громче")
                return FastCommandResult(True, "Сделал громче, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path volume up error: %s", e)

        # ── 7. Без звука (Mute) ───────────────────────────────────────────────
        if re.match(r"^(без\s+звука|выключи\s+звук|заглуши\s+звук|мьют)$", clean):
            try:
                from actions.computer_settings import computer_settings
                res = computer_settings({"action": "громкость", "description": "без звука"}, player=player)
                _trigger_action_feedback()
                logger.info("Fast-Path: 🔇 Звук заглушен")
                if player:
                    player.write_log("SYS: 🔇 Fast-Path: Звук выключен")
                return FastCommandResult(True, "Звук отключен, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path mute error: %s", e)

        # ── 8. Полный экран видео (Fullscreen) ────────────────────────────────
        if re.match(r"^((разверни|сделай|включи)?\s*(на весь экран|полный экран)|во весь экран)$", clean):
            try:
                from actions.movie_player import movie_player
                movie_player({"action": "fullscreen"}, player=player)
                _trigger_action_feedback()
                logger.info("Fast-Path: ⛶ Полный экран видео")
                if player:
                    player.write_log("SYS: ⛶ Fast-Path: Полный экран")
                return FastCommandResult(True, "Развернул на полный экран, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path fullscreen error: %s", e)

        # ── 9. Перемотка фильма / видео вперед ────────────────────────────────
        m_fwd = re.match(
            r"^(?:перемотай|отмотай)\s+впер[её]д(?:\s+на\s+(\d+)\s*(секунд[уыа]?|сек|минут[уыа]?|мин)?)?$",
            clean,
        )
        if m_fwd:
            try:
                from actions.movie_player import movie_player
                val = int(m_fwd.group(1)) if m_fwd.group(1) else 10
                unit = m_fwd.group(2) or "сек"
                is_min = "мин" in unit
                movie_player({"action": "seek_forward", "seconds": 0 if is_min else val, "minutes": val if is_min else 0}, player=player)
                _trigger_action_feedback()
                logger.info("Fast-Path: ⏩ Перемотка вперед (%s %s)", val, unit)
                return FastCommandResult(True, f"Перемотал вперёд на {val} {'мин.' if is_min else 'сек.'}, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path seek forward error: %s", e)

        # ── 10. Перемотка фильма / видео назад ────────────────────────────────
        m_back = re.match(
            r"^(?:перемотай|отмотай)\s+назад(?:\s+на\s+(\d+)\s*(секунд[уыа]?|сек|минут[уыа]?|мин)?)?$",
            clean,
        )
        if m_back:
            try:
                from actions.movie_player import movie_player
                val = int(m_back.group(1)) if m_back.group(1) else 10
                unit = m_back.group(2) or "сек"
                is_min = "мин" in unit
                movie_player({"action": "seek_back", "seconds": 0 if is_min else val, "minutes": val if is_min else 0}, player=player)
                _trigger_action_feedback()
                logger.info("Fast-Path: ⏪ Перемотка назад (%s %s)", val, unit)
                return FastCommandResult(True, f"Перемотал назад на {val} {'мин.' if is_min else 'сек.'}, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path seek back error: %s", e)

        # ── 11. Выключить экран / монитор ─────────────────────────────────────
        if re.match(r"^((выключи|погаси)\s+(экран|монитор|дисплей))$", clean):
            try:
                from actions.computer_settings import computer_settings
                computer_settings({"action": "заблокировать экран"}, player=player)
                _trigger_action_feedback()
                logger.info("Fast-Path: 💻 Экран заблокирован / погашен")
                return FastCommandResult(True, "Экран выключен, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path screen off error: %s", e)

        # ── 12. Что сейчас играет? ───────────────────────────────────────────
        if re.match(
            r"^(что(\s+сейчас)?\s+играет|какой\s+(трек|песня)\s+играет|что\s+за\s+(песня|трек|музыка)|кто\s+по[её]т|название\s+трека)$",
            clean,
        ):
            try:
                from core.media_session_manager import MediaSessionManager
                speech = MediaSessionManager.get_now_playing_speech()
                logger.info("Fast-Path: 🎵 'Что сейчас играет': %s", speech)
                if player:
                    player.write_log(f"SYS: 🎵 {speech}")
                return FastCommandResult(True, speech, is_action=False)
            except Exception as e:
                logger.error("Fast-Path now playing error: %s", e)

        # ── 13. Зрение: Посмотри на экран / Что на экране ───────────────────
        m_vision = re.match(
            r"^(?:посмотри|взгляни|глянь|погляди)\s+(?:на\s+)?(экран|монитор|дисплей)(?:\s*[,:]?\s*(.+))?$|"
            r"^что(?:\s+(?:сейчас|у\s+меня|ты\s+видишь))?\s+(?:на\s+)?(экране|мониторе|дисплее)(?:\s*[,:]?\s*(.+))?$|"
            r"^(?:прочитай|разбери|переведи)\s+(?:текст\s+)?(?:на\s+)?(экране|мониторе)(?:\s*[,:]?\s*(.+))?$|"
            r"^(?:найди\s+ошибку|что\s+за\s+ошибка)\s+(?:на\s+)?(экране|мониторе)(?:\s*[,:]?\s*(.+))?$",
            clean,
        )
        if m_vision:
            try:
                _trigger_action_feedback()
                query = (
                    m_vision.group(2)
                    or m_vision.group(4)
                    or m_vision.group(6)
                    or m_vision.group(8)
                    or "Опиши кратко, что изображено на экране и на что стоит обратить внимание."
                ).strip()
                logger.info("Fast-Path: 👁 Зрение экрана, запрос: '%s'", query)
                if player:
                    player.write_log(f"SYS: 👁 Fast-Path: Зрение экрана ('{query}')")
                from actions.vision import analyze_vision
                speech = analyze_vision(prompt=query, source="screen")
                return FastCommandResult(True, speech, is_action=False)
            except Exception as e:
                logger.error("Fast-Path screen vision error: %s", e)

        # ── 14. Зрение: Посмотри в камеру ────────────────────────────────────
        m_cam = re.match(
            r"^(?:посмотри|взгляни|глянь)(?:\s+(?:в|через))?\s+(камеру|веб-?камеру)(?:\s*[,:]?\s*(.+))?$|"
            r"^что(?:\s+(?:сейчас|ты\s+видишь))?(?:\s+(?:в|через))?\s+(камере|веб-?камере)(?:\s*[,:]?\s*(.+))?$",
            clean,
        )
        if m_cam:
            try:
                _trigger_action_feedback()
                query = (
                    m_cam.group(2)
                    or m_cam.group(4)
                    or "Опиши кратко, что ты видишь через камеру."
                ).strip()
                logger.info("Fast-Path: 📷 Зрение камеры, запрос: '%s'", query)
                if player:
                    player.write_log(f"SYS: 📷 Fast-Path: Зрение камеры ('{query}')")
                from actions.vision import analyze_vision
                speech = analyze_vision(prompt=query, source="camera")
                return FastCommandResult(True, speech, is_action=False)
            except Exception as e:
                logger.error("Fast-Path camera vision error: %s", e)

        # ── 15. Медиа / Видео: YouTube ───────────────────────────────────────
        m_yt = re.match(
            r"^(?:включи|найди|открой|поставь)\s+(?:на\s+)?(?:ютубе|youtube)\s+(.+)$",
            clean,
        )
        if m_yt:
            try:
                from actions.movie_player import movie_player
                title_query = m_yt.group(1).strip()
                _trigger_action_feedback()
                logger.info("Fast-Path: 🎬 Запуск на YouTube: '%s'", title_query)
                resp = movie_player({"action": "play", "platform": "youtube", "title": title_query}, player=player)
                return FastCommandResult(True, resp, is_action=True)
            except Exception as e:
                logger.error("Fast-Path YouTube play error: %s", e)

        # ── 16. Медиа / Видео: Трейлер фильма ────────────────────────────────
        m_tr = re.match(
            r"^(?:включи|найди|покажи|открой)\s+трейлер(?:\s+(?:фильма|сериала|игры))?\s+(.+)$",
            clean,
        )
        if m_tr:
            try:
                from actions.movie_player import movie_player
                title_query = f"трейлер {m_tr.group(1).strip()}"
                _trigger_action_feedback()
                logger.info("Fast-Path: 🎬 Запуск трейлера: '%s'", title_query)
                resp = movie_player({"action": "play", "platform": "youtube", "title": title_query}, player=player)
                return FastCommandResult(True, resp, is_action=True)
            except Exception as e:
                logger.error("Fast-Path trailer play error: %s", e)

        # ── 17. Медиа / Кино: Кинопоиск ──────────────────────────────────────
        m_kp = re.match(
            r"^(?:включи|найди|открой)\s+(?:на\s+)?кинопоиске\s+(.+)$",
            clean,
        )
        if m_kp:
            try:
                from actions.movie_player import movie_player
                title_query = m_kp.group(1).strip()
                _trigger_action_feedback()
                logger.info("Fast-Path: 🎬 Запуск на Кинопоиске: '%s'", title_query)
                resp = movie_player({"action": "play", "platform": "kinopoisk", "title": title_query}, player=player)
                return FastCommandResult(True, resp, is_action=True)
            except Exception as e:
                logger.error("Fast-Path Kinopoisk play error: %s", e)

        # ── 18. Медиа / Фильм: Фильм / Сериал / Кино ─────────────────────────
        m_film = re.match(
            r"^(?:включи|найди|поставь)\s+(?:фильм|сериал|кино)\s+(.+)$",
            clean,
        )
        if m_film:
            try:
                from actions.movie_player import movie_player
                title_query = m_film.group(1).strip()
                _trigger_action_feedback()
                logger.info("Fast-Path: 🎬 Запуск фильма/сериала: '%s'", title_query)
                resp = movie_player({"action": "play", "platform": "auto", "title": title_query}, player=player)
                return FastCommandResult(True, resp, is_action=True)
            except Exception as e:
                logger.error("Fast-Path film play error: %s", e)

        # ── 19. Медиа / Видео: VK Видео ──────────────────────────────────────
        m_vk = re.match(
            r"^(?:включи|найди|открой)\s+(?:на\s+)?(?:вк\s+видео|vk\s+video|vk|вк)\s+(.+)$",
            clean,
        )
        if m_vk:
            try:
                from actions.movie_player import movie_player
                title_query = m_vk.group(1).strip()
                _trigger_action_feedback()
                logger.info("Fast-Path: 🎬 Запуск на VK Видео: '%s'", title_query)
                resp = movie_player({"action": "play", "platform": "vkvideo", "title": title_query}, player=player)
                return FastCommandResult(True, resp, is_action=True)
            except Exception as e:
                logger.error("Fast-Path VK Video play error: %s", e)

        # ── 20. Компактный режим / Виджет реактора ───────────────────────────
        if re.match(
            r"^(?:свернись\s+в\s+виджет|свернись\s+в\s+реактор|компактный\s+режим|включи\s+виджет|покажи\s+виджет|сверни\s+окно|свернись)$",
            clean,
        ):
            try:
                _trigger_action_feedback()
                if player and hasattr(player, "set_compact_mode"):
                    player.set_compact_mode(True)
                logger.info("Fast-Path: 🛸 Переход в компактный режим (HUD виджет)")
                if player and hasattr(player, "write_log"):
                    player.write_log("SYS: 🛸 Fast-Path: Компактный виджет Arc Reactor")
                return FastCommandResult(True, "Перешёл в компактный режим реактора, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path compact mode error: %s", e)

        if re.match(
            r"^(?:развернись|разверни\s+интерфейс|разверни\s+окно|полный\s+режим|открой\s+окно|главное\s+окно)$",
            clean,
        ):
            try:
                _trigger_action_feedback()
                if player and hasattr(player, "set_compact_mode"):
                    player.set_compact_mode(False)
                logger.info("Fast-Path: 🖥 Возврат в полный интерфейс")
                if player and hasattr(player, "write_log"):
                    player.write_log("SYS: 🖥 Fast-Path: Полный интерфейс")
                return FastCommandResult(True, "Развернул полный интерфейс, сэр.", is_action=True)
            except Exception as e:
                logger.error("Fast-Path full mode error: %s", e)

        # ── 21. Умные сценарии автоматизации (Routines) ───────────────────────
        if re.match(r"^(?:доброе\s+утро|утренний\s+брифинг|привет\s+джарвис)$", clean):
            try:
                from core.routines_engine import RoutinesEngine
                _trigger_action_feedback()
                speech = RoutinesEngine.execute("morning", player=player)
                return FastCommandResult(True, speech, is_action=False)
            except Exception as e:
                logger.error("Fast-Path morning routine error: %s", e)

        if re.match(r"^(?:я\s+за\s+работу|пора\s+работать|начинаем\s+работу|рабочий\s+режим)$", clean):
            try:
                from core.routines_engine import RoutinesEngine
                _trigger_action_feedback()
                speech = RoutinesEngine.execute("work", player=player)
                return FastCommandResult(True, speech, is_action=True)
            except Exception as e:
                logger.error("Fast-Path work routine error: %s", e)

        if re.match(r"^(?:режим\s+кинотеатра|время\s+кино|кинотеатр)$", clean):
            try:
                from core.routines_engine import RoutinesEngine
                _trigger_action_feedback()
                speech = RoutinesEngine.execute("movie", player=player)
                return FastCommandResult(True, speech, is_action=True)
            except Exception as e:
                logger.error("Fast-Path movie routine error: %s", e)

        if re.match(r"^(?:спокойной\s+ночи|я\s+спать|отбой)$", clean):
            try:
                from core.routines_engine import RoutinesEngine
                _trigger_action_feedback()
                speech = RoutinesEngine.execute("bedtime", player=player)
                return FastCommandResult(True, speech, is_action=True)
            except Exception as e:
                logger.error("Fast-Path bedtime routine error: %s", e)

        # ── 22. Долгосрочная эпизодическая память (Episodic Memory RAG) ───────
        # Сохранение нового факта
        m_save = re.match(r"^(?:запомни|сохрани)(?:\s+(?:что|в\s+память|себе))?[,\s:]*(.+)$", clean)
        if m_save:
            try:
                from core.episodic_memory import EpisodicMemory
                fact_text = m_save.group(1).strip()
                _trigger_action_feedback()
                resp = EpisodicMemory.save_fact(fact_text)
                if player and hasattr(player, "write_log"):
                    player.write_log(f"SYS: 🧠 Память: «{fact_text}»")
                return FastCommandResult(True, resp, is_action=True)
            except Exception as e:
                logger.error("Fast-Path memory save error: %s", e)

        # Сводка профиля пользователя
        if re.match(r"^(?:что\s+ты\s+обо\s+мне\s+знаешь|расскажи\s+обо\s+мне|мои\s+факты|мои\s+заметки|что\s+ты\s+помнишь)$", clean):
            try:
                from core.episodic_memory import EpisodicMemory
                summary = EpisodicMemory.get_profile_summary()
                if player and hasattr(player, "write_log"):
                    player.write_log(f"SYS: 🧠 Сводка профиля")
                return FastCommandResult(True, summary, is_action=False)
            except Exception as e:
                logger.error("Fast-Path profile summary error: %s", e)

        # Поиск и извлечение воспоминаний по запросу
        m_recall = re.match(
            r"^(?:вспомни|найди\s+в\s+памяти|где\s+(?:мой|моя|мои|мое|моё)|какой\s+(?:у\s+меня|мой))\s+(.+)$",
            clean,
        )
        if m_recall:
            try:
                from core.episodic_memory import EpisodicMemory
                sub_query = m_recall.group(1).strip()
                speech = EpisodicMemory.recall(sub_query)
                if player and hasattr(player, "write_log"):
                    player.write_log(f"SYS: 🧠 Поиск в памяти («{sub_query}»)")
                return FastCommandResult(True, speech, is_action=False)
            except Exception as e:
                logger.error("Fast-Path memory recall error: %s", e)

        return FastCommandResult(False, None, is_action=False)
