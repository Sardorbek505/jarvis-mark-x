"""Перерывы («вы смотрите уже 2 часа») и звонок Джарвиса в Telegram.

Звонок проверяется на подменённых Telegram и Gemini: дозвон, звук в обе
стороны с пересэмплированием, перебивание, end_call → отбой, «не взял
трубку». Настоящий звонок здесь не сделать — нужен второй аккаунт."""
import asyncio
import time
from datetime import datetime

import numpy as np
import pytest

from core import break_reminder as br
from core import tg_call as tc

# ── перерывы ─────────────────────────────────────────────────────────────────


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 26, 20, 0).timestamp()

    def __call__(self):
        return self.t

    def run(self, rem, minutes, act):
        """Прожить `minutes` минут с тактом 30 с; вернуть сказанное."""
        said = []
        for _ in range(int(minutes * 2)):
            self.t += 30
            rem._sense = lambda: act
            p = rem.tick()
            if p:
                said.append((round((self.t - start[0]) / 60), p))
        return said


start = [0.0]


def make(tmp_path=None):
    clock = Clock()
    start[0] = clock.t
    spoken = []
    rem = br.BreakReminder(spoken.append, sense=lambda: None, clock=clock,
                           settings_path=str(tmp_path / "br.json") if tmp_path else None)
    return rem, clock, spoken


def test_film_reminder_at_two_hours_then_every_hour():
    rem, clock, spoken = make()
    film = br.Activity("video", "Железный человек 2")
    said = clock.run(rem, 4 * 60 + 5, film)
    assert [m for m, _ in said] == [120, 180, 240]
    assert "2 часа" in said[0][1] and "Поставить на паузу" in said[0][1] and "video_control" in said[0][1]
    assert "Железный человек 2" in said[0][1]
    assert spoken == [p for _, p in said]


def test_game_reminder_does_not_touch_the_game():
    rem, clock, _ = make()
    said = clock.run(rem, 121, br.Activity("game", "Counter-Strike 2"))
    assert len(said) == 1
    p = said[0][1]
    assert "Counter-Strike 2" in p and "Ничего не нажимай" in p and "video_control" not in p


def test_short_break_keeps_session_long_break_resets():
    rem, clock, _ = make()
    film = br.Activity("video", "")
    assert clock.run(rem, 100, film) == []
    clock.run(rem, 5, None)                     # вышел на 5 минут — сессия та же
    said = clock.run(rem, 20, film)
    assert said and 115 <= said[0][0] <= 126
    rem2, clock2, _ = make()
    clock2.run(rem2, 100, film)
    clock2.run(rem2, 15, None)                  # 15 минут отдыха — это перерыв
    assert clock2.run(rem2, 60, film) == []


def test_off_today_and_custom_interval(tmp_path):
    rem, clock, spoken = make(tmp_path)
    assert "сегодня" in rem.command("off_today")
    clock.run(rem, 130, br.Activity("video", ""))
    assert spoken == []
    rem, clock, spoken = make(tmp_path)          # настройки с диска: всё ещё сегодня
    assert "до завтра" in rem.command("status")
    assert "1 час" in rem.command("set", first_min=60, repeat_min=30)
    said = clock.run(rem, 125, br.Activity("game", "Dota 2"))
    assert [m for m, _ in said] == [60, 90, 120]


