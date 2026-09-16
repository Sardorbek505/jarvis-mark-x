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
import json
import traceback
import random
import re
import threading
import time
import subprocess
import atexit
from datetime import datetime
import logging
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('JARVIS')

import sounddevice as sd
import numpy as np
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
from core.user_profile import UserProfile
from core.initiative_engine import InitiativeEngine
from core.proactive_engine import ProactiveEngine
from core.team_collaboration import TeamCollaborationEngine
from core.onboarding import ensure_gemini_key
from core.latency import LatencyTracker
from core.headless_ui import HeadlessUI, headless_requested
from actions.open_app import open_app
from actions.weather import weather_action
from actions.web_search import web_search
from actions.computer_settings import computer_settings
from actions.browser_control import browser_control
from actions.file_controller import file_controller
from actions.modes import set_mode, get_current_mode
from actions.movie_player import movie_player
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
from core.command_context import PendingGeminiTurn

# Ходы, ответ Gemini на которые не озвучивается и инструменты не исполняются:
# реплику забрал локальный контроллер или она была не к Джарвису.
_SILENT_ROUTES = ("LOCAL", "DISCARDED")



# ─── Пути и константы ─────────────────────────────────────────────────────────
from core.paths import get_base_dir, get_config_path, get_prompt_path

BASE_DIR      = get_base_dir()
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

# Сколько тишины ждать, прежде чем считать фразу законченной.
# 400 мс — сбалансированная пауза, позволяющая комфортно произносить фразы без обрывания между словами.
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

from core.voice_cache import get_voice_cache  # noqa: E402 — после настройки окружения выше

# Сколько ждать первый звук от Fish, прежде чем параллельно поднять Edge-TTS.
# Бесплатный тариф Fish (s2.1-pro-free) за один вечер 15.09.2026 плавал от
# 0.9 с до 11 с на первый байт; на 11 с диалог разваливается целиком. Хедж,
# а не замена: кто отдал звук первым, тот и говорит, второй отменяется.
_TTS_FIRST_AUDIO_TIMEOUT_SEC = float(os.getenv("JARVIS_TTS_FIRST_AUDIO_TIMEOUT", "3"))

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

_VOICE_PROVIDER = (os.getenv("JARVIS_VOICE") or _read_config_voice() or "fish").strip().lower()

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


def _device_is_silent(index: int, seconds: float = 0.05) -> bool:
    """Проверяет, является ли устройство мёртвым или фантомным виртуальным входом."""
    try:
        import numpy as np
        rec = sd.rec(int(seconds * SEND_SAMPLE_RATE), samplerate=SEND_SAMPLE_RATE,
                     channels=1, dtype="int16", device=index)
        sd.wait()
        peak = int(np.abs(rec).max())
        return peak == 0
    except Exception as exc:
        logger.warning("Устройство %s не удалось проверить (%s) — пропускаю", index, exc)
        return True


def _pick_input_device():
    """Индекс микрофона: из MIC_DEVICE, иначе гарнитура, иначе системный микрофон по умолчанию (как в Windows).
    
    ВАЖНО: Виртуальные драйверы фильтрации (например, 'ASUS AI Noise-cancelling' / 'Intelligo')
    часто выдают сильный паразитный шум (RMS > 8000) или агрессивно режут звуки речи.
    Поэтому системный микрофон по умолчанию (Realtek Array) имеет приоритет над виртуальными утилитами.
    """
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
    for i, d in devices:
        if d["max_input_channels"] <= 0 or d.get("hostapi", 0) != 0:
            continue
        name = d["name"].lower()
        if any(k in name for k in ("headset", "headphone", "bluetooth", "wireless", "buds", "airpods", "freebuds", "wh-1000", "airdots", "гарнитур", "наушник", "hands-free", "usb")) and not _device_is_silent(i):
            if "virtual" not in name and "line" not in name and "output" not in name:
                logger.info("Обнаружена подключенная гарнитура/наушники — выбран микрофон: «%s» (индекс %d)", d["name"], i)
                return i

    # 2. Системное устройство Windows по умолчанию (то же, через что работают все остальные программы)
    default_in = sd.default.device[0] if sd.default.device and sd.default.device[0] is not None else None
    if default_in is not None and default_in >= 0:
        try:
            d = sd.query_devices(default_in)
            name = d.get("name", "").lower()
            if "virtual" not in name and "intelligo" not in name and "stereo mix" not in name and "стерео" not in name:
                if not _device_is_silent(default_in):
                    logger.info("Выбран системный микрофон по умолчанию (как в других приложениях): «%s» (индекс %d)", d["name"], default_in)
                    return default_in
        except Exception as exc:
            logger.debug("Проверка default_in не удалась: %s", exc)

    # 3. Встроенный Realtek / массив микрофонов
    for i, d in devices:
        if d["max_input_channels"] <= 0 or d.get("hostapi", 0) != 0:
            continue
        name = d["name"].lower()
        if any(k in name for k in ("realtek", "микрофон", "array", "массив")) and not _device_is_silent(i):
            if "virtual" not in name and "line" not in name and "asus" not in name and "intelligo" not in name:
                logger.info("Выбран микрофон: «%s» (индекс %d)", d["name"], i)
                return i

    # 4. Системное устройство по умолчанию
    return None
CHUNK_SIZE        = 1024

# Порог тишины для микрофона (RMS по int16).
# 12.0 обеспечивает высокую чувствительность ко всем типам микрофонов (гарнитуры, USB, встроенные)
# и улавливает даже спокойную речь и команды с расстояния.
MIC_RMS_THRESHOLD = float(os.getenv("MIC_RMS_THRESHOLD", "12.0"))
# Хвост тишины после речи — не косметика, а условие того, что тебе вообще
# ответят. Конец фразы определяет VAD на стороне Gemini, и определить его он
# может только по ПОЛУЧЕННОЙ тишине: когда гейт обрывает поток сразу за
# последним громким кадром, сервер остаётся ждать продолжения фразы.
MIC_HANGOVER_MS = int(os.getenv("MIC_HANGOVER_MS", "800"))
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

    # Убираем системные заголовки и метаданные в скобках ([ТЕКУЩЕЕ ВРЕМЯ ...], [CURRENT TIME ...])
    text = re.sub(r"\[(?:ТЕКУЩЕЕ ВРЕМЯ|ВРЕМЯ|ДАТА|CURRENT TIME|TIME|DATE|SYS)[^\]]*\]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\[[A-ZА-ЯЁ\s_0-9—:,-]{3,60}\]\s*", "", text)

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

_PC_WAKE_WORDS = (
    "джарвис", "jarvis", "жарвис", "джарв", "jarv",
    "эй джарвис", "hey jarvis", "хэй джарвис",
    "слушай джарвис", "ок джарвис", "ok jarvis",
)


_GREETINGS = (
    "эй", "hey", "хэй", "слушай", "listen", "тыңда", "тында", "eshit",
    "ок", "ok", "а", "ну", "так", "вот", "давай", "привет", "hi", "салом", "salom",
    "сәлем", "алло", "короче", "брат", "друг", "qalesan", "қалайсың",
    "э", "ээ", "эээ", "мм", "ммм", "эм",
)

from core.wake_names import WAKE_NAME_VARIANTS as _WAKE_NAME_VARIANTS  # noqa: E402

_WAKE_VARIANTS = tuple(sorted(set(_WAKE_NAME_VARIANTS) | {"djarv", "джарв", "jarv"}, key=len, reverse=True))

_GREETING_PREFIX_PAT = r"^(?:(?:" + "|".join(_GREETINGS) + r")[\s,.:!—?\"'«»\-]+)*"
_WAKE_NAMES_PAT = r"(?:" + "|".join(_WAKE_VARIANTS) + r")"
_WAKE_REGEX = re.compile(
    rf"{_GREETING_PREFIX_PAT}({_WAKE_NAMES_PAT})(?!['’]s\b)(?:[\s,.:!—?'’\"«»\-]|$)",
    re.IGNORECASE,
)


def is_addressed_to_jarvis(text: str) -> bool:
    """Проверяет, обращается ли пользователь к Джарвису по имени.

    На ПК Джарвис отвечает ТОЛЬКО если фраза начинается с обращения
    'Джарвис' (или предваряется исключительно междометиями/приветствиями).
    
    Защищён от ложных срабатываний:
    - Склонения в третьем лице ('Джарвиса нет', 'Джарвису пора', 'Джарвисом пользуюсь', "Jarvis's car")
      считаются разговором О Джарвисе, а не обращением К нему, и игнорируются!
    - Упоминания в третьем лице с предшествующими существительными
      ('певец Jarvis Cocker', 'остров Jarvis Island', 'проект Джарвис') игнорируются!
    - Созвучные слова ('джаз', 'джем', 'джакузи', 'жара', 'жаркое') игнорируются.
    """
    if not text:
        return False
    # 1. Убираем аудиотеги STT ([музыка], [шум], (смех))
    t = re.sub(r"^\[.*?\]\s*|^\(.*?\)\s*", "", text.strip())
    # 2. Убираем эмодзи и спецсимволы в начале строки
    t = re.sub(r"^[^\wа-яёА-ЯЁa-zA-Z0-9]+", "", t).strip()
    t = t.lower()
    # 3. Убираем вокальные заминки и заикания («э-э-э», «м-м-м»)
    t = re.sub(r"^(?:[эеаоmм]\s*[-—–]\s*)+[эеаоmм]?\s*", "", t).strip()
    if not t:
        return False

    # 4. Точная проверка регулярным выражением
    if _WAKE_REGEX.search(t):
        return True

    # 5. Проверка первых 5 токенов: все предшествующие слова обязаны быть приветствиями
    tokens = re.findall(r"[a-zA-Zа-яёА-ЯЁ0-9']+", t[:60])
    for i, tok in enumerate(tokens[:5]):
        if tok in _WAKE_VARIANTS and not tok.endswith("'s"):
            preceding = tokens[:i]
            if all(p in _GREETINGS for p in preceding):
                return True

    return False


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


