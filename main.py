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
import platform
import threading
import time
import random
import subprocess
import atexit
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
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    search_memory, format_search_results, over_limit,
)
from core import confirm as confirm_gate
from core import acknowledge
from core import watcher as topic_watcher
from core import settings as conv_settings
from core import session_log
from core import undo as undo_stack
from core.action_loader import discover_actions
from core import audio_devices
from core.push_to_talk import PushToTalk
from core import push_to_talk as ptt_setting
from core.emotion_analyzer import EmotionAnalyzer
from core.user_profile import UserProfile
from core.initiative_engine import InitiativeEngine
from core.proactive_engine import ProactiveEngine
from core.team_collaboration import TeamCollaborationEngine
from core.onboarding import ensure_gemini_key
from core.latency import LatencyTracker
from core.headless_ui import HeadlessUI, headless_requested
# Обработчики действий здесь больше не импортируются: их находит реестр
# (core/action_loader.py) по объявлению TOOL в самом модуле. Остаётся только
# то, что нужно самому main.py помимо вызова инструмента.
from actions.modes import get_current_mode

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

# Окно VAD и размышления переехали в core/settings.py: их крутит человек в
# окне настроек, и читаются они в момент сборки конфигурации сессии. Здесь
# остался снимок на момент запуска — для скриптов замера и для проверки
# инварианта «хвост тишины длиннее окна».
def _vad_silence_ms() -> int:
    return conv_settings.get("vad_silence_ms")

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
    """Индекс микрофона.

    Порядок: выбор человека в настройках → переменная MIC_DEVICE → эвристика по
    названиям → системный по умолчанию.

    Выбор человека идёт первым сознательно: эвристика ниже работает, пока
    угадывает, а когда не угадала, повлиять на неё было нечем — ни списка, ни
    настройки, ни даже способа узнать, какое устройство взято."""
    выбранный = audio_devices.chosen_index("input")
    if выбранный is not None:
        return выбранный

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
        if any(k in name for k in ("headset", "headphone", "bluetooth", "wireless", "buds", "airpods", "freebuds", "wh-1000", "airdots", "гарнитур", "наушник", "hands-free", "usb")) and not _device_is_silent(i):
            if "virtual" not in name and "line" not in name and "output" not in name:
                logger.info("Обнаружена подключенная гарнитура/наушники — выбран микрофон: «%s» (индекс %d)", d["name"], i)
                return i

    # 2. Аппаратные/драйверные микрофоны с ИИ-шумоподавлением (ASUS AI Noise-cancelling, Krisp, RTX Voice, Intelligo)
    # Они отсекают пространственные шумы комнаты, эхо и посторонние голоса при работе со встроенного микрофона ноутбука.
    for i, d in devices:
        if d["max_input_channels"] <= 0 or d.get("hostapi", 0) != 0:
            continue
        name = d["name"].lower()
        if any(k in name for k in ("noise-cancelling", "noise cancelling", "noise-canceling", "шумоподавлен", "ai noise")) and not _device_is_silent(i):
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

# Порог тишины для микрофона (RMS по int16).
# 35.0 обеспечивает высокую чувствительность к обычной речи и шёпоту
# без необходимости повышать голос или кричать в микрофон ноутбука.
MIC_RMS_THRESHOLD = float(os.getenv("MIC_RMS_THRESHOLD", "35.0"))
# Хвост тишины после речи — не косметика, а условие того, что тебе вообще
# ответят. Конец фразы определяет VAD на стороне Gemini, и определить его он
# может только по ПОЛУЧЕННОЙ тишине: когда гейт обрывает поток сразу за
# последним громким кадром, сервер остаётся ждать продолжения фразы.
_HANGOVER_BASE_MS = int(os.getenv("MIC_HANGOVER_MS", "450"))
_FRAME_MS = CHUNK_SIZE / SEND_SAMPLE_RATE * 1000
_HANGOVER_FRAMES_FORCED = os.getenv("MIC_HANGOVER_FRAMES", "").strip()

MIC_HANGOVER_MS = _HANGOVER_BASE_MS
MIC_HANGOVER_FRAMES = int(_HANGOVER_FRAMES_FORCED or max(1, round(MIC_HANGOVER_MS / _FRAME_MS)))


def _sync_hangover(silence_ms: int) -> None:
    """Приводит хвост тишины в соответствие с окном VAD.

    Связь здесь не косметическая: если хвост короче окна, Gemini не дожидается
    тишины и НЕ ОТВЕЧАЕТ ВОВСЕ. Пока окно жило в переменной среды, инвариант
    держался руками. Теперь окно двигает человек из окна настроек, и хвост
    обязан двигаться следом — иначе ползунок «пусть не перебивает» делает
    Джарвиса немым, и связать одно с другим человек не сможет никогда.
    """
    global MIC_HANGOVER_MS, MIC_HANGOVER_FRAMES

    нужно = max(_HANGOVER_BASE_MS, int(silence_ms) * 2)
    if нужно == MIC_HANGOVER_MS:
        return
    MIC_HANGOVER_MS = нужно
    if not _HANGOVER_FRAMES_FORCED:
        MIC_HANGOVER_FRAMES = max(1, round(нужно / _FRAME_MS))
    logger.info("Хвост тишины подтянут до %d мс под окно VAD %d мс", нужно, silence_ms)

