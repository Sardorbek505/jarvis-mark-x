"""Сквозной прогон голосового круга — без микрофона, без сети, без окна.

Зачем это есть. Задержку и работоспособность до сих пор нельзя было проверить
иначе как вручную: `main()` создаёт PyQt-окно и блокируется на
`ui.wait_for_api_key()`, дальше нужен живой микрофон и живой Gemini. В итоге
все четыре задачи голосового круга — микрофон, отправка, приём,
воспроизведение — никогда не проверялись вместе.

Здесь они запускаются в одной TaskGroup, как в бою, но с поддельными краями:
звуковая карта, вход и сессия Gemini заменены. Проверяется весь путь кадра:

    громкий кадр микрофона
        → фильтр тишины (_is_loud_enough)
        → out_queue → _send_realtime → session.send_realtime_input
    ответ модели
        → session.receive → _receive_audio → audio_in_queue
        → _play_audio → stream.write
    и параллельно — секундомер core/latency

Настоящий Gemini сюда не ходит, поэтому цифры задержки здесь — накладные
расходы самого конвейера, без сети. Это нижняя граница: живой замер даст
больше, и разница как раз покажет вклад облака.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

import main as jarvis_main  # noqa: E402


# ─── Поддельные края ──────────────────────────────────────────────────────────

class _UI:
    """Ровно та поверхность, которой Jarvis пользуется от окна."""

    def __init__(self):
        self.logs = []
        self.states = []
        self.muted = False
        self.on_text_command = None

    def write_log(self, msg):
        self.logs.append(msg)

    def set_state(self, state):
        self.states.append(state)


class _OutStream:
    def __init__(self):
        self.written = []

    def write(self, chunk):
        self.written.append(chunk)

    def stop(self):
        pass

    def close(self):
        pass


class _MicStream:
    """Подменяет sd.InputStream: на входе в контекст начинает сыпать кадры."""

    def __init__(self, frames, **kwargs):
        self._frames = frames
        self._callback = kwargs.get("callback")
        self.delivered = 0

    def __enter__(self):
        for frame in self._frames:
            self._callback(frame, len(frame), None, None)
            self.delivered += 1
        return self

    def __exit__(self, *exc):
        return False


def _resp(*, data=None, heard=None, said=None, turn_complete=False):
    """Кусок ответа Gemini в том виде, в каком его читает _receive_audio."""
    content = None
    if heard is not None or said is not None or turn_complete:
        content = SimpleNamespace(
            input_transcription=SimpleNamespace(text=heard) if heard else None,
            output_transcription=SimpleNamespace(text=said) if said else None,
            turn_complete=turn_complete,
        )
    return SimpleNamespace(data=data, server_content=content, tool_call=None)


class _Session:
    """Сессия Gemini Live: принимает кадры, отдаёт заготовленный ответ."""

    def __init__(self, script):
        self._script = script
        self.sent = []              # что ушло в облако
        self.client_content = []    # проактивные вбросы

    async def send_realtime_input(self, media=None):
        self.sent.append(media)

    async def send_client_content(self, **kwargs):
        self.client_content.append(kwargs)

    async def receive(self):
        for item in self._script:
            # Пауза настоящая, а не sleep(0). Одного проворота планировщика
            # не хватало: кадр звука и turn_complete приходили в один тик, и
            # ход закрывался раньше, чем задача воспроизведения успевала снять
            # его с очереди, — метрика «звучит» оставалась незаполненной. На
            # быстрой машине везло, на раннере CI нет. В бою такого не бывает:
            # звук Gemini течёт секундами раньше конца хода (замер 31.08.2026 —
            # turn_complete позже первого звука на 3.9-4.9 с).
            await asyncio.sleep(0.01)
            yield item
        # Дальше — тишина. Если просто выйти, внешний `while True` в
        # _receive_audio позовёт receive() заново и проиграет ответ по кругу.
        while True:
            await asyncio.sleep(0.05)


# ─── Сборка стенда ────────────────────────────────────────────────────────────

def _loud(value=4000):
    """Кадр заведомо громче порога MIC_RMS_THRESHOLD (250)."""
    return np.full((jarvis_main.CHUNK_SIZE, 1), value, dtype=np.int16)


def _quiet():
    return np.zeros((jarvis_main.CHUNK_SIZE, 1), dtype=np.int16)


@pytest.fixture
def стенд(tmp_path, monkeypatch):
    """Джарвис с подменёнными краями и профилем в песочнице."""
    # Профиль, паттерны и данные команды пишутся от BASE_DIR — уводим в tmp,
    # чтобы тест не трогал настоящие файлы пользователя.
    monkeypatch.setattr(jarvis_main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(jarvis_main, "_IGNORE_SPEAKERS", False)
    monkeypatch.setattr(jarvis_main, "_pick_input_device", lambda: None)
    # Тракт озвучки закрепляем явно: по умолчанию говорит Fish, и тогда звук
    # Gemini намеренно выбрасывается. Тесты ниже проверяют именно путь Gemini,
    # поэтому провайдер тут не «как настроено у владельца», а заданный.
    monkeypatch.setattr(jarvis_main, "_VOICE_PROVIDER", "gemini")

    ui = _UI()
    jarvis = jarvis_main.Jarvis(ui)
    out = _OutStream()
    jarvis._open_output = lambda: out

    return SimpleNamespace(jarvis=jarvis, ui=ui, out=out, tmp=tmp_path,
                           monkeypatch=monkeypatch)


async def _прогнать(стенд, frames, script, timeout=5.0):
    """Крутит все четыре задачи круга, пока ответ не доиграет."""
    j = стенд.jarvis
    session = _Session(script)

    j.session = session
    j.audio_in_queue = asyncio.Queue(maxsize=200)
    j.out_queue = asyncio.Queue(maxsize=50)
    j._turn_done_event = asyncio.Event()
    j._loop = asyncio.get_event_loop()

    стенд.monkeypatch.setattr(
        jarvis_main.sd, "InputStream",
        lambda **kw: _MicStream(frames, **kw),
    )

    expected_audio = sum(1 for r in script if r.data)

    async def круг():
        async with asyncio.TaskGroup() as tg:
            tg.create_task(j._send_realtime())
            tg.create_task(j._listen_audio())
            tg.create_task(j._receive_audio())
            tg.create_task(j._play_audio())

            deadline = asyncio.get_event_loop().time() + timeout
            while (len(стенд.out.written) < expected_audio
                   and asyncio.get_event_loop().time() < deadline):
                await asyncio.sleep(0.01)

            raise asyncio.CancelledError    # снимаем всю группу разом

    with pytest.raises((asyncio.CancelledError, BaseExceptionGroup)):
        await круг()

    return session


# ─── Собственно проверки ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_полный_круг_голос_доходит_туда_и_обратно(стенд):
    """Кадр с микрофона уезжает в облако, ответ доигрывается в динамики."""
    script = [
        _resp(heard="включи музыку"),
        _resp(data=b"\x01\x02" * 100),
        _resp(data=b"\x03\x04" * 100),
        _resp(said="Разумеется, сэр.", turn_complete=True),
    ]

    session = await _прогнать(стенд, [_loud(), _loud()], script)

    assert session.sent, "кадры микрофона не дошли до сессии"
    assert all(m["mime_type"] == "audio/pcm" for m in session.sent)
    assert стенд.out.written == [b"\x01\x02" * 100, b"\x03\x04" * 100], \
        "ответ модели не доиграл в устройство"


@pytest.mark.asyncio
async def test_тишина_в_облако_не_уходит(стенд):
    """Фильтр порога — то, что бережёт квоту и не шлёт комнату в Google."""
    script = [_resp(said="", turn_complete=True)]

    session = await _прогнать(стенд, [_quiet()] * 30, script, timeout=1.0)

    assert session.sent == [], "тихие кадры не должны попадать в облако"


@pytest.mark.asyncio
async def test_замер_задержки_срабатывает_на_живом_круге(стенд):
    """Секундомер должен получить цифры от реального прохода, а не в тесте на себя."""
    script = [
        _resp(heard="какая погода"),
        _resp(data=b"\x05\x06" * 100),
        _resp(said="Плюс двадцать, сэр.", turn_complete=True),
    ]

    await _прогнать(стенд, [_loud()], script)

    stats = стенд.jarvis._latency._stats
    assert stats["answered"].count == 1, "задержка ответа не замерена"
    assert stats["heard"].count == 1, "задержка распознавания не замерена"
    assert stats["played"].count == 1, "задержка воспроизведения не замерена"

    # Конвейер без сети обязан укладываться в сотни миллисекунд: если тут
    # вылезли секунды — тормозит сам код, а не облако.
    assert stats["answered"].worst < 1000, (
        f"конвейер сам по себе тормозит: {stats['answered'].worst}мс"
    )
    print("\n" + стенд.jarvis._latency.summary())


@pytest.mark.asyncio
async def test_расшифровка_и_ответ_попадают_в_окно(стенд):
    script = [
        _resp(heard="привет"),
        _resp(data=b"\x07\x08" * 50),
        _resp(said="Здравствуйте, сэр.", turn_complete=True),
    ]

    await _прогнать(стенд, [_loud()], script)

    logs = " | ".join(стенд.ui.logs)
    assert "привет" in logs, "сказанное пользователем не показано"
    assert "Здравствуйте, сэр." in logs, "ответ Джарвиса не показан"


@pytest.mark.asyncio
async def test_с_голосом_fish_звук_gemini_не_играет(стенд, monkeypatch):
    """Два голоса на один ответ — худшее из возможного.

    Когда говорит Fish, аудио Gemini обязано быть выброшено: иначе Charon и
    Джарвис произнесут одну и ту же реплику одновременно.
    """
    monkeypatch.setattr(jarvis_main, "_VOICE_PROVIDER", "fish")
    сказанное = []

    async def поддельный_fish(self, text):
        сказанное.append(text)
    monkeypatch.setattr(jarvis_main.Jarvis, "_speak_fish", поддельный_fish)

    script = [
        _resp(heard="как дела"),
        _resp(data=b"\x01\x02" * 100),          # голос Charon — в мусор
        _resp(said="Всё в норме, сэр.", turn_complete=True),
    ]

    await _прогнать(стенд, [_loud()], script)

    assert стенд.out.written == [], "звук Gemini не должен доходить до динамиков"
    assert сказанное == ["Всё в норме, сэр."], "Fish должен получить текст ответа"


def test_первый_кусок_речи_короткий_остальные_содержательные():
    """От длины первого куска зависит, когда человек услышит хоть что-то.

    Синтез тем быстрее, чем короче фраза, поэтому короткий зачин отпускаем
    отдельно — он звучит почти сразу и прикрывает синтез остального ответа.
    Дальше склеиваем: обрывки ломают интонацию и стоят лишнего round-trip'а.
    """
    куски = jarvis_main._split_for_speech(
        "Секунду, сэр. Смотрю. В Шымкенте восемь градусов, небольшой дождь. "
        "Ветер сорок четыре километра в час."
    )

    assert куски[0] == "Секунду, сэр.", "зачин должен уйти в синтез отдельно"
    assert len(куски) == 3
    assert all(len(k) >= jarvis_main._MIN_FIRST_CHUNK for k in куски)


def test_короткий_ответ_не_дробится():
    assert jarvis_main._split_for_speech("Да, сэр.") == ["Да, сэр."]
    assert jarvis_main._split_for_speech("") == []


def test_хвост_тишины_заведомо_длиннее_окна_vad():
    """Без запаса Джарвис не отвечает вообще — это не про скорость, а про то,
    услышат ли тебя.

    Конец фразы определяет VAD на стороне Gemini, и определить его он может
    только по полученной тишине. Замер 17.08.2026 на scripts/latency_probe.py:
    хвост 0.64 с → ноль ответов на три реплики, модель расшифровывала
    сказанное и молчала; 1.0 с → отвечает стабильно.
    """
    assert jarvis_main.MIC_HANGOVER_MS >= jarvis_main._VAD_SILENCE_MS * 2, (
        "хвост тишины должен перекрывать окно VAD с запасом"
    )


def test_точка_отсчёта_замера_только_громкий_кадр(стенд):
    """Кадры хвоста уходят в облако, но «человек договорил» — не про них.

    Пока отсчёт вёлся и от них, из задержки вычиталась длина собственного
    хвоста, и «слышит» выходило отрицательным — цифра лучше правды.
    """
    j = стенд.jarvis

    assert j._is_loud_enough(_loud()), "громкий кадр обязан уехать"
    assert j._frame_was_loud, "громкий кадр — законная точка отсчёта"

    assert j._is_loud_enough(_quiet()), "кадр хвоста тоже уезжает в облако"
    assert not j._frame_was_loud, "но точкой отсчёта служить не должен"


@pytest.mark.asyncio
async def test_молчание_пользователя_не_рождает_замер(стенд):
    """Джарвис заговорил сам — это не «ответ на реплику», в статистику не идёт."""
    script = [
        _resp(data=b"\x09\x0a" * 50),
        _resp(said="Час поздний, сэр.", turn_complete=True),
    ]

    await _прогнать(стенд, [_quiet()], script)

    assert стенд.jarvis._latency._stats["answered"].count == 0
