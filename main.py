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
import re
import threading
import time
import subprocess
import atexit
from types import SimpleNamespace
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
    if issubclass(exc_type, KeyboardInterrupt):
        # Ctrl+C в консоли прилетает в обработчик Qt: раньше это писалось
        # как «Необработанная ошибка», а окно продолжало жить. Это просьба выйти.
        logger.info("Ctrl+C — завершаю работу")
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
        except Exception:
            app = None
        if app is not None:
            app.quit()
        return
    logger.critical("Необработанная ошибка", exc_info=(exc_type, exc, tb))


sys.excepthook = _log_unhandled
# Падение рабочего потока раньше было беззвучным: окно жило, Джарвис — нет.
threading.excepthook = lambda args: _log_unhandled(args.exc_type, args.exc_value, args.exc_traceback)

# Настройки из экрана «Настройки» (core/settings.py) → в окружение ДО того, как
# ниже прочитаются MIC_DEVICE, MIC_RMS_THRESHOLD, JARVIS_WAKE_MODE и др.
try:
    from core import settings as _settings
    _settings.apply_env()
except Exception as _settings_exc:
    logger.warning("Настройки не прочитались: %s", _settings_exc)

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
from core import quick
from core.speech_text import for_speech, short_reason
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

# Звук из своих колонок отличается от голоса не порогом громкости колонок
# (был SPEAKER_GATE=0.08 и замыкал круг с приглушением), а сравнением
# микрофона с тем, что играет сейчас, — см. core/echo_gate.py.
# MIC_IGNORE_SPEAKERS=0 отключает защиту.
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
# Офлайн-детектор имени — только после калибровки на голосе владельца
# (--wake-calibrate) или по явному JARVIS_LOCAL_WAKE=1. Без неё маленькая
# русская модель Vosk слова «Джарвис» не знает (пишет «из») и ловила его лишь
# по случайным промежуточным догадкам: на живом голосе владельца не сработала
# за час ни разу, и Джарвис «глох». JARVIS_LOCAL_WAKE=0 — выключить совсем.
def _local_wake_enabled() -> bool:
    env = os.getenv("JARVIS_LOCAL_WAKE", "").strip()
    if env in ("0", "1"):
        return env == "1"
    try:                                     # своё слово в Porcupine — лучший детектор
        from core import wake_porcupine
        if wake_porcupine.configured():
            return True
    except Exception:
        pass
    try:                                     # sherpa-onnx: «Джарвис» без ключей и обучения
        from core import wake_kws
        if wake_kws.available():
            return True
    except Exception:
        pass
    from core.wake_vosk import load_aliases
    return bool(load_aliases())


# Имя сказали, а расшифровка его потеряла: осталась запятая после обращения.
# Журнал владельца 26.09: «Джарвис, привет» пришло как «, привет.» — и Джарвис
# решил, что это не ему, и промолчал.
_LOST_NAME_RE = re.compile(r"^\s*[,，]\s*\w")


def _has_wake_word(text: str) -> bool:
    return bool(text) and bool(_WAKE_RE.search(text) or _LOST_NAME_RE.match(text))


# Микрофоны, которые звук своих динамиков не слышат: с шумоподавлением в
# драйвере (ASUS AI Noise-cancelling вычитает звук динамиков) и гарнитуры.
_SPEAKER_DEAF_MICS = ("noise-cancelling", "noise cancelling", "noise-canceling", "шумоподавлен",
                      "ai noise", "headset", "headphone", "гарнитур", "наушник", "buds", "airpods",
                      "hands-free")


_HEADPHONE_OUTPUTS = ("headphone", "headset", "наушник", "гарнитур", "earphone", "buds", "airpods",
                      "hands-free")


def _output_is_headphones() -> bool:
    """Звук идёт в наушники — до микрофона он не доходит, кто бы ни был микрофоном."""
    try:
        name = os.getenv("JARVIS_OUTPUT_DEVICE", "").strip()
        if not name:
            name = str(sd.query_devices(kind="output").get("name", ""))
    except Exception:
        return False
    return any(k in name.lower() for k in _HEADPHONE_OUTPUTS)


def _mic_hears_speakers(device) -> bool:
    """Слышит ли выбранный микрофон собственные динамики. Для тех, что не
    слышат, глушить микрофон по громкости динамиков незачем — а глушение
    делало Джарвиса глухим на всё время музыки, фильма и игры.

    Раньше смотрели только на имя микрофона: гарнитура, видная в Windows как
    «Микрофон (USB Audio Device)», считалась слышащей колонки — и в игре в
    наушниках Джарвис был глух. Теперь и на то, куда идёт звук."""
    if _output_is_headphones():
        return False
    try:
        info = sd.query_devices(device) if device is not None else sd.query_devices(kind="input")
        name = str(info.get("name", "")).lower()
    except Exception:
        return True
    return not any(k in name for k in _SPEAKER_DEAF_MICS)


# Сколько после собственной речи ещё не слушать микрофон: звук досыпается из
# буфера звуковой карты и отражается от стен. Без этого хвоста Джарвис
# слышал конец своей фразы и отвечал сам себе.
_ECHO_TAIL_SEC = float(os.getenv("JARVIS_ECHO_TAIL_SEC", "0.5"))
# «Окно поправки»: включили музыку/видео — столько секунд звук приглушён и
# микрофон пропускает речь поверх него. Иначе «поставь Люби меня… нет-нет,
# не её, а …» терялось: заиграла музыка — гейт динамиков закрыл микрофон.
_CORRECTION_SEC = float(os.getenv("JARVIS_CORRECTION_SEC", "8"))
_MEDIA_TOOLS = {"music_player", "youtube_player", "movie_player"}
# Пауза между слогами голоса поверх музыки — столько кадров ещё пропускаем.
_VOICE_OVER_MUSIC_HOLD = 0.6


def _frame_rms(indata) -> float:
    try:
        import numpy as np
        return float(np.sqrt(np.mean(np.square(indata.astype(np.float32)))))
    except Exception:
        return 0.0

# Потолок паузы между попытками подключения к Gemini.
_RECONNECT_MAX_SEC = 30.0
# Сессия считается здоровой, если прожила столько — иначе разрыв сразу после
# подключения идёт с нарастающей паузой, а не крутится по два раза в секунду.
_SESSION_HEALTHY_SEC = 10.0
# Сколько секунд без кадров считать, что микрофон отвалился (см. _listen_audio).
_MIC_STALL_SEC = 2.0
# Fish ждёт следующий кусок ответа не дольше этого (см. _fish_worker).
_FISH_IDLE_SEC = 20.0
# Fish недавно ответил — следующие куски синтезируются сразу, без «пробы»
# первым куском (иначе второе предложение ждало первое, и между ними — пауза).
_FISH_TRUST_SEC = 300.0

# Мгновенные ответы (core/quick.py): сколько после своего ответа глушить
# запоздалую речь Gemini на ту же команду, если пользователь молчит.
_QUICK_HOLD_SEC = 8.0


