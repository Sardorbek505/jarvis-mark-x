"""Простые команды — сделать и подтвердить звуком, без «Есть, сэр»."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from core import quick


@pytest.mark.parametrize("said", ["пауза", "громче", "следующий трек", "таймер на 5 минут", "сверни все окна",
                                  "выключи звук"])
def test_actions_are_confirmed_by_sound(said, monkeypatch):
    monkeypatch.delenv("JARVIS_QUICK_VOICE", raising=False)
    q = quick.match(said)
    assert q and quick.silent(q, "Готово.")


@pytest.mark.parametrize("said", ["который час", "что играет", "спасибо", "ты тут"])
def test_answers_and_talk_stay_voiced(said):
    q = quick.match(said)
    assert q and not quick.silent(q, "Сейчас 10:15.")


def test_failure_is_said_out_loud():
    q = quick.match("следующий трек")
    assert not quick.silent(q, "Spotify не запущен на компьютере.")


def test_setting_brings_voice_back(monkeypatch):
    monkeypatch.setenv("JARVIS_QUICK_VOICE", "1")
    assert not quick.silent(quick.match("пауза"), "ok")


def test_done_sound_is_short_and_clean():
    from core.sounds import done_pcm
    pcm = np.frombuffer(done_pcm(24000), dtype=np.int16)
    assert 0.15 < len(pcm) / 24000 < 0.35                    # короче «Есть, сэр» в разы
    assert np.abs(pcm).max() < 32767 * 0.3                   # тихо, без перегруза
    assert abs(int(pcm[0])) < 300 and abs(int(pcm[-1])) < 300   # без щелчков


def test_quick_run_plays_sound_instead_of_speaking(monkeypatch):
    import main
    j = main.Jarvis.__new__(main.Jarvis)
    spoken, logs = [], []
    j.ui = SimpleNamespace(write_log=logs.append, tool_started=lambda *a: None, tool_finished=lambda *a: None)
    j.audio_in_queue = asyncio.Queue(maxsize=200)
    j._show_card = lambda *a: None
    j._remember_turn = lambda *a: None

    async def fake_tool(fc):
        return SimpleNamespace(response={"result": "Пауза."})

    async def fake_speak(text):
        spoken.append(text)
    j._execute_tool = fake_tool
    j._speak_fish = fake_speak
    asyncio.run(j._quick_run(quick.match("пауза"), "пауза"))
    assert spoken == [] and j.audio_in_queue.qsize() > 0            # звук, а не фраза
    assert logs[-1].startswith("Джарвис: ✓")
    asyncio.run(j._quick_run(quick.match("который час"), "который час"))
    assert spoken                                                   # ответ нужен — голосом


@pytest.mark.parametrize("said,tool,args", [
    ("открой телеграм", "open_app", {"app_name": "телеграм"}),
    ("открой ютуб", "browser", {"action": "go_to", "url": "youtube.com"}),
    ("найди на ютубе как собрать пк", "youtube_player", {"action": "play", "query": "как собрать пк"}),
    ("поставь последнее видео mrbeast", "youtube_player", {"action": "latest", "channel": "mrbeast"}),
    ("включи believer", "music_player", {"action": "play", "query": "believer"}),
    ("поставь песню люби меня", "music_player", {"action": "play", "query": "люби меня"}),
])
def test_open_and_play_run_instantly_like_alfred(said, tool, args):
    q = quick.match(said)
    assert q and (q.tool, q.args) == (tool, args)
    assert quick.silent(q, "Открыл.")


@pytest.mark.parametrize("said", ["открой хром и найди погоду", "открой сайт вк", "включи музыку", "включи камеру",
                                  "включи видео", "включи музыку погромче", "включи режим стрима"])
def test_compound_or_unclear_still_goes_to_gemini(said):
    q = quick.match(said)
    assert q is None or q.tool not in ("open_app", "music_player") or q.args.get("query") != said
