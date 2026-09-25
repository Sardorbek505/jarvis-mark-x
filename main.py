"""
ДЖАРВИС — Голосовой ИИ-ассистент
Движок: Google Gemini Live API (нативный аудио)
Язык: Русский
"""

# Force UTF-8 encoding for Windows console.
#
# line_buffering обязателен: обёртка создаёт НОВЫЙ поток и тем самым отменяет
# и `python -u`, и обычную построчную выдачу в консоль. В оконном режиме это
# было незаметно (всё видно в HUD), а без окна консоль — единственный
# интерфейс: при остановке процесса весь накопленный вывод пропадал.
import sys
import io
import os

if sys.platform == "win32":
    if sys.stdout is not None and hasattr(sys.stdout, "buffer"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            try:
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
            except Exception:
                pass
    elif sys.stdout is None:
        sys.stdout = io.StringIO()

    if sys.stderr is not None and hasattr(sys.stderr, "buffer"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            try:
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)
            except Exception:
                pass
    elif sys.stderr is None:
        sys.stderr = io.StringIO()

import asyncio
import collections
import json
import traceback
import re
import threading
import time
import subprocess
import atexit
from datetime import datetime
import logging

# Configure logging
#
# Лог пишется ещё и в файл %APPDATA%/JARVIS/jarvis.log. В оконной сборке
# (.exe без консоли) stderr нет вовсе, и раньше логи не попадали никуда:
# при любой поломке у пользователя диагностировать было нечем.
def _log_handlers() -> list:
    from logging.handlers import RotatingFileHandler
    from core.paths import get_user_data_dir   # только stdlib — безопасно до Qt
    handlers = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    try:
        handlers.append(RotatingFileHandler(
            get_user_data_dir() / "jarvis.log", maxBytes=2_000_000,
            backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    return handlers


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=_log_handlers(),
)
logger = logging.getLogger('JARVIS')


def _log_unhandled(exc_type, exc, tb):
    logger.critical("Необработанная ошибка", exc_info=(exc_type, exc, tb))


sys.excepthook = _log_unhandled
# Падение рабочего потока раньше было беззвучным: окно жило, Джарвис — нет.
threading.excepthook = lambda args: _log_unhandled(args.exc_type, args.exc_value, args.exc_traceback)

import sounddevice as sd
from google import genai
from google.genai import types

# ВАЖЕН ПОРЯДОК: onnxruntime грузится ДО PyQt6 (его тянет `from ui import ...`).
#
# На Windows Qt подменяет путь поиска DLL и подкладывает свои копии рантайма.
# Если onnxruntime импортируется после него, загрузка нативной части падает:
#   DLL load failed while importing onnxruntime_pybind11_state
# Наружу это не вылетает: WakeWordDetector2Stage ловит исключение, пишет
# предупреждение и остаётся с _oww_model = None, то есть process_pcm() всегда
# возвращает False. Ассистент при этом работает — просто ключевое слово
# «Джарвис» не срабатывает никогда, и понять почему по поведению нельзя.
#
# Проверено на этой машине: без прогрева модель не грузится, с прогревом
# грузится. Импорт «в никуда» — вся суть в том, чтобы DLL встали первыми.
try:
    import onnxruntime  # noqa: F401
except Exception as _onnx_exc:
    logger.debug("onnxruntime warm-up skipped: %s", _onnx_exc)

from ui import JarvisUI
from memory.memory_manager import load_memory, update_memory, format_memory_for_prompt
from core.emotion_analyzer import EmotionAnalyzer
from core.user_profile import UserProfile
from core.initiative_engine import InitiativeEngine
from core.proactive_engine import ProactiveEngine
from core.team_collaboration import TeamCollaborationEngine
from core.onboarding import ensure_gemini_key
from core.latency import LatencyTracker
from core.result_card import build_card, capture_foreground_png
from core.headless_ui import HeadlessUI, headless_requested
from actions.open_app import open_app
from actions.weather import weather_action
from actions.web_search import web_search
from actions.computer_settings import computer_settings
from actions.browser_control import browser_control
from actions.file_controller import file_controller
from actions.modes import set_mode, get_current_mode
from actions.movie_player import movie_player
from actions.spotify_controller import spotify_player
from actions.window_control import window_control
from actions.calendar import calendar
from actions.obsidian import obsidian_action

from core import (
    translate_text,
    get_translation_history,
    search_translations,
    set_language_enabled,
    set_default_language,
    set_learning_mode
)


# ─── Пути и константы ─────────────────────────────────────────────────────────
from core.paths import get_base_dir, get_config_path, get_data_root, get_prompt_path

BASE_DIR      = get_base_dir()
# Изменяемые данные (профиль, прогнозы, команда): в .exe — %APPDATA%/JARVIS.
DATA_DIR      = get_data_root()
API_CONFIG    = get_config_path("api_keys.json")
PROMPT_PATH   = get_prompt_path()

# Модель Gemini Live с нативным аудио
LIVE_MODEL        = "models/gemini-2.5-flash-native-audio-latest"
CHANNELS          = 1
SEND_SAMPLE_RATE  = 16000
RECV_SAMPLE_RATE  = 24000
# Пауза перед переоткрытием звукового устройства после сбоя записи. Короче —
# бьёмся в устройство, которое ещё не освободилось; длиннее — заметная дыра
# в речи, ведь ответ в это время уже идёт.
_PLAYBACK_RETRY_SEC = 1.0

# Микрофон с аппаратным шумоподавлением, если он есть в системе.
#
# Обычный микрофон слышит комнату целиком — включая музыку из собственных
# динамиков ноутбука. В логе это выглядело так: Джарвис прилежно расшифровывал
# узбекскую песню и отвечал ей, а живую речь рядом не разбирал. Программный
# порог громкости тут бессилен: замерено, музыка даёт RMS 7000-14000, ровно
# как речь, и по громкости они неразличимы.
#
# У ASUS (Intelligo) и у ряда ноутбуков есть отдельное устройство ввода с
# подавлением фона на уровне драйвера — оно вычитает и звук своих динамиков.
# Берём его, если найдётся; MIC_DEVICE позволяет задать вручную.
_NOISE_CANCEL_HINTS = ("noise-cancelling", "noise cancelling", "noise-canceling",
                       "шумоподавлен")

# Сколько тишины ждать, прежде чем считать фразу законченной. По умолчанию
# модель ждёт около секунды — это и есть та самая пауза перед ответом.
# Ниже 300 мс модель режет человека на паузах внутри фразы (см. _build_config).
_VAD_SILENCE_MS = int(os.getenv("VAD_SILENCE_MS", "400"))
_VAD_PREFIX_MS = int(os.getenv("VAD_PREFIX_MS", "120"))

# Сколько модели позволено думать перед тем, как открыть рот.
#
# Это оказалось главным источником задержки, а вовсе не синтез речи. Замер
# 17.08.2026, один и тот же звук, три прогона на конфиг, первый байт аудио:
#     без ограничения  — медиана 4152 мс
#     thinking_budget=0 — медиана 1377 мс
# Разговорной реплике и вызову инструмента рассуждения не нужны: Джарвис
# отвечает на «который час» и «включи музыку», а не решает задачи. Поднять
# стоит только если он начнёт путаться в многошаговых просьбах.
_THINKING_BUDGET = int(os.getenv("JARVIS_THINKING_BUDGET", "0"))

# Чей голос звучит из динамиков: "fish" — тот самый Джарвис, которым говорит
# Telegram-бот (тот же ключ, голос и модель, telegram_bot/tts_fish.py),
# "gemini" — встроенный пресет Charon.
#
# Мозг в обоих случаях один и тот же: Gemini Live понимает речь, держит
# характер и вызывает инструменты. Меняется только, кто произносит готовый
# ответ. Текст для Fish берём из output_transcription — просить у Live-модели
# ответ текстом нельзя, native-audio отвечает на TEXT-модальность ошибкой
# 1007 (проверено 17.08.2026 и на 2.5-native-audio, и на 3.1-flash-live).
#
# Цена голоса — секунда: Fish начинает звучать только когда текст готов
# (первый кусок ~990 мс). Gemini в это время всё равно синтезирует Charon'а,
# и этот звук мы выбрасываем — иначе они заговорили бы хором.
def _read_config_voice() -> str:
    try:
        if API_CONFIG.exists():
            data = json.loads(API_CONFIG.read_text(encoding="utf-8"))
            val = data.get("jarvis_voice") or data.get("voice_provider")
            if val:
                return str(val).strip().lower()
    except Exception:
        pass
    return ""

def _default_voice() -> str:
    # Без ключа Fish «киношный» голос недостижим: звук Gemini выбрасывается,
    # а ответ ждёт полного текста и озвучивается Edge-TTS. Тогда пусть говорит
    # сам Gemini — сразу и без лишней секунды.
    try:
        from telegram_bot import tts_fish
        return "fish" if tts_fish.is_configured() else "gemini"
    except Exception:
        return "gemini"

_VOICE_PROVIDER = (os.getenv("JARVIS_VOICE") or _read_config_voice() or _default_voice()).strip().lower()

def get_voice_provider() -> str:
    global _VOICE_PROVIDER
    return _VOICE_PROVIDER

def set_voice_provider(provider: str):
    global _VOICE_PROVIDER
    _VOICE_PROVIDER = provider.strip().lower()
    try:
        if API_CONFIG.exists():
            data = json.loads(API_CONFIG.read_text(encoding="utf-8"))
            data["jarvis_voice"] = _VOICE_PROVIDER
            API_CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("Не удалось сохранить jarvis_voice в конфиг: %s", exc)

# За что принимаем «полную громкость» на индикаторе HUD. Не 32767: обычная
# речь в метре от ноутбука даёт RMS порядка 1000-5000, и по полной шкале
# int16 полоска почти не двигалась бы.
_MIC_FULL_SCALE = float(os.getenv("JARVIS_LEVEL_SCALE", "4000"))

# У синтеза громкость ровнее и выше, чем у микрофона в комнате, поэтому шкала
# своя: по микрофонной волна упиралась бы в потолок на каждом слове.
_SPEAK_FULL_SCALE = float(os.getenv("JARVIS_SPEAK_LEVEL_SCALE", "9000"))

# Выше какого уровня в динамиках микрофон не слушаем.
#
# 0.08 — отсекает умеренно громкий звук в динамиках, не блокируя микрофон
# при тихом фоновом шуме. Во время воспроизведения собственного ответа
# Джарвиса микрофон глушится явно. MIC_IGNORE_SPEAKERS=0 отключает защиту.
_SPEAKER_GATE = float(os.getenv("SPEAKER_GATE", "0.08"))
# Как часто рапортовать, что микрофон глух. Реже — можно не заметить, чаще —
# спам: колбэк зовётся ~15 раз в секунду.
_GATE_REPORT_SEC = float(os.getenv("MIC_GATE_REPORT_SEC", "5"))
# Защита включена по умолчанию. Выключенной она означает, что музыка из
# собственных динамиков едет в облако как речь: замерено ещё 17.08.2026 —
# музыка даёт RMS 7000-14000, ровно как голос, по громкости их не различить
# (см. коммит «Джарвис слышал музыку из своих динамиков, а не человека»).
# Собственный ответ Джарвиса прикрыт отдельно, флагом _is_speaking, — а вот
# фильм или плейлист ничем, кроме этого гейта.
_IGNORE_SPEAKERS = os.getenv("MIC_IGNORE_SPEAKERS", "1") != "0"

# ── Обращение по имени ───────────────────────────────────────────────────────
# Пока Джарвис «спит», имя слушает офлайн-детектор на ПК (core/wake_vosk.py),
# и в Gemini не уходит ничего. Услышал «Джарвис» — открывается окно разговора
# (_AWAKE_SEC после последней реплики), звук идёт в Gemini. Модели Vosk нет —
# запасной путь: звук идёт в Gemini, а имя ищется в его расшифровке (старый
# детектор hey_jarvis русское «Джарвис» не слышал: 0.004 при пороге 0.5).
# JARVIS_WAKE_MODE=always_on — отвечать на всё без имени.
_WAKE_MODE = os.getenv("JARVIS_WAKE_MODE", "wake_word").strip().lower()
_AWAKE_SEC = float(os.getenv("JARVIS_AWAKE_SEC", "30"))
# Как пишется имя (Джарвис, Жарвис, Джервис, Jarvis, падежи) — в одном месте,
# им пользуются и расшифровка Gemini, и локальный детектор.
from core.wake_vosk import WAKE_RE as _WAKE_RE, LocalWake  # noqa: E402


def _has_wake_word(text: str) -> bool:
    return bool(text) and bool(_WAKE_RE.search(text))


# Сколько после собственной речи ещё не слушать микрофон: звук досыпается из
# буфера звуковой карты и отражается от стен. Без этого хвоста Джарвис
# слышал конец своей фразы и отвечал сам себе.
_ECHO_TAIL_SEC = float(os.getenv("JARVIS_ECHO_TAIL_SEC", "0.5"))

# Потолок паузы между попытками подключения к Gemini.
_RECONNECT_MAX_SEC = 30.0
# Сессия считается здоровой, если прожила столько — иначе разрыв сразу после
# подключения идёт с нарастающей паузой, а не крутится по два раза в секунду.
_SESSION_HEALTHY_SEC = 10.0
# Сколько секунд без кадров считать, что микрофон отвалился (см. _listen_audio).
_MIC_STALL_SEC = 2.0
# Fish ждёт следующий кусок ответа не дольше этого (см. _fish_worker).
_FISH_IDLE_SEC = 20.0
# Сколько ждать ответа инструмента, прежде чем сказать «не успело».
_TOOL_TIMEOUT_SEC = float(os.getenv("JARVIS_TOOL_TIMEOUT_SEC", "45"))
# Сколько ждать расшифровку с именем, если вызов инструмента пришёл раньше.
_TOOL_WAKE_WAIT_SEC = 1.5
# Сколько реплик подряд без имени продолжают разговор (см. _continue_conversation).
_FOLLOWUPS = int(os.getenv("JARVIS_FOLLOWUPS", "4"))
# Сколько звука до пробуждения отдать в Gemini: имя и начало команды —
# «Джарвис, открой ютуб» говорят на одном дыхании. 32 кадра по 64 мс ≈ 2 с.
_WAKE_PREROLL_FRAMES = 32


def _device_is_silent(index: int, seconds: float = 0.05, need_signal: bool = True) -> bool:
    """Проверяет, является ли устройство мёртвым или фантомным виртуальным входом.

    need_signal=False — достаточно, что устройство открывается и пишет.
    Гарнитура и микрофон с шумоподавлением (ASUS AI, Krisp) в тихой комнате
    честно отдают цифровой ноль, и проверка «есть ли сигнал» отбрасывала их
    в пользу встроенного массива.
    """
    try:
        import numpy as np
        rec = sd.rec(int(seconds * SEND_SAMPLE_RATE), samplerate=SEND_SAMPLE_RATE,
                     channels=1, dtype="int16", device=index)
        sd.wait()
        if not need_signal:
            return False
        peak = int(np.abs(rec).max())
        return peak == 0
    except Exception as exc:
        logger.warning("Устройство %s не удалось проверить (%s) — пропускаю", index, exc)
        return True


def _pick_input_device():
    """Индекс микрофона: из MIC_DEVICE, иначе лучший физический/шумоподавляющий микрофон, иначе None."""
    manual = os.getenv("MIC_DEVICE", "").strip()
    if manual:
        try:
            return int(manual)
        except ValueError:
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and manual.lower() in d["name"].lower():
                    return i
            logger.warning("MIC_DEVICE=%r не найден — беру системный по умолчанию", manual)
            return None

    devices = list(enumerate(sd.query_devices()))

    # 1. Подключенные наушники и гарнитуры (Bluetooth, USB, AirPods, Buds, Headset, Гарнитура)
    # Если вы надели наушники — их микрофон находится ближе всего ко рту, поэтому имеет абсолютный приоритет!
    for i, d in devices:
        if d["max_input_channels"] <= 0 or d.get("hostapi", 0) != 0:
            continue
        name = d["name"].lower()
        if any(k in name for k in ("headset", "headphone", "bluetooth", "wireless", "buds", "airpods", "freebuds", "wh-1000", "airdots", "гарнитур", "наушник", "hands-free", "usb")) and not _device_is_silent(i, need_signal=False):
            # Веб-камера тоже «USB», но её микрофон — через всю комнату.
            is_camera = any(k in name for k in ("cam", "камер"))
            if not is_camera and "virtual" not in name and "line" not in name and "output" not in name:
                logger.info("Обнаружена подключенная гарнитура/наушники — выбран микрофон: «%s» (индекс %d)", d["name"], i)
                return i

    # 2. Аппаратные/драйверные микрофоны с ИИ-шумоподавлением (ASUS AI Noise-cancelling, Krisp, RTX Voice, Intelligo)
    # Они отсекают пространственные шумы комнаты, эхо и посторонние голоса при работе со встроенного микрофона ноутбука.
    for i, d in devices:
        if d["max_input_channels"] <= 0 or d.get("hostapi", 0) != 0:
            continue
        name = d["name"].lower()
        if any(k in name for k in ("noise-cancelling", "noise cancelling", "noise-canceling", "шумоподавлен", "ai noise")) and not _device_is_silent(i, need_signal=False):
            if "virtual line" not in name and "output" not in name:
                logger.info("Выбран микрофон с аппаратным шумоподавлением: «%s» (индекс %d)", d["name"], i)
                return i

    # 3. Встроенный Realtek / массив микрофонов
    for i, d in devices:
        if d["max_input_channels"] <= 0 or d.get("hostapi", 0) != 0:
            continue
        name = d["name"].lower()
        if any(k in name for k in ("realtek", "микрофон", "array", "массив")) and not _device_is_silent(i):
            if "virtual" not in name and "line" not in name:
                logger.info("Выбран микрофон: «%s» (индекс %d)", d["name"], i)
                return i

    # 4. Системное устройство по умолчанию
    return None
CHUNK_SIZE        = 1024

# Порог тишины для микрофона (RMS по int16). Ниже него кадры в облако не
# уходят вовсе. Речь в метре от ноутбука даёт ~1000-5000, тишина — единицы
# и десятки. 150 отсекает пространственный шум комнаты, шёпот и шорохи.
# Тихий микрофон — подобрать своё значение: scripts/mic_check.py.
MIC_RMS_THRESHOLD = float(os.getenv("MIC_RMS_THRESHOLD", "150"))
# Хвост тишины после речи — не косметика, а условие того, что тебе вообще
# ответят. Конец фразы определяет VAD на стороне Gemini, и определить его он
# может только по ПОЛУЧЕННОЙ тишине: когда гейт обрывает поток сразу за
# последним громким кадром, сервер остаётся ждать продолжения фразы.
MIC_HANGOVER_MS = int(os.getenv("MIC_HANGOVER_MS", str(_VAD_SILENCE_MS + 800)))
_FRAME_MS = CHUNK_SIZE / SEND_SAMPLE_RATE * 1000
MIC_HANGOVER_FRAMES = int(os.getenv(
    "MIC_HANGOVER_FRAMES", str(max(1, round(MIC_HANGOVER_MS / _FRAME_MS)))
))

# ── Необратимые действия ──────────────────────────────────────────────────────
# Окно, в течение которого повторный вызов считается подтверждением.
_CONFIRM_WINDOW_SEC = 90

# Ключи ищем и по-английски (как объявлено модели), и по-русски: слой действий
# сопоставляет русские подстроки, и «перезагрузи» доходит именно так.
_DESTRUCTIVE = {
    "computer_control": ("shutdown", "restart", "reboot", "выключ", "перезагруз"),
    "files": ("delete", "remove", "удал"),
}


def _action_of(args: dict) -> str:
    return str(args.get("action", "")).strip().lower()


def _args_key(name: str, args: dict) -> str:
    """Подтверждение действует на ЭТОТ вызов целиком: «да» на удаление
    a.txt не должно открывать удаление всего рабочего стола."""
    import json as _json
    return name + ":" + _json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


_YES_RE = re.compile(
    r"\b(да|давай|подтверждаю|конечно|выключай|удаляй|перезагружай|ага|угу|"
    r"yes|yeah|ok|окей|ha|ҳа|иә|иа)\b", re.IGNORECASE)
_NO_RE = re.compile(r"\b(нет|не|отмена|стоп|no|yo'q|жоқ)\b", re.IGNORECASE)


def _is_affirmative(text: str) -> bool:
    return bool(text) and bool(_YES_RE.search(text)) and not _NO_RE.search(text)


def _is_destructive(name: str, args: dict) -> bool:
    keys = _DESTRUCTIVE.get(name)
    if not keys:
        return False
    action = _action_of(args)
    return any(k in action for k in keys)


_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)
_NOISE_TOKENS_RE = re.compile(
    r"<\s*noise\s*>|<\s*laughter\s*>|<\s*applause\s*>|\[\s*noise\s*\]|\(\s*noise\s*\)|"
    r"<\s*whisper\s*>|<\s*gasp\s*>|<\s*groan\s*>",
    re.IGNORECASE,
)


# ─── Вспомогательные функции ──────────────────────────────────────────────────
def _get_api_key() -> str:
    key = ensure_gemini_key(API_CONFIG)
    if not key:
        sys.exit(1)
    return key


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        # В файле 27 КБ: личность, правила инструментов, поддержка узбекского.
        # Запасной вариант ниже — четыре строки. Без крика в лог подмена
        # незаметна: Джарвис просто становится обычным ассистентом, забывает
        # узбекский и перестаёт слушаться правил, а причина невидима.
        logger.critical(
            "НЕ ПРОЧИТАЛСЯ %s (%s) — работаю на урезанном промпте: без узбекского "
            "и без правил поведения. Проверь файл.", PROMPT_PATH, exc
        )
        return (
            "Ты ДЖАРВИС — персональный голосовой ИИ-ассистент. "
            "Говоришь ТОЛЬКО на русском языке. "
            "Отвечаешь кратко, уверенно. Обращаешься 'сэр'. "
            "Всегда вызываешь инструменты — никогда не симулируешь результат."
        )


def _clean_dialog_text(text: str) -> str:
    """Очищает и нормализует текст диалога, отсекая шум и звуковые артефакты."""
    if not text:
        return ""
    text = _CTRL_RE.sub("", text)
    text = _NOISE_TOKENS_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)

    # Убираем звуки-паразиты и заминки
    for filler in ("э-э-э", "м-м-м", "э-м-м", "м-э-м", "э-э", "м-м", "а-а"):
        text = re.sub(rf"\b{filler}\b", "", text, flags=re.IGNORECASE)

    # Исправляем склеивание оторванных знаков (например, "став ь" -> "ставь", "под ъ" -> "подъ")
    text = re.sub(r"([а-яёА-ЯЁ]{2,})\s+([ьъы])\b", r"\1\2", text)

    # Нормализуем множественные пробелы
    text = re.sub(r"\s+", " ", text).strip()

    # Если после очистки осталась только пунктуация (например, ".", "...", "?", "-") — пустая строка
    if re.match(r"^[\s\W_]*$", text):
        return ""
    return text