def _quick_allowed() -> bool:
    """Готовые фразы — голосом Джарвиса (Fish). С голосом Gemini они звучали
    бы чужим голосом посреди разговора, поэтому там — только по JARVIS_QUICK=1."""
    if not quick.enabled():
        return False
    return get_voice_provider() == "fish" or os.getenv("JARVIS_QUICK", "").strip() == "1"
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
    a.txt не должно открывать удаление всего рабочего стола.

    Сообщение и звонок контакту — по «кому и что сделать»: модель на втором
    вызове может пересказать текст иначе, а уйдёт всё равно ровно тот текст,
    который пользователь услышал и подтвердил (см. _execute_tool)."""
    import json as _json
    if name == "contacts":
        return f"contacts:{_action_of(args)}:{str(args.get('name', '')).strip().lower()}"
    return name + ":" + _json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


def _confirm_question(name: str, args: dict) -> str:
    """Что именно переспросить — дословно (для сообщений и звонков людям)."""
    if name != "contacts":
        return ""
    try:
        from core.contacts import contacts
        return contacts().confirm_text(_action_of(args), str(args.get("name", "")), str(args.get("text", "")),
                                       str(args.get("ask", "")))
    except Exception:
        return ""


def _tool_human(name: str) -> str:
    """«Поиск», «Открываю…» вместо web_search/open_app — в чате владельца."""
    try:
        from ui_island import tool_label
        return tool_label(name)
    except Exception:
        return name


def _confirm_label(name: str, args: dict) -> str:
    """Вопрос для кнопок на капсуле: «Выключить компьютер?», «Удалить a.txt?»."""
    question = _confirm_question(name, args)
    if question:
        return question
    action = _action_of(args)
    target = str(args.get("path") or args.get("name") or args.get("value") or "").strip()
    if name == "computer_control":
        if any(k in action for k in ("restart", "reboot", "перезагруз")):
            return "Перезагрузить компьютер?"
        return "Выключить компьютер?"
    if name == "files":
        return f"Удалить {target}?" if target else "Удалить файлы?"
    if name == "macro":
        return f"Запустить команду «{target}»?" if target else "Запустить свою команду?"
    return f"Выполнить {name} · {action}?"


def _tool_outcome(result) -> bool | None:
    """Как закончился шаг для капсулы: True — сделано, False — не вышло,
    None — ждёт «да» (это не провал)."""
    text = str((result or {}).get("result", "") if isinstance(result, dict) else result or "")
    if text.startswith("НЕ ВЫПОЛНЕНО — нужно подтверждение"):
        return None
    return not text.startswith(("Ошибка", "НЕ ВЫПОЛНЕНО", "Не выполнено", "Не успело"))


_QUOTA_WORDS = ("resource_exhausted", "quota", "429", "rate limit", "too many requests")


_YES_RE = re.compile(
    r"\b(да|давай|подтверждаю|конечно|выключай|удаляй|перезагружай|ага|угу|"
    r"yes|yeah|ok|окей|ha|ҳа|иә|иа)\b", re.IGNORECASE)
_NO_RE = re.compile(r"\b(нет|не|отмена|стоп|no|yo'q|жоқ)\b", re.IGNORECASE)


def _is_affirmative(text: str) -> bool:
    return bool(text) and bool(_YES_RE.search(text)) and not _NO_RE.search(text)


# Будильник ставится только по просьбе владельца. Модель слышит всю комнату и
# сама додумывала «разбужу вас в 7» из «Обо мне» или чужой речи — в капсуле
# появлялся «Будильник 07:00», которого никто не заводил.
_ALARM_ASK_RE = re.compile(
    r"будильник|буди|подним|подъ[её]м|просн|встать|вставать|встаю|alarm|wake|uyg['ʻ’`]?ot|budilnik",
    re.I)


def _alarm_requested(*heard: str) -> bool:
    return any(_ALARM_ASK_RE.search(h or "") for h in heard)


def _is_destructive(name: str, args: dict) -> bool:
    if name == "contacts" and _action_of(args) in ("message", "call"):
        # Человеку от вашего имени — только после «да». Но если такого
        # контакта нет или писать ему нельзя, спрашивать нечего: инструмент
        # сам скажет, что не так.
        try:
            from core.contacts import contacts
            c, _problem = contacts().precheck(_action_of(args), str(args.get("name", "")),
                                              str(args.get("text", "")) or "…", urgent=True)
            return c is not None
        except Exception:
            return True
    if name == "macro" and _action_of(args) == "run":
        try:
            from core.macros import macros
            return macros().needs_confirm(str(args.get("name") or args.get("phrase") or ""))
        except Exception:
            return False
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
    if "`" in text or "**" in text:                 # блоки кода и разметка — не в чат
        from core.speech_text import for_chat
        text = for_chat(text)

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


# Конец предложения в самом конце расшифровки: «…, сэр.» — не цифра («2.» → «2.5»)
# и не однобуквенное сокращение («г.», «И.»).
_END_OF_SENTENCE = re.compile(r"[^\W\d_]{2,}[.!?…]+[»\"')]*\s*$")
# Первый кусок можно отрезать и по запятой, если до неё набралось столько символов.
_MIN_CLAUSE_CHUNK = 20
# Расшифровка затихла на законченном предложении — дальше не ждём (см. _receive_audio).
_FISH_SENTENCE_IDLE_SEC = float(os.getenv("JARVIS_SENTENCE_IDLE_MS", "250")) / 1000


def _take_speakable(buf: str, first: bool, final: bool, force: bool = False) -> tuple[list[str], str]:
    """Отрезает от потоковой расшифровки ответа готовые к синтезу куски.

    Fish раньше получал ответ только по turn_complete — а тот приходит на
    4-5 секунд позже первого звука Gemini (замер в test_voice_loop_e2e):
    модель «проговаривает» весь ответ, прежде чем закрыть ход. Эти секунды
    Джарвис молчал. Теперь предложение уходит в синтез, как только в
    расшифровке появилась его точка. Пороги те же, что у _split_for_speech.

    Точка в самом конце расшифровки тоже конец: раньше резали только по
    «точка + пробел», и ПОСЛЕДНЕЕ предложение (а у Джарвиса ответ чаще всего
    из одного: «Включаю, сэр.») ждало turn_complete — те самые 4-5 секунд.
    force — расшифровка затихла: законченное предложение отдаём и короче порога.
    """
    chunks: list[str] = []
    while True:
        floor = _MIN_FIRST_CHUNK if first and not chunks else _MIN_SPEECH_CHUNK
        cut = next((m.end() for m in re.finditer(r"[.!?…]+(?=\s)", buf)
                    if len(buf[:m.end()].strip()) >= floor), None)
        if cut is None and buf.strip() and _END_OF_SENTENCE.search(buf) and (force or len(buf.strip()) >= floor):
            cut = len(buf)
        if cut is None and first and not chunks:
            # Длинное первое предложение ждало своей точки целиком. Первый звук
            # важнее интонации одной запятой: «Включаю плейлист для учёбы, …»
            # уходит в озвучку по запятой, остальное догоняет.
            cut = next((m.end() for m in re.finditer(r"[,;:—–]+(?=\s)", buf)
                        if len(buf[:m.end()].strip()) >= _MIN_CLAUSE_CHUNK), None)
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
        "description": ("Погода в городе: сейчас и прогноз на сегодня, завтра и послезавтра. Вызывай и на "
                        "«погода на завтра» — прогноз уже в ответе. Город не назван — не передавай city: "
                        "возьмётся город, где пользователь."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "Название города (если назван)"}
            },
            "required": []
        }
    },
    {
        "name": "web_search",
        "description": (
            "Найти в интернете и получить результаты ТЕКСТОМ, чтобы ответить вслух: факты, новости, "
            "цены, «кто такой», «что случилось». Не открывает браузер. Если пользователь хочет сам "
            "посмотреть выдачу на экране — browser."
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
            "в файл (чтобы посмотреть на экран — look_at_screen). Громкость здесь — СИСТЕМНАЯ (весь "
            "ноутбук): «громче», «громкость на 100», «звук на 50» без уточнения. Про музыку — "
            "music_player, про фильм/видео/ролик — video_control."
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
            "Открыть сайт или поисковую выдачу НА ЭКРАНЕ: «открой ютуб», «зайди на vk.com», "
            "«покажи в гугле …». Для ответа голосом без браузера — web_search."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["go_to", "search"]},
                "url":    {"type": "STRING", "description": "Адрес для go_to (youtube.com, https://…)"},
                "query":  {"type": "STRING", "description": "Запрос для search"},
                "engine": {"type": "STRING", "enum": ["google", "yandex", "duckduckgo", "bing"]},
                "browser": {"type": "STRING", "enum": ["chrome", "edge", "firefox", "yandex", "opera", "brave"],
                            "description": "Только если пользователь назвал браузер"}
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
                    "enum": ["identity", "relationships", "work", "health", "habits", "dates",
                             "preferences", "projects", "wishes", "notes"],
                },
                "key":   {"type": "STRING", "description": "Короткий ключ: name, city, любимая_музыка"},
                "value": {"type": "STRING", "description": "Значение на языке пользователя, как он сказал"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "recall_memory",
        "description": (
            "Вспомнить, что ты знаешь о пользователе и о чём вы говорили. Вызывай на «что ты обо мне знаешь», "
            "«помнишь, я говорил про…», «о чём мы вчера говорили», «как зовут моего брата», и когда сам "
            "не уверен в факте о пользователе — прежде чем переспрашивать. query — тема; пусто — всё."
        ),
        "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}}
    },
    {
        "name": "forget_memory",
        "description": "Забыть факт о пользователе: «забудь, что я…», «удали из памяти…». query — о чём.",
        "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}
    },
    {
        "name": "obsidian",
        "description": (
            "Личная база знаний пользователя в Obsidian (markdown-заметки). "
            "Вызывай, когда пользователь просит: запиши/сохрани заметку, добавь в дневник, "
            "«что я записывал про…», найди заметку, прочитай заметку, покажи список заметок. "
            "action=write — новая заметка (title + content); "
            "append — дописать в существующую заметку (title + content: «добавь в список покупок молоко»); "
            "delete — удалить заметку (title; уходит в корзину, можно вернуть); "
            "append_daily — дописать строку в дневник за сегодня (content); "
            "search — найти по базе (query); "
            "read — прочитать заметку по заголовку (title; «последняя» — самая свежая); "
            "list — список заметок (folder — опционально)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "write | append | delete | append_daily | search | read | list"},
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
            "Вызывай ТОЛЬКО на «режим учёбы/работы/кино/музыки», «обычный режим», «пора учиться», "
            "«пора работать». «Включи музыку» — это music_player, «хочу фильм X» — movie_player."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {"type": "STRING", "enum": ["study", "work", "movie", "music", "normal"]},
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
            "ФИЛЬМЫ И СЕРИАЛЫ — всегда на VK Видео: найти, открыть, запустить и развернуть на весь "
            "экран. «Поставь Железный человек 2», «включи фильм Интерстеллар», «хочу посмотреть "
            "Дюну». Управление после запуска — video_control."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["play"]},
                "title": {"type": "STRING", "description": "Название фильма/сериала (можно с годом, сезоном)"},
            },
            "required": ["action", "title"]
        }
    },
    {
        "name": "youtube_player",
        "description": (
            "ВИДЕО И КЛИПЫ — YouTube, сразу на весь экран. play + query: «поставь клип Люби меня», "
            "«включи видео как собрать ПК». latest + channel: «поставь последнее видео MrBeast». "
            "Управление после запуска — video_control."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["play", "latest"]},
                "query": {"type": "STRING", "description": "Что искать (для клипа можно добавить «клип»)"},
                "channel": {"type": "STRING", "description": "Канал для latest: «MrBeast», «Wylsacom»"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "video_control",
        "description": (
            "Управление фильмом или роликом, который идёт в окне Джарвиса (VK Видео, YouTube): пауза, "
            "продолжи, перемотай вперёд/назад на N, перемотай на 1:20:00, «сколько осталось» / "
            "«который час в фильме» (time), громче/тише/громкость N, выключи/включи звук видео, "
            "полный экран / выйди из полного экрана, скорость, следующее видео, закрой фильм. "
            "Если идёт видео, «пауза» — сюда, а не в music_player. Громкость — сюда, только если "
            "сказано про фильм/видео/ролик («громкость фильма на 100», «сделай видео тише»); просто "
            "«громче» — системная (computer_control)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["pause", "resume", "seek_forward", "seek_back", "seek_to", "restart",
                             "time", "volume_up", "volume_down", "volume_set", "mute", "unmute",
                             "fullscreen", "exit_fullscreen", "speed", "next", "close"],
                },
                "value": {
                    "type": "STRING",
                    "description": ("seek_forward/back — сколько («30 секунд», «5 минут», по умолчанию 10 с); "
                                    "seek_to — время («1:20:00», «45 минут»); volume_* — проценты; speed — «1.5»"),
                },
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
            "МУЗЫКА — всегда Spotify (Premium). login — «подключи Spotify» (вход один раз). "
            "play + query — включить трек/исполнителя/"
            "альбом («включи Believer», «поставь Любэ»); play без query — продолжить; mood + query — "
            "плейлист под настроение («спокойное», «для работы»); pause, resume, next, previous; "
            "now_playing — «что играет», «кто поёт»; volume_* — громкость САМОГО Spotify, когда "
            "речь про музыку: «громкость музыки на 100», «музыку тише», «сделай Spotify громче». "
            "Просто «громче/тише/громкость» без слова «музыка» — computer_control. "
            "НЕ переспрашивай «какая именно песня/исполнитель?»: сразу play с тем, что сказали "
            "(«safe sound» → query «safe sound»), Spotify сам найдёт лучшее совпадение."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["play", "mood", "pause", "resume", "next", "previous", "now_playing",
                             "volume_up", "volume_down", "volume_set", "shuffle", "login"],
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
            "Завершить саму программу ДЖАРВИС. ТОЛЬКО на явное: «выключись, Джарвис», «заверши "
            "работу», «закройся». НЕ на «стоп», «пауза», «хватит», «пока», «выключи музыку», "
            "«закрой окно», «выключи компьютер» — для них другие инструменты или просто ответ."
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
            "ЕДИНСТВЕННЫЙ способ увидеть экран. Вызывай на «что у меня на экране», «посмотри», "
            "«где ошибка в коде», «прочитай», «оцени дизайн», «что это за окно». Никогда не описывай "
            "экран без вызова. После ответа кадр остаётся у тебя — на уточнения отвечай по нему."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "Вопрос пользователя про экран, его словами"},
                "source": {
                    "type": "STRING",
                    "enum": ["screen", "active_window"],
                    "description": "active_window — для кода, текста, одной программы (чётче); screen — весь монитор"
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "remember_screen",
        "description": (
            "«Запомни это», «сохрани, что на экране», «запомни эту статью / номер заказа / ошибку». "
            "Делает снимок окна, выписывает главное (суть, номера, даты, ссылки) и кладёт заметку "
            "в Obsidian (папка «Запомнил») вместе с картинкой. Найти потом — obsidian search. "
            "Не путай с save_to_memory: тот — факт о пользователе словами, этот — то, что на экране."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "focus": {"type": "STRING", "description": "Что именно важно, если уточнил («номер заказа»)"},
                "source": {"type": "STRING", "enum": ["window", "screen"],
                           "description": "window — активное окно (обычно), screen — весь экран"},
            },
        }
    },
    {
        "name": "look_at_camera",
        "description": (
            "Посмотреть через веб-камеру: «что у меня в руке», «как я выгляжу», «посмотри на меня», "
            "«что это за предмет»."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "Вопрос пользователя, его словами"}
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
            "Брифинг — факты на сегодня: погода в его городе, будильники и таймеры, дела из календаря, "
            "новые сообщения от близких, новости по его темам, день рождения. Вернёт факты — расскажи "
            "своими словами, коротко. Вызывай на «брифинг», «что сегодня», «доброе утро», «введи в курс "
            "дня». Утром система запускает его сама, один раз — сам повторно не вызывай."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "clock",
        "description": (
            "Часы: время, таймеры, секундомер, будильники. ВСЕГДА вызывай на «который час», «какое "
            "сегодня число/день» (action=now) — время в промпте устаревает. Время в другом городе — "
            "world_time (city). Таймер: «поставь таймер на 10 минут», «таймер на пасту на 8 минут» — "
            "timer_set (minutes/seconds/hours или duration, label); «сколько осталось» — timer_list; "
            "«отмени таймер» — timer_cancel (label; «все»); «добавь 5 минут» — timer_add; пауза — "
            "timer_pause / timer_resume. Секундомер: stopwatch_start / stopwatch_stop / stopwatch_lap / "
            "stopwatch_reset / stopwatch_status. Будильник: «разбуди в 7», «будильник на 6:30 по "
            "будням» — alarm_set (time, repeat, label); alarm_list; alarm_cancel (time или label; "
            "«все»); звенит и просят отложить — alarm_snooze (minutes); выключить — alarm_stop. "
            "«Напомни через час позвонить» — это не таймер, а calendar."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": [
                    "now", "world_time", "timer_set", "timer_list", "timer_cancel", "timer_add",
                    "timer_pause", "timer_resume", "stopwatch_start", "stopwatch_stop", "stopwatch_lap",
                    "stopwatch_reset", "stopwatch_status", "alarm_set", "alarm_list", "alarm_cancel",
                    "alarm_stop", "alarm_snooze"]},
                "hours": {"type": "NUMBER"},
                "minutes": {"type": "NUMBER", "description": "Минуты (таймер, добавить время, отложить будильник)"},
                "seconds": {"type": "NUMBER"},
                "duration": {"type": "STRING", "description": "Длительность словами, если так проще: «полчаса», «1:30»"},
                "label": {"type": "STRING", "description": "Подпись таймера/будильника («паста», «созвон»)"},
                "time": {"type": "STRING", "description": "Время будильника «07:30» (24 часа)"},
                "repeat": {"type": "STRING", "enum": ["once", "daily", "weekdays", "weekends"]},
                "city": {"type": "STRING", "description": "Город для world_time"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "eyes",
        "description": (
            "Глаза — постоянное зрение, как демонстрация экрана. «Смотри на экран», «следи за "
            "экраном», «будь моими глазами» — open source=screen (window — только окно впереди); "
            "«смотри на меня», «включи камеру» — open source=camera; «закрой глаза», «не смотри» — "
            "close; «ты видишь?» — status. Пока глаза открыты, кадры приходят тебе сами: на «что "
            "это?», «где ошибка?» отвечай по ним (для мелкого текста — look_at_screen). Сам по "
            "кадрам не заговаривай. «Скажи, когда загрузка дойдёт до 100%», «следи, когда придёт "
            "ответ» — watch (condition — что должно случиться, minutes — сколько ждать); "
            "stop_watch — перестать."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["open", "close", "status", "watch", "stop_watch"]},
                "source": {"type": "STRING", "enum": ["screen", "window", "camera"]},
                "condition": {"type": "STRING", "description": "Для watch: что должно появиться на экране"},
                "minutes": {"type": "NUMBER", "description": "Для watch: сколько минут следить (по умолчанию 30)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "macro",
        "description": (
            "СВОИ КОМАНДЫ пользователя: фраза → цепочка действий. create — «создай команду режим "
            "стрима: открой OBS, подожди 2 секунды, нажми ctrl+shift+s и включи музыку» (разложи на "
            "steps сам; phrases — как он будет её говорить, 1-3 варианта; {слово} во фразе — "
            "переменная часть). run — выполнить по названию, list — какие есть, show — что делает, "
            "delete — удалить. packs — готовые паки для программ (браузер, Windows, Telegram, VS Code, "
            "Photoshop, Discord, Spotify), install_pack / remove_pack (name — пак). editor — открыть "
            "окно «Свои команды». Сказанная фраза своей команды обычно выполняется сама мгновенно. "
            "when — запускать и без фразы: «каждый будний день в 9 открывай почту», «когда открываю "
            "OBS — включи музыку», «при запуске»; тогда phrases можно не давать."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["create", "run", "list", "show", "delete", "packs",
                                                      "install_pack", "remove_pack", "editor"]},
                "name": {"type": "STRING", "description": "Название команды или пака"},
                "phrases": {"type": "ARRAY", "items": {"type": "STRING"},
                            "description": "create: фразы запуска («включи режим стрима»)"},
                "app": {"type": "STRING", "description": "create: только в этой программе («chrome.exe»), обычно пусто"},
                "confirm": {"type": "BOOLEAN", "description": "create: переспрашивать перед запуском"},
                "when": {
                    "type": "ARRAY",
                    "description": "create: когда запускать самой (обычно пусто)",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "on": {"type": "STRING", "enum": ["time", "app", "start"]},
                            "at": {"type": "STRING", "description": "on=time: «09:00»"},
                            "days": {"type": "STRING", "description": "on=time: будни / выходные / каждый день "
                                                                      "/ «пн,ср,пт»"},
                            "app": {"type": "STRING", "description": "on=app: процесс или имя («obs», «steam»)"},
                        },
                        "required": ["on"],
                    },
                },
                "steps": {
                    "type": "ARRAY",
                    "description": "create: шаги по порядку",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "do": {"type": "STRING", "enum": ["open_app", "open_url", "keys", "type", "click",
                                                              "wait", "volume", "media", "say", "tool"]},
                            "value": {"type": "STRING", "description": (
                                "open_app — программа; open_url — адрес; keys — «ctrl+shift+s»; type — "
                                "текст; wait — секунды; volume — 0-100; media — playpause/next/previous; "
                                "say — фраза; click — left/right/double")},
                            "x": {"type": "NUMBER"}, "y": {"type": "NUMBER"},
                            "tool": {"type": "STRING", "description": "do=tool: инструмент (music_player, clock…)"},
                            "args": {"type": "STRING", "description": "do=tool: аргументы JSON-строкой "
                                                                      "(«{\"action\": \"play\", \"query\": \"lofi\"}»)"},
                        },
                        "required": ["do"],
                    },
                },
            },
            "required": ["action"]
        }
    },
    {
        "name": "football",
        "description": (
            "Любимый футбольный клуб пользователя: next — «когда играет Реал», «когда следующий матч»; "
            "last — «как сыграли», «с каким счётом закончили»; score — «какой счёт»; news — «новости Барсы»; "
            "set_club — «я болею за Реал» (club — как сказал); goals_off / goals_on — не сообщать / сообщать о голах; "
            "news_off / news_on — о новостях клуба; watch — «поставь матч Реала против Барсы» (match — как сказал): "
            "открывает трансляцию на Кинопоиске в Chrome/Яндекс.Браузере, если она там есть."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["next", "last", "score", "news", "set_club", "goals_off",
                                                      "goals_on", "news_off", "news_on", "watch"]},
                "match": {"type": "STRING", "description": "watch: какой матч, как сказал («Реал против Барсы»)"},
                "club": {"type": "STRING", "description": "set_club: клуб, как назвал («Реал», «Барселона»)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "study",
        "description": (
            "УЧЁБА пользователя: расписание пар и задачи. today / tomorrow — «какие пары сегодня/завтра», "
            "day (when — «в четверг») , week — вся неделя, now — «какая сейчас/следующая пара», deadlines — "
            "«какие дедлайны / что задали», add_task — «задали решить задачи 5-10 по матану до пятницы» "
            "(title, subject, due словами, kind: домашка/контрольная/экзамен/проект), done — «сделал эссе», "
            "delete_task, add_lesson (subject, weekday, start, end, room, kind, weeks all/odd/even), "
            "focus — «режим учёбы», «давай позанимаемся 25 минут» (minutes, subject). Окно — app_window "
            "window=study. Объяснить тему, проверить решение — сам, это не сюда."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["today", "tomorrow", "day", "week", "now", "deadlines",
                                                      "add_task", "done", "delete_task", "add_lesson", "focus"]},
                "when": {"type": "STRING", "description": "day: день словами"},
                "title": {"type": "STRING", "description": "Задача: что сделать"},
                "subject": {"type": "STRING", "description": "Предмет, как сказал (можно «матан»)"},
                "due": {"type": "STRING", "description": "Срок словами: «в пятницу», «до 5 октября», «завтра»"},
                "kind": {"type": "STRING", "description": "домашка/контрольная/экзамен/проект; для пары — лекция/практика/семинар/лабораторная"},
                "weekday": {"type": "STRING"}, "start": {"type": "STRING"}, "end": {"type": "STRING"},
                "room": {"type": "STRING"}, "weeks": {"type": "STRING", "enum": ["all", "odd", "even"]},
                "minutes": {"type": "NUMBER"}, "days": {"type": "NUMBER"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "about_me",
        "description": (
            "Знакомство с пользователем — анкета «Обо мне» (имя, как обращаться, город, подъём/отбой, "
            "музыка, новости, близкие, цели…). next — какой вопрос задать следующим; answer (key, value) — "
            "сохранить ответ, вернёт следующий вопрос; skip (key) — пропустить; later — «потом»/«хватит»; "
            "restart — «давай познакомимся», «спроси меня обо мне»; status — «что ты обо мне знаешь по "
            "анкете». Окно — app_window window=about. Отдельные факты вне анкеты — save_to_memory."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["next", "answer", "skip", "later", "restart", "status"]},
                "key": {"type": "STRING", "description": "Ключ вопроса из next (name, city, wake_time…)"},
                "value": {"type": "STRING", "description": "answer: ответ кратко, как сказал пользователь"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "contacts",
        "description": (
            "ЛЮДИ из записной книжки пользователя. message — «напиши маме, что задержусь на 20 минут» "
            "(от ЕГО Telegram; text — само сообщение от первого лица, как написал бы он: «Задержусь на "
            "20 минут»; as_voice — голосовым); call — «позвони брату и скажи, что ужин готов» (звонит "
            "аккаунт Джарвиса, text — что передать; urgent — если сказал «срочно»); read — «что мне "
            "написали?», «что написала мама?»; add — «добавь контакт Азиз — @aziz» (aliases — как ещё "
            "его называет); delete; find; list. Перед отправкой и звонком система попросит "
            "подтверждение — переспроси ДОСЛОВНО и вызови снова только после «да». Себе в Telegram — "
            "send_to_telegram, позвонить самому пользователю — phone_call."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["message", "call", "read", "add", "delete", "find", "list"]},
                "name": {"type": "STRING", "description": "Кому — как назвал пользователь: «мама», «брат», «Азиз»"},
                "text": {"type": "STRING", "description": "message: текст сообщения; call: что передать"},
                "as_voice": {"type": "BOOLEAN"},
                "urgent": {"type": "BOOLEAN", "description": "call: сказал «срочно» — можно и ночью"},
                "ask": {"type": "STRING", "description": "call: что спросить у человека и запомнить ответ («во сколько придёт»)"},
                "telegram": {"type": "STRING", "description": "add: @username или номер"},
                "aliases": {"type": "STRING", "description": "add: как ещё называет, через запятую"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "app_window",
        "description": (
            "Открыть окно Джарвиса: keys — «открой ключи», «где ввести ключ», «проверь ключи», "
            "«подключи Spotify/звонки» (там же вход кнопкой); commands — «открой редактор команд»; "
            "contacts — «открой контакты», «подключи мой телеграм»; about — «открой обо мне», «что ты обо мне знаешь» "
            "(окно); study — «открой расписание», «открой учёбу»; football — «открой футбол», «покажи матчи Реала»; settings — «открой настройки», «поменяй микрофон», «выбери динамик»; help — «что ты умеешь», «помощь», «подсказки», "
            "«с чего начать»; backup — «сделай резервную копию», «перенеси на новый ПК», «восстанови из копии» "
            "(пароль — только в окне, не голосом)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"window": {"type": "STRING",
                                      "enum": ["keys", "commands", "contacts", "about", "study", "help",
                                               "backup", "football", "settings"]}},
            "required": ["window"]
        }
    },
    {
        "name": "location",
        "description": (
            "Где пользователь: «где я», «какой у меня город» — where; «я живу в Ташкенте», «мой город "
            "Алматы» — set_home (city); «сколько километров до Москвы» — distance (city, опционально "
            "from_city); координаты и часовой пояс города — coordinates (city; без него — свои)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["where", "set_home", "distance", "coordinates"]},
                "city": {"type": "STRING"},
                "from_city": {"type": "STRING"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "sleep_timer",
        "description": (
            "Управляет умным таймером сна с подтверждением голосом и автовыключением ноутбука/ПК. "
            "По истечении времени Джарвис спросит разрешение выключить ПК. Если пользователь молчит 30 сек — выключает. "
            "Вызывай когда пользователь говорит: 'через полчаса буду спать', 'через 30 минут спать', "
            "'таймер сна на 45 минут', 'через час выключи ноут', 'отмени таймер сна', 'сколько осталось до сна'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["set", "cancel", "status", "confirm"],
                    "description": "confirm — пользователь сказал «да» на вопрос о выключении"
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
        "name": "break_reminder",
        "description": (
            "Напоминания о перерыве, когда пользователь долго смотрит фильм/YouTube или играет. "
            "Джарвис сам напоминает через 2 часа, потом каждый час. Вызывай на: «не напоминай сегодня» "
            "(off_today), «выключи напоминания о перерыве» (off), «включи напоминания» (on), "
            "«напоминай через час / каждые 30 минут» (set), «сколько я уже смотрю/играю» (status)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["status", "off_today", "off", "on", "set"]},
                "first_minutes": {"type": "NUMBER", "description": "set: через сколько минут первое напоминание"},
                "repeat_minutes": {"type": "NUMBER", "description": "set: как часто потом, в минутах"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "phone_call",
        "description": (
            "Джарвис САМ звонит пользователю в Telegram и разговаривает голосом. "
            "call_now — «позвони мне»; schedule — «позвони мне в 6 утра» (time \"06:00\"), "
            "«звони каждое утро в 7» (repeat=daily), «позвони через 20 минут» (in_minutes); "
            "transcript — «что он сказал?», «о чём говорили», «покажи расшифровку звонка» (name — с кем); "
            "history — «кому ты звонил», «история звонков»; "
            "topic — зачем звонить («утренний отчёт», «напомнить про встречу»): для утреннего звонка "
            "Джарвис зачитает погоду, календарь и новости. cancel — отменить (time — какой), list — какие звонки стоят."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["call_now", "schedule", "cancel", "list", "transcript",
                                                      "history"]},
                "name": {"type": "STRING", "description": "transcript: с кем был звонок (пусто — последний)"},
                "time": {"type": "STRING", "description": "ЧЧ:ММ, 24 часа: «6 утра» → \"06:00\", «в 9 вечера» → \"21:00\""},
                "in_minutes": {"type": "NUMBER", "description": "позвонить через столько минут"},
                "repeat": {"type": "STRING", "enum": ["once", "daily"]},
                "topic": {"type": "STRING", "description": "повод звонка"}
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
    _go_away_at: float | None = None      # Gemini прислал GoAway — переподключение плановое

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
        # «Настройки» меняют микрофон/динамик/порог на ходу (core/settings.py).
        self._mic_reopen = False
        self._out_reopen = False
        try:
            from core import settings as _st
            _st.subscribe(self._on_setting)
        except Exception as exc:
            logger.debug("Настройки: подписка не удалась: %s", exc)
        self._followups_left = 0      # сколько реплик без имени ещё продолжат разговор
        self._rearm_after_speech = False
        self._fish_task: asyncio.Task | None = None
        self._quick_lift = False     # system-реплика: снять глушение мгновенного хода
        self._echo_guard_until = 0.0  # см. _ECHO_TAIL_SEC
        self._through_until = 0.0     # «окно поправки»: слышим речь поверх музыки (_CORRECTION_SEC)
        from core.echo_gate import EchoGate
        self._echo = EchoGate()       # голос или эхо колонок (учится на ходу, помнит комнату)
        self._voice_over_music_at = 0.0
        self._resume_handle: str | None = None  # возобновление сессии после разрыва

        # Новый мозг ДЖАРВИС
        self.user_profile = UserProfile(DATA_DIR)
        self.initiative_engine = InitiativeEngine()
        self.proactive_engine = ProactiveEngine(DATA_DIR)
        self.team_engine = TeamCollaborationEngine(DATA_DIR)
        self.last_user_text = ""
        self._user_turn = 0      # номер последней реплики пользователя (для подтверждений)
        self._heard_now = ""     # реплика, которую пользователь говорит прямо сейчас
        # Ждёт «да»: (ключ, когда спросили, номер реплики) и сами аргументы.
        # Без этого первое же «выключи компьютер» падало AttributeError
        # вместо вопроса «точно?» — проверялось только правило, не сам вопрос.
        self._pending_destructive: tuple | None = None
        self._pending_args: dict | None = None
        # Последние ~30 с вашей речи (громкие кадры) — для проверки голоса,
        # и номер реплики, набранной с клавиатуры (ей доверяем: вы за ПК).
        self._voice_ring: collections.deque = collections.deque(maxlen=480)
        self._typed_turn = -1

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
        # Spotify: токен и статус Premium — заранее, первая песня не ждёт.
        try:
            from actions import spotify_premium
            spotify_premium.warm_up()
        except Exception as exc:
            logger.debug("Spotify заранее: %s", exc)

        # Список программ для «открой …» — собирается в фоне, пока грузится
        # остальное (Get-StartApps занимает пару секунд).
        try:
            from core import win_apps
            win_apps.warm_up()
        except Exception as exc:
            logger.debug("Индекс программ: %s", exc)

        # Память: после разговора факты и итог — в долгосрочную память.
        try:
            from memory.conversation import collector
            collector().start()
            from memory import shared
            shared.start()          # одна память с Telegram-ботом
        except Exception as exc:
            logger.warning("Сбор памяти не запустился: %s", exc)

        # Глаза: постоянное зрение экрана или камеры (core/eyes.py).
        try:
            from core.eyes import SOURCES, eyes
            ey = eyes()
            ey.send = self._send_frame
            ey.active = self.is_awake
            ey.say = self.speak
            ey.on_change = lambda src: self.ui.write_log(
                "SYS: 👁 глаза закрыты" if src == "off" else f"SYS: 👁 смотрю на {SOURCES.get(src, src)}")
        except Exception as exc:
            logger.warning("Глаза не подключились: %s", exc)

        # Самопроверка после команд: снимок экрана → «вышло ли» (core/verify.py).
        try:
            from core.verify import verifier
            vf = verifier()
            vf.say = self.speak
            vf.log = self.ui.write_log
        except Exception as exc:
            logger.warning("Самопроверка не подключилась: %s", exc)

        # Ключи: сломанный или закончившийся ключ — сразу видно (журнал и
        # капсула), а не догадываться по странному поведению. В фоне.
        def _check_keys():
            try:
                from core import keys
                for sid, (state, text) in keys.check_all().items():
                    if state in ("bad", "warn") and sid != "gemini":
                        self.ui.write_log(f"SYS: 🔑 {keys.BY_ID[sid].title}: {text}")
            except Exception as exc:
                logger.debug("Проверка ключей: %s", exc)
        if os.getenv("JARVIS_KEYS_CHECK", "1") != "0":
            threading.Thread(target=_check_keys, daemon=True, name="keys-check").start()

        # Учёба (core/study.py): напоминания о парах и дедлайнах.
        try:
            from core.study import study
            stu = study()
            stu.say = self.speak
            stu.notify = lambda title, text: self.ui.write_log(f"SYS: 📚 {title}: {text}")
            stu.start()
        except Exception as exc:
            logger.warning("Учёба не запустилась: %s", exc)

        # Любимый клуб (core/football.py): напоминания о матчах, голы, важные новости.
        if os.getenv("JARVIS_FOOTBALL_WATCH", "1") != "0":
            try:
                from core.football import football
                fb = football()
                fb.say = self.speak
                fb.notify = lambda title, text: self.ui.write_log(f"SYS: ⚽ {title}: {text}")
                fb.start()
            except Exception as exc:
                logger.warning("Футбол не запустился: %s", exc)

        # «Обо мне»: кнопка «Познакомиться голосом» в окне зовёт сюда.
        def _voice_intro():
            from core import about_me
            self.speak(about_me.intro_instruction(restart=True))
        self.ui.on_voice_intro = _voice_intro

        # Звонки (core/call_log.py): расшифровка — в «Диалог» сразу после звонка.
        try:
            from core.call_log import call_log, title as call_title

            def _show_call(entry):
                self.ui.write_log(f"SYS: 📞 {call_title(entry)}")
                for ln in entry["lines"]:
                    self.ui.write_log(f"CALL: {ln['who']}: {ln['text']}")
            call_log().on_added = _show_call
        except Exception as exc:
            logger.warning("История звонков не подключилась: %s", exc)

        # Контакты (core/contacts.py): новые сообщения близких — в капсулу.
        try:
            from core.contacts import contacts
            ct = contacts()
            ct.log = self.ui.write_log
            ct.say = self.speak
            if os.getenv("JARVIS_CONTACTS_LISTEN", "1") != "0":
                ct.me.start_listening()
        except Exception as exc:
            logger.warning("Контакты не подключились: %s", exc)

        # Свои команды (core/macros.py): шагам нужны голос, журнал и инструменты.
        try:
            from core.macros import macros
            mc = macros()
            mc.say = self.speak
            mc.log = self.ui.write_log
            mc.run_tool = self._run_tool_blocking
            if os.getenv("JARVIS_MACRO_TRIGGERS", "1") != "0":
                from core.macro_triggers import Scheduler
                self._macro_triggers = Scheduler(mc)
                self._macro_triggers.start()
        except Exception as exc:
            logger.warning("Свои команды не подключились: %s", exc)

        # Мгновенные ответы (core/quick.py): готовые фразы озвучить заранее,
        # один раз — дальше «Есть, сэр» звучит без сети и без ожидания.
        if _quick_allowed() and os.getenv("JARVIS_QUICK_PREWARM", "1") != "0":
            def _prewarm():
                from telegram_bot import tts_edge, tts_fish

                async def synth(text):
                    if tts_fish.is_configured():
                        return await tts_fish.speak_pcm(text, sample_rate=RECV_SAMPLE_RATE)
                    return await tts_edge.speak_pcm(text, sample_rate=RECV_SAMPLE_RATE)
                try:
                    asyncio.run(quick.prewarm(synth, RECV_SAMPLE_RATE))
                except Exception as exc:
                    logger.warning("Готовые фразы не озвучены: %s", exc)
            threading.Thread(target=_prewarm, daemon=True, name="quick-prewarm").start()

        # Часы: таймеры, секундомер, будильники (core/clock.py). Сработало —
        # звук, голос Джарвиса и событие в журнале и капсуле.
        try:
            from core.clock import clock
            ck = clock()
            ck.say = self.speak
            ck.notify = lambda title, text: self.ui.write_log(f"SYS: ⏰ {title}: {text}")
            ck.start()
        except Exception as exc:
            logger.warning("Часы не запустились: %s", exc)

        # «Вы смотрите уже два часа…» — забота о перерывах (core/break_reminder.py)
        # и звонки по расписанию в Telegram (core/tg_call.py).
        try:
            from core import break_reminder, tg_call
            break_reminder.get(say=self.speak).start()
            tg_call.start_scheduler(done=self._call_finished)
        except Exception as exc:
            logger.warning("Перерывы/звонки не запустились: %s", exc)

        # Громкость программам, которую не вернули в прошлый раз (Джарвис
        # закрыли, пока музыка была приглушена), — сразу при запуске, а не
        # при первом приглушении: контроллер создаётся лениво.
        if sys.platform == "win32":
            try:
                from core.ducking_controller import get_ducking_controller
                get_ducking_controller()
            except Exception as exc:
                logger.debug("Дакинг: %s", exc)

        # Локальное слово «Джарвис». Запускается в _listen_audio: модели
        # нужен событийный цикл, чтобы будить Джарвиса из своего потока.
        self._local_wake: LocalWake | None = None
        self._wake_ring = collections.deque(maxlen=_WAKE_PREROLL_FRAMES)

        self.ui.on_text_command = self._on_text_command
        self.ui.on_island_confirm = self._on_island_confirm
        self.ui.on_file_dropped = self._on_file_dropped
        self.ui.on_island_file_action = self._on_island_file_action
        self.ui.on_island_mic = self._on_island_mic
        self.ui.on_wake_trained = self._on_wake_trained
        # Обучение — на том микрофоне, которым Джарвис слушает (раньше — системный).
        self.ui.wake_device = lambda: (getattr(self, "_input_device", None)
                                       if isinstance(getattr(self, "_input_device", None), int) else None)

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
        self._pc_link = self._start_pc_link()
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
            ducking_controller.duck("горячая клавиша F8")
        except Exception:
            pass

    def _on_hotkey_mute(self):
        """Реакция на глобальный хоткей Ctrl+Shift+M из любого приложения."""
        self.ui.toggle_mute()

    def _start_pc_link(self):
        """Связь с телефоном (Mini App «ПК-пульт», команды из Telegram) — внутри Джарвиса.
        Без неё установленный JARVIS.exe был для телефона «офлайн» (мост жил только
        отдельным процессом из папки с исходниками)."""
        if os.getenv("JARVIS_PC_LINK", "1") == "0":
            return None
        try:
            from telegram_bot import pc_server
            return pc_server.start_in_background()
        except Exception as exc:
            logger.warning("Связь с телефоном не поднялась: %s", exc)
            return None

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

    # ── Глаза: кадр в Live-сессию (core/eyes.py) ──────────────────────────────
    def _send_frame(self, jpeg: bytes) -> bool:
        """Из потока глаз: кадр — в голосовую сессию. Нет связи или микрофон
        выключен (Ctrl+M — «не слушай и не смотри») — кадр не уходит."""
        if not jpeg or not self._loop or not self.session or not self._loop.is_running() or self.ui.muted:
            return False
        fut = asyncio.run_coroutine_threadsafe(
            self.session.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg")),
            self._loop)
        try:
            fut.result(timeout=3)
            return True
        except Exception as exc:
            logger.debug("Кадр глаз не ушёл: %s", exc)
            return False

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
            self._typed_turn = self._user_turn
            # Набранная частая команда («поставь музыку safe sound») — сразу на ПК,
            # как сказанная голосом. Раньше текст всегда шёл в Gemini, и тот
            # переспрашивал «какая именно музыка?» вместо того, чтобы включить.
            q = quick.match(text) if getattr(self, "_pending_destructive", None) is None else None
            if q is not None and self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._quick_run(q, text, echo=False), self._loop)
                return
            self._remember_turn(text, "")
            self._send_text_to_session(text)

    def _on_island_confirm(self, ok: bool):
        """«Разрешить» / «Отклонить» на капсуле — то же, что набрать «да» / «нет»:
        клик мышью за этим ПК — владелец, как и набранное с клавиатуры. Проверка
        в _execute_tool та же: «да» засчитывается только на ждущий вызов."""
        logger.info("Капсула: %s", "разрешил" if ok else "отклонил")
        self.ui.write_log(f"SYS: {'✅ разрешено' if ok else '⛔ отклонено'} кнопкой на капсуле")
        if not ok:
            self._pending_destructive = None
        self._on_text_command("да" if ok else "нет, отмена")

    def _confirm_heard(self, text: str):
        """Голосом сказали «нет» на вопрос — снять кнопки с капсулы."""
        if self._pending_destructive and _NO_RE.search(text or "") and not _is_affirmative(text):
            done = getattr(self.ui, "confirm_done", None)
            if done:
                done()

    def _on_file_dropped(self, path: str):
        threading.Thread(target=self._send_dropped_file, args=(path,), daemon=True, name="drop-file").start()

    def _send_dropped_file(self, path: str):
        """Файл с капсулы: прочитать и спросить, что с ним сделать («Спросить
        про него» / «Кратко» / «Отмена»). Нет капсулы — сразу в разговор."""
        from core import dropped_file
        progress = getattr(self.ui, "file_progress", None) or (lambda *_a: None)
        name = os.path.basename(path)
        progress(name, 0.15, "Читаю")
        try:
            f = dropped_file.prepare(path)
        except dropped_file.Unsupported as exc:
            logger.info("Файл с капсулы не прочитан: %s — %s", name, exc)
            progress(name, -1.0, str(exc))
            return
        except Exception as exc:
            logger.warning("Файл с капсулы: %s", exc, exc_info=True)
            progress(name, -1.0, "не получилось прочитать")
            return
        self._dropped = f
        ready = getattr(self.ui, "file_ready", None)
        if not (ready and ready(f.name)):
            self._send_prepared_file(f)

    def _on_island_file_action(self, action: str, question: str = ""):
        """Кнопка на капсуле: «summary» — кратко, «ask» — вопрос из чата, «cancel»."""
        f, self._dropped = getattr(self, "_dropped", None), None
        if action == "cancel" or f is None:
            return
        threading.Thread(target=self._send_prepared_file, args=(f, question, action == "summary"),
                         daemon=True, name="drop-file").start()

    def _on_island_mic(self):
        """«Сказать голосом» из чата в капсуле — как F8, но окно не разворачиваем."""
        if self.ui.muted:
            self.ui.toggle_mute()
        self.wake()

    def _send_prepared_file(self, f, question: str = "", summary: bool = False):
        """Прочитанный файл — в разговор; ответ придёт голосом и в чат капсулы."""
        from core import dropped_file
        progress = getattr(self.ui, "file_progress", None) or (lambda *_a: None)
        name = f.name
        if not self._loop or not self.session or not self._loop.is_running():
            progress(name, -1.0, "нет связи с Gemini")
            return
        progress(name, 0.6, "Отправляю")
        parts = [types.Part.from_text(text=dropped_file.instruction(f, question, summary))]
        if f.kind == "image":
            parts.append(types.Part.from_bytes(data=f.data, mime_type=f.mime))
        else:
            parts.append(types.Part.from_text(text=f"Содержимое «{f.name}»:\n{f.text}"))
        self.wake()
        self.last_user_text = question or f"[файл {name}]"
        self._user_turn += 1
        self._typed_turn = self._user_turn
        self.ui.write_log(f"Вы: 📎 {name}" + (f" — {question}" if question else ""))
        fut = asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(turns=[types.Content(role="user", parts=parts)], turn_complete=True),
            self._loop)
        try:
            fut.result(timeout=30)
        except Exception as exc:
            logger.warning("Файл %s не ушёл в Gemini: %s", name, exc)
            progress(name, -1.0, "не отправился — повторите")
            return
        progress(name, 1.0, "Отправил")

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
        if self._is_speaking:
            # Перебили по имени: замолкаем сразу, недоговорённое выбрасываем.
            logger.info("Перебили словом «Джарвис» — замолкаю")
            self._drop_pending_speech()
            self.set_speaking(False)
        self.wake()
        # Позвали под музыку — дальше речь должна доходить и поверх неё.
        self._open_through(_CORRECTION_SEC)
        while self._wake_ring:
            put({"data": self._wake_ring.popleft(), "mime_type": "audio/pcm"})

    def _open_through(self, sec: float):
        """Окно поправки: музыка приглушена, микрофон пропускает речь поверх неё."""
        self._through_until = max(self._through_until, time.monotonic() + sec)
        try:
            from core.ducking_controller import ducking_controller
            ducking_controller.duck("окно поправки / позвали по имени")
        except Exception:
            pass
        try:
            asyncio.get_running_loop().call_later(sec + 0.1, self._close_through)
        except RuntimeError:
            pass                                  # не из событийного цикла (тесты) — закроется по времени

    def _close_through(self):
        if time.monotonic() >= self._through_until and not self._is_speaking:
            self._release_ducking()

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
            # Кроме окна поправки: его закроет _close_through.
            if time.monotonic() >= getattr(self, "_through_until", 0.0) or self.ui.muted:
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
        self._quick_lift = True     # это Джарвис должен сказать — не глушить
        self.wake()
        self._send_text_to_session(text)

    def _remember_turn(self, user: str, jarvis: str):
        """Реплики — в журнал разговора (memory/conversation.py). В потоке:
        этот цикл гонит звук, а журнал изредка подрезается на диске."""
        def write():
            from memory.conversation import log_turn
            try:
                log_turn("user", user)
                log_turn("jarvis", jarvis)
            except Exception as exc:
                logger.warning("Журнал разговора: %s", exc)
        threading.Thread(target=write, daemon=True).start()

    # Служебное и сама память — не «действия», в журнал не пишем.
    _NOT_ACTIONS = {"save_to_memory", "recall_memory", "forget_memory", "set_mode", "switch_voice"}

    def _remember_action(self, name: str, args: dict, response):
        """Что Джарвис сделал — в журнал разговора (и в общую память с ботом)."""
        if name in self._NOT_ACTIONS:
            return
        result = str((response or {}).get("result", "") if isinstance(response, dict) else response or "")
        if result.startswith("НЕ ВЫПОЛНЕНО"):
            return                                   # ждёт «да» — это ещё не действие
        outcome = for_speech(result)[:160]
        text = _tool_human(name) + (f" — {outcome}" if outcome else "")
        try:
            from ui_island import tool_label
            text = tool_label(name, args) + (f" — {outcome}" if outcome else "")
        except Exception:
            pass

        def write():
            try:
                from memory.conversation import log_turn
                log_turn("action", text)
            except Exception as exc:
                logger.debug("Журнал действий: %s", exc)
        threading.Thread(target=write, daemon=True).start()

    def _call_finished(self, result: str):
        """Итог звонка — в журнал, не голосом: звонок мог быть в 6 утра."""
        self.ui.write_log(f"SYS: 📞 {result}")

    # ── Конфигурация Gemini ───────────────────────────────────────────────────
    def _asr_config(self) -> dict:
        """Расшифровка речи: русский, узбекский, казахский и само имя «Джарвис».

        Без подсказок модель писала имя как «ჯარის» или терялось («, привет»),
        и Джарвис решал, что обращались не к нему. Подсказки — новые поля
        Live API; сессия их не приняла — run() отключает их (_asr_hints)."""
        if not getattr(self, "_asr_hints", False):
            return {}
        return {"language_codes": ["ru-RU", "uz-UZ", "kk-KZ", "en-US"],
                "custom_vocabulary": ["Джарвис", "Jarvis", "сэр"]}

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
        # Итоги прошлых разговоров, а если сессию не удалось возобновить
        # (или это первый запуск) — ещё и хвост разговора: без него Джарвис
        # после разрыва начинал с чистого листа.
        try:
            from memory.conversation import prompt_context
            conv = prompt_context(resuming=bool(self._resume_handle))
            if conv:
                parts.append(conv)
        except Exception as exc:
            logger.warning("Контекст разговора не загрузился: %s", exc)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription=self._asr_config(),
            system_instruction="\n".join(parts),
            # Встроенный Google Search: «кто выиграл», «курс доллара», новости —
            # модель ищет сама и отвечает по свежим данным. Сессия с ним не
            # подключается — run() отключает его и пробует без (_grounding).
            tools=([{"google_search": {}}] if getattr(self, "_grounding", False) else [])
                  + [{"function_declarations": TOOLS}],
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

        if name == "clock" and _action_of(args) == "alarm_set" \
                and not _alarm_requested(getattr(self, "_heard_now", ""), getattr(self, "last_user_text", "")):
            logger.warning("Будильник без просьбы не ставлю: %s", args)
            return types.FunctionResponse(id=fc.id, name=name, response={"result": (
                "НЕ ВЫПОЛНЕНО: пользователь не просил будильник. Не ставь будильники сам — "
                "только когда он прямо скажет «разбуди» или «поставь будильник».")})

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
                self._pending_args = dict(args)
                question = _confirm_question(name, args)
                logger.warning("Требую подтверждения: %s/%s", name, _action_of(args))
                self.ui.write_log(f"SYS: жду подтверждения — {question or name + '/' + _action_of(args)}")
                ask = getattr(self.ui, "ask_confirm", None)
                if ask:
                    ask(_confirm_label(name, args))
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": (
                        "НЕ ВЫПОЛНЕНО — нужно подтверждение. Переспроси пользователя вслух"
                        + (f" дословно: «{question}»" if question else ", точно ли он хочет это сделать")
                        + ", и вызови инструмент повторно ТОЛЬКО если он ответит утвердительно."
                    )},
                )
            if name == "contacts":
                args = dict(getattr(self, "_pending_args", None) or args)   # ровно то, что подтвердили
            done = getattr(self.ui, "confirm_done", None)
            if done:
                done()
            denied = await self._voice_denied(name, args)
            if denied:
                self._pending_destructive = None
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return types.FunctionResponse(id=fc.id, name=name, response={"result": denied})
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
                from actions import vision
                source = "camera" if name == "look_at_camera" else args.get("source", "screen")
                args["source"] = source
                grab = (vision.capture_camera_jpeg if source == "camera"
                        else vision.capture_active_window_jpeg if source == "active_window"
                        else vision.capture_screen_jpeg)
                jpeg = await loop.run_in_executor(None, grab)
                # Кадр — и в саму голосовую сессию: тогда на «а что справа?»
                # модель отвечает по картинке, а не по пересказу. Точный разбор
                # (мелкий текст, код) делает отдельный запрос ниже.
                if jpeg and self.session is not None:
                    try:
                        await self.session.send_realtime_input(
                            video=types.Blob(data=jpeg, mime_type="image/jpeg"))
                    except Exception as exc:
                        logger.debug("Кадр в Live-сессию не ушёл: %s", exc)
                r = await loop.run_in_executor(
                    None, lambda: vision.analyze_vision(args.get("prompt", ""), source, jpeg))
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

            # ── YouTube и управление видео в окне Джарвиса ───────────
            elif name == "youtube_player":
                from actions import video_player
                r = await loop.run_in_executor(None, lambda: video_player.play_youtube(
                    args.get("query", ""), args.get("channel", "") if args.get("action") == "latest" else "",
                    self.ui))
                result = r or "Готово."

            elif name == "video_control":
                from actions import video_player
                r = await loop.run_in_executor(
                    None, lambda: video_player.control(args.get("action", ""), args.get("value")))
                if not r:
                    # Видео нет — может, речь про музыку («пауза»).
                    r = await loop.run_in_executor(
                        None, lambda: spotify_player(parameters=args, player=self.ui)) \
                        if args.get("action") in ("pause", "resume", "volume_up", "volume_down", "volume_set") \
                        else "Сейчас в окне Джарвиса ничего не идёт, сэр."
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
                        last = history[0].get("translation") or ""
                        result = f"В истории переводов {len(history)}" + (f". Последний: «{last}»" if last else "")
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
                from core.briefing import briefing_tool
                result = await asyncio.to_thread(briefing_tool, args)

            # ── Инструмент: умный таймер сна ──────────────────────────────
            elif name == "clock":
                from core.clock import clock_tool
                result = await asyncio.to_thread(clock_tool, args)

            elif name == "eyes":
                from core.eyes import eyes_tool
                result = await asyncio.to_thread(eyes_tool, args)

            elif name == "app_window":
                which = str(args.get("window", "")).lower()
                titles = {"keys": ("open_keys", "Ключи и подключения"), "commands": ("open_macros", "Свои команды"),
                          "contacts": ("open_contacts", "Контакты"), "about": ("open_about", "Обо мне"),
                          "study": ("open_study", "Учёба"), "help": ("open_welcome", "Что умеет Джарвис"),
                          "backup": ("open_backup", "Резервная копия"),
                          "football": ("open_football", "Футбол"),
                          "settings": ("open_settings", "Настройки")}
                method, title = titles.get(which, titles["commands"])
                opener = getattr(self.ui, method, None)
                if opener:
                    opener()
                    result = f"Открыл окно «{title}»."
                else:
                    result = "Окна тут нет — запущен без интерфейса."

            elif name == "remember_screen":
                from core.remember import remember_screen
                result = await asyncio.to_thread(remember_screen, args)

            elif name == "football":
                from core.football import football_tool
                result = await asyncio.to_thread(football_tool, args)

            elif name == "study":
                from core.study import study_tool
                result = await asyncio.to_thread(study_tool, args)

            elif name == "about_me":
                from core.about_me import about_tool
                result = await asyncio.to_thread(about_tool, args)

            elif name == "contacts":
                from core.contacts import contacts_tool
                who = str(args.get("name", ""))
                result = await asyncio.to_thread(contacts_tool, args, lambda r, w=who: self.speak(
                    f"[СИСТЕМА: звонок ({w}) закончился: {r} Коротко перескажи пользователю, что ответили.]"))

            elif name == "macro":
                if str(args.get("action", "")).lower() == "editor":
                    opener = getattr(self.ui, "open_macros", None)
                    if opener:
                        opener()
                    result = "Открыл окно «Свои команды»." if opener else "Окна команд тут нет."
                else:
                    from core.macros import macro_tool
                    result = await asyncio.to_thread(macro_tool, args)

            elif name == "location":
                from core.location import location_tool
                result = await asyncio.to_thread(location_tool, args)

            elif name == "sleep_timer":
                from actions.sleep_timer import sleep_timer
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: sleep_timer(args, player=self.ui, bot=self)
                )

            # ── Инструмент: память ────────────────────────────────────────
            elif name == "recall_memory":
                from memory.conversation import recall
                result = await asyncio.to_thread(recall, args.get("query", ""))

            elif name == "forget_memory":
                from memory.conversation import forget_about
                result = await asyncio.to_thread(forget_about, args.get("query", ""))

            # ── Инструмент: перерывы и звонки ─────────────────────────────
            elif name == "break_reminder":
                from core.break_reminder import break_reminder
                result = break_reminder(args)

            elif name == "phone_call":
                from core import tg_call
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: tg_call.phone_call(args, done=self._call_finished)
                )

            # ── Инструмент: переключение голоса ───────────────────────────
            elif name == "switch_voice":
                provider = args.get("provider", "fish").strip().lower()
                if provider not in ("fish", "gemini"):
                    provider = "fish"
                set_voice_provider(provider)
                rus_name = "голос Джарвиса из фильмов" if provider == "fish" else "быстрый встроенный голос"
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
            # Модели — причина человеческими словами, не текст исключения: она
            # пересказывала его вслух («ConnectionResetError, WinError…»).
            # Подробности — в журнал (logger), не в чат.
            result = f"Ошибка: {short_reason(e)}. Скажи сэру по-человечески, без технических деталей."
            logger.exception("Инструмент %s упал", name)
            self.ui.write_log(f"ERR: {_tool_human(name)} — не получилось ({short_reason(e)})")

        # Профиль и прогнозы пишутся на диск — в поток, и их сбой не должен
        # рвать сессию: без ответа на вызов модель ждёт его и после реконнекта.
        try:
            await asyncio.to_thread(self._remember_tool_use, name, args)
        except Exception as exc:
            logger.debug("Профиль не обновлён: %s", exc, exc_info=True)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        # В журнал, а не только в консоль: у собранного .exe консоли нет, и
        # «музыку не поставил» по журналу было не разобрать — вызов виден,
        # а что инструмент ответил, нет.
        logger.info("📤 %s → %s", name, str(result).replace("\n", " ")[:200])
        # Сам глянуть на экран, вышло ли (core/verify.py) — в фоне, ответ не ждёт.
        try:
            from core.verify import verifier
            verifier().after(name, args, result)
        except Exception as exc:
            logger.debug("Проверка не запущена: %s", exc)
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

    async def _start_speaker_meter(self):
        """Замер громкости колонок (для «Не слушать, пока играет звук»)."""
        if self._speaker_meter is not None:
            return
        from speaker_meter import SpeakerMeter
        meter = SpeakerMeter()
        # start() ждёт рабочий поток до 5 с — не в событийном цикле:
        # иначе на это время вставали бы голос и связь с Gemini.
        if await asyncio.to_thread(meter.start):
            self._speaker_meter = meter
            logger.info("Speaker meter started successfully (loopback active)")
        else:
            logger.info("Speaker meter unavailable, capturing all audio")

    def _put_frame(self, item):
        try:
            self.out_queue.put_nowait(item)
        except asyncio.QueueFull:
            pass  # Drop audio frame silently to avoid flooding event loop

    async def _start_local_wake(self):
        """Офлайн-детектор имени (после обучения на голосе владельца)."""
        if self._local_wake is None and _WAKE_MODE == "wake_word" and not _local_wake_enabled():
            self._local_wake = False         # имя ищется в расшифровке Gemini
        if self._local_wake is None and _WAKE_MODE == "wake_word":
            loop = asyncio.get_running_loop()

            def _heard(text: str):
                loop.call_soon_threadsafe(self._on_local_wake, self._put_frame)
            wake = None
            # Сначала Porcupine (слово «Джарвис», обученное в консоли Picovoice):
            # Vosk-small-RU этого слова не знает вовсе.
            try:
                from core.wake_porcupine import PorcupineWake, configured
                if configured():
                    pw = PorcupineWake(_heard)
                    if await asyncio.to_thread(pw.start):
                        wake = pw
            except Exception as exc:
                logger.warning("Porcupine: %s", exc)
            if wake is not None:
                self._local_wake = wake
                self.ui.write_log("SYS: слово «Джарвис» слушается на компьютере (Porcupine)")
                return
            # Без ключей: sherpa-onnx знает «Джарвис» текстом. Модели ещё нет —
            # пока работает Vosk, модель качается в фоне, потом переключимся.
            try:
                from core import wake_kws
                if wake_kws.available():
                    if wake_kws.find_model() is not None:
                        kw = wake_kws.KwsWake(_heard)
                        if await asyncio.to_thread(kw.start):
                            self._local_wake = kw
                            self.ui.write_log("SYS: слово «Джарвис» слушается на компьютере (sherpa-onnx)")
                            return
                    elif not getattr(self, "_kws_downloading", False):
                        self._kws_downloading = True
                        threading.Thread(target=self._download_kws, daemon=True, name="kws-download").start()
            except Exception as exc:
                logger.warning("Детектор слова sherpa-onnx: %s", exc)
            wake = LocalWake(_heard)
            if await asyncio.to_thread(wake.start):
                self._local_wake = wake
                self.ui.write_log("SYS: слово «Джарвис» слушается на компьютере — до него звук никуда не уходит")
            else:
                self._local_wake = False     # не пробовать при каждом переподключении
                self.ui.write_log("SYS: нет модели слова «Джарвис» — слушаю через Gemini")

    def _download_kws(self):
        """Скачать модель «Джарвис» (5 МБ) и перейти на неё с Vosk."""
        try:
            from core import wake_kws
            wake_kws.download()
        except Exception as exc:
            logger.warning("Модель слова «Джарвис» не скачалась: %s — остаюсь на Vosk", exc)
            return
        finally:
            self._kws_downloading = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._switch_to_kws(), self._loop)

    async def _switch_to_kws(self):
        old = self._local_wake
        if old and not isinstance(old, LocalWake):
            return                                 # уже Porcupine или sherpa
        if old:
            old.stop()
        self._local_wake = None
        await self._start_local_wake()

    def _on_wake_trained(self):
        """Обучили слову «Джарвис» — включить детектор сразу. Раньше флаг «нет
        детектора» стоял до перезапуска, и обучение не действовало."""
        if self._local_wake:
            return
        self._local_wake = None
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._start_local_wake(), self._loop)

    async def _listen_audio(self):
        print("[ДЖАРВИС] 🎤 Микрофон запущен")
        loop = asyncio.get_event_loop()

        if self._speaker_meter is None and _IGNORE_SPEAKERS:
            await self._start_speaker_meter()

        def _put_nowait_safe(item):
            try:
                self.out_queue.put_nowait(item)
            except asyncio.QueueFull:
                pass  # Drop audio frame silently to avoid flooding event loop

        await self._start_local_wake()

        preroll = collections.deque(maxlen=10)

        def callback(indata, frames, time_info, status):
            self._mic_last_cb = time.monotonic()
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if jarvis_speaking or time.monotonic() < self._echo_guard_until:
                if not getattr(self, "_mic_hears_speakers", True) and not self.ui.muted:
                    # Наушники: свой голос Джарвиса микрофон не слышит — можно
                    # перебивать обычной речью, Gemini сам оборвёт ответ.
                    pass
                else:
                    # Колонки: перебить можно словом «Джарвис» — его слышит
                    # локальный детектор, а свой голос Джарвис по имени не зовёт.
                    if self._local_wake and jarvis_speaking and not self.ui.muted:
                        self._wake_ring.append(indata.tobytes())
                        self._local_wake.feed(indata.tobytes())
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
            # Играет музыка / фильм из своих колонок. Раньше при громких колонках
            # микрофон глушился целиком, а стоило их приглушить — музыка шла в
            # Gemini как «речь» и снова приглушала себя (ползунки прыгали сами).
            # Теперь кадр сравнивается с тем, что сейчас играет (core/echo_gate):
            # голос заметно громче эха — пропускаем, эхо — нет.
            meter = self._speaker_meter
            level = float(getattr(meter, "recent", getattr(meter, "peak", 0.0))) if meter is not None else 0.0
            # _IGNORE_SPEAKERS — живая настройка «Не слушать, пока играет звук»:
            # раньше её читали только при запуске, и выключатель ничего не менял.
            music = (_IGNORE_SPEAKERS and meter is not None and getattr(self, "_mic_hears_speakers", True)
                     and self._echo.music(level))
            if music:
                rms = _frame_rms(indata)
                self._echo.observe(rms, level)
                now = time.monotonic()
                through = now < self._through_until
                if self._echo.coupling() is None:
                    # Связь «колонки → микрофон» ещё не выучена: как раньше —
                    # только в окне поправки и только заметно громче порога.
                    voice = through and rms >= MIC_RMS_THRESHOLD * 2
                else:
                    voice = self._echo.is_voice(rms, level, MIC_RMS_THRESHOLD)
                if voice:
                    if now - self._voice_over_music_at > _VOICE_OVER_MUSIC_HOLD:
                        logger.info("Голос поверх музыки (уровень %.2f, RMS %.0f) — приглушаю и слушаю",
                                    level, rms)
                    self._voice_over_music_at = now
                    if not through:
                        loop.call_soon_threadsafe(self._open_through, 3.0)
                elif now - self._voice_over_music_at > _VOICE_OVER_MUSIC_HOLD:
                    # Музыка, не голос. Имя слушаем всегда: раньше в открытом
                    # окне разговора под музыкой не было слышно и «Джарвис».
                    if self._local_wake:
                        self._wake_ring.append(pcm_bytes)
                        self._local_wake.feed(pcm_bytes)
                    self._note_gate(f"играет звук {level:.2f} — это не голос")
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
                # Речь — и в копилку для проверки голоса (core/voice_id.py):
                # на подтверждении опасного сверяется просьба + «да».
                self._voice_ring.append((time.monotonic(), pcm_bytes))

            # Начало речи: приглушаем музыку — только если она правда играет.
            # Раньше приглушение шло на любой звук, и ползунки всех программ
            # в микшере прыгали от каждого слова и шороха.
            if was_silent and music:
                try:
                    from core.ducking_controller import ducking_controller
                    ducking_controller.duck("голос поверх музыки")
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
                self._mic_hears_speakers = await asyncio.to_thread(_mic_hears_speakers, device)
                if not self._mic_hears_speakers:
                    logger.info("Микрофон не слышит динамики — при музыке и фильмах он не глушится")
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
                        if getattr(self, "_mic_reopen", False):   # выбрали другой микрофон в «Настройках»
                            self._mic_reopen = False
                            logger.info("Микрофон сменён в настройках — переоткрываю")
                            break
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
        for chunk in _split_for_speech(for_speech(text)):
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
            from core import speech_pace
            from telegram_bot import tts_fish
            from telegram_bot import tts_edge

            trimmed = [0]                  # сколько тишины срезано за ответ (журнал)
            # Жив ли Fish — решается один раз: после первого отказа остаток
            # ответа договаривает Edge, а не ждёт таймаута на каждом куске.
            # Куски синтезируются параллельно, поэтому первый запрос к Fish —
            # пробный: остальные ждут его вердикта, а не бьются в мёртвый
            # сервис каждый со своим таймаутом.
            fish_alive = tts_fish.is_configured()
            probe: asyncio.Future | None = None
            if fish_alive and time.monotonic() - getattr(self, "_fish_ok_at", -1e9) < _FISH_TRUST_SEC:
                probe = asyncio.get_running_loop().create_future()      # проба не нужна — Fish жив
                probe.set_result(None)

            cache = quick.voice_cache()

            async def fish_stream(fragment: str, emit) -> bytes | None:
                """Кусок голосом Fish ПОТОКОМ: звук уходит в динамики по мере
                синтеза, не дожидаясь конца. → весь звук (для кэша) или None —
                Fish не ответил, пусть договорит Edge."""
                nonlocal fish_alive, probe
                if fish_alive and probe is not None:
                    await probe
                if not fish_alive:
                    return None
                first = probe is None
                if first:
                    probe = asyncio.get_running_loop().create_future()
                raw = bytearray()
                alive = complete = False
                try:
                    async for chunk in tts_fish.stream_pcm(fragment, sample_rate=RECV_SAMPLE_RATE):
                        raw += chunk
                        if not alive and len(raw) >= 500:       # настоящий звук, а не обрывок ошибки
                            alive = True
                            self._fish_ok_at = time.monotonic()
                            if first and not probe.done():
                                probe.set_result(None)
                            emit(bytes(raw))
                        elif alive:
                            emit(chunk)
                    complete = True
                except Exception as exc:
                    logger.warning("Fish поток: %s: %s", type(exc).__name__, exc)
                finally:
                    if not alive:
                        if fish_alive:
                            self._fish_ok_at = -1e9
                            fish_alive = False
                            self.ui.write_log("SYS: Fish молчит — остаток ответа озвучит Edge-TTS")
                        if first and not probe.done():
                            probe.set_result(None)
                if not alive:
                    return None
                return bytes(raw) if complete else b""

            async def produce(fragment: str, out: asyncio.Queue):
                """Звук одного куска в out (None — кусок кончился). Тишина по краям
                срезается на лету (speech_pace), после куска — своя короткая пауза."""
                pace = speech_pace.Tightener(RECV_SAMPLE_RATE)

                def emit(pcm: bytes):
                    body = pace.feed(pcm)
                    if body:
                        out.put_nowait(body)
                try:
                    # Готовая фраза («Есть, сэр.») уже озвучена — звучит сразу.
                    pcm = cache.get(fragment, RECV_SAMPLE_RATE)
                    if pcm:
                        emit(pcm)
                    else:
                        whole = await fish_stream(fragment, emit)
                        if whole is None:
                            pcm = await tts_edge.speak_pcm(fragment, sample_rate=RECV_SAMPLE_RATE)
                            if pcm:
                                emit(pcm)
                        elif whole and cache.wanted(fragment):
                            # Кэш — только своим голосом и только целиком: Edge и обрывки не пишем.
                            cache.put(fragment, RECV_SAMPLE_RATE, whole)
                    if pace.started:
                        out.put_nowait(pace.finish(speech_pace.ends_sentence(fragment)))
                finally:
                    trimmed[0] += pace.dropped * 2
                    out.put_nowait(None)

            ordered: asyncio.Queue = asyncio.Queue()
            producers: list[asyncio.Task] = []

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
                    out: asyncio.Queue = asyncio.Queue()
                    producers.append(asyncio.create_task(produce(fragment, out)))
                    ordered.put_nowait(out)
                ordered.put_nowait(None)

            feeder = asyncio.create_task(feed())
            step = CHUNK_SIZE * 2
            spoken = 0
            try:
                while True:
                    out = await ordered.get()
                    if out is None:
                        break
                    played = 0
                    while True:
                        pcm = await out.get()
                        if pcm is None:
                            break
                        if not spoken:
                            self._latency.mark_answer_audio()
                        spoken += len(pcm)
                        played += len(pcm)
                        for j in range(0, len(pcm), step):
                            try:
                                self.audio_in_queue.put_nowait(pcm[j:j + step])
                            except asyncio.QueueFull:
                                await self.audio_in_queue.put(pcm[j:j + step])
                    if not played:
                        self.ui.write_log("SYS: синтез речи недоступен — ответ остался текстом")
            finally:
                feeder.cancel()
                for t in producers:
                    t.cancel()

            if spoken:
                logger.info("Голос Fish: %.1f с звука, срезано тишины %.1f с", spoken / 2 / RECV_SAMPLE_RATE,
                            trimmed[0] / 2 / RECV_SAMPLE_RATE)
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
            _step = getattr(self.ui, "tool_started", None)
            if _step:
                _step(fc.name, dict(fc.args or {}))
            try:
                # Инструмент без ответа (Spotify не отвечает, браузерный вход в
                # Google) держал весь приём: ни звука, ни реакции — «завис».
                fr = await asyncio.wait_for(self._execute_tool(fc), _TOOL_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                logger.error("Инструмент %s не ответил за %.0f с", fc.name, _TOOL_TIMEOUT_SEC)
                self.ui.write_log(f"ERR: {_tool_human(fc.name)} — нет ответа {_TOOL_TIMEOUT_SEC:.0f} с")
                fr = types.FunctionResponse(id=fc.id, name=fc.name, response={
                    "result": f"Не успело выполниться за {_TOOL_TIMEOUT_SEC:.0f} секунд."})
            except Exception as exc:
                # Сбой инструмента не должен рвать сессию: без ответа на
                # вызов модель так и ждёт его после переподключения.
                logger.exception("Инструмент %s упал", fc.name)
                fr = types.FunctionResponse(id=fc.id, name=fc.name,
                                            response={"result": f"Ошибка: {short_reason(exc)}."})
            finally:
                # Медленный инструмент — самая частая причина
                # паузы, которую слышно как «завис».
                self._latency.add_tool(
                    fc.name,
                    int((time.perf_counter() - _tool_started) * 1000),
                )
            _end = getattr(self.ui, "tool_finished", None)
            if _end:
                _end(fc.name, _tool_outcome(getattr(fr, "response", None)))
            self._remember_action(fc.name, dict(fc.args or {}), getattr(fr, "response", None))
            responses.append(fr)
            self._show_card(fc, fr)
            if fc.name in _MEDIA_TOOLS and str((fc.args or {}).get("action", "play")).lower() in (
                    "play", "mood", "search", "latest", "open", ""):
                # Только что включили — даём поправить: «нет-нет, не её, а …».
                self._open_through(_CORRECTION_SEC)
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

    async def _quick_run(self, q, heard: str, echo: bool = True):
        """Мгновенная команда: выполнить на ПК и сразу ответить готовой
        фразой (или ответом инструмента, если не вышло) — без круга через
        Gemini (core/quick.py)."""
        started = time.perf_counter()
        result = ""
        if q.tool:
            self._quick_seq = getattr(self, "_quick_seq", 0) + 1
            fc = SimpleNamespace(id=f"quick-{self._quick_seq}", name=q.tool, args=dict(q.args))
            _step = getattr(self.ui, "tool_started", None)
            if _step:
                _step(fc.name, dict(fc.args))
            ok = False
            try:
                fr = await asyncio.wait_for(self._execute_tool(fc), _TOOL_TIMEOUT_SEC)
                result = str((getattr(fr, "response", None) or {}).get("result", ""))
                ok = _tool_outcome(getattr(fr, "response", None))
                self._show_card(fc, fr)
            except Exception as exc:
                logger.warning("Мгновенная команда %s: %s", q.tool, exc)
                result = f"Ошибка: {short_reason(exc)}"
            _end = getattr(self.ui, "tool_finished", None)
            if _end:
                _end(fc.name, ok)
        text = quick.reply_for(q, result)
        silent = quick.silent(q, result)
        logger.info("⚡ Мгновенно: «%s» → %s %s → «%s»%s (%d мс)", heard[:80], q.tool or "-",
                    q.args, text, " [звуком]" if silent else "", int((time.perf_counter() - started) * 1000))
        if echo:                                  # набранное окно чата уже показало само
            self.ui.write_log(f"Вы: {heard}")
        self.ui.write_log(f"Джарвис: {'✓ ' + text if silent else text}")
        self._remember_turn(heard, text)
        if silent:
            self._play_done_sound()               # сделано — звук и галочка, без «Есть, сэр»
        elif text:
            await self._speak_fish(text)

    async def _reconnect_when_quiet(self, session, deadline: float, mid_turn):
        """GoAway: ждём, пока Джарвис договорит (и ход закончится), но не
        дольше срока сервера, — и закрываем сессию сами. run() переподключится
        с handle'ом возобновления, без строки «связь оборвалась» в чате.
        Закрываем именно ту сессию, что прислала GoAway: если сервер успел
        закрыть её сам и Джарвис уже переподключился, новую не трогаем."""
        while time.monotonic() < deadline and (mid_turn() or self._is_speaking) and self.session is session:
            await asyncio.sleep(0.1)
        if session is not None and self.session is session:
            try:
                await session.close()
            except Exception as exc:
                logger.debug("Закрытие сессии перед переподключением: %s", exc)

    def _play_done_sound(self):
        """Короткое «готово» тем же путём, что голос Джарвиса (в выбранный динамик)."""
        try:
            from core.sounds import done_pcm
            pcm = done_pcm(RECV_SAMPLE_RATE)
            step = CHUNK_SIZE * 2
            for j in range(0, len(pcm), step):
                self.audio_in_queue.put_nowait(pcm[j:j + step])
        except Exception as exc:
            logger.debug("Звук «готово»: %s", exc)

    async def _voice_denied(self, name: str, args: dict) -> str:
        """Опасное подтверждено — но ВАШИМ ли голосом? '' — да (или проверить
        нечем), иначе — что сказать. Набранное с клавиатуры — доверенное."""
        try:
            from core import voice_id
            if not voice_id.sensitive(name, args) or self._typed_turn == self._user_turn:
                return ""
            vid = voice_id.voice_id()
            if not vid.enrolled():
                return ""
            since = time.monotonic() - 30
            pcm = b"".join(p for t, p in list(self._voice_ring) if t >= since)
            ok, score = await asyncio.to_thread(vid.verify, pcm)
        except Exception as exc:
            logger.warning("Проверка голоса: %s", exc)
            return ""
        if ok is None:
            return ""                          # мало речи или нет модели — не мешаем
        logger.info("Голос владельца: %s (сходство %.2f, порог %.2f)", "да" if ok else "НЕТ", score, vid.threshold)
        if ok:
            return ""
        self.ui.write_log(f"SYS: 🔒 не узнал голос владельца ({score:.2f}) — {name}/{_action_of(args)} не выполнено")
        return ("НЕ ВЫПОЛНЕНО — голос не похож на голос владельца. Скажи: «Не узнаю ваш голос, сэр. "
                "Повторите ближе к микрофону или подтвердите, набрав «да» в окне Джарвиса». Сам не повторяй вызов.")

    def _maybe_briefing(self):
        """Первый разговор утром (после подъёма из «Обо мне», до полудня, раз в
        день) — после ответа на просьбу коротко рассказать, что сегодня."""
        if getattr(self, "_intro_offered", False):
            return                                      # сегодня знакомимся — хватит монологов
        try:
            from core import briefing
            if not briefing.due():
                return
            briefing.mark_done()
        except Exception as exc:
            logger.debug("Брифинг: %s", exc)
            return

        def run():
            try:
                facts = briefing.gather()
                logger.info("Утренний брифинг: %s", ", ".join(facts) or "фактов нет")
                self.speak(briefing.instruction(facts))
            except Exception as exc:
                logger.warning("Утренний брифинг: %s", exc)
        threading.Thread(target=run, daemon=True, name="briefing").start()

    def _run_tool_blocking(self, name: str, args: dict) -> str:
        """Инструмент Джарвиса из фонового потока (шаг своей команды)."""
        if not self._loop or not self._loop.is_running():
            return "Нет связи с Джарвисом."
        self._quick_seq = getattr(self, "_quick_seq", 0) + 1
        fc = SimpleNamespace(id=f"macro-{self._quick_seq}", name=name, args=dict(args or {}))
        fut = asyncio.run_coroutine_threadsafe(self._execute_tool(fc), self._loop)
        fr = fut.result(timeout=_TOOL_TIMEOUT_SEC + 5)
        return str((getattr(fr, "response", None) or {}).get("result", ""))

    async def _quick_absorb(self, function_calls, why: str):
        """Gemini тоже решил выполнить команду, которую Джарвис уже сделал сам:
        ответить ему «уже сделано», не выполняя второй раз."""
        await self.session.send_tool_response(function_responses=[
            types.FunctionResponse(id=fc.id, name=fc.name, response={"result": why})
            for fc in function_calls])

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

        fish_idle: asyncio.Task | None = None

        async def fish_idle_flush():
            # Расшифровка затихла на законченном предложении («Есть, сэр.»):
            # не ждать turn_complete ради короткой фразы ниже порога.
            await asyncio.sleep(_FISH_SENTENCE_IDLE_SEC)
            fish_flush(force=True)

        def fish_flush(final: bool = False, force: bool = False):
            nonlocal fish_text, fish_q, fish_idle
            if fish_idle is not None and not force:
                fish_idle.cancel()
                fish_idle = None
            if addressed and get_voice_provider() == "fish":
                chunks, fish_text = _take_speakable(fish_text, fish_q is None, final, force)
                # Ссылки, пути, коды, служебные метки — не вслух (core/speech_text).
                chunks = [c for c in (for_speech(x) for x in chunks) if c.strip()]
                if chunks and fish_q is None:
                    self._latency.mark("в озвучку")
                    self._drop_pending_speech()   # один голос за раз
                    fish_q = asyncio.Queue()
                    self._fish_task = self._spawn(self._fish_worker(fish_q))
                for chunk in chunks:
                    fish_q.put_nowait(chunk)
                if not final and not force and fish_text.strip() and _END_OF_SENTENCE.search(fish_text):
                    fish_idle = asyncio.create_task(fish_idle_flush())
            if final:
                fish_close()

        def decide() -> bool:
            return self.is_awake() or named.is_set()

        # Мгновенный ответ (core/quick.py): частую команду Джарвис выполнил и
        # озвучил сам — речь и вызовы Gemini на неё глушатся, пока человек не
        # заговорит снова (или _QUICK_HOLD_SEC после конца хода).
        quick_on = False
        quick_q = None              # что выполнили
        quick_after = False         # ход с мгновенным ответом уже завершён
        quick_until = 0.0
        quick_skip = None           # досказал фразу — глушение снято, но повтор той же команды не делать
        quick_timer: asyncio.Task | None = None
        answered = False            # Gemini уже отвечает на этот ход — не перебивать

        def quick_try() -> bool:
            """Если фраза целиком — частая команда, выполнить её сразу."""
            nonlocal quick_on, quick_q, quick_until, held, fish_text
            if quick_on:
                return True
            if answered or not _quick_allowed() or not decide():
                return False
            heard = "".join(in_buf)
            q = quick.match(heard)
            if not q:
                return False
            quick_on, quick_q = True, q
            quick_until = time.monotonic() + _QUICK_HOLD_SEC
            held, fish_text = [], ""
            fish_close()
            self._drop_pending_speech()          # начатый ответ Gemini — не доигрывать
            self._spawn(self._quick_run(q, _clean_dialog_text(heard)))
            return True

        async def quick_later():
            await asyncio.sleep(quick.QUIET_SEC)
            quick_try()

        def quick_lift(why: str):
            nonlocal quick_on, quick_q, quick_after
            if quick_on:
                logger.debug("Мгновенный ход: глушение снято (%s)", why)
            quick_on, quick_q, quick_after = False, None, False

        try:
            while True:
                async for response in self.session.receive():
                    if quick_on and (self._quick_lift or (quick_after and time.monotonic() > quick_until)):
                        quick_lift("system-реплика" if self._quick_lift else "время вышло")
                    self._quick_lift = False
                    upd = response.session_resumption_update
                    if upd and upd.resumable and upd.new_handle:
                        self._resume_handle = upd.new_handle

                    if response.go_away:
                        # Плановое: Google закрывает голосовую сессию примерно
                        # раз в 10 минут. Раньше уходили сразу — обрывая ответ
                        # на полуслове — и писали в чат «связь оборвалась».
                        # Теперь договариваем и переподключаемся молча, с тем
                        # же handle'ом: разговор продолжается.
                        if self._go_away_at is None:
                            left = _go_away_seconds(response.go_away.time_left)
                            self._go_away_at = time.monotonic()
                            logger.info("Gemini: плановое переподключение (GoAway, осталось %.0f с) — "
                                        "после ответа", left)
                            self._spawn(self._reconnect_when_quiet(
                                self.session, time.monotonic() + max(0.5, left - 1.5),
                                lambda: bool(in_buf or out_buf)))
                        continue

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
                            if quick_after:
                                quick_lift("новая реплика")
                            in_buf.append(txt)
                            self._heard_now = "".join(in_buf)
                            self._latency.mark_transcript()
                            if len(in_buf) == 1 and get_voice_provider() == "fish":
                                # Сэр ещё говорит — TLS до Fish уже открываем:
                                # к ответу соединение будет готово.
                                try:
                                    from telegram_bot import tts_fish
                                    tts_fish.prewarm()
                                except Exception as exc:
                                    logger.debug("Fish: прогрев: %s", exc)
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
                            if quick_on and not quick.match("".join(in_buf)):
                                # «Громче… и открой хром» — фраза шире команды:
                                # остальное решает Gemini, громкость второй раз не трогаем.
                                quick_skip = quick_q
                                quick_lift("фраза продолжилась")
                            elif not quick_on and _quick_allowed():
                                # Конец фразы — тишина в расшифровке (или начало ответа).
                                if quick_timer:
                                    quick_timer.cancel()
                                quick_timer = asyncio.create_task(quick_later())

                    if response.data and not quick_try():
                        answered = True
                        self._latency.mark("gemini-звук")
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
                        if (sc.output_transcription and sc.output_transcription.text
                                and not quick_try()):
                            answered = True
                            self._latency.mark("текст")
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
                            if quick_timer:
                                quick_timer.cancel()
                                quick_timer = None
                            # Gemini промолчал, а фраза — частая команда: выполнить.
                            quick_turn = quick_try()
                            if quick_turn:
                                quick_after = True
                                quick_until = time.monotonic() + _QUICK_HOLD_SEC
                            quick_skip = None
                            answered = False
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
                            self._heard_now = ""
                            was_addressed, addressed = addressed, None
                            named = asyncio.Event()

                            if not was_addressed:
                                if full_in:
                                    logger.info("Не ко мне, молчу: «%s»", full_in[:80])
                                self._release_ducking()
                                continue

                            if quick_turn:
                                # Реплику и ответ уже записал _quick_run.
                                if full_in:
                                    self.last_user_text = full_in
                                    self._user_turn += 1
                                    if not by_name:
                                        self._continue_conversation()
                                    self._maybe_briefing()
                                continue

                            if full_in:
                                print(f"[ДЖАРВИС] 🎤 Полная фраза: '{full_in}'")
                                self.ui.write_log(f"Вы: {full_in}")
                                self.last_user_text = full_in
                                self._user_turn += 1
                                self._confirm_heard(full_in)
                                asyncio.get_running_loop().run_in_executor(
                                    None, self._learn_from_phrase, full_in
                                )
                                # Продолжение разговора без имени продлевает окно
                                # лишь несколько раз подряд — иначе телевизор держал
                                # бы Джарвиса «проснувшимся» бесконечно.
                                if not by_name:
                                    self._continue_conversation()
                                self._maybe_briefing()

                            if full_out:
                                self.ui.write_log(f"Джарвис: {full_out}")
                            # В журнал — каждый принятый ход. «Не слышит» и «молчит»
                            # иначе неотличимы: «Не ко мне» журнал писал, а ход,
                            # принятый и оставшийся без ответа, не оставлял следа.
                            if full_in or full_out:
                                logger.info("Ход: «%s» → «%s»%s", full_in[:120], full_out[:120],
                                            "" if full_out else "  [ответа нет]")
                            self._remember_turn(full_in, full_out)

                    if response.tool_call:
                        if addressed is None:
                            addressed = decide()
                        calls = response.tool_call.function_calls
                        if quick_try():
                            await self._quick_absorb(calls, "Уже выполнено мгновенно, пользователь уже "
                                                     "услышал ответ. Ничего не говори.")
                            continue
                        if quick_skip is not None:
                            same = [fc for fc in calls if fc.name == quick_skip.tool
                                    and _action_of(dict(fc.args or {})) == quick_skip.args.get("action")]
                            if same:
                                await self._quick_absorb(same, "Это уже выполнено. Про это не говори.")
                                calls = [fc for fc in calls if fc not in same]
                            quick_skip = None
                            if not calls:
                                continue
                        answered = True
                        if addressed:
                            await self._run_tool_calls(calls, True)
                        else:
                            self._spawn(self._deferred_tool_calls(calls, named))

                if self._go_away_at is not None:          # сессию закрыли сами — после ответа
                    raise _PlannedReconnect("GoAway: ответ договорён")
        except Exception as e:
            if self._go_away_at is not None:
                logger.info("Плановое переподключение к Gemini (%s)", e)
                raise _PlannedReconnect(str(e)) from e
            logger.error("Приём оборвался: %s", e)
            # Пробрасываем наверх: TaskGroup свернётся, и сработает
            # реконнект в `run()` — с handle'ом возобновления, так что
            # разговор продолжится с того же места.
            self.ui.write_log("SYS: связь с Gemini оборвалась — переподключаюсь…")
            raise
        finally:
            fish_close()

    def _on_setting(self, key: str, value):
        """Изменили настройку на экране — применяем без перезапуска."""
        global MIC_RMS_THRESHOLD, _IGNORE_SPEAKERS, _WAKE_MODE, _AWAKE_SEC
        if key == "mic":
            self._mic_reopen = True
        elif key == "speaker":
            self._out_reopen = True
        elif key == "mic_threshold":
            MIC_RMS_THRESHOLD = float(value)
        elif key == "ignore_speakers":
            _IGNORE_SPEAKERS = bool(value)
            if _IGNORE_SPEAKERS and self._speaker_meter is None and self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._start_speaker_meter(), self._loop)
        elif key == "wake_mode":
            _WAKE_MODE = str(value or "wake_word")
        elif key == "awake_sec":
            _AWAKE_SEC = float(value)
        elif key == "voice" and value:
            set_voice_provider(str(value))
        logger.info("Настройка «%s» = %r применена", key, value)

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

        # Динамик, выбранный в «Настройках» (по имени: номера в Windows меняются).
        want = os.getenv("JARVIS_OUTPUT_DEVICE", "").strip()
        if want:
            from core.settings import find_device
            idx = find_device(want, "output")
            if idx is None:
                logger.warning("Динамик «%s» не найден — беру системный", want)
            else:
                try:
                    return _try(idx)
                except Exception as exc:
                    logger.warning("Динамик «%s» не играет: %s — беру системный", want, exc)

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
                            if getattr(self, "_out_reopen", False):   # выбрали другой динамик — между фразами
                                self._out_reopen = False
                                logger.info("Динамик сменён в настройках — переоткрываю")
                                break
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
                self.ui.write_log("SYS: звук сбился — перезапускаю воспроизведение")
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
        self._grounding = os.getenv("JARVIS_GOOGLE_SEARCH", "1") != "0"
        self._asr_hints = os.getenv("JARVIS_ASR_HINTS", "1") != "0"

        while True:
            connected = 0.0
            low = ""
            try:
                print("[ДЖАРВИС] 🔌 Подключение к Gemini...")
                self.ui.set_state("RECONNECTING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    connected = time.monotonic()
                    self._go_away_at = None
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

                    # Первое знакомство: пока Джарвис почти ничего о владельце не
                    # знает — предложить (не чаще раза в день, не больше 3 раз,
                    # никогда после «не надо»; core/about_me.py). В этот раз —
                    # без брифинга: два монолога подряд — перебор.
                    offer_intro = False
                    if getattr(self, "_intro_checked", False) is False:
                        self._intro_checked = True
                        try:
                            from core import about_me
                            offer_intro = await asyncio.to_thread(about_me.should_offer)
                            if offer_intro:
                                about_me.mark_offered()

                                async def _intro():
                                    await asyncio.sleep(2.0)
                                    self.speak(about_me.intro_instruction())
                                tg.create_task(_intro())
                        except Exception as exc:
                            logger.debug("Знакомство: %s", exc)

                    # Утренний брифинг — не здесь (при каждом подключении), а в
                    # первом разговоре утра: см. _maybe_briefing / core/briefing.py.
                    if offer_intro:
                        self._intro_offered = True

            except asyncio.CancelledError:
                raise
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                reason = _root_error_text(e)
                low = reason.lower()
                if self._go_away_at is not None:
                    logger.info("Сессия Gemini закрыта по плану (GoAway) — переподключаюсь")
                else:
                    logger.error("Сессия Gemini оборвалась: %s", reason)
                logger.debug("Подробности разрыва", exc_info=True)

                if "1008" in low or "leaked" in low:
                    logger.error("Ключ Gemini заблокирован (1008) — создайте новый: "
                                 "https://aistudio.google.com/app/apikey")
                    self.ui.write_log("SYS: ❌ API-ключ Gemini заблокирован. Получите новый (aistudio.google.com) и впишите на экране «Ключи»")
                    break
                if any(k in low for k in ("api key not valid", "api key expired",
                                          "api_key_invalid", "invalid api key",
                                          "api key not found")):
                    logger.error("Ключ Gemini недействителен. Обновите его в %s", API_CONFIG)
                    self.ui.write_log("SYS: ❌ API-ключ Gemini недействителен. Обновите его на экране «Ключи»")
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
                # Сессия с поиском Google падает сразу — значит, модель его
                # не принимает: работаем без него, чем не работать вовсе.
                # Подсказки распознаванию (языки, слово «Джарвис») — новые поля
                # Live API: не примет — первыми отключаем их, а не поиск Google.
                if getattr(self, "_asr_hints", False) and (failures >= 2 or any(
                        k in low for k in ("transcription", "vocabulary", "language_code", "1007"))):
                    self._asr_hints = False
                    logger.warning("Подсказки распознаванию отключены: сессия с ними не подключается")
                    delay = 0.5
                elif self._grounding and (failures >= 3 or "search" in low or "tool" in low):
                    self._grounding = False
                    logger.warning("Встроенный поиск Google отключён: сессия с ним не подключается")
                    self.ui.write_log("SYS: встроенный поиск Google недоступен — ищу своим поиском")
                    delay = 0.5
                # Протухший handle возобновления сервер не примет — и цикл
                # бился бы в него бесконечно. Со второй неудачи — чистый старт.
                if failures >= 2:
                    self._resume_handle = None
                if failures == 3:
                    self.ui.write_log("SYS: нет связи с Gemini — продолжаю попытки…")
                if any(k in low for k in _QUOTA_WORDS):
                    self.ui.write_log(f"SYS: 😵 Слишком много запросов: Gemini просит паузу — "
                                      f"вернусь через {delay:.0f} с")
            logger.info("Переподключение через %.1f с", delay)
            if self._go_away_at is None:          # плановое — без «переподключаюсь» на экране
                self.ui.set_state("RECONNECTING")
            await asyncio.sleep(delay)


class _PlannedReconnect(ConnectionResetError):
    """Google сам попросил переподключиться (GoAway) — не сбой."""


def _go_away_seconds(time_left) -> float:
    """time_left из GoAway: «50s», «1.5s» или None → секунды (по умолчанию 10)."""
    try:
        return max(0.0, float(str(time_left).strip().rstrip("s")))
    except (TypeError, ValueError):
        return 10.0


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

    # Вход аккаунта Джарвиса для звонков в Telegram (один раз).
    if "--caller-login" in sys.argv:
        from core import tg_call
        sys.exit(tg_call.login_gui())

    # Обучить слово «Джарвис» на своём голосе (Vosk, один раз).
    if "--wake-calibrate" in sys.argv:
        from core import wake_calibrate
        try:
            device = _pick_input_device()
        except Exception:
            device = None
        sys.exit(wake_calibrate.run_gui(device))

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
        # Крестик закрывает программу целиком: вернуть громкость, снять хоткеи, погасить бота.
        ui.on_quit = [*getattr(ui, "on_quit", []), jarvis.cleanup]
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
