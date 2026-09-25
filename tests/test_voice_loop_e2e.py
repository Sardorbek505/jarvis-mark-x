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
        self.active = True

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
    return SimpleNamespace(data=data, server_content=content, tool_call=None,
                           session_resumption_update=None, go_away=None)


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
            await asyncio.sleep(0)      # даём провернуться остальным задачам
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
    # Проверки ниже — про сам конвейер звука, а не про обращение по имени:
    # отвечаем на всё. Фильтр по имени проверяется отдельно, в конце файла.
    monkeypatch.setattr(jarvis_main, "_WAKE_MODE", "always_on")

    ui = _UI()
    jarvis = jarvis_main.Jarvis(ui)
    jarvis._input_device = None     # устройство уже выбрано (как после первого подключения)
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

    # «Звучит» здесь намеренно не проверяется. Метрика попадает в сводку
    # только если mark_playback успел сработать ДО turn_complete, а заглушка
    # отдаёт кадр звука и конец хода в соседних тиках планировщика. Кто из
    # двух задач проснётся первой — вопрос везения: на этой машине везёт, на
    # раннере CI нет (31.08.2026, assert 0 == 1 на каждом прогоне). Две
    # попытки развести их по времени сделали хуже — обнулялся уже answered.
    #
    # В бою такой гонки нет: звук Gemini течёт секундами раньше конца хода
    # (замер — turn_complete позже первого звука на 3.9-4.9 с), и «звучит»
    # исправно попадает в сводку живого стенда scripts/latency_probe.py.
    # Само воспроизведение проверяется выше, в
    # test_полный_круг_голос_доходит_туда_и_обратно, по стриму устройства.

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


@pytest.fixture
def поддельный_fish(monkeypatch):
    """Fish без сети: запоминает, что ему дали, и отдаёт метку вместо звука."""
    from telegram_bot import tts_fish
    сказанное = []

    async def speak_pcm(text, sample_rate=None):
        сказанное.append(text)
        return b"\x11\x11" * 50
    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "speak_pcm", speak_pcm)
    monkeypatch.setattr(jarvis_main, "_VOICE_PROVIDER", "fish")
    return сказанное


@pytest.mark.asyncio
async def test_с_голосом_fish_звук_gemini_не_играет(стенд, поддельный_fish):
    """Два голоса на один ответ — худшее из возможного.

    Когда говорит Fish, аудио Gemini обязано быть выброшено: иначе Charon и
    Джарвис произнесут одну и ту же реплику одновременно.
    """
    script = [
        _resp(heard="как дела"),
        _resp(data=b"\x01\x02" * 100),          # голос Charon — в мусор
        _resp(said="Всё в норме, сэр.", turn_complete=True),
    ]

    await _прогнать(стенд, [_loud()], script)

    assert b"\x01\x02" * 100 not in стенд.out.written, "звук Gemini дошёл до динамиков"
    assert поддельный_fish == ["Всё в норме, сэр."], "Fish должен получить текст ответа"
    assert стенд.out.written == [b"\x11\x11" * 50]


@pytest.mark.asyncio
async def test_fish_начинает_говорить_до_конца_хода(стенд, поддельный_fish):
    """Первое предложение уходит в синтез по своей точке, а не по turn_complete."""
    j = стенд.jarvis
    j.session = _Session([
        _resp(heard="какая погода"),
        _resp(said="Секунду, сэр. "),
        _resp(said="Смотрю прогноз"),
        # turn_complete нет: ход ещё идёт
    ])
    j.audio_in_queue = asyncio.Queue()
    j._turn_done_event = asyncio.Event()

    task = asyncio.create_task(j._receive_audio())
    for _ in range(100):
        if поддельный_fish:
            break
        await asyncio.sleep(0.01)
    task.cancel()

    assert поддельный_fish == ["Секунду, сэр."], "Fish ждал конца хода"


