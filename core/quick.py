"""Мгновенные ответы: частые команды без раздумий облака.

Раньше каждое «пауза» шло полным кругом: Gemini дослушивает, думает, зовёт
инструмент, получает ответ, пишет фразу, Fish её синтезирует — 2–4 секунды на
то, что человек делает одной кнопкой. Теперь частые команды («пауза»,
«громче», «следующий трек», «который час», «таймер на 5 минут», «спасибо»)
узнаются прямо по расшифровке речи и выполняются на ПК сразу, а ответ звучит
из заранее озвученных фраз голосом Джарвиса — без сети и без ожидания.

- Узнаётся только фраза ЦЕЛИКОМ («громче» — да; «громче и открой хром» — нет,
  это уйдёт в Gemini как обычно): лучше отдать облаку, чем сделать не то.
- Команда не вышла («Spotify не запущен») — Джарвис говорит, что ответил
  инструмент, а не «Готово».
- Ответ, где нужны данные («который час», «что играет»), — это ответ самого
  инструмента, озвученный сразу, без круга через модель.
- Готовые фразы синтезируются один раз (voice_cache/) и дальше звучат
  мгновенно; у каждой команды несколько вариантов, чтобы не звучать роботом.
JARVIS_QUICK=0 — выключить.
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.wake_vosk import WAKE_RE

logger = logging.getLogger(__name__)

# Сколько тишины в расшифровке считать концом фразы (если ответ модели ещё
# не начался — он начинается, когда её VAD решил, что человек договорил).
QUIET_SEC = 0.45


def enabled() -> bool:
    return os.getenv("JARVIS_QUICK", "1").strip() != "0"


# ── Готовые фразы ─────────────────────────────────────────────────────────────
ACK = ("Есть, сэр.", "Сделано, сэр.", "Готово, сэр.")
PHRASES: dict[str, tuple[str, ...]] = {
    "ack": ACK,
    "pause": ("Пауза, сэр.", "Поставил на паузу, сэр."),
    "resume": ("Продолжаю, сэр.", "Играет дальше, сэр."),
    "next": ("Следующий трек, сэр.", "Переключаю, сэр."),
    "previous": ("Возвращаю предыдущий, сэр.", "Предыдущий трек, сэр."),
    "louder": ("Громче, сэр.", "Прибавил, сэр."),
    "quieter": ("Тише, сэр.", "Убавил, сэр."),
    "mute": ("Звук выключен, сэр.",),
    "unmute": ("Звук включён, сэр.",),
    "fullscreen": ("На весь экран, сэр.", "Разворачиваю, сэр."),
    "exit_fullscreen": ("Обычный экран, сэр.",),
    "desktop": ("Сворачиваю всё, сэр.", "Рабочий стол, сэр."),
    "eyes_open": ("Смотрю, сэр.", "Вижу экран, сэр."),
    "thanks": ("Всегда пожалуйста, сэр.", "Рад помочь, сэр.", "К вашим услугам, сэр."),
    "here": ("Здесь, сэр.", "Слушаю, сэр.", "На месте, сэр."),
}


def all_phrases() -> list[str]:
    return [p for variants in PHRASES.values() for p in variants]


# ── Разбор фразы ──────────────────────────────────────────────────────────────
@dataclass
class Quick:
    tool: str | None                 # None — ответ без действия («спасибо»)
    args: dict = field(default_factory=dict)
    reply: str = ""                  # ключ PHRASES; "" — озвучить ответ инструмента
    text: str = ""                   # что узнали (нормализованное)

    def phrase(self) -> str:
        return random.choice(PHRASES[self.reply]) if self.reply else ""


_FILLER = re.compile(r"\b(пожалуйста|сэр|ну|а|давай|слушай|эй|окей|ок|быстро|плиз)\b")


def normalize(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = re.sub(r"[^\w\s:%]", " ", t)
    t = " ".join(w for w in t.split() if not WAKE_RE.search(w))   # имя — целым словом
    t = _FILLER.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _level(v: str) -> str | None:
    from actions.computer_settings import parse_level
    return v if parse_level(v) is not None else None


def _duration(d: str) -> str | None:
    from core.clock import parse_duration
    return d if parse_duration({"duration": d}) else None


_MUSIC = r"(музык[аиуе]|spotify|спотифа[йя]|песн[июя]|трек[а]?)"
_VIDEO = r"(фильм[аe]?|видео|ролик[а]?)"
_NUM = r"(?P<v>(на |до )?[\w% ]{1,30})"

# (шаблон всей фразы, инструмент, аргументы, ключ ответа).
# Аргумент-строка вида "@v" берётся из группы шаблона и проверяется.
_RULES: list[tuple[str, str | None, dict, str]] = [
    # Пауза и продолжение: video_control сам уходит в Spotify, если видео нет.
    # Голое «стоп» — не здесь: им перебивают самого Джарвиса.
    (r"(поставь )?(на )?паузу|пауза|(стоп|останови) (" + _MUSIC + "|" + _VIDEO + ")|"
     r"(поставь )?(музыку|видео|фильм) на паузу", "video_control", {"action": "pause"}, "pause"),
    (r"продолж(и|ай)( играть| " + _MUSIC + "| " + _VIDEO + ")?|сними с паузы|играй дальше|"
     r"сними (музыку|видео|фильм) с паузы", "video_control", {"action": "resume"}, "resume"),
    (r"(включи |поставь )?следующ(ий|ая|ую|ее)( трек| песн[юя])?|переключи( трек| песню)?|"
     r"другую песню|дальше трек", "music_player", {"action": "next"}, "next"),
    (r"(включи |поставь )?предыдущ(ий|ая|ую|ее)( трек| песн[юя])?|верни (прошлую|предыдущую)( песню)?|"
     r"прошлую песню|прошлый трек", "music_player", {"action": "previous"}, "previous"),
    (r"что (сейчас )?(играет|за песня|за трек|за музыка)|кто (это )?поет|как называется (песня|трек)",
     "music_player", {"action": "now_playing"}, ""),
    # Громкость: музыка → Spotify, фильм → видео, без уточнения → система.
    (r"(сделай )?" + _MUSIC + r" (по)?громче|(по)?громче " + _MUSIC,
     "music_player", {"action": "volume_up"}, "louder"),
    (r"(сделай )?" + _MUSIC + r" (по)?тише|(по)?тише " + _MUSIC,
     "music_player", {"action": "volume_down"}, "quieter"),
    (r"громкость " + _MUSIC + " " + _NUM + r"|" + _MUSIC + r" на (?P<v2>[\w% ]{1,30})",
     "music_player", {"action": "volume_set", "value": "@v"}, "ack"),
    (r"(сделай )?" + _VIDEO + r" (по)?громче|(по)?громче " + _VIDEO,
     "video_control", {"action": "volume_up"}, "louder"),
    (r"(сделай )?" + _VIDEO + r" (по)?тише|(по)?тише " + _VIDEO,
     "video_control", {"action": "volume_down"}, "quieter"),
    (r"громкость " + _VIDEO + " " + _NUM,
     "video_control", {"action": "volume_set", "value": "@v"}, "ack"),
    (r"(сделай )?(по)?громче|прибавь( звук| громкость)?|громче", "computer_control",
     {"action": "volume_up"}, "louder"),
    (r"(сделай )?(по)?тише|убавь( звук| громкость)?|тише", "computer_control",
     {"action": "volume_down"}, "quieter"),
    (r"(громкость|звук|поставь громкость|сделай громкость) " + _NUM, "computer_control",
     {"action": "volume_set", "value": "@v"}, "ack"),
    (r"выключи звук|без звука|отключи звук", "computer_control", {"action": "mute"}, "mute"),
    (r"включи звук|верни звук", "computer_control", {"action": "unmute"}, "unmute"),
    # Экран.
    (r"(разверни |сделай )?(" + _VIDEO + " )?(на )?(весь|полный) экран", "video_control",
     {"action": "fullscreen"}, "fullscreen"),
    (r"выйди из полного экрана|обычный экран|сверни (видео|фильм)", "video_control",
     {"action": "exit_fullscreen"}, "exit_fullscreen"),
    (r"сверни все( окна)?|покажи рабочий стол|рабочий стол", "window_control",
     {"action": "minimize_all"}, "desktop"),
    # Часы.
    (r"(который|сколько) (сейчас )?(час|времени)|сколько время|какое (сегодня )?число|"
     r"какой (сегодня )?день", "clock", {"action": "now"}, ""),
    (r"(поставь |запусти |заведи |засеки )?таймер на (?P<d>.+)", "clock",
     {"action": "timer_set", "duration": "@d"}, ""),
    (r"сколько (осталось|там) (на таймере|таймеру)", "clock", {"action": "timer_list"}, ""),
    (r"(запусти |включи |засеки )?секундомер", "clock", {"action": "stopwatch_start"}, ""),
    (r"(останови|стоп|выключи) секундомер|секундомер стоп", "clock", {"action": "stopwatch_stop"}, ""),
    (r"выключи будильник|я (встал|проснулся)|хватит звенеть", "clock", {"action": "alarm_stop"}, ""),
    (r"отложи( будильник)?( на (?P<m>\d+) минут[уы]?)?", "clock",
     {"action": "alarm_snooze", "minutes": "@m"}, ""),
    # Глаза.
    (r"смотри на экран|будь моими глазами", "eyes", {"action": "open", "source": "screen"}, "eyes_open"),
    (r"закрой глаза|не смотри|хватит смотреть", "eyes", {"action": "close"}, ""),
    # Вежливость.
    (r"спасибо( большое| тебе)?|благодарю|спс", None, {}, "thanks"),
    (r"ты (тут|здесь)|ты меня слышишь|ты на связи", None, {}, "here"),
]
_COMPILED = [(re.compile(p), tool, args, reply) for p, tool, args, reply in _RULES]
_CHECK = {"v": _level, "v2": _level, "d": _duration}


def match(text: str) -> Quick | None:
    """Команда целиком — Quick; иначе None (фразу разбирает Gemini)."""
    if not enabled():
        return None
    t = normalize(text)
    if not t:
        return None
    # Свои команды и паки (core/macros.py) — первыми: их выбрал сам владелец.
    try:
        from core.macros import macros
        hit = macros().match(text)
    except Exception as exc:
        logger.debug("Свои команды: %s", exc)
        hit = None
    if hit:
        cmd, _slots = hit
        if cmd.confirm:
            return None                           # переспросит Gemini
        return Quick("macro", {"action": "run", "phrase": text}, "ack", t)
    for rx, tool, args, reply in _COMPILED:
        m = rx.fullmatch(t)
        if not m:
            continue
        out = {}
        for k, v in args.items():
            if isinstance(v, str) and v.startswith("@"):
                group = v[1:]
                raw = m.groupdict().get(group) or m.groupdict().get(group + "2")
                if raw is None:
                    continue                      # необязательное: «отложи» без минут
                raw = raw.strip()
                check = _CHECK.get(group)
                if check and not check(raw):
                    return None                   # «громкость какая-то» — пусть решает Gemini
                out[k] = raw
            else:
                out[k] = v
        return Quick(tool, out, reply, t)
    return None


# ── Неудачу видно по ответу инструмента ──────────────────────────────────────
_FAIL_RE = re.compile(r"(не удалось|не получилось|не нашел|не нашёл|не найден|ошибк|не могу|недоступ|"
                      r"нет связи|не запущен|ничего не|не подключ|не играет|не отвечает|не успело|"
                      r"не выполнено|нет активн|не понял|уточните)", re.I)


def failed(result) -> bool:
    return bool(_FAIL_RE.search(str(result or "")))


def reply_for(q: Quick, result) -> str:
    """Что сказать после команды: готовая фраза, а если не вышло или ответ
    содержательный — сам ответ инструмента."""
    text = str(result or "").strip()
    if q.tool is None:
        return q.phrase()
    if q.reply and not failed(text):
        return q.phrase()
    return text or q.phrase() or random.choice(ACK)


# ── Кэш озвученных фраз ───────────────────────────────────────────────────────
class VoiceCache:
    """PCM готовых фраз на диске: ключ — голос, частота и сам текст."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._mem: dict[str, bytes] = {}

    @staticmethod
    def voice() -> str:
        try:
            from telegram_bot import tts_fish
            if tts_fish.is_configured():
                return "fish:" + tts_fish._voice_id()
        except Exception:
            pass
        return "edge:" + os.getenv("EDGE_VOICE", "ru-RU-DmitryNeural")

    def _key(self, text: str, rate: int, voice: str | None) -> str:
        raw = f"{voice or self.voice()}|{rate}|{text.strip()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]

    def get(self, text: str, rate: int, voice: str | None = None) -> bytes | None:
        k = self._key(text, rate, voice)
        if k in self._mem:
            return self._mem[k]
        p = self.root / f"{k}.pcm"
        try:
            data = p.read_bytes()
        except OSError:
            return None
        if data:
            self._mem[k] = data
        return data or None

    def put(self, text: str, rate: int, pcm: bytes, voice: str | None = None):
        if not pcm:
            return
        k = self._key(text, rate, voice)
        self._mem[k] = pcm
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / f"{k}.tmp"
            tmp.write_bytes(pcm)
            tmp.replace(self.root / f"{k}.pcm")
        except OSError as exc:
            logger.debug("Кэш голоса не записан: %s", exc)

    def wanted(self, text: str) -> bool:
        """Кэшируем только готовые фразы — не весь разговор."""
        return text.strip() in _PHRASE_SET


_PHRASE_SET = set(all_phrases())
_cache: VoiceCache | None = None


def voice_cache() -> VoiceCache:
    global _cache
    if _cache is None:
        try:
            from core.paths import get_data_root
            root = Path(get_data_root()) / "voice_cache"
        except Exception:
            root = Path(__file__).resolve().parent.parent / "voice_cache"
        _cache = VoiceCache(root)
    return _cache


async def prewarm(synth, rate: int) -> int:
    """Озвучить заранее всё, чего ещё нет в кэше. synth(text) → pcm.
    Сколько озвучено."""
    cache = voice_cache()
    voice = cache.voice()
    done = 0
    for text in all_phrases():
        if cache.get(text, rate, voice):
            continue
        try:
            pcm = await synth(text)
        except Exception as exc:
            logger.debug("Готовая фраза «%s» не озвучена: %s", text, exc)
            continue
        if pcm:
            cache.put(text, rate, pcm, voice)
            done += 1
    if done:
        logger.info("Готовые фразы озвучены: %d", done)
    return done
