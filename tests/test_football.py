"""Любимый клуб: «Реал» находится у ESPN, ближайший и прошедший матч,
напоминание один раз, «Гол!» по смене счёта, итог, важные новости без
повторов, брифинг, голосовые команды. Сеть подменена."""
from datetime import datetime, timedelta, timezone

import pytest

from core import football as F

NOW = datetime(2026, 9, 28, 21, 0)                     # местное время теста


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _event(eid, when, home, away, hs="", as_="", state="pre", detail="", league="LaLiga", score_obj=False):
    def sc(v):
        return {"value": float(v), "displayValue": v} if (score_obj and v != "") else v
    return {"id": eid, "date": _utc(when), "league": {"name": league},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"id": "86", "displayName": home}, "score": sc(hs)},
                {"homeAway": "away", "team": {"id": "83", "displayName": away}, "score": sc(as_)}],
                "status": {"type": {"state": state, "shortDetail": detail}}}]}


class Net:
    def __init__(self):
        self.calls = []
        self.live = {"hs": "0", "as": "0", "state": "in", "detail": "12'"}
        self.rss = b""
        self.kick = NOW + timedelta(minutes=20)

    def json(self, url):
        self.calls.append(url)
        if url.endswith("/esp.1/teams"):
            return {"sports": [{"leagues": [{"teams": [
                {"team": {"id": "83", "displayName": "Barcelona", "shortDisplayName": "Barcelona"}},
                {"team": {"id": "86", "displayName": "Real Madrid", "shortDisplayName": "Real Madrid"}}]}]}]}
        if url.endswith("/teams"):
            return {"sports": [{"leagues": [{"teams": []}]}]}
        if "/esp.1/teams/86/schedule" in url:
            return {"events": [
                _event("1", NOW - timedelta(days=1), "Real Madrid", "Sevilla", "3", "1", "post", "FT", score_obj=True),
                _event("2", self.kick, "Real Madrid", "Barcelona")]}
        if "/uefa.champions/teams/86/schedule" in url:
            return {"events": [_event("3", NOW + timedelta(days=3), "Liverpool", "Real Madrid",
                                      league="UEFA Champions League")]}
        if url.endswith("/esp.1/scoreboard"):
            L = self.live
            return {"events": [_event("2", self.kick, "Real Madrid", "Barcelona", L["hs"], L["as"], L["state"],
                                      L["detail"])]}
        raise OSError("нет такого адреса")

    def raw(self, url):
        self.calls.append(url)
        return self.rss


@pytest.fixture
def fb(tmp_path):
    net = Net()
    clock = {"t": NOW}
    f = F.Football(tmp_path / "football.json", now=lambda: clock["t"], get_json=net.json, get_raw=net.raw)
    said, notes = [], []
    f.say, f.notify = said.append, lambda t, x: notes.append((t, x))
    return f, net, clock, said, notes


def test_russian_name_finds_club_and_fixtures(fb):
    f, net, *_ = fb
    assert F.club_names("Реал") == ("Real Madrid", "Реал Мадрид") and F.club_names("барсу")[0] == "Barcelona"
    assert "Реал Мадрид" in f.set_club("Реал")
    assert f.state["team"] == {"id": "86", "name": "Real Madrid", "league": "esp.1"}
    ms = f.fixtures()
    assert [m.id for m in ms] == ["1", "2", "3"]                         # лига + Лига чемпионов, по времени
    assert ms[0].home_score == "3" and ms[0].state == "post"             # счёт объектом тоже читается
    assert f.next_match().id == "2" and f.last_match().id == "1"
    assert "Real Madrid — Barcelona — сегодня в 21:20" in f.next_text()
    assert "вчера" in f.last_text() and "3:1" in f.last_text()
    calls = len(net.calls)
    f.fixtures()
    assert len(net.calls) == calls                                       # кэш, сеть не дёргаем
    assert F.Football(f.path, get_json=net.json).state["team"]["id"] == "86"   # клуб запомнен


