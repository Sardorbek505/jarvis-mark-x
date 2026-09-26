"""Фильмы (VK), ролики (YouTube) и голосовое управление просмотром.

Модульные тесты — на подменённом плеере. Интеграционный — на настоящем
Chrome/Chromium с локальной страницей и видео (пропускается, если браузера
нет): запуск, полный экран, перемотка, громкость, mute, пауза, время.
"""
import http.server
import os
import re
import shutil
import subprocess
import threading

import pytest

from actions import video_player as vp
from core import browser_cdp as cdp


# ── разбор времени ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,sec", [
    ("1:20:00", 4800), ("20:30", 1230), ("5 минут", 300), ("10 секунд", 10),
    ("полчаса", 1800), ("1 час 20 минут", 4800), ("две минуты", 120), ("30", 30),
])
def test_parse_seconds(text, sec):
    assert vp.parse_seconds(text) == sec


# ── выбор фильма из выдачи VK ────────────────────────────────────────────────

RESULTS = [
    {"id": "-1_1", "title": "Железный человек 2 — официальный трейлер", "dur": "2:31"},
    {"id": "-1_2", "title": "Железный человек 2 (2010) фильм", "dur": "2:04:47"},
    {"id": "-1_3", "title": "Обзор фильма Железный человек 2", "dur": "18:02"},
    {"id": "-1_4", "title": "Мстители", "dur": "2:23:00"},
]


def test_film_prefers_full_movie_over_trailer_and_review():
    assert vp._pick_vk(RESULTS, "Железный человек 2", film=True)["id"] == "-1_2"


def test_non_film_search_takes_best_title_match():
    assert vp._pick_vk(RESULTS[:1], "Железный человек 2", film=False)["id"] == "-1_1"


# ── управление на подменённом плеере ─────────────────────────────────────────

@pytest.fixture
def fake_player(monkeypatch):
    st = {"paused": False, "t": 600.0, "d": 7200.0, "vol": 0.5, "muted": False, "fs": True, "ready": 4}

    def video_js(body, t=None, await_promise=False):
        if "v.pause()" in body and "paused ?" not in body:
            st["paused"] = True
        if "v.play()" in body:
            st["paused"] = False
        m = re.search(r"v\.currentTime \+ (-?\d+(?:\.\d+)?)", body)
        if m:
            st["t"] = max(0, min(st["d"], st["t"] + float(m.group(1))))
        m = re.search(r"Math\.min\(v\.duration \|\| 1e9, (\d+(?:\.\d+)?)\)", body)
        if m:
            st["t"] = float(m.group(1))
        m = re.search(r"v\.volume = (\d+(?:\.\d+)?)", body)
        if m:
            st["vol"] = float(m.group(1))
        if "v.muted = true" in body:
            st["muted"] = True
        return dict(st)

    monkeypatch.setattr(cdp, "video_state", lambda t=None: dict(st))
    monkeypatch.setattr(cdp, "video_js", video_js)
    monkeypatch.setattr(cdp, "fullscreen", lambda on=True, t=None: st.update(fs=on) or True)
    return st


def test_controls_do_what_they_say(fake_player):
    assert vp.control("pause") == "Пауза." and fake_player["paused"]
    assert vp.control("resume") == "Продолжаю."
    assert vp.control("seek_forward", "5 минут") == "Вперёд на 5 мин — 15:00."
    assert vp.control("seek_back", None) == "Назад на 10 с — 14:50."
    assert vp.control("seek_to", "1:20:00") == "Перемотал на 1:20:00."
    assert vp.control("time") == "Идёт 1:20:00 из 2:00:00, осталось 40 мин."
    assert vp.control("volume_set", "30") == "Громкость видео 30%."
    assert vp.control("volume_up") == "Громкость видео 45%."
    assert vp.control("mute") == "Звук видео выключен." and fake_player["muted"]
    assert vp.control("exit_fullscreen") == "Вышел из полного экрана." and not fake_player["fs"]


def test_no_video_returns_empty_for_caller(monkeypatch):
    monkeypatch.setattr(cdp, "video_state", lambda t=None: None)
    assert vp.control("pause") == ""


def test_music_pause_goes_to_film_when_only_film_plays(monkeypatch, fake_player):
    from actions import music_player as mp
    monkeypatch.setattr(vp, "video_playing", lambda: True)
    monkeypatch.setattr(mp, "_music_playing", lambda: False)
    assert mp.music_player({"action": "pause"}) == "Пауза." and fake_player["paused"]


# ── настоящий браузер ────────────────────────────────────────────────────────

def _chrome():
    for c in (os.getenv("JARVIS_BROWSER"), shutil.which("google-chrome"), shutil.which("chromium"),
              "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"):
        if c and os.path.isfile(c):
            return c
    return None


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


