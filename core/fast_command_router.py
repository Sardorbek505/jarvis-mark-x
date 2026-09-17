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

from core.wake_names import WAKE_NAME_PATTERN
from enum import Enum
from typing import Optional

logger = logging.getLogger("jarvis-fast-router")


class ExecutionStatus(str, Enum):
    """Статус фактического исполнения команды в системе."""
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CommandCategory(str, Enum):
    """Категория команды по уровню локальной автономности."""
    LOCAL_SAFE = "LOCAL_SAFE"                    # Безопасные локальные действия (звук, экран, память)
    LOCAL_CONTEXT_DEPENDENT = "LOCAL_CONTEXT_DEPENDENT"  # Зависят от активного плеера/окна (пауза, перемотка)
    LLM_REQUIRED = "LLM_REQUIRED"                # Требуют облачного интеллекта Gemini


# Шаблоны очистки обращения по имени
_WAKE_PREFIX_RE = re.compile(
    r"^(?:эй\s+)?" + WAKE_NAME_PATTERN + r"[,\s!\.\?]*",
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

    def __new__(
        cls,
        handled: bool,
        text: Optional[str] = None,
        is_action: bool = False,
        status: ExecutionStatus = ExecutionStatus.SUCCESS,
        category: CommandCategory = CommandCategory.LOCAL_SAFE,
    ):
        return super().__new__(cls, (handled, text))

    def __init__(
        self,
        handled: bool,
        text: Optional[str] = None,
        is_action: bool = False,
        status: ExecutionStatus = ExecutionStatus.SUCCESS,
        category: CommandCategory = CommandCategory.LOCAL_SAFE,
    ):
        self.handled = handled
        self.text = text
        self.is_action = is_action
        self.status = status
        self.category = category


def _trigger_action_feedback():
    """Мягкий звуковой щелчок подтверждения выполнения действия (Success Earcon)."""
    try:
        from core.earcons import play_success_earcon
        play_success_earcon()
    except Exception:
        pass


def _active_media_session():
    try:
        from core.media.state import get_media_tracker
        return get_media_tracker().get_active_session()
    except Exception as e:
        logger.debug("media tracker note: %s", e)
        return None


def _pause_active_session() -> bool:
    session = _active_media_session()
    if not session or not session.controller:
        return False
    from core.media.orchestrator import get_media_orchestrator
    get_media_orchestrator().pause()
    return True


def _resume_active_session() -> bool:
    session = _active_media_session()
    if not session or not session.controller:
        return False
    from core.media.orchestrator import get_media_orchestrator
    get_media_orchestrator().play()
    return True


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
            return FastCommandResult(
                False,
                None,
                is_action=False,
                status=ExecutionStatus.NOT_APPLICABLE,
                category=CommandCategory.LLM_REQUIRED,
            )

        # ── 1. Пауза / Стоп ──────────────────────────────────────────────────
        if re.match(r"^(пауза|стоп|остановись|останови|поставь на паузу|замолчи|тихо|заткнись)$", clean):
            try:
                # Есть активная сессия (фильм через мост, Spotify через API) —
                # пауза адресная. Системная клавиша — переключатель, и при
                # открытом фильме и приглушённой музыке неизвестно, кого она тронет.
                if _pause_active_session():
                    _trigger_action_feedback()
                    logger.info("Fast-Path: ⏯ Пауза активной медиа-сессии")
                    return FastCommandResult(
                        True, "Поставил на паузу, сэр.", is_action=True,
                        status=ExecutionStatus.SUCCESS, category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
                from actions.music_player import _send_media_key
                ok = _send_media_key("playpause")
                if ok is not False:
                    _trigger_action_feedback()
                    logger.info("Fast-Path: ⏯ Пауза/Стоп исполнена локально")
                    if player:
                        player.write_log("SYS: ⏯ Fast-Path: Пауза")
                    return FastCommandResult(
                        True,
                        "Поставил на паузу, сэр.",
                        is_action=True,
                        status=ExecutionStatus.SUCCESS,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
                else:
                    return FastCommandResult(
                        True,
                        "Не удалось поставить воспроизведение на паузу: медиа-плеер не отвечает, сэр.",
                        is_action=True,
                        status=ExecutionStatus.FAILED,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
            except Exception as e:
                logger.error("Fast-Path pause error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка выполнения команды паузы: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

        # ── 2. Возобновление / Играй ──────────────────────────────────────────
        if re.match(r"^(продолжи|продолжай|возобнови|играй|запусти музыку|вруби музыку|продолжи музыку|продолжи фильм|продолжи видео)$", clean):
            try:
                if _resume_active_session():
                    _trigger_action_feedback()
                    logger.info("Fast-Path: ⏯ Возобновление активной медиа-сессии")
                    return FastCommandResult(
                        True, "Продолжаю воспроизведение, сэр.", is_action=True,
                        status=ExecutionStatus.SUCCESS, category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
                from actions.music_player import _send_media_key
                ok = _send_media_key("playpause")
                if ok is not False:
                    _trigger_action_feedback()
                    logger.info("Fast-Path: ⏯ Возобновление музыки исполнено локально")
                    if player:
                        player.write_log("SYS: ⏯ Fast-Path: Воспроизведение")
                    return FastCommandResult(
                        True,
                        "Продолжаю воспроизведение, сэр.",
                        is_action=True,
                        status=ExecutionStatus.SUCCESS,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
                else:
                    return FastCommandResult(
                        True,
                        "Не удалось возобновить воспроизведение: медиа-плеер не отвечает, сэр.",
                        is_action=True,
                        status=ExecutionStatus.FAILED,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
            except Exception as e:
                logger.error("Fast-Path resume error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка возобновления воспроизведения: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

        # ── 3. Следующий трек ─────────────────────────────────────────────────
        if re.match(r"^(следующий( трек| песню)?|дальше|переключи( трек)?|некст)$", clean):
            try:
                from actions.music_player import _send_media_key
                ok = _send_media_key("next")
                if ok is not False:
                    _trigger_action_feedback()
                    logger.info("Fast-Path: ⏭ Следующий трек исполнен локально")
                    if player:
                        player.write_log("SYS: ⏭ Fast-Path: Следующий трек")
                    return FastCommandResult(
                        True,
                        "Включаю следующий трек, сэр.",
                        is_action=True,
                        status=ExecutionStatus.SUCCESS,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
                else:
                    return FastCommandResult(
                        True,
                        "Не удалось переключить трек, сэр.",
                        is_action=True,
                        status=ExecutionStatus.FAILED,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
            except Exception as e:
                logger.error("Fast-Path next track error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка переключения трека: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

        # ── 4. Предыдущий трек ────────────────────────────────────────────────
        if re.match(r"^(предыдущий( трек| песню)?|назад)$", clean):
            try:
                from actions.music_player import _send_media_key
                ok = _send_media_key("prev")
                if ok is not False:
                    _trigger_action_feedback()
                    logger.info("Fast-Path: ⏮ Предыдущий трек исполнен локально")
                    if player:
                        player.write_log("SYS: ⏮ Fast-Path: Предыдущий трек")
                    return FastCommandResult(
                        True,
                        "Включаю предыдущий трек, сэр.",
                        is_action=True,
                        status=ExecutionStatus.SUCCESS,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
                else:
                    return FastCommandResult(
                        True,
                        "Не удалось вернуться к предыдущему треку, сэр.",
                        is_action=True,
                        status=ExecutionStatus.FAILED,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
            except Exception as e:
                logger.error("Fast-Path prev track error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка перехода к предыдущему треку: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

        # ── 5. Громкость: Тише ────────────────────────────────────────────────
        if re.match(r"^((?:сделай\s+)?(?:по)?тише|убавь(?:\s+(?:звук|громкость))?|приглуши(?:\s+звук)?)$", clean):
            try:
                from actions.computer_settings import computer_settings
                res = computer_settings({"action": "громкость", "description": "тише", "value": "10"}, player=player)
                if "ошибка" in (res or "").lower():
                    return FastCommandResult(
                        True,
                        f"Не удалось уменьшить громкость: {res}",
                        is_action=True,
                        status=ExecutionStatus.FAILED,
                        category=CommandCategory.LOCAL_SAFE,
                    )
                _trigger_action_feedback()
                logger.info("Fast-Path: 🔉 Громкость уменьшена")
                if player:
                    player.write_log("SYS: 🔉 Fast-Path: Громкость тише")
                return FastCommandResult(
                    True,
                    "Сделал тише, сэр.",
                    is_action=True,
                    status=ExecutionStatus.SUCCESS,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path volume down error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка уменьшения громкости: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        # ── 6. Громкость: Громче ──────────────────────────────────────────────
        if re.match(r"^((?:сделай\s+)?(?:по)?громче|прибавь(?:\s+(?:звук|громкость))?|увеличь\s+громкость)$", clean):
            try:
                from actions.computer_settings import computer_settings
                res = computer_settings({"action": "громкость", "description": "громче", "value": "10"}, player=player)
                if "ошибка" in (res or "").lower():
                    return FastCommandResult(
                        True,
                        f"Не удалось увеличить громкость: {res}",
                        is_action=True,
                        status=ExecutionStatus.FAILED,
                        category=CommandCategory.LOCAL_SAFE,
                    )
                _trigger_action_feedback()
                logger.info("Fast-Path: 🔊 Громкость увеличена")
                if player:
                    player.write_log("SYS: 🔊 Fast-Path: Громкость громче")
                return FastCommandResult(
                    True,
                    "Сделал громче, сэр.",
                    is_action=True,
                    status=ExecutionStatus.SUCCESS,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path volume up error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка увеличения громкости: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        # ── 7. Без звука (Mute) ───────────────────────────────────────────────
        if re.match(r"^(без\s+звука|выключи\s+звук|заглуши\s+звук|мьют)$", clean):
            try:
                from actions.computer_settings import computer_settings
                res = computer_settings({"action": "громкость", "description": "без звука"}, player=player)
                if "ошибка" in (res or "").lower():
                    return FastCommandResult(
                        True,
                        f"Не удалось отключить звук: {res}",
                        is_action=True,
                        status=ExecutionStatus.FAILED,
                        category=CommandCategory.LOCAL_SAFE,
                    )
                _trigger_action_feedback()
                logger.info("Fast-Path: 🔇 Звук заглушен")
                if player:
                    player.write_log("SYS: 🔇 Fast-Path: Звук выключен")
                return FastCommandResult(
                    True,
                    "Звук отключен, сэр.",
                    is_action=True,
                    status=ExecutionStatus.SUCCESS,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path mute error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка отключения звука: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        # ── 8. Полный экран видео (Fullscreen) ────────────────────────────────
        if re.match(r"^((разверни|сделай|включи)?\s*(на весь экран|полный экран)|во весь экран)$", clean):
            try:
                from actions.movie_player import movie_player
                res = movie_player({"action": "fullscreen"}, player=player)
                if "не удалось" in (res or "").lower() or "ошибка" in (res or "").lower():
                    return FastCommandResult(
                        True,
                        "Окно видеоплеера не найдено для разворачивания, сэр.",
                        is_action=True,
                        status=ExecutionStatus.UNAVAILABLE,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
                _trigger_action_feedback()
                logger.info("Fast-Path: ⛶ Полный экран видео")
                if player:
                    player.write_log("SYS: ⛶ Fast-Path: Полный экран")
                return FastCommandResult(
                    True,
                    "Развернул на полный экран, сэр.",
                    is_action=True,
                    status=ExecutionStatus.SUCCESS,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )
            except Exception as e:
                logger.error("Fast-Path fullscreen error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка переключения экрана: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

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
                resp = movie_player({"action": "seek_forward", "seconds": 0 if is_min else val, "minutes": val if is_min else 0}, player=player)
                if "не удалось" in (resp or "").lower() or "ошибка" in (resp or "").lower():
                    return FastCommandResult(
                        True,
                        "Окно видеоплеера не найдено для перемотки, сэр.",
                        is_action=True,
                        status=ExecutionStatus.UNAVAILABLE,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
                _trigger_action_feedback()
                logger.info("Fast-Path: ⏩ Перемотка вперед (%s %s)", val, unit)
                return FastCommandResult(
                    True,
                    f"Перемотал вперёд на {val} {'мин.' if is_min else 'сек.'}, сэр.",
                    is_action=True,
                    status=ExecutionStatus.SUCCESS,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )
            except Exception as e:
                logger.error("Fast-Path seek forward error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка перемотки видео: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

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
                resp = movie_player({"action": "seek_back", "seconds": 0 if is_min else val, "minutes": val if is_min else 0}, player=player)
                if "не удалось" in (resp or "").lower() or "ошибка" in (resp or "").lower():
                    return FastCommandResult(
                        True,
                        "Окно видеоплеера не найдено для перемотки, сэр.",
                        is_action=True,
                        status=ExecutionStatus.UNAVAILABLE,
                        category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                    )
                _trigger_action_feedback()
                logger.info("Fast-Path: ⏪ Перемотка назад (%s %s)", val, unit)
                return FastCommandResult(
                    True,
                    f"Перемотал назад на {val} {'мин.' if is_min else 'сек.'}, сэр.",
                    is_action=True,
                    status=ExecutionStatus.SUCCESS,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )
            except Exception as e:
                logger.error("Fast-Path seek back error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка перемотки видео назад: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

        # ── 11. Выключить экран / монитор ─────────────────────────────────────
        if re.match(r"^((выключи|погаси)\s+(экран|монитор|дисплей))$", clean):
            try:
                from actions.computer_settings import computer_settings
                res = computer_settings({"action": "заблокировать экран"}, player=player)
                if "ошибка" in (res or "").lower():
                    return FastCommandResult(
                        True,
                        f"Не удалось выключить экран: {res}",
                        is_action=True,
                        status=ExecutionStatus.FAILED,
                        category=CommandCategory.LOCAL_SAFE,
                    )
                _trigger_action_feedback()
                logger.info("Fast-Path: 💻 Экран заблокирован / погашен")
                return FastCommandResult(
                    True,
                    "Экран выключен, сэр.",
                    is_action=True,
                    status=ExecutionStatus.SUCCESS,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path screen off error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка блокировки экрана: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        # ── 12. Что сейчас играет? ───────────────────────────────────────────
        if re.match(
            r"^(что(\s+сейчас)?\s+играет|какой\s+(трек|песня)\s+играет|что\s+за\s+(песня|трек|музыка)|кто\s+по[её]т|название\s+трека)$",
            clean,
        ):
            try:
                from core.media_session_manager import MediaSessionManager
                speech = MediaSessionManager.get_now_playing_speech()
                is_active = speech and "ничего не играет" not in speech.lower() and "не удалось" not in speech.lower()
                status = ExecutionStatus.SUCCESS if is_active else ExecutionStatus.UNAVAILABLE
                logger.info("Fast-Path: 🎵 'Что сейчас играет': %s", speech)
                if player:
                    player.write_log(f"SYS: 🎵 {speech}")
                return FastCommandResult(
                    True,
                    speech,
                    is_action=False,
                    status=status,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )
            except Exception as e:
                logger.error("Fast-Path now playing error: %s", e)
                return FastCommandResult(
                    True,
                    f"Не удалось определить играющий трек: {e}",
                    is_action=False,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

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
                is_failed = not speech or "не удалось" in speech.lower() or "ошибка" in speech.lower()
                status = ExecutionStatus.FAILED if is_failed else ExecutionStatus.SUCCESS
                return FastCommandResult(
                    True,
                    speech,
                    is_action=False,
                    status=status,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path screen vision error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка анализа экрана: {e}",
                    is_action=False,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

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
                is_failed = not speech or "не удалось" in speech.lower() or "ошибка" in speech.lower()
                status = ExecutionStatus.FAILED if is_failed else ExecutionStatus.SUCCESS
                return FastCommandResult(
                    True,
                    speech,
                    is_action=False,
                    status=status,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path camera vision error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка снимка камеры: {e}",
                    is_action=False,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

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
                is_err = not resp or "не удалось" in resp.lower() or "ошибка" in resp.lower()
                status = ExecutionStatus.FAILED if is_err else ExecutionStatus.SUCCESS
                return FastCommandResult(
                    True,
                    resp,
                    is_action=True,
                    status=status,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )
            except Exception as e:
                logger.error("Fast-Path YouTube play error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка запуска YouTube: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

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
                is_err = not resp or "не удалось" in resp.lower() or "ошибка" in resp.lower()
                status = ExecutionStatus.FAILED if is_err else ExecutionStatus.SUCCESS
                return FastCommandResult(
                    True,
                    resp,
                    is_action=True,
                    status=status,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )
            except Exception as e:
                logger.error("Fast-Path trailer play error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка запуска трейлера: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

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
                is_err = not resp or "не удалось" in resp.lower() or "ошибка" in resp.lower()
                status = ExecutionStatus.FAILED if is_err else ExecutionStatus.SUCCESS
                return FastCommandResult(
                    True,
                    resp,
                    is_action=True,
                    status=status,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )
            except Exception as e:
                logger.error("Fast-Path Kinopoisk play error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка открытия Кинопоиска: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

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
                is_err = not resp or "не удалось" in resp.lower() or "ошибка" in resp.lower()
                status = ExecutionStatus.FAILED if is_err else ExecutionStatus.SUCCESS
                return FastCommandResult(
                    True,
                    resp,
                    is_action=True,
                    status=status,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )
            except Exception as e:
                logger.error("Fast-Path film play error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка запуска фильма: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

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
                is_err = not resp or "не удалось" in resp.lower() or "ошибка" in resp.lower()
                status = ExecutionStatus.FAILED if is_err else ExecutionStatus.SUCCESS
                return FastCommandResult(
                    True,
                    resp,
                    is_action=True,
                    status=status,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )
            except Exception as e:
                logger.error("Fast-Path VK Video play error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка запуска VK Видео: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_CONTEXT_DEPENDENT,
                )

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
                    if hasattr(player, "write_log"):
                        player.write_log("SYS: 🛸 Fast-Path: Компактный виджет Arc Reactor")
                    return FastCommandResult(
                        True,
                        "Перешёл в компактный режим реактора, сэр.",
                        is_action=True,
                        status=ExecutionStatus.SUCCESS,
                        category=CommandCategory.LOCAL_SAFE,
                    )
                else:
                    return FastCommandResult(
                        True,
                        "Интерфейс не поддерживает компактный режим, сэр.",
                        is_action=True,
                        status=ExecutionStatus.UNAVAILABLE,
                        category=CommandCategory.LOCAL_SAFE,
                    )
            except Exception as e:
                logger.error("Fast-Path compact mode error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка переключения в компактный режим: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        if re.match(
            r"^(?:развернись|разверни\s+интерфейс|разверни\s+окно|полный\s+режим|открой\s+окно|главное\s+окно)$",
            clean,
        ):
            try:
                _trigger_action_feedback()
                if player and hasattr(player, "set_compact_mode"):
                    player.set_compact_mode(False)
                    logger.info("Fast-Path: 🖥 Возврат в полный интерфейс")
                    if hasattr(player, "write_log"):
                        player.write_log("SYS: 🖥 Fast-Path: Полный интерфейс")
                    return FastCommandResult(
                        True,
                        "Развернул полный интерфейс, сэр.",
                        is_action=True,
                        status=ExecutionStatus.SUCCESS,
                        category=CommandCategory.LOCAL_SAFE,
                    )
                else:
                    return FastCommandResult(
                        True,
                        "Интерфейс не поддерживает разворачивание, сэр.",
                        is_action=True,
                        status=ExecutionStatus.UNAVAILABLE,
                        category=CommandCategory.LOCAL_SAFE,
                    )
            except Exception as e:
                logger.error("Fast-Path full mode error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка разворачивания интерфейса: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        # ── 21. Умные сценарии автоматизации (Routines) ───────────────────────
        if re.match(r"^(?:доброе\s+утро|утренний\s+брифинг|привет\s+джарвис)$", clean):
            try:
                from core.routines_engine import RoutinesEngine
                _trigger_action_feedback()
                speech = RoutinesEngine.execute("morning", player=player)
                raw_st = getattr(speech, "status", ExecutionStatus.SUCCESS)
                status = ExecutionStatus(getattr(raw_st, "value", raw_st))
                return FastCommandResult(
                    True,
                    speech,
                    is_action=False,
                    status=status,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path morning routine error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка выполнения утреннего сценария: {e}",
                    is_action=False,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        if re.match(r"^(?:я\s+за\s+работу|пора\s+работать|начинаем\s+работу|рабочий\s+режим)$", clean):
            try:
                from core.routines_engine import RoutinesEngine
                _trigger_action_feedback()
                speech = RoutinesEngine.execute("work", player=player)
                raw_st = getattr(speech, "status", ExecutionStatus.SUCCESS)
                status = ExecutionStatus(getattr(raw_st, "value", raw_st))
                return FastCommandResult(
                    True,
                    speech,
                    is_action=True,
                    status=status,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path work routine error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка запуска рабочего режима: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        if re.match(r"^(?:режим\s+кинотеатра|время\s+кино|кинотеатр)$", clean):
            try:
                from core.routines_engine import RoutinesEngine
                _trigger_action_feedback()
                speech = RoutinesEngine.execute("movie", player=player)
                raw_st = getattr(speech, "status", ExecutionStatus.SUCCESS)
                status = ExecutionStatus(getattr(raw_st, "value", raw_st))
                return FastCommandResult(
                    True,
                    speech,
                    is_action=True,
                    status=status,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path movie routine error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка режима кинотеатра: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        if re.match(r"^(?:спокойной\s+ночи|я\s+спать|отбой)$", clean):
            try:
                from core.routines_engine import RoutinesEngine
                _trigger_action_feedback()
                speech = RoutinesEngine.execute("bedtime", player=player)
                raw_st = getattr(speech, "status", ExecutionStatus.SUCCESS)
                status = ExecutionStatus(getattr(raw_st, "value", raw_st))
                return FastCommandResult(
                    True,
                    speech,
                    is_action=True,
                    status=status,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path bedtime routine error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка сценария отбоя: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

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
                return FastCommandResult(
                    True,
                    resp,
                    is_action=True,
                    status=ExecutionStatus.SUCCESS,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path memory save error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка сохранения факта: {e}",
                    is_action=True,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        # Сводка профиля пользователя
        if re.match(r"^(?:что\s+ты\s+обо\s+мне\s+знаешь|расскажи\s+обо\s+мне|мои\s+факты|мои\s+заметки|что\s+ты\s+помнишь)$", clean):
            try:
                from core.episodic_memory import EpisodicMemory
                summary = EpisodicMemory.get_profile_summary()
                if player and hasattr(player, "write_log"):
                    player.write_log("SYS: 🧠 Сводка профиля")
                return FastCommandResult(
                    True,
                    summary,
                    is_action=False,
                    status=ExecutionStatus.SUCCESS,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path profile summary error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка получения сводки: {e}",
                    is_action=False,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

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
                return FastCommandResult(
                    True,
                    speech,
                    is_action=False,
                    status=ExecutionStatus.SUCCESS,
                    category=CommandCategory.LOCAL_SAFE,
                )
            except Exception as e:
                logger.error("Fast-Path memory recall error: %s", e)
                return FastCommandResult(
                    True,
                    f"Ошибка поиска в памяти: {e}",
                    is_action=False,
                    status=ExecutionStatus.FAILED,
                    category=CommandCategory.LOCAL_SAFE,
                )

        # ── 22a. Закрыть видео / выключить музыку — без модели ────────────────
        if re.match(r"^(?:закрой|выключи|выруби|отключи|останови)\s+(?:фильм|видео|плеер|кино|сериал|вкладку|мультик|ролик)$", clean):
            from core.media.orchestrator import get_media_orchestrator
            text = get_media_orchestrator().close()
            _trigger_action_feedback()
            logger.info("Fast-Path: ✕ %s", text)
            return FastCommandResult(True, text, is_action=True, status=ExecutionStatus.SUCCESS,
                                     category=CommandCategory.LOCAL_CONTEXT_DEPENDENT)

        if re.match(r"^(?:выключи|выруби|отключи|останови|закрой)\s+(?:музыку|песню|трек|спотифай|spotify)$", clean):
            from core.media.orchestrator import get_media_orchestrator
            text = get_media_orchestrator().stop()
            _trigger_action_feedback()
            logger.info("Fast-Path: ⏹ %s", text)
            return FastCommandResult(True, text, is_action=True, status=ExecutionStatus.SUCCESS,
                                     category=CommandCategory.LOCAL_CONTEXT_DEPENDENT)

        # ── 23. Время и дата — без модели ─────────────────────────────────────
        # Через Gemini «который час» стоил 4–7 с. Строгие шаблоны: «сколько
        # времени займёт дорога» и «который час в Нью-Йорке» — вопросы модели.
        if re.match(
            r"^(?:скажи\s+)?(?:который\s+(?:сейчас\s+)?час|который\s+сейчас|сколько\s+(?:сейчас\s+)?времени|"
            r"сколько\s+время|время|текущее\s+время|скажи\s+время)(?:\s+сейчас)?$",
            clean,
        ):
            from actions.local_answers import time_answer
            text = time_answer()
            logger.info("Fast-Path: 🕒 %s", text)
            return FastCommandResult(True, text, is_action=False, status=ExecutionStatus.SUCCESS,
                                     category=CommandCategory.LOCAL_SAFE)

        if re.match(
            r"^(?:скажи\s+)?(?:какое\s+сегодня\s+число|какой\s+сегодня\s+день(?:\s+недели)?|"
            r"какая\s+сегодня\s+дата|какое\s+число|какой\s+день\s+недели|число\s+сегодня|сегодня\s+какое\s+число)$",
            clean,
        ):
            from actions.local_answers import date_answer
            text = date_answer()
            logger.info("Fast-Path: 📅 %s", text)
            return FastCommandResult(True, text, is_action=False, status=ExecutionStatus.SUCCESS,
                                     category=CommandCategory.LOCAL_SAFE)

        # ── 24. Текущая погода — без модели ───────────────────────────────────
        # Только «сейчас»: прогноз («завтра», «на неделю», «будет дождь») —
        # модели с её инструментом.
        m_weather = re.match(
            r"^(?:джарвис\s+)?(?:скажи\s+)?(?:"
            r"какая\s+(?:сегодня\s+|сейчас\s+)?погода|погода|что\s+(?:там\s+)?с\s+погодой|"
            r"сколько\s+(?:сейчас\s+)?градусов|какая\s+(?:сейчас\s+)?температура"
            r")(?:\s+(?:сейчас|сегодня|на\s+улице|за\s+окном))*"
            r"(?:\s+(?:в|во)\s+(?P<city>[а-яёa-z\-\s]+?))?(?:\s+(?:сейчас|сегодня))?$",
            clean,
        )
        if m_weather:
            from actions.local_answers import weather_answer
            city = (m_weather.group("city") or "").strip()
            city = city[:1].upper() + city[1:]  # normalize_command_text опускает регистр
            text = weather_answer(city)
            logger.info("Fast-Path: 🌤 %s", text)
            if player:
                player.write_log(f"SYS: 🌤 {text}")
            return FastCommandResult(True, text, is_action=False, status=ExecutionStatus.SUCCESS,
                                     category=CommandCategory.LOCAL_SAFE)

        return FastCommandResult(
            False,
            None,
            is_action=False,
            status=ExecutionStatus.NOT_APPLICABLE,
            category=CommandCategory.LLM_REQUIRED,
        )