def test_reminder_goal_and_final(fb):
    f, net, clock, said, notes = fb
    f.set_club("Реал")
    f.tick()
    f.tick()
    assert [t for t, _ in notes].count("МАТЧ") == 1 and "через 20 мин" in notes[0][1]   # один раз
    clock["t"] = net.kick + timedelta(minutes=12)
    f.tick()                                                             # счёт 0:0 — только «начался»
    assert ("МАТЧ НАЧАЛСЯ", "Real Madrid — Barcelona") in notes and not any(t == "ГОЛ" for t, _ in notes)
    net.live.update(hs="1", detail="23'")
    clock["t"] += timedelta(minutes=11)
    f.tick()
    assert ("ГОЛ", "Real Madrid 1:0 Barcelona (23')") in notes and "гол" in said[-1].lower()
    f.tick()
    assert [t for t, _ in notes].count("ГОЛ") == 1                       # тот же счёт — не повторяет
    net.live.update(state="post", detail="FT")
    clock["t"] += timedelta(minutes=80)
    f.tick()
    f.tick()
    assert [t for t, _ in notes].count("ИТОГ") == 1 and "1:0" in said[-1]


def test_goals_can_be_muted(fb):
    f, net, clock, said, notes = fb
    f.set_club("Реал")
    f.state["goals"] = False
    clock["t"] = net.kick + timedelta(minutes=5)
    f.tick()
    net.live.update(hs="1")
    f.tick()
    assert not any(t == "ГОЛ" for t, _ in notes)


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Реал подписал контракт с защитником - Sports.ru</title><link>https://a</link>
<pubDate>Sun, 28 Sep 2026 15:00:00 GMT</pubDate><source>Sports.ru</source></item>
<item><title>Болельщики Реала устроили праздник - Чемпионат</title><link>https://b</link>
<pubDate>Sun, 28 Sep 2026 14:00:00 GMT</pubDate><source>Чемпионат</source></item>
<item><title>Винисиус получил травму колена - Матч ТВ</title><link>https://c</link>
<pubDate>Sun, 28 Sep 2026 13:00:00 GMT</pubDate><source>Матч ТВ</source></item>
<item><title>Старая новость про трансфер - X</title><link>https://d</link>
<pubDate>Mon, 01 Sep 2026 13:00:00 GMT</pubDate><source>X</source></item>
</channel></rss>""".encode()


def test_important_news_once(fb, monkeypatch):
    f, net, clock, said, notes = fb
    net.rss = RSS
    f.set_club("Реал")
    items = f.news(limit=10)
    assert [n.title for n in items] == ["Реал подписал контракт с защитником", "Болельщики Реала устроили праздник",
                                        "Винисиус получил травму колена"]           # старое — отброшено
    assert items[0].source == "Sports.ru"
    assert "%22%D0%A0%D0%B5%D0%B0%D0%BB" in net.calls[-1]                            # искали «"Реал Мадрид"»
    f.state["news_at"] = 0
    f.tick()
    got = [x for t, x in notes if t == "НОВОСТЬ КЛУБА"]
    assert got == ["Реал подписал контракт с защитником", "Винисиус получил травму колена"]   # только важные
    f.state["news_at"] = 0
    f.tick()
    assert len([t for t, _ in notes if t == "НОВОСТЬ КЛУБА"]) == 2                   # не повторяет


def test_no_club_no_network(fb):
    f, net, *_ = fb
    f.tick()
    assert net.calls == [] and "не указан" in f.next_text() and f.briefing() == ""


def test_briefing_and_tool(fb, monkeypatch):
    f, net, clock, *_ = fb
    monkeypatch.setattr(F, "_fb", f)
    res = F.football_tool({"action": "set_club", "club": "Реал"})
    assert "Реал Мадрид" in res
    from core import about_me
    assert about_me.answers()["club"] == "Реал"                                      # и в анкете
    b = f.briefing()
    assert "3:1" in b and "Real Madrid — Barcelona сегодня в 21:20" in b
    assert "Ближайший матч" in F.football_tool({"action": "next"})
    assert "3:1" in F.football_tool({"action": "last"})
    assert "сообщать не буду" in F.football_tool({"action": "goals_off"}) and f.state["goals"] is False
    clock["t"] = net.kick + timedelta(minutes=30)
    assert F.football_tool({"action": "score"}).startswith("Сейчас Real Madrid 0:0")


def test_club_set_in_other_process_is_picked_up(fb, tmp_path):
    f, net, *_ = fb
    other = F.Football(f.path, get_json=net.json, get_raw=net.raw)                  # pc_server: анкета с телефона
    other.set_club("Реал")
    f.tick()
    assert f.state["club"] == "Реал" and f.state["team"]["id"] == "86"


# ── «поставь матч …» на Кинопоиске ──────────────────────────────────────────

def test_match_teams_and_pick():
    teams = F.match_teams("поставь матч Реала против Барсы")
    assert [t[0] for t in teams] == ["Real Madrid", "Barcelona"]
    links = [{"href": "https://hd.kinopoisk.ru/sport/competition/37526/", "text": "Ла Лига: Реал Мадрид, Барселона"},
             {"href": "https://hd.kinopoisk.ru/sport/event/1/", "text": "Севилья — Барселона"},
             {"href": "https://hd.kinopoisk.ru/sport/event/2/", "text": "Реал Мадрид — Барселона. Эль-Класико"}]
    assert F.pick_match(links, teams) == "https://hd.kinopoisk.ru/sport/event/2/"     # матч, а не турнир
    assert F.pick_match(links[:2], teams) == links[0]["href"]                          # турнир — если матча нет
    assert F.pick_match(links[1:2], teams) is None


class Tab:
    def __init__(self, links, login=False, play="button"):
        self.links, self.login, self.play, self.opened = links, login, play, []

    def navigate(self, url):
        self.opened.append(url)

    def wait(self, js, timeout=10):
        return True

    def eval(self, js):
        if "querySelectorAll('a[href]')" in js:
            return __import__("json").dumps(self.links)
        if "Войти" in js:
            return self.login
        if "смотреть" in js:
            return self.play
        return None


def test_watch_match_opens_and_plays(monkeypatch):
    from core import browser_cdp as cdp
    monkeypatch.setattr(cdp, "bring_to_front", lambda: None)
    monkeypatch.setattr(cdp, "fullscreen", lambda on=True, t=None: True)
    tab = Tab([{"href": "https://hd.kinopoisk.ru/sport/event/2/", "text": "Реал Мадрид — Барселона"}])
    res = F.watch_match("Реал против Барселоны", tab=tab, pages=["https://hd.kinopoisk.ru/sport/"], wait=lambda s: 0)
    assert res.startswith("Включил Реал Мадрид — Барселона")
    assert tab.opened == ["https://hd.kinopoisk.ru/sport/", "https://hd.kinopoisk.ru/sport/event/2/"]


def test_watch_match_not_found_hints_login():
    tab = Tab([], login=True)
    res = F.watch_match("Реал против Барсы", tab=tab, pages=["a", "b"], wait=lambda s: 0)
    assert "не нашёл" in res and "вход" in res and tab.opened == ["a", "b"]
    assert "Какой матч" in F.watch_match("", tab=tab)


def test_browser_prefers_chrome_and_yandex(monkeypatch):
    from actions import browser_control
    from core import browser_cdp as cdp
    have = {"edge": "msedge.exe", "yandex": "browser.exe"}
    monkeypatch.delenv("JARVIS_BROWSER", raising=False)
    monkeypatch.setattr(cdp.sys, "platform", "win32")
    monkeypatch.setattr(browser_control, "_browser_exe", lambda name: have.get(name))
    assert cdp.browser_exe() == "browser.exe"
    have["chrome"] = "chrome.exe"
    assert cdp.browser_exe() == "chrome.exe"


def test_espn_request_without_browser_ua_then_fallback(monkeypatch):
    """ESPN режет «браузерный» User-Agent (403) — сначала без него, отказ — запасной адрес."""
    import urllib.error
    asked = []

    def get(url, timeout=10.0, ua="Mozilla/5.0 Jarvis/1.0"):
        asked.append((url, ua))
        if url.startswith(F.ESPN):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return b'{"ok": 1}'
    monkeypatch.setattr(F, "_get", get)
    assert F._json(f"{F.ESPN}/esp.1/teams") == {"ok": 1}
    assert asked[0] == (f"{F.ESPN}/esp.1/teams", None)                          # без «Mozilla»
    assert asked[1] == (f"{F.ESPN_WEB}/esp.1/teams", F.CHROME_UA)
    asked.clear()
    assert F._json("https://news.example/rss") == {"ok": 1} and asked[0][1].startswith("Mozilla")