# ── Необратимые действия ──────────────────────────────────────────────────────
# Окно, в течение которого повторный вызов считается подтверждением.
_CONFIRM_WINDOW_SEC = 90

# Ключи ищем и по-английски (как объявлено модели), и по-русски: слой действий
# сопоставляет русские подстроки, и «перезагрузи» доходит именно так.
_DESTRUCTIVE = {
    "computer_control": ("shutdown", "restart", "reboot", "выключ", "перезагруз"),
    "files": ("delete", "remove", "удал"),
}

# Инструменты, у которых опасно ВСЁ, кроме перечисленного.
#
# Обратная таблица нужна там, где опасно действие ПО УМОЛЧАНИЮ. Запуск скрипта
# — единственное действие во всём наборе, выполняющее произвольный код: что он
# сделает, заранее не знает никто, и отмены у этого нет. Спрашивай мы про
# «run», как про «delete», модель обошла бы гейт, просто не передав параметр:
# пропущенное действие — это тоже запуск.
_DESTRUCTIVE_UNLESS = {
    "code_runner": ("check", "проверь", "syntax"),
}

# Что написать на баннере. Ключ — «инструмент/действие», как его собирает
# _confirm_destructive. Незнакомая пара получает техническое имя: лучше
# невнятный заголовок, чем необратимое действие без спроса.
_DESTRUCTIVE_TITLES = {
    "computer_control/shutdown": "Выключение компьютера",
    "computer_control/restart":  "Перезагрузка компьютера",
    "computer_control/reboot":   "Перезагрузка компьютера",
    "files/delete":              "Удаление файла",
    "files/remove":              "Удаление файла",
    "code_runner/run":           "Запуск программы",
    "code_runner/":              "Запуск программы",
}


# «Это не мой инструмент» от инлайн-диспетчера. Отдельный объект, а не None
# и не пустая строка: те означали бы «инструмент отработал и промолчал».
_НЕ_ИНЛАЙН = object()


def _action_of(args: dict) -> str:
    return str(args.get("action", "")).strip().lower()


def _is_destructive(name: str, args: dict) -> bool:
    безопасные = _DESTRUCTIVE_UNLESS.get(name)
    if безопасные is not None:
        return _action_of(args) not in безопасные

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



# ─── Самоописание: что ассистент умеет и чего не умеет ────────────────────────

# Настоящая ОС, а не та, что записана в конфиге: ассистент должен знать, где
# он запущен, — команды на Windows и Linux разные.
_OS_NAME = platform.system()

def _render_prompt(template: str, values: dict[str, str]) -> str:
    """Подставляет `{токены}` в текст промпта.

    Простой заменой, а не `str.format`: в промпте 27 КБ живого текста, и одна
    фигурная скобка в чьей-нибудь фразе уронила бы запуск с KeyError. Токен,
    которого в шаблоне нет, просто не подставляется."""
    for ключ, значение in values.items():
        template = template.replace("{" + ключ + "}", значение)
    return template


def _describe_capabilities() -> str:
    """Список способностей — из живого реестра, а не из отдельного перечня.

    Перечень пришлось бы править руками, и он разошёлся бы с кодом в первый же
    месяц: инструмент удалили, а ассистент продолжает его обещать."""
    строки = [f"- {s}" for s in _ACTIONS.describe()]
    строки += [f"- {s}" for s in _PLUGINS.describe()]
    встроенные = {
        "save_to_memory": "запоминает факты о пользователе",
        "recall_memory":  "ищет в долгосрочной памяти то, чего нет в этой инструкции",
        "undo":           "отменяет собственные изменения файлов и настроек",
        "look_at_screen": "смотрит на экран — один снимок по запросу",
        "look_at_camera": "смотрит в камеру — один снимок по запросу",
        "translation":    "переводит текст и ведёт историю переводов",
        "team_collaboration": "ведёт проекты, задачи и состав команды",
        "sleep_timer":    "ставит таймер сна с автовыключением",
        "switch_voice":   "переключает голос между киношным и быстрым",
        "shutdown_jarvis": "завершает свою работу",
    }
    for имя, описание in встроенные.items():
        строки.append(f"- {имя} — {описание}.")
    return "\n".join(sorted(строки))


# Чего ассистент не может. Эта половина важнее списка умений: не зная границ,
# модель придумывает недостающее и уверенно рапортует о сделанном.
_LIMITS = """- Зрение — это ОДИН снимок по запросу, а не постоянное наблюдение:
  того, что было на экране минуту назад, ты не видел.
- Ты действуешь только на этой машине. Чужими компьютерами, телефоном и
  умным домом ты не управляешь.
- Выключение, перезагрузка и удаление файла требуют нажатия кнопки на экране.
  Пока её не нажали, действие НЕ выполнено — не говори, что сделал.
- Ты не можешь отменить то, чего не делал сам: «отмени» относится к твоим
  действиям, а не к Ctrl+Z в чужом приложении.
- Всего, чего нет в списке выше, ты не умеешь. Скажи об этом прямо, вместо
  того чтобы придумывать результат."""

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