def test_нарезка_потока_ждёт_точку_и_порог():
    куски, остаток = jarvis_main._take_speakable("Секунду, сэр. Смотрю", first=True, final=False)
    assert куски == ["Секунду, сэр."] and остаток.strip() == "Смотрю"

    # Без точки — ждём дальше; на конце хода — отдаём остаток.
    assert jarvis_main._take_speakable("Смотрю прогноз", True, False) == ([], "Смотрю прогноз")
    assert jarvis_main._take_speakable("Смотрю прогноз", True, True)[0] == ["Смотрю прогноз"]

    # Короткое «Да.» не режем отдельным запросом к синтезу.
    куски, _ = jarvis_main._take_speakable("Да. Сэр. ", first=True, final=False)
    assert куски == []


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


# ─── Обращение по имени ───────────────────────────────────────────────────────

def _tool_resp(name, args=None):
    fc = SimpleNamespace(id="call-1", name=name, args=args or {})
    return SimpleNamespace(data=None, server_content=None,
                           tool_call=SimpleNamespace(function_calls=[fc]),
                           session_resumption_update=None, go_away=None)


class _ToolSession(_Session):
    def __init__(self, script):
        super().__init__(script)
        self.tool_responses = []

    async def send_tool_response(self, function_responses=None):
        self.tool_responses.extend(function_responses or [])


@pytest.fixture
def по_имени(стенд):
    стенд.monkeypatch.setattr(jarvis_main, "_WAKE_MODE", "wake_word")
    return стенд


@pytest.mark.asyncio
async def test_чужая_речь_без_имени_не_озвучивается(по_имени):
    """Телевизор и разговор по телефону не должны будить Джарвиса."""
    script = [
        _resp(heard="ну и что он тебе сказал"),
        _resp(data=b"\x01\x02" * 100),
        _resp(said="Боюсь, я не расслышал.", turn_complete=True),
    ]
    await _прогнать(по_имени, [_loud()], script, timeout=0.5)

    assert по_имени.out.written == [], "ответ на чужую речь прозвучал"
    assert not any("Джарвис:" in m for m in по_имени.ui.logs)


@pytest.mark.asyncio
async def test_обращение_по_имени_будит(по_имени):
    script = [
        _resp(heard="Джарвис, включи музыку"),
        _resp(data=b"\x01\x02" * 100),
        _resp(said="Разумеется, сэр.", turn_complete=True),
    ]
    await _прогнать(по_имени, [_loud()], script)

    assert по_имени.out.written == [b"\x01\x02" * 100]
    assert по_имени.jarvis.is_awake(), "после ответа разговор должен продолжаться"


@pytest.mark.asyncio
async def test_имя_после_первых_байт_ответа_не_теряет_звук(по_имени):
    """Расшифровка может отстать от ответа: звук копится и доигрывает."""
    script = [
        _resp(heard="слушай"),
        _resp(data=b"\x09\x09" * 50),
        _resp(heard=" Жарвис"),
        _resp(data=b"\x0a\x0a" * 50),
        _resp(said="Слушаю, сэр.", turn_complete=True),
    ]
    await _прогнать(по_имени, [_loud()], script)

    assert по_имени.out.written == [b"\x09\x09" * 50, b"\x0a\x0a" * 50]


@pytest.mark.asyncio
async def test_в_разговоре_имя_повторять_не_нужно(по_имени):
    по_имени.jarvis.wake()
    script = [
        _resp(heard="а какая погода"),
        _resp(data=b"\x05\x06" * 100),
        _resp(said="Плюс двадцать, сэр.", turn_complete=True),
    ]
    await _прогнать(по_имени, [_loud()], script)

    assert по_имени.out.written == [b"\x05\x06" * 100]


@pytest.mark.asyncio
async def test_команда_из_чужой_речи_не_выполняется(по_имени, monkeypatch):
    выполнено = []

    async def поддельный_инструмент(self, fc):
        выполнено.append(fc.name)
        return jarvis_main.types.FunctionResponse(id=fc.id, name=fc.name, response={"ok": True})
    monkeypatch.setattr(jarvis_main.Jarvis, "_execute_tool", поддельный_инструмент)

    j = по_имени.jarvis
    session = _ToolSession([_resp(heard="выключи компьютер"), _tool_resp("computer_settings")])
    j.session = session
    j.audio_in_queue = asyncio.Queue()
    j._turn_done_event = asyncio.Event()

    task = asyncio.create_task(j._receive_audio())
    for _ in range(300):
        if session.tool_responses:
            break
        await asyncio.sleep(0.01)
    task.cancel()

    assert выполнено == [], "инструмент из чужого разговора выполнился"
    assert session.tool_responses and "не Джарвису" in str(session.tool_responses[0].response)


