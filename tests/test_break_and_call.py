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
    tg = FakeTg(error=type(err, (Exception,), {})())
    live = FakeLive([])
    assert text in asyncio.run(tc.CallSession(tg, live, 42, "x").run())
    assert not hasattr(live, "prompt")                    # Gemini даже не открывали


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
