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
ANSWER_TIMEOUT = 45
MAX_CALL_SEC = 10 * 60

_call_lock = threading.Lock()


# ── звук ──────────────────────────────────────────────────────────────────────

class Downsampler:
    """48 кГц → 16 кГц: среднее по тройкам (простой фильтр от наложения).
    Хвост, не кратный трём, ждёт следующего куска."""

    def __init__(self):
        self._tail = b""

    def __call__(self, pcm: bytes) -> bytes:
        import numpy as np
        data = self._tail + pcm
        n = (len(data) // 6) * 6
        self._tail = data[n:]
        if not n:
            return b""
        x = np.frombuffer(data[:n], dtype="<i2").astype(np.int32).reshape(-1, 3)
        return x.mean(axis=1).astype("<i2").tobytes()


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
    "description": "Положить трубку. Вызывай, когда разговор окончен и вы попрощались.",
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
        "без списков и разметки. Начни сам: поздоровайся и скажи, зачем звонишь. "
        "Отвечай на вопросы. Когда разговор окончен и вы попрощались — вызови end_call."
    )


class CallSession:
    """Один звонок: дозвон → разговор с Gemini → отбой.

    tg — обёртка над звонком (ring/listen/send/hangup, колбэки on_audio и
    on_hangup), live — фабрика Live-сессии Gemini (async context manager).
    Обе подменяются в тестах."""

    def __init__(self, tg, live: Callable, peer, prompt: str, max_sec: float = MAX_CALL_SEC,
                 log: Callable[[str], None] | None = None):
        self.tg, self.live, self.peer, self.prompt = tg, live, peer, prompt
        self.max_sec = max_sec
        self.log = log or (lambda s: logger.info("Звонок: %s", s))
        self.out = OutBuffer()
        self.up, self.down = Upsampler(), Downsampler()
        self.transcript: list[str] = []
        self._mic: asyncio.Queue[bytes] | None = None
        self._hung_up = asyncio.Event()
        self._ending = False

    # колбэки из py-tgcalls (его цикл — тот же, что у нас)
    def _on_audio(self, pcm48: bytes):
        if self._mic is not None:
            chunk = self.down(pcm48)
            if chunk:
                self._mic.put_nowait(chunk)

    def _on_hangup(self):
        self._hung_up.set()

    async def run(self) -> str:
        self._mic = asyncio.Queue()
        self.tg.on_audio = self._on_audio
        self.tg.on_hangup = self._on_hangup
        try:
            await self.tg.ring(self.peer)
        except Exception as exc:
            return _ring_error(exc)
        started = time.monotonic()
        self.log("взял трубку")
        try:
            await self.tg.listen(self.peer)
            async with self.live(self.prompt) as session:
                await session.send_client_content(
                    turns=[{"role": "user", "parts": [{"text": "[Собеседник взял трубку. Начинай разговор.]"}]}],
                    turn_complete=True)
                pace = asyncio.create_task(self._pace())
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
                for t in pumps | {pace, ended}:
                    t.cancel()
        finally:
            if not self._hung_up.is_set():
                try:
                    await self.tg.hangup(self.peer)
                except Exception as exc:
                    logger.debug("Отбой: %s", exc)
        mins = (time.monotonic() - started) / 60
        who = "вы положили трубку" if self._hung_up.is_set() and not self._ending else "попрощались"
        return f"Поговорили {max(1, round(mins))} мин, {who}."

    async def _pump_mic(self, session):
        from google.genai import types
        while True:
            chunk = await self._mic.get()
            await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={IN_RATE}"))

    async def _pump_gemini(self, session):
        from google.genai import types
        while True:
            async for msg in session.receive():
                sc = getattr(msg, "server_content", None)
                if sc is not None and getattr(sc, "interrupted", False):
                    self.out.clear()                      # перебили — замолкаем сразу
                    self.up.reset()
                if getattr(msg, "data", None):
                    self.out.push(self.up(msg.data))
                if sc is not None:
                    for attr, who in (("input_transcription", "Вы"), ("output_transcription", "Джарвис")):
                        tr = getattr(sc, attr, None)
                        if tr is not None and getattr(tr, "text", None):
                            self.transcript.append(f"{who}: {tr.text}")
                tc = getattr(msg, "tool_call", None)
                if tc is not None:
                    replies = []
                    for fc in tc.function_calls or []:
                        if fc.name == "end_call":
                            self._ending = True
                        replies.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"ok": True}))
                    if replies:
                        await session.send_tool_response(function_responses=replies)
                    if self._ending:
                        return

    async def _pace(self):
        """Кадр каждые 10 мс по часам, а не по sleep: на Windows sleep
        дрожит на ~15 мс, поэтому отстающие кадры досылаются пачкой."""
        next_t = time.monotonic()
        idle_after_end = 0
        while True:
            now = time.monotonic()
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


def _ring_error(exc: BaseException) -> str:
    kind = type(exc).__name__
    if kind in ("TimedOutAnswer", "TimeoutError"):
        return "Вы не взяли трубку."
    if kind == "CallDeclined":
        return "Вы сбросили звонок."
    if kind == "CallBusy":
        return "Линия занята — вы на другом звонке."
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
        await self.app.leave_call(peer)


async def resolve_peer(client, target: str) -> int:
    """@username, ссылка t.me или номер телефона → id пользователя."""
    t = (target or "").strip()
    t = re.sub(r"^(https?://)?t\.me/", "@", t)
    digits = re.sub(r"[^\d+]", "", t)
    if digits.lstrip("+").isdigit() and len(digits.lstrip("+")) >= 9 and not t.startswith("@"):
        from telethon.tl.functions.contacts import ImportContactsRequest
        from telethon.tl.types import InputPhoneContact
        phone = digits if digits.startswith("+") else "+" + digits
        res = await client(ImportContactsRequest([InputPhoneContact(0, phone, "Сэр", "")]))
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
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon"))),
    )
    model = os.getenv("JARVIS_LIVE_MODEL", "models/gemini-2.5-flash-native-audio-latest")
    return client.aio.live.connect(model=model, config=config)


async def _call_async(topic: str, context: str, log) -> str:
    from telethon import TelegramClient
    api_id, api_hash = _credentials()
    client = TelegramClient(session_path(), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return "Аккаунт Джарвиса для звонков вышел из сессии: запустите JARVIS.exe --caller-login."
        peer = await resolve_peer(client, str(_keys().get("call_to", "")))
        tg = TgCall(client)
        await tg.start()
        name = (_keys().get("user_name") or "сэр")
        sess = CallSession(tg, _gemini_live, peer, instruction(topic, context, name), log=log)
        return await sess.run()
    finally:
        await client.disconnect()


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
                    self.items = list(json.load(f))
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


def schedule() -> Schedule:
    global _schedule
    if _schedule is None:
        try:
            from core.paths import get_data_root
            path = os.path.join(str(get_data_root()), "calls.json")
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