# ─── Описания инструментов (на русском) ───────────────────────────────────────
TOOLS = [
    {
        "name": "open_app",
        "description": (
            "Открывает любое приложение или программу на компьютере. "
            "Вызывай всегда, когда пользователь просит открыть, запустить или включить что-либо. "
            "Никогда не говори что открыл — всегда вызывай этот инструмент."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Название приложения (например: Chrome, Telegram, Spotify)"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "weather",
        "description": "Сообщает текущую погоду в указанном городе.",
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
            "Управляет настройками компьютера: громкость, яркость, скриншот, "
            "блокировка экрана, выключение, перезагрузка."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "Действие: volume_up | volume_down | mute | "
                        "brightness_up | brightness_down | screenshot | lock | "
                        "shutdown | restart"
                    )
                },
                "value": {"type": "STRING", "description": "Значение (например: 50 для 50%)"}
            },
            "required": []
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
            "запуск фильма на vkvideo.ru (автоматически открывает 1-й фильм и включает полный экран), "
            "пауза/продолжить (Space), полный экран (F), "
            "перемотка вперед/назад на указанное количество секунд/минут (seek_forward/seek_back), "
            "перемотка на позицию/процент (seek_to: начало, 50%, середина), громкость (↑/↓), выход. "
            "Вызывай когда пользователь говорит: включи фильм X, поставь X, фильм X, "
            "пауза, продолжай, перемотай вперед/назад на X минут/секунд, перемотай на середину, полный экран, громче фильм, тише, закрой фильм."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "play (запустить фильм на vkvideo.ru) | pause (пауза) | "
                        "resume (продолжить) | fullscreen (полный экран) | "
                        "seek_forward (перемотка вперед) | seek_back (перемотка назад) | "
                        "seek_to (перемотка на процент/позицию: начало, 50%, середина) | "
                        "volume_up (громкость +10%) | volume_down (-10%) | "
                        "exit (выход + закрыть фильм)"
                    )
                },
                "title": {
                    "type": "STRING",
                    "description": "Название фильма или сериала (для action=play)"
                },
                "season": {
                    "type": "NUMBER",
                    "description": "Номер сезона для сериалов (например 1, 2)"
                },
                "episode": {
                    "type": "NUMBER",
                    "description": "Номер серии (например 1, 5)"
                },
                "seconds": {
                    "type": "NUMBER",
                    "description": "Количество секунд для перемотки (для seek_forward / seek_back)"
                },
                "minutes": {
                    "type": "NUMBER",
                    "description": "Количество минут для перемотки (для seek_forward / seek_back, например 5)"
                },
                "position": {
                    "type": "STRING",
                    "description": "Позиция фильма (для seek_to: 'начало', 'середина', '50%', '75%')"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "execute_routine",
        "description": (
            "Исполняет комплексные автоматизированные сценарии: "
            "morning (доброе утро: брифинг, дата, время, погода, задачи, утренний трек), "
            "work (я за работу: запуск рабочего софта, focus-музыка), "
            "movie (режим кинотеатра: сворачивание в Arc Reactor виджет, плеер), "
            "bedtime (спокойной ночи: пауза медиа, блокировка экрана, таймер сна), "
            "или кастомные макросы из config/routines.json."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "routine_name": {
                    "type": "STRING",
                    "description": "Название сценария: morning | work | movie | bedtime | relax"
                }
            },
            "required": ["routine_name"]
        }
    },
    {
        "name": "query_memory",
        "description": (
            "Ищет информацию в долгосрочной эпизодической памяти о пользователе: "
            "его предпочтения, привычки, сохранённые факты, заметки, задачи, пароли, расположение вещей. "
            "Вызывай при вопросах: что ты обо мне знаешь, какой мой любимый кофе, где лежат мои документы, вспомни X."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Поисковый запрос для извлечения воспоминаний из базы знаний"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "window_control",
        "description": (
            "Управляет окнами и системой Windows: закрыть/свернуть/развернуть окно, "
            "переключение окон, рабочий стол, проводник, диспетчер задач, параметры. "
            "Вызывай когда пользователь говорит: закрой окно, сверни окно, разверни, "
            "переключи окно, покажи рабочий стол, сверни все окна, открой проводник, "
            "открой диспетчер задач, открой параметры, переключись на Chrome/Spotify/etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "close (Alt+F4 закрыть окно) | "
                        "minimize (свернуть) | maximize (развернуть) | "
                        "minimize_all (свернуть все окна Win+M) | "
                        "snap_left (прижать влево Win+←) | snap_right (Win+→) | "
                        "switch (переключиться Alt+Tab) | "
                        "show_desktop (Win+D рабочий стол) | "
                        "open_explorer (Win+E проводник) | "
                        "task_manager (диспетчер задач) | "
                        "settings (параметры Windows) | "
                        "run (Win+R выполнить) | "
                        "activate (переключиться на окно по имени, нужен target)"
                    )
                },
                "target": {
                    "type": "STRING",
                    "description": (
                        "Для action=activate — название приложения "
                        "(например 'Chrome', 'Spotify', 'Telegram')"
                    )
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "music_player",
        "description": (
            "Управляет Spotify через официальный Web API: точный поиск треков, пауза, "
            "переключение, громкость, перемешивание, повтор, информация о текущем треке, "
            "mood mode (спокойное/мотивационное/ночной вайб). "
            "Гарантирует воспроизведение запрошенного трека, не последнего проигранного. "
            "Вызывай когда пользователь говорит: включи музыку, включи <исполнителя/трек>, "
            "поставь песню, пауза, продолжи, следующий трек, предыдущий трек, "
            "стоп музыку, громче, тише, громкость X, перемешай, повтор, что играет, "
            "кто поет, включи спокойное/мотивационное/ночной вайб."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "play (запуск с поиском) | pause | resume | "
                        "next (следующий трек) | prev (предыдущий) | stop | "
                        "volume_up | volume_down | volume | shuffle | repeat | "
                        "now_playing | mood"
                    )
                },
                "query": {
                    "type": "STRING",
                    "description": (
                        "Что играть (для action=play/mood): название трека, исполнителя, "
                        "альбома, жанра или настроение. Например: 'Imagine Dragons', 'lofi hip hop', "
                        "'Любэ', 'jazz', 'спокойное', 'мотивационное', 'ночной вайб'."
                    )
                },
                "value": {
                    "type": "STRING",
                    "description": (
                        "Значение для action=volume (0-100) или action=repeat (track/context/off)"
                    )
                },
                "playlist_url": {
                    "type": "STRING",
                    "description": "Прямой URL Spotify-плейлиста (опционально, имеет приоритет над query)"
                }
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
            "Также вызывай автоматически при старте сессии если сейчас утро (6-10). "
            "НЕ вызывай на светские вопросы вроде 'как дела', 'как ты', 'что нового у тебя' — "
            "на них отвечай сам, коротко, без сводки."
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
                    "description": "set (установить таймер) | cancel (отменить) | status (проверить оставшееся время)"
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
    {
        "name": "type_to_terminal",
        "description": (
            "Печатает текст или команду в активное окно терминала с Claude Code или консолью ИИ. "
            "Автоматически проверяет блокировку экрана, находит окно терминала (например Smart Store), "
            "проверяет происходящее глазами через зрение и отправляет команду с подтверждением. "
            "Вызывай когда пользователь говорит: 'напиши клоду ...', 'скажи клоду продолжить', "
            "'напиши в терминал ...', 'напечатай в терминале ...', 'отправь в консоль ...'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {
                    "type": "STRING",
                    "description": "Текст или команда для ввода в терминал (например: 'продолжай работу', 'yes', 'npm test')"
                },
                "press_enter": {
                    "type": "BOOLEAN",
                    "description": "True (по умолчанию) чтобы нажать клавишу Enter после ввода"
                }
            },
            "required": ["text"]
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
        self._turn_done_event: asyncio.Event | None = None

        # Новый мозг ДЖАРВИС
        self.user_profile = UserProfile(BASE_DIR)
        self.initiative_engine = InitiativeEngine()
        self.proactive_engine = ProactiveEngine(BASE_DIR)
        self.team_engine = TeamCollaborationEngine(BASE_DIR)
        self.last_user_text = ""
        self._followup_timeout = None

        # Секундомер голосового хода. Пишет в лог задержку от конца речи до
        # первого звука ответа при JARVIS_DEBUG_UI=1.
        self._latency = LatencyTracker(
            sink=self.ui.write_log if os.getenv("JARVIS_DEBUG_UI") == "1" else None
        )

        # ── ConversationStateMachine — центральный источник истины lifecycle ──
        from core.conversation_state import ConversationState, ConversationStateMachine
        self.state_machine = ConversationStateMachine()
        self._wire_state_machine_listeners()

        # Опорный поток колонок для AEC поднимается лениво, вместе с микрофоном.
        self._aec_reference = None

        # ── AudioPipeline — неблокирующий конвейер (AEC, VAD, KWS, Barge-in) ──
        try:
            from core.audio_pipeline import AudioPipeline
            self.audio_pipeline = AudioPipeline(
                state_machine=self.state_machine,
                on_wake=self._on_wake_spotted,
                on_quick_command=self._handle_quick_command,
                on_speech_frame=self._on_speech_frame,
                on_barge_in=lambda reason: self.interrupt_speech(reason),
                on_level=lambda lvl: self._push_level(lvl),
                gateway_active_provider=lambda: (
                    time.monotonic() < getattr(self, "_wake_active_until", 0.0)
                    or time.monotonic() < getattr(self, "_hotkey_active_until", 0.0)
                ),
                ref_provider=self._aec_reference_window,
                on_silence_timeout=self._on_listen_silence_timeout,
                on_local_transcript=self._on_local_transcript,
                rms_threshold=MIC_RMS_THRESHOLD,
                hangover_frames=MIC_HANGOVER_FRAMES,
                enable_aec=True,
                enable_ducking=True,
            )
            self.audio_pipeline.start()
            self._wake_detector = self.audio_pipeline.wake_detector
            logger.info("AudioPipeline: подключён к Jarvis (KWS, VAD, Barge-in)")
        except Exception as _e:
            logger.debug("AudioPipeline init note: %s", _e)
            self.audio_pipeline = None
            try:
                from core.wake_detector import WakeWordDetector2Stage
                self._wake_detector = WakeWordDetector2Stage(
                    on_quick_command=self._handle_quick_command,
                    state_provider=lambda: self.state_machine.state if hasattr(self, "state_machine") else ConversationState.STANDBY,
                )
            except Exception:
                self._wake_detector = None

        self.ui.on_text_command = self._on_text_command
        if hasattr(self.ui, "on_wake"):
            self.ui.on_wake = self._on_hotkey_wake

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

        self._wake_active_until = 0.0    # Окно активности после слова «Джарвис»
        self._hotkey_active_until = 0.0  # Окно активности при нажатии F8 или вводе текста
        self._fast_command_thread = None  # Поток исполнения текущей быстрой команды
        self._speech_prefetch = None      # (текст, задача) — синтез первой фразы наперёд
        self._streaming_speech_active = False
        self._streaming_queue: Optional[asyncio.Queue] = None
        self._streamed_chunks_indices: set[int] = set()

        # ── CommandOrchestrator — оркестратор многошаговых и контекстных команд ──
        from core.command_orchestrator import CommandOrchestrator
        self.command_orchestrator = CommandOrchestrator()
        self._active_speech_command_id = None
        self._active_speech_turn_id = 0
        self._current_utterance_id: str = ""
        self._current_generation_id: int = 0
        self._active_speech_generation_id: int = 0
        self._pending_gemini_turn: Optional[PendingGeminiTurn] = None

        # Автоматический запуск мобильного Telegram-бота (@Aimyjarvisbot) в фоне
        self._telegram_proc = self._start_telegram_bot()
        atexit.register(self.cleanup)

    def _begin_new_utterance(self) -> str:
        """Создаёт новый utterance_id и инициализирует буфер карантина ответа Gemini."""
        import uuid
        self._current_utterance_id = f"utt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        self._current_generation_id = getattr(self, "_current_generation_id", 0) + 1
        self._pending_gemini_turn = PendingGeminiTurn(
            utterance_id=self._current_utterance_id,
            generation_id=self._current_generation_id,
        )
        self._streaming_speech_active = False
        self._streaming_queue = None
        self._streamed_chunks_indices = set()
        return self._current_utterance_id


    def _wire_state_machine_listeners(self):
        """Подключение слушателей изменения состояния к интерфейсу и регуляторам."""
        def _on_state_change(old_state, new_state, reason=""):
            logger.info("[State] %s -> %s (%s)", old_state.name, new_state.name, reason or "unspecified")
            from core.conversation_state import ConversationState
            if new_state == ConversationState.STANDBY:
                self._wake_active_until = 0.0
                self._hotkey_active_until = 0.0
                if self.ui and hasattr(self.ui, "set_state"):
                    self.ui.set_state("IDLE")
                try:
                    from core.ducking_controller import ducking_controller
                    ducking_controller.restore()
                except Exception:
                    pass
            elif new_state == ConversationState.LISTENING:
                if self.ui and hasattr(self.ui, "set_state"):
                    self.ui.set_state("LISTENING")
                try:
                    from core.ducking_controller import ducking_controller, DuckingState
                    ducking_controller.set_state(DuckingState.LISTENING)
                except Exception:
                    pass
            elif new_state == ConversationState.THINKING:
                if self.ui and hasattr(self.ui, "set_state"):
                    self.ui.set_state("THINKING")
            elif new_state == ConversationState.EXECUTING:
                if self.ui and hasattr(self.ui, "set_state"):
                    self.ui.set_state("EXECUTING")
            elif new_state == ConversationState.SPEAKING:
                if self.ui and hasattr(self.ui, "set_state"):
                    self.ui.set_state("SPEAKING")
                try:
                    from core.ducking_controller import ducking_controller, DuckingState
                    ducking_controller.set_state(DuckingState.SPEAKING)
                except Exception:
                    pass
            elif new_state == ConversationState.FOLLOW_UP:
                if self.ui and hasattr(self.ui, "set_state"):
                    self.ui.set_state("LISTENING")
                try:
                    from core.ducking_controller import ducking_controller, DuckingState
                    ducking_controller.set_state(DuckingState.LISTENING)
                except Exception:
                    pass
            elif new_state == ConversationState.INTERRUPTED:
                if self.ui and hasattr(self.ui, "set_state"):
                    self.ui.set_state("LISTENING")
            elif new_state == ConversationState.RECONNECTING:
                if self.ui and hasattr(self.ui, "set_state"):
                    self.ui.set_state("RECONNECTING")

        self.state_machine.subscribe(_on_state_change)

    def _on_wake_spotted(self):
        """Реакция на фиксацию ключевого слова 'Джарвис'."""
        self._begin_new_utterance()
        self._pending_gemini_turn.addressed = True
        self._pending_gemini_turn.addressed_at = time.monotonic()
        self._wake_active_until = time.monotonic() + 8.0
        self._chime_until = 0.0

        try:
            from core.wakeword import play_activation_chime
            play_activation_chime()
        except Exception:
            pass
        if self.ui and hasattr(self.ui, "bring_to_front"):
            try:
                self.ui.bring_to_front()
            except Exception:
                pass

    def _on_speech_frame(self, frame_bytes: bytes):
        """Отправка кадра речи пользователя в очередь исходящего аудио для Gemini."""
        if not self.out_queue or not getattr(self, "_loop", None) or not self._loop.is_running():
            return
        self._loop.call_soon_threadsafe(
            self._put_nowait_safe,
            {"data": frame_bytes, "mime_type": "audio/pcm"},
        )

    def _put_nowait_safe(self, item):
        """Отправка в out_queue с защитой от переполнения (в потоке цикла событий)."""
        if not self.out_queue:
            return
        try:
            self.out_queue.put_nowait(item)
        except asyncio.QueueFull:
            pass

    def _drain_out_queue(self):
        """Опустошает очередь исходящего аудио. Только в потоке цикла событий."""
        if not self.out_queue:
            return
        while not self.out_queue.empty():
            try:
                self.out_queue.get_nowait()
            except Exception:
                break

    def _drain_out_queue_threadsafe(self):
        """Опустошение out_queue из любого потока.

        `asyncio.Queue` не потокобезопасна: её `get_nowait()` из аудиоворкера
        рвёт внутренние счётчики и будит ожидающих в чужом потоке.
        """
        if not self.out_queue:
            return
        loop = getattr(self, "_loop", None)
        if loop and loop.is_running() and threading.current_thread() is not threading.main_thread():
            loop.call_soon_threadsafe(self._drain_out_queue)
        else:
            self._drain_out_queue()

    def _handle_quick_command(self, cmd: str):
        """Быстрая команда без слова «Джарвис». Вызывается из аудиоворкера.

        Само исполнение уходит в отдельный поток. Роутер выглядит мгновенным
        только на медиа-клавишах: «полный экран», «перемотай», «включи фильм»
        идут в `actions/movie_player.py`, где есть паузы до 2 секунд, а
        «посмотри на экран» — вообще снимок экрана и запрос к модели. Пока это
        крутилось прямо в воркере, конвейер на всё это время переставал
        разбирать кадры: ограниченная очередь (100 кадров ≈ 3 с) переполнялась,
        звук терялся, ключевое слово и перебивание не работали.
        """
        logger.info("[Spotterless] ⚡ Быстрая команда: '%s'", cmd)
        # Если Джарвис говорит сам — немедленно прерываем речь
        self.interrupt_speech("quick-command-barge-in")

        sm = getattr(self, "state_machine", None)
        if sm:
            from core.conversation_state import ConversationState
            # Из ожидания в исполнение напрямую нельзя: пользователь всё-таки
            # что-то сказал, поэтому проходим через LISTENING.
            if sm.state == ConversationState.STANDBY:
                sm.transition_to(ConversationState.LISTENING, reason="quick command heard")
            sm.transition_to(ConversationState.EXECUTING, reason=f"fast path: {cmd}")

        # Single-execution guarantee: сбрасываем out_queue, чтобы та же фраза
        # не ушла в облако и не выполнилась вторым заходом.
        # asyncio.Queue не потокобезопасна, а сюда мы приходим из аудиоворкера,
        # поэтому чистим строго в потоке цикла событий.
        self._drain_out_queue_threadsafe()

        thread = threading.Thread(
            target=self._run_quick_command,
            args=(cmd, sm),
            name="JARVIS-FastCommand",
            daemon=True,
        )
        self._fast_command_thread = thread
        thread.start()

    def _run_quick_command(self, cmd: str, sm):
        """Собственно исполнение быстрой команды — в отдельном потоке."""
        try:
            from core.fast_command_router import FastCommandRouter
            res = FastCommandRouter.match_and_execute(cmd, player=self.ui)
            handled, resp = res
            if handled:
                self._wake_active_until = 0.0  # Шлюз остаётся закрытым для облака
                if self.ui:
                    self.ui.write_log(f"⚡ [Быстрая команда]: {cmd}")
                    if resp:
                        self.ui.write_log(f"Джарвис: {resp}")
                # Физические действия не требуют речи — немедленно восстанавливаем фоновый звук
                if getattr(res, "is_action", False):
                    try:
                        from core.ducking_controller import ducking_controller
                        ducking_controller.restore()
                    except Exception:
                        pass
                    if sm:
                        from core.conversation_state import ConversationState
                        sm.transition_to(ConversationState.STANDBY)
                # Если это не физическое действие, а информационный ответ (напр. трек) — озвучиваем
                elif resp:
                    if sm:
                        from core.conversation_state import ConversationState
                        sm.transition_to(ConversationState.SPEAKING)
                    if getattr(self, "_loop", None) and self._loop.is_running() and get_voice_provider() == "fish":
                        asyncio.run_coroutine_threadsafe(self._async_start_speech(resp), self._loop)
                elif sm:
                    from core.conversation_state import ConversationState
                    sm.transition_to(ConversationState.STANDBY)
            else:
                if sm:
                    from core.conversation_state import ConversationState
                    sm.transition_to(ConversationState.STANDBY)
        except Exception as exc:
            logger.error("Ошибка исполнения быстрой команды: %s", exc)
            if sm:
                from core.conversation_state import ConversationState
                sm.transition_to(ConversationState.STANDBY)

    def _on_hotkey_wake(self):
        """Реакция на глобальный хоткей F8 / Ctrl+Shift+J из любого приложения или игры."""
        logger.info("[Hotkey] Нажата горячая клавиша вызова Джарвиса (F8)")
        self._begin_new_utterance()
        self._pending_gemini_turn.addressed = True
        self._hotkey_active_until = time.monotonic() + 10.0

        # Снимаем мьют ДО перехода: из MUTED сразу в LISTENING нельзя, сначала
        # надо вернуться в STANDBY — иначе переход отклонялся и машина
        # оставалась в MUTED при живом микрофоне.
        if self.ui.muted:
            self.ui.toggle_mute()
            self.ui.write_log("SYS: ⚡ Микрофон активирован по горячей клавише F8.")
        else:
            self.ui.write_log("SYS: ⚡ Активация по горячей клавише F8.")
        sm = getattr(self, "state_machine", None)
        if sm:
            from core.conversation_state import ConversationState
            if sm.state == ConversationState.MUTED:
                sm.transition_to(ConversationState.STANDBY, reason="unmuted by hotkey")
            sm.transition_to(ConversationState.LISTENING, reason="hotkey wake")
        try:
            self.ui.bring_to_front()
        except Exception:
            pass
        try:
            from core.ducking_controller import ducking_controller
            ducking_controller.duck()
        except Exception:
            pass

    def _on_hotkey_mute(self):
        """Реакция на глобальный хоткей Ctrl+Shift+M из любого приложения."""
        self.ui.toggle_mute()
        sm = getattr(self, "state_machine", None)
        if sm:
            from core.conversation_state import ConversationState
            if self.ui.muted:
                sm.transition_to(ConversationState.MUTED)
            else:
                sm.transition_to(ConversationState.STANDBY)
        try:
            from core.earcons import play_mute_earcon
            play_mute_earcon(self.ui.muted)
        except Exception:
            pass

    def _start_telegram_bot(self):
        """Гарантирует единую связь с Telegram через PC Bridge (pc_server)."""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", 47821))
                sock.close()
                is_running = False
            except OSError:
                is_running = True

            if is_running:
                logger.info("PC Bridge уже подключён к Telegram (порт 47821 занят).")
                self.ui.write_log("SYS: 🔗 Связь с Telegram активна (PC Bridge на связи).")
                return None

            from telegram_bot.config import load as load_config
            cfg = load_config(require_bot=False)
            if not cfg.pc_link_url and not cfg.telegram_token:
                return None

            py_exec = sys.executable
            if getattr(sys, "frozen", False):
                import shutil
                py_exec = shutil.which("python") or shutil.which("python3")
                if not py_exec:
                    logger.info("Автозапуск PC Bridge в режиме frozen .exe пропущен (python не найден в PATH)")
                    return None

            proc = subprocess.Popen(
                [py_exec, "-m", "telegram_bot.pc_server"],
                cwd=str(BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            logger.info("PC Bridge к Telegram запущен в фоне (PID %d)", proc.pid)
            self.ui.write_log("SYS: 🔗 Связь с Telegram подключена (PC Bridge запущен).")
            return proc
        except Exception as exc:
            logger.warning("Не удалось запустить PC Bridge: %s", exc)
            return None

    def cleanup(self):
        """Корректное освобождение системных ресурсов и дочерних процессов."""
        thread = getattr(self, "_fast_command_thread", None)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if hasattr(self, "audio_pipeline") and self.audio_pipeline:
            try:
                self.audio_pipeline.stop()
            except Exception:
                pass
            self.audio_pipeline = None
        if hasattr(self, "_hotkey_mgr") and self._hotkey_mgr:
            try:
                self._hotkey_mgr.stop()
            except Exception:
                pass
            self._hotkey_mgr = None
        if hasattr(self, "_telegram_proc") and self._telegram_proc:
            proc = self._telegram_proc
            self._telegram_proc = None
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
            except Exception as e:
                logger.debug("Telegram process cleanup error: %s", e)

    # ── Текстовый ввод ────────────────────────────────────────────────────────
    def _send_text_to_session(self, text: str):
        """Отправляет текстовое сообщение в Live-сессию с корректной структурой типов."""
        if not self._loop or not self.session or not self._loop.is_running() or not text:
            return
        content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=text)],
        )
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns=[content],
                turn_complete=True,
            ),
            self._loop,
        )

    def _on_text_command(self, text: str):
        # Normalize text before sending
        text = self._normalize_input_text(text)
        if not text:
            return

        self._begin_new_utterance()
        self._pending_gemini_turn.addressed = True  # напечатанное всегда адресовано Джарвису
        self._hotkey_active_until = time.monotonic() + 15.0
        if self.ui and hasattr(self.ui, "write_log"):
            self.ui.write_log(f"Вы: {text}")

        # 1. FastCommandRouter: проверка быстрых директивных команд
        from core.fast_command_router import FastCommandRouter
        fast_res = FastCommandRouter.match_and_execute(text, self.ui)
        if fast_res[0]:
            if fast_res[1] and self.ui:
                self.ui.write_log(f"Джарвис: {fast_res[1]}")
            return

        # 2. CommandOrchestrator: проверка многошаговых и контекстных команд
        orch_res = None
        if hasattr(self, "command_orchestrator"):
            from core.command_orchestrator import RoutingDecision
            orch_res = self.command_orchestrator.process_user_text(text, player=self.ui)
            if orch_res.decision != RoutingDecision.NEEDS_LLM and orch_res.decision != RoutingDecision.IGNORED:
                if orch_res.decision == RoutingDecision.CONSUMED_ACTION:
                    msg = orch_res.executed_result or "Готово, сэр."
                    if self.ui:
                        self.ui.write_log(f"Джарвис: {msg}")
                    if get_voice_provider() == "fish":
                        self._start_speech(msg, command_id=orch_res.command_id, turn_id=orch_res.turn_id)
                elif orch_res.decision == RoutingDecision.CONSUMED_CLARIFICATION:
                    prompt = orch_res.prompt_to_user or "Уточните, сэр."
                    if self.ui:
                        self.ui.write_log(f"Джарвис: {prompt}")
                    self._wake_active_until = time.monotonic() + 15.0
                    if get_voice_provider() == "fish":
                        self._start_speech(prompt, command_id=orch_res.command_id, turn_id=orch_res.turn_id)
                elif orch_res.decision == RoutingDecision.CONSUMED_CANCEL:
                    cancel_msg = orch_res.prompt_to_user or "Отменено, сэр."
                    if self.ui:
                        self.ui.write_log(f"Джарвис: {cancel_msg}")
                    if get_voice_provider() == "fish":
                        self._start_speech(cancel_msg, command_id=orch_res.command_id, turn_id=orch_res.turn_id)
                return

        # 3. Gemini Live session fallback
        out_query = orch_res.cleaned_text if (orch_res and orch_res.cleaned_text) else text
        self._send_text_to_session(out_query)


    def _normalize_input_text(self, text: str) -> str:
        """Normalize user input text for better intent parsing."""
        return _clean_dialog_text(text)

    # ── Управление состоянием ─────────────────────────────────────────────────
    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        sm = getattr(self, "state_machine", None)
        if value:
            if sm:
                from core.conversation_state import ConversationState
                if sm.can_transition_to(ConversationState.SPEAKING):
                    sm.transition_to(ConversationState.SPEAKING)
                else:
                    sm.transition_to(ConversationState.SPEAKING, force=True)
            else:
                self.ui.set_state("SPEAKING")
                try:
                    from core.ducking_controller import ducking_controller, DuckingState
                    ducking_controller.set_state(DuckingState.SPEAKING)
                except Exception:
                    pass
        else:
            if sm:
                from core.conversation_state import ConversationState
                if sm.state in (ConversationState.SPEAKING, ConversationState.FOLLOW_UP):
                    if time.monotonic() < getattr(self, "_wake_active_until", 0.0):
                        rem_time = max(1.0, self._wake_active_until - time.monotonic())
                        sm.start_follow_up(timeout_sec=rem_time)
                    else:
                        if sm.state != ConversationState.STANDBY:
                            sm.transition_to(ConversationState.STANDBY)
            elif not self.ui.muted:
                self.ui.set_state("LISTENING")
                try:
                    from core.ducking_controller import ducking_controller, DuckingState
                    if time.monotonic() < getattr(self, "_wake_active_until", 0.0):
                        ducking_controller.set_state(DuckingState.LISTENING)
                    else:
                        ducking_controller.set_state(DuckingState.RESTORING)
                except Exception:
                    pass

    def _abort_playback(self):
        """Снимает задачи синтеза и опустошает буфер воспроизведения.

        Только в потоке цикла событий: и таски, и `asyncio.Queue` принадлежат ему.
        """
        self._streaming_speech_active = False
        if getattr(self, "_streaming_queue", None) is not None:
            while not self._streaming_queue.empty():
                try:
                    self._streaming_queue.get_nowait()
                except Exception:
                    break
            try:
                self._streaming_queue.put_nowait(None)
            except Exception:
                pass
            self._streaming_queue = None

        for task in list(getattr(self, "_active_speech_tasks", set())):
            if not task.done():
                task.cancel()
        if hasattr(self, "_active_speech_tasks"):
            self._active_speech_tasks.clear()

        self._clear_audio_in_queue()

    def _clear_audio_in_queue(self):
        """Очищает очередь воспроизведения аудио."""
        if self.audio_in_queue:
            while not self.audio_in_queue.empty():
                try:
                    self.audio_in_queue.get_nowait()
                except Exception:
                    break

    def _abort_playback_threadsafe(self):
        """Прерывание воспроизведения из любого потока."""
        loop = getattr(self, "_loop", None)
        if loop and loop.is_running() and threading.current_thread() is not threading.main_thread():
            loop.call_soon_threadsafe(self._abort_playback)
        else:
            self._abort_playback()

    def interrupt_speech(self, reason: str = "barge-in"):
        """Мгновенное аппаратное и программное прерывание речи Джарвиса на полуслове (Barge-In)."""
        with self._speaking_lock:
            if not self._is_speaking and getattr(self, "_active_synth_tasks", 0) == 0 and (not self.audio_in_queue or self.audio_in_queue.empty()):
                return
            logger.info("[Barge-In] ⚡ Прерывание речи Джарвиса (%s)", reason)
            self._is_speaking = False
            self._active_synth_tasks = 0
            self._speech_epoch = getattr(self, "_speech_epoch", 0) + 1
            self._current_generation_id = getattr(self, "_current_generation_id", 0) + 1
            self._active_speech_generation_id = self._current_generation_id
            self._interrupted_turn = True
            from core import wake_policy
            self._wake_active_until = time.monotonic() + wake_policy.gate_after_interrupt(reason)
            if getattr(self, "_pending_gemini_turn", None):
                self._pending_gemini_turn.clear()

        # State transition to INTERRUPTED
        sm = getattr(self, "state_machine", None)
        if sm:
            from core.conversation_state import ConversationState
            # Из STANDBY перебивать нечего — переход туда и не нужен.
            if sm.state != ConversationState.STANDBY:
                sm.transition_to(ConversationState.INTERRUPTED, reason=reason)

        self._drop_speech_prefetch()

        # 1. Отмена задач синтеза и очистка буфера воспроизведения.
        self._abort_playback_threadsafe()

        # 2. Переход в LISTENING
        if sm:
            from core.conversation_state import ConversationState
            sm.transition_to(ConversationState.LISTENING, reason="listen after barge-in")
        self.set_speaking(False)
        if self.ui:
            self.ui.set_state("LISTENING")
            self.ui.write_log("⚡ [Прерван]: слушаю вас, сэр...")

        # 4. Акустический отклик подтверждения перебивания
        try:
            from core.earcons import play_success_earcon
            play_success_earcon()
        except Exception:
            pass

    def _start_speech(
        self,
        text: str,
        command_id: Optional[str] = None,
        turn_id: int = 0,
        generation_id: Optional[int] = None,
        utterance_id: Optional[str] = None,
    ):
        """Запускает воспроизведение речи с регистрацией в менеджере прерываний."""
        if not text:
            return None
        if not hasattr(self, "_active_speech_tasks"):
            self._active_speech_tasks = set()
        self._speech_epoch = getattr(self, "_speech_epoch", 0) + 1
        epoch = self._speech_epoch
        self._active_speech_command_id = command_id
        self._active_speech_turn_id = turn_id
        gen_id = generation_id if generation_id is not None else getattr(self, "_current_generation_id", 0)
        self._active_speech_generation_id = gen_id
        self._active_speech_utterance_id = utterance_id or getattr(self, "_current_utterance_id", "")
        self._interrupted_turn = False
        try:
            task = asyncio.create_task(
                self._speak_fish(
                    text,
                    epoch=epoch,
                    generation_id=gen_id,
                )
            )
        except Exception:
            task = asyncio.create_task(self._speak_fish(text, epoch=epoch))
        self._active_speech_tasks.add(task)
        task.add_done_callback(lambda t: getattr(self, "_active_speech_tasks", set()).discard(t))
        return task


    def _maybe_prefetch_speech(self, raw_text: str):
        """Запускает синтез первой фразы, не дожидаясь конца генерации.

        Замер на этой машине: тёплый запрос к Fish — около 690 мс и почти не
        зависит от длины текста (это сетевой round-trip, а не генерация звука).
        Раньше он стартовал только на turn_complete, то есть после того, как
        модель договорила ВЕСЬ ответ. Здесь запрос уходит, как только первая
        фраза окончательно сформирована, и его время прячется за генерацией
        остатка.

        Именно предзагрузка, а не ранняя проигровка: играть до конца хода нельзя,
        иначе сломается барж-ин и придётся озвучивать переспрос, который позже
        решено было укоротить.
        """
        if self._speech_prefetch is not None or getattr(self, "_streaming_speech_active", False) or get_voice_provider() != "fish":
            return
        loop = getattr(self, "_loop", None)
        if not loop or not loop.is_running():
            return
        cleaned = _clean_dialog_text(raw_text)
        if not cleaned:
            return
        chunks = _split_for_speech(cleaned)
        # Пока кусок один, он ещё может дорасти и слиться со следующей фразой —
        # тогда предзагруженный текст не совпадёт с тем, что будут озвучивать.
        # Начиная со второго куска первый уже окончателен.
        if len(chunks) < 2:
            return
        try:
            from telegram_bot import tts_fish
            if not tts_fish.is_configured():
                return
            task = asyncio.create_task(
                tts_fish.speak_pcm(chunks[0], sample_rate=RECV_SAMPLE_RATE)
            )
            self._speech_prefetch = (chunks[0], task)
            logger.info("TTS: синтез первой фразы запущен наперёд (%d симв)", len(chunks[0]))
        except Exception as e:
            logger.debug("Speech prefetch note: %s", e)

    def _take_prefetched_speech(self, fragment: str):
        """Отдаёт заранее запущенную задачу синтеза, если текст совпал."""
        pre = self._speech_prefetch
        if not pre or pre[0] != fragment:
            return None
        self._speech_prefetch = None
        return pre[1]

    def _drop_speech_prefetch(self):
        """Снимает предзагрузку: ход отменён или начался новый."""
        pre = getattr(self, "_speech_prefetch", None)
        self._speech_prefetch = None
        if pre and not pre[1].done():
            pre[1].cancel()
        self._streaming_speech_active = False
        if getattr(self, "_streaming_queue", None) is not None:
            while not self._streaming_queue.empty():
                try:
                    self._streaming_queue.get_nowait()
                except Exception:
                    break
            try:
                self._streaming_queue.put_nowait(None)
            except Exception:
                pass
            self._streaming_queue = None

    def _maybe_stream_speech(self, raw_text: str):
        """Запускает потоковый синтез и воспроизведение предложений по мере генерации ответа Gemini."""
        if get_voice_provider() != "fish":
            return
        loop = getattr(self, "_loop", None)
        if not loop or not loop.is_running():
            return
        pt = getattr(self, "_pending_gemini_turn", None)
        if pt:
            if pt.routed_to in _SILENT_ROUTES or pt.routed_to == "LOCAL":
                return
            if not pt.arbitrated:
                return

        cleaned = _clean_dialog_text(raw_text)
        if not cleaned:
            return
        chunks = _split_for_speech(cleaned)
        if len(chunks) < 2:
            return

        try:
            from telegram_bot import tts_fish
            if not tts_fish.is_configured():
                return
        except Exception:
            return

        # Если стриминг ещё не запущен для текущей реплики:
        if not getattr(self, "_streaming_speech_active", False):
            self._streaming_speech_active = True
            self._streaming_queue = asyncio.Queue()
            self._streamed_chunks_indices = set()

            self._speech_epoch = getattr(self, "_speech_epoch", 0) + 1
            epoch = self._speech_epoch
            gen_id = getattr(self, "_current_generation_id", 0)
            self._active_speech_generation_id = gen_id
            self._active_speech_utterance_id = getattr(self, "_current_utterance_id", "")
            self._interrupted_turn = False

            task = asyncio.create_task(
                self._run_streaming_speech(
                    self._streaming_queue,
                    epoch=epoch,
                    generation_id=gen_id,
                )
            )
            if not hasattr(self, "_active_speech_tasks"):
                self._active_speech_tasks = set()
            self._active_speech_tasks.add(task)
            task.add_done_callback(lambda t: getattr(self, "_active_speech_tasks", set()).discard(t))
            logger.info("TTS Stream: стриминг речи запущен (epoch=%d, gen=%d)", epoch, gen_id)

        # Отправляем все готовые завершённые куски (кроме последнего, т.к. он ещё может дописываться)
        for idx in range(len(chunks) - 1):
            if idx not in self._streamed_chunks_indices:
                self._streamed_chunks_indices.add(idx)
                if self._streaming_queue is not None:
                    self._streaming_queue.put_nowait(chunks[idx])
                    logger.info("TTS Stream: чанк #%d отправлен в очередь (%d симв): '%.30s...'",
                                idx, len(chunks[idx]), chunks[idx])

    async def _iter_fragment_audio(self, fragment: str, epoch: int, generation_id, voice: dict):
        """Сэмплы одного фрагмента ответа по мере готовности.

        Порядок: заранее заказанная первая фраза → поток Fish → Edge-TTS.
        `voice["fish_alive"]` общий на весь ответ: отказавший Fish опрашивается
        один раз, остаток договаривает Edge. Пусто = озвучить нечем.
        """
        def _stale() -> bool:
            if getattr(self, "_speech_epoch", 0) != epoch or not getattr(self, "_is_speaking", False):
                return True
            return generation_id is not None and getattr(self, "_active_speech_generation_id", None) != generation_id

        if _stale():
            return

        from telegram_bot import tts_fish
        from telegram_bot import tts_edge

        async def _edge() -> bytes | None:
            return await tts_edge.speak_pcm(fragment, sample_rate=RECV_SAMPLE_RATE)

        # Готовая фраза с диска — раньше любого синтеза. Только тембр Fish.
        cache = None
        if voice.get("fish_alive"):
            try:
                cache = get_voice_cache(RECV_SAMPLE_RATE)
                cached = cache.get(fragment)
            except Exception as e:
                logger.debug("Кэш голоса недоступен: %s", e)
                cached = None
            if cached:
                logger.info("TTS: фраза из кэша (%d симв)", len(fragment))
                yield cached
                return

        # Источник Fish: заранее заказанная первая фраза (задача с целым PCM)
        # либо поток. Оба проходят через один хедж по первому звуку.
        taker = getattr(self, "_take_prefetched_speech", None)
        prefetched = taker(fragment) if taker else None
        fish = None
        if prefetched is not None:
            first_task = prefetched
        elif voice.get("fish_alive"):
            fish = tts_fish.stream_pcm(fragment, sample_rate=RECV_SAMPLE_RATE)
            first_task = asyncio.ensure_future(fish.__anext__())
        else:
            if _stale():
                return
            pcm = await _edge()
            if pcm:
                yield pcm
            return

        async def _close_fish():
            if not first_task.done():
                first_task.cancel()
                await asyncio.gather(first_task, return_exceptions=True)
            if fish is not None:
                await fish.aclose()

        edge_task = None
        try:
            done, _ = await asyncio.wait({first_task}, timeout=_TTS_FIRST_AUDIO_TIMEOUT_SEC)
            if not done:
                logger.warning(
                    "TTS: Fish молчит %.0f с — параллельно поднимаю Edge-TTS", _TTS_FIRST_AUDIO_TIMEOUT_SEC
                )
                edge_task = asyncio.ensure_future(_edge())
                done, _ = await asyncio.wait({first_task, edge_task}, return_when=asyncio.FIRST_COMPLETED)

            if edge_task is not None and edge_task in done and first_task not in done:
                try:
                    pcm = edge_task.result()
                except Exception as e:
                    logger.debug("Edge hedge unusable: %s", e)
                    pcm = None
                if pcm:
                    await _close_fish()
                    if not _stale():
                        if hasattr(self, "ui") and hasattr(self.ui, "write_log"):
                            self.ui.write_log("SYS: Fish тормозит — фразу озвучил Edge-TTS")
                        yield pcm
                    return
                # Edge тоже промолчал — остаётся дождаться Fish
                logger.warning("TTS: Edge не дал звука — жду Fish")
                await asyncio.wait({first_task})

            try:
                first_pcm = first_task.result()
            except (StopAsyncIteration, asyncio.CancelledError):
                first_pcm = None
            except Exception as e:
                logger.debug("Fish first audio unusable: %s", e)
                first_pcm = None
            if edge_task is not None:
                edge_task.cancel()
            if not first_pcm:
                voice["fish_alive"] = False
                if hasattr(self, "ui") and hasattr(self.ui, "write_log"):
                    self.ui.write_log("SYS: Fish молчит — остаток ответа озвучит Edge-TTS")
                if _stale():
                    return
                pcm = await _edge()
                if pcm:
                    yield pcm
                return

            if _stale():
                return
            collected = [first_pcm]
            yield first_pcm
            if fish is not None:
                async for pcm in fish:
                    if _stale():
                        return
                    collected.append(pcm)
                    yield pcm
            # Фраза прозвучала целиком голосом Fish и ход не перебит — в кэш.
            # Половина фразы на диске хуже, чем ничего, поэтому только здесь.
            if cache is not None and not _stale():
                try:
                    cache.put(fragment, b"".join(collected))
                except Exception as e:
                    logger.debug("Кэш голоса: не записал: %s", e)
        finally:
            await _close_fish()
            if edge_task is not None and not edge_task.done():
                edge_task.cancel()

    async def _push_fragment_audio(self, fragment: str, epoch: int, generation_id, voice: dict) -> int:
        """Гонит сэмплы фрагмента в очередь воспроизведения. Возвращает байты."""
        step = CHUNK_SIZE * 2
        pushed = 0
        started = time.perf_counter()
        first_at = None
        async for pcm in self._iter_fragment_audio(fragment, epoch, generation_id, voice):
            if first_at is None:
                first_at = time.perf_counter()
                if not pushed and hasattr(self, "_latency") and hasattr(self._latency, "mark_answer_audio"):
                    self._latency.mark_answer_audio()
            if not self.audio_in_queue:
                pushed += len(pcm)
                continue
            for j in range(0, len(pcm), step):
                if getattr(self, "_speech_epoch", 0) != epoch or not getattr(self, "_is_speaking", False):
                    return pushed
                if generation_id is not None and getattr(self, "_active_speech_generation_id", None) != generation_id:
                    return pushed
                piece = pcm[j:j + step]
                try:
                    self.audio_in_queue.put_nowait(piece)
                except asyncio.QueueFull:
                    await self.audio_in_queue.put(piece)
                pushed += len(piece)
        logger.info(
            "TTS: фрагмент (%d симв) — первый звук через %.0f мс, весь %.0f мс, %.1f с звука",
            len(fragment),
            ((first_at - started) * 1000) if first_at else -1,
            (time.perf_counter() - started) * 1000,
            pushed / 2 / RECV_SAMPLE_RATE,
        )
        return pushed

    async def _run_streaming_speech(
        self,
        queue: asyncio.Queue,
        epoch: int | None = None,
        generation_id: int | None = None,
    ):
        """Фоновый потребитель очереди предложений: синтезирует и воспроизводит чанки по мере готовности."""
        with getattr(self, "_speaking_lock", threading.Lock()):
            self._active_synth_tasks = getattr(self, "_active_synth_tasks", 0) + 1
        self.set_speaking(True)

        if epoch is None:
            epoch = getattr(self, "_speech_epoch", 0)

        if generation_id is not None and getattr(self, "_active_speech_generation_id", None) != generation_id:
            if hasattr(self, "_latency") and hasattr(self._latency, "mark_turn_complete"):
                self._latency.mark_turn_complete()
            with getattr(self, "_speaking_lock", threading.Lock()):
                self._active_synth_tasks = max(0, getattr(self, "_active_synth_tasks", 1) - 1)
            return

        try:
            from telegram_bot import tts_fish

            fish_alive = tts_fish.is_configured()
            if not fish_alive and not getattr(self, "_fish_unconfigured_logged", False):
                self._fish_unconfigured_logged = True
                logger.warning(
                    "Голос Fish выбран, но ключ не найден — отвечать будет "
                    "Edge-TTS. Проверьте fish_api_key в %s", API_CONFIG
                )
                if hasattr(self, "ui") and hasattr(self.ui, "write_log"):
                    self.ui.write_log("SYS: ключ Fish не найден — голос звучит через Edge-TTS")

            voice = {"fish_alive": fish_alive}
            spoken = 0
            chunks_count = 0

            while True:
                if getattr(self, "_speech_epoch", 0) != epoch or not getattr(self, "_is_speaking", False):
                    break
                if generation_id is not None and getattr(self, "_active_speech_generation_id", None) != generation_id:
                    break

                try:
                    fragment = await asyncio.wait_for(queue.get(), timeout=20.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    break

                if fragment is None:
                    # Маркер завершения стрима
                    break

                pushed = await self._push_fragment_audio(fragment, epoch, generation_id, voice)
                if getattr(self, "_speech_epoch", 0) != epoch or not getattr(self, "_is_speaking", False):
                    break

                if not pushed:
                    if getattr(self, "_speech_epoch", 0) == epoch and getattr(self, "_is_speaking", False):
                        if hasattr(self, "ui") and hasattr(self.ui, "write_log"):
                            self.ui.write_log("SYS: синтез речи недоступен — ответ остался текстом")
                    break

                spoken += pushed
                chunks_count += 1

            if spoken and getattr(self, "_speech_epoch", 0) == epoch:
                logger.info("Голос Fish (stream): %.1f с звука, %d фрагмент(ов)",
                            spoken / 2 / RECV_SAMPLE_RATE, chunks_count)
            if hasattr(self, "_latency") and hasattr(self._latency, "mark_turn_complete"):
                self._latency.mark_turn_complete()
        except asyncio.CancelledError:
            pass
        finally:
            with getattr(self, "_speaking_lock", threading.Lock()):
                self._active_synth_tasks = max(0, getattr(self, "_active_synth_tasks", 1) - 1)

    async def _async_start_speech(self, text: str):
        self._start_speech(text)

    def speak(self, text: str):
        """Отправляет текст в сессию для озвучки."""
        if text:
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
            f"Используй исключительно для точного расчёта напоминаний и дат.\n"
            f"КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выводить строку '[ТЕКУЩЕЕ ВРЕМЯ ...]' или любые другие теги в квадратных скобках вслух или в текст!\n\n"
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

        wake_anchor = (
            "[ПРОТОКОЛ АКТИВАЦИИ И ВЫЗОВА]\n"
            "Твоё имя: Джарвис (JARVIS). Собеседник говорит по-русски.\n"
            "Если пользователь обратился к тебе только по имени ('Джарвис', 'Jarvis') или позвал без конкретного вопроса/команды, коротко и вежливо откликнись голосом: 'Да, сэр?', 'Слушаю вас', 'На связи' или 'Да?' и жди указаний.\n"
            "Когда к тебе обращаются с вопросом или поручением — отвечай чётко и по существу.\n"
            "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО реагировать на фоновый шум комнаты, покашливания, вздохи или случайные обрывки звуков.\n"
            "НИКОГДА не говори фразы вроде 'я не расслышал', 'не мог бы повторить', 'из-за шума не могу разобрать'.\n"
            "Если входящий аудиофрагмент — фоновый шум или тишина БЕЗ обращения к тебе по имени, СОХРАНЯЙ ПОЛНОЕ МОЛЧАНИЕ (пустой ответ)!\n"
            "Никогда не выводи вслух и в текст системные теги, заголовки в скобках [ТЕКУЩЕЕ ВРЕМЯ ...] и метаданные.\n\n"
        )
        lang_anchor = (
            "[ОСНОВНОЙ ЯЗЫК: РУССКИЙ]\n"
            "Собеседник говорит по-русски (либо узбекский/казахский). Твоё имя: Джарвис (JARVIS).\n"
            "Все входящие акустические фразы ('Čia Harvis', 'Harvis', 'Jarvis') расшифровывай и воспринимай строго как обращение 'Джарвис' на русском языке.\n\n"
        )
        # Подгрузка заметок Obsidian в системный контекст
        try:
            from actions.obsidian import get_recent_obsidian_notes
            obsidian_ctx = get_recent_obsidian_notes(limit=3)
        except Exception:
            obsidian_ctx = ""

        parts = [lang_anchor, wake_anchor, time_ctx]
        if mode_ctx:
            parts.append(mode_ctx)
        if profile_str:
            parts.append(profile_str)
        if obsidian_ctx:
            parts.append(obsidian_ctx)
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        # Все инструменты объявляются модели. Раньше фильмы, музыка, приложения,
        # настройки ПК и окна вырезались отсюда «в пользу локального контроллера»,
        # а он понимает лишь узкий набор фраз — всё остальное модель физически
        # не могла выполнить и отвечала словами вместо действия.
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},  # Без language_code (Pydantic не принимает)
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOLS}],
            session_resumption=types.SessionResumptionConfig(),
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
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
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

        # Двойное исполнение отсекается в цикле приёма: если ход уже забрал
        # локальный контроллер, tool_call туда не доходит. Безусловная блокировка
        # локальных инструментов здесь стояла раньше и ломала всё, что локальные
        # регулярки не узнали: громкость, окна, музыку по имени исполнителя —
        # Gemini отвечала «готово», а действие не выполнялось вовсе.
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
        if _is_destructive(name, args):
            pending = self._pending_destructive
            same = pending and pending[0] == name and pending[1] == _action_of(args)
            fresh = same and (time.time() - pending[2]) < _CONFIRM_WINDOW_SEC
            if not fresh:
                self._pending_destructive = (name, _action_of(args), time.time())
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
                # Маппинг action → параметры
                action_en = args.get("action", "")
                action_map = {
                    "volume_up":       {"action": "увеличить громкость", "value": args.get("value", "10")},
                    "volume_down":     {"action": "уменьшить громкость", "value": args.get("value", "10")},
                    "mute":            {"action": "без звука"},
                    "brightness_up":   {"action": "увеличить яркость",  "value": args.get("value", "10")},
                    "brightness_down": {"action": "уменьшить яркость",  "value": args.get("value", "10")},
                    "screenshot":      {"action": "скриншот"},
                    "lock":            {"action": "заблокировать"},
                    "shutdown":        {"action": "выключить"},
                    "restart":         {"action": "перезагрузить"},
                }
                mapped = action_map.get(action_en, {"action": action_en, "value": args.get("value", "")})
                r = await loop.run_in_executor(
                    None, lambda: computer_settings(parameters=mapped, player=self.ui)
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

            # ── Инструмент: музыкальный плеер ──────────────────────
            elif name == "music_player":
                from actions.music_player import music_player
                r = await loop.run_in_executor(
                    None, lambda: music_player(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: управление окнами Windows ────────────────
            elif name == "window_control":
                r = await loop.run_in_executor(
                    None, lambda: window_control(parameters=args, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: автоматизированные сценарии (Routines) ───
            elif name == "execute_routine":
                from core.routines_engine import RoutinesEngine
                routine_name = args.get("routine_name", "morning")
                r = await loop.run_in_executor(
                    None, lambda: RoutinesEngine.execute(routine_name=routine_name, player=self.ui)
                )
                result = r or "Готово."

            # ── Инструмент: долгосрочная эпизодическая память ────────
            elif name == "query_memory":
                from core.episodic_memory import EpisodicMemory
                query_str = args.get("query", "")
                r = await loop.run_in_executor(
                    None, lambda: EpisodicMemory.recall(query=query_str)
                )
                result = r or "В памяти ничего не найдено."

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

            # ── Инструмент: ввод в терминал Claude Code ────────────────────
            elif name == "type_to_terminal":
                from actions.claude_terminal import execute_claude_typing
                text = args.get("text", "")
                press_enter = args.get("press_enter", True)
                loop = asyncio.get_event_loop()
                res = await loop.run_in_executor(
                    None, lambda: execute_claude_typing(text, press_enter=press_enter)
                )
                self.ui.write_log(f"SYS: ⌨ Терминал: '{text}'")
                result = res.get("text", "Готово, сэр.")

            # ── Инструмент: выключить ────────────────────────────────
            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Завершение работы...")
                self.speak("До свидания, сэр. Отключаюсь.")
                def _shutdown():
                    import time
                    import os
                    time.sleep(1.5)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Неизвестный инструмент: {name}"

        except asyncio.TimeoutError:
            result = f"Таймаут выполнения инструмента '{name}' (превышено время ожидания)."
            logger.warning("Tool %s timed out internally", name)
            if hasattr(self, "ui") and hasattr(self.ui, "write_log"):
                self.ui.write_log(f"SYS: ⏱ Таймаут инструмента '{name}'")
        except Exception as e:
            result = f"Ошибка инструмента '{name}': {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        # Обновление контекста (новый мозг)
        if name in ["movie_player", "music_player", "set_mode"]:
            activity = name
            if name == "movie_player":
                action = args.get("action", "")
                if action == "play":
                    title = args.get("title", "")
                    if title:
                        self.user_profile.add_to_history("recent_movies", title)
                        activity = f"watching_movie: {title}"
            elif name == "music_player":
                action = args.get("action", "")
                if action == "play":
                    query = args.get("query", "")
                    if query:
                        self.user_profile.add_to_history("recent_music", query)
                        activity = f"listening_music: {query}"
            elif name == "set_mode":
                mode = args.get("mode", "")
                activity = f"mode_{mode}"

            self.user_profile.update_context(activity=activity)

            # Запись действия для прогнозирования (новый мозг)
            context = {
                "time": datetime.now().strftime("%H:%M"),
                "emotion": self.user_profile.get_context().get("last_emotion", "neutral"),
                "mode": get_current_mode().get("mode", "normal")
            }
            self.proactive_engine.record_action(name, context)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[ДЖАРВИС] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(id=fc.id, name=name, response={"result": result})

    # ── Отправка аудио на сервер ──────────────────────────────────────────────
    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            if getattr(self, "_tool_in_progress", False):
                continue
            await self.session.send_realtime_input(media=msg)

    # ── Захват микрофона ──────────────────────────────────────────────────────

    def _push_level(self, value: float):
        """Отдаёт громкость окну. Зовётся из аудио-потока, поэтому дёшево и
        молча: замер для красоты не имеет права ни тормозить звук, ни падать."""
        try:
            self.ui.set_level(value)
        except Exception as exc:
            logger.debug("Уровень в HUD не ушёл: %s", exc, exc_info=True)

    def _start_aec_reference(self):
        """Поднимает опорный поток колонок (WASAPI loopback) для эхоподавления.

        Без него AECPipeline не выполняется ни разу: вычитать из микрофона
        нечего. Микрофонный поток остаётся за sounddevice в `_listen_audio` —
        здесь открывается ТОЛЬКО loopback.
        """
        if self._aec_reference is not None:
            return
        try:
            from core.audio_capture import AudioCaptureEngine
            engine = AudioCaptureEngine()
            engine.start(reference_only=True)
            if getattr(engine, "reference_active", False):
                self._aec_reference = engine
                logger.info("AEC: опорный поток колонок поднят, эхоподавление активно")
            else:
                engine.stop()
                logger.warning(
                    "AEC: опорный поток колонок недоступен — эхоподавление выключено, "
                    "перебивание работает только по ключевому слову"
                )
        except Exception as e:
            logger.warning("AEC: не удалось поднять опорный поток (%s), эхоподавление выключено", e)

    def _on_listen_silence_timeout(self):
        """Пользователь замолчал надолго — закрываем окно активности.

        Без этого шлюз держался до конца таймера wake-слова, и всё это время
        комнатный шум выше порога уезжал в Gemini Live. На непрерывном шуме
        модель начинает выдавать реплики на случайных языках, а извинения
        «не разобрал, повторите» открывают новое окно — и петля не кончается.
        """
        self._wake_active_until = 0.0
        self._hotkey_active_until = 0.0
        # Слово было, а речи за ним так и не последовало — обращение
        # не состоялось: фраза, пришедшая позже, к нему не относится.
        pt = getattr(self, "_pending_gemini_turn", None)
        if pt is not None and pt.addressed and pt.first_transcript_at is None:
            pt.addressed = False
        logger.info("Dialog: тишина затянулась — шлюз закрыт, жду обращения по имени")

    def _aec_reference_window(self, timestamp: float, n_bytes: int) -> bytes:
        """Опорный кадр колонок под текущий кадр микрофона.

        Вызывается прямо из аудиоколбэка, поэтому делает только срез кольцевого
        буфера. Часы — `perf_counter()`, те же, которыми помечается буфер
        в AudioCaptureEngine (`timestamp` конвейера идёт по monotonic и здесь
        не годится).
        """
        engine = self._aec_reference
        if engine is None:
            return b""
        try:
            return engine.ref_window(time.perf_counter(), n_bytes)
        except Exception:
            return b""

    async def _listen_audio(self):
        print("[ДЖАРВИС] 🎤 Микрофон запущен")

        # Опорный поток колонок поднимается в фоне: открытие WASAPI-устройства
        # занимает сотни миллисекунд, и делать это на пути к микрофону нельзя —
        # ровно на это время Джарвис оставался бы глухим при каждом старте.
        # До готовности loopback `_aec_reference_window` отдаёт пустоту, и AEC
        # честно считается неактивным.
        threading.Thread(
            target=self._start_aec_reference,
            name="JARVIS-AECReference",
            daemon=True,
        ).start()

        def callback(indata, frames, time_info, status):
            if status:
                logger.debug("PortAudio status: %s", status)
            mic_pcm = indata.tobytes()
            # Гарантированно неблокирующий вызов: кладёт в bounded-очередь pipeline.
            # Никакой обработки здесь быть не должно — PortAudio ждёт возврата за
            # единицы миллисекунд.
            if getattr(self, "audio_pipeline", None):
                # Секундомер отмечается по факту ЗАХВАТА громкого кадра, а не по
                # отправке из воркера: иначе задержка обработки в очереди выпадала
                # бы из замера, а расшифровка могла прийти раньше первой отметки —
                # и метрика «слышит» просто не считалась.
                if self._latency.enabled:
                    try:
                        rms = float(np.sqrt(np.mean(np.square(indata.astype(np.float32)))))
                        # Порог тот же, что у тракта (адаптирован под шум комнаты),
                        # иначе в шумной комнате хвост фона сдвигал точку отсчёта.
                        if rms >= getattr(self.audio_pipeline, "last_rms_threshold", MIC_RMS_THRESHOLD):
                            self._latency.mark_voice_frame()
                    except Exception:
                        pass
                self.audio_pipeline.push_frame(mic_pcm)
            else:
                self._push_level(min(1.0, float(np.sqrt(np.mean(np.square(indata.astype(np.float32))))) / _MIC_FULL_SCALE))

        backoff = 1.0
        max_backoff = 10.0

        while True:
            try:
                device_idx = _pick_input_device()
                with sd.InputStream(
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    device=device_idx,
                    latency="low",
                    callback=callback,
                ):
                    print("[ДЖАРВИС] 🎤 Поток микрофона открыт")
                    backoff = 1.0  # Сброс backoff при успешном открытии
                    sm = getattr(self, "state_machine", None)
                    if sm:
                        from core.conversation_state import ConversationState
                        if sm.state == ConversationState.RECONNECTING:
                            sm.transition_to(ConversationState.STANDBY)

                    while True:
                        await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Microphone device error: %s. Reconnecting in %.1fs...", e, backoff)
                if self.ui and hasattr(self.ui, "write_log"):
                    self.ui.write_log(f"SYS: Сбой микрофона ({e}). Переподключение через {backoff:.1f}с...")
                sm = getattr(self, "state_machine", None)
                if sm:
                    from core.conversation_state import ConversationState
                    if sm.state != ConversationState.RECONNECTING:
                        sm.transition_to(ConversationState.RECONNECTING)

                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2.0)

    async def _speak_fish(self, text: str, epoch: int | None = None, generation_id: int | None = None):
        """Озвучивает готовый ответ голосом Джарвиса из Telegram-бота."""
        with self._speaking_lock:
            self._active_synth_tasks += 1
        self.set_speaking(True)

        if epoch is None:
            epoch = getattr(self, "_speech_epoch", 0)

        if generation_id is not None and getattr(self, "_active_speech_generation_id", None) != generation_id:
            self._latency.mark_turn_complete()
            return

        try:
            from telegram_bot import tts_fish

            chunks = _split_for_speech(text)
            if not chunks or getattr(self, "_speech_epoch", 0) != epoch or not self._is_speaking:
                self._latency.mark_turn_complete()
                return

            fish_alive = tts_fish.is_configured()
            if not fish_alive:
                if not getattr(self, "_fish_unconfigured_logged", False):
                    self._fish_unconfigured_logged = True
                    logger.warning(
                        "Голос Fish выбран, но ключ не найден — отвечать будет "
                        "Edge-TTS. Проверьте fish_api_key в %s", API_CONFIG
                    )
                    self.ui.write_log("SYS: ключ Fish не найден — голос звучит через Edge-TTS")

            voice = {"fish_alive": fish_alive}
            spoken = 0

            for i, fragment in enumerate(chunks):
                if getattr(self, "_speech_epoch", 0) != epoch or not self._is_speaking or (generation_id is not None and getattr(self, "_active_speech_generation_id", None) != generation_id):
                    break

                # Следующий фрагмент заказываем целиком, пока звучит текущий:
                # его время прячется за воспроизведением, а первый — потоком.
                if i + 1 < len(chunks) and voice["fish_alive"] and getattr(self, "_speech_prefetch", None) is None and hasattr(self, "_take_prefetched_speech"):
                    self._speech_prefetch = (
                        chunks[i + 1],
                        asyncio.create_task(tts_fish.speak_pcm(chunks[i + 1], sample_rate=RECV_SAMPLE_RATE)),
                    )

                pushed = await self._push_fragment_audio(fragment, epoch, generation_id, voice)
                if getattr(self, "_speech_epoch", 0) != epoch or not self._is_speaking:
                    break

                if not pushed:
                    if getattr(self, "_speech_epoch", 0) == epoch and self._is_speaking:
                        self.ui.write_log("SYS: синтез речи недоступен — ответ остался текстом")
                    break
                spoken += pushed

            if spoken and getattr(self, "_speech_epoch", 0) == epoch:
                logger.info("Голос Fish: %.1f с звука, %d фрагмент(ов) на %d символов",
                            spoken / 2 / RECV_SAMPLE_RATE, len(chunks), len(text))
            self._latency.mark_turn_complete()
        except asyncio.CancelledError:
            pass
        finally:
            with self._speaking_lock:
                self._active_synth_tasks = max(0, self._active_synth_tasks - 1)

    # ── Арбитраж владения репликой ────────────────────────────────────────────
    async def _arbitrate_if_input_ready(self, pt, in_buf: list, out_buf: list) -> None:
        """Арбитраж в момент, когда модель начала отвечать (текст или tool_call).

        К этому моменту фраза пользователя закончена и расшифрована целиком —
        обрывков вроде «Джар» уже нет, — а ждать turn_complete не нужно: с
        голосом Fish это стоило ~8 с, пока Gemini генерирует свой ответ целиком.
        """
        if not pt or pt.arbitrated or not in_buf:
            return
        full = _clean_dialog_text("".join(in_buf))
        if full:
            await self._arbitrate_turn(full, out_buf, in_buf)

    def _start_voice_warmup(self) -> None:
        """Фоновый прогрев кэша фиксированных фраз голосом Fish. Один раз за процесс."""
        if getattr(self, "_voice_warmup_started", False) or get_voice_provider() != "fish":
            return
        try:
            from telegram_bot import tts_fish
            if not tts_fish.is_configured():
                return
        except Exception:
            return
        self._voice_warmup_started = True

        def _synth(text: str):
            return asyncio.run(tts_fish.speak_pcm(text, sample_rate=RECV_SAMPLE_RATE))

        def _worker():
            try:
                from core.voice_phrases import CANNED_PHRASES
                get_voice_cache(RECV_SAMPLE_RATE).warmup(CANNED_PHRASES, _synth)
            except Exception as e:
                logger.debug("Прогрев голоса не удался: %s", e)

        threading.Thread(target=_worker, name="voice-warmup", daemon=True).start()

    def _start_browser_bridge(self) -> None:
        """Сервер моста к браузеру поднимается заранее: иначе расширение
        подключается только через несколько секунд после первого фильма, и он
        стартует через хоткеи (стенд 16.09.2026)."""
        try:
            from core.media.bridge.server import BrowserBridgeServer
            BrowserBridgeServer.get_instance()
        except Exception as e:
            logger.debug("Мост к браузеру не поднялся: %s", e)

    def _play_earcon(self, kind: str) -> None:
        """Сигнал подтверждения/ошибки; звук для красоты не имеет права падать."""
        try:
            from core import earcons
            if kind == "error":
                earcons.play_error_earcon()
            else:
                earcons.play_success_earcon()
        except Exception as e:
            logger.debug("earcon %s: %s", kind, e)

    def _enter_thinking(self) -> None:
        """Ход отдан Gemini: ждём модель, таймер тишины и шлюз к этому не относятся."""
        sm = getattr(self, "state_machine", None)
        if not sm:
            return
        from core.conversation_state import ConversationState
        if sm.state in (ConversationState.LISTENING, ConversationState.FOLLOW_UP, ConversationState.STANDBY):
            sm.transition_to(ConversationState.THINKING, reason="waiting for model")

    def _on_local_transcript(self, text: str) -> None:
        """Локальная расшифровка реплики готова (поток AudioPipeline).

        Пока Gemini ещё 3–5 с молчит, команда уже может исполниться: тот же
        арбитраж, но только локальные роутеры — модель ход не получает.
        """
        loop = getattr(self, "_loop", None)
        if not loop or not loop.is_running():
            return

        def _schedule():
            asyncio.ensure_future(self._arbitrate_turn(text, [], [], local_only=True))

        loop.call_soon_threadsafe(_schedule)

    async def _arbitrate_turn(self, full_in: str, out_buf: list, in_buf: list, local_only: bool = False) -> bool:
        """
        Строгий арбитраж владения репликой (Single Ownership).
        FastCommandRouter -> CommandOrchestrator -> Gemini.
        Возвращает True если реплика обработана локально, False если передана Gemini.

        `local_only` — ранний заход по локальной расшифровке: локальные роутеры
        пробуются, но если фраза не команда, реплика остаётся неарбитрованной
        и дожидается расшифровки Gemini (она точнее на именах и названиях).
        """
        pt = getattr(self, "_pending_gemini_turn", None)
        if pt and pt.arbitrated:
            return pt.routed_to == "LOCAL"

        is_hotkey = time.monotonic() < getattr(self, "_hotkey_active_until", 0.0)
        is_wake = time.monotonic() < getattr(self, "_wake_active_until", 0.0)
        has_active_cmd = (
            hasattr(self, "command_orchestrator")
            and self.command_orchestrator.has_active_command()
        )
        was_addressed = bool(pt and pt.addressed)
        gap = None
        if pt and pt.addressed_at is not None and pt.first_transcript_at is not None:
            gap = max(0.0, pt.first_transcript_at - pt.addressed_at)
        elif local_only and pt and pt.addressed:
            # Локальная расшифровка готова в момент конца речи, шлюз ещё открыт
            gap = 0.0
        from core import wake_policy
        is_active = wake_policy.is_addressed(
            name_in_text=is_addressed_to_jarvis(full_in),
            wake_spotted=was_addressed,
            wake_to_speech_gap=gap,
            hotkey=is_hotkey,
            gate_open=is_wake,
            active_dialog=has_active_cmd or bool(getattr(self, "_pending_question", False)),
        )
        if is_hotkey:
            self._hotkey_active_until = 0.0

        if not is_active:
            if local_only:
                return False  # решит расшифровка Gemini — она видит больше
            if pt:
                pt.arbitrated = True
                pt.routed_to = "DISCARDED"
                pt.clear()
            self.ui.write_log(f"Игнор (не к Джарвису): {full_in}")
            out_buf.clear()
            self._clear_audio_in_queue()
            return False

        if local_only:
            print(f"[ДЖАРВИС] 🎤 Локально: '{full_in}'")
        else:
            print(f"[ДЖАРВИС] 🎤 Арбитраж реплики: '{full_in}'")
            self.ui.write_log(f"Вы: {full_in}")
        self.last_user_text = full_in

        # 1. Fast-Path: мгновенное детерминированное исполнение команд управления
        from core.fast_command_router import ExecutionStatus, FastCommandRouter
        fast_res = await asyncio.to_thread(FastCommandRouter.match_and_execute, full_in, self.ui)
        fast_handled, fast_resp = fast_res
        if fast_handled:
            if local_only:
                self.ui.write_log(f"Вы: {full_in}")
                self._latency.mark_transcript()
            if pt:
                pt.arbitrated = True
                pt.routed_to = "LOCAL"
                pt.clear()
            self._drop_speech_prefetch()
            self._clear_audio_in_queue()
            self._wake_active_until = 0.0
            sm = getattr(self, "state_machine", None)
            if sm:
                from core.conversation_state import ConversationState
                sm.transition_to(ConversationState.EXECUTING)

            fast_failed = getattr(fast_res, "status", None) in (ExecutionStatus.FAILED, ExecutionStatus.UNAVAILABLE)
            if getattr(fast_res, "is_action", False) and fast_failed and fast_resp:
                # Действие не вышло — молчать нельзя: сигнал ошибки и суть словами
                self._play_earcon("error")
                self.ui.write_log(f"Джарвис: {fast_resp}")
                if get_voice_provider() == "fish":
                    if sm:
                        from core.conversation_state import ConversationState
                        sm.transition_to(ConversationState.SPEAKING)
                    self._start_speech(fast_resp)
                elif sm:
                    from core.conversation_state import ConversationState
                    sm.transition_to(ConversationState.STANDBY)
            elif getattr(fast_res, "is_action", False):
                try:
                    from core.ducking_controller import ducking_controller
                    ducking_controller.restore()
                except Exception:
                    pass
                if sm:
                    from core.conversation_state import ConversationState
                    sm.transition_to(ConversationState.STANDBY)
            elif fast_resp:
                self.ui.write_log(f"Джарвис: {fast_resp}")
                if not getattr(fast_res, "is_action", False) and get_voice_provider() == "fish":
                    # Локальный ответ (время, погода) — такой же ход диалога:
                    # после него окно продолжения, чтобы «а в Москве?» не
                    # требовало снова имени.
                    from core import wake_policy
                    self._pending_question = False
                    self._followup_timeout = wake_policy.follow_up_window(False)
                    if sm:
                        from core.conversation_state import ConversationState
                        sm.transition_to(ConversationState.SPEAKING)
                    self._start_speech(fast_resp)
                elif sm:
                    from core.conversation_state import ConversationState
                    sm.transition_to(ConversationState.STANDBY)
            elif sm:
                from core.conversation_state import ConversationState
                sm.transition_to(ConversationState.STANDBY)
            return True

        # 2. CommandOrchestrator: строгое исполнение и маршрутизация команд
        if hasattr(self, "command_orchestrator"):
            from core.command_orchestrator import RoutingDecision
            orch_res = await asyncio.to_thread(self.command_orchestrator.process_user_text, full_in, self.ui)
            if orch_res.decision != RoutingDecision.NEEDS_LLM and orch_res.decision != RoutingDecision.IGNORED:
                if local_only:
                    self.ui.write_log(f"Вы: {full_in}")
                    self._latency.mark_transcript()
                if pt:
                    pt.arbitrated = True
                    pt.routed_to = "LOCAL"
                    pt.clear()
                self._drop_speech_prefetch()
                self._clear_audio_in_queue()
                sm = getattr(self, "state_machine", None)

                if orch_res.decision == RoutingDecision.CONSUMED_ACTION:
                    resp_text = orch_res.executed_result or "Готово, сэр."
                    self.ui.write_log(f"Джарвис: {resp_text}")
                    self._wake_active_until = 0.0
                    from core.confirmations import should_stay_silent
                    success = getattr(orch_res, "success", True)
                    if should_stay_silent(orch_res.action_name, success, resp_text):
                        # Фильм/музыка/приложение запустились — это и есть
                        # подтверждение. Сигнал вместо фразы (как у Алисы).
                        self._play_earcon("success")
                        if sm:
                            from core.conversation_state import ConversationState
                            sm.transition_to(ConversationState.STANDBY)
                        return True
                    if not success:
                        self._play_earcon("error")
                    if sm:
                        from core.conversation_state import ConversationState
                        sm.transition_to(ConversationState.SPEAKING)
                    if get_voice_provider() == "fish":
                        self._start_speech(resp_text, command_id=orch_res.command_id, turn_id=orch_res.turn_id)
                    elif sm:
                        from core.conversation_state import ConversationState
                        sm.transition_to(ConversationState.STANDBY)

                elif orch_res.decision == RoutingDecision.CONSUMED_CLARIFICATION:
                    prompt_text = orch_res.prompt_to_user or "Уточните, сэр."
                    self.ui.write_log(f"Джарвис: {prompt_text}")
                    self._wake_active_until = time.monotonic() + 15.0
                    if sm:
                        from core.conversation_state import ConversationState
                        sm.transition_to(ConversationState.SPEAKING)
                    if get_voice_provider() == "fish":
                        self._start_speech(prompt_text, command_id=orch_res.command_id, turn_id=orch_res.turn_id)
                    elif sm:
                        from core.conversation_state import ConversationState
                        sm.start_follow_up(timeout_sec=12.0)

                elif orch_res.decision == RoutingDecision.CONSUMED_CANCEL:
                    cancel_text = orch_res.prompt_to_user or "Команда отменена, сэр."
                    self.ui.write_log(f"Джарвис: {cancel_text}")
                    self._wake_active_until = 0.0
                    if sm:
                        from core.conversation_state import ConversationState
                        sm.transition_to(ConversationState.SPEAKING)
                    if get_voice_provider() == "fish":
                        self._start_speech(cancel_text, command_id=orch_res.command_id, turn_id=orch_res.turn_id)
                    elif sm:
                        from core.conversation_state import ConversationState
                        sm.transition_to(ConversationState.STANDBY)
                return True

            elif orch_res.decision == RoutingDecision.NEEDS_LLM:
                if local_only:
                    return False  # не команда — ждём Gemini, ход ему не отдаём заранее
                self._enter_thinking()
                if pt:
                    pt.arbitrated = True
                    pt.routed_to = "GEMINI"
                    if get_voice_provider() != "fish":
                        if pt.audio_chunks:
                            self._latency.mark_answer_audio()
                            for chunk in pt.audio_chunks:
                                try:
                                    self.audio_in_queue.put_nowait(chunk)
                                except Exception:
                                    pass
                            pt.audio_chunks.clear()
                        self.set_speaking(True)
                    else:
                        self._maybe_prefetch_speech(pt.full_output_text())
                        self._maybe_stream_speech(pt.full_output_text())
                return False

        if local_only:
            return False
        if pt and not pt.arbitrated:
            pt.arbitrated = True
            pt.routed_to = "GEMINI"
        self._enter_thinking()
        return False

    # ── Получение ответа от Gemini ────────────────────────────────────────────
    async def _receive_audio(self):
        print("[ДЖАРВИС] 👂 Приём запущен")
        out_buf, in_buf = [], []
        full_in = ""

        try:
            while True:
                if self._pending_gemini_turn is None:
                    self._begin_new_utterance()

                async for response in self.session.receive():
                    has_active_cmd = (
                        hasattr(self, "command_orchestrator")
                        and self.command_orchestrator.has_active_command()
                    )
                    from core.command_context import CommandState
                    is_collecting = (
                        has_active_cmd
                        and getattr(getattr(self.command_orchestrator, "active_command", None), "state", None) == CommandState.COLLECTING_PARAMETERS
                    )
                    pt = self._pending_gemini_turn

                    # 1. Приём аудиопотока модели (Quarantine Buffer)
                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()

                        # Во время сбора параметров — Gemini в режиме STT-only, аудио подавляется
                        if is_collecting:
                            continue

                        # Адресованность решает арбитраж, а не таймер окна: до
                        # решения звук копится в карантине pt, после — либо
                        # играет (GEMINI), либо выбрасывается (LOCAL/DISCARDED).
                        if getattr(self, "_interrupted_turn", False):
                            continue
                        if pt and pt.routed_to in _SILENT_ROUTES:
                            continue
                        if get_voice_provider() == "fish":
                            if pt:
                                pt.clear()
                            continue

                        if pt and pt.routed_to == "GEMINI":
                            self.set_speaking(True)
                            self._latency.mark_answer_audio()
                            try:
                                self.audio_in_queue.put_nowait(response.data)
                            except asyncio.QueueFull:
                                try:
                                    self.audio_in_queue.get_nowait()
                                    self.audio_in_queue.put_nowait(response.data)
                                except (asyncio.QueueEmpty, asyncio.QueueFull):
                                    pass
                        else:
                            if pt:
                                pt.add_audio(response.data)

                    # 2. Обработка сообщений сервера (транскрипции, завершение хода)
                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt_out = sc.output_transcription.text
                            if not is_collecting:
                                await self._arbitrate_if_input_ready(pt, in_buf, out_buf)
                                if pt and pt.routed_to in _SILENT_ROUTES:
                                    pass
                                elif pt and pt.routed_to == "GEMINI":
                                    out_buf.append(txt_out)
                                    self._maybe_prefetch_speech("".join(out_buf))
                                    self._maybe_stream_speech("".join(out_buf))
                                else:
                                    out_buf.append(txt_out)
                                    if pt:
                                        pt.add_text(txt_out)
                                    self._maybe_stream_speech("".join(out_buf))

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text
                            in_buf.append(txt)
                            self._latency.mark_transcript()
                            if pt is not None and pt.first_transcript_at is None:
                                pt.first_transcript_at = time.monotonic()
                            print(f"[ДЖАРВИС] 🎤 Фрагмент: '{txt}'")
                            # Арбитраж здесь НЕ делаем: расшифровка идёт кусками по
                            # слогу. По обрывку «Джар» реплика признавалась «не к
                            # Джарвису» и выбрасывалась целиком, а по «включи сериал»
                            # запускалось уточнение, и договорённое название терялось.
                            # Решение — когда модель начала отвечать или в turn_complete.

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()
                            if getattr(self, "_interrupted_turn", False):
                                # Ход перебит пользователем: его текст и звук
                                # выбрасываются целиком. Раньше флаг сбрасывался
                                # первой же строкой, и прерванный ответ звучал
                                # заново от начала до конца (стенд 16.09.2026).
                                self._interrupted_turn = False
                                logger.info("Dialog: ход, прерванный пользователем, закрыт без озвучки")
                                out_buf.clear()
                                in_buf.clear()
                                self._clear_audio_in_queue()
                                self._begin_new_utterance()
                                continue

                            raw_in = "".join(in_buf)
                            full_in = _clean_dialog_text(raw_in)
                            in_buf.clear()

                            is_local = False
                            if not pt or not pt.arbitrated:
                                is_local = await self._arbitrate_turn(full_in, out_buf, in_buf)
                            # DISCARDED тоже молчит: раньше «не к Джарвису» ответ
                            # всё равно доходил до озвучки через ветку хода Gemini.
                            if is_local or (pt and pt.routed_to in _SILENT_ROUTES):
                                out_buf.clear()
                                self._clear_audio_in_queue()
                                self._begin_new_utterance()
                                continue

                            # Ход Gemini: воспроизведение ответа
                            if get_voice_provider() != "fish":
                                if pt and pt.audio_chunks:
                                    self.set_speaking(True)
                                    self._latency.mark_answer_audio()
                                    for chunk in pt.audio_chunks:
                                        try:
                                            self.audio_in_queue.put_nowait(chunk)
                                        except Exception:
                                            pass
                                self._latency.mark_turn_complete()

                            raw_out = "".join(out_buf)
                            full_out = _clean_dialog_text(raw_out)
                            out_buf.clear()

                            from core.confirmations import is_bare_confirmation
                            if full_out and is_bare_confirmation(full_out) and getattr(self, "_tool_ran_this_turn", False):
                                # Модель ответила голым «Готово, сэр» после инструмента:
                                # действие уже видно, слова лишние — сигнал.
                                self.ui.write_log(f"Джарвис: {full_out}")
                                self._play_earcon("success")
                                self._drop_speech_prefetch()
                                if getattr(self, "_streaming_speech_active", False) and self._streaming_queue is not None:
                                    self._abort_playback()
                                from core import wake_policy
                                window = wake_policy.follow_up_window(False)
                                self._followup_timeout = window
                                self._wake_active_until = time.monotonic() + window
                                sm = getattr(self, "state_machine", None)
                                if sm:
                                    if window > 0:
                                        sm.start_follow_up(timeout_sec=window)
                                    else:
                                        from core.conversation_state import ConversationState
                                        sm.transition_to(ConversationState.STANDBY, reason="answer finished (strict)")
                                self._tool_ran_this_turn = False
                                self._begin_new_utterance()
                                continue
                            self._tool_ran_this_turn = False
                            if full_out:
                                self.ui.write_log(f"Джарвис: {full_out}")
                                is_noise_or_apology = any(p in full_out.lower() for p in (
                                    "не распознал", "не разобрал", "не расслышал", "фонового шума",
                                    "повторить", "вызов, сэр", "чем могу быть полезен"
                                ))
                                from core import wake_policy
                                if is_noise_or_apology:
                                    self._pending_question = False
                                    timeout = wake_policy.follow_up_window(False, is_apology=True)
                                else:
                                    self._pending_question = bool(full_out.strip().endswith("?") or getattr(self, "_pending_destructive", None))
                                    timeout = wake_policy.follow_up_window(bool(self._pending_question))
                                self._followup_timeout = timeout
                                self.last_user_text = ""

                                if get_voice_provider() == "fish":
                                    if getattr(self, "_streaming_speech_active", False) and self._streaming_queue is not None:
                                        final_chunks = _split_for_speech(full_out)
                                        for idx, ch in enumerate(final_chunks):
                                            if idx not in self._streamed_chunks_indices:
                                                self._streamed_chunks_indices.add(idx)
                                                self._streaming_queue.put_nowait(ch)
                                                logger.info("TTS Stream (turn_complete): финальный чанк #%d (%d симв)", idx, len(ch))
                                        self._streaming_queue.put_nowait(None)
                                    else:
                                        self._start_speech(full_out)
                                else:
                                    self._wake_active_until = time.monotonic() + timeout
                                    sm = getattr(self, "state_machine", None)
                                    if sm:
                                        sm.start_follow_up(timeout_sec=timeout)
                            elif getattr(self, "_streaming_speech_active", False) and self._streaming_queue is not None:
                                self._streaming_queue.put_nowait(None)
                            else:
                                # Модель закрыла ход молча — ждать больше нечего
                                sm = getattr(self, "state_machine", None)
                                if sm:
                                    from core.conversation_state import ConversationState
                                    if sm.state == ConversationState.THINKING:
                                        sm.transition_to(ConversationState.STANDBY, reason="empty model turn")

                            self._begin_new_utterance()

                    # 3. Инструменты модели: строгая защита от double execution
                    if response.tool_call:
                        # Решить владение ДО исполнения: иначе инструмент успевал
                        # отработать на фразе, которую потом признавали «не к Джарвису».
                        if not is_collecting:
                            await self._arbitrate_if_input_ready(pt, in_buf, out_buf)
                        is_local_turn = bool(pt and pt.routed_to in _SILENT_ROUTES)
                        if is_collecting or is_local_turn:
                            logger.info("[SAFETY] Подавлен tool_call Gemini (%s) в пользу локального контроллера",
                                        [fc.name for fc in response.tool_call.function_calls])
                            responses = [
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={"result": "ignored_local_ownership"}
                                )
                                for fc in response.tool_call.function_calls
                            ]
                            await self.session.send_tool_response(function_responses=responses)
                            continue

                        current_user_text = full_in or " ".join(in_buf)
                        is_hotkey = time.monotonic() < getattr(self, "_hotkey_active_until", 0.0)
                        is_wake = time.monotonic() < getattr(self, "_wake_active_until", 0.0)
                        owned_by_gemini = bool(pt and (pt.routed_to == "GEMINI" or pt.addressed))
                        is_active = (is_addressed_to_jarvis(current_user_text) if current_user_text else False) or is_hotkey or is_wake or owned_by_gemini
                        if not is_active:
                            logger.info(f"[ДЖАРВИС] 🔇 Инструменты заблокированы — нет обращения 'Джарвис' (фраза: '{current_user_text}')")
                            responses = [
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response={"result": "ignored_no_wake_word"}
                                )
                                for fc in response.tool_call.function_calls
                            ]
                            await self.session.send_tool_response(function_responses=responses)
                            continue

                        self._wake_active_until = time.monotonic() + 15.0
                        self._tool_in_progress = True
                        self._tool_ran_this_turn = True
                        while not self.out_queue.empty():
                            try:
                                self.out_queue.get_nowait()
                            except Exception:
                                break

                        try:
                            responses = []
                            for fc in response.tool_call.function_calls:
                                print(f"[ДЖАРВИС] 📞 {fc.name}")
                                try:
                                    self.ui.lock_on(fc.name)
                                except Exception as exc:
                                    logger.debug("Прицел не встал: %s", exc, exc_info=True)
                                _tool_started = time.perf_counter()
                                try:
                                    fr = await asyncio.wait_for(self._execute_tool(fc), timeout=25.0)
                                except asyncio.TimeoutError:
                                    logger.warning("Tool %s timed out after 25s", fc.name)
                                    if hasattr(self, "ui") and hasattr(self.ui, "write_log"):
                                        self.ui.write_log(f"SYS: ⏱ Таймаут инструмента '{fc.name}' (25с)")
                                    if not self.ui.muted:
                                        self.ui.set_state("LISTENING")
                                    fr = types.FunctionResponse(
                                        id=fc.id,
                                        name=fc.name,
                                        response={"result": f"Таймаут выполнения инструмента '{fc.name}' (25 сек). Действие прервано."}
                                    )
                                finally:
                                    self._latency.add_tool(
                                        fc.name,
                                        int((time.perf_counter() - _tool_started) * 1000),
                                    )
                                responses.append(fr)
                            await self.session.send_tool_response(function_responses=responses)
                        finally:
                            self._tool_in_progress = False
                            self._wake_active_until = 0.0


        except Exception as e:
            logger.warning("Приём оборвался: %s", e)
            raise

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
        while True:
            stream = None
            try:
                stream = await asyncio.to_thread(self._open_output)
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

                        # «Ответ закончен» — событие ОДНОКРАТНОЕ: только когда Джарвис
                        # действительно говорил и замолчал. Без этой проверки блок
                        # срабатывал на каждом холостом тике (10 раз в секунду) и сразу
                        # после «Джарвис» захлопывал окно прослушивания: LISTENING ->
                        # STANDBY в ту же миллисекунду, команда до модели не доходила.
                        if not is_busy and getattr(self, "_is_speaking", False):
                            cmd_id = getattr(self, "_active_speech_command_id", None)
                            t_id = getattr(self, "_active_speech_turn_id", 0)
                            has_active_cmd = (
                                hasattr(self, "command_orchestrator")
                                and self.command_orchestrator.has_active_command()
                            )

                            # Проверка на устаревший колбэк (команда была отменена или перебита)
                            is_stale = False
                            if cmd_id and hasattr(self, "command_orchestrator"):
                                is_stale = self.command_orchestrator.is_stale_callback(cmd_id, t_id)
                                if is_stale:
                                    logger.warning(
                                        "[Playback] Окончание TTS проигнорировано: команда %s (turn %d) устарела",
                                        cmd_id, t_id
                                    )

                            sm = getattr(self, "state_machine", None)
                            if not is_stale and has_active_cmd:
                                # Идёт многошаговый диалог (сбор параметров): щедрое окно 12-15 сек
                                timeout = 12.0
                                self._wake_active_until = time.monotonic() + timeout
                                if sm:
                                    from core.conversation_state import ConversationState
                                    sm.start_follow_up(timeout_sec=timeout)
                                logger.info("Dialog: TTS завершён, активен сбор параметров. Follow-up окно: %.1f с", timeout)
                            elif not is_stale:
                                timeout = getattr(self, "_followup_timeout", None)
                                if timeout is None and getattr(self, "_pending_question", False):
                                    timeout = 6.0
                                
                                if timeout and timeout > 0:
                                    self._wake_active_until = time.monotonic() + timeout
                                    if sm:
                                        from core.conversation_state import ConversationState
                                        if sm.state in (ConversationState.SPEAKING, ConversationState.EXECUTING):
                                            sm.start_follow_up(timeout_sec=timeout)
                                    logger.info("Dialog: Физическое воспроизведение завершено, Follow-up окно активно %.1f с", timeout)
                                else:
                                    # Режим Алисы (Strict Wake-word): после ответа сразу возвращаемся в STANDBY
                                    self._wake_active_until = 0.0
                                    if sm:
                                        from core.conversation_state import ConversationState
                                        sm.transition_to(ConversationState.STANDBY, reason="answer finished (alice mode)")
                                    logger.info("Dialog: Ответ завершён -> STANDBY (ожидание слова 'Джарвис')")

                            # Снятие флага говорения ПОСЛЕ установки follow-up окна и таймеров
                            if getattr(self, "_is_speaking", False):
                                self.set_speaking(False)

                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                        continue

                    # Проверка на прерывание (Barge-In): если реплика была прервана пользователем, отбрасываем остаточные куски
                    if getattr(self, "_interrupted_turn", False):
                        continue

                    self.set_speaking(True)
                    self._latency.mark_playback()
                    self._push_level(_chunk_level(chunk))
                    await asyncio.to_thread(stream.write, chunk)

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
                    try:
                        stream.stop()
                        stream.close()
                    except Exception as exc:
                        logger.debug("Закрытие потока вывода: %s", exc)

    # ── Основной цикл ─────────────────────────────────────────────────────────
    # Переподключение к Gemini Live: экспоненциальная пауза с джиттером.
    # Верхнего предела по числу попыток нет намеренно — домашний ассистент
    # обязан пережить получасовое падение интернета. Ограничена ПАУЗА, а после
    # RECONNECT_LOUD_AFTER неудач пользователю говорят об этом вслух один раз.
    RECONNECT_MAX_DELAY_SEC = 60.0
    RECONNECT_LOUD_AFTER = 5

    def _reconnect_delay(self, retry_count: int, fast_first: bool = False) -> float:
        """Пауза перед следующей попыткой подключения.

        Джиттер нужен, чтобы после массового обрыва (перезапуск сервера Google)
        клиенты не ломились обратно синхронным залпом.
        """
        if fast_first and retry_count <= 2:
            base = 0.3
        else:
            base = min(2.0 ** max(retry_count - (2 if fast_first else 0), 1),
                       self.RECONNECT_MAX_DELAY_SEC)
        return base * random.uniform(0.8, 1.2)

    def _note_reconnect(self, retry_count: int, delay: float):
        """Единая реакция интерфейса и машины состояний на переподключение."""
        logger.info("Reconnecting to Gemini in %.1fs (attempt %d)", delay, retry_count)
        if retry_count >= 3:
            self.ui.write_log(f"SYS: связь с Gemini оборвалась — попытка {retry_count}…")
        if retry_count == self.RECONNECT_LOUD_AFTER:
            self.ui.write_log(
                "SYS: ⚠️ Gemini недоступен уже несколько попыток. "
                "Продолжаю пробовать реже — проверьте интернет и ключ API."
            )
        self.ui.set_state("RECONNECTING")
        sm = getattr(self, "state_machine", None)
        if sm:
            from core.conversation_state import ConversationState
            sm.transition_to(ConversationState.RECONNECTING, reason="gemini reconnect", force=True)

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1alpha"},
        )

        retry_count = 0

        while True:
            try:
                print("[ДЖАРВИС] 🔌 Подключение к Gemini...")
                self.ui.set_state("RECONNECTING")
                config = self._build_config()

                try:
                    async with (
                        client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                        asyncio.TaskGroup() as tg,
                    ):
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
                        if hasattr(self, "state_machine") and self.state_machine:
                            self.state_machine.reset_to_standby(reason="connected")
                        try:
                            from core.wakeword import play_activation_chime
                            play_activation_chime()
                        except Exception:
                            pass
                        self._start_voice_warmup()
                        self._start_browser_bridge()

                        # Reset retry count on successful connection
                        retry_count = 0

                        tg.create_task(self._send_realtime())
                        tg.create_task(self._listen_audio())
                        tg.create_task(self._receive_audio())
                        tg.create_task(self._play_audio())

                        # Авто-триггер утреннего брифинга (6-11 утра, 1 раз в день)
                        from datetime import datetime
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
                except Exception as e:
                    logger.error(f"Live API connection error: {e}")
                    logger.error(f"Error type: {type(e).__name__}")
                    logger.error("Full traceback:")
                    traceback.print_exc()
                    
                    # Различаем ошибки WebSocket: 1008 (policy violation) vs 1011 (server shutdown)
                    error_msg = str(e)
                    if "1008" in error_msg:
                        logger.error("Policy violation (1008) - API key blocked")
                        logger.error("Your API key was marked as leaked.")
                        logger.error("What to do:")
                        logger.error("  1. Go to https://aistudio.google.com/app/apikey")
                        logger.error("  2. Delete old key and create new one")
                        logger.error("  3. Update config/api_keys.json with new key")
                        logger.error("  4. Restart JARVIS")
                        logger.error("Important: never commit API keys to Git!")
                        self.ui.write_log("SYS: ❌ API ключ заблокирован. Получите новый на https://aistudio.google.com/app/apikey")
                        break
                    elif "1011" in error_msg:
                        print("[ДЖАРВИС] ⚠️ Server shutdown (1011) - нормальный перезапуск")
                        retry_count += 1
                        delay = self._reconnect_delay(retry_count, fast_first=True)
                        self._note_reconnect(retry_count, delay)
                        await asyncio.sleep(delay)
                        continue
                    
                    self.set_speaking(False)
                    sm = getattr(self, "state_machine", None)
                    if sm:
                        from core.conversation_state import ConversationState
                        if sm.state != ConversationState.RECONNECTING:
                            sm.transition_to(ConversationState.RECONNECTING, reason="live api reconnect")
                    elif hasattr(self, "ui") and hasattr(self.ui, "set_state"):
                        self.ui.set_state("RECONNECTING")
                    retry_count += 1
                    delay = self._reconnect_delay(retry_count)
                    print(f"[ДЖАРВИС] ⏸️ Ожидаю {delay:.1f} сек перед попыткой {retry_count}...")
                    self._note_reconnect(retry_count, delay)
                    await asyncio.sleep(delay)
                    continue

            except Exception as e:
                error_msg = str(e)
                print(f"[ДЖАРВИС] ⚠️ {e}")
                traceback.print_exc()
                
                # Check for API key errors - don't retry these
                if any(key_word in error_msg.lower() for key_word in 
                   ["api key expired", "api_key_invalid", "invalid api key", "api key not found"]):
                    print("[JARVIS] FATAL: API key is invalid or expired!")
                    print("[JARVIS] Please get a new key at: https://aistudio.google.com/apikey")
                    print(f"[JARVIS] And update it in: {API_CONFIG}")
                    self.ui.write_log("SYS: API key invalid. Please update config/api_keys.json")
                    self.speak("Сэр, ключ API недействителен. Пожалуйста, обновите его.")
                    break  # Exit the reconnect loop
                
                self.set_speaking(False)
                sm = getattr(self, "state_machine", None)
                if sm:
                    from core.conversation_state import ConversationState
                    if sm.state != ConversationState.RECONNECTING:
                        sm.transition_to(ConversationState.RECONNECTING, reason="network exception")
                elif hasattr(self, "ui") and hasattr(self.ui, "set_state"):
                    self.ui.set_state("RECONNECTING")
                retry_count += 1
                delay = self._reconnect_delay(retry_count)
                self._note_reconnect(retry_count, delay)
                await asyncio.sleep(delay)


# ─── Точка входа ──────────────────────────────────────────────────────────────
def main():
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
