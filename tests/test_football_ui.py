"""Футбол на экране: капсула (живой счёт, «ГОЛ!» с салютом, итог) и экран
«Футбол» в окне (карточка матча, результаты В/Н/П, новости, переключатели,
«Смотреть на Кинопоиске»). Сеть подменена (tests/test_football.Net)."""
import os
import time
from datetime import timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui_island as ui  # noqa: E402
from core import football as F  # noqa: E402
from test_football import NOW, RSS, Net  # noqa: E402

GOAL = "Real Madrid 1:0 Barcelona (23')"


# ── капсула: модель ──────────────────────────────────────────────────────────

def test_parse_score_and_colors():
    sc = ui.parse_score(GOAL)
    assert (sc.home, sc.home_score, sc.away_score, sc.away, sc.detail) == ("Real Madrid", "1", "0", "Barcelona", "23'")
    assert ui.parse_score("1. FC Köln 2:2 Bayern Munich").home == "1. FC Köln"
    assert ui.parse_score("Real Madrid — Barcelona") is None
    assert sc.abbr("home") == "REA"                                    # нет сокращения ESPN — первые буквы
    sc.away_abbr, sc.away_color = "BAR", "000044"
    assert sc.abbr("away") == "BAR"
    r, g, b = sc.rgb("away")
    assert 0.3 * r + 0.59 * g + 0.11 * b > 60                          # тёмно-синий виден на чёрной капсуле
    assert sc.rgb("home") == sc.rgb("home")                            # без цвета — свой, но постоянный


def test_live_match_goal_and_final_views():
    m = ui.IslandModel()
    m.media = ui.Media("Трек", "", "music", True)
    live = ui.Score("Real Madrid", "Barcelona", "0", "0", "12'", "RMA", "BAR", "ffffff", "004d98")
    m.set_match(live, now=0)
    assert m.mode(False, now=0) == "match" and not m.quiet(now=0)      # счёт важнее музыки, капсула не уходит
    m.football("ГОЛ", GOAL, now=1)
    assert m.mode(False, now=1) == "goal" and m.goal_side == "home" and m.pop_side == "home"
    assert m.match.home_score == "1" and m.match.home_abbr == "RMA"    # цвета и сокращения — из живого матча
    m.set_match(ui.Score("Real Madrid", "Barcelona", "0", "0", "23'"), now=2)
    assert m.match.home_score == "1"                                   # опрос не откатывает счёт гола
    assert m.mode(False, now=1 + ui.GOAL_SEC + 0.1) == "match"         # салют прошёл — снова живой счёт
    m.football("ИТОГ", "Real Madrid 2:1 Barcelona", now=20)
    assert m.match is None and m.mode(False, now=20) == "goal" and m.banners[-1].kind == "final"
    assert m.mode(False, now=40) == "activity"                         # матч кончился — снова музыка
    m.football("НОВОСТЬ КЛУБА", "Реал подписал контракт", now=50)
    assert m.mode(False, now=50) == "banner" and m.banners[-1].kind == "football"


# ── капсула: окно ────────────────────────────────────────────────────────────

@pytest.fixture
def island():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    w = ui.Island(poll=False)
    yield w, app
    w.hide()
    w.deleteLater()


