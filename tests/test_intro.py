"""Интро на два хлопка: звук по сценарию, реплики с субтитрами, кадры по этапам, пропуск."""
import numpy as np
import pytest

from core import intro as S


# ── сценарий и звук ──────────────────────────────────────────────────────────

def test_sound_follows_the_script():
    x = S.render()
    assert len(x) == int((S.T_END + 0.8) * S.RATE)
    assert np.max(np.abs(x)) <= 0.9                               # без перегруза
    env = np.array([np.sqrt(np.mean(x[i:i + 2400] ** 2)) for i in range(0, len(x) - 2400, 2400)])
    assert abs(int(np.argmax(env)) * 0.1 - S.T_HIT) < 0.3, "самый громкий момент — удар"
    assert env[int(S.T_HIT * 10) - 2] < 0.5 * env.max(), "перед ударом — «вдох», а не стена звука"
    assert env[int(S.T_FINAL * 10) + 1] > 2 * env[int((S.T_FINAL - 0.3) * 10)], "финальный удар слышен"
    assert env[-2] < 0.05 * env.max(), "к концу затихает, не обрывается"


def test_pcm_for_speakers():
    assert len(S.pcm16(0.6, 24000)) == 2 * int((S.T_END + 0.8) * 24000)


def test_timeline_is_in_order_and_nodes_accelerate():
    assert S.T_DARK < S.T_BOOT < S.T_CHARGE < S.T_HIT < S.T_NODES < S.T_NET < S.T_FINAL < S.T_FADE < S.T_END
    times = S.node_times()
    gaps = np.diff(times)
    assert len(times) == len(S.NODES) and all(gaps[i] >= gaps[i + 1] - 1e-6 for i in range(len(gaps) - 1)), "разгон"
    assert times[-1] + 0.5 < S.T_NET


def test_lines_and_word_timing():
    lines = S.lines({}, 20)
    assert [text for _, text in lines][:2] == ["Проверка систем.", "Подключаю модули."]
    assert lines[-1][1] == "Добрый вечер, сэр. Все системы в норме."
    words = S.word_times("Подключаю модули.", 4.3, 1.5)
    assert [w for w, _ in words] == ["Подключаю", "модули."]
    assert words[0][1] == 4.3 and 4.3 < words[1][1] < 5.8


@pytest.mark.parametrize("hour,hello", [(3, "Доброй ночи"), (8, "Доброе утро"), (14, "Добрый день"), (20, "Добрый вечер")])
def test_greeting_by_time_of_day(hour, hello):
    assert S.greeting({}, hour) == f"{hello}, сэр. Все системы в норме."


def test_greeting_names_what_is_broken():
    assert "кроме Telegram." in S.greeting({"Telegram": False}, 10)
    assert "кроме Gemini и памяти." in S.greeting({"Gemini": False, "Память": False}, 10)


def test_prewarm_covers_every_line_that_can_be_said_without_failures():
    texts = S.prewarm_texts()
    for hour in (3, 8, 14, 20):
        assert all(text in texts for _, text in S.lines({}, hour))


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
    assert silent[int(8.5 * rate)] == 10000


def test_two_voices_both_duck():
    rate = 24000
    music = (np.ones(int(12 * rate)) * 10000).astype("<i2").tobytes()
    v = bytes(int(1 * rate) * 2)
    out = np.frombuffer(S.mix_voice(S.mix_voice(music, v, rate, at=1.0), v, rate, at=5.0), "<i2").astype(int)
    assert out[int(1.5 * rate)] == 3500 and out[int(5.5 * rate)] == 3500 and out[int(3.5 * rate)] == 10000


