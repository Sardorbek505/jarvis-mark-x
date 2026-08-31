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
import traceback
import re
import threading
import time
import random
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('JARVIS')

import sounddevice as sd
from google import genai
from google.genai import types

from ui import JarvisUI
from memory.memory_manager import load_memory, update_memory, format_memory_for_prompt
from core.emotion_analyzer import EmotionAnalyzer
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

# Сколько тишины ждать, прежде чем считать фразу законченной. По умолчанию
# модель ждёт около секунды — это и есть та самая пауза перед ответом.
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
_VOICE_PROVIDER = (os.getenv("JARVIS_VOICE") or "gemini").strip().lower()

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
_IGNORE_SPEAKERS = os.getenv("MIC_IGNORE_SPEAKERS", "1") != "0"


def _device_is_silent(index: int, seconds: float = 0.3) -> bool:
    """Проверяет, является ли устройство мёртвым или фантомным виртуальным входом.

    Виртуальные входы (например, ASUS AI Noise-cancelling VAC) могут возвращать
    паразитный белый шум с пиком < 300 и RMS < 50, но не передавать полезную речь.
    """
    try:
        import numpy as np
        rec = sd.rec(int(seconds * SEND_SAMPLE_RATE), samplerate=SEND_SAMPLE_RATE,
                     channels=1, dtype="int16", device=index)
        sd.wait()
        peak = int(np.abs(rec).max())
        # Если сигнал абсолютный ноль — устройство мертво.
        return peak == 0
    except Exception as exc:
        logger.warning("Устройство %s не удалось проверить (%s) — пропускаю", index, exc)
        return True          # не открылось или не читается — точно не кандидат


def _pick_input_device():
    """Индекс микрофона: из MIC_DEVICE, иначе рабочий физический микрофон, иначе None."""
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

    # Приоритет аппаратным микрофонам (USB, Realtek, встроенный массив),
    # исключая фантомные виртуальные драйверы без полезного сигнала
    devices = list(enumerate(sd.query_devices()))

    # 1. Сначала ищем реальные USB микрофоны/гарнитуры (наилучшее качество)
    for i, d in devices:
        if d["max_input_channels"] <= 0 or d.get("hostapi", 0) != 0:
            continue
        name = d["name"].lower()
        if ("usb" in name or "headset" in name or "микрофон" in name) and not _device_is_silent(i):
            if "virtual" not in name and "line" not in name and "noise-cancelling" not in name:
                logger.info("Выбран USB/внешний микрофон: «%s» (индекс %d)", d["name"], i)
                return i

    # 2. Ищем встроенный Realtek массив
    for i, d in devices:
        if d["max_input_channels"] <= 0 or d.get("hostapi", 0) != 0:
            continue
        name = d["name"].lower()
        if "realtek" in name and not _device_is_silent(i):
            logger.info("Выбран встроенный микрофон Realtek: «%s» (индекс %d)", d["name"], i)
            return i

    # 3. Фолбэк на дефолтное системное устройство
    return None
CHUNK_SIZE        = 1024