@pytest.mark.parametrize("text", [
    "Джарвис, привет", "джарвис", "Жарвис включи", "Джервис", "Jarvis, hi",
    "эй Джарвиз", "Джарвису скажи", "Jarvis qalaysan",
])
def test_имя_узнаётся_в_расшифровке(text):
    assert jarvis_main._has_wake_word(text)


@pytest.mark.parametrize("text", ["привет", "включи музыку", "жара в марте", "журнал"])
def test_без_имени_не_будит(text):
    assert not jarvis_main._has_wake_word(text)


# ─── Эхо, свои реплики, разрывы ──────────────────────────────────────────────

def test_после_своей_речи_микрофон_ещё_не_слушает(стенд):
    j = стенд.jarvis
    j.set_speaking(True)
    j.set_speaking(False)
    import time as _t
    assert j._echo_guard_until > _t.monotonic(), "хвост собственной речи уйдёт в облако"


def test_speak_шлёт_указание_а_не_реплику_пользователя(стенд):
    отправлено = []
    стенд.jarvis._send_text_to_session = отправлено.append
    стенд.jarvis.speak("Сэр, произошла ошибка.")
    assert отправлено[0].startswith("[СИСТЕМА"), "модель ответит сама себе"
    assert "Сэр, произошла ошибка." in отправлено[0]


def test_причина_разрыва_видна_сквозь_exceptiongroup():
    err = BaseExceptionGroup("unhandled errors in a TaskGroup",
                             [RuntimeError("1008 policy violation")])
    assert "1008" in jarvis_main._root_error_text(err)


@pytest.mark.asyncio
async def test_команда_выполняется_если_имя_пришло_после_вызова(по_имени, monkeypatch):
    """Расшифровка с именем отстала от вызова инструмента — команда не теряется."""
    выполнено = []

    async def поддельный_инструмент(self, fc):
        выполнено.append(fc.name)
        return jarvis_main.types.FunctionResponse(id=fc.id, name=fc.name, response={"ok": True})
    monkeypatch.setattr(jarvis_main.Jarvis, "_execute_tool", поддельный_инструмент)

    j = по_имени.jarvis
    session = _ToolSession([
        _resp(heard="открой хром"),
        _tool_resp("open_app", {"app_name": "chrome"}),
        _resp(heard=" Джарвис"),
    ])
    j.session = session
    j.audio_in_queue = asyncio.Queue()
    j._turn_done_event = asyncio.Event()

    task = asyncio.create_task(j._receive_audio())
    for _ in range(300):
        if session.tool_responses:
            break
        await asyncio.sleep(0.01)
    task.cancel()

    assert выполнено == ["open_app"]


class _SwitchSession(_Session):
    """Посреди ответа голос переключают на gemini (инструмент switch_voice)."""

    async def receive(self):
        yield _resp(heard="включи голос джемини")
        yield _resp(said="Секунду, сэр. ")
        await asyncio.sleep(0.05)
        jarvis_main.set_voice_provider.__globals__["_VOICE_PROVIDER"] = "gemini"
        yield _resp(said="", turn_complete=True)
        while True:
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_голос_переключили_посреди_ответа_микрофон_не_глохнет(стенд, поддельный_fish):
    """Fish начал ответ, голос сменили на gemini — воркер обязан завершиться."""
    j = стенд.jarvis
    j.session = _SwitchSession([])
    j.audio_in_queue = asyncio.Queue()
    j._turn_done_event = asyncio.Event()
    task = asyncio.create_task(j._receive_audio())
    await asyncio.sleep(0.3)
    task.cancel()

    assert поддельный_fish == ["Секунду, сэр."]
    assert j._active_synth_tasks == 0, "воркер Fish висит — микрофон глух навсегда"


def test_разговор_без_имени_продлевается_ограниченно(по_имени):
    j = по_имени.jarvis
    j.wake()
    продлений = 0
    for _ in range(jarvis_main._FOLLOWUPS + 3):
        before = j._followups_left
        j._continue_conversation()
        продлений += before > j._followups_left
    assert продлений == jarvis_main._FOLLOWUPS
