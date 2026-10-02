"""Интро на два хлопка: звук по сценарию, кадры без накладок, пропуск."""
import numpy as np
import pytest

from core import intro as S


def test_sound_follows_the_script():
    x = S.render()
    assert len(x) == int((S.T_END + 0.6) * S.RATE)
    assert np.max(np.abs(x)) <= 0.9                               # без перегруза
    env = np.array([np.sqrt(np.mean(x[i:i + 2400] ** 2)) for i in range(0, len(x) - 2400, 2400)])
    loudest = int(np.argmax(env)) * 0.1
    assert abs(loudest - S.T_HIT) < 0.3, "самый громкий момент — удар"
    assert env[int(S.T_FINAL * 10)] > 3 * env[int((S.T_FINAL - 0.3) * 10)], "финальный аккорд слышен"
    assert env[-2] < 0.05 * env.max(), "к концу затихает, не обрывается"


def test_pcm_for_speakers():
    pcm = S.pcm16(0.6, 24000)
    assert len(pcm) == 2 * int((S.T_END + 0.6) * 24000)


def test_timeline_is_in_order():
    assert S.T_DARK < S.T_TYPE < S.T_RING < S.T_HIT < S.T_NODES < S.T_CHECKS < S.T_FINAL < S.T_FADE < S.T_END
    assert S.type_times()[-1] < S.T_RING + 0.2
    assert S.node_times()[-1] + 0.6 < S.T_FINAL
    assert S.check_times()[-1] < S.T_FINAL


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _lit(img, x0, y0, x1, y1):
    """Сколько ярких точек в прямоугольнике (доли кадра)."""
    from PyQt6.QtGui import QColor
    w, h = img.width(), img.height()
    n = 0
    for x in range(int(x0 * w), int(x1 * w), 3):
        for y in range(int(y0 * h), int(y1 * h), 3):
            if QColor(img.pixel(x, y)).lightness() > 140:
                n += 1
    return n


def _white(img):
    """Почти белые точки в центре: лицо белое, шар — бирюзовый."""
    from PyQt6.QtGui import QColor
    w, h = img.width(), img.height()
    return sum(1 for x in range(int(w * 0.42), int(w * 0.58), 2) for y in range(int(h * 0.38), int(h * 0.62), 2)
               if (c := QColor(img.pixel(x, y))).lightness() > 170 and c.hslSaturation() < 140)


def test_frames_show_each_stage(qapp):
    from ui_intro import IntroScene
    sc = IntroScene()
    frames = {t: sc.render(t, 640, 360) for t in (0.2, 1.5, 2.6, 4.0, 6.0, 7.0, S.T_END)}
    assert _lit(frames[1.5], 0.3, 0.08, 0.7, 0.16) > 0, "заполняется полоса-сканер"
    assert _lit(frames[0.2], 0.3, 0.3, 0.7, 0.7) == 0 and _lit(frames[2.6], 0.3, 0.3, 0.7, 0.7) > 0, "загорается кольцо"
    assert _lit(frames[6.0], 0.0, 0.3, 0.2, 0.7) > 0, "проверка систем слева"
    assert _white(frames[7.0]) > 300 and _white(frames[6.0]) < 30, "в финале в кольце — белое лицо Джарвиса вместо шара"
    assert _lit(frames[S.T_END], 0, 0, 1, 1) == 0, "в конце — снова рабочий стол"


def test_title_leaves_before_top_node_arrives(qapp):
    """Верхний узел «Музыка» и полоса-сканер не должны наезжать друг на друга."""
    from ui_intro import IntroScene
    sc = IntroScene()
    sc.render(S.T_NODES + 0.35, 640, 360)
    img = sc.render(S.T_NODES + 0.9, 640, 360)
    title_band = _lit(img, 0.3, 0.1, 0.7, 0.165)
    sc2 = IntroScene()
    sc2.render(S.T_RING + 0.5, 640, 360)
    assert title_band < _lit(sc2.render(S.T_RING + 0.6, 640, 360), 0.3, 0.1, 0.7, 0.165)


def test_failed_check_is_shown_red(qapp):
    from PyQt6.QtGui import QColor

    from ui_intro import IntroScene
    img = IntroScene({"Gemini": False}).render(S.T_FINAL - 0.1, 640, 360)
    reds = sum(1 for x in range(0, 120) for y in range(100, 300)
               if (c := QColor(img.pixel(x, y))).red() > 180 and c.green() < 120)
    assert reds > 0


def test_click_skips_intro(qapp):
    from ui_intro import IntroOverlay
    ov = IntroOverlay()
    done = []
    ov.finished.connect(lambda: done.append(ov.skipped))
    ov.play()
    ov.skip()
    assert done == [True]


def test_every_node_and_check_has_an_icon():
    """Текста на экране нет — у каждого узла и строки проверки своя иконка."""
    assert set(S.NODE_ICONS) == set(S.NODES) and set(S.CHECK_ICONS) == set(S.CHECKS)


def test_no_text_on_screen(qapp, monkeypatch):
    """Интро рисует без текста: drawText не вызывается ни в одном кадре."""
    from PyQt6.QtGui import QPainter

    from ui_intro import IntroScene
    calls = []
    monkeypatch.setattr(QPainter, "drawText", lambda self, *a, **k: calls.append(a))
    sc = IntroScene()
    for i in range(0, int(S.T_END * 10)):
        sc.render(i / 10, 320, 180)
    assert calls == []


@pytest.mark.parametrize("hour,hello", [(3, "Доброй ночи"), (8, "Доброе утро"), (14, "Добрый день"), (20, "Добрый вечер")])
def test_greeting_by_time_of_day(hour, hello):
    assert S.greeting({}, hour) == f"{hello}, сэр. Все системы в норме. Слушаю."


def test_greeting_names_what_is_broken():
    assert "кроме Telegram." in S.greeting({"Telegram": False}, 10)
    assert "кроме Gemini и памяти." in S.greeting({"Gemini": False, "Память": False}, 10)


def test_voice_is_mixed_in_and_music_ducks_under_it():
    rate = 24000
    music = (np.ones(int(9 * rate)) * 10000).astype("<i2").tobytes()
    voice = (np.ones(int(3 * rate)) * 5000).astype("<i2").tobytes()          # голос дольше конца музыки
    out = np.frombuffer(S.mix_voice(music, voice, rate, at=7.0), "<i2").astype(int)
    assert len(out) == int(10 * rate), "звук удлинился под голос"
    assert out[int(5 * rate)] == 10000, "до голоса музыка как была"
    assert out[int(8 * rate)] == int(10000 * 0.35) + 5000, "под голосом музыка тише"
    silent = np.frombuffer(S.mix_voice(music, bytes(2 * rate), rate, at=7.0), "<i2").astype(int)   # 1 с «голоса»
    assert np.abs(np.diff(silent[int(6.7 * rate):int(8.9 * rate)])).max() < 200, "музыка стихает и возвращается плавно"
    assert silent[int(8.5 * rate)] == 10000, "после голоса музыка снова в полную силу"


def test_intro_waits_for_long_greeting(qapp):
    """Фраза Джарвиса длиннее сценария — лицо не исчезает посреди «…Слушаю»."""
    from ui_intro import IntroScene
    sc = IntroScene()
    sc.extend_to(12.0)
    sc.render(S.T_FINAL + 0.5, 640, 360)
    assert _white(sc.render(S.T_END + 0.5, 640, 360)) > 300, "лицо ещё на экране"
    assert _lit(sc.render(12.0, 640, 360), 0, 0, 1, 1) == 0, "к концу фразы — рабочий стол"
