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
import time
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
            await asyncio.sleep(0)      # даём провернуться остальным задачам
            yield item
        # Дальше — тишина. Если просто выйти, внешний `while True` в
        # _receive_audio позовёт receive() заново и проиграет ответ по кругу.
        while True:
            await asyncio.sleep(0.05)


# ─── Сборка стенда ────────────────────────────────────────────────────────────

def _речеподобный(n: int, sr: int = 16000, seed: int = 0) -> np.ndarray:
    """Сигнал с формантами и слоговой огибающей.

    Постоянный уровень (`np.full(..., 4000)`) громкий, но не речь: нейросетевой
    эндпоинтинг его справедливо отвергает. Сквозной тест обязан гонять по тракту
    то, что тракт и должен пропускать.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    sig = np.zeros(n)
    for f, a in ((120, 0.5), (350, 0.35), (900, 0.2), (2400, 0.1)):
        sig += a * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 4.5 * t)
    return np.clip(sig * env * 9000, -32768, 32767).astype(np.int16)


def _loud(seed=0):
    """Кадр, который тракт обязан признать речью и пропустить в облако."""
    return _речеподобный(jarvis_main.CHUNK_SIZE, seed=seed).reshape(-1, 1)


def _фраза(кадров: int = 12):
    """Реплика длиной ~0.8 с.

    Пара кадров — это 128 мс: ни человек, ни нейросетевой VAD такое фразой не
    считают, у Silero на этом отрезке ещё не раскачалось рекуррентное состояние.
    Сквозной тест обязан подавать на вход то, что бывает в жизни.
    """
    # Кадры обязаны отличаться: настоящая речь не повторяет кадр в кадр,
    # а на строго периодическом сигнале Silero упирается в 0.48 и не
    # переходит порог — это артефакт синтетики, а не поведение тракта.
    return [_loud(seed=i) for i in range(кадров)]


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
    # Опорный поток колонок для AEC — настоящее WASAPI-устройство. Стенду оно
    # не нужно и не должно быть нужно: здесь проверяется маршрут кадра, а не
    # звуковая карта. Реальный loopback проверяется отдельно, на живом железе.
    monkeypatch.setattr(jarvis_main.Jarvis, "_start_aec_reference", lambda self: None)
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


async def _прогнать(стенд, frames, script, timeout=5.0, wake=None):
    """Крутит все четыре задачи круга, пока ответ не доиграет."""
    j = стенд.jarvis
    session = _Session(script)

    j.session = session
    j.audio_in_queue = asyncio.Queue(maxsize=200)
    j.out_queue = asyncio.Queue(maxsize=50)
    j._turn_done_event = asyncio.Event()
    j._loop = asyncio.get_event_loop()

    if wake is None:
        has_wake = any(
            "джарвис" in (getattr(r.server_content.input_transcription, "text", "") or "").lower()
            for r in script
            if getattr(r, "server_content", None) and getattr(r.server_content, "input_transcription", None)
        )
        j._wake_active_until = time.monotonic() + 10.0 if has_wake else 0.0
    else:
        j._wake_active_until = time.monotonic() + 10.0 if wake else 0.0

    стенд.monkeypatch.setattr(
        jarvis_main.sd, "InputStream",
        lambda **kw: _MicStream(frames, **kw),
    )

    expected_audio = sum(1 for r in script if r.data)

    # Ждать только звук — мало: приём хода и воспроизведение идут в разных
    # задачах, и обработка конца хода (запись ответа в лог, окно продолжения
    # диалога) может ещё не случиться, когда последний кадр уже доиграл.
    # Раньше это не вылезало лишь потому, что весь конец хода выполнялся
    # синхронно в одном тике; любой await внутри — и группа снималась раньше.
    expected_said = ""
    for r in script:
        текст = getattr(getattr(r, "server_content", None), "output_transcription", None)
        if текст is not None and getattr(текст, "text", ""):
            expected_said = текст.text

    def ход_доигран():
        # Микрофонная ветка идёт через фоновый воркер и call_soon_threadsafe,
        # то есть заведомо медленнее, чем приём готового ответа из заглушки.
        # Ответ доигрывает за десятки миллисекунд, и без этой проверки группа
        # снималась раньше, чем речь успевала доехать до сессии. Раньше это
        # не вылезало только потому, что первый же кадр считался речью по
        # порогу громкости; нейросетевому VAD нужно несколько кадров.
        if frames and not session.sent:
            return False
        if len(стенд.out.written) < expected_audio:
            return False
        if not expected_said:
            return True
        return any(str(log).startswith("Джарвис:") for log in стенд.ui.logs)

    async def круг():
        async with asyncio.TaskGroup() as tg:
            tg.create_task(j._send_realtime())
            tg.create_task(j._listen_audio())
            tg.create_task(j._receive_audio())
            tg.create_task(j._play_audio())

            deadline = asyncio.get_event_loop().time() + timeout
            while not ход_доигран() and asyncio.get_event_loop().time() < deadline:
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
        _resp(heard="джарвис, расскажи интересную историю"),
        _resp(data=b"\x01\x02" * 100),
        _resp(data=b"\x03\x04" * 100),
        _resp(said="Разумеется, сэр.", turn_complete=True),
    ]

    session = await _прогнать(стенд, _фраза(), script)

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
        _resp(heard="джарвис, как твои дела"),
        _resp(data=b"\x05\x06" * 100),
        _resp(said="Плюс двадцать, сэр.", turn_complete=True),
    ]

    await _прогнать(стенд, _фраза(), script)

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
        _resp(heard="джарвис, привет"),
        _resp(data=b"\x07\x08" * 50),
        _resp(said="Здравствуйте, сэр.", turn_complete=True),
    ]

    await _прогнать(стенд, _фраза(), script)

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

    async def поддельный_fish(self, text, *args, **kwargs):
        сказанное.append(text)
    monkeypatch.setattr(jarvis_main.Jarvis, "_speak_fish", поддельный_fish)

    script = [
        _resp(heard="джарвис, как дела"),
        _resp(data=b"\x01\x02" * 100),          # голос Charon — в мусор
        _resp(said="Всё в норме, сэр.", turn_complete=True),
    ]

    await _прогнать(стенд, _фраза(), script)

    assert стенд.out.written == [], "звук Gemini не должен доходить до динамиков"
    assert сказанное == ["Всё в норме, сэр."], "Fish должен получить текст ответа"


@pytest.mark.asyncio
async def test_без_обращения_джарвис_на_пк_молчит(стенд):
    """Если фраза звучит БЕЗ 'Джарвис' — ПК-ассистент обязан молчать и игнорировать."""
    script = [
        _resp(heard="поставь музыку мияги"),
        _resp(data=b"\x09\x09" * 50),
        _resp(said="Включаю трек.", turn_complete=True),
    ]

    await _прогнать(стенд, _фраза(), script)

    # Звук не воспроизводится, в логах отметка об игноре
    assert стенд.out.written == [], "без слова Джарвис звук не должен проигрываться"
    logs = " | ".join(стенд.ui.logs)
    assert "Игнор" in logs, "должен быть зафиксирован игнор фразы без обращения"


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


# Порог тишины и «хвост» после громкого кадра переехали из main.Jarvis
# в AudioPipeline (единый воркер вместо работы в аудиоколбэке).
# Проверка хвоста — в tests/test_audio_gateway_gating.py.


@pytest.mark.asyncio
async def test_молчание_пользователя_не_рождает_замер(стенд):
    """Джарвис заговорил сам — это не «ответ на реплику», в статистику не идёт."""
    script = [
        _resp(data=b"\x09\x0a" * 50),
        _resp(said="Час поздний, сэр.", turn_complete=True),
    ]

    await _прогнать(стенд, [_quiet()], script)

    assert стенд.jarvis._latency._stats["answered"].count == 0


@pytest.mark.asyncio
async def test_шумовой_переспрос_получает_короткое_окно(стенд):
    """После «не разобрал, повторите» окно продолжения обязано быть коротким.

    Живой сбой: Джарвис ловил шорох, извинялся, извинение открывало обычное
    окно на 4 секунды, за это время снова ловился шорох — и так по кругу,
    вперемешку с галлюцинациями на случайных языках.

    Таймаут считается на конце хода, а окно открывается позже — по факту
    окончания воспроизведения. Раньше посчитанное значение туда не доезжало.
    """
    script = [
        _resp(heard="джарвис"),
        # Кадр звука обязателен: стенд крутит круг, пока не доиграет ответ.
        # Без него задачи снимаются раньше, чем ход дойдёт до turn_complete.
        _resp(data=b"\x01\x02" * 20),
        _resp(said="Прошу прощения, я не разобрал из-за фонового шума. Повторить?",
              turn_complete=True),
    ]
    await _прогнать(стенд, _фраза(), script, timeout=2.0)

    j = стенд.jarvis
    assert j._followup_timeout == 2.0, "шумовой переспрос открыл обычное окно"
    assert j._pending_question is False, "извинение не должно считаться вопросом к пользователю"


@pytest.mark.asyncio
async def test_обычный_ответ_в_строгом_режиме_не_держит_окно(стенд):
    """Строгий режим (как Алиса): после обычного ответа — снова ждём имя."""
    стенд.monkeypatch.setenv("JARVIS_STRICT_WAKE", "1")
    script = [
        _resp(heard="джарвис, расскажи что-нибудь про марс"),
        _resp(data=b"" * 20),
        _resp(said="Марс — четвёртая планета, сэр.", turn_complete=True),
    ]
    await _прогнать(стенд, _фраза(), script, timeout=2.0)

    assert стенд.jarvis._followup_timeout == 0.0


@pytest.mark.asyncio
async def test_обычный_ответ_получает_нормальное_окно(стенд):
    """Мягкий режим: содержательный ответ держит окно продолжения диалога."""
    стенд.monkeypatch.setenv("JARVIS_STRICT_WAKE", "0")
    script = [
        _resp(heard="джарвис, расскажи что-нибудь про марс"),
        _resp(data=b"\x01\x02" * 20),
        _resp(said="Марс — четвёртая планета, сэр.", turn_complete=True),
    ]
    await _прогнать(стенд, _фраза(), script, timeout=2.0)

    assert стенд.jarvis._followup_timeout == 3.5


@pytest.mark.asyncio
async def test_вопрос_джарвиса_держит_окно_дольше(стенд):
    """Если Джарвис сам задал вопрос, у пользователя больше времени ответить."""
    script = [
        _resp(heard="джарвис, удали файл"),
        _resp(data=b"\x01\x02" * 20),
        _resp(said="Вы уверены, сэр?", turn_complete=True),
    ]
    await _прогнать(стенд, _фраза(), script, timeout=2.0)

    assert стенд.jarvis._followup_timeout == 6.0
    assert стенд.jarvis._pending_question is True


class _InterruptingResponse:
    """Кусок ответа, при чтении которого пользователь перебивает Джарвиса."""

    def __init__(self, jarvis):
        self._jarvis = jarvis
        self.server_content = None
        self.tool_call = None

    @property
    def data(self):
        self._jarvis._is_speaking = True      # есть что перебивать
        self._jarvis.interrupt_speech("test barge-in")
        return None


@pytest.mark.asyncio
async def test_прерванный_ход_не_озвучивается_заново(стенд):
    """turn_complete прерванного хода закрывает его молча.

    Стенд 16.09.2026: после barge-in флаг прерывания сбрасывался первой же
    строкой обработки turn_complete, и весь старый ответ звучал заново.
    """
    j = стенд.jarvis
    script = [
        _resp(heard="джарвис, как дела"),
        _resp(said="Благодарю, сэр, всё в полном порядке. "),
        _InterruptingResponse(j),
        _resp(data=b"\x05\x06" * 100),
        _resp(said="Согласно протоколу, докладываю.", turn_complete=True),
    ]

    await _прогнать(стенд, _фраза(), script, timeout=2.0)

    assert not any(str(log).startswith("Джарвис:") for log in стенд.ui.logs), \
        "прерванный ответ попал в озвучку/журнал заново"
    assert стенд.out.written == [], "звук прерванного хода доиграл в устройство"
    assert j._interrupted_turn is False
