"""Мгновенные ответы: какие фразы Джарвис выполняет сам, без облака, и что
говорит в ответ."""
import asyncio

import pytest

from core import quick as q


@pytest.mark.parametrize("text,tool,args", [
    ("Джарвис, пауза", "video_control", {"action": "pause"}),
    ("поставь на паузу", "video_control", {"action": "pause"}),
    ("продолжи", "video_control", {"action": "resume"}),
    ("следующий трек", "music_player", {"action": "next"}),
    ("включи предыдущую песню", "music_player", {"action": "previous"}),
    ("что сейчас играет?", "music_player", {"action": "now_playing"}),
    ("Джарвис, громче", "computer_control", {"action": "volume_up"}),
    ("сделай тише, пожалуйста", "computer_control", {"action": "volume_down"}),
    ("громкость 50", "computer_control", {"action": "volume_set", "value": "50"}),
    ("звук на максимум", "computer_control", {"action": "volume_set", "value": "на максимум"}),
    ("громкость музыки на 100", "music_player", {"action": "volume_set", "value": "на 100"}),
    ("сделай музыку громче", "music_player", {"action": "volume_up"}),
    ("громкость фильма 70", "video_control", {"action": "volume_set", "value": "70"}),
    ("выключи звук", "computer_control", {"action": "mute"}),
    ("на весь экран", "video_control", {"action": "fullscreen"}),
    ("сверни все окна", "window_control", {"action": "minimize_all"}),
    ("который час?", "clock", {"action": "now"}),
    ("поставь таймер на 5 минут", "clock", {"action": "timer_set", "duration": "5 минут"}),
    ("таймер на полчаса", "clock", {"action": "timer_set", "duration": "полчаса"}),
    ("останови секундомер", "clock", {"action": "stopwatch_stop"}),
    ("отложи на 10 минут", "clock", {"action": "alarm_snooze", "minutes": "10"}),
    ("отложи", "clock", {"action": "alarm_snooze"}),
    ("закрой глаза", "eyes", {"action": "close"}),
    ("спасибо", None, {}),
])
def test_known_commands(text, tool, args):
    m = q.match(text)
    assert m is not None and (m.tool, m.args) == (tool, args)


@pytest.mark.parametrize("text", [
    "стоп",                         # им перебивают самого Джарвиса
    "громче и открой хром",         # шире команды — решает Gemini
    "включи музыку",                # нужен поиск трека
    "громкость какая-то",           # уровень не понят
    "таймер на что-то",
    "привет", "Джарвис", "", "сколько осталось",
])
def test_everything_else_goes_to_gemini(text):
    assert q.match(text) is None


def test_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("JARVIS_QUICK", "0")
    assert q.match("пауза") is None


def test_reply():
    pause = q.match("пауза")
    assert q.reply_for(pause, "Пауза.") in q.PHRASES["pause"]
    assert q.reply_for(pause, "Сейчас ничего не играет, сэр.") == "Сейчас ничего не играет, сэр."
    now = q.match("который час")
    assert q.reply_for(now, "Сейчас 14:05, суббота.") == "Сейчас 14:05, суббота."
    assert q.reply_for(q.match("спасибо"), "") in q.PHRASES["thanks"]


def test_cache_and_prewarm(tmp_path, monkeypatch):
    cache = q.VoiceCache(tmp_path)
    monkeypatch.setattr(q, "_cache", cache)
    said = []

    async def synth(text):
        said.append(text)
        return b"\x01\x00" * 10

    assert asyncio.run(q.prewarm(synth, 24000)) == len(q.all_phrases())
    assert asyncio.run(q.prewarm(synth, 24000)) == 0          # второй раз — ничего
    assert len(said) == len(q.all_phrases())
    fresh = q.VoiceCache(tmp_path)                             # с диска, после перезапуска
    assert fresh.get("Есть, сэр.", 24000) == b"\x01\x00" * 10
    assert fresh.get("Есть, сэр.", 16000) is None               # другая частота — другой звук
    assert cache.wanted("Есть, сэр.") and not cache.wanted("Сейчас 14:05.")


def test_cache_ignores_commas_and_case_learns_short_phrases(tmp_path):
    """Gemini ставит запятые как придётся: «Готово сэр» — та же готовая фраза.
    Короткие ответы без чисел запоминаются после первого раза."""
    cache = q.VoiceCache(tmp_path)
    cache.put("Готово, сэр.", 24000, b"\x02\x00" * 8)
    assert cache.get("готово сэр", 24000) == b"\x02\x00" * 8
    assert cache.get("Готово, сэр?", 24000) is None                 # вопрос звучит иначе
    assert "Секунду, сэр." in q.all_phrases() and len(q.all_phrases()) == len(set(q.all_phrases()))
    assert cache.wanted("Секунду сэр.") and cache.wanted("Открываю Telegram, сэр.")
    assert not cache.wanted("В Ташкенте плюс 18, сэр.")                 # числа меняются
    assert not cache.wanted("Сегодня у вас три пары и одна контрольная по физике, сэр.")   # длинное
    cache.MAX_LEARNED = 1 - len(q._PHRASE_SET)                         # на диске уже 1 файл — предел
    assert not cache.wanted("Открываю Telegram, сэр.") and cache.wanted("Секунду, сэр.")   # предел