# ─── Встроенные инструменты ───────────────────────────────────────────────────
# Здесь остаётся только то, что вплетено в состояние живой сессии: память,
# отмена, зрение с дозагрузкой кадра в тот же ход, переключение голоса,
# выключение. Всё остальное описывает себя само в actions/*.py и приходит из
# core/action_loader.py — см. _ACTIONS ниже.
INLINE_TOOLS = [
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
        "name": "recall_memory",
        "description": (
            "Ищет факт о пользователе, сохранённый в долгосрочной памяти, но не попавший "
            "в системную инструкцию. Ключи таких фактов перечислены в блоке [ТАКЖЕ ПОМНЮ] — "
            "если разговор коснулся любого из них, вызывай ЭТОТ инструмент первым. "
            "Вызывай его и прежде, чем сказать «я не знаю» о чём-то личном, и когда "
            "пользователь спрашивает, что ты о нём помнишь (тогда query оставь пустым). "
            "Это локальный поиск по файлу: мгновенный и бесплатный, сети не требует."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Ключевое слово: имя, тема или категория (например 'азиза', "
                        "'кофе', 'projects'). Пусто — выдать всё, что помню."
                    ),
                },
            },
            "required": []
        }
    },
    {
        "name": "undo",
        "description": (
            "Отменяет последнее изменение, которое ТЫ внёс в этот компьютер: перемещённый, "
            "переименованный, созданный или переписанный файл, а также настройку, которую "
            "менял — громкость, яркость. Вызывай, когда пользователь говорит: отмени, верни "
            "назад, верни как было, отмена, не то сделал — на любом языке. "
            "action=list — показать, что можно отменить. "
            "Это отмена ТВОИХ действий, а не Ctrl+Z в открытом приложении "
            "(для него — computer_control)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "undo (по умолчанию) — отменить последнее | list — показать список",
                },
            },
            "required": []
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
]


# ─── Ядро ДЖАРВИС ─────────────────────────────────────────────────────────────

# Действия, которые описывают себя сами. Реестр собирается один раз при импорте:
# список способностей ассистента — это он, а не отдельный список, который
# пришлось бы править вручную и который разошёлся бы с кодом в первый же месяц.
_INLINE_NAMES = {t["name"] for t in INLINE_TOOLS}
_ACTIONS = discover_actions(BASE_DIR / "actions", reserved_names=_INLINE_NAMES)


def _plugin_enabled(имя: str) -> bool:
    """Выключенный плагин не загружается вовсе.

    Не «загружается и молчит»: модель не должна видеть в списке способность,
    которой человек её лишил, — иначе она будет её предлагать, а вызов
    упрётся в «неизвестный инструмент»."""
    try:
        from core.paths import load_api_keys
        выключены = load_api_keys().get("plugins_disabled") or []
        return имя not in {str(н).strip() for н in выключены}
    except Exception:
        return True


# Пользовательские плагины: один файл — одна способность, положил и работает.
# Имена штатных инструментов заняты: плагин не должен незаметно подменять
# «выключи компьютер» своей версией.
_PLUGINS = discover_actions(
    BASE_DIR / "plugins",
    reserved_names=_INLINE_NAMES | set(_ACTIONS.names()),
    package="plugins",
    attribute="PLUGIN",
    enabled=_plugin_enabled,
)