_clean = _clean_dialog_text


# Короче этого предложение не отправляем в синтез отдельно: «Да, сэр.» звучит
# оборванно, если оторвать его от следующей фразы, а выигрыша по времени не
# даёт — накладные расходы запроса больше самой фразы.
_MIN_SPEECH_CHUNK = 40

# Первому куску порог ниже: он определяет, через сколько человек услышит хоть
# что-то, а синтез тем короче, чем короче фраза. «Секунду, сэр.» — идеальное
# начало: звучит почти сразу и прикрывает синтез остального ответа.
_MIN_FIRST_CHUNK = 12


def _chunk_level(chunk: bytes) -> float:
    """Громкость 0..1 куска int16-аудио — для волны на HUD.

    Считается на каждом кадре воспроизведения, поэтому берём срез, а не весь
    буфер: точность здесь никому не нужна, а лишние миллисекунды в звуковом
    цикле слышно как щелчки.
    """
    try:
        import numpy as np
        head = chunk[:2048]
        if len(head) < 2:
            return 0.0
        data = np.frombuffer(head[:len(head) // 2 * 2], dtype=np.int16)
        rms = float(np.sqrt(np.mean(np.square(data.astype(np.float32)))))
        return min(1.0, rms / _SPEAK_FULL_SCALE)
    except Exception:
        return 0.0


def _split_for_speech(text: str) -> list[str]:
    """Режет ответ на куски, которые можно синтезировать и играть по очереди.

    Смысл в том, чтобы человек услышал первое предложение, пока синтезируется
    второе: целиком длинный ответ готовится секундами, а первая фраза — почти
    сразу. Слишком мелкие куски вредны — у синтеза ломается интонация, и
    каждый запрос стоит своего round-trip'а, поэтому короткие склеиваем.
    """
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    chunks: list[str] = []
    for part in parts:
        if not part:
            continue
        floor = _MIN_FIRST_CHUNK if len(chunks) == 1 else _MIN_SPEECH_CHUNK
        if chunks and len(chunks[-1]) < floor:
            chunks[-1] = f"{chunks[-1]} {part}"
        else:
            chunks.append(part)
    return chunks


def _take_speakable(buf: str, first: bool, final: bool) -> tuple[list[str], str]:
    """Отрезает от потоковой расшифровки ответа готовые к синтезу куски.

    Fish раньше получал ответ только по turn_complete — а тот приходит на
    4-5 секунд позже первого звука Gemini (замер в test_voice_loop_e2e):
    модель «проговаривает» весь ответ, прежде чем закрыть ход. Эти секунды
    Джарвис молчал. Теперь предложение уходит в синтез, как только в
    расшифровке появилась его точка. Пороги те же, что у _split_for_speech.
    """
    chunks: list[str] = []
    while True:
        floor = _MIN_FIRST_CHUNK if first and not chunks else _MIN_SPEECH_CHUNK
        cut = next((m.end() for m in re.finditer(r"[.!?…]+(?=\s)", buf)
                    if len(buf[:m.end()].strip()) >= floor), None)
        if cut is None:
            break
        chunks.append(buf[:cut].strip())
        buf = buf[cut:]
    if final and buf.strip():
        chunks.append(buf.strip())
        buf = ""
    return chunks, buf


# ─── Описания инструментов (на русском) ───────────────────────────────────────
TOOLS = [
    {
        "name": "open_app",
        "description": (
            "Запускает установленную программу Windows по имени: Telegram, Chrome, Steam, Discord, "
            "Spotify, Word, VS Code, калькулятор, проводник, настройки. Если уже запущена — выводит "
            "её окно вперёд. НЕ для сайтов (browser), музыки (music_player), фильмов (movie_player)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Имя программы как сказал пользователь («телеграм», «хром», «стим»)"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "weather",
        "description": "Погода в городе: сейчас и прогноз на сегодня, завтра и послезавтра. Вызывай и на «погода на завтра» — прогноз уже в ответе.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "Название города"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Ищет информацию в интернете по запросу пользователя. "
            "Используй когда нужны актуальные данные, факты, новости или что-либо неизвестное."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Поисковый запрос"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "computer_control",
        "description": (
            "Системные настройки ПК. Громкость: «громче», «тише», «громкость 50», «выключи/включи звук». "
            "Яркость: «ярче», «темнее», «яркость 30». Также lock — заблокировать, shutdown/restart — "
            "выключить/перезагрузить ПК (только по явной просьбе), screenshot — ТОЛЬКО сохранить снимок "
            "в файл (чтобы посмотреть на экран — look_at_screen). «Громче/тише» — это системная громкость, "
            "если не сказано «музыку громче»."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["volume_up", "volume_down", "volume_set", "mute", "unmute",
                             "brightness_up", "brightness_down", "brightness_set",
                             "screenshot", "lock", "shutdown", "restart"],
                },
                "value": {
                    "type": "STRING",
                    "description": ("Для *_set — целевой уровень 0-100 («50», «максимум»). "
                                    "Для *_up/*_down — шаг в процентах, по умолчанию 10."),
                },
            },
            "required": ["action"]
        }
    },
    {
        "name": "browser",
        "description": (
            "Управляет браузером: открывает сайты, выполняет поиск в браузере."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "go_to — открыть сайт | search — поиск в браузере"
                },
                "url":    {"type": "STRING", "description": "URL для go_to"},
                "query":  {"type": "STRING", "description": "Поисковый запрос для search"},
                "engine": {"type": "STRING", "description": "google | yandex | duckduckgo (по умолчанию google)"},
                "browser": {"type": "STRING", "description": "chrome | firefox | edge (необязательно)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "files",
        "description": (
            "Управляет файлами и папками: показывает список, читает, "
            "создаёт, перемещает, копирует, переименовывает, удаляет файлы. "
            "Может показать использование диска."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "list | read | create_file | create_folder | "
                        "delete | move | copy | rename | find | disk_usage"
                    )
                },
                "path":        {"type": "STRING", "description": "Путь к файлу/папке или: desktop, downloads, documents"},
                "destination": {"type": "STRING", "description": "Путь назначения для move/copy"},
                "content":     {"type": "STRING", "description": "Содержимое для create_file"},
                "new_name":    {"type": "STRING", "description": "Новое имя для rename"},
                "name":        {"type": "STRING", "description": "Имя для поиска (find)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "save_to_memory",
        "description": (
            "Сохраняет важный факт о пользователе в долгосрочную память. "
            "Вызывай тихо, когда пользователь называет своё имя, город, проект или предпочтение. "
            "Не сообщай пользователю что сохраняешь."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "identity | preferences | projects | relationships | wishes | notes"
                },
                "key":   {"type": "STRING", "description": "Ключ (snake_case, на английском)"},
                "value": {"type": "STRING", "description": "Значение (на английском)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "obsidian",
        "description": (
            "Личная база знаний пользователя в Obsidian (markdown-заметки). "
            "Вызывай, когда пользователь просит: запиши/сохрани заметку, добавь в дневник, "
            "«что я записывал про…», найди заметку, прочитай заметку, покажи список заметок. "
            "action=write — новая заметка (title + content); "
            "append_daily — дописать строку в дневник за сегодня (content); "
            "search — найти по базе (query); "
            "read — прочитать заметку по заголовку (title); "
            "list — список заметок (folder — опционально)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "write | append_daily | search | read | list"},
                "title":   {"type": "STRING", "description": "Заголовок заметки (для write / read)"},
                "content": {"type": "STRING", "description": "Текст заметки (для write / append_daily)"},
                "query":   {"type": "STRING", "description": "Поисковый запрос (для search)"},
                "folder":  {"type": "STRING", "description": "Папка внутри vault (опционально)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "set_mode",
        "description": (
            "Активирует один из lifestyle-режимов ДЖАРВИС или сбрасывает в обычный. "
            "Каждый режим открывает релевантные приложения и сайты. "
            "Вызывай когда пользователь говорит: режим учебы, режим работы, режим кино, "
            "режим музыки, обычный режим, пора учиться, пора работать, хочу фильм, включи музыку."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {
                    "type": "STRING",
                    "description": (
                        "study (учеба) | work (работа) | movie (кино) | "
                        "music (музыка) | normal (обычный, сброс)"
                    )
                },
                "preference": {
                    "type": "STRING",
                    "description": (
                        "Опциональная под-опция. "
                        "Для work: design | code | client. "
                        "Для music: energy | calm | focus | power. "
                        "Для movie: название фильма. "
                        "Если не указано — Джарвис задаст уточняющий вопрос."
                    )
                }
            },
            "required": ["mode"]
        }
    },
    {
        "name": "movie_player",
        "description": (
            "Управляет видеоплеером фильмов и сериалов через VK Видео (https://vkvideo.ru/): "
            "запуск фильма на vkvideo.ru, пауза (Space), полный экран (F), "
            "перемотка вперед/назад на 10 сек (←/→), громкость (↑/↓), выход. "
            "Вызывай когда пользователь говорит: включи фильм X, поставь X, фильм X, "
            "пауза, продолжай, перемотай, полный экран, вперёд, назад, громче фильм, тише, выйти из фильма."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "play (запустить фильм на vkvideo.ru) | pause (Space, переключатель) | "
                        "resume (Space) | fullscreen (F) | "
                        "seek_forward (→ 10 сек) | seek_back (← 10 сек) | "
                        "volume_up (громкость +10%) | volume_down (-10%) | "
                        "exit (выход + закрыть вкладку)"
                    )
                },
                "title": {
                    "type": "STRING",
                    "description": "Название фильма для воспроизведения на vkvideo.ru (для action=play)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "window_control",
        "description": (
            "Окна Windows: закрыть/свернуть/развернуть окно (target — программа: «хром», «телеграм»; "
            "без target — окно впереди), переключиться на программу (activate), свернуть все, "
            "рабочий стол, прижать влево/вправо, диспетчер задач, параметры Windows, «Выполнить»."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["close", "minimize", "maximize", "activate", "minimize_all",
                             "show_desktop", "snap_left", "snap_right", "switch",
                             "open_explorer", "task_manager", "settings", "run"],
                },
                "target": {
                    "type": "STRING",
                    "description": "Программа, чьё окно: «хром», «телеграм», «spotify». Для activate — обязательно."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "music_player",
        "description": (
            "Музыка в Spotify (и что играет в Windows). play + query — включить трек/исполнителя/"
            "альбом («включи Believer», «поставь Любэ»); play без query — продолжить; mood + query — "
            "плейлист под настроение («спокойное», «для работы»); pause, resume, next, previous; "
            "now_playing — «что играет», «кто поёт»; volume_* — громкость самого Spotify, только если "
            "сказано «музыку громче/тише» (иначе computer_control)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["play", "mood", "pause", "resume", "next", "previous", "now_playing",
                             "volume_up", "volume_down", "volume_set"],
                },
                "query": {
                    "type": "STRING",
                    "description": "Для play/mood: трек, исполнитель, альбом или настроение — как сказал пользователь",
                },
                "value": {"type": "STRING", "description": "Для volume_set — 0-100; для volume_up/down — шаг"},
                "playlist_url": {"type": "STRING", "description": "Ссылка на плейлист, если пользователь её дал"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "team_collaboration",
        "description": (
            "Управление командной работой и проектами: добавление членов команды, "
            "создание проектов, управление задачами, анализ коммуникаций, "
            "генерация отчётов по командной работе. "
            "Вызывай когда пользователь говорит: добавь в команду, создай проект, "
            "добавь задачу, статус проекта, отчёт по команде, анализ коммуникаций."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "add_member — добавить члена команды (нужны name, role) | "
                        "add_project — создать проект (нужен name) | "
                        "add_task — добавить задачу (нужен project_name, task) | "
                        "project_status — статус проекта (нужен project_name) | "
                        "team_report — отчёт по команде | "
                        "team_suggestions — предложения по командной работе"
                    )
                },
                "name": {
                    "type": "STRING",
                    "description": "Имя (для add_member, add_project, add_task)"
                },
                "role": {
                    "type": "STRING",
                    "description": "Роль (для add_member)"
                },
                "project_name": {
                    "type": "STRING",
                    "description": "Название проекта (для add_task, project_status)"
                },
                "task": {
                    "type": "STRING",
                    "description": "Задача (для add_task)"
                },
                "priority": {
                    "type": "STRING",
                    "description": "Приоритет: high | medium | low (для add_task)"
                },
                # Код читал эти два поля с самого начала, а модели их не объявили —
                # значит проекты создавались без описания, а задачи без исполнителя,
                # и повлиять на это было нельзя никакими словами.
                "description": {
                    "type": "STRING",
                    "description": "Описание проекта (для add_project)"
                },
                "assignee": {
                    "type": "STRING",
                    "description": "Кому поручена задача (для add_task)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Полностью завершает работу ДЖАРВИС. "
            "Вызывай когда пользователь говорит: выключи, закрой, до свидания, пока, стоп, хватит."
        ),
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "calendar",
        "description": (
            "Управление календарём и напоминаниями: добавление событий, просмотр расписания, "
            "удаление событий, обновление времени, добавление напоминаний. "
            "Поддерживает локальный календарь и Google Calendar (опционально). "
            "Вызывай когда пользователь говорит: добавь встречу, создай событие, какие дела на сегодня, "
            "покажи календарь, напомни мне, перенеси встречу, отмени событие, расписание на завтра."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "add_event — добавить событие (нужны title, datetime) | "
                        "get_events — показать события (date_range: today/tomorrow/week/all) | "
                        "delete_event — удалить событие (нужен title_or_id) | "
                        "update_event — обновить событие (нужен title_or_id, опционально new_datetime, new_duration) | "
                        "add_reminder — добавить напоминание (нужны text, datetime) | "
                        "todays_schedule — расписание на сегодня | "
                        "sync_google — синхронизация с Google Calendar"
                    )
                },
                "title": {
                    "type": "STRING",
                    "description": "Название события (для add_event)"
                },
                "datetime": {
                    "type": "STRING",
                    "description": "Дата и время (русский текст: 'завтра в 14:00', 'через 30 минут')"
                },
                "duration": {
                    "type": "STRING",
                    "description": "Длительность (например: '1 час', '30 минут')"
                },
                "description": {
                    "type": "STRING",
                    "description": "Описание события (для add_event)"
                },
                "location": {
                    "type": "STRING",
                    "description": "Место (для add_event)"
                },
                "date_range": {
                    "type": "STRING",
                    "description": "Период для get_events: today/tomorrow/week/all"
                },
                "title_or_id": {
                    "type": "STRING",
                    "description": "Название или ID события (для delete_event, update_event)"
                },
                "new_datetime": {
                    "type": "STRING",
                    "description": "Новое дата/время (для update_event)"
                },
                "new_duration": {
                    "type": "STRING",
                    "description": "Новая длительность (для update_event)"
                },
                "text": {
                    "type": "STRING",
                    "description": "Текст напоминания (для add_reminder)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "translation",
        "description": (
            "Перевод текста и речи на разные языки в реальном времени. "
            "Поддерживает множества языков, контекстную память и режим изучения языков. "
            "Вызывай когда пользователь говорит: переведи на английский, переведи на французский, "
            "как сказать по-испански, включи режим изучения английского, найди перевод слова."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "translate — перевести текст (нужны text, target_language) | "
                        "history — показать историю переводов (date_range: today/all) | "
                        "search — найти перевод (нужен query) | "
                        "enable_learning — включить режим изучения (нужен language) | "
                        "disable_learning — отключить режим изучения | "
                        "set_default_language — установить язык по умолчанию (нужен language) | "
                        "enable_language — включить язык (нужен language) | "
                        "disable_language — отключить язык (нужен language)"
                    )
                },
                "text": {
                    "type": "STRING",
                    "description": "Текст для перевода (для action=translate)"
                },
                "target_language": {
                    "type": "STRING",
                    "description": (
                        "Целевой язык: english, french, german, spanish, chinese, japanese, "
                        "italian, portuguese (для action=translate, enable_learning, set_default_language)"
                    )
                },
                "query": {
                    "type": "STRING",
                    "description": "Поисковый запрос (для action=search)"
                },
                "date_range": {
                    "type": "STRING",
                    "description": "Период для истории: today/all (для action=history)"
                },
                "language": {
                    "type": "STRING",
                    "description": "Язык (для action=enable_learning, disable_learning, set_default_language)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "look_at_screen",
        "description": (
            "Захватывает текущий экран или активное окно и анализирует его с помощью компьютерного зрения. "
            "Вызывай когда пользователь просит посмотреть на экран, найти ошибку в коде, оценить дизайн, "
            "прочитать что написано на мониторе, или говорит 'что на экране'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {
                    "type": "STRING",
                    "description": "Что конкретно нужно проанализировать или найти на экране"
                },
                "source": {
                    "type": "STRING",
                    "description": "Источник: 'screen' (весь монитор) или 'active_window' (только активное окно)"
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "look_at_camera",
        "description": (
            "Делает снимок с веб-камеры и анализирует окружающую обстановку. "
            "Вызывай когда пользователь просит взглянуть через камеру, посмотреть на него или показать предмет."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {
                    "type": "STRING",
                    "description": "Вопрос или задача для анализа изображения с камеры"
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "send_to_telegram",
        "description": (
            "Отправляет текстовое сообщение или скриншот экрана в личный Telegram-чат пользователя. "
            "Вызывай когда пользователь просит скинуть ссылку, отправить заметку или скриншот в телеграм."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {
                    "type": "STRING",
                    "description": "Текст сообщения для отправки"
                },
                "send_screenshot": {
                    "type": "BOOLEAN",
                    "description": "True, если нужно прикрепить снимок экрана"
                }
            },
            "required": []
        }
    },
    {
        "name": "morning_briefing",
        "description": (
            "Дневной брифинг — погода, события на сегодня, главные новости. "
            "Приветствие зависит от времени суток: доброе утро (6-12), добрый день (12-18), "
            "добрый вечер (18-22), доброй ночи (22-6). "
            "Вызывай когда пользователь говорит 'брифинг', 'что сегодня', "
            "'доброе утро', 'добрый день', 'введи в курс дня'. "
            "Также вызывай автоматически при старте сессии если сейчас утро (6-10)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "sleep_timer",
        "description": (
            "Управляет умным таймером сна с подтверждением голосом и автовыключением ноутбука/ПК. "
            "По истечении времени Джарвис спросит разрешение выключить ПК. Если пользователь молчит 15 сек — выключает. "
            "Вызывай когда пользователь говорит: 'через полчаса буду спать', 'через 30 минут спать', "
            "'таймер сна на 45 минут', 'через час выключи ноут', 'отмени таймер сна', 'сколько осталось до сна'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "set (установить таймер) | cancel (отменить) | status (проверить оставшееся время) | confirm (пользователь сказал «да» на вопрос о выключении)"
                },
                "duration_minutes": {
                    "type": "NUMBER",
                    "description": "Время таймера в минутах (например, 30 для получаса, 60 для часа, 45, 15)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "switch_voice",
        "description": (
            "Переключает голос Джарвиса между киношным голосом Пола Беттани ('fish') и встроенным быстрым голосом ('gemini'). "
            "Вызывай когда пользователь просит сменить голос: 'включи голос из фильма', 'говори как в кино', 'включи оригинальный голос', "
            "'говори голосом Пола Беттани', 'верни быстрый голос', 'переключи на Gemini'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {
                    "type": "STRING",
                    "description": "'fish' для канонического киношного голоса (Fish Audio) или 'gemini' для стандартного быстрого голоса Charon",
                    "enum": ["fish", "gemini"]
                }
            },
            "required": ["provider"]
        }
    },
]


# ─── Ядро ДЖАРВИС ─────────────────────────────────────────────────────────────
class Jarvis:
    def __init__(self, ui: JarvisUI):
        self.ui = ui
        self.session = None
        self.audio_in_queue  = None
        self.out_queue       = None
        self._loop           = None
        self._is_speaking    = False
        self._speaking_lock  = threading.Lock()
        self._active_synth_tasks = 0
        self._speaker_meter  = None   # см. _listen_audio: не слушаем свои динамики
        self._turn_done_event: asyncio.Event | None = None
        self._awake_until    = 0.0    # см. _WAKE_MODE: до какого момента идёт разговор
        self._followups_left = 0      # сколько реплик без имени ещё продолжат разговор
        self._rearm_after_speech = False
        self._fish_task: asyncio.Task | None = None
        self._echo_guard_until = 0.0  # см. _ECHO_TAIL_SEC
        self._resume_handle: str | None = None  # возобновление сессии после разрыва

        # Новый мозг ДЖАРВИС
        self.user_profile = UserProfile(DATA_DIR)
        self.initiative_engine = InitiativeEngine()
        self.proactive_engine = ProactiveEngine(DATA_DIR)
        self.team_engine = TeamCollaborationEngine(DATA_DIR)
        self.last_user_text = ""
        self._user_turn = 0      # номер последней реплики пользователя (для подтверждений)

        # Секундомер голосового хода. Пишет в лог задержку от конца речи до
        # первого звука ответа при JARVIS_DEBUG_UI=1.
        self._latency = LatencyTracker(
            sink=self.ui.write_log if os.getenv("JARVIS_DEBUG_UI") == "1" else None
        )

        # 2-Stage KWS: ловит «Джарвис», когда микрофонный шлюз закрыт музыкой.
        #
        # AEC здесь СОЗНАТЕЛЬНО не поднимается. Раньше строка `self._aec =
        # AECPipeline()` тут была, а в логе значилось «AEC подключён» — но поле
        # больше нигде не читалось: эхоподавление в живом тракте не работало,
        # лог вводил в заблуждение при разборе проблем со звуком.
        # Чтобы включить его по-настоящему, микрофонному циклу нужен опорный
        # поток колонок (WASAPI loopback) — сейчас его нет: speaker_meter.py
        # отдаёт только скалярный уровень, не PCM. См. core/audio_capture.py.
        # Список программ для «открой …» — собирается в фоне, пока грузится
        # остальное (Get-StartApps занимает пару секунд).
        try:
            from core import win_apps
            win_apps.warm_up()
        except Exception as exc:
            logger.debug("Индекс программ: %s", exc)

        # Локальное слово «Джарвис». Запускается в _listen_audio: модели
        # нужен событийный цикл, чтобы будить Джарвиса из своего потока.
        self._local_wake: LocalWake | None = None
        self._wake_ring = collections.deque(maxlen=_WAKE_PREROLL_FRAMES)

        self.ui.on_text_command = self._on_text_command

        # Глобальные системные горячие клавиши (F8 / Ctrl+Shift+J — вызов, Ctrl+Shift+M — мьют)
        try:
            from core.hotkey_manager import GlobalHotkeyManager
            self._hotkey_mgr = GlobalHotkeyManager(
                on_wake=self._on_hotkey_wake,
                on_mute=self._on_hotkey_mute,
            )
            self._hotkey_mgr.start()
        except Exception as _e:
            logger.debug("Hotkey init note: %s", _e)
            self._hotkey_mgr = None

        # Локальный Telegram-бот — только по JARVIS_AUTOSTART_BOT=1 (см. метод)
        self._telegram_proc = self._start_telegram_bot()
        atexit.register(self.cleanup)

    def _on_hotkey_wake(self):
        """Реакция на глобальный хоткей F8 / Ctrl+Shift+J из любого приложения или игры."""
        logger.info("[Hotkey] Нажата горячая клавиша вызова Джарвиса (F8)")
        if self.ui.muted:
            self.ui.toggle_mute()
        self.wake()
        self.ui.write_log("SYS: ⚡ Вызов по горячей клавише F8.")
        self.ui.bring_to_front()
        try:
            from core.ducking_controller import ducking_controller
            ducking_controller.duck()
        except Exception:
            pass

    def _on_hotkey_mute(self):
        """Реакция на глобальный хоткей Ctrl+Shift+M из любого приложения."""
        self.ui.toggle_mute()

    def _start_telegram_bot(self):
        """Запускает Telegram-бота в отдельном фоновом процессе при наличии токена."""
        # Только по явному JARVIS_AUTOSTART_BOT=1. Боевой бот живёт на Hugging
        # Face по вебхуку; локальный polling-двойник снимал его вебхук,
        # _webhook_keeper через 90 с ставил обратно — и два бота по очереди
        # отбирали друг у друга сообщения. Со стороны: «бот не работает».
        # В собранном .exe sys.executable — сам Джарвис: вместо бота
        # запустилась бы его копия, а та — следующая.
        if getattr(sys, "frozen", False) or os.getenv("JARVIS_AUTOSTART_BOT", "0") != "1":
            return None
        try:
            from telegram_bot.config import load as load_config
            cfg = load_config(require_bot=False)
            if not cfg.telegram_token:
                return None
            # Бот уже живёт в облаке (Render/HF, вебхук). Локальный polling
            # снял бы этот вебхук и увёл обновления у облачного бота.
            if cfg.pc_link_url:
                logger.info("Telegram-бот работает в облаке (%s) — локально не запускаю",
                            cfg.pc_link_url)
                return None
            bot_script = BASE_DIR / "telegram_bot" / "bot.py"
            if not bot_script.exists():
                return None
            proc = subprocess.Popen(
                [sys.executable, str(bot_script)],
                cwd=str(BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            logger.info("Telegram-бот (@Aimyjarvisbot) запущен в фоне (PID %d)", proc.pid)
            self.ui.write_log("SYS: 📱 Telegram-бот (@Aimyjarvisbot) запущен в фоне.")
            return proc
        except Exception as exc:
            logger.warning("Не удалось запустить Telegram-бот: %s", exc)
            return None

    def cleanup(self):
        """Корректное освобождение системных ресурсов и дочерних процессов."""
        try:
            import core.ducking_controller as dc
            if dc._singleton is not None:
                dc._singleton.close()     # вернуть громкость другим программам
        except Exception:
            pass
        if hasattr(self, "_hotkey_mgr") and self._hotkey_mgr:
            try:
                self._hotkey_mgr.stop()
            except Exception:
                pass
            self._hotkey_mgr = None
        if hasattr(self, "_telegram_proc") and self._telegram_proc:
            try:
                self._telegram_proc.terminate()
            except Exception:
                pass
            self._telegram_proc = None

    # ── Текстовый ввод ────────────────────────────────────────────────────────
    def _send_text_to_session(self, text: str):
        """Отправляет текстовое сообщение в Live-сессию с корректной структурой типов."""
        if not text:
            return
        if not self._loop or not self.session or not self._loop.is_running():
            # Раньше текст молча пропадал — команда «ушла», ответа нет.
            logger.warning("Нет связи с Gemini — сообщение не отправлено: %s", text[:60])
            self.ui.write_log("SYS: нет связи с Gemini, повторите через пару секунд")
            return
        content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=text)],
        )
        fut = asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns=[content],
                turn_complete=True,
            ),
            self._loop,
        )

        def _report(f):
            if not f.cancelled() and f.exception():
                logger.warning("Сообщение в Gemini не ушло: %s", f.exception())
                self.ui.write_log("SYS: сообщение не отправилось — повторите")
        fut.add_done_callback(_report)

    def _on_text_command(self, text: str):
        # Normalize text before sending
        text = self._normalize_input_text(text)
        if text:
            self.wake()
            self.last_user_text = text
            self._user_turn += 1
            self._send_text_to_session(text)

    # ── Обращение по имени ────────────────────────────────────────────────────
    def wake(self):
        """Открывает окно разговора: следующие _AWAKE_SEC Джарвис отвечает
        без имени. Зовут: имя в речи, F8, текстовая команда, сам Джарвис."""
        was_awake = self.is_awake()
        self._followups_left = _FOLLOWUPS
        self._arm()
        if not was_awake:
            logger.info("Джарвис слушает (окно %.0f с)", _AWAKE_SEC)
            self._show_listen_state()

    def _on_local_wake(self, put):
        """Локальный детектор услышал имя (зовётся в событийном цикле).
        Будим, приглушаем музыку и отдаём в Gemini звук с самого имени —
        иначе «Джарвис, открой ютуб» дошло бы как «…ютуб»."""
        if self.ui.muted:
            return
        self.wake()
        try:
            from core.ducking_controller import ducking_controller
            ducking_controller.duck()
        except Exception:
            pass
        while self._wake_ring:
            put({"data": self._wake_ring.popleft(), "mime_type": "audio/pcm"})

    def _arm(self):
        self._awake_until = time.monotonic() + _AWAKE_SEC
        # Окно отсчитывается от конца ответа — один раз на выданное окно.
        self._rearm_after_speech = True

    def _continue_conversation(self):
        """Реплика без имени в окне разговора продлевает его ограниченное
        число раз. Раньше каждый ответ продлевал окно заново, и речь из
        телевизора держала Джарвиса «проснувшимся» без конца."""
        if self._followups_left > 0:
            self._followups_left -= 1
            self._arm()

    def is_awake(self) -> bool:
        return _WAKE_MODE == "always_on" or time.monotonic() < self._awake_until

    def _normalize_input_text(self, text: str) -> str:
        """Normalize user input text for better intent parsing."""
        return _clean_dialog_text(text)

    # ── Управление состоянием ─────────────────────────────────────────────────
    def set_speaking(self, value: bool):
        with self._speaking_lock:
            was = self._is_speaking
            self._is_speaking = value
        if was and not value:
            self._echo_guard_until = time.monotonic() + _ECHO_TAIL_SEC
            # Окно разговора отсчитывается от конца ответа, а не от начала:
            # иначе длинный ответ съедал бы его целиком. Только раз на окно.
            if self._rearm_after_speech and self.is_awake():
                self._rearm_after_speech = False
                self._awake_until = time.monotonic() + _AWAKE_SEC
        if value:
            self.ui.set_state("SPEAKING")
            try:
                from core.ducking_controller import ducking_controller, DuckingState
                ducking_controller.set_state(DuckingState.SPEAKING)
            except Exception:
                pass
        else:
            if not self.ui.muted:
                self._show_listen_state(force=True)
            # Вернуть звук — всегда, и при выключенном микрофоне: раньше
            # Ctrl+M во время ответа оставлял музыку приглушённой навсегда.
            self._release_ducking()

    def _release_ducking(self):
        try:
            from core.ducking_controller import ducking_controller, DuckingState
            ducking_controller.set_state(DuckingState.RESTORING)
        except Exception:
            pass

    def _show_listen_state(self, force: bool = False):
        """«СЛУШАЕТ» — идёт разговор и имя не нужно; «ОЖИДАЕТ» — ждёт «Джарвис».
        Без этого не видно, почему он молчит: спит или не расслышал."""
        awake = self.is_awake()
        if not force and awake == getattr(self, "_shown_awake", None):
            return
        self._shown_awake = awake
        with self._speaking_lock:
            speaking = self._is_speaking
        if not speaking and not self.ui.muted:
            self.ui.set_state("LISTENING" if awake else "IDLE")

    def speak(self, text: str):
        """Просит Джарвиса произнести текст.

        Текст уходит в сессию репликой ПОЛЬЗОВАТЕЛЯ. Голая фраза «Сэр,
        произошла ошибка…» читалась моделью как слова собеседника, и Джарвис
        отвечал на неё — разговаривал сам с собой. Поэтому — указание.
        """
        if not text:
            return
        if not text.lstrip().startswith("["):
            text = f"[СИСТЕМА: произнеси пользователю, своими словами не дополняй: «{text}»]"
        self.wake()
        self._send_text_to_session(text)

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:100]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Сэр, произошла ошибка в модуле {tool_name}. {short}")

    # ── Конфигурация Gemini ───────────────────────────────────────────────────
    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime
        memory    = load_memory()
        mem_str   = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %d %B %Y — %H:%M")
        time_ctx = (
            f"[ТЕКУЩЕЕ ВРЕМЯ И ДАТА]\n"
            f"Сейчас: {time_str}\n"
            f"Используй для точного расчёта напоминаний.\n\n"
        )

        # Текущий режим (если активен) — для контекста при перезапуске
        mode_state = get_current_mode()
        mode_ctx = ""
        if mode_state.get("mode") and mode_state["mode"] != "normal":
            pref = mode_state.get("preference", "")
            pref_str = f" / {pref}" if pref else ""
            mode_ctx = (
                f"[ТЕКУЩИЙ РЕЖИМ]\n"
                f"Активен режим: {mode_state['mode']}{pref_str}\n\n"
            )

        # Профиль пользователя (новый мозг)
        profile_str = self.user_profile.format_for_prompt()

        parts = [time_ctx]
        if mode_ctx:
            parts.append(mode_ctx)
        if profile_str:
            parts.append(profile_str)
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},  # Без language_code (Pydantic не принимает)
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOLS}],
            # Голосовая сессия без сжатия контекста живёт ~15 минут, потом
            # сервер её закрывает. Скользящее окно снимает лимит, а handle
            # возобновления переносит разговор через разрыв: раньше после
            # каждого переподключения Джарвис начинал с чистого листа.
            session_resumption=types.SessionResumptionConfig(handle=self._resume_handle),
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            # Когда считать, что человек договорил.
            #
            # По умолчанию модель ждёт около секунды тишины — отсюда пауза
            # перед каждым ответом. Порог опущен до 400 мс и включена высокая
            # чувствительность к концу речи: ответ начинается почти сразу.
            # Ниже 300 мс модель начинает перебивать на паузах внутри фразы.
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    silence_duration_ms=_VAD_SILENCE_MS,
                    prefix_padding_ms=_VAD_PREFIX_MS,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                ),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"  # Глубокий мужской голос
                    )
                )
            ),
            # Раздумья стоили 2.8 секунды молчания перед каждым ответом —
            # больше, чем весь остальной круг вместе взятый (см. константу).
            thinking_config=types.ThinkingConfig(
                thinking_budget=_THINKING_BUDGET,
            ),
        )

    # ── Выполнение инструментов ───────────────────────────────────────────────
    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        logger.info(f"🔧 Tool: {name} {args}")
        self.ui.set_state("THINKING")

        # Необратимое — только после подтверждения.
        #
        # Здесь стоял словарь «критических действий», комментарий обещал
        # подтверждение, а кода не было: выключение компьютера и удаление
        # файлов выполнялись сразу, с одной строчкой в лог. И это не теория —
        # микрофон отдавал в модель всё, что слышал в комнате, включая музыку,
        # так что «выключи компьютер» могло родиться из ниоткуда, а
        # computer_settings делает shutdown /s /t 5 по-настоящему.
        #
        # Блокировка экрана осталась без подтверждения: она безвредна и
        # обратима, а спрашивать о ней каждый раз — раздражать зря.
        # Подтверждение засчитывается, только если пользователь ПОСЛЕ вопроса
        # сам сказал «да» — новой репликой и именно на этот вызов. Раньше хватало
        # повторного вызова того же инструмента в 90 секунд: модель могла
        # «подтвердить» сама, вторым вызовом в той же пачке.
        if _is_destructive(name, args):
            pending = self._pending_destructive
            key = _args_key(name, args)
            fresh = bool(
                pending and pending[0] == key
                and (time.time() - pending[1]) < _CONFIRM_WINDOW_SEC
                and self._user_turn > pending[2]
                and _is_affirmative(self.last_user_text)
            )
            if not fresh:
                self._pending_destructive = (key, time.time(), self._user_turn)
                logger.warning("Требую подтверждения: %s/%s", name, _action_of(args))
                self.ui.write_log(f"SYS: жду подтверждения — {name}/{_action_of(args)}")
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": (
                        "НЕ ВЫПОЛНЕНО — нужно подтверждение. Переспроси пользователя вслух, "
                        "точно ли он хочет это сделать, и вызови инструмент повторно "
                        "ТОЛЬКО если он ответит утвердительно."
                    )},
                )
            self._pending_destructive = None
            logger.warning("Подтверждено, выполняю: %s/%s", name, _action_of(args))

        # Сохранение в память (без задержки)
        if name == "save_to_memory":
            cat = args.get("category", "notes")
            key = args.get("key", "")
            val = args.get("value", "")
            if key and val:
                # Запись на диск — в поток: этот же цикл гонит звук в Live API.
                await asyncio.to_thread(update_memory, {cat: {key: {"value": val}}})
                print(f"[Память] 💾 {cat}/{key} = {val}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop = asyncio.get_event_loop()
        result = "Готово."

        try:
            # ── Инструмент: открыть приложение ──────────────────────
            if name == "open_app":
                r = await loop.run_in_executor(
                    None, lambda: open_app(parameters={"app_name": args.get("app_name", "")},
                                           player=self.ui)
                )
                result = r or "Открыл."

            # ── Инструмент: погода ───────────────────────────────────
            elif name == "weather":
                r = await loop.run_in_executor(
                    None, lambda: weather_action(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: поиск ────────────────────────────────────
            elif name == "web_search":
                r = await loop.run_in_executor(
                    None, lambda: web_search(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: база знаний Obsidian ─────────────────────
            elif name == "obsidian":
                r = await loop.run_in_executor(
                    None, lambda: obsidian_action(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: управление компьютером ───────────────────
            elif name == "computer_control":
                # Действия схемы computer_settings понимает напрямую.
                params = {"action": args.get("action", ""), "value": args.get("value", "")}
                r = await loop.run_in_executor(
                    None, lambda: computer_settings(parameters=params, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: браузер ──────────────────────────────────
            elif name == "browser":
                r = await loop.run_in_executor(
                    None, lambda: browser_control(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: файлы ────────────────────────────────────
            elif name == "files":
                r = await loop.run_in_executor(
                    None, lambda: file_controller(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: Vision (анализ экрана и камеры) ─────────
            elif name in ("look_at_screen", "look_at_camera", "vision_review"):
                from actions.vision import vision_action
                source = "camera" if name == "look_at_camera" else args.get("source", "screen")
                args["source"] = source
                r = await loop.run_in_executor(None, lambda: vision_action(args))
                result = r or "Анализ изображения завершен."

            # ── Инструмент: отправка в Telegram ──────────────────────
            elif name in ("send_to_telegram", "telegram_send"):
                from actions.telegram_sender import telegram_sender_action
                r = await loop.run_in_executor(None, lambda: telegram_sender_action(args))
                result = r or "Отправлено в Telegram."

            # ── Инструмент: режимы (study/work/movie/music) ──────────
            elif name == "set_mode":
                r = await loop.run_in_executor(
                    None, lambda: set_mode(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: продвинутый кино-плеер ───────────────────
            elif name == "movie_player":
                r = await loop.run_in_executor(
                    None, lambda: movie_player(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: Spotify music-плеер ──────────────────────
            elif name == "music_player":
                r = await loop.run_in_executor(
                    None, lambda: spotify_player(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: управление окнами Windows ────────────────
            elif name == "window_control":
                r = await loop.run_in_executor(
                    None, lambda: window_control(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: командная работа ─────────────────────────
            elif name == "team_collaboration":
                action = args.get("action", "")
                team_action = args.get("name", "")
                role = args.get("role", "")
                project_name = args.get("project_name", "")
                task = args.get("task", "")
                priority = args.get("priority", "medium")

                if action == "add_member":
                    if team_action and role:
                        success = self.team_engine.add_team_member(team_action, role)
                        result = f"Добавил {team_action} в команду." if success else "Ошибка добавления."
                    else:
                        result = "Укажите имя и роль члена команды."

                elif action == "add_project":
                    if team_action:
                        success = self.team_engine.add_project(team_action, args.get("description", ""))
                        result = f"Создал проект '{team_action}'." if success else "Ошибка создания."
                    else:
                        result = "Укажите название проекта."

                elif action == "add_task":
                    if project_name and task:
                        success = self.team_engine.add_task(project_name, task, priority, args.get("assignee", ""))
                        result = f"Добавил задачу в '{project_name}'." if success else "Ошибка добавления."
                    else:
                        result = "Укажите проект и задачу."

                elif action == "project_status":
                    if project_name:
                        status = self.team_engine.get_project_status(project_name)
                        if status:
                            result = f"Проект '{status['name']}': {status['completed']}/{status['total_tasks']} задач, прогресс {status['progress']:.0f}%."
                        else:
                            result = f"Проект '{project_name}' не найден."
                    else:
                        result = "Укажите название проекта."

                elif action == "team_report":
                    result = self.team_engine.generate_team_report()

                elif action == "team_suggestions":
                    result = self.team_engine.generate_suggestions()
                else:
                    result = "Не понял команду командной работы."

            # ── Инструмент: календарь ──────────────────────────────────
            elif name == "calendar":
                r = await loop.run_in_executor(
                    None, lambda: calendar(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: перевод ─────────────────────────────────
            #
            # Раньше все настройки языков правились здесь руками по ключам
            # "enabled_languages" и "default_language", которых в файле нет:
            # включение языка гарантированно падало с KeyError, а смена языка
            # по умолчанию писала мимо схемы и бодро рапортовала об успехе.
            # Схемой владеет translation_manager — операции живут там.
            #
            # Плюс сам перевод — это сетевой запрос к Gemini, а он выполнялся
            # прямо в событийном цикле, который в это же время гонит микрофон
            # в Live API. Всё, что лезет в сеть или на диск, уходит в поток.
            elif name == "translation":
                action = args.get("action", "")
                lang = args.get("language") or args.get("target_language") or ""

                if action == "translate":
                    text = args.get("text", "")
                    target_lang = args.get("target_language", "english")
                    if text:
                        translated = await loop.run_in_executor(
                            None, lambda: translate_text(text, target_lang)
                        )
                        result = f"Перевод на {target_lang}: {translated}"
                    else:
                        result = "Укажите текст для перевода."

                elif action == "history":
                    date_range = args.get("date_range", "all")
                    history = await loop.run_in_executor(
                        None, lambda: get_translation_history(date_range)
                    )
                    if history:
                        result = f"История переводов ({date_range}): {len(history)} записей. Последний: {history[0].get('translation', 'N/A')}"
                    else:
                        result = f"История переводов ({date_range}) пуста."

                elif action == "search":
                    query = args.get("query", "")
                    if query:
                        results = await loop.run_in_executor(
                            None, lambda: search_translations(query)
                        )
                        if results:
                            result = f"Найдено переводов: {len(results)}. Первый: {results[0].get('translation', 'N/A')}"
                        else:
                            result = f"Переводы по запросу '{query}' не найдены."
                    else:
                        result = "Укажите поисковый запрос."

                elif action == "enable_learning":
                    code = await loop.run_in_executor(
                        None, lambda: set_learning_mode(True, lang or "english")
                    )
                    result = (f"Включил режим изучения: {code}." if code
                              else f"Не знаю язык '{lang}' — назовите другой.")

                elif action == "disable_learning":
                    ok = await loop.run_in_executor(None, lambda: set_learning_mode(False))
                    result = ("Отключил режим изучения языков." if ok is not None
                              else "Не смог сохранить настройки изучения.")

                elif action == "set_default_language":
                    code = await loop.run_in_executor(
                        None, lambda: set_default_language(lang)
                    )
                    result = (f"Язык по умолчанию теперь {code}." if code
                              else f"Не знаю язык '{lang}' — назовите другой.")

                elif action in ("enable_language", "disable_language"):
                    on = action == "enable_language"
                    code = await loop.run_in_executor(
                        None, lambda: set_language_enabled(lang, on)
                    )
                    if not code:
                        result = f"Не знаю язык '{lang}' — назовите другой."
                    else:
                        result = f"{'Включил' if on else 'Отключил'} язык: {code}."
                else:
                    result = "Не понял команду перевода."

            # ── Инструмент: утренний брифинг ─────────────────────────────
            elif name == "morning_briefing":
                from actions.morning_briefing import morning_briefing
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: morning_briefing(args, player=self.ui)
                )

            # ── Инструмент: умный таймер сна ──────────────────────────────
            elif name == "sleep_timer":
                from actions.sleep_timer import sleep_timer
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: sleep_timer(args, player=self.ui, bot=self)
                )

            # ── Инструмент: переключение голоса ───────────────────────────
            elif name == "switch_voice":
                provider = args.get("provider", "fish").strip().lower()
                if provider not in ("fish", "gemini"):
                    provider = "fish"
                set_voice_provider(provider)
                rus_name = "киношный дубляж Пола Беттани (Fish Audio)" if provider == "fish" else "стандартный быстрый голос Gemini"
                self.ui.write_log(f"SYS: Голос переключён на {rus_name}")
                result = {"status": "success", "voice": provider, "message": f"Голос переключён на {rus_name}"}

            # ── Инструмент: выключить ────────────────────────────────
            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Завершение работы...")
                self.speak("До свидания, сэр. Отключаюсь.")
                def _shutdown():
                    import time
                    import os
                    time.sleep(1.5)
                    self.cleanup()     # os._exit не зовёт atexit: иначе звук остался бы приглушён
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Неизвестный инструмент: {name}"

        except Exception as e:
            # Ошибку модель получает в ответе инструмента и сама скажет о ней.
            # Раньше здесь ещё и speak_error() слал отдельную реплику в сессию —
            # Джарвис отвечал дважды, второй раз «сам себе».
            result = f"Ошибка инструмента '{name}': {e}"
            traceback.print_exc()
            self.ui.write_log(f"ERR: {name} — {str(e)[:100]}")

        # Профиль и прогнозы пишутся на диск — в поток, и их сбой не должен
        # рвать сессию: без ответа на вызов модель ждёт его и после реконнекта.
        try:
            await asyncio.to_thread(self._remember_tool_use, name, args)
        except Exception as exc:
            logger.debug("Профиль не обновлён: %s", exc, exc_info=True)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[ДЖАРВИС] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(id=fc.id, name=name, response={"result": result})

    def _remember_tool_use(self, name: str, args: dict):
        """Обновление контекста (новый мозг)."""
        if name not in ("movie_player", "music_player", "set_mode"):
            return
        activity = name
        if name == "movie_player" and args.get("action", "") == "play":
            title = args.get("title", "")
            if title:
                self.user_profile.add_to_history("recent_movies", title)
                activity = f"watching_movie: {title}"
        elif name == "music_player" and args.get("action", "") == "play":
            query = args.get("query", "")
            if query:
                self.user_profile.add_to_history("recent_music", query)
                activity = f"listening_music: {query}"
        elif name == "set_mode":
            activity = f"mode_{args.get('mode', '')}"

        self.user_profile.update_context(activity=activity)

        # Запись действия для прогнозирования (новый мозг)
        context = {
            "time": datetime.now().strftime("%H:%M"),
            "emotion": self.user_profile.get_context().get("last_emotion", "neutral"),
            "mode": get_current_mode().get("mode", "normal")
        }
        self.proactive_engine.record_action(name, context)

    # ── Отправка аудио на сервер ──────────────────────────────────────────────
    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    # ── Захват микрофона ──────────────────────────────────────────────────────

    def _note_gate(self, reason: str | None):
        """Копит причины, по которым кадры не уезжают, и раз в несколько
        секунд пишет сводку. Зовётся из аудио-колбэка — только счётчики.

        Без этого «Джарвис меня не слышит» неотличимо от «Джарвис завис»:
        все три отказа в callback молчаливые, и глухой микрофон выглядит
        ровно как исправный.
        """
        now = time.monotonic()
        if reason is None:
            self._gate_passed = getattr(self, "_gate_passed", 0) + 1
        else:
            counts = getattr(self, "_gate_counts", None)
            if counts is None:
                counts = self._gate_counts = {}
            counts[reason] = counts.get(reason, 0) + 1

        last = getattr(self, "_gate_reported_at", 0.0)
        if now - last < _GATE_REPORT_SEC:
            return
        self._gate_reported_at = now

        counts = getattr(self, "_gate_counts", {}) or {}
        passed = getattr(self, "_gate_passed", 0)
        self._gate_counts, self._gate_passed = {}, 0
        if not counts or passed:
            return          # что-то доезжает — значит слух работает
        top = max(counts.items(), key=lambda kv: kv[1])
        if top[0].startswith("тихо"):
            logger.debug("Микрофон: тишина в комнате (RMS < %s, %d кадров)", MIC_RMS_THRESHOLD, top[1])
        else:
            logger.info("Микрофон гейт: %s (%d кадров)", top[0], top[1])

    def _push_level(self, value: float):
        """Отдаёт громкость окну. Зовётся из аудио-потока, поэтому дёшево и
        молча: замер для красоты не имеет права ни тормозить звук, ни падать."""
        try:
            self.ui.set_level(value)
        except Exception as exc:
            logger.debug("Уровень в HUD не ушёл: %s", exc, exc_info=True)

    def _is_loud_enough(self, indata) -> bool:
        """Пропускать ли кадр в облако.

        Раньше в Gemini Live уходил КАЖДЫЙ кадр с микрофона, пока Джарвис не
        говорит сам: тишина, шум вентилятора, разговоры в комнате — всё
        непрерывным потоком. Это и квоту жгло, и в облако уезжало то, что туда
        никто не отправлял осознанно.

        Порог с «хвостом»: после громкого кадра ещё несколько тихих проходят,
        иначе обрезаются окончания слов.
        """
        try:
            import numpy as np
            rms = float(np.sqrt(np.mean(np.square(indata.astype(np.float32)))))
        except Exception:
            self._frame_was_loud = True
            return True          # не смогли посчитать — лучше пропустить, чем оглохнуть

        # HUD дышит этим числом. Раньше он «реагировал» на random.uniform и
        # выглядел одинаково в тишине и на крике. _MIC_FULL_SCALE — не предел
        # int16, а громкость обычной речи в метре от ноутбука: масштабируя по
        # 32767, мы получили бы почти неподвижную полоску.
        self._push_level(min(1.0, rms / _MIC_FULL_SCALE))
        if rms >= MIC_RMS_THRESHOLD:
            self._quiet_frames = 0
            self._frame_was_loud = True
            return True
        self._quiet_frames = getattr(self, "_quiet_frames", MIC_HANGOVER_FRAMES) + 1
        self._frame_was_loud = False
        return self._quiet_frames <= MIC_HANGOVER_FRAMES

    async def _listen_audio(self):
        print("[ДЖАРВИС] 🎤 Микрофон запущен")
        loop = asyncio.get_event_loop()

        if self._speaker_meter is None and _IGNORE_SPEAKERS:
            from speaker_meter import SpeakerMeter
            meter = SpeakerMeter()
            if meter.start():
                self._speaker_meter = meter
                logger.info("Speaker meter started successfully (loopback active)")
            else:
                logger.info("Speaker meter unavailable, capturing all audio")

        def _put_nowait_safe(item):
            try:
                self.out_queue.put_nowait(item)
            except asyncio.QueueFull:
                pass  # Drop audio frame silently to avoid flooding event loop

        if self._local_wake is None and _WAKE_MODE == "wake_word":
            def _heard(text: str):
                loop.call_soon_threadsafe(self._on_local_wake, _put_nowait_safe)
            wake = LocalWake(_heard)
            if await asyncio.to_thread(wake.start):
                self._local_wake = wake
                self.ui.write_log("SYS: слово «Джарвис» слушается на компьютере — до него звук никуда не уходит")
            else:
                self._local_wake = False     # не пробовать при каждом переподключении
                self.ui.write_log("SYS: нет модели слова «Джарвис» — слушаю через Gemini")

        preroll = collections.deque(maxlen=10)

        def callback(indata, frames, time_info, status):
            self._mic_last_cb = time.monotonic()
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if jarvis_speaking or time.monotonic() < self._echo_guard_until:
                self._note_gate("Джарвис говорит сам")
                preroll.clear()
                return
            if self.ui.muted:
                self._note_gate("микрофон выключен (Ctrl+M)")
                preroll.clear()
                return

            pcm_bytes = indata.tobytes()

            # Спит — звук только в локальный детектор имени, в облако ничего.
            if self._local_wake and not self.is_awake():
                self._wake_ring.append(pcm_bytes)
                self._local_wake.feed(pcm_bytes)
                self._is_loud_enough(indata)          # только чтобы HUD дышал
                self._note_gate("жду слово «Джарвис»")
                preroll.clear()
                return

            # Громко играет музыка/кино из своих динамиков — в облако не шлём:
            # по громкости её от голоса не отличить (см. _IGNORE_SPEAKERS).
            # Слушаем только ключевое слово «Джарвис», чтобы приглушить звук.
            if self._speaker_meter is not None and self._speaker_meter.peak > _SPEAKER_GATE:
                self._note_gate(
                    f"звук в динамиках {self._speaker_meter.peak:.3f} > "
                    f"порога {_SPEAKER_GATE}"
                )
                preroll.clear()
                return

            was_silent = getattr(self, "_quiet_frames", MIC_HANGOVER_FRAMES + 1) > MIC_HANGOVER_FRAMES
            if not self._is_loud_enough(indata):
                self._note_gate("тихо для порога MIC_RMS_THRESHOLD")
                preroll.append(pcm_bytes)
                return

            self._note_gate(None)
            if self._frame_was_loud:
                self._latency.mark_voice_frame()

            # Сбрасываем предбуфер (pre-roll) и плавно приглушаем музыку/кино при начале речи
            if was_silent:
                try:
                    from core.ducking_controller import ducking_controller
                    ducking_controller.duck()
                except Exception:
                    pass
                if preroll:
                    while preroll:
                        loop.call_soon_threadsafe(
                            _put_nowait_safe,
                            {"data": preroll.popleft(), "mime_type": "audio/pcm"},
                        )

            loop.call_soon_threadsafe(
                _put_nowait_safe,
                {"data": pcm_bytes, "mime_type": "audio/pcm"},
            )

        # Поток микрофона живёт в цикле. Раньше при выдернутой гарнитуре или
        # смене устройства в Windows колбэк просто переставал вызываться, а
        # задача спала дальше; при ошибке открытия — выходила, и микрофона не
        # было до следующего переподключения. Джарвис «игнорировал».
        backoff = 1.0
        while True:
            device = getattr(self, "_input_device", "unset")
            if device == "unset":
                # Перебор устройств пишет пробы звука — не в событийном цикле.
                device = await asyncio.to_thread(_pick_input_device)
                self._input_device = device
            try:
                with sd.InputStream(
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    device=device,
                    latency="low",
                    callback=callback,
                ) as stream:
                    print("[ДЖАРВИС] 🎤 Поток микрофона открыт")
                    self._mic_last_cb = time.monotonic()
                    backoff = 1.0
                    while True:
                        await asyncio.sleep(0.1)
                        self._show_listen_state()   # окно разговора истекло → «ОЖИДАЕТ»
                        silent_for = time.monotonic() - self._mic_last_cb
                        if not stream.active or silent_for > _MIC_STALL_SEC:
                            logger.warning("Микрофон замолчал (%.1f с без кадров) — переоткрываю", silent_for)
                            self.ui.write_log("SYS: микрофон пропал — переподключаю…")
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Microphone error: %s", e)
                self.ui.write_log("SYS: микрофон не открывается — пробую снова…")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
            # Устройство могло исчезнуть — выбираем заново.
            self._input_device = "unset"

    async def _speak_fish(self, text: str):
        """Озвучивает готовый текст голосом Джарвиса из Telegram-бота."""
        q: asyncio.Queue = asyncio.Queue()
        for chunk in _split_for_speech(text):
            q.put_nowait(chunk)
        q.put_nowait(None)
        await self._fish_worker(q)

    async def _fish_worker(self, fragments: asyncio.Queue):
        """Синтезирует куски из очереди (None — конец ответа) и играет их по
        порядку. Каждый кусок уходит в синтез сразу, как пришёл, — пока
        звучит первое предложение, следующие уже готовятся."""
        with self._speaking_lock:
            self._active_synth_tasks += 1
        self.set_speaking(True)

        try:
            from telegram_bot import tts_fish
            from telegram_bot import tts_edge

            # Жив ли Fish — решается один раз: после первого отказа остаток
            # ответа договаривает Edge, а не ждёт таймаута на каждом куске.
            # Куски синтезируются параллельно, поэтому первый запрос к Fish —
            # пробный: остальные ждут его вердикта, а не бьются в мёртвый
            # сервис каждый со своим таймаутом.
            fish_alive = tts_fish.is_configured()
            probe: asyncio.Future | None = None

            async def synth(fragment: str):
                nonlocal fish_alive, probe
                if fish_alive and probe is not None:
                    await probe
                if fish_alive:
                    first = probe is None
                    if first:
                        probe = asyncio.get_running_loop().create_future()
                    pcm = None
                    try:
                        pcm = await tts_fish.speak_pcm(fragment, sample_rate=RECV_SAMPLE_RATE)
                    finally:
                        if not pcm and fish_alive:
                            fish_alive = False
                            self.ui.write_log("SYS: Fish молчит — остаток ответа озвучит Edge-TTS")
                        if first:
                            probe.set_result(None)
                    if pcm:
                        return pcm
                return await tts_edge.speak_pcm(fragment, sample_rate=RECV_SAMPLE_RATE)

            ordered: asyncio.Queue = asyncio.Queue()

            async def feed():
                while True:
                    # Страховка: конец ответа не пришёл (разрыв, сбой) — не
                    # держать микрофон глухим вечно.
                    try:
                        fragment = await asyncio.wait_for(fragments.get(), _FISH_IDLE_SEC)
                    except asyncio.TimeoutError:
                        logger.warning("Fish: конец ответа не пришёл за %.0f с — закрываю", _FISH_IDLE_SEC)
                        break
                    if fragment is None:
                        break
                    ordered.put_nowait(asyncio.create_task(synth(fragment)))
                ordered.put_nowait(None)

            feeder = asyncio.create_task(feed())
            step = CHUNK_SIZE * 2
            spoken = 0
            try:
                while True:
                    task = await ordered.get()
                    if task is None:
                        break
                    pcm = await task
                    if not pcm:
                        self.ui.write_log("SYS: синтез речи недоступен — ответ остался текстом")
                        continue
                    if not spoken:
                        self._latency.mark_answer_audio()
                    spoken += len(pcm)
                    for j in range(0, len(pcm), step):
                        try:
                            self.audio_in_queue.put_nowait(pcm[j:j + step])
                        except asyncio.QueueFull:
                            await self.audio_in_queue.put(pcm[j:j + step])
            finally:
                feeder.cancel()
                while not ordered.empty():
                    leftover = ordered.get_nowait()
                    if leftover is not None:
                        leftover.cancel()

            if spoken:
                logger.info("Голос Fish: %.1f с звука", spoken / 2 / RECV_SAMPLE_RATE)
            self._latency.mark_turn_complete()
        finally:
            with self._speaking_lock:
                self._active_synth_tasks = max(0, self._active_synth_tasks - 1)

    # ── Получение ответа от Gemini ────────────────────────────────────────────
    def _spawn(self, coro):
        """Фоновая задача, которую сборщик мусора не снимет на полуслове.

        asyncio держит на задачу только слабую ссылку: create_task() без
        сохранения результата может исчезнуть посреди работы — для озвучки
        Fish это обрыв ответа на середине фразы.
        """
        tasks = self.__dict__.setdefault("_bg_tasks", set())
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    def _learn_from_phrase(self, text: str):
        """Эмоции и предпочтения из фразы — в профиль. Идёт в пуле потоков:
        профиль пишется на диск, а цикл приёма ждать этого не должен.

        Раньше здесь же InitiativeEngine и ProactiveEngine с шансом 30%
        отправляли в сессию готовые реплики — «Я здесь, сэр», «Всё тихо,
        сэр» — от лица ПОЛЬЗОВАТЕЛЯ. Модель отвечала на них, и Джарвис
        разговаривал сам с собой. А ProactiveEngine на каждой фразе
        синхронно ходил в Google Календарь прямо в событийном цикле —
        приём звука стоял, пока не ответит сеть. Проактивность живёт в
        Telegram-боте (proactive.py), там у неё свой канал и расписание.
        """
        try:
            emotion = EmotionAnalyzer.analyze(text)
            if emotion["emotion"] != "neutral":
                logger.debug("Эмоция: %s (%.2f)", emotion["emotion"], emotion["confidence"])
                self.user_profile.update_context(emotion=emotion["emotion"])
            preference = self.initiative_engine.should_learn_preference(text, "")
            if preference:
                self.user_profile.update_preference(preference["type"], preference["value"])
                logger.info("Выучил предпочтение: %s = %s", preference["type"], preference["value"])
        except Exception as exc:
            logger.debug("Обучение на фразе не удалось: %s", exc, exc_info=True)

    def _queue_answer_audio(self, data: bytes):
        self._latency.mark_answer_audio()
        try:
            self.audio_in_queue.put_nowait(data)
        except asyncio.QueueFull:
            # _play_audio не успевает — дропаем старейший
            # фрейм, чтобы освободить место под новый.
            try:
                self.audio_in_queue.get_nowait()
                self.audio_in_queue.put_nowait(data)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def _drop_pending_speech(self):
        """Выбросить недоигранный ответ: Gemini прервал ход или начался новый.
        Иначе старый ответ доигрывал поверх нового."""
        task = getattr(self, "_fish_task", None)
        if task and not task.done():
            task.cancel()
        self._fish_task = None
        q = self.audio_in_queue
        if q is not None:
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break

    async def _run_tool_calls(self, function_calls, allowed: bool):
        responses = []
        for fc in function_calls:
            if not allowed:
                # Команда из чужого разговора: «выключи свет»
                # по телевизору не должно выключать свет.
                logger.info("Инструмент %s не выполнен: обращались не ко мне", fc.name)
                responses.append(types.FunctionResponse(
                    id=fc.id, name=fc.name,
                    response={"result": "Не выполнено: реплика адресована не Джарвису. Промолчи."},
                ))
                continue
            print(f"[ДЖАРВИС] 📞 {fc.name}")
            # Джарвис у Старка не работает молча: HUD всегда
            # показывает, на что наведён.
            try:
                self.ui.lock_on(fc.name)
            except Exception as exc:
                logger.debug("Прицел не встал: %s", exc, exc_info=True)
            _tool_started = time.perf_counter()
            try:
                # Инструмент без ответа (Spotify не отвечает, браузерный вход в
                # Google) держал весь приём: ни звука, ни реакции — «завис».
                fr = await asyncio.wait_for(self._execute_tool(fc), _TOOL_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                logger.error("Инструмент %s не ответил за %.0f с", fc.name, _TOOL_TIMEOUT_SEC)
                self.ui.write_log(f"ERR: {fc.name} — нет ответа {_TOOL_TIMEOUT_SEC:.0f} с")
                fr = types.FunctionResponse(id=fc.id, name=fc.name, response={
                    "result": f"Не успело выполниться за {_TOOL_TIMEOUT_SEC:.0f} секунд."})
            except Exception as exc:
                # Сбой инструмента не должен рвать сессию: без ответа на
                # вызов модель так и ждёт его после переподключения.
                logger.exception("Инструмент %s упал", fc.name)
                fr = types.FunctionResponse(id=fc.id, name=fc.name,
                                            response={"result": f"Ошибка: {exc}"})
            finally:
                # Медленный инструмент — самая частая причина
                # паузы, которую слышно как «завис».
                self._latency.add_tool(
                    fc.name,
                    int((time.perf_counter() - _tool_started) * 1000),
                )
            responses.append(fr)
            self._show_card(fc, fr)
        await self.session.send_tool_response(function_responses=responses)

    def _show_card(self, fc, fr):
        """Карточка «что сделал» рядом с шаром. Окно, которое команда
        открыла, снимается чуть позже — ему нужно время появиться."""
        show = getattr(self.ui, "show_card", None)
        if not show:
            return
        try:
            card = build_card(fc.name, dict(fc.args or {}), getattr(fr, "response", None))
        except Exception as exc:
            logger.debug("Карточка не собралась: %s", exc)
            return
        if not card:
            return
        show(card["title"], card["address"], card["body"], b"", card["extra"])
        if card["want_shot"]:
            def _shot():
                time.sleep(1.8)
                png = capture_foreground_png()
                if png:
                    show(card["title"], card["address"], card["body"], png, card["extra"])
            threading.Thread(target=_shot, daemon=True, name="card-shot").start()

    async def _deferred_tool_calls(self, function_calls, named: asyncio.Event):
        """Вызов пришёл раньше расшифровки с именем — ждём её немного.
        Раньше такой вызов отклонялся сразу: «Джарвис, открой хром» при
        отставшей расшифровке молча не выполнялось."""
        try:
            await asyncio.wait_for(named.wait(), _TOOL_WAKE_WAIT_SEC)
        except asyncio.TimeoutError:
            pass
        await self._run_tool_calls(function_calls, named.is_set() or self.is_awake())

    async def _receive_audio(self):
        print("[ДЖАРВИС] 👂 Приём запущен")
        out_buf, in_buf = [], []
        # Решение по текущему ходу: None — ещё не ясно, True — обращались к
        # Джарвису, False — речь не к нему. Звук ответа до решения копится в
        # held: имя может прийти в расшифровке чуть позже первых байт ответа.
        addressed: bool | None = None
        named = asyncio.Event()      # в этом ходе прозвучало имя
        held: list[bytes] = []
        # Голос Fish: расшифровка ответа ещё не отданная в синтез и очередь
        # кусков для _fish_worker этого хода (см. _take_speakable).
        fish_text = ""
        fish_q: asyncio.Queue | None = None

        def fish_close():
            nonlocal fish_q
            # Закрываем очередь всегда, при любом голосе: пока её не закрыли,
            # воркер считается говорящим, и микрофон глух. Раньше закрытие
            # пропускалось, если голос переключили на gemini посреди ответа.
            if fish_q is not None:
                fish_q.put_nowait(None)
                fish_q = None

        def fish_flush(final: bool = False):
            nonlocal fish_text, fish_q
            if addressed and get_voice_provider() == "fish":
                chunks, fish_text = _take_speakable(fish_text, fish_q is None, final)
                if chunks and fish_q is None:
                    self._drop_pending_speech()   # один голос за раз
                    fish_q = asyncio.Queue()
                    self._fish_task = self._spawn(self._fish_worker(fish_q))
                for chunk in chunks:
                    fish_q.put_nowait(chunk)
            if final:
                fish_close()

        def decide() -> bool:
            return self.is_awake() or named.is_set()

        try:
            while True:
                async for response in self.session.receive():
                    upd = response.session_resumption_update
                    if upd and upd.resumable and upd.new_handle:
                        self._resume_handle = upd.new_handle

                    if response.go_away:
                        # Сервер скоро закроет сессию. Уходим сами, пока
                        # handle свежий, — реконнект в run() займёт полсекунды.
                        logger.info("Gemini просит переподключиться (GoAway)")
                        raise ConnectionResetError("GoAway от сервера")

                    sc = response.server_content

                    if sc and getattr(sc, "interrupted", None):
                        # Сервер оборвал ответ (новая реплика) — хвост не доигрываем.
                        self._drop_pending_speech()
                        held = []
                        fish_text = ""
                        fish_close()

                    if sc and sc.input_transcription:
                        txt = sc.input_transcription.text
                        if txt:
                            in_buf.append(txt)
                            self._latency.mark_transcript()
                            print(f"[ДЖАРВИС] 🎤 Фрагмент: '{txt}'")
                            if not named.is_set() and _has_wake_word("".join(in_buf)):
                                named.set()
                                self.wake()
                                if addressed is not True:
                                    addressed = True
                                    for chunk in held:
                                        if get_voice_provider() != "fish":
                                            self._queue_answer_audio(chunk)
                                    held = []
                                    fish_flush()

                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        if addressed is None:
                            addressed = decide()
                        if get_voice_provider() == "fish":
                            # Говорит Fish — звук Gemini выбрасываем, иначе
                            # два голоса произнесут один ответ одновременно.
                            pass
                        elif addressed:
                            self._queue_answer_audio(response.data)
                        else:
                            held.append(response.data)

                    if sc:
                        if sc.output_transcription and sc.output_transcription.text:
                            out_buf.append(sc.output_transcription.text)
                            fish_text += sc.output_transcription.text
                            if addressed is None:
                                addressed = decide()
                            if addressed:
                                # Субтитр идёт вслед за речью, а не после неё.
                                sub = getattr(self.ui, "set_subtitle", None)
                                if sub:
                                    sub(_clean_dialog_text("".join(out_buf)))
                            fish_flush()

                        if sc.turn_complete:
                            if addressed is None:
                                addressed = decide()
                            fish_flush(final=True)
                            fish_text = ""
                            # С внешним голосом ход закрывает _fish_worker,
                            # когда звук реально пошёл: здесь готов только текст.
                            if get_voice_provider() != "fish" or not addressed:
                                self._latency.mark_turn_complete()
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = _clean_dialog_text("".join(in_buf))
                            full_out = _clean_dialog_text("".join(out_buf))
                            by_name = named.is_set()
                            in_buf, out_buf, held = [], [], []
                            was_addressed, addressed = addressed, None
                            named = asyncio.Event()

                            if not was_addressed:
                                if full_in:
                                    logger.info("Не ко мне, молчу: «%s»", full_in[:80])
                                self._release_ducking()
                                continue

                            if full_in:
                                print(f"[ДЖАРВИС] 🎤 Полная фраза: '{full_in}'")
                                self.ui.write_log(f"Вы: {full_in}")
                                self.last_user_text = full_in
                                self._user_turn += 1
                                asyncio.get_running_loop().run_in_executor(
                                    None, self._learn_from_phrase, full_in
                                )
                                # Продолжение разговора без имени продлевает окно
                                # лишь несколько раз подряд — иначе телевизор держал
                                # бы Джарвиса «проснувшимся» бесконечно.
                                if not by_name:
                                    self._continue_conversation()

                            if full_out:
                                self.ui.write_log(f"Джарвис: {full_out}")

                    if response.tool_call:
                        if addressed is None:
                            addressed = decide()
                        calls = response.tool_call.function_calls
                        if addressed:
                            await self._run_tool_calls(calls, True)
                        else:
                            self._spawn(self._deferred_tool_calls(calls, named))

        except Exception as e:
            logger.error("Приём оборвался: %s", e)
            # Пробрасываем наверх: TaskGroup свернётся, и сработает
            # реконнект в `run()` — с handle'ом возобновления, так что
            # разговор продолжится с того же места.
            self.ui.write_log("SYS: связь с Gemini оборвалась — переподключаюсь…")
            raise
        finally:
            fish_close()

    # ── Воспроизведение аудио ─────────────────────────────────────────────────
    def _open_output(self):
        """Поток вывода: сначала устройство по умолчанию, потом любое рабочее.

        Устройство по умолчанию может существовать в списке и при этом не
        играть — Windows держит «Наушники» в endpoint'ах, когда их физически
        нет, MME открывает такой поток молча и падает уже на записи с
        «There is no driver installed on your system».
        """
        def _try(device):
            s = sd.RawOutputStream(samplerate=RECV_SAMPLE_RATE, channels=CHANNELS,
                                   dtype="int16", blocksize=CHUNK_SIZE, device=device, latency="low")
            s.start()
            s.write(b"\x00" * (CHUNK_SIZE * 2))   # тишина: проверяем, что ПИШЕТСЯ
            return s

        try:
            return _try(None)
        except Exception as exc:
            logger.warning("Устройство вывода по умолчанию не играет: %s", exc)

        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_output_channels"] < CHANNELS:
                continue
            try:
                stream = _try(idx)
            except Exception:
                continue
            logger.warning("Звук переключён на «%s»", dev["name"])
            self.ui.write_log(f"SYS: звук через «{dev['name'][:32]}»")
            return stream
        raise RuntimeError("ни одно устройство вывода не принимает звук")

    async def _play_audio(self):
        """Воспроизведение ответа. Переживает пропажу звукового устройства.

        Раньше цикл сидел ВНУТРИ try, а finally закрывал поток: одна ошибка
        записи — вынутые наушники, переключение устройства в Windows — и
        задача завершалась навсегда. Комментарий обещал «let the task be
        recreated», но пересоздавать её было некому: _play_audio создаётся
        единожды в TaskGroup. Джарвис немел до перезапуска, продолжая при
        этом слушать и отвечать текстом — со стороны выглядело как «сломался
        голос».
        """
        print("[ДЖАРВИС] 🔊 Воспроизведение запущено")
        def _close(s):
            try:
                s.stop()
                s.close()
            except Exception as exc:
                logger.debug("Закрытие потока вывода: %s", exc)

        while True:
            stream = None
            pending_write = None
            opening = asyncio.ensure_future(asyncio.to_thread(self._open_output))
            try:
                # Отмена посреди открытия (переподключение) не должна бросать
                # уже открытый поток: поток-исполнитель доделает открытие, и
                # его надо закрыть — иначе на каждом реконнекте утечка устройства.
                try:
                    stream = await asyncio.shield(opening)
                except asyncio.CancelledError:
                    opening.add_done_callback(
                        lambda t: _close(t.result()) if not t.cancelled() and not t.exception() else None)
                    raise
                while True:
                    try:
                        chunk = await asyncio.wait_for(self.audio_in_queue.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        lock = getattr(self, "_speaking_lock", None)
                        if lock is not None:
                            with lock:
                                is_busy = (getattr(self, "_active_synth_tasks", 0) > 0) or (not self.audio_in_queue.empty())
                        else:
                            is_busy = (getattr(self, "_active_synth_tasks", 0) > 0) or (not self.audio_in_queue.empty())

                        if not is_busy:
                            if getattr(self, "_is_speaking", False):
                                self.set_speaking(False)
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                        continue

                    self.set_speaking(True)
                    self._latency.mark_playback()
                    self._push_level(_chunk_level(chunk))
                    pending_write = asyncio.ensure_future(asyncio.to_thread(stream.write, chunk))
                    await asyncio.shield(pending_write)
                    pending_write = None

            except asyncio.CancelledError:
                raise
            except (AttributeError, TypeError, NameError, ImportError) as bug:
                # Дефект кода, а не пропавшие наушники. Раньше он попадал в
                # ветку ниже: пользователю сообщалось «звук отвалился», и цикл
                # переоткрывал исправное устройство до бесконечности, пряча
                # настоящую причину. Такое должно быть громким и заметным —
                # наверху есть счётчик попыток, который остановит Джарвиса
                # по-человечески.
                logger.exception("Сбой в коде воспроизведения (%s) — устройство ни при чём",
                                 type(bug).__name__)
                self.ui.write_log("SYS: ошибка воспроизведения в коде — подробности в логе")
                raise
            except Exception as e:
                logger.error("Воспроизведение оборвалось (%s) — переоткрываю устройство", e)
                self.ui.write_log("SYS: звук отвалился, переподключаю устройство…")
                await asyncio.sleep(_PLAYBACK_RETRY_SEC)
            finally:
                self.set_speaking(False)
                if stream is not None:
                    if pending_write is not None and not pending_write.done():
                        # Закрывать поток, пока другой поток внутри Pa_WriteStream,
                        # — зависание или падение на MME. Закроем, когда запись выйдет.
                        pending_write.add_done_callback(lambda _t, s=stream: _close(s))
                    else:
                        _close(stream)

    # ── Основной цикл ─────────────────────────────────────────────────────────
    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"},
        )

        # Разрывы Live-сессии — штатное явление: сервер закрывает голосовую
        # сессию по лимиту времени, шлёт GoAway, отвечает 1011. Раньше после
        # пяти неудач подряд цикл выходил, и Джарвис молча глох до перезапуска,
        # а проверки «1008»/«ключ» не срабатывали вовсе: из TaskGroup ошибка
        # приходит ExceptionGroup'ом, в str() которого нет текста причины.
        # Теперь пробуем бесконечно, фатальны только проблемы с ключом.
        failures = 0
        first_connect = True

        while True:
            connected = 0.0
            try:
                print("[ДЖАРВИС] 🔌 Подключение к Gemini...")
                self.ui.set_state("RECONNECTING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    connected = time.monotonic()
                    self.session            = session
                    self._loop              = asyncio.get_event_loop()
                    # maxsize защищает от unbounded роста памяти
                    # если _play_audio тормозит. При переполнении старые
                    # фреймы дропаются в _receive_audio.
                    self.audio_in_queue     = asyncio.Queue(maxsize=200)
                    self.out_queue          = asyncio.Queue(maxsize=50)
                    self._turn_done_event   = asyncio.Event()

                    print("[ДЖАРВИС] ✅ Подключён.")
                    self.ui.set_state("IDLE")
                    # Сигнал — только при запуске. На каждом переподключении
                    # он звучал как «Джарвис активировался сам по себе».
                    if first_connect:
                        first_connect = False
                        try:
                            from core.wakeword import play_activation_chime
                            play_activation_chime()
                        except Exception:
                            pass

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

                    # Авто-триггер утреннего брифинга (6-11 утра, 1 раз в день)
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    if 6 <= datetime.now().hour < 11 and getattr(self, "_last_briefing_date", None) != today_str:
                        self._last_briefing_date = today_str
                        async def _run_morning_briefing():
                            await asyncio.sleep(1.5)
                            try:
                                from actions.morning_briefing import morning_briefing
                                loop = asyncio.get_event_loop()
                                briefing = await loop.run_in_executor(
                                    None, lambda: morning_briefing({}, player=self.ui)
                                )
                                if briefing:
                                    self.speak(briefing)
                            except Exception as e:
                                logger.warning("Утренний брифинг: %s", e)
                        tg.create_task(_run_morning_briefing())

            except asyncio.CancelledError:
                raise
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                reason = _root_error_text(e)
                low = reason.lower()
                logger.error("Сессия Gemini оборвалась: %s", reason)
                logger.debug("Подробности разрыва", exc_info=True)

                if "1008" in low or "leaked" in low:
                    logger.error("Ключ Gemini заблокирован (1008) — создайте новый: "
                                 "https://aistudio.google.com/app/apikey")
                    self.ui.write_log("SYS: ❌ API ключ заблокирован. Получите новый на https://aistudio.google.com/app/apikey")
                    break
                if any(k in low for k in ("api key not valid", "api key expired",
                                          "api_key_invalid", "invalid api key",
                                          "api key not found")):
                    logger.error("Ключ Gemini недействителен. Обновите его в %s", API_CONFIG)
                    self.ui.write_log("SYS: ❌ API ключ недействителен. Обновите config/api_keys.json")
                    break

            self.set_speaking(False)
            self.session = None
            if connected and time.monotonic() - connected >= _SESSION_HEALTHY_SEC:
                # Сессия жила и закрылась — обычный разрыв. Возвращаемся сразу,
                # с тем же handle'ом возобновления: разговор продолжается.
                failures = 0
                delay = 0.5
            else:
                # Упала сразу после подключения (отвергнутый handle, квота) —
                # это неудача, а не разрыв: пауза растёт.
                failures += 1
                delay = min(2 ** failures, _RECONNECT_MAX_SEC)
                # Протухший handle возобновления сервер не примет — и цикл
                # бился бы в него бесконечно. Со второй неудачи — чистый старт.
                if failures >= 2:
                    self._resume_handle = None
                if failures == 3:
                    self.ui.write_log("SYS: нет связи с Gemini — продолжаю попытки…")
            logger.info("Переподключение через %.1f с", delay)
            self.ui.set_state("RECONNECTING")
            await asyncio.sleep(delay)


def _root_error_text(exc: BaseException) -> str:
    """Текст первопричины, в том числе из ExceptionGroup, которой TaskGroup
    заворачивает ошибку задачи."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


# ─── Точка входа ──────────────────────────────────────────────────────────────
def main():
    # Самопроверка сборки (CI на Windows): без окна и без Gemini.
    if "--selftest" in sys.argv:
        from core import selftest
        sys.exit(selftest.run())

    # Без графики: JARVIS_HEADLESS=1 или --headless. Голосовой круг тот же,
    # разница только в том, кто показывает состояние и кто ждёт ключ.
    headless = headless_requested()

    # Если запускаемся с графикой — проверяем наличие API ключа
    if not headless:
        try:
            from ui_setup import ensure_setup
            if not ensure_setup(force=False):
                print("[ДЖАРВИС] Настройка отменена пользователем.")
                return
        except Exception as _e:
            logger.debug("Setup wizard bypass: %s", _e)

    ui = HeadlessUI() if headless else JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = Jarvis(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Завершение работы...")
        except SystemExit:
            # sys.exit в рабочем потоке гасил только поток: окно оставалось
            # висеть и молчать. Теперь хотя бы видно, почему.
            ui.write_log("SYS: ❌ Ключ Gemini не найден. Перезапустите и введите ключ.")
        finally:
            jarvis.cleanup()
            summary = jarvis._latency.summary()
            if summary:
                print(summary)

    if headless:
        # Окна нет, значит нет и цикла событий, который держал бы процесс:
        # крутим круг прямо в главном потоке, иначе демон-поток умрёт вместе
        # с мгновенно завершившимся main().
        ui.start_text_input()
        runner()
        return

    threading.Thread(target=runner, daemon=True).start()
    ui.mainloop()


if __name__ == "__main__":
    main()