def test_game_detection_by_exe_and_folder():
    assert br.game_name(r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs2.exe") == "Counter-Strike 2"
    assert br.game_name(r"D:\SteamLibrary\steamapps\common\Hades II\Hades2.exe") == "Hades II"
    assert br.game_name(r"C:\Program Files (x86)\Steam\steam.exe") is None
    assert br.game_name(r"C:\Program Files\Google\Chrome\Application\chrome.exe") is None
    assert br.game_name("") is None


def test_duration_words():
    assert br.say_duration(120) == "2 часа"
    assert br.say_duration(125) == "2 часа 5 минут"
    assert br.say_duration(61) == "1 час"
    assert br.say_duration(300) == "5 часов"
    assert br.say_duration(21) == "21 минуту"


# ── звук звонка ──────────────────────────────────────────────────────────────

def sine(rate, sec, hz=440):
    t = np.arange(int(rate * sec)) / rate
    return (np.sin(2 * np.pi * hz * t) * 8000).astype("<i2")


def test_resampling_keeps_tone_and_length_across_chunks():
    down, up = tc.Downsampler(), tc.Upsampler()
    x48 = sine(48000, 1.0).tobytes()
    parts = [x48[i:i + 1001] for i in range(0, len(x48), 1001)]     # куски некратной длины
    y16 = b"".join(down(p) for p in parts)
    assert len(y16) == 16000 * 2
    x24 = sine(24000, 1.0).tobytes()
    y48 = b"".join(up(x24[i:i + 4800]) for i in range(0, len(x24), 4800))
    assert len(y48) == 48000 * 2
    spec = np.abs(np.fft.rfft(np.frombuffer(y48, "<i2")))
    assert abs(np.argmax(spec) - 440) <= 1                            # тон на месте
    d = np.abs(np.diff(np.frombuffer(y48, "<i2").astype(int)))
    assert d.max() < 600         # шаг чистого синуса ~460; щелчок на стыке дал бы тысячи


def test_out_buffer_frames():
    b = tc.OutBuffer()
    b.push(b"\1" * (tc.FRAME_BYTES * 2 + 10))
    assert b.pop() and b.pop() and b.pop() is None
    tail = b.flush_tail()
    assert len(tail) == tc.FRAME_BYTES and tail[:10] == b"\1" * 10 and b.flush_tail() is None


# ── звонок на подменах ───────────────────────────────────────────────────────

class FakeTg:
    def __init__(self, answer=True, error=None):
        self.answer, self.error = answer, error
        self.sent: list[bytes] = []
        self.hung = False
        self.on_audio = self.on_hangup = None

    async def ring(self, peer):
        if self.error:
            raise self.error

    async def listen(self, peer):
        # человек говорит полсекунды: 50 кадров по 10 мс
        async def talk():
            for _ in range(50):
                self.on_audio(sine(48000, 0.01).tobytes())
                await asyncio.sleep(0)
        asyncio.get_running_loop().create_task(talk())

    async def send(self, peer, frame):
        assert len(frame) == tc.FRAME_BYTES
        self.sent.append(frame)

    async def hangup(self, peer):
        self.hung = True


class Msg:
    def __init__(self, data=None, interrupted=False, end=False):
        self.data = data
        self.server_content = type("SC", (), {"interrupted": interrupted, "input_transcription": None,
                                              "output_transcription": None})()
        self.tool_call = None
        if end:
            fc = type("FC", (), {"name": "end_call", "id": "1"})()
            self.tool_call = type("TC", (), {"function_calls": [fc]})()


class FakeLive:
    def __init__(self, script):
        self.script, self.audio_in, self.texts, self.tool_resp = script, 0, [], []

    def __call__(self, prompt):
        self.prompt = prompt
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send_client_content(self, turns, turn_complete):
        self.texts.append(turns)

    async def send_realtime_input(self, audio):
        assert audio.mime_type == "audio/pcm;rate=16000"
        self.audio_in += len(audio.data)

    async def send_tool_response(self, function_responses):
        self.tool_resp += function_responses

    def receive(self):
        script = self.script

        async def gen():
            while script:
                await asyncio.sleep(0.005)
                yield script.pop(0)
        return gen()


def test_call_talks_both_ways_and_hangs_up_after_goodbye():
    tg = FakeTg()
    speech = sine(24000, 0.3).tobytes()                   # 0,3 с ответа Джарвиса
    live = FakeLive([Msg(speech[:4800]), Msg(speech[4800:]), Msg(end=True)])
    sess = tc.CallSession(tg, live, 42, tc.instruction("утренний отчёт", "Погода: +12"), max_sec=5)
    t0 = time.monotonic()
    result = asyncio.run(sess.run())
    assert "попрощались" in result and tg.hung
    assert "утренний отчёт" in live.prompt and "Погода: +12" in live.prompt
    assert live.texts                                     # Джарвис начал разговор сам
    assert live.audio_in == 16000 * 2 * 0.5               # 0,5 с голоса ушли в Gemini в 16 кГц
    voiced = [f for f in tg.sent if any(f)]
    assert len(voiced) == 30                              # 0,3 с ответа = 30 кадров по 10 мс
    assert live.tool_resp and live.tool_resp[0].name == "end_call"
    assert time.monotonic() - t0 < 3


def test_interrupt_drops_queued_speech():
    tg = FakeTg()
    long = sine(24000, 2.0).tobytes()
    live = FakeLive([Msg(long), Msg(interrupted=True), Msg(end=True)])
    asyncio.run(tc.CallSession(tg, live, 42, "x", max_sec=5).run())
    assert len([f for f in tg.sent if any(f)]) < 50       # из 200 кадров почти всё сброшено


@pytest.mark.parametrize("err,text", [("TimedOutAnswer", "не взяли"), ("CallDeclined", "сбросили"),
                                      ("CallBusy", "занята")])
def test_not_answered(err, text):
    class Ringing(FakeTg):
        async def ring(self, peer):
            await asyncio.sleep(0.05)                     # гудки, Gemini тем временем подключился
            raise type(err, (Exception,), {})()

    class Live(FakeLive):
        closed = False

        async def __aexit__(self, *a):
            Live.closed = True
    live = Live([])
    assert text in asyncio.run(tc.CallSession(Ringing(), live, 42, "x").run())
    assert Live.closed                                    # трубку не взяли — Gemini закрыт


def test_user_hangs_up():
    tg = FakeTg()

    class Silent(FakeLive):
        def receive(self):
            async def gen():
                await asyncio.sleep(0.05)
                tg.on_hangup()
                await asyncio.sleep(10)
                yield None
            return gen()
    assert "вы положили трубку" in asyncio.run(tc.CallSession(tg, Silent([]), 42, "x", max_sec=5).run())
    assert not tg.hung                                    # класть уже нечего


# ── расписание и инструмент ─────────────────────────────────────────────────

def test_schedule_once_and_daily(tmp_path):
    s = tc.Schedule(str(tmp_path / "calls.json"))
    now = datetime(2026, 9, 26, 22, 0)
    once = s.add("06:00", "утренний отчёт", "once", now=now)
    assert once["date"] == "2026-09-27"
    s.add("7:30", "подъём", "daily", now=now)
    assert s.due(datetime(2026, 9, 27, 5, 59)) == []
    assert [i["topic"] for i in s.due(datetime(2026, 9, 27, 6, 1))] == ["утренний отчёт"]
    assert s.due(datetime(2026, 9, 27, 6, 2)) == []                   # один раз
    assert [i["topic"] for i in s.due(datetime(2026, 9, 27, 7, 31))] == ["подъём"]
    assert s.due(datetime(2026, 9, 27, 7, 33)) == []
    assert s.due(datetime(2026, 9, 27, 7, 40)) == []                  # опоздание больше 5 мин
    assert [i["topic"] for i in tc.Schedule(str(tmp_path / "calls.json")).due(datetime(2026, 9, 28, 7, 30))] == ["подъём"]
    assert "07:30" in s.describe() and s.cancel("07:30") == 1 and s.describe() == "Звонков по расписанию нет."


def test_morning_topic_reads_briefing():
    assert tc._context_for("утренний отчёт") is tc._briefing
    assert tc._context_for("разбуди меня") is tc._briefing
    assert tc._context_for("напомнить про встречу") is None


def test_tool_without_account_says_what_to_do(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "_keys", lambda: {"telethon_api_id": "1", "telethon_api_hash": "h"})
    monkeypatch.setattr(tc, "session_path", lambda: str(tmp_path / "nope"))
    monkeypatch.setattr(tc, "_schedule", tc.Schedule(str(tmp_path / "c.json")))
    assert "--caller-login" in tc.phone_call({"action": "call_now"})
    out = tc.phone_call({"action": "schedule", "time": "06:00", "topic": "утренний отчёт"})
    assert "06:00" in out and "--caller-login" in out
    assert "06:00" in tc.phone_call({"action": "list"})
    assert tc.phone_call({"action": "schedule", "time": "25:99"}).startswith("Сэр, не понял время")


def test_real_pytgcalls_objects_build():
    """Настоящие объекты py-tgcalls для внешнего звука собираются (API не уехал)."""
    pytest.importorskip("pytgcalls")
    from pytgcalls.methods.utilities.stream_params import StreamParams
    from pytgcalls.types import MediaStream, RecordStream
    from pytgcalls.types.raw import AudioParameters
    from pytgcalls.types.stream.external_media import ExternalMedia

    async def build():
        await StreamParams.get_stream_params(MediaStream(ExternalMedia.AUDIO, AudioParameters(48000, 1),
                                                         video_flags=MediaStream.Flags.IGNORE))
        await StreamParams.get_record_params(RecordStream(audio=True, audio_parameters=AudioParameters(48000, 1)))
    asyncio.run(build())


def test_tgcall_wraps_real_pytgcalls(tmp_path):
    """Обёртка собирается на настоящем Telethon + py-tgcalls (без сети)."""
    pytest.importorskip("pytgcalls")
    telethon = pytest.importorskip("telethon")

    async def build():
        t = tc.TgCall(telethon.TelegramClient(str(tmp_path / "s"), 1, "x"))
        assert hasattr(t.app, "send_frame") and hasattr(t.app, "record")
    asyncio.run(build())


# ── вход в аккаунт Джарвиса по QR-коду ────────────────────────────────────────

class _View:
    def __init__(self, cancel_after=None):
        self.shown, self.closed, self._pumps, self._cancel_after = [], False, 0, cancel_after

    def show(self, url):
        self.shown.append(url)

    def pump(self):
        self._pumps += 1

    def cancelled(self):
        return self._cancel_after is not None and self._pumps >= self._cancel_after

    def close(self):
        self.closed = True


class _QR:
    def __init__(self, outcomes):
        self.outcomes, self.n = list(outcomes), 0
        self.url = "tg://login?token=t0"

    async def wait(self, timeout=None):
        self.timeouts = getattr(self, "timeouts", []) + [timeout]
        await asyncio.sleep(0.01)
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out

    async def recreate(self):
        self.n += 1
        self.url = f"tg://login?token=t{self.n}"


class _Client:
    def __init__(self, qr):
        self.qr, self.password = qr, None

    async def qr_login(self):
        return self.qr

    async def sign_in(self, password=None):
        self.password = password


def test_qr_login_refreshes_expired_code_then_signs_in():
    """Живой случай: код по номеру не пришёл ни разу из четырёх. QR не
    зависит от доставки кода; просроченный QR показываем заново."""
    qr = _QR([asyncio.TimeoutError(), "user"])
    view = _View()
    assert asyncio.run(tc._qr_sign_in(_Client(qr), view, lambda t, s: "")) is True
    assert view.shown == ["tg://login?token=t0", "tg://login?token=t1"] and view.closed
    assert qr.timeouts == [tc._QR_REFRESH_SEC] * 2        # срок — свой, не по часам ПК


def test_qr_login_asks_two_step_password():
    from telethon.errors import SessionPasswordNeededError
    client = _Client(_QR([SessionPasswordNeededError(request=None)]))
    assert asyncio.run(tc._qr_sign_in(client, _View(), lambda t, s: "секрет" if s else "")) is True
    assert client.password == "секрет"


def test_qr_login_cancel():
    class _Slow(_QR):
        async def wait(self, timeout=None):
            await asyncio.sleep(10)

    view = _View(cancel_after=3)
    assert asyncio.run(tc._qr_sign_in(_Client(_Slow([])), view, lambda t, s: "")) is False
    assert view.closed


def test_qr_matrix_is_square_with_dark_cells():
    m = tc.qr_matrix("tg://login?token=AQIDBAUGBwgJCgsMDQ4PEA")
    assert len(m) == len(m[0]) >= 25 and any(any(r) for r in m)


# ─── Огрызок сессии после неудачного входа ───────────────────────────────────
class _StubClient:
    """Телеграм-клиент, в который так и не вошли."""

    def __init__(self, *a, **kw):
        self.disconnected = False

    async def connect(self):
        return None

    async def is_user_authorized(self):
        return False

    async def disconnect(self):
        self.disconnected = True


def _prepare_login(monkeypatch, tmp_path):
    import telethon
    monkeypatch.setattr(tc, "_credentials", lambda: (1, "hash"))
    monkeypatch.setattr(tc, "session_path", lambda: str(tmp_path / "jarvis_caller"))
    monkeypatch.setattr(telethon, "TelegramClient", _StubClient)
    stub = tmp_path / "jarvis_caller.session"
    stub.write_bytes(b"SQLite format 3\x00")          # как его оставляет telethon
    return stub


def test_aborted_login_does_not_leave_a_session_stub(monkeypatch, tmp_path):
    """Живой случай: вход прервали, файл сессии остался — и ready() доложил,
    что аккаунт подключён, хотя авторизации не было."""
    stub = _prepare_login(monkeypatch, tmp_path)

    answer = tc.login(lambda text, secret: "")        # отказ на первом же вопросе

    assert "отмен" in answer.lower(), answer
    assert not stub.exists(), "огрызок сессии остался и врёт про подключённый аккаунт"


def test_successful_login_keeps_the_session(monkeypatch, tmp_path):
    stub = _prepare_login(monkeypatch, tmp_path)

    class _Ok(_StubClient):
        async def is_user_authorized(self):
            return True

        async def get_me(self):
            return type("Me", (), {"first_name": "Джарвис", "phone": "77014815055"})()

    import telethon
    monkeypatch.setattr(telethon, "TelegramClient", _Ok)

    answer = tc.login(lambda text, secret: "")        # цель не указали — и не надо

    assert answer.startswith("Готово"), answer
    assert stub.exists(), "рабочую сессию удалять нельзя"


def test_call_logs_what_came_from_the_phone(caplog):
    """«Говорит, но не отвечает»: журнал обязан показать, дошёл ли голос."""
    import logging
    s = tc.CallSession(tg=None, live=None, peer=1, prompt="")
    s._on_audio((np.full(480, 3000, dtype="<i2")).tobytes())
    s.transcript.append("Вы: алло")
    st = s.audio_stats()
    assert "кадров: 1" in st and "960" in st and "RMS: 3000" in st and "собеседника в расшифровке: 1" in st

    silent = tc.CallSession(tg=None, live=None, peer=1, prompt="")
    with caplog.at_level(logging.WARNING, logger=tc.logger.name):
        asyncio.run(silent._watch_audio(after=0.01))
    assert "не пришло ни одного кадра" in caplog.text


def _heard(text):
    m = Msg()
    m.server_content.input_transcription = type("T", (), {"text": text})()
    return m


def test_keeps_talking_when_user_answers_after_goodbye():
    """Живой случай: «позвони, скажи, что пора спать» — Джарвис сказал и
    замолчал; на «привет, как дела» и «пока, отключись» — тишина. Прощание
    Джарвиса не повод класть трубку, пока собеседник говорит."""
    tg = FakeTg()
    speech = sine(24000, 0.2).tobytes()
    live = FakeLive([Msg(speech), Msg(end=True), _heard("привет, как дела?"),
                     Msg(speech), _heard("пока, отключись"), Msg(end=True)])
    sess = tc.CallSession(tg, live, 42, tc.instruction("сказать, что пора спать"), max_sec=5)
    asyncio.run(sess.run())
    assert tg.hung and not live.script                    # дослушал до конца и только потом отбой
    assert any(t.startswith("Вы:") and "привет, как дела?" in t for t in sess.transcript)
    voiced = [f for f in tg.sent if any(f)]
    assert len(voiced) == 40                              # оба ответа прозвучали


def test_call_prompt_forbids_hanging_up_after_own_message():
    p = tc.instruction("сказать, что пора спать")
    assert "НЕ клади трубку" in p and "ТОЛЬКО когда собеседник сам попрощался" in p
    assert "Не вызывай сразу" in tc.END_CALL["description"]


def test_hangup_falls_back_to_discard_when_leave_call_fails():
    called = []

    class _Inner:
        async def discard_call(self, chat_id, video):
            called.append(chat_id)

    class _App:
        _app = _Inner()

        async def leave_call(self, peer):
            raise RuntimeError("связь уже остановлена")

        async def resolve_chat_id(self, peer):
            return peer

    t = tc.TgCall.__new__(tc.TgCall)
    t.app = _App()
    asyncio.run(t.hangup(42))
    assert called == [42]


def _said(text, who="input_transcription", done=False):
    m = Msg()
    setattr(m.server_content, who, type("T", (), {"text": text})())
    m.server_content.turn_complete = done
    return m


def test_late_goodbye_transcript_does_not_cancel_hangup():
    """Живой случай: «пока» → Джарвис «пока» + end_call, а расшифровка «пока»
    пришла позже — раньше это отменяло отбой, и звонок висел."""
    tg = FakeTg()
    speech = sine(24000, 0.2).tobytes()
    live = FakeLive([Msg(speech), Msg(end=True), _said(" пока"), _said(", спасибо")])
    sess = tc.CallSession(tg, live, 42, "x", max_sec=5)
    asyncio.run(sess.run())
    assert tg.hung and sess.ended_by == "end_call"


def test_hangs_up_after_goodbye_even_without_end_call():
    """Gemini попрощался голосом, но end_call не вызвал — кладём трубку сами."""
    tg = FakeTg()
    speech = sine(24000, 0.2).tobytes()
    live = FakeLive([_said("Ладно, пока!"), Msg(speech), _said("До свидания, сэр.", "output_transcription", done=True)])
    t0 = time.monotonic()
    sess = tc.CallSession(tg, live, 42, "x", max_sec=5)
    asyncio.run(sess.run())
    assert tg.hung and sess.ended_by == "попрощались (без end_call)" and time.monotonic() - t0 < 3


def test_silence_after_goodbye_hangs_up(monkeypatch):
    monkeypatch.setattr(tc, "BYE_SILENCE_SEC", 0.6)
    tg = FakeTg()
    live = FakeLive([_said("всё, пока")])                   # Джарвис молчит, end_call не пришёл
    sess = tc.CallSession(tg, live, 42, "x", max_sec=5)
    asyncio.run(sess.run())
    assert tg.hung and sess.ended_by == "попрощались и тишина"


def test_what_counts_as_goodbye_and_continuing():
    assert tc.says_bye("Ну всё, пока") and tc.says_bye("До свидания!") and tc.says_bye("положи трубку")
    s = tc.CallSession(tg=None, live=None, peer=1, prompt="")
    s._heard("Вы", "пока не знаю, подумаю")                       # «пока» не в конце — не прощание
    assert s._user_bye_at == 0
    s._heard("Вы", ". Ладно, пока!")
    assert s._user_bye_at > 0
    for late in (" пока", "спасибо, пока", "угу", "ладно, давай", "и тебе хорошего вечера", "по", "ка"):
        assert not tc.keeps_talking(late), late
    for more in ("привет, как дела?", "стой", "подожди, ещё вопрос", "а завтра во сколько встреча"):
        assert tc.keeps_talking(more), more


def test_contact_call_goes_through_your_telegram(monkeypatch):
    """Звонок контакту с вашего аккаунта (core/contacts.Me): свой клиент,
    один py-tgcalls на клиента, разговор записан в историю звонков."""
    from core.call_log import call_log

    class Me:
        def __init__(self):
            self.client = object()

        def run(self, fn, timeout=40):
            return asyncio.run(fn(self.client))

    made = []

    class Tg(FakeTg):
        def __init__(self, client):
            super().__init__()
            self.client = client
            made.append(self)

        async def start(self):
            pass

    async def peer(client, target, name=""):
        assert target == "id:22" and name == "Азиз"
        return 22
    speech = sine(24000, 0.2).tobytes()
    monkeypatch.setattr(tc, "TgCall", Tg)
    monkeypatch.setattr(tc, "resolve_peer", peer)
    monkeypatch.setattr(tc, "_credentials", lambda: (1, "h"))
    monkeypatch.setattr(tc, "_gemini_live", FakeLive([Msg(speech), _said(" Иду!"), Msg(end=True)]))
    me = Me()
    res = tc.call_contact("id:22", "Азиз", "Ужин готов", via=me)
    assert "Азиз ответил: «Иду!»" in res and made and made[0].hung
    assert call_log().find("Азиз")["lines"][-1] == {"who": "Азиз", "text": "Иду!"}
    monkeypatch.setattr(tc, "_gemini_live", FakeLive([Msg(end=True)]))
    tc.call_contact("id:22", "Азиз", "ещё", via=me)
    assert len(made) == 1                                   # py-tgcalls не создаётся заново


def test_call_waits_short_pause_before_answering(monkeypatch):
    """Звонок: «договорил» — после 450 мс тишины, с высокой чувствительностью
    к концу речи (как голосовой Джарвис на ПК), а не по умолчанию ~секунду."""
    captured = {}

    class Live:
        def connect(self, model, config):
            captured["config"] = config

    class Client:
        def __init__(self, **kw):
            self.aio = type("A", (), {"live": Live()})()
    import core.onboarding as onboarding
    from google import genai
    monkeypatch.setattr(genai, "Client", Client)
    monkeypatch.setattr(onboarding, "ensure_gemini_key", lambda interactive=False: "k")
    tc._gemini_live("x")
    vad = captured["config"].realtime_input_config.automatic_activity_detection
    assert vad.silence_duration_ms == tc.CALL_VAD_SILENCE_MS == 450
    assert vad.end_of_speech_sensitivity.name == "END_SENSITIVITY_HIGH"
    # раздумья выключены, как на ПК: с ними каждая реплика в трубке ждала ~2,8 с
    assert captured["config"].thinking_config.thinking_budget == 0


def test_call_logs_reply_delay_and_send_lateness():
    tg = FakeTg()
    speech = sine(24000, 0.2).tobytes()
    live = FakeLive([_said("Привет, как дела?"), Msg(speech), Msg(end=True)])
    sess = tc.CallSession(tg, live, 42, "x", max_sec=5)
    asyncio.run(sess.run())
    assert len(sess.reply_delays) == 1 and 0 <= sess.reply_delays[0] < 1
    stats = sess.audio_stats()
    assert "ответ через: медиана" in stats and "опоздание отправки звука" in stats


def test_call_logs_when_jarvis_starts_speaking(caplog):
    """«В звонке он вообще не говорит» — теперь в журнале видно, заговорил ли
    Джарвис и через сколько после ответа."""
    import logging
    tg = FakeTg()
    speech = sine(24000, 0.2).tobytes()
    sess = tc.CallSession(tg, FakeLive([Msg(speech), Msg(end=True)]), 42, "x", max_sec=5)
    with caplog.at_level(logging.INFO, logger="core.tg_call"):
        asyncio.run(sess.run())
    assert sess.first_voice_sec > 0 and sess.gemini_msgs >= 2
    assert "Gemini на связи через" in caplog.text and "Джарвис заговорил через" in caplog.text


def test_call_survives_google_1011_on_connect(monkeypatch):
    """Журнал 02.10, 21:00: «1011 service unavailable» при подключении Gemini ронял
    весь звонок. Временный сбой — пробуем ещё раз, и разговор идёт."""
    monkeypatch.setattr(tc.asyncio, "sleep", _fast_sleep)

    class Flaky(FakeLive):
        tries = 0

        async def __aenter__(self):
            Flaky.tries += 1
            if Flaky.tries == 1:
                raise RuntimeError("1011 None. The service is currently unavailable.")
            return self
    tg = FakeTg()
    live = Flaky([Msg(end=True)])
    result = asyncio.run(tc.CallSession(tg, live, 42, "x", max_sec=5).run())
    assert Flaky.tries == 2 and "Поговорили" in result


def test_quota_error_is_not_retried():
    class Quota(FakeLive):
        tries = 0

        async def __aenter__(self):
            Quota.tries += 1
            raise ConnectionError("1008 quota")
    with pytest.raises(ConnectionError):
        asyncio.run(tc.CallSession(FakeTg(), Quota([]), 42, "x", max_sec=5).run())
    assert Quota.tries == 1


def test_voice_is_saved_even_when_gemini_fails(tmp_path, monkeypatch):
    """Запись голоса нужнее всего именно после сбоя — раньше она писалась только
    после нормального звонка."""
    monkeypatch.setenv("JARVIS_CALL_REC_DIR", str(tmp_path))
    monkeypatch.setattr(tc.asyncio, "sleep", _fast_sleep)

    class Down(FakeLive):
        async def __aenter__(self):
            for _ in range(20):
                self_tg.on_audio(sine(48000, 0.01).tobytes())     # человек уже говорит в трубку
            raise RuntimeError("1011 service unavailable")
    self_tg = FakeTg()
    with pytest.raises(RuntimeError):
        asyncio.run(tc.CallSession(self_tg, Down([]), 42, "x", max_sec=5).run())
    assert (tmp_path / tc.REC_NAMES[0]).is_file()


_real_sleep = asyncio.sleep


async def _fast_sleep(sec, *a, **k):
    await _real_sleep(min(sec, 0.01))


def test_call_gemini_connect_failure_is_logged_and_hung_up(caplog):
    """Трубку взяли, Gemini не подключился — раньше человек слышал тишину без причины в журнале."""
    import logging

    class Broken(FakeLive):
        async def __aenter__(self):
            raise ConnectionError("1008 quota")
    tg = FakeTg()
    sess = tc.CallSession(tg, Broken([]), 42, "x", max_sec=5)
    with caplog.at_level(logging.INFO, logger="core.tg_call"), pytest.raises(ConnectionError):
        asyncio.run(sess.run())
    assert "Gemini не подключился — ConnectionError: 1008 quota" in caplog.text
    assert tg.hung                                              # трубку положили, а не молчим


# ── голос Fish и подключение во время гудков ────────────────────────────────

def _fish(level=3000, delay=0.0, spoken=None, fail=False):
    """Подмена tts_fish.stream_pcm: 0,1 с ровного тона на каждый кусок."""
    async def speak(text):
        if spoken is not None:
            spoken.append(text)
        await asyncio.sleep(delay)
        if fail:
            raise RuntimeError("402 Payment Required")
        yield (np.ones(2400) * level).astype("<i2").tobytes()
    return speak


def _levels(tg):
    return {int(np.abs(np.frombuffer(f, "<i2")).max()) for f in tg.sent if any(f)}


def test_call_speaks_with_fish_voice_not_gemini():
    """«Почему он на Fish Audio не говорит при звонке» — говорит Fish по расшифровке
    ответа, звук Gemini (Charon) в трубку не идёт."""
    tg, spoken = FakeTg(), []
    gemini = sine(24000, 0.5).tobytes()
    live = FakeLive([Msg(gemini), _said("Добрый день, сэр. ", "output_transcription"),
                     _said("Чем могу помочь?", "output_transcription", done=True), Msg(end=True)])
    sess = tc.CallSession(tg, live, 42, "x", max_sec=5, speaker=_fish(spoken=spoken))
    asyncio.run(sess.run())
    assert spoken == ["Добрый день, сэр.", "Чем могу помочь?"]
    voiced = [f for f in tg.sent if any(f)]
    assert len(voiced) == 20                              # 2 куска по 0,1 с; 0,5 с Gemini выброшены
    assert _levels(tg) <= {3000, 1500}                    # только тон Fish (1500 — сглаживание на стыке)
    assert sess.first_voice_sec > 0


def test_fish_starts_on_first_sentence_before_turn_ends():
    tg, spoken = FakeTg(), []

    class Slow(FakeLive):
        def receive(self):
            async def gen():
                yield _said("Секунду, сэр. ", "output_transcription")
                await asyncio.sleep(0.4)                  # модель ещё «проговаривает» остальное
                spoken.append("—ход ещё идёт—")
                yield _said("Сейчас посмотрю.", "output_transcription", done=True)
                yield Msg(end=True)
                await asyncio.sleep(5)
            return gen()
    asyncio.run(tc.CallSession(tg, Slow([]), 42, "x", max_sec=5, speaker=_fish(spoken=spoken)).run())
    assert spoken[0] == "Секунду, сэр." and spoken[1] == "—ход ещё идёт—"


def test_fish_failure_falls_back_to_edge_then_gemini():
    tg, edge = FakeTg(), []

    async def fallback(text):
        edge.append(text)
        return (np.ones(2400) * 2000).astype("<i2").tobytes()
    gemini = (np.ones(2400) * 1000).astype("<i2").tobytes()
    live = FakeLive([_said("Слушаю, сэр.", "output_transcription", done=True),
                     _said("Привет"), Msg(gemini), _said("Я тут.", "output_transcription", done=True),
                     Msg(end=True)])
    sess = tc.CallSession(tg, live, 42, "x", max_sec=5, speaker=_fish(fail=True), fallback=fallback)
    asyncio.run(sess.run())
    assert edge == ["Слушаю, сэр."]                       # этот ход договорил Edge
    assert {2000, 1000} <= _levels(tg)                    # следующий — голосом Gemini


def test_interrupt_stops_fish():
    tg = FakeTg()

    async def long_speech(text):
        for _ in range(40):                               # 4 с речи кусками по 0,1 с
            await asyncio.sleep(0.01)
            yield (np.ones(2400) * 3000).astype("<i2").tobytes()
    live = FakeLive([_said("Длинный рассказ про погоду на неделю.", "output_transcription"),
                     *[Msg() for _ in range(10)], Msg(interrupted=True), Msg(end=True)])
    asyncio.run(tc.CallSession(tg, live, 42, "x", max_sec=5, speaker=long_speech).run())
    assert len([f for f in tg.sent if any(f)]) < 150      # из 400 кадров большая часть сброшена


def test_goodbye_is_spoken_before_hanging_up():
    """end_call пришёл, а Fish ещё синтезирует «До свидания» — трубку кладём после него."""
    tg = FakeTg()
    live = FakeLive([_said("До свидания, сэр.", "output_transcription"), Msg(end=True)])
    sess = tc.CallSession(tg, live, 42, "x", max_sec=5, speaker=_fish(delay=0.3))
    asyncio.run(sess.run())
    assert tg.hung and len([f for f in tg.sent if any(f)]) == 10


def test_gemini_connects_while_phone_rings(caplog):
    """Журнал 02.10: Gemini подключался 1,2–2,2 с ПОСЛЕ ответа — человек слушал тишину.
    Теперь подключение идёт во время гудков."""
    import logging
    order = []

    class Ringing(FakeTg):
        async def ring(self, peer):
            await asyncio.sleep(0.3)
            order.append("взял трубку")

    class Live(FakeLive):
        async def __aenter__(self):
            await asyncio.sleep(0.1)
            order.append("Gemini")
            return self
    with caplog.at_level(logging.INFO, logger="core.tg_call"):
        asyncio.run(tc.CallSession(Ringing(), Live([Msg(end=True)]), 42, "x", max_sec=5).run())
    assert order == ["Gemini", "взял трубку"]
    assert "Gemini на связи через 0.0 с" in caplog.text


@pytest.mark.parametrize("setting,fish_key,voice", [("", True, "fish"), ("gemini", True, "gemini"),
                                                    ("fish", False, "gemini")])
def test_call_voice_follows_desktop_setting(monkeypatch, setting, fish_key, voice):
    from telegram_bot import tts_fish
    monkeypatch.delenv("JARVIS_VOICE", raising=False)
    monkeypatch.setattr(tc, "_keys", lambda: {"jarvis_voice": setting})
    monkeypatch.setattr(tts_fish, "is_configured", lambda: fish_key)
    assert tc.call_voice() == voice