TOOLS = (INLINE_TOOLS
         + _ACTIONS.get_tool_declarations()
         + _PLUGINS.get_tool_declarations())

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

        # Запасной гейт необратимых действий для headless-режима, где нажать
        # кнопку негде. Поле раньше не создавалось вовсе, и первое же
        # «выключи компьютер» падало с AttributeError прямо в приёмном цикле.
        self._pending_destructive: tuple[str, str, float] | None = None

        # Хендл возобновления сессии. Сервер присылает его раз в несколько
        # секунд и обновляет по ходу разговора; при реконнекте мы отдаём его
        # обратно, и беседа продолжается с того же места. Держим только в
        # памяти: записанный на диск, он заставил бы новый запуск продолжать
        # вчерашний разговор.
        self._resume_handle: str | None = None

        # Подтверждение выдаёт интерфейс, а не модель: единственный путь к
        # `resolve` — нажатие кнопки в окне. В headless нажимать некому, и
        # интерфейс честно об этом говорит — там остаётся запасной гейт
        # (см. _confirm_destructive).
        if getattr(self.ui, "supports_confirm", False):
            confirm_gate.bind(
                show=self.ui.show_confirm,
                hide=self.ui.hide_confirm,
                notify=self._send_text_to_session,
                log=self.ui.write_log,
            )
        else:
            logger.info("Экранное подтверждение недоступно: интерфейс без баннера")

        # Слежение за темами. Проверяет фоновый поток, а говорит — эта же
        # сессия: `_send_text_to_session` потокобезопасен (внутри
        # run_coroutine_threadsafe), поэтому отдельного моста не нужно.
        #
        # Поток поднимается, только если с прошлого запуска осталось за чем
        # следить: пустой сторож — это разбуженный раз в минуту процесс,
        # который ничего не делает.
        topic_watcher.bind(
            notify=self._send_text_to_session,
            log=self.ui.write_log,
        )
        if topic_watcher.start():
            logger.info("Слежение за темами возобновлено: %s", topic_watcher.describe())

        # Новый мозг ДЖАРВИС
        self.user_profile = UserProfile(BASE_DIR)
        self.initiative_engine = InitiativeEngine()
        self.proactive_engine = ProactiveEngine(BASE_DIR)
        self.team_engine = TeamCollaborationEngine(BASE_DIR)
        self.last_user_text = ""

        # Секундомер голосового хода. Пишет в лог задержку от конца речи до
        # первого звука ответа при JARVIS_DEBUG_UI=1.
        self._latency = LatencyTracker(
            sink=self.ui.write_log if conv_settings.get("show_latency") else None
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
        try:
            from core.wake_detector import WakeWordDetector2Stage
            self._wake_detector = WakeWordDetector2Stage()
            logger.info("WakeWord: 2-Stage KWS подключён к JarvisBot")
        except Exception as _e:
            logger.debug("WakeWord init note: %s", _e)
            self._wake_detector = None

        self.ui.on_text_command = self._on_text_command

        # Push-to-talk: микрофон закрыт, пока не держат Ctrl+Space.
        #
        # Включается в настройках и по умолчанию выключен: для тихой комнаты
        # пробуждение голосом удобнее. Смысл режима в том, что в шумной
        # комнате кадры микрофона НЕ уезжают — а не в том, что их игнорируют
        # на той стороне.
        self._ptt = PushToTalk(on_change=self._on_ptt)
        self._ptt_enabled = ptt_setting.enabled()
        if self._ptt_enabled:
            глобально = self._ptt.start()
            self.ui.write_log(f"SYS: режим «зажми и говори» — {self._ptt.scope_note()}")
            if not глобально:
                # Окно умеет ловить аккорд само, но только когда оно в фокусе.
                self.ui.bind_push_to_talk(self._ptt.set_held)

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

        # Автоматический запуск мобильного Telegram-бота (@Aimyjarvisbot) в фоне
        self._telegram_proc = self._start_telegram_bot()
        atexit.register(self.cleanup)

    def _on_ptt(self, держат: bool):
        """Клавишу зажали или отпустили. Зовётся из опрашивающего потока."""
        if держат:
            # Зажатие — это и пробуждение: тянуться за словом, когда рука уже
            # на клавише, незачем.
            if self.ui.muted:
                self.ui.toggle_mute()
            self.ui.set_state("LISTENING")
        else:
            self.ui.set_state("IDLE")

    def _on_hotkey_wake(self):
        """Реакция на глобальный хоткей F8 / Ctrl+Shift+J из любого приложения или игры."""
        logger.info("[Hotkey] Нажата горячая клавиша вызова Джарвиса (F8)")
        if self.ui.muted:
            self.ui.toggle_mute()
            self.ui.write_log("SYS: ⚡ Микрофон активирован по горячей клавише F8.")
        else:
            self.ui.write_log("SYS: ⚡ Активация по горячей клавише F8.")
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

    def _start_telegram_bot(self):
        """Запускает Telegram-бота в отдельном фоновом процессе при наличии токена."""
        try:
            from telegram_bot.config import load as load_config
            cfg = load_config(require_bot=False)
            if not cfg.telegram_token:
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

        # Замыкания в стеке отмены держат прежнее содержимое файлов — пережить
        # сессию они не должны. И висящее подтверждение вместе с ними: кнопка
        # выключения, оставшаяся «нажатой» от прошлого запуска, — не то, что
        # стоит находить при следующем.
        if getattr(self, "_ptt", None):
            self._ptt.stop()

        # Автоматизируемое окно браузера переживёт процесс, если его не закрыть:
        # Chromium останется висеть без хозяина.
        try:
            from core import browser_session
            browser_session.close()
        except Exception as exc:
            logger.debug("Браузер при выходе: %s", exc)

        # Фоновый сторож — демон, но join на выходе честнее: иначе он успеет
        # заговорить в мёртвую сессию уже после прощания.
        try:
            topic_watcher.stop()
        except Exception as exc:
            logger.debug("Слежение при выходе: %s", exc)

        undo_stack.clear()
        confirm_gate.reset()

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
        if text:
            self._send_text_to_session(text)

    def _normalize_input_text(self, text: str) -> str:
        """Normalize user input text for better intent parsing."""
        return _clean_dialog_text(text)

    # ── Управление состоянием ─────────────────────────────────────────────────
    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
            try:
                from core.ducking_controller import ducking_controller, DuckingState
                ducking_controller.set_state(DuckingState.SPEAKING)
            except Exception:
                pass
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")
            try:
                from core.ducking_controller import ducking_controller, DuckingState
                ducking_controller.set_state(DuckingState.RESTORING)
            except Exception:
                pass

    def speak(self, text: str):
        """Отправляет текст в сессию для озвучки."""
        if text:
            self._send_text_to_session(text)

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:100]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Сэр, произошла ошибка в модуле {tool_name}. {short}")

    def _acknowledge(self, name: str, args: dict, уже_сказано: bool) -> None:
        """Говорит короткую фразу перед медленным инструментом.

        Молчит, когда сказать нечего (быстрый инструмент), когда модель уже
        что-то сказала в этом ходе и когда голосом занимается сам Gemini:
        подмешивать к его голосу чужой ради полутора секунд — хуже паузы."""
        if уже_сказано or get_voice_provider() != "fish":
            return
        try:
            фраза = acknowledge.phrase(name, args)
            if not фраза:
                return
            asyncio.create_task(self._speak_fish(фраза, метрики=False))
            self.ui.write_log(f"Джарвис: {фраза}")
        except Exception as exc:
            # Подтверждение — удобство, а не работа. Его срыв не должен
            # стоить вызова инструмента, ради которого всё затевалось.
            logger.debug("Подтверждение не прозвучало: %s", exc, exc_info=True)

    def _confirm_destructive(self, key: str, name: str, args: dict) -> str | None:
        """Гейт необратимого действия.

        None — человек подтвердил, выполняем. Строка — ответ модели вместо
        выполнения.

        Основной путь: баннер в окне, токен выдаёт кнопка. Запасной, когда
        интерфейса нет (headless, консольный запуск): прежнее правило о
        повторном вызове — слабее, но лучше, чем выключать машину молча."""
        title = _DESTRUCTIVE_TITLES.get(key) or f"{name} / {_action_of(args)}"

        if confirm_gate.available():
            if confirm_gate.consume(key):
                return None
            detail = ", ".join(f"{k}={v}" for k, v in args.items() if v) or "без параметров"
            asked = confirm_gate.request(key, title, detail)
            if asked:
                return asked
            # Баннер показать не удалось — необратимое действие без спроса не
            # выполняем.
            return (
                f"НЕ ВЫПОЛНЕНО: не смог показать подтверждение на экране для «{title}». "
                f"Скажи об этом пользователю и не утверждай, что сделал."
            )

        pending = self._pending_destructive
        same = pending and pending[0] == name and pending[1] == _action_of(args)
        fresh = same and (time.time() - pending[2]) < _CONFIRM_WINDOW_SEC
        if fresh:
            self._pending_destructive = None
            return None

        self._pending_destructive = (name, _action_of(args), time.time())
        logger.warning("Требую подтверждения (без интерфейса): %s", key)
        self.ui.write_log(f"SYS: жду подтверждения — {title}")
        return (
            "НЕ ВЫПОЛНЕНО — нужно подтверждение. Переспроси пользователя вслух, "
            "точно ли он хочет это сделать, и вызови инструмент повторно "
            "ТОЛЬКО если он ответит утвердительно."
        )

    def _drop_stale_handle(self, exc: Exception, resumed_with: str | None) -> bool:
        """Сбрасывает хендл возобновления, если сервер отказался его принять.

        Только если этой попыткой мы действительно возобновлялись: обычный
        обрыв связи хендл не портит, и терять из-за него разговор незачем.
        True — значит подключаемся заново сразу, без паузы: ошибка была не в
        сети, а в хендле, и вторая попытка пойдёт с чистого листа."""
        if not resumed_with or resumed_with != self._resume_handle:
            return False
        text = str(exc).lower()
        if not any(k in text for k in ("resum", "handle", "invalid_argument", "not_found")):
            return False
        self._resume_handle = None
        logger.warning("Хендл возобновления отклонён сервером — начинаю новую сессию")
        self.ui.write_log("SYS: разговор восстановить не удалось — начинаю новую сессию")
        return True

    # ── Конфигурация Gemini ───────────────────────────────────────────────────
    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime
        memory    = load_memory()
        mem_str   = format_memory_for_prompt(memory)
        # Кто ты, что умеешь и чего не умеешь — собирается из живой системы на
        # старте сессии, а не пишется в файл, который назавтра устареет.
        шаблон     = _load_system_prompt()
        способности = _describe_capabilities()

        # Окно VAD и хвост микрофона — связанная пара, и подтянуть хвост надо
        # ДО того, как окно уедет на сервер.
        тишина_мс = _vad_silence_ms()
        _sync_hangover(тишина_мс)
        sys_prompt = _render_prompt(шаблон, {
            "tools":  способности,
            "limits": _LIMITS,
            "os":     _OS_NAME,
        })
        # Своего места для этих блоков в промпте может и не быть: файл писали
        # до того, как они появились. Тогда дописываем в конец — молча остаться
        # без описания собственных границ хуже, чем поставить его не там.
        if "{tools}" not in шаблон:
            sys_prompt += (
                "\n\n[ЧТО ТЫ УМЕЕШЬ — собрано из установленных инструментов]\n"
                + способности
                + "\n\n[ЧЕГО ТЫ НЕ УМЕЕШЬ]\n" + _LIMITS + "\n"
            )

        # Модель не слышит того, что произнесла система за неё.
        if get_voice_provider() == "fish":
            sys_prompt += "\n\n" + acknowledge.PROMPT_RULE + "\n"

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
            # Хендл с прошлой сессии — иначе реконнект начинает пустой
            # разговор. Конфиг здесь стоял с самого начала, но хендл,
            # который сервер присылает в ответ, никто не читал: за
            # «бесконечные сессии» мы платили и не получали их. Хендл
            # подхватывается в _receive_audio.
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle,
            ),
            # Когда считать, что человек договорил.
            #
            # По умолчанию модель ждёт около секунды тишины — отсюда пауза
            # перед каждым ответом. Порог опущен до 400 мс и включена высокая
            # чувствительность к концу речи: ответ начинается почти сразу.
            # Ниже 300 мс модель начинает перебивать на паузах внутри фразы.
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    # Из настроек, а не из констант: «перебивает» и «долго
                    # молчит» — самые частые жалобы, и это одна и та же
                    # ручка. Читается здесь, поэтому новое число вступает в
                    # силу со следующим подключением, а не с перезапуском.
                    silence_duration_ms=тишина_мс,
                    prefix_padding_ms=conv_settings.get("vad_prefix_ms"),
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
                thinking_budget=conv_settings.get("thinking_budget"),
            ),
        )

    # ── Выполнение инструментов ───────────────────────────────────────────────
    # Инструменты, по которым видно, чем человек занят. Остальные о занятии
    # ничего не говорят: «посмотрел погоду» — не деятельность.
    _ЗАНЯТИЯ = ("movie_player", "music_player", "set_mode")

    def _note_activity(self, name: str, args: dict) -> None:
        """Отмечает в профиле, чем человек занялся, и кормит прогноз."""
        if name not in self._ЗАНЯТИЯ:
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
        self.proactive_engine.record_action(name, {
            "time": datetime.now().strftime("%H:%M"),
            "emotion": self.user_profile.get_context().get("last_emotion", "neutral"),
            "mode": get_current_mode().get("mode", "normal"),
        })

    async def _run_inline_tool(self, name: str, args: dict):
        """Десять инструментов, вплетённых в состояние живой сессии.

        Остальные живут в `actions/` и объявляют себя сами; эти остались
        здесь, потому что им нужны голос, очередь звука, стек отмены или
        завершение процесса — то, чего у отдельного модуля нет.

        Возвращает `_НЕ_ИНЛАЙН`, если имя не отсюда: пустая строка или None
        значили бы «инструмент отработал и промолчал», а это другое.
        """
        loop = asyncio.get_event_loop()
        result = "Готово."

        # ── Инструмент: поиск по долгосрочной памяти ─────────────
        if name == "recall_memory":
            hits = await asyncio.to_thread(search_memory, args.get("query", ""))
            result = format_search_results(hits)

        # ── Инструмент: отмена собственного действия ─────────────
        elif name == "undo":
            if str(args.get("action", "")).strip().lower() == "list":
                items = undo_stack.history()
                result = (
                    "Могу отменить: " + "; ".join(items) + "."
                    if items else
                    "Отменять нечего, сэр."
                )
            else:
                result = await asyncio.to_thread(undo_stack.undo_last)
            self.ui.write_log(f"SYS: {result}")

        # ── Инструмент: Vision (анализ экрана и камеры) ─────────
        elif name in ("look_at_screen", "look_at_camera", "vision_review"):
            from actions.vision import vision_action
            source = "camera" if name == "look_at_camera" else args.get("source", "screen")
            args["source"] = source
            r = await loop.run_in_executor(None, lambda: vision_action(args))
            result = r or "Анализ изображения завершен."

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
                os._exit(0)
            threading.Thread(target=_shutdown, daemon=True).start()

        else:
            return _НЕ_ИНЛАЙН

        return result

    async def _execute_tool(self, fc, *, уже_сказано: bool = False) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        logger.info(f"🔧 Tool: {name} {args}")
        self.ui.set_state("THINKING")

        # Пауза перед долгим инструментом — заполненная.
        #
        # Поиск в сети или разбор PDF занимает секунды, и всё это время
        # снаружи тишина: неотличимо от «не расслышал». Короткая фраза
        # («Секунду, ищу») уходит в очередь звука прямо сейчас, ответ встанет
        # следом — очередь одна, порядок сохраняется.
        #
        # `уже_сказано` — текст, который модель успела произнести в этом же
        # ходе до вызова. Он прозвучит вместе с ответом, и подтверждение
        # поверх него было бы вторым «сейчас посмотрю» подряд.
        self._acknowledge(name, args, уже_сказано)

        # Необратимое — только после подтверждения человеком.
        #
        # Микрофон отдаёт в модель всё, что слышит в комнате, включая звук из
        # фильма, так что «выключи компьютер» может родиться из ниоткуда, а
        # computer_settings делает shutdown /s /t 5 по-настоящему.
        #
        # Раньше подтверждением считался ПОВТОРНЫЙ вызов того же инструмента в
        # течение 90 секунд. Модели было велено переспросить вслух, но ничто
        # не проверяло, что человек ответил: два вызова подряд модель делает
        # сама. Теперь токен выдаёт интерфейс — единственный путь к нему лежит
        # через нажатие кнопки в окне (core/confirm.py).
        #
        # Блокировка экрана осталась без подтверждения: она безвредна и
        # обратима, а спрашивать о ней каждый раз — раздражать зря.
        if _is_destructive(name, args):
            key = f"{name}/{_action_of(args)}"
            gate_result = self._confirm_destructive(key, name, args)
            if gate_result is not None:
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return types.FunctionResponse(
                    id=fc.id, name=name, response={"result": gate_result},
                )
            logger.warning("Подтверждено, выполняю: %s", key)

        # Сохранение в память (без задержки)
        if name == "save_to_memory":
            cat = args.get("category", "notes")
            key = args.get("key", "")
            val = args.get("value", "")
            if key and val:
                # Запись на диск — в поток: этот же цикл гонит звук в Live API.
                await asyncio.to_thread(update_memory, {cat: {key: {"value": val}}})
                print(f"[Память] 💾 {cat}/{key} = {val}")
                # Предохранитель. Ничего не удаляем, но говорим об этом в лог,
                # который читают, а не в stdout, который нет.
                if await asyncio.to_thread(over_limit):
                    self.ui.write_log(
                        "SYS: память разрослась сверх предохранителя — стоит проредить memory/data.json"
                    )
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop = asyncio.get_event_loop()
        result = "Готово."

        try:
            result = await self._run_inline_tool(name, args)

            if result is _НЕ_ИНЛАЙН:
                # ── Самоописывающиеся действия из actions/ ──────────────
                #
                # Одна ветка вместо четырнадцати. Реестр знает и объявление,
                # и обработчик, поэтому расхождение между «что обещано
                # модели» и «что выполнится» невозможно: они из одного места.
                if _ACTIONS.has(name):
                    result = await loop.run_in_executor(
                        None, lambda: _ACTIONS.run(name, args, player=self.ui)
                    )

                # ── Пользовательские плагины ────────────────────────────
                elif _PLUGINS.has(name):
                    result = await loop.run_in_executor(
                        None, lambda: _PLUGINS.run(name, args, player=self.ui)
                    )

                else:
                    result = f"Неизвестный инструмент: {name}"

        except Exception as e:
            result = f"Ошибка инструмента '{name}': {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        self._note_activity(name, args)

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

        preroll = collections.deque(maxlen=10)

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if jarvis_speaking:
                self._note_gate("Джарвис говорит сам")
                preroll.clear()
                return
            if self.ui.muted:
                self._note_gate("микрофон выключен (Ctrl+M)")
                preroll.clear()
                return
            # Режим «зажми и говори»: пока клавишу не держат, кадр не уходит
            # никуда. Предбуфер при этом чистим — иначе первая же отпущенная
            # клавиша отправила бы полсекунды чужого разговора.
            if self._ptt_enabled and not self._ptt.mic_open():
                self._note_gate("зажмите Ctrl+Space, чтобы говорить")
                preroll.clear()
                return

            pcm_bytes = indata.tobytes()

            # Если в динамиках играет звук — проверяем ключевое слово для немедленного ducking
            if self._speaker_meter is not None and self._speaker_meter.peak > _SPEAKER_GATE:
                if self._wake_detector:
                    if self._wake_detector.process_pcm(pcm_bytes):
                        try:
                            from core.ducking_controller import ducking_controller
                            ducking_controller.duck()
                        except Exception:
                            pass

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

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=_pick_input_device(),
                latency="low",
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

    async def _speak_fish(self, text: str, *, метрики: bool = True):
        """Озвучивает готовый текст голосом Джарвиса из Telegram-бота.

        `метрики=False` — для мгновенного подтверждения перед долгим
        инструментом. Такая фраза не ответ, а заполнение паузы: закрыть
        ею ход значило бы записать в статистику полсекунды вместо
        настоящих четырёх и перестать замечать медленные инструменты —
        ровно ту беду, ради которой фраза и произносится."""
        with self._speaking_lock:
            self._active_synth_tasks += 1
        self.set_speaking(True)

        try:
            from telegram_bot import tts_fish
            from telegram_bot import tts_edge

            chunks = _split_for_speech(text)
            if not chunks:
                if метрики:
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

                if not spoken and метрики:
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
            if метрики:
                self._latency.mark_turn_complete()
        finally:
            with self._speaking_lock:
                self._active_synth_tasks = max(0, self._active_synth_tasks - 1)

    # ── Получение ответа от Gemini ────────────────────────────────────────────
    def _catch_resume_handle(self, response) -> None:
        """Хендл возобновления. Сервер шлёт его периодически и обновляет по
        ходу разговора — берём последний."""
        sru = getattr(response, "session_resumption_update", None)
        if sru is None or not getattr(sru, "resumable", False):
            return
        new_handle = getattr(sru, "new_handle", None)
        if not new_handle or new_handle == self._resume_handle:
            return
        first = self._resume_handle is None
        self._resume_handle = new_handle
        if first:
            print("[ДЖАРВИС] 🔗 Возобновление сессии вооружено")

    def _play_model_audio(self, data: bytes) -> None:
        """Звук от самой модели — в очередь воспроизведения."""
        if self._turn_done_event and self._turn_done_event.is_set():
            self._turn_done_event.clear()
        if get_voice_provider() == "fish":
            # Говорит Fish — звук Gemini выбрасываем, иначе два голоса
            # произнесут один ответ одновременно.
            return
        self._latency.mark_answer_audio()
        try:
            self.audio_in_queue.put_nowait(data)
        except asyncio.QueueFull:
            # _play_audio не успевает — дропаем старейший фрейм, чтобы
            # освободить место под новый.
            try:
                self.audio_in_queue.get_nowait()
                self.audio_in_queue.put_nowait(data)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def _on_user_said(self, full_in: str) -> None:
        """Реплика человека дослушана: журнал, эмоции, инициатива, прогноз.

        Всё это не имеет отношения к чтению сокета и жило внутри цикла приёма
        только потому, что там оказалось под рукой. Разбор ответа сервера и
        поведение ассистента — разные задачи, и ошибка в одной не должна
        требовать чтения другой.
        """
        print(f"[ДЖАРВИС] 🎤 Полная фраза: '{full_in}'")
        self.ui.write_log(f"Вы: {full_in}")
        self.last_user_text = full_in
        # Дописывание в файл дня — миллисекунды, и оно переживает закрытие
        # крестиком. Выжимка считается потом, когда о ней спросят.
        session_log.remember("Вы", full_in)

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
                self._send_text_to_session(initiative)

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
                self._send_text_to_session(proactive_suggestions[0])

    def _on_jarvis_said(self, full_out: str) -> None:
        """Ответ дописан: в журнал окна, в журнал дня и — если голос внешний —
        на синтез."""
        self.ui.write_log(f"Джарвис: {full_out}")
        session_log.remember("Джарвис", full_out)
        if get_voice_provider() == "fish":
            # Отдельной задачей: синтез идёт около секунды, а приём в это
            # время должен продолжать читать сессию.
            asyncio.create_task(self._speak_fish(full_out))

    async def _run_tool_calls(self, tool_call, уже_сказано: bool) -> None:
        responses = []
        for fc in tool_call.function_calls:
            print(f"[ДЖАРВИС] 📞 {fc.name}")
            # Джарвис у Старка не работает молча: HUD всегда показывает,
            # на что наведён.
            try:
                self.ui.lock_on(fc.name)
            except Exception as exc:
                logger.debug("Прицел не встал: %s", exc, exc_info=True)
            начало = time.perf_counter()
            try:
                fr = await self._execute_tool(fc, уже_сказано=уже_сказано)
            finally:
                # Медленный инструмент — самая частая причина паузы, которую
                # слышно как «завис».
                self._latency.add_tool(
                    fc.name, int((time.perf_counter() - начало) * 1000))
            responses.append(fr)
        await self.session.send_tool_response(function_responses=responses)

    async def _receive_audio(self):
        """Чтение сессии: что пришло — то и раздать. Вся работа по кускам
        живёт в отдельных методах выше."""
        print("[ДЖАРВИС] 👂 Приём запущен")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():
                    self._catch_resume_handle(response)

                    if response.data:
                        self._play_model_audio(response.data)

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
                            if get_voice_provider() != "fish":
                                self._latency.mark_turn_complete()
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = _clean_dialog_text("".join(in_buf))
                            in_buf = []
                            if full_in:
                                self._on_user_said(full_in)

                            full_out = _clean_dialog_text("".join(out_buf))
                            out_buf = []
                            if full_out:
                                self._on_jarvis_said(full_out)

                    if response.tool_call:
                        # Признак «модель уже заговорила в этом ходе» —
                        # из буфера на момент вызова: см. _acknowledge.
                        await self._run_tool_calls(
                            response.tool_call,
                            уже_сказано=bool("".join(out_buf).strip()),
                        )

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
                                   dtype="int16", blocksize=CHUNK_SIZE, device=device, latency="low")
            s.start()
            s.write(b"\x00" * (CHUNK_SIZE * 2))   # тишина: проверяем, что ПИШЕТСЯ
            return s

        # Выбор человека — первым. Раньше здесь стоял только None, то есть
        # «то, что система назвала по умолчанию», а в Windows это меняется
        # само при подключении гарнитуры.
        выбранный = audio_devices.chosen_index("output")
        if выбранный is not None:
            try:
                return _try(выбранный)
            except Exception as exc:
                logger.warning("Выбранное устройство вывода не играет: %s", exc)
                self.ui.write_log("SYS: выбранные динамики молчат — беру системные")

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
            http_options={"api_version": "v1alpha"},
        )

        retry_count = 0
        max_retry_delay = 60  # Maximum delay between retries (seconds)
        max_retries = 5  # Maximum number of retry attempts

        while True:
            try:
                # С каким хендлом идём на этот раз — чтобы отличить отказ
                # сервера принять хендл от обычного обрыва связи. Снимается
                # до первого вызова, который может бросить: обработчики ниже
                # на него рассчитывают.
                resumed_with = self._resume_handle
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

                    # Хендл, который сервер не принимает — протух или
                    # относится к сессии, которую сервер уже забыл. Без этой
                    # ветки один мёртвый хендл повторялся бы на каждой
                    # попытке, и функция, призванная пережить реконнект, сама
                    # бы его и не давала. Сбрасываем один раз и идём заново.
                    if self._drop_stale_handle(e, resumed_with):
                        continue


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

                if self._drop_stale_handle(e, resumed_with):
                    continue


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