def _draw(w, mode):
    from PyQt6.QtGui import QColor, QImage
    tw, th = ui.SIZES[mode]
    if mode == "expanded" and w.model.match:
        th += ui.EXPANDED_MATCH_EXTRA
    w._w, w._h, w._r, w._s, w._view, w._ca = tw, th, 1.0, 1.0, mode, 1.0
    w.hovered = mode == "expanded"
    img = QImage(w.W, w.H, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    w.render(img)
    return img


def _bright(img, x0, x1, y0, y1):
    return sum(1 for x in range(x0, x1, 2) for y in range(y0, y1, 2) if img.pixelColor(x, y).lightness() > 150)


def test_every_football_view_draws(island):
    w, _app = island
    w.model.set_match(ui.Score("Real Madrid", "Barcelona", "0", "0", "12'", "RMA", "BAR", "ffffff", "004d98"))
    assert _bright(_draw(w, "match"), 80, 380, 0, 34) > 20
    assert _bright(_draw(w, "expanded"), 10, 450, 40, 100) > 20        # строка матча в панели
    w.model.football("ГОЛ", GOAL)
    for t in (0.05, 0.4, 1.0, 3.0):                                    # вся анимация, от вспышки до покоя
        w.model.goal_at = time.monotonic() - t
        img = _draw(w, "goal")
        assert _bright(img, 30, 200, 10, 70) > 30, t                   # «ГОЛ!»
        assert _bright(img, 230, 440, 10, 50) > 30, t                  # табло
    w.model.banners.clear()
    w.model.football("ИТОГ", "Real Madrid 2:1 Barcelona")
    assert _bright(_draw(w, "goal"), 30, 440, 10, 70) > 30


def test_window_routes_football_log_to_capsule(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import ui as jarvis_ui
    win = jarvis_ui.JarvisUI("face.png")
    try:
        isl = win._island
        win.write_log(f"SYS: ⚽ ГОЛ: {GOAL}")
        app.processEvents()
        assert isl.model.banners[-1].kind == "goal" and isl.model.match.home_score == "1"
    finally:
        isl.close()
        win.hide()
        win.deleteLater()


# ── данные для экрана ────────────────────────────────────────────────────────

@pytest.fixture
def fb(tmp_path):
    net = Net()
    net.rss = RSS
    clock = {"t": NOW}
    f = F.Football(tmp_path / "football.json", now=lambda: clock["t"], get_json=net.json, get_raw=net.raw)
    f.set_club("Реал")
    return f, net, clock


def test_overview_and_live_current(fb):
    f, net, clock = fb
    o = f.overview()
    assert o["name"] == "Реал Мадрид" and o["next"].id == "2"
    assert [m.id for m in o["results"]] == ["1"] and [m.id for m in o["upcoming"]] == ["3"]
    assert len(o["news"]) == 3 and f.current is None
    clock["t"] = net.kick + timedelta(minutes=12)
    f.tick()
    assert f.current and f.current.state == "in"                       # капсула увидит матч
    assert ui.poll_match() is None                                     # чужой экземпляр — не наш _fb
    assert F.club_names("Real Madrid")[1] == "Реал Мадрид"             # ESPN → по-русски для Кинопоиска


def test_result_letter_and_countdown():
    import ui_football as U
    m = F.Match("1", NOW, "Real Madrid", "Sevilla", "1", "3", "post")
    assert U.result_letter(m, "Real Madrid") == "П" and U.result_letter(m, "Sevilla") == "В"
    m.away_score = "1"
    assert U.result_letter(m, "Real Madrid") == "Н"
    assert U.countdown(NOW + timedelta(hours=2, minutes=5), NOW) == "через 2 ч 05 мин"
    assert U.countdown(NOW + timedelta(seconds=75), NOW) == "через 1:15"
    assert U.countdown(NOW - timedelta(minutes=1), NOW) == "вот-вот начнётся"


# ── экран «Футбол» ───────────────────────────────────────────────────────────

def test_football_page(fb, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import ui_football as U
    f, net, clock = fb
    d = U.FootballDialog(None, fb=f, auto_refresh=False)
    try:
        d.show()
        d._apply(f.overview())
        app.processEvents()
        assert d.title.text() == "Футбол · Реал Мадрид"
        assert d.results.count() == 1 and d.upcoming.count() == 1 and d.news.count() == 3
        assert d.hero.match.id == "2" and d.hero.score.home == "Real Madrid"
        d.hero.grab()                                                   # рисуется без ошибок
        # идёт матч: счёт поменялся — цифра подпрыгивает
        clock["t"] = net.kick + timedelta(minutes=30)
        d._apply(f.overview())
        net.live.update(hs="1", detail="31'")
        d._apply(f.overview())
        assert d.hero.score.home_score == "1" and d.hero._pop_side == "home"
        d.hero.grab()
        # переключатели пишут в настройки клуба
        d.goals_t.setChecked(False)
        assert F.Football(f.path).state["goals"] is False
        # «Смотреть на Кинопоиске» — тот же поиск, что голосом
        asked = []
        monkeypatch.setattr(F, "watch_match", lambda q: asked.append(q) or "Включил.")
        d._watch()
        end = time.monotonic() + 3
        while not asked or d.status.text() != "Включил." or not d.watch_btn.isEnabled():
            app.processEvents()
            assert time.monotonic() < end
        assert asked == ["Real Madrid против Barcelona"] and d.watch_btn.isEnabled()
        # без клуба — подсказка, а не пустота
        d._apply({"club": ""})
        assert d.title.text() == "Футбол" and d.results.count() == 1
        d.hero.grab()
    finally:
        d.hide()
        d.deleteLater()
