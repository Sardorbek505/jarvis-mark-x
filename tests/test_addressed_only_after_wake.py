"""Джарвис отвечает только на речь, прозвучавшую ПОСЛЕ ключевого слова.

Живой прогон 21.09.2026, при играющем видео. Ложные срабатывания шли цепочкой
каждые 8 секунд — микрофон фактически не закрывался:

    10:54:59 [STANDBY -> LISTENING] (wake word)
    10:55:07 [LISTENING -> STANDBY] (gateway closed)
    10:55:07 [NN CONFIRMED] джарвис (Score: 1.00)
    10:55:15 [NN CONFIRMED] джарвис (Score: 1.00)
    …Арбитраж реплики: 'Даже в туалете у него был личный слуга…'  ← ответил вслух

Защита в `_on_listen_silence_timeout` была, но проверяла расшифровку Gemini
(`first_transcript_at`), а та идёт по всему, что слышит микрофон. При
работающем телевизоре куски приходят всегда — признак не срабатывал никогда.
"""

import asyncio
from unittest.mock import patch

import pytest

from core.headless_ui import HeadlessUI
from main import Jarvis


@pytest.fixture
def jarvis_app():
    ui = HeadlessUI()
    with patch("core.hotkey_manager.GlobalHotkeyManager"), \
         patch("core.wake_detector.WakeWordDetector2Stage"), \
         patch("main.Jarvis._start_telegram_bot", return_value=None):
        j = Jarvis(ui)
        j.audio_in_queue = asyncio.Queue()
        j.out_queue = asyncio.Queue()
        return j


class _StubPipelineWithSTT:
    """Конвейер с живым локальным распознавателем."""
    local_stt = object()


def test_room_noise_after_false_wake_is_not_an_address(jarvis_app):
    """Слово сработало, локально не услышано ни слова — обращения не было."""
    j = jarvis_app
    j.audio_pipeline = _StubPipelineWithSTT()
    j._on_wake_spotted()
    pt = j._pending_gemini_turn
    assert pt.addressed is True

    # Gemini всё это время расшифровывает телевизор
    pt.first_transcript_at = 123.0

    j._on_listen_silence_timeout()

    assert pt.addressed is False, "монолог из видео принят за обращение"


def test_real_speech_after_wake_keeps_the_address(jarvis_app):
    """Локальный распознаватель услышал речь — обращение состоялось."""
    j = jarvis_app
    j.audio_pipeline = _StubPipelineWithSTT()
    j._on_wake_spotted()
    pt = j._pending_gemini_turn

    j._on_local_transcript("сделай громче")   # Vosk услышал речь после слова

    j._on_listen_silence_timeout()

    assert pt.addressed is True


def test_each_wake_starts_the_proof_from_scratch(jarvis_app):
    """Доказательство от прошлой реплики не наследуется следующим пробуждением."""
    j = jarvis_app
    j.audio_pipeline = _StubPipelineWithSTT()
    j._on_wake_spotted()
    j._on_local_transcript("сделай громче")

    j._on_wake_spotted()                      # следующее, ложное срабатывание
    pt = j._pending_gemini_turn
    pt.first_transcript_at = 456.0

    j._on_listen_silence_timeout()

    assert pt.addressed is False


def test_without_local_recognizer_falls_back_to_old_signal(jarvis_app):
    """Без Vosk судить не по чему — иначе сбой распознавателя сделает Джарвиса глухим."""
    j = jarvis_app
    j.audio_pipeline = None
    j._on_wake_spotted()
    pt = j._pending_gemini_turn
    pt.first_transcript_at = 789.0

    j._on_listen_silence_timeout()

    assert pt.addressed is True, "без локального распознавателя обращение отвергать нельзя"


def test_wake_with_no_transcript_at_all_is_still_dropped(jarvis_app):
    """Слово в тишине: ни локально, ни в облаке ничего — обращения не было."""
    j = jarvis_app
    j.audio_pipeline = None
    j._on_wake_spotted()
    pt = j._pending_gemini_turn

    j._on_listen_silence_timeout()

    assert pt.addressed is False


# ── Куски расшифровки от брошенной реплики ───────────────────────────────────

def test_chunks_of_an_abandoned_utterance_are_dropped(jarvis_app):
    """Шлюз закрылся без turn_complete — накопленное не должно достаться следующей реплике."""
    j = jarvis_app
    j._begin_new_utterance()
    in_buf = []

    j._drop_foreign_transcript_chunks(in_buf, j._pending_gemini_turn)
    in_buf.extend(["Коммуникация", " — это", " доминирующее присутствие"])

    j._on_wake_spotted()                      # новая реплика, ход подменён
    j._drop_foreign_transcript_chunks(in_buf, j._pending_gemini_turn)

    assert in_buf == [], "ответ уехал бы на чужую речь минутной давности"


def test_chunks_of_the_same_utterance_are_kept(jarvis_app):
    """Внутри одной реплики куски обязаны накапливаться."""
    j = jarvis_app
    j._begin_new_utterance()
    pt = j._pending_gemini_turn
    in_buf = []

    for chunk in ("сде", "лай", " громче"):
        j._drop_foreign_transcript_chunks(in_buf, pt)
        in_buf.append(chunk)

    assert "".join(in_buf) == "сделай громче"


# ── Само правило, без Jarvis вокруг ──────────────────────────────────────────

@pytest.mark.parametrize(
    "local_speech, stt_available, cloud_started, expected, why",
    [
        (True,  True,  False, True,  "Vosk услышал речь после слова"),
        (False, True,  True,  False, "молчит Vosk, а облако слышит телевизор"),
        (False, True,  False, False, "после слова не прозвучало ничего"),
        (False, False, True,  True,  "без Vosk судим по прежнему признаку"),
        (False, False, False, False, "без Vosk и без расшифровки — обращения нет"),
    ],
)
def test_speech_followed_the_wake_rule(local_speech, stt_available, cloud_started, expected, why):
    from main import speech_followed_the_wake

    assert speech_followed_the_wake(
        local_speech=local_speech,
        local_stt_available=stt_available,
        cloud_transcript_started=cloud_started,
    ) is expected, why