# Порог тишины для микрофона (RMS по int16). Ниже него кадры в облако не
# уходят вовсе. Порог 80 обеспечивает отличную чувствительность для любых микрофонов.
MIC_RMS_THRESHOLD = float(os.getenv("MIC_RMS_THRESHOLD", "80"))
# Хвост тишины после речи — не косметика, а условие того, что тебе вообще
# ответят. Конец фразы определяет VAD на стороне Gemini, и определить его он
# может только по ПОЛУЧЕННОЙ тишине: когда гейт обрывает поток сразу за
# последним громким кадром, сервер остаётся ждать продолжения фразы.
#
# Замер 17.08.2026 на стенде scripts/latency_probe.py: с хвостом 0.64 с (10
# кадров) модель не ответила НИ РАЗУ — расшифровывала сказанное и молчала;
# с 1.92 с отвечала всегда. Поэтому хвост считается от окна VAD с запасом,
# а не подбирается на глаз.
#
# Замер 30.08.2026, тот же стенд, три реплики подряд:
#     +400 (0.8 с) — ответ ОДИН на три вопроса. Реплики расшифровывались
#                    порознь, но turn_complete приходил единственный раз:
#                    сервер так и не увидел конца первых двух фраз и склеил
#                    их в один ход. Задержка при этом «улучшается» до 2.8 с,
#                    но мерить нечего — ходов больше нет.
#     +600 (1.0 с) — три ответа из трёх.
# Обрыв лежит между ними, а не в 0.64 с, как думалось раньше. Экономия 200 мс
# стоила двух ответов из трёх, поэтому запас вернули.
MIC_HANGOVER_MS = int(os.getenv("MIC_HANGOVER_MS", str(_VAD_SILENCE_MS + 600)))
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
    """Очищает и нормализует текст диалога, собирая фрагменты речи в связный текст."""
    if not text:
        return ""
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)

    # Убираем звуки-паразиты и заминки
    for filler in ("э-э-э", "м-м-м", "э-м-м", "м-э-м", "э-э", "м-м", "а-а"):
        text = re.sub(rf"\b{filler}\b", "", text, flags=re.IGNORECASE)

    # Исправляем склеивание оторванных знаков (например, "став ь" -> "ставь", "под ъ" -> "подъ")
    text = re.sub(r"([а-яёА-ЯЁ]{2,})\s+([ьъы])\b", r"\1\2", text)

    # Нормализуем множественные пробелы
    text = re.sub(r"\s+", " ", text).strip()
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
            "Управляет видеоплеером в браузере (kinogo.mu): запуск фильма, "
            "пауза, перемотка, полный экран, выход. "
            "Вызывай когда пользователь говорит: включи фильм X, поставь X, фильм X, "
            "пауза, продолжай, перемотай, полный экран, вперёд, назад, выйти из фильма."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "play (запустить с поиском) | pause (Space, переключатель) | "
                        "resume (Space) | fullscreen (F) | "
                        "seek_forward (→ 10 сек) | seek_back (← 10 сек) | "
                        "volume_up (системная громкость +10%) | volume_down (-10%) | "
                        "exit (выход + закрыть вкладку)"
                    )
                },
                "title": {
                    "type": "STRING",
                    "description": "Название фильма (для action=play)"
                }
            },
            "required": ["action"]
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
            "Также вызывай автоматически при старте сессии если сейчас утро (6-10)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
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

        # Новый мозг ДЖАРВИС
        self.user_profile = UserProfile(BASE_DIR)
        self.initiative_engine = InitiativeEngine()
        self.proactive_engine = ProactiveEngine(BASE_DIR)
        self.team_engine = TeamCollaborationEngine(BASE_DIR)
        self.last_user_text = ""

        # Секундомер голосового хода. Пишет в лог задержку от конца речи до
        # первого звука ответа при JARVIS_DEBUG_UI=1.
        self._latency = LatencyTracker(
            sink=self.ui.write_log if os.getenv("JARVIS_DEBUG_UI") == "1" else None
        )

        self.ui.on_text_command = self._on_text_command

    # ── Текстовый ввод ────────────────────────────────────────────────────────
    def _on_text_command(self, text: str):
        if not self._loop or not self.session or not self._loop.is_running():
            return
        
        # Normalize text before sending
        text = self._normalize_input_text(text)
        
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True,
            ),
            self._loop,
        )
    
    def _normalize_input_text(self, text: str) -> str:
        """Normalize user input text for better intent parsing."""
        return _clean_dialog_text(text)

    # ── Управление состоянием ─────────────────────────────────────────────────
    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        """Отправляет текст в сессию для озвучки."""
        if not self._loop or not self.session or not self._loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True,
            ),
            self._loop,
        )

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

        def callback(indata, frames, time_info, status):
            # Почему кадр не уехал — самое важное, чего тут не хватало.
            # Все три отказа ниже молчаливые, и когда микрофон глохнет
            # насовсем, в логе нет ни строчки: со стороны Джарвис просто
            # «не отвечает». Считаем причины и раз в _GATE_REPORT_SEC пишем
            # сводку — по кадру логировать нельзя, их 15 в секунду.
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if jarvis_speaking:
                self._note_gate("Джарвис говорит сам")
                return
            if self.ui.muted:
                self._note_gate("микрофон выключен (Ctrl+M)")
                return
            # Из динамиков сейчас что-то играет — значит микрофон слышит это,
            # а не человека. Читаем готовое число: COM из аудио-колбэка звать
            # нельзя, замер идёт в своём потоке.
            if self._speaker_meter is not None and self._speaker_meter.peak > _SPEAKER_GATE:
                self._note_gate(
                    f"звук в динамиках {self._speaker_meter.peak:.3f} > "
                    f"порога {_SPEAKER_GATE}"
                )
                return
            if not self._is_loud_enough(indata):
                self._note_gate("тихо для порога MIC_RMS_THRESHOLD")
                return
            self._note_gate(None)
            # Отсчёт задержки ведём от последнего ГРОМКОГО кадра: именно он и
            # есть «человек договорил». Кадры хвоста тишины тоже уезжают в
            # облако, но человек в это время уже молчит — считая и от них, мы
            # вычитали из задержки длину собственного хвоста и отчитывались
            # цифрой лучше правды: «слышит» выходило отрицательным, потому что
            # расшифровка успевала прийти раньше конца хвоста.
            if self._frame_was_loud:
                self._latency.mark_voice_frame()
            loop.call_soon_threadsafe(
                _put_nowait_safe,
                {"data": indata.tobytes(), "mime_type": "audio/pcm"},
            )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=_pick_input_device(),
                callback=callback,
            ):
                print("[ДЖАРВИС] 🎤 Поток микрофона открыт")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Microphone error: {e}")
            traceback.print_exc()
            # Don't raise - let the task be recreated
            # Brief pause before attempting to continue
            await asyncio.sleep(1)

    async def _speak_fish(self, text: str):
        """Озвучивает готовый ответ голосом Джарвиса из Telegram-бота."""
        with self._speaking_lock:
            self._active_synth_tasks += 1
        self.set_speaking(True)

        try:
            from telegram_bot import tts_fish
            from telegram_bot import tts_edge

            chunks = _split_for_speech(text)
            if not chunks:
                self._latency.mark_turn_complete()
                return

            # Жив ли Fish — решается ОДИН раз за ответ, а не на каждом куске.
            #
            # Раньше падение Fish стоило по таймауту на фрагмент: у запроса
            # tts_fish._TIMEOUT_SEC = 60, и ответ из четырёх предложений мог
            # молчать четыре минуты, каждый раз заново убеждаясь в том, что
            # уже известно. Один отказ — и остаток ответа договаривает Edge.
            fish_alive = tts_fish.is_configured()

            async def _synth_fragment(fragment: str):
                nonlocal fish_alive
                if fish_alive:
                    pcm = await tts_fish.speak_pcm(fragment, sample_rate=RECV_SAMPLE_RATE)
                    if pcm:
                        return pcm
                    fish_alive = False
                    self.ui.write_log("SYS: Fish молчит — остаток ответа озвучит Edge-TTS")
                return await tts_edge.speak_pcm(fragment, sample_rate=RECV_SAMPLE_RATE)

            def synth(fragment: str):
                return asyncio.create_task(_synth_fragment(fragment))

            pending = synth(chunks[0])
            step = CHUNK_SIZE * 2
            spoken = 0

            for i in range(len(chunks)):
                pcm = await pending
                pending = synth(chunks[i + 1]) if i + 1 < len(chunks) else None

                if not pcm:
                    self.ui.write_log("SYS: синтез речи недоступен — ответ остался текстом")
                    if pending:
                        pending.cancel()
                    break

                if not spoken:
                    self._latency.mark_answer_audio()
                spoken += len(pcm)

                for j in range(0, len(pcm), step):
                    try:
                        self.audio_in_queue.put_nowait(pcm[j:j + step])
                    except asyncio.QueueFull:
                        await self.audio_in_queue.put(pcm[j:j + step])

            if spoken:
                logger.info("Голос Fish: %.1f с звука, %d фрагмент(ов) на %d символов",
                            spoken / 2 / RECV_SAMPLE_RATE, len(chunks), len(text))
            self._latency.mark_turn_complete()
        finally:
            with self._speaking_lock:
                self._active_synth_tasks = max(0, self._active_synth_tasks - 1)

    # ── Получение ответа от Gemini ────────────────────────────────────────────
    async def _receive_audio(self):
        print("[ДЖАРВИС] 👂 Приём запущен")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():
                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        if _VOICE_PROVIDER == "fish":
                            # Говорит Fish — звук Gemini выбрасываем, иначе
                            # два голоса произнесут один ответ одновременно.
                            continue
                        self._latency.mark_answer_audio()
                        try:
                            self.audio_in_queue.put_nowait(response.data)
                        except asyncio.QueueFull:
                            # _play_audio не успевает — дропаем старейший
                            # фрейм, чтобы освободить место под новый.
                            try:
                                self.audio_in_queue.get_nowait()
                                self.audio_in_queue.put_nowait(response.data)
                            except (asyncio.QueueEmpty, asyncio.QueueFull):
                                pass

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            out_buf.append(sc.output_transcription.text)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text
                            in_buf.append(txt)
                            self._latency.mark_transcript()
                            print(f"[ДЖАРВИС] 🎤 Фрагмент: '{txt}'")

                        if sc.turn_complete:
                            # С внешним голосом ход закрывает _speak_fish,
                            # когда звук реально пошёл: здесь готов только текст.
                            if _VOICE_PROVIDER != "fish":
                                self._latency.mark_turn_complete()
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            raw_in = "".join(in_buf)
                            full_in = _clean_dialog_text(raw_in)
                            in_buf = []

                            if full_in:
                                print(f"[ДЖАРВИС] 🎤 Полная фраза: '{full_in}'")
                                self.ui.write_log(f"Вы: {full_in}")
                                self.last_user_text = full_in

                                # Анализ эмоций (новый мозг)
                                emotion_result = EmotionAnalyzer.analyze(full_in)
                                if emotion_result["emotion"] != "neutral":
                                    print(f"[Эмоции] {emotion_result['emotion']} (confidence: {emotion_result['confidence']:.2f})")
                                    self.user_profile.update_context(emotion=emotion_result["emotion"])

                                    # Проверяем инициативу
                                    mode_state = get_current_mode()
                                    current_mode = mode_state.get("mode", "normal")
                                    initiative = self.initiative_engine.should_show_initiative(
                                        emotion_result,
                                        self.user_profile.get_full_profile(),
                                        current_mode
                                    )
                                    if initiative:
                                        print(f"[Инициатива] {initiative}")
                                        # Отправляем инициативное предложение
                                        if self._loop and self._loop.is_running():
                                            asyncio.run_coroutine_threadsafe(
                                                self.session.send_client_content(
                                                    turns={"parts": [{"text": initiative}]},
                                                    turn_complete=True,
                                                ),
                                                self._loop,
                                            )

                                # Обучение из контекста
                                preference = self.initiative_engine.should_learn_preference(full_in, "")
                                if preference:
                                    self.user_profile.update_preference(preference["type"], preference["value"])
                                    print(f"[Обучение] Выучил предпочтение: {preference['type']} = {preference['value']}")

                                # Прогнозирование потребностей (новый мозг)
                                context = {
                                    "current_activity": self.user_profile.get_context().get("current_activity"),
                                    "mode": get_current_mode().get("mode", "normal"),
                                    "last_emotion": emotion_result.get("emotion") if emotion_result else "neutral"
                                }
                                proactive_suggestions = self.proactive_engine.get_proactive_suggestions(context)
                                if proactive_suggestions:
                                    print(f"[Прогноз] Предложения: {proactive_suggestions}")
                                    # Отправляем первое предложение (не навязчиво)
                                    if proactive_suggestions and random.random() < 0.3:  # 30% шанс
                                        if self._loop and self._loop.is_running():
                                            asyncio.run_coroutine_threadsafe(
                                                self.session.send_client_content(
                                                    turns={"parts": [{"text": proactive_suggestions[0]}]},
                                                    turn_complete=True,
                                                ),
                                                self._loop,
                                            )

                            raw_out = "".join(out_buf)
                            full_out = _clean_dialog_text(raw_out)
                            out_buf = []

                            if full_out:
                                self.ui.write_log(f"Джарвис: {full_out}")
                                if _VOICE_PROVIDER == "fish":
                                    # Отдельной задачей: синтез идёт около
                                    # секунды, а приём в это время должен
                                    # продолжать читать сессию.
                                    asyncio.create_task(self._speak_fish(full_out))

                    if response.tool_call:
                        responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[ДЖАРВИС] 📞 {fc.name}")
                            # Джарвис у Старка не работает молча: HUD всегда
                            # показывает, на что наведён.
                            try:
                                self.ui.lock_on(fc.name)
                            except Exception as exc:
                                logger.debug("Прицел не встал: %s", exc, exc_info=True)
                            _tool_started = time.perf_counter()
                            try:
                                fr = await self._execute_tool(fc)
                            finally:
                                # Медленный инструмент — самая частая причина
                                # паузы, которую слышно как «завис».
                                self._latency.add_tool(
                                    fc.name,
                                    int((time.perf_counter() - _tool_started) * 1000),
                                )
                            responses.append(fr)
                        await self.session.send_tool_response(function_responses=responses)

        except Exception as e:
            logger.error("Приём оборвался: %s", e)
            # Комментарий здесь обещал «внешний цикл переподключится», а код
            # молча проглатывал ошибку и завершал задачу. Переподключаться было
            # НЕКОМУ: остальные три задачи продолжали работать с мёртвой
            # сессией, Джарвис оставался запущенным и глухим, а на разрыв
            # 1011 от Gemini (тот приходит регулярно, это штатное поведение их
            # серверов) просто закрывался посреди разговора.
            #
            # Пробрасываем наверх: TaskGroup свернётся, и сработает настоящий
            # реконнект в `run()`, где уже есть счётчик попыток и backoff.
            self.ui.write_log("SYS: связь с Gemini оборвалась — переподключаюсь…")
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
                                   dtype="int16", blocksize=CHUNK_SIZE, device=device)
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

                        if not is_busy:
                            if getattr(self, "_is_speaking", False):
                                self.set_speaking(False)
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
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
    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"},
        )

        retry_count = 0
        max_retry_delay = 60  # Maximum delay between retries (seconds)
        max_retries = 5  # Maximum number of retry attempts

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
                        try:
                            from core.wakeword import play_activation_chime
                            play_activation_chime()
                        except Exception:
                            pass

                        # Reset retry count on successful connection
                        retry_count = 0

                        # Авто-триггер утреннего брифинга (6-10 утра)
                        from datetime import datetime
                        current_hour = datetime.now().hour
                        if 6 <= current_hour < 11:
                            print("[ДЖАРВИС] 🌅 Утро: авто-брифинг")
                            self.ui.write_log("SYS: Утренний брифинг...")
                            try:
                                from actions.morning_briefing import morning_briefing
                                loop = asyncio.get_event_loop()
                                briefing = await loop.run_in_executor(
                                    None, lambda: morning_briefing({}, player=self.ui)
                                )
                                self.speak(briefing)
                            except Exception as e:
                                print(f"[ДЖАРВИС] ⚠️ Брифинг не удался: {e}")

                        tg.create_task(self._send_realtime())
                        tg.create_task(self._listen_audio())
                        tg.create_task(self._receive_audio())
                        tg.create_task(self._play_audio())
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
                        if retry_count > max_retries:
                            print(f"[ДЖАРВИС] ❌ Превышен лимит попыток ({max_retries})")
                            self.ui.write_log("SYS: Превышен лимит попыток подключения")
                            break
                        delay = min(2 ** retry_count, max_retry_delay)
                        logger.info(f"Waiting {delay}s before retry {retry_count}/{max_retries}...")
                        self.ui.set_state("RECONNECTING")
                        await asyncio.sleep(delay)
                        continue
                    
                    retry_count += 1
                    if retry_count > max_retries:
                        print(f"[ДЖАРВИС] ❌ Превышен лимит попыток ({max_retries})")
                        self.ui.write_log("SYS: Превышен лимит попыток подключения")
                        break
                    delay = min(2 ** retry_count, max_retry_delay)
                    print(f"[ДЖАРВИС] ⏸️ Ожидаю {delay} сек перед попыткой {retry_count}/{max_retries}...")
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
                self.ui.set_state("THINKING")
                retry_count += 1
                if retry_count > max_retries:
                    print(f"[ДЖАРВИС] ❌ Превышен лимит попыток ({max_retries})")
                    self.ui.write_log("SYS: Превышен лимит попыток подключения")
                    break
                delay = min(2 ** retry_count, max_retry_delay)
                logger.info(f"Reconnecting in {delay}s (attempt {retry_count}/{max_retries})...")
                self.ui.set_state("RECONNECTING")
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