class _Range(http.server.SimpleHTTPRequestHandler):
    """Отдаёт файлы кусками (Range) — как настоящие видеосайты: без этого
    перемотка в браузере сбрасывается на начало."""

    def send_head(self):
        path = self.translate_path(self.path)
        rng = self.headers.get("Range")
        if not rng or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        a, b = re.match(r"bytes=(\d*)-(\d*)", rng).groups()
        a, b = int(a or 0), (int(b) if b else size - 1)
        f = open(path, "rb")
        f.seek(a)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.send_header("Content-Length", str(b - a + 1))
        self.end_headers()
        self._left = b - a + 1
        return f

    def copyfile(self, src, dst):
        left = getattr(self, "_left", None)
        if left is None:
            return super().copyfile(src, dst)
        while left > 0:
            chunk = src.read(min(65536, left))
            if not chunk:
                break
            dst.write(chunk)
            left -= len(chunk)

    def log_message(self, *a):
        pass


@pytest.mark.skipif(not (_chrome() and _ffmpeg()), reason="нет Chrome/Chromium или ffmpeg")
def test_real_browser_film_flow(tmp_path, monkeypatch):
    subprocess.run([_ffmpeg(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=duration=90:size=160x120:rate=5", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=90", "-c:v", "libvpx", "-b:v", "60k",
                    "-c:a", "libopus", "-shortest", str(tmp_path / "movie.webm")], check=True)
    (tmp_path / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Фильм — VK Видео</title>'
        '<div class="videoplayer"><video src="movie.webm" style="width:640px;height:360px"></video></div>'
        '<video src="movie.webm" style="width:60px;height:40px"></video>', encoding="utf-8")
    handler = lambda *a, **k: _Range(*a, directory=str(tmp_path), **k)  # noqa: E731
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    monkeypatch.setattr(cdp, "PORT", 9400 + os.getpid() % 500)
    monkeypatch.setattr(cdp, "_tab", None)
    monkeypatch.setattr(cdp, "_profile_dir", lambda: str(tmp_path / "profile"))
    monkeypatch.setenv("JARVIS_BROWSER", _chrome())
    args = "--headless=new --mute-audio" + (" --no-sandbox" if hasattr(os, "geteuid") and os.geteuid() == 0 else "")
    monkeypatch.setenv("JARVIS_BROWSER_ARGS", args)
    try:
        assert cdp.ensure_browser(), cdp.browser_log()[-2000:]
        st = vp._start(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
        assert st and not st["paused"] and st["fs"], st
        assert vp.control("seek_to", "0:45") == "Перемотал на 0:45."
        assert vp.control("seek_forward", "15 секунд").endswith("1:00.")
        assert vp.control("volume_set", "40") == "Громкость видео 40%."
        assert vp.control("mute") == "Звук видео выключен."
        assert vp.control("pause") == "Пауза."
        assert vp.control("time").startswith("Идёт 1:00 из 1:30")
        assert vp.control("exit_fullscreen") == "Вышел из полного экрана."
        assert vp.video_playing() is False
    finally:
        try:
            if cdp._proc:
                cdp._proc.terminate()
        finally:
            srv.shutdown()


# ─── Провал не должен повторяться по кругу ───────────────────────────────────
def _film_that_never_starts(monkeypatch):
    """VK открывается, но плеер не поднимается — живой случай без входа в VK."""
    calls = []
    monkeypatch.setattr(cdp, "ensure_browser", lambda: True)
    monkeypatch.setattr(vp, "vk_find", lambda t, film=True: "https://vkvideo.ru/x")
    monkeypatch.setattr(vp, "_start", lambda url, fullscreen=True, wait_sec=15.0:
                        calls.append(url) or None)
    vp._last_fail = None
    return calls


def test_failed_film_says_plainly_that_retrying_will_not_help(monkeypatch):
    """Ответ «Открыл …, но плеер не загрузился» модель читала как «почти
    получилось» и звала инструмент снова: в живом журнале семь заходов по
    18 секунд подряд, 27 секунд молчания и убитая сессия Gemini."""
    _film_that_never_starts(monkeypatch)

    answer = vp.play_film("Железный человек")

    assert not answer.startswith("Открыл"), answer
    assert "не" in answer.lower() and "vk" in answer.lower()
    assert "повтор" in answer.lower(), "модель должна понять, что повторять бесполезно"


def test_same_film_is_not_retried_immediately(monkeypatch):
    calls = _film_that_never_starts(monkeypatch)
    now = [1000.0]
    monkeypatch.setattr(vp.time, "monotonic", lambda: now[0])

    first = vp.play_film("Железный человек")
    second = vp.play_film("Железный человек")

    assert len(calls) == 1, f"второй заход ходил в VK снова: {calls}"
    assert second == first

    now[0] += vp._FAIL_COOLDOWN_SEC + 1          # остыло — пробуем заново
    vp.play_film("Железный человек")
    assert len(calls) == 2


def test_another_film_is_still_tried(monkeypatch):
    calls = _film_that_never_starts(monkeypatch)
    monkeypatch.setattr(vp.time, "monotonic", lambda: 1000.0)

    vp.play_film("Железный человек")
    vp.play_film("Интерстеллар")

    assert len(calls) == 2, "чужой фильм не должен упираться в чужую неудачу"
