"""Джарвис звонит в Telegram — голосом, в обе стороны, как в фильме.

Бот Telegram звонить не умеет, а сам себе аккаунт не позвонит. Поэтому у
Джарвиса свой аккаунт (второй номер). Он звонит с ПК через py-tgcalls:
  твой голос из звонка (48 кГц) → 16 кГц → Gemini Live,
  ответ Gemini (24 кГц) → 48 кГц → звонок кадрами по 10 мс.
Перебил — очередь ответа сбрасывается. Попрощались — Gemini вызывает
end_call, Джарвис договаривает и кладёт трубку.

Вход один раз: `JARVIS.exe --caller-login` (или `python -m core.tg_call login`)
спросит номер аккаунта Джарвиса, код из Telegram и, если есть, пароль, а
потом — кому звонить (@username или номер). Сессия лежит в папке данных
(jarvis_caller.session) и в репозиторий не попадает.

Ключи: telethon_api_id и telethon_api_hash (my.telegram.org), call_to.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime

logger = logging.getLogger(__name__)

TG_RATE = 48000
FRAME_SEC = 0.01
FRAME_BYTES = int(TG_RATE * FRAME_SEC) * 2           # 10 мс, моно, 16 бит = 960 байт
IN_RATE, OUT_RATE = 16000, 24000
MIC_BATCH_SEC = 0.03
# Диагностика «говорю, а он не слышит»: первые секунды голоса из трубки — в файлы
# (как пришло из Telegram и что ушло в Gemini). Только в звонках хозяину:
# голоса контактов не сохраняются.
REC_SEC = 20
REC_NAMES = ("call_in_raw.wav", "call_in_gemini.wav")
ANSWER_TIMEOUT = 45
MAX_CALL_SEC = 10 * 60
BYE_SILENCE_SEC = 6.0           # попрощались и тишина — кладём трубку сами
IDLE_SEC = 90.0                 # никто ничего не говорит — тоже
# Когда считать, что собеседник договорил. По умолчанию Gemini ждёт ~секунду
# тишины, а шум телефонной линии растягивает это ещё — отсюда долгие паузы
# перед ответом. Как в голосовом Джарвисе на ПК (main._build_config), но чуть
# длиннее: в трубке люди делают паузы внутри фразы.
CALL_VAD_SILENCE_MS = int(os.getenv("CALL_VAD_SILENCE_MS", "450"))
# Раздумья модели перед ответом. На ПК их выключили давно (main._THINKING_BUDGET:
# медиана первого звука 4152 мс → 1377 мс), а в звонке забыли — каждая реплика
# в трубке ждала лишние ~2,8 с. Разговору по телефону рассуждения не нужны.
CALL_THINKING_BUDGET = int(os.getenv("JARVIS_THINKING_BUDGET", "0"))
_BYE = re.compile(r"(?<!\w)(пока|до свидания|до встречи|до связи|спокойной ночи|доброй ночи|всего доброго|"
                  r"всего хорошего|хорошего дня|хорошего вечера|бывай|прощай|отключ\w*|клад\w* трубку|"
                  r"полож\w* трубку|bye|goodbye)(?!\w)", re.I)
_STOP = re.compile(r"(?<!\w)(стой|подожди|погоди|секунду|ещё вопрос|еще вопрос|не клади)(?!\w)", re.I)
# Не мешают отбою: вежливость, согласие и обрывки слов из опоздавшей расшифровки.
_FILLER = {"да", "угу", "ага", "ок", "окей", "ну", "хорошо", "ладно", "спасибо", "благодарю", "и", "тебе",
           "вам", "тоже", "взаимно", "давай", "давайте", "всё", "все", "понял", "поняла", "ясно", "отлично",
           "супер", "пока", "до", "свидания", "встречи", "связи", "спокойной", "доброй", "ночи", "всего",
           "доброго", "хорошего", "дня", "вечера", "бывай", "прощай", "сэр", "джарвис"}


def says_bye(text: str) -> bool:
    return bool(_BYE.search(text or ""))


def keeps_talking(text: str) -> bool:
    """После прощания человек правда продолжил разговор (а не опоздавшее «пока»)?"""
    if _STOP.search(text or ""):
        return True
    words = [w for w in re.findall(r"[a-zа-яё]+", (text or "").lower()) if len(w) > 2 and w not in _FILLER]
    words = [w for w in words if not _BYE.fullmatch(w)]
    return len(words) >= 2

_call_lock = threading.Lock()


# ── звук ──────────────────────────────────────────────────────────────────────

class Downsampler:
    """48 кГц → 16 кГц: фильтр нижних частот (окно Хэмминга, срез 7 кГц), потом
    каждый третий отсчёт. Раньше было среднее по тройкам — оно почти не режет
    7–16 кГц, и шипение линии заворачивалось в речь: Gemini слышал «<noise>».
    Хвост, не кратный трём, и историю фильтра помнит между кусками."""

    TAPS = 63

    def __init__(self):
        import numpy as np
        n = np.arange(self.TAPS) - (self.TAPS - 1) / 2
        h = np.sinc(2 * 7000 / TG_RATE * n) * np.hamming(self.TAPS)
        self._h = h / h.sum()
        self._hist = np.zeros(self.TAPS - 1)
        self._tail = b""

    def __call__(self, pcm: bytes) -> bytes:
        import numpy as np
        data = self._tail + pcm
        n = (len(data) // 6) * 6
        self._tail = data[n:]
        if not n:
            return b""
        x = np.concatenate((self._hist, np.frombuffer(data[:n], dtype="<i2").astype(np.float64)))
        self._hist = x[-(self.TAPS - 1):]
        y = np.convolve(x, self._h, mode="valid")[::3]
        return np.clip(np.round(y), -32768, 32767).astype("<i2").tobytes()


class LineGain:
    """Тихая трубка → нормальная громкость. Телефон отдаёт голос в 5–10 раз
    тише микрофона у ПК, и Gemini с «низкой чувствительностью к началу речи»
    принимал его за шум. Усиление подстраивается по громкости РЕЧИ (тишина и
    шум линии его не раскачивают), не больше MAX раз, плавно — без щелчков."""

    TARGET, FLOOR, MAX = 4000.0, 120.0, 6.0

    def __init__(self):
        self.gain = 1.0
        self._level = 0.0                      # огибающая громкости речи

    def __call__(self, pcm: bytes) -> bytes:
        import numpy as np
        x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64)
        if not len(x):
            return b""
        rms = float(np.sqrt(np.mean(x * x)))
        if rms > self.FLOOR:                   # это речь, а не шум линии
            k = 0.3 if rms > self._level else 0.02          # быстро вверх, медленно вниз
            self._level = rms if not self._level else self._level + k * (rms - self._level)
        want = min(self.MAX, max(1.0, self.TARGET / self._level)) if self._level else 1.0
        ramp = np.linspace(self.gain, want, len(x), endpoint=False)
        self.gain = want
        return np.clip(np.round(x * ramp), -32768, 32767).astype("<i2").tobytes()


class Upsampler:
    """24 кГц → 48 кГц линейной интерполяцией; помнит последний отсчёт, чтобы
    на стыке кусков не было щелчка."""

    def __init__(self):
        self._last: int | None = None

    def reset(self):
        self._last = None

    def __call__(self, pcm: bytes) -> bytes:
        import numpy as np
        x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.int32)
        if not len(x):
            return b""
        prev = np.concatenate(([x[0] if self._last is None else self._last], x[:-1]))
        self._last = int(x[-1])
        out = np.empty(len(x) * 2, dtype=np.int32)
        out[0::2] = (prev + x) // 2
        out[1::2] = x
        return out.astype("<i2").tobytes()


class OutBuffer:
    """Очередь звука в звонок: кладём любыми кусками, берём кадрами по 10 мс."""

    def __init__(self):
        self._buf = bytearray()

    def push(self, pcm: bytes):
        self._buf += pcm

    def pop(self) -> bytes | None:
        if len(self._buf) < FRAME_BYTES:
            return None
        frame = bytes(self._buf[:FRAME_BYTES])
        del self._buf[:FRAME_BYTES]
        return frame

    def flush_tail(self) -> bytes | None:
        """Последний неполный кадр, добитый тишиной."""
        if not self._buf:
            return None
        frame = bytes(self._buf).ljust(FRAME_BYTES, b"\0")
        self._buf.clear()
        return frame

    def clear(self):
        self._buf.clear()

    def __len__(self):
        return len(self._buf)


SILENCE = b"\0" * FRAME_BYTES


# ── разговор ──────────────────────────────────────────────────────────────────

END_CALL = {
    "name": "end_call",
    "description": ("Положить трубку. Только когда собеседник сам попрощался или попросил "
                    "отключиться/положить трубку. Не вызывай сразу после того, как передал сообщение."),
    "parameters": {"type": "OBJECT", "properties": {}},
}


def instruction(topic: str, context: str = "", name: str = "сэр") -> str:
    now = datetime.now().strftime("%A, %d %B %Y, %H:%M")
    return (
        "Ты — ДЖАРВИС, ИИ-ассистент в стиле фильмов о Железном человеке: вежливый, "
        "спокойный, с лёгкой британской иронией. Обращайся «сэр».\n"
        f"Сейчас {now}. Ты САМ позвонил {name} по Telegram, и он только что взял трубку.\n"
        f"Повод звонка: {topic}.\n"
        + (f"Что нужно сообщить:\n{context}\n" if context else "")
        + "Это телефонный разговор: говори по-русски, коротко и живо, по одной мысли за раз, "
        "без списков и разметки. Начни сам: поздоровайся и скажи, зачем звонишь.\n"
        "Сказав, зачем звонишь, НЕ клади трубку: это живой разговор. Замолчи и дай собеседнику "
        "ответить; отвечай на всё, что он скажет («привет», «как дела» и т. п.), поддерживай беседу.\n"
        "end_call вызывай ТОЛЬКО когда собеседник сам попрощался («пока», «до свидания», «спокойной "
        "ночи») или попросил отключиться / положить трубку — тогда одной короткой фразой попрощайся "
        "и сразу вызови end_call. Твоё собственное «спокойной ночи» — не повод класть трубку."
    )


def instruction_contact(owner: str, contact: str, message: str, ask: str = "", note: str = "") -> str:
    """Звонок НЕ хозяину, а его контакту — по его просьбе. Живой разговор по
    заданию: передать, спросить, выслушать, ответить — а не зачитать фразу.

    Раньше было «по-русски, ничего не добавляя от себя»: Джарвис зачитывал
    сообщение и не умел поддержать разговор, а узбекоязычному собеседнику
    всё равно отвечал по-русски."""
    now = datetime.now().strftime("%A, %d %B %Y, %H:%M")
    return (
        f"Ты — ДЖАРВИС, голосовой ИИ-ассистент {owner}. Вежливый, спокойный, тёплый, с лёгким юмором.\n"
        f"Сейчас {now}. Ты САМ позвонил человеку по имени {contact} по Telegram по просьбе {owner}, "
        "и он только что взял трубку.\n"
        + (f"Кто это для {owner}: {note}.\n" if note else "")
        + f"ЗАДАНИЕ. Передать: «{message or 'просто узнать, как дела'}».\n"
        + (f"Спросить и запомнить ответ: «{ask}».\n" if ask else "")
        + "КАК ГОВОРИТЬ. Это телефонный разговор: коротко, живо, по одной мысли, без списков. Начни сам: "
        f"поздоровайся, представься («Это Джарвис, ИИ-ассистент {owner}») и передай сообщение своими "
        "словами. Говори на том языке, на котором отвечает собеседник (русский, узбекский, казахский, "
        "английский) и переключайся вслед за ним.\n"
        + ("Задай вопросы из задания по одному и дождись ответа на каждый; не понял — переспроси.\n" if ask else "")
        + "Это живой разговор, а не автоответчик: отвечай на то, что тебе говорят, поддержи small talk, "
        "уточни, если ответ неясен. Отвечай по сути из задания; чего не знаешь — не выдумывай и не обещай "
        f"ничего за {owner} (встречи, деньги, сроки): скажи, что передашь. Попросят что-то передать — "
        "запомни дословно и повтори вслух, чтобы подтвердить.\n"
        "Не клади трубку сразу после сообщения: дай ответить. Перед прощанием коротко повтори, что "
        f"передашь {owner}. end_call — когда собеседник попрощался или всё сказано и вы попрощались."
    )


class CallSession:
    """Один звонок: дозвон → разговор с Gemini → отбой.

    tg — обёртка над звонком (ring/listen/send/hangup, колбэки on_audio и
    on_hangup), live — фабрика Live-сессии Gemini (async context manager).
    Обе подменяются в тестах."""

    def __init__(self, tg, live: Callable, peer, prompt: str, max_sec: float = MAX_CALL_SEC,
                 log: Callable[[str], None] | None = None, callee: str = ""):
        self.tg, self.live, self.peer, self.prompt = tg, live, peer, prompt
        self.callee = callee               # кому звоним; "" — хозяину («Вы не взяли трубку»)
        self.max_sec = max_sec
        self.log = log or (lambda s: logger.info("Звонок: %s", s))
        self.out = OutBuffer()
        self.up, self.down, self.boost = Upsampler(), Downsampler(), LineGain()
        self._rec_raw, self._rec_sent = bytearray(), bytearray()
        self.transcript: list[str] = []
        self._mic: asyncio.Queue[bytes] | None = None
        self._hung_up = asyncio.Event()
        self._ending = False
        self.ended_by = ""                 # end_call | прощание | тишина — для журнала
        self._after_end = ""               # что собеседник сказал после нашего прощания
        self._user_said = ""               # текущая реплика собеседника
        self._jarvis_said = ""             # текущая реплика Джарвиса
        self._user_bye_at = 0.0
        self._last_voice = time.monotonic()
        # Замеры для журнала: где тормозит, если звонок «лагает».
        self._user_spoke_at = 0.0          # последняя реплика собеседника без ответа
        self.reply_delays: list[float] = []   # от его слов до первого звука Джарвиса
        self.late_ms = 0.0                 # на сколько максимум опаздывала отправка кадра
        # Что реально пришло из трубки. Жалоба «говорит, но не отвечает»
        # неотличима по поведению для «звук не приходит», «приходит тишина»
        # и «приходит, но Gemini не слышит речь» — журнал их различает.
        self.frames_in, self.sizes, self._sq, self._samples = 0, set(), 0.0, 0
        # Молчит ли сам Джарвис: сколько сообщений пришло от Gemini и когда он заговорил.
        self.gemini_msgs = 0
        self._picked_up_at = 0.0
        self.first_voice_sec = 0.0

    def audio_stats(self) -> str:
        rms = (self._sq / self._samples) ** 0.5 if self._samples else 0.0
        heard = sum(1 for t in self.transcript if t.startswith("Вы:"))
        d = sorted(self.reply_delays)
        delays = (f", ответ через: медиана {d[len(d) // 2]:.1f} с, макс {d[-1]:.1f} с" if d else "")
        return (f"из трубки кадров: {self.frames_in}, размеры: {sorted(self.sizes)[:4]}, "
                f"громкость RMS: {rms:.0f}, реплик собеседника в расшифровке: {heard}{delays}, "
                f"опоздание отправки звука: макс {self.late_ms:.0f} мс")

    # колбэки из py-tgcalls (его цикл — тот же, что у нас)
    def _on_audio(self, pcm48: bytes):
        self.frames_in += 1
        if len(self.sizes) < 8:
            self.sizes.add(len(pcm48))
        if self.frames_in % 10 == 1 and len(pcm48) >= 2:
            import numpy as np
            x = np.frombuffer(pcm48[: len(pcm48) // 2 * 2], dtype="<i2").astype(np.float64)
            self._sq += float((x * x).sum())
            self._samples += x.size
        recording = not self.callee
        if recording and len(self._rec_raw) < REC_SEC * TG_RATE * 2:
            self._rec_raw += pcm48
        if self._mic is not None:
            chunk = self.down(pcm48)
            if chunk:
                sent = self.boost(chunk)
                if recording and len(self._rec_sent) < REC_SEC * IN_RATE * 2:
                    self._rec_sent += sent
                self._mic.put_nowait(sent)

    def save_recording(self, folder=None) -> list[str]:
        """Голос из трубки — в WAV рядом с журналом: что пришло и что услышал Gemini."""
        if not self._rec_raw:
            return []
        import wave
        try:
            if folder is None:
                from core.paths import get_user_data_dir
                folder = os.getenv("JARVIS_CALL_REC_DIR") or get_user_data_dir()
            saved = []
            for name, data, rate in ((REC_NAMES[0], self._rec_raw, TG_RATE),
                                     (REC_NAMES[1], self._rec_sent, IN_RATE)):
                path = os.path.join(str(folder), name)
                with wave.open(path, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(rate)
                    w.writeframes(bytes(data))
                saved.append(path)
            logger.info("Звонок: голос из трубки сохранён (%d с) — %s", len(self._rec_raw) // (TG_RATE * 2),
                        ", ".join(saved))
            return saved
        except Exception as exc:
            logger.warning("Звонок: запись голоса не сохранилась: %s", exc)
            return []

    async def _connect_live(self):
        """Подключить Gemini к взятой трубке. «1011 service unavailable» и обрывы —
        временные сбои Google: раньше первый же такой сбой ронял весь звонок
        (журнал 02.10, 21:00). Теперь — ещё две попытки с паузой; ключ и квоту
        повторять бессмысленно."""
        for attempt in range(3):
            try:
                live = self.live(self.prompt)
                return live, await live.__aenter__()
            except Exception as exc:
                text = f"{type(exc).__name__}: {exc}"
                msg = str(exc).lower()
                fatal = any(k in msg for k in ("1008", "quota", "key", "permission", "invalid"))
                transient = not fatal and (isinstance(exc, (TimeoutError, ConnectionError)) or any(
                    k in msg for k in ("1011", "unavailable", "timeout", "timed out", "1006", "503", "internal")))
                if not transient or attempt == 2:
                    # Трубку взяли, а Gemini не подключился (ключ, квота, сеть) — человек
                    # слышал тишину, а причина пропадала. Теперь — в журнал целиком.
                    logger.error("Звонок: Gemini не подключился — %s", text)
                    raise
                logger.warning("Звонок: Gemini не ответил (%s) — пробую ещё раз", text)
                await asyncio.sleep(0.8 * (attempt + 1))
        raise RuntimeError("недостижимо")

    def _on_hangup(self):
        self._hung_up.set()

    async def run(self) -> str:
        self._mic = asyncio.Queue()
        self.tg.on_audio = self._on_audio
        self.tg.on_hangup = self._on_hangup
        try:
            await self.tg.ring(self.peer)
        except Exception as exc:
            # Настоящая причина — в журнал: раньше она терялась, и «не дозвонился»
            # нечем было объяснить.
            logger.warning("Звонок %s не состоялся: %s: %s", self.callee or "хозяину", type(exc).__name__, exc)
            return _ring_error(exc, self.callee)
        started = time.monotonic()
        self.log("взял трубку")
        try:
            await self.tg.listen(self.peer)
            self._picked_up_at = time.monotonic()
            live, session = await self._connect_live()
            logger.info("Звонок: Gemini на связи через %.1f с после ответа", time.monotonic() - self._picked_up_at)
            try:
                await session.send_client_content(
                    turns=[{"role": "user", "parts": [{"text": "[Собеседник взял трубку. Начинай разговор.]"}]}],
                    turn_complete=True)
                pace = asyncio.create_task(self._pace())
                watch = asyncio.create_task(self._watch_audio())
                guard = asyncio.create_task(self._idle_guard())
                ended = asyncio.create_task(self._hung_up.wait())
                pumps = {asyncio.create_task(self._pump_mic(session)),
                         asyncio.create_task(self._pump_gemini(session))}
                # Конец разговора — когда Джарвис договорил после end_call
                # (pace) или положили трубку. Gemini, закончивший после
                # end_call, звонок не обрывает: хвост фразы ещё в очереди.
                waiting, deadline = pumps | {pace, ended}, time.monotonic() + self.max_sec
                while waiting:
                    done, _ = await asyncio.wait(waiting, timeout=max(0.0, deadline - time.monotonic()),
                                                 return_when=asyncio.FIRST_COMPLETED)
                    failed = [t for t in done if t not in (pace, ended) and t.exception()]
                    for t in failed:
                        logger.warning("Звонок оборвался: %r", t.exception())
                    if not done or pace in done or ended in done or failed:
                        break
                    waiting -= done
                for t in pumps | {pace, ended, watch, guard}:
                    t.cancel()
            finally:
                await live.__aexit__(None, None, None)
        finally:
            if not self._hung_up.is_set():
                try:
                    await self.tg.hangup(self.peer)
                except Exception as exc:
                    logger.warning("Отбой не удался: %s: %s", type(exc).__name__, exc)
            self.save_recording()                 # и после сбоя: запись нужнее всего именно тогда
        logger.info("Звонок: %s", self.audio_stats())
        for line in self.transcript[-40:]:
            logger.info("Звонок | %s", line[:200])
        if self.ended_by:
            logger.info("Звонок: отбой — %s", self.ended_by)
        mins = (time.monotonic() - started) / 60
        who = "вы положили трубку" if self._hung_up.is_set() and not self._ending else "попрощались"
        return f"Поговорили {max(1, round(mins))} мин, {who}."

    async def _watch_audio(self, after: float = 6.0):
        await asyncio.sleep(after)
        if not self.first_voice_sec:
            logger.warning("Звонок: Джарвис за %.0f с не сказал ни слова — сообщений от Gemini: %d",
                           after, self.gemini_msgs)
        if self.frames_in == 0:
            logger.warning("Звонок: за %.0f с из трубки не пришло ни одного кадра звука — "
                           "Джарвис говорит, но собеседника не слышит", after)
        else:
            logger.info("Звонок: звук из трубки идёт — %s", self.audio_stats())

    def _end(self, why: str):
        if not self._ending:
            self._ending, self.ended_by, self._after_end = True, why, ""
            self.log(f"кладу трубку: {why}")

    async def _idle_guard(self):
        """Страховка отбоя: вы попрощались и замолчали — или все молчат давно."""
        while True:
            await asyncio.sleep(0.5)
            quiet = time.monotonic() - self._last_voice
            if self._user_bye_at and quiet > BYE_SILENCE_SEC and not len(self.out):
                self._end("попрощались и тишина")
            elif quiet > IDLE_SEC:
                self._end("долго тишина")

    async def _pump_mic(self, session):
        from google.genai import types
        while True:
            # Из трубки кадры по 10 мс: слать каждый — 100 сообщений в секунду,
            # отправка не успевает, и голос доходит до Gemini с опозданием.
            # Копим ~40 мс и шлём одним куском.
            chunks = [await self._mic.get()]
            await asyncio.sleep(MIC_BATCH_SEC)
            while not self._mic.empty():
                chunks.append(self._mic.get_nowait())
            await session.send_realtime_input(audio=types.Blob(data=b"".join(chunks),
                                                               mime_type=f"audio/pcm;rate={IN_RATE}"))

    async def _pump_gemini(self, session):
        from google.genai import types
        while True:
            # receive() отдаёт один ход; если он кончился сразу (сессия
            # закрылась), без паузы цикл крутился бы вхолостую и душил звонок.
            await asyncio.sleep(0.01)
            async for msg in session.receive():
                self.gemini_msgs += 1
                sc = getattr(msg, "server_content", None)
                if sc is not None and getattr(sc, "interrupted", False):
                    self.out.clear()                      # перебили — замолкаем сразу
                    self.up.reset()
                if getattr(msg, "data", None):
                    if not self.first_voice_sec and self._picked_up_at:
                        self.first_voice_sec = time.monotonic() - self._picked_up_at
                        logger.info("Звонок: Джарвис заговорил через %.1f с после ответа", self.first_voice_sec)
                    if self._user_spoke_at:
                        self.reply_delays.append(time.monotonic() - self._user_spoke_at)
                        self._user_spoke_at = 0.0
                    self.out.push(self.up(msg.data))
                    self._last_voice = time.monotonic()
                if sc is not None:
                    for attr, who in (("input_transcription", "Вы"), ("output_transcription", "Джарвис")):
                        tr = getattr(sc, attr, None)
                        if tr is not None and getattr(tr, "text", None):
                            # Кусочек как пришёл (с пробелом или без) — core/call_log склеит слова.
                            self.transcript.append(f"{who}:{tr.text}")
                            self._last_voice = time.monotonic()
                            self._heard(who, tr.text)
                    # Джарвис договорил реплику: вы попрощались, и он попрощался в ответ —
                    # кладём трубку, даже если Gemini забыл вызвать end_call.
                    if getattr(sc, "turn_complete", False):
                        if self._user_bye_at and says_bye(self._jarvis_said):
                            self._end("попрощались (без end_call)")
                        self._jarvis_said = ""
                tc = getattr(msg, "tool_call", None)
                if tc is not None:
                    replies = []
                    for fc in tc.function_calls or []:
                        if fc.name == "end_call":
                            self._end("end_call")
                        replies.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"ok": True}))
                    if replies:
                        await session.send_tool_response(function_responses=replies)

    def _heard(self, who: str, text: str):
        if who == "Джарвис":
            self._jarvis_said += text
            self._user_said = ""
            return
        self._user_said += text
        self._user_spoke_at = time.monotonic()
        # Прощание — если реплика им КОНЧАЕТСЯ: «пока не знаю» — не прощание.
        words = re.findall(r"[a-zа-яё]+", self._user_said.lower())
        while words and words[-1] in ("сэр", "джарвис", "спасибо", "тебе", "вам", "тоже", "и", "ну"):
            words.pop()
        tail = " ".join(words[-3:])
        if says_bye(tail):
            self._user_bye_at = time.monotonic()
        elif self._user_bye_at and keeps_talking(tail):
            self._user_bye_at = 0.0
        # Мы уже прощались, а собеседник говорит. Опоздавшая расшифровка его «пока»
        # или «спасибо» отбой не отменяет — только настоящее продолжение разговора.
        if self._ending:
            self._after_end += " " + text
            if keeps_talking(self._after_end):
                self._ending, self.ended_by, self._after_end = False, "", ""
                self._user_bye_at = 0.0
                self.log("собеседник продолжает говорить — трубку не кладу")

    async def _pace(self):
        """Кадр каждые 10 мс по часам, а не по sleep: на Windows sleep
        дрожит на ~15 мс, поэтому отстающие кадры досылаются пачкой."""
        next_t = time.monotonic()
        idle_after_end = 0
        while True:
            now = time.monotonic()
            self.late_ms = max(self.late_ms, (now - next_t) * 1000)
            if now - next_t > 0.2:                        # долго стояли — не догоняем прошлое
                next_t = now
            while next_t <= now:
                frame = self.out.pop()
                if frame is None and self._ending:
                    frame = self.out.flush_tail()
                    if frame is None:
                        idle_after_end += 1
                        if idle_after_end > 40:               # договорил + 0,4 с тишины — отбой
                            return
                await self.tg.send(self.peer, frame or SILENCE)
                next_t += FRAME_SEC
            await asyncio.sleep(max(0.0, next_t - time.monotonic()))


def _ring_error(exc: BaseException, callee: str = "") -> str:
    """Почему не дозвонились — про того, кому звонили (хозяину — «вы»)."""
    kind, text = type(exc).__name__, str(exc)
    if kind in ("TimedOutAnswer", "TimeoutError"):
        return (f"{callee} не взял трубку за {ANSWER_TIMEOUT} с." if callee else "Вы не взяли трубку.")
    if kind == "CallDeclined":
        return f"{callee} сбросил звонок." if callee else "Вы сбросили звонок."
    if kind == "CallBusy":
        return f"У {callee} занята линия." if callee else "Линия занята — вы на другом звонке."
    if "PRIVACY" in text.upper():
        return (f"{callee} не принимает звонки от этого аккаунта (настройки приватности Telegram)."
                if callee else "Ваши настройки Telegram не пускают звонки от аккаунта Джарвиса.")
    return f"Не удалось позвонить: {kind}: {exc}"


# ── Telegram ──────────────────────────────────────────────────────────────────

def _keys() -> dict:
    from core.paths import load_api_keys
    return load_api_keys()


def _credentials() -> tuple[int, str]:
    k = _keys()
    api_id = os.getenv("TELETHON_API_ID") or k.get("telethon_api_id")
    api_hash = os.getenv("TELETHON_API_HASH") or k.get("telethon_api_hash")
    if not api_id or not api_hash:
        raise RuntimeError("нет telethon_api_id и telethon_api_hash (их выдаёт my.telegram.org)")
    return int(api_id), str(api_hash)


def session_path() -> str:
    from core.paths import get_data_root
    return os.path.join(str(get_data_root()), "jarvis_caller")


def _drop_session() -> None:
    """Убрать файлы сессии, в которую так и не вошли."""
    base = session_path()
    for suffix in (".session", ".session-journal", ".login.json"):
        try:
            os.remove(base + suffix)
        except OSError:
            pass


def ready() -> str:
    """'' — можно звонить, иначе — что мешает, человеческими словами."""
    try:
        _credentials()
    except RuntimeError as exc:
        return str(exc).capitalize() + "."
    if not os.path.isfile(session_path() + ".session"):
        return "Аккаунт Джарвиса для звонков не подключён: запустите JARVIS.exe --caller-login."
    if not str(_keys().get("call_to") or "").strip():
        return "Не знаю, кому звонить: выполните вход заново (JARVIS.exe --caller-login)."
    return ""


class TgCall:
    """Обёртка py-tgcalls под CallSession: звонок один на один, звук
    внешними кадрами в обе стороны."""

    def __init__(self, client):
        from pytgcalls import PyTgCalls, filters
        from pytgcalls.types import ChatUpdate, Direction, StreamFrames

        self.client = client
        self.app = PyTgCalls(client)
        self.on_audio: Callable[[bytes], None] = lambda b: None
        self.on_hangup: Callable[[], None] = lambda: None

        @self.app.on_update(filters.stream_frame(Direction.INCOMING))
        async def _frames(_, upd: StreamFrames):
            for f in upd.frames:
                self.on_audio(f.frame)

        @self.app.on_update(filters.chat_update(ChatUpdate.Status.LEFT_CALL))
        async def _left(_, upd):
            self.on_hangup()

    async def start(self):
        await self.app.start()

    async def ring(self, peer):
        from pytgcalls.types import CallConfig, MediaStream
        from pytgcalls.types.raw import AudioParameters
        from pytgcalls.types.stream.external_media import ExternalMedia
        await self.app.play(peer, MediaStream(ExternalMedia.AUDIO, AudioParameters(TG_RATE, 1),
                                              video_flags=MediaStream.Flags.IGNORE),
                            CallConfig(timeout=ANSWER_TIMEOUT))

    async def listen(self, peer):
        from pytgcalls.types import RecordStream
        from pytgcalls.types.raw import AudioParameters
        await self.app.record(peer, RecordStream(audio=True, audio_parameters=AudioParameters(TG_RATE, 1)))

    async def send(self, peer, frame: bytes):
        from pytgcalls.types import Device
        await self.app.send_frame(peer, Device.MICROPHONE, frame)

    async def hangup(self, peer):
        """Положить трубку. Если leave_call упал на полпути (связь остановлена,
        а отбой в Telegram не ушёл), у собеседника звонок так и висит в
        тишине — поэтому отбой в Telegram отправляем ещё раз напрямую."""
        try:
            await self.app.leave_call(peer)
        except Exception as exc:
            logger.warning("Отбой звонка не прошёл (%s: %s) — сбрасываю напрямую", type(exc).__name__, exc)
            chat_id = await self.app.resolve_chat_id(peer)
            await self.app._app.discard_call(chat_id, False)


def _same_phone(a: str, b: str) -> bool:
    """Один номер в разных записях: «+7 777…», «8 777…», «7777…» — по последним 10 цифрам."""
    da, db = re.sub(r"\D", "", a or ""), re.sub(r"\D", "", b or "")
    return len(da) >= 9 and len(db) >= 9 and da[-10:] == db[-10:]


async def resolve_peer(client, target: str, name: str = "Сэр") -> int:
    """@username, ссылка t.me, номер телефона или «id:123» → id пользователя.

    Номер телефона: сначала ищем среди УЖЕ сохранённых контактов аккаунта и
    ничего в них не меняем. Раньше номер всегда «импортировался» с именем
    «Сэр» (писалось для звонка хозяину) — а звонок контакту идёт с ВАШЕГО
    Telegram, и Telegram переименовывал вашего «Ибрагима» в «Сэр».
    Добавляем в контакты, только если человека там нет, и под его именем.

    «id:…» — внутренний номер аккаунта (контакт, подтянутый из вашего
    Telegram). Раньше он шёл голыми цифрами и принимался за номер телефона:
    «+123456789» не находился, и звонок контакту не проходил."""
    t = (target or "").strip()
    if t.startswith("id:"):
        uid = int(t[3:])
        try:
            await client.get_input_entity(uid)
        except (ValueError, TypeError):
            # Нет в кэше сессии — подтянуть контакты аккаунта и попробовать ещё раз.
            from telethon.tl.functions.contacts import GetContactsRequest
            await client(GetContactsRequest(hash=0))
            try:
                await client.get_input_entity(uid)
            except (ValueError, TypeError):
                raise RuntimeError("этот аккаунт Telegram не знает этого человека — впишите в «Контактах» "
                                   "его @username или номер") from None
        return uid
    t = re.sub(r"^(https?://)?t\.me/", "@", t)
    digits = re.sub(r"[^\d+]", "", t)
    if digits.lstrip("+").isdigit() and len(digits.lstrip("+")) >= 9 and not t.startswith("@"):
        from telethon.tl.functions.contacts import ImportContactsRequest
        from telethon.tl.types import InputPhoneContact
        from telethon.tl.functions.contacts import GetContactsRequest
        phone = digits if digits.startswith("+") else "+" + digits
        saved = await client(GetContactsRequest(hash=0))
        for u in getattr(saved, "users", None) or []:
            if _same_phone(getattr(u, "phone", ""), phone):
                return u.id                                  # уже в контактах — имя не трогаем
        first, _, last = (name or "Контакт").strip().partition(" ")
        res = await client(ImportContactsRequest([InputPhoneContact(0, phone, first, last)]))
        if not res.users:
            raise RuntimeError(f"в Telegram нет аккаунта с номером {phone} или он скрыт настройками")
        return res.users[0].id
    return (await client.get_entity(t)).id


def _gemini_live(prompt: str):
    from google.genai import types

    from core.onboarding import ensure_gemini_key
    from google import genai
    client = genai.Client(api_key=ensure_gemini_key(interactive=False),
                          http_options={"api_version": "v1beta"})
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=prompt,
        tools=[{"function_declarations": [END_CALL]}],
        input_audio_transcription={}, output_audio_transcription={},
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                silence_duration_ms=CALL_VAD_SILENCE_MS, prefix_padding_ms=200,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW)),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon"))),
        thinking_config=types.ThinkingConfig(thinking_budget=CALL_THINKING_BUDGET),
    )
    model = os.getenv("JARVIS_LIVE_MODEL", "models/gemini-2.5-flash-native-audio-latest")
    return client.aio.live.connect(model=model, config=config)


async def _call_async(topic: str, context: str, log, target: str = "", prompt: str = "",
                      transcript: list | None = None, who: str = "вам") -> str:
    from telethon import TelegramClient
    api_id, api_hash = _credentials()
    client = TelegramClient(session_path(), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return "Аккаунт Джарвиса для звонков вышел из сессии: запустите JARVIS.exe --caller-login."
        peer = await resolve_peer(client, target or str(_keys().get("call_to", "")),
                                  name="Сэр" if who == "вам" else who)
        tg = TgCall(client)
        await tg.start()
        name = (_keys().get("user_name") or "сэр")
        return await _talk(tg, peer, prompt or instruction(topic, context, name), log, transcript, who, topic)
    finally:
        await client.disconnect()


async def _talk(tg, peer, prompt: str, log, transcript: list | None, who: str, topic: str) -> str:
    """Сам разговор — и запись в историю звонков, чем бы он ни кончился."""
    sess = CallSession(tg, _gemini_live, peer, prompt, log=log, callee="" if who == "вам" else who)
    started, result = time.time(), "Звонок оборвался."
    try:
        result = await sess.run()
        return result
    finally:
        if transcript is not None:
            transcript.extend(sess.transcript)
        try:
            from core.call_log import call_log
            call_log().add(who, topic, result, sess.transcript, started)
        except Exception as exc:
            logger.warning("История звонков: %s", exc)


async def _call_via(client, holder, target: str, prompt: str, log, transcript: list, who: str, topic: str) -> str:
    """Звонок с ВАШЕГО аккаунта (core/contacts.Me): ваши люди вас знают —
    приватность Telegram не мешает, и видно, что звоните вы."""
    peer = await resolve_peer(client, target, name=who)
    tg = getattr(holder, "_tgcall", None)
    if tg is None or tg.client is not client:              # один py-tgcalls на клиента
        tg = TgCall(client)
        await tg.start()
        holder._tgcall = tg
    return await _talk(tg, peer, prompt, log, transcript, who, topic)


def call(topic: str = "просто позвонить", context: str = "", log=None) -> str:
    """Позвонить и поговорить. Блокирует до конца звонка; один звонок за раз."""
    problem = ready()
    if problem:
        return problem
    if not _call_lock.acquire(blocking=False):
        return "Я уже на звонке, сэр."
    try:
        return asyncio.run(_call_async(topic, context, log or (lambda s: logger.info("Звонок: %s", s))))
    except Exception as exc:
        logger.exception("Звонок")
        return f"Звонок не удался: {type(exc).__name__}: {exc}"
    finally:
        _call_lock.release()


def _what_they_said(transcript: list[str], limit: int = 400) -> str:
    """Реплики собеседника (в расшифровке — «Вы:») — одной строкой.

    Склейка — как в истории звонков (call_log.merge): расшифровка приходит
    кусками посреди слова, и простое " ".join давало «При вет, Джар вис»."""
    from core.call_log import merge
    said = " ".join(ln["text"] for ln in merge(transcript, "Собеседник") if ln["who"] == "Собеседник")
    said = re.sub(r"\s+", " ", said).strip()
    return said if len(said) <= limit else said[:limit - 1] + "…"


def call_contact(target: str, contact: str, message: str, log=None, via=None, ask: str = "",
                 note: str = "") -> str:
    """Позвонить контакту хозяина, передать сообщение, вернуть пересказ ответа.
    via — ваш Telegram (core/contacts.Me): звонок с вашего аккаунта; иначе —
    с аккаунта Джарвиса."""
    if via is None:
        problem = ready()
        if problem and "Не знаю, кому звонить" not in problem:
            return problem
    else:
        try:
            _credentials()
        except RuntimeError as exc:
            return str(exc).capitalize() + "."
    if not _call_lock.acquire(blocking=False):
        return "Я уже на звонке — позвоню после."
    heard: list[str] = []
    try:
        owner = (_keys().get("user_name") or "моего владельца")
        log = log or (lambda s: logger.info("Звонок %s: %s", contact, s))
        prompt = instruction_contact(owner, contact, message, ask, note)
        if via is not None:
            result = via.run(lambda client: _call_via(client, via, target, prompt, log, heard, contact, message),
                             timeout=MAX_CALL_SEC + ANSWER_TIMEOUT + 60)
        else:
            result = asyncio.run(_call_async(message, "", log, target=target, prompt=prompt,
                                             transcript=heard, who=contact))
    except Exception as exc:
        logger.exception("Звонок контакту")
        return f"Звонок не удался: {type(exc).__name__}: {exc}"
    finally:
        _call_lock.release()
    said = _what_they_said(heard)
    if said:
        return result + f" {contact} ответил: «{said}»."
    # не дозвонились — «ответа не расслышал» только путает: разговора не было
    return result + (" Ответа не расслышал." if result.startswith("Поговорили") else "")


def call_in_background(topic: str, context_fn: Callable[[], str] | None = None,
                       done: Callable[[str], None] | None = None) -> None:
    """Звонок в своём потоке: голосовой круг Джарвиса не ждёт."""
    def run():
        ctx = ""
        if context_fn:
            try:
                ctx = context_fn() or ""
            except Exception as exc:
                logger.warning("Контекст звонка: %s", exc)
        result = call(topic, ctx)
        logger.info("Звонок «%s»: %s", topic, result)
        if done:
            done(result)
    threading.Thread(target=run, daemon=True, name="tg-call").start()


# ── вход ──────────────────────────────────────────────────────────────────────

def qr_matrix(url: str) -> list[list[bool]]:
    """Клетки QR-кода (True — чёрная), с белой рамкой в 2 клетки."""
    import qrcode
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(url)
    q.make(fit=True)
    return q.get_matrix()


_QR_REFRESH_SEC = 25.0


async def _qr_sign_in(client, view, ask: Callable[[str, bool], str], total_sec: float = 300) -> bool:
    """Вход без кода: QR сканируется в Telegram аккаунта Джарвиса
    (Настройки → Устройства → Подключить устройство).

    Код по номеру у нового аккаунта может не приходить вовсе — у владельца
    26.09 не пришёл ни один из четырёх, а после нескольких запросов Telegram
    ещё и перестаёт их слать на часы. QR не зависит ни от того, ни от другого.
    QR живёт ~30 с — по истечении показываем новый."""
    from telethon.errors import SessionPasswordNeededError
    qr = await client.qr_login()
    end = time.monotonic() + total_sec
    try:
        while time.monotonic() < end:
            view.show(qr.url)
            # Не ждём до qr.expires: его Telethon сравнивает с часами ПК, и при
            # сбитых часах на экране висел просроченный код («Неверный QR-код»).
            # Telegram даёт ~30 с — меняем код каждые 25 с сами.
            task = asyncio.ensure_future(qr.wait(timeout=_QR_REFRESH_SEC))
            while not task.done():
                if view.cancelled():
                    task.cancel()
                    return False
                view.pump()
                await asyncio.sleep(0.05)
            try:
                task.result()
                return True
            except asyncio.TimeoutError:
                await qr.recreate()
            except SessionPasswordNeededError:
                view.close()
                await client.sign_in(password=ask("Пароль двухэтапной проверки аккаунта Джарвиса:", True))
                return True
        return False
    finally:
        view.close()


async def _login_async(ask: Callable[[str, bool], str], qr_view=None) -> str:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    from core.paths import save_api_keys
    api_id, api_hash = _credentials()
    client = TelegramClient(session_path(), api_id, api_hash)
    await client.connect()
    logged_in = False
    try:
        if not await client.is_user_authorized():
            by_code = qr_view is None or ask(
                "Как войти в аккаунт Джарвиса?\n\n"
                "Enter — по QR-коду, без кода (рекомендую): его Telegram → Настройки → Устройства → "
                "Подключить устройство → навести камеру на экран.\n\n"
                "Напишите 2 — по номеру телефона и коду из Telegram.", False) == "2"
            if not by_code:
                if not await _qr_sign_in(client, qr_view, ask):
                    return "Вход отменён: QR-код не отсканировали."
            else:
                phone = ask("Номер телефона АККАУНТА ДЖАРВИСА (в международном формате, с +):", False)
                if not phone:
                    return "Вход отменён."
                await client.send_code_request(phone)
                code = ask("Код из Telegram (приходит в чат «Telegram» аккаунта Джарвиса, не SMS):", False)
                try:
                    await client.sign_in(phone, code)
                except SessionPasswordNeededError:
                    await client.sign_in(password=ask("Пароль двухэтапной проверки аккаунта Джарвиса:", True))
        me = await client.get_me()
        logged_in = True
        target = ask("Кому звонить — ВАШ @username или номер телефона:", False)
        if target:
            await resolve_peer(client, target)             # проверяем сразу, а не в 6 утра
            keys = _keys()
            keys["call_to"] = target.strip()
            save_api_keys(keys)
        return (f"Готово: Джарвис звонит с аккаунта {me.first_name or me.phone}. "
                "Добавьте этот аккаунт в свои контакты, иначе настройки приватности могут не пропустить звонок.")
    finally:
        await client.disconnect()
        if not logged_in:
            # Telethon создаёт файл сессии уже при connect, до всякого входа.
            # Оставить его — значит соврать: ready() считает аккаунт
            # подключённым по одному наличию файла.
            _drop_session()


def login(ask: Callable[[str, bool], str], qr_view=None) -> str:
    try:
        return asyncio.run(_login_async(ask, qr_view))
    except Exception as exc:
        return f"Вход не удался: {type(exc).__name__}: {exc}"


class _QrDialog:
    """Окно с QR-кодом для входа (Qt). Не модальное: пока оно открыто,
    вход ждёт скан в том же потоке, прокачивая события Qt (pump)."""

    def __init__(self):
        self._dlg = None
        self._label = None
        self._closed_by_user = False

    def _ensure(self):
        if self._dlg is not None:
            return
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout
        dlg = QDialog()
        dlg.setWindowTitle("ДЖАРВИС — вход по QR-коду")
        lay = QVBoxLayout(dlg)
        hint = QLabel("Откройте Telegram аккаунта Джарвиса:\n"
                      "Настройки → Устройства → Подключить устройство\n"
                      "и наведите камеру на этот код. Код обновляется сам.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(dlg.reject)
        dlg.rejected.connect(lambda: setattr(self, "_closed_by_user", True))
        lay.addWidget(hint)
        lay.addWidget(self._label)
        lay.addWidget(cancel)
        self._dlg = dlg

    def show(self, url: str):
        from PyQt6.QtGui import QColor, QPainter, QPixmap
        self._ensure()
        m = qr_matrix(url)
        cell = 8
        pm = QPixmap(len(m) * cell, len(m) * cell)
        pm.fill(QColor("white"))
        p = QPainter(pm)
        for y, row in enumerate(m):
            for x, dark in enumerate(row):
                if dark:
                    p.fillRect(x * cell, y * cell, cell, cell, QColor("black"))
        p.end()
        self._label.setPixmap(pm)
        self._dlg.show()
        self._dlg.raise_()

    def pump(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

    def cancelled(self) -> bool:
        return self._closed_by_user

    def close(self):
        if self._dlg is not None:
            self._dlg.hide()


class _ConsoleQr:
    """QR-код символами в консоли (python -m core.tg_call login)."""

    def show(self, url: str):
        for row in qr_matrix(url):
            print("".join("██" if dark else "  " for dark in row))
        print("Telegram аккаунта Джарвиса → Настройки → Устройства → Подключить устройство")

    def pump(self):
        pass

    def cancelled(self) -> bool:
        return False

    def close(self):
        pass


def login_gui() -> int:
    """`JARVIS.exe --caller-login`: окна Qt вместо консоли (у .exe её нет)."""
    from PyQt6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMessageBox
    app = QApplication.instance() or QApplication([])

    def ask(text: str, secret: bool) -> str:
        val, ok = QInputDialog.getText(None, "ДЖАРВИС — звонки", text,
                                       QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal)
        return val.strip() if ok else ""

    msg = login(ask, _QrDialog())
    QMessageBox.information(None, "ДЖАРВИС — звонки", msg)
    del app
    return 0 if msg.startswith("Готово") else 1


# ── расписание ────────────────────────────────────────────────────────────────

class Schedule:
    """Звонки по расписанию: [{id, time "06:00", repeat "daily"|"once",
    date "2026-09-27" для разового, topic, last "дата последнего"}]."""

    def __init__(self, path: str | None):
        self.path = path
        self.items: list[dict] = []
        if path and os.path.isfile(path):
            try:
                import json
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                # Только записи расписания: в calls.json жила ещё и история
                # звонков ({"version", "calls"}) — её ключи становились «звонками».
                self.items = [i for i in data if isinstance(i, dict) and "time" in i] if isinstance(data, list) else []
            except Exception as exc:
                logger.warning("Расписание звонков: %s", exc)

    def _save(self):
        if self.path:
            from pathlib import Path

            from core.storage import atomic_write_json
            atomic_write_json(Path(self.path), self.items)

    def add(self, hhmm: str, topic: str, repeat: str = "once", now: datetime | None = None) -> dict:
        now = now or datetime.now()
        h, m = parse_hhmm(hhmm)
        day = now.date()
        if (h, m) <= (now.hour, now.minute):
            from datetime import timedelta
            day += timedelta(days=1)
        item = {"id": int(time.time() * 1000) % 10**9, "time": f"{h:02d}:{m:02d}",
                "repeat": "daily" if repeat == "daily" else "once", "date": day.isoformat(),
                "topic": topic or "утренний отчёт", "last": ""}
        self.items.append(item)
        self._save()
        return item

    def cancel(self, which: str = "") -> int:
        before = len(self.items)
        w = (which or "").strip()
        self.items = [i for i in self.items if w and w not in (i["time"], str(i["id"]), i["topic"])] if w else []
        self._save()
        return before - len(self.items)

    def due(self, now: datetime | None = None) -> list[dict]:
        """Звонки, чьё время пришло (в пределах 5 минут — если ПК проснулся чуть позже)."""
        now = now or datetime.now()
        out, changed = [], False
        for it in list(self.items):
            h, m = parse_hhmm(it["time"])
            at = now.replace(hour=h, minute=m, second=0, microsecond=0)
            today = now.date().isoformat()
            if it["repeat"] == "once" and it["date"] != today:
                continue
            if it.get("last") == today or not (0 <= (now - at).total_seconds() <= 300):
                continue
            it["last"] = today
            out.append(it)
            changed = True
            if it["repeat"] == "once":
                self.items.remove(it)
        if changed:
            self._save()
        return out

    def describe(self) -> str:
        if not self.items:
            return "Звонков по расписанию нет."
        return "; ".join(f"{i['time']} {'каждый день' if i['repeat'] == 'daily' else i['date']} — {i['topic']}"
                         for i in self.items)


def parse_hhmm(s: str) -> tuple[int, int]:
    m = re.search(r"(\d{1,2})(?:[:.\s](\d{2}))?", str(s or ""))
    if not m:
        raise ValueError(f"не понял время «{s}»")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"не понял время «{s}»")
    return h, mi


_schedule: Schedule | None = None


def _migrate_schedule(legacy: str, path: str) -> None:
    """Расписание и история звонков писали в один calls.json разными форматами
    и портили друг друга: расписание падало (TypeError), история обнулялась.
    Теперь расписание — в call_schedule.json; старое расписание переносим один раз.
    История (словарь {"calls": …}) остаётся в calls.json нетронутой."""
    if os.path.exists(path) or not os.path.isfile(legacy):
        return
    try:
        import json
        with open(legacy, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    if isinstance(data, list):
        items = [i for i in data if isinstance(i, dict) and "time" in i]
        if items:
            from pathlib import Path

            from core.storage import atomic_write_json
            atomic_write_json(Path(path), items)
            logger.info("Расписание звонков перенесено в %s (%d)", path, len(items))


def schedule() -> Schedule:
    global _schedule
    if _schedule is None:
        try:
            from core.paths import get_data_root
            root = str(get_data_root())
            path = os.path.join(root, "call_schedule.json")
            _migrate_schedule(os.path.join(root, "calls.json"), path)
        except Exception:
            path = None
        _schedule = Schedule(path)
    return _schedule


def _briefing() -> str:
    from actions.morning_briefing import morning_briefing
    return morning_briefing({})


def _context_for(topic: str) -> Callable[[], str] | None:
    return _briefing if re.search(r"утр|брифинг|отч[её]т|разбуд|подъ[её]м", topic or "", re.I) else None


def start_scheduler(done: Callable[[str], None] | None = None, tick_sec: float = 20):
    """Фоновая проверка расписания (Джарвис запускает при старте)."""
    def run():
        while True:
            time.sleep(tick_sec)
            try:
                for it in schedule().due():
                    logger.info("Звонок по расписанию: %s %s", it["time"], it["topic"])
                    call_in_background(it["topic"], _context_for(it["topic"]), done)
            except Exception as exc:
                logger.warning("Расписание звонков: %s", exc)
    threading.Thread(target=run, daemon=True, name="call-scheduler").start()


# ── инструмент ────────────────────────────────────────────────────────────────

def phone_call(parameters: dict, player=None, done: Callable[[str], None] | None = None) -> str:
    p = parameters or {}
    action = (p.get("action") or "call_now").lower()
    topic = (p.get("topic") or "").strip()
    if action in ("transcript", "history"):
        from core.call_log import transcript_tool
        return transcript_tool({**p, "which": "list" if action == "history" else p.get("which", "")})
    if action == "list":
        return schedule().describe()
    if action == "cancel":
        n = schedule().cancel(p.get("time") or "")
        return f"Отменил звонков: {n}." if n else "Отменять нечего."
    problem = ready()
    if action == "schedule":
        minutes = p.get("in_minutes")
        try:
            if minutes:
                from datetime import timedelta
                at = datetime.now() + timedelta(minutes=float(minutes))
                item = schedule().add(at.strftime("%H:%M"), topic, "once")
            else:
                item = schedule().add(p.get("time") or "", topic, p.get("repeat") or "once")
        except ValueError as exc:
            return f"Сэр, {exc}."
        when = "каждый день" if item["repeat"] == "daily" else (
            "завтра" if item["date"] != datetime.now().date().isoformat() else "сегодня")
        return (f"Позвоню {when} в {item['time']} — {item['topic']}."
                + (f" Но сначала: {problem}" if problem else " Компьютер в это время должен быть включён."))
    if problem:
        return problem
    call_in_background(topic or "вы попросили позвонить", _context_for(topic), done)
    return "Звоню вам в Telegram, сэр."


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        import getpass
        print(login(lambda text, secret: (getpass.getpass if secret else input)(text + " ").strip(),
                    _ConsoleQr()))
    else:
        print(call(" ".join(sys.argv[1:]) or "проверка связи"))