def test_envelope_follows_voice():
    rate = 24000
    v = np.concatenate([np.zeros(rate // 2), np.ones(rate // 2) * 8000]).astype("<i2").tobytes()
    env = S.envelope(v, rate)
    assert env[2] == 0.0 and env[-2] == 1.0


# ── кадры ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _lit(img, x0, y0, x1, y1, level=150):
    from PyQt6.QtGui import QColor
    w, h = img.width(), img.height()
    return sum(1 for x in range(int(x0 * w), int(x1 * w), 3) for y in range(int(y0 * h), int(y1 * h), 3)
               if QColor(img.pixel(x, y)).lightness() > level)


def _scene(checks=None):
    from datetime import datetime

    from ui_intro import IntroScene
    return IntroScene(checks, now=datetime(2026, 10, 2, 18, 39))


def test_frames_show_each_stage(qapp):
    sc = _scene()
    fr = {t: sc.render(t, 640, 360) for t in (0.15, 0.75, 1.3, 2.2, 3.6, S.T_HIT + 0.04, 7.2)}
    assert _lit(fr[0.15], 0.3, 0.45, 0.7, 0.55) > 0, "экран схлопывается в светлую полосу"
    assert _lit(fr[0.75], 0, 0, 1, 1, 60) == 0, "потом — темнота"
    assert _lit(fr[2.2], 0.0, 0.0, 0.25, 0.15) > 0, "часы HUD слева сверху"
    assert _lit(fr[1.3], 0.25, 0.85, 0.75, 0.93) > 0, "субтитр «Проверка систем.»"
    assert _lit(fr[3.6], 0.4, 0.3, 0.6, 0.6) > 0, "загорается реактор"
    assert _lit(fr[S.T_HIT + 0.04], 0, 0, 1, 1, 120) > 2 * _lit(fr[3.6], 0, 0, 1, 1, 120), "вспышка удара"
    assert _lit(fr[7.2], 0.0, 0.15, 0.3, 0.8) > 0, "модули слева от реактора"


def test_nodes_are_big_with_icons_and_labels(qapp):
    from ui_intro import _icon_path
    for _, icon in S.NODES:
        assert not _icon_path(icon).isEmpty(), icon
    sc = _scene()
    img = sc.render(7.2, 1280, 720)
    from PyQt6.QtGui import QColor
    x, y = sc.node_pos(0, 640, 720 * 0.455, min(1280, 720) * 0.13)
    whites = sum(1 for dx in range(-30, 31, 2) for dy in range(-30, 31, 2)
                 if QColor(img.pixel(int(x + dx), int(y + dy))).lightness() > 200)
    assert whites > 10, "в верхнем узле — белая иконка, не пустой кружок"


def test_reactor_coils_light_up_in_turn_then_core_flares(qapp):
    sc = _scene()
    mid = S.T_CHARGE + 0.3 + 4.5 * (S.T_HIT - S.T_CHARGE - 0.6) / sc.COILS
    lit = [sc._coil_power(i, mid) for i in range(sc.COILS)]
    assert lit[0] == 1.0 and lit[-1] == 0.0, "катушки зажигаются по кругу, а не все сразу"
    assert sc._power(S.T_HIT - 0.05) < sc._power(S.T_HIT - 0.4), "«вдох» перед ударом"
    assert sc._power(S.T_HIT + 0.02) > 1.5 > sc._power(S.T_HIT + 1.5) >= 1.0, "вспышка ядра и ровный свет"


def test_failed_module_flashes_red_during_check(qapp):
    from PyQt6.QtGui import QColor
    sc = _scene({"Telegram": False})
    i = [n for n, _ in S.NODES].index("Telegram")
    passed = S.T_NET + 1.1 * i / len(S.NODES)
    for t in np.arange(S.T_HIT, passed + 0.06, 0.05):
        img = sc.render(float(t), 1280, 720)
    x, y = sc.node_pos(i, 640, 720 * 0.455, min(1280, 720) * 0.13)
    reds = sum(1 for dx in range(-40, 41) for dy in range(-40, 41)
               if (c := QColor(img.pixel(int(x + dx), int(y + dy)))).red() > 170 and c.green() < 130)
    assert reds > 5


def test_subtitles_follow_real_voice_length(qapp):
    sc = _scene()
    sc.set_voice([(0.9, 3.0, "Проверка систем.")], [0.0] * 30 + [1.0] * 100)
    assert _lit(sc.render(3.5, 640, 360), 0.25, 0.85, 0.75, 0.93) > 0, "длинная реплика — субтитр ещё на экране"
    assert sc.voice_level(1.0) == 1.0 and sc.voice_level(0.5) == 0.0


def test_intro_waits_for_long_greeting_and_ends_clean(qapp):
    sc = _scene()
    sc.extend_to(15.0)
    for t in np.arange(S.T_HIT, S.T_END + 0.5, 0.25):
        sc.render(float(t), 320, 180)
    assert _lit(sc.render(S.T_END + 0.5, 640, 360), 0.4, 0.3, 0.6, 0.6) > 0, "реактор ещё на экране"
    img = sc.render(15.0, 640, 360)
    from PyQt6.QtGui import QColor
    assert QColor(img.pixel(320, 180)) == QColor("#203040"), "в конце — снова рабочий стол"


def test_click_skips_intro(qapp):
    from ui_intro import IntroOverlay
    ov = IntroOverlay()
    done = []
    ov.finished.connect(lambda: done.append(ov.skipped))
    ov.play()
    ov.skip()
    assert done == [True]
