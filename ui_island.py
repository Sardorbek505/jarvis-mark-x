"""«Капсула» Джарвиса — как Dynamic Island, когда окно свёрнуто.

Чёрная капсула вверху по центру экрана. Внутри — крошечный шар из точек
цвета состояния (ждёт / слушает / думает / говорит), он дышит голосом.
Капсула сама раскрывается на события и сворачивается обратно:
  • ответ Джарвиса — субтитром;
  • что играет (Spotify, медиа-сессии Windows, видео Джарвиса) — с эквалайзером;
  • таймер сна, ближайший звонок по расписанию;
  • матч любимого клуба: живой счёт с минутой, на гол — вспышка цветом
    команды, конфетти, катящийся мяч и «подпрыгнувшая» цифра счёта.
Наведи мышь — откроется панель: плеер с кнопками, таймер, последний ответ.
Клик по капсуле возвращает окно Джарвиса. В полноэкранной игре или фильме
капсула прячется. В покое (ничего не играет, никто не зовёт, таймер не
идёт) она через IDLE_HIDE_SEC сама уходит и возвращается на событие.

Рисование: окно прозрачное, клики проходят сквозь пустые пиксели сами —
маску окна не меняем (на Windows прозрачное окно с меняющейся маской не
стирало старые кадры: рамки и текст оставляли шлейф). Содержимое при смене
вида сначала гаснет, капсула меняет размер, новое проявляется, когда размер
почти готов, — тексты двух видов никогда не рисуются друг на друге.

Модель (IslandModel) отдельно от рисования: что показать — решает она,
и это проверяется тестами без экрана.
"""
from __future__ import annotations

import logging
import math
import re
import sys
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

STATE_RGB = {
    "idle": (48, 208, 190), "listening": (70, 232, 128), "thinking": (182, 226, 64),
    "speaking": (255, 138, 52), "offline": (255, 70, 96), "muted": (105, 112, 124),
}
STATE_LABEL = {"idle": "ГОТОВ", "listening": "СЛУШАЮ", "thinking": "ДУМАЮ", "speaking": "ГОВОРЮ",
               "offline": "НЕТ СВЯЗИ", "muted": "МИКРОФОН ВЫКЛ"}
_STATE_FROM_UI = {"IDLE": "idle", "LISTENING": "listening", "THINKING": "thinking", "PROCESSING": "thinking",
                  "SPEAKING": "speaking", "RECONNECTING": "offline", "INITIALISING": "thinking"}

# Размеры видов (ширина, высота) в точках экрана.
SIZES = {"compact": (168, 34), "activity": (292, 34), "listening": (312, 48), "banner": (420, 66),
         "expanded": (440, 196), "match": (300, 34), "goal": (420, 84)}
EXPANDED_MATCH_EXTRA = 40      # строка матча в развёрнутой панели
GOAL_SEC = 7.0                 # сколько висит «ГОЛ!»
LISTEN_RGB = (70, 232, 128)
BANNER_SEC = 5.5
IDLE_HIDE_SEC = 8.0            # в покое капсула уходит через столько секунд
HOVER_IN_SEC = 0.22            # раскрытие по наведению — не от случайного пролёта мыши
HOVER_OUT_SEC = 0.35


@dataclass
class Media:
    title: str
    artist: str = ""
    source: str = "music"          # "music" | "video"
    playing: bool = True


@dataclass
class Score:
    """Матч любимого клуба — то, что рисует капсула."""
    home: str
    away: str
    home_score: str = ""
    away_score: str = ""
    detail: str = ""               # «67'», «HT»
    home_abbr: str = ""
    away_abbr: str = ""
    home_color: str = ""           # «00529f» с ESPN
    away_color: str = ""

    def abbr(self, side: str) -> str:
        a = self.home_abbr if side == "home" else self.away_abbr
        name = self.home if side == "home" else self.away
        return (a or re.sub(r"[^A-Za-zА-Яа-яЁё]", "", name)[:3]).upper()

    def rgb(self, side: str) -> tuple[int, int, int]:
        """Цвет формы; слишком тёмный на чёрной капсуле — осветлён; нет цвета — свой по названию."""
        h = (self.home_color if side == "home" else self.away_color).lstrip("#")
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except (ValueError, IndexError):
            name = self.home if side == "home" else self.away
            r, g, b = _PALETTE[sum(map(ord, name)) % len(_PALETTE)]
        lum = 0.3 * r + 0.59 * g + 0.11 * b
        if lum < 90:                                   # тёмно-синий «Барсы» не пропадает на чёрном
            k = (90 - lum) / 255 + 0.25
            r, g, b = (int(c + (255 - c) * k) for c in (r, g, b))
        return r, g, b

    def same_game(self, other: "Score | None") -> bool:
        return bool(other) and (self.home, self.away) == (other.home, other.away)

    def scorer(self, before: "Score | None") -> str:
        """Кто забил по сравнению с прошлым счётом: home / away / ""."""
        if not self.same_game(before):
            return ""
        for side, now, was in (("home", self.home_score, before.home_score),
                               ("away", self.away_score, before.away_score)):
            if now.isdigit() and was.isdigit() and int(now) > int(was):
                return side
        return ""


_PALETTE = [(63, 208, 189), (255, 138, 52), (120, 170, 255), (236, 90, 120), (217, 226, 90), (180, 130, 255)]
_SCORE_RE = re.compile(r"^(.+?) (\d+):(\d+) (.+?)(?: \((.*)\))?$")


def parse_score(text: str) -> Score | None:
    """«Real Madrid 1:0 Barcelona (23')» (так пишет core/football.py) → Score."""
    m = _SCORE_RE.match(" ".join((text or "").split()))
    if not m:
        return None
    return Score(m.group(1), m.group(4), m.group(2), m.group(3), m.group(5) or "")


def score_from_match(m) -> Score:
    """core.football.Match → Score."""
    return Score(m.home, m.away, m.home_score or "0", m.away_score or "0", m.detail, m.home_abbr, m.away_abbr,
                 m.home_color, m.away_color)


@dataclass
class Banner:
    kind: str                      # "reply" | "event" | "football" | "goal" | "final"
    title: str
    text: str
    until: float
    score: Score | None = None


@dataclass
class IslandModel:
    state: str = "idle"
    level: float = 0.0
    media: Media | None = None
    timer_label: str = ""          # «Сон через 23:14»
    timer_end: float = 0.0         # time.time() окончания (для обратного отсчёта)
    last_reply: str = ""
    banners: list[Banner] = field(default_factory=list)
    listen_since: float = 0.0      # когда позвали «Джарвис» (для вспышки)
    eyes: bool = False             # глаза открыты (core/eyes.py) — видно значок
    match: Score | None = None     # идёт матч любимого клуба
    goal_at: float = -1e9          # когда показали «ГОЛ!» (для анимации)
    goal_side: str = ""            # кто забил: home / away
    pop_at: float = -1e9           # счёт поменялся — цифра «подпрыгивает»
    pop_side: str = ""

    def set_state(self, ui_state: str, now: float | None = None):
        new = _STATE_FROM_UI.get((ui_state or "").upper(), ui_state if ui_state in STATE_RGB else "idle")
        if new == "listening" and self.state != "listening":
            self.listen_since = time.monotonic() if now is None else now
        self.state = new

    def notify(self, title: str, text: str, kind: str = "event", now: float | None = None, sec: float = BANNER_SEC):
        now = time.monotonic() if now is None else now
        text = " ".join((text or "").split())
        if not text and not title:
            return
        if kind == "reply":
            self.last_reply = text
            # Ответ дописывается по ходу речи — обновляем тот же баннер, а не копим.
            for b in self.banners:
                if b.kind == "reply":
                    b.text, b.until = text, now + max(sec, min(14.0, 2.0 + len(text) / 16))
                    return
            sec = max(sec, min(14.0, 2.0 + len(text) / 16))
        self.banners.append(Banner(kind, title, text, now + sec))
        del self.banners[:-4]

    def set_match(self, score: Score | None, now: float | None = None):
        now = time.monotonic() if now is None else now
        if score:
            side = score.scorer(self.match)
            if side:
                self.pop_at, self.pop_side = now, side
            # Опрос раз в пару секунд не должен «откатить» счёт, который уже пришёл голом.
            if self.match and score.same_game(self.match) and self.match.scorer(score):
                score.home_score, score.away_score = self.match.home_score, self.match.away_score
        self.match = score

    def football(self, title: str, text: str, now: float | None = None):
        """Событие матча (core/football.py): гол и итог — своим видом, остальное — баннер с мячом."""
        now = time.monotonic() if now is None else now
        text = " ".join((text or "").split())
        sc = parse_score(text)
        if title == "ГОЛ" and sc:
            if self.match and sc.same_game(self.match):          # цвета и сокращения — из живого матча
                for k in ("home_abbr", "away_abbr", "home_color", "away_color"):
                    setattr(sc, k, getattr(self.match, k))
            self.goal_side = sc.scorer(self.match)
            self.goal_at = now
            self.set_match(sc, now)
            self.banners.append(Banner("goal", title, text, now + GOAL_SEC, sc))
        elif title == "ИТОГ" and sc:
            if self.match and sc.same_game(self.match):
                for k in ("home_abbr", "away_abbr", "home_color", "away_color"):
                    setattr(sc, k, getattr(self.match, k))
            self.match = None
            self.banners.append(Banner("final", title, text, now + 8.0, sc))
        else:
            self.banners.append(Banner("football", title, text, now + BANNER_SEC))
        del self.banners[:-4]

    def banner(self, now: float | None = None) -> Banner | None:
        now = time.monotonic() if now is None else now
        self.banners = [b for b in self.banners if b.until > now]
        return self.banners[0] if self.banners else None

    def timer_text(self, now_wall: float | None = None) -> str:
        if not self.timer_label:
            return ""
        if self.timer_end:
            left = max(0, int(self.timer_end - (now_wall or time.time())))
            h, rem = divmod(left, 3600)
            m, s = divmod(rem, 60)
            clock = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            return f"{self.timer_label} · {clock}"
        return self.timer_label

    def quiet(self, now: float | None = None) -> bool:
        """Нечего показывать: ждёт имени, нет ответа, музыки и идущего таймера.
        Ближайший будильник или звонок по расписанию — не повод висеть на экране."""
        live_timer = bool(self.timer_end) or self.timer_label.startswith(("Секундомер", "Сон"))
        return (self.state == "idle" and not self.banner(now) and not (self.media and self.media.playing)
                and not live_timer and not self.match)

    def mode(self, hovered: bool, now: float | None = None) -> str:
        if hovered:
            return "expanded"
        b = self.banner(now)
        if b:
            return "goal" if b.kind in ("goal", "final") else "banner"
        # Позвали «Джарвис» — капсула раскрывается: «Слушаю…» и волна голоса.
        if self.state == "listening":
            return "listening"
        # Идёт матч клуба — живой счёт важнее музыки (как Live Activity на iPhone).
        if self.match:
            return "match"
        if self.media and self.media.playing:
            return "activity"
        return "compact"


# ── данные в фоне: что играет, таймеры ──────────────────────────────────────

def poll_media() -> Media | None:
    """Видео Джарвиса важнее музыки: если идёт фильм, управляем им."""
    try:
        from core import browser_cdp as cdp
        if cdp.running():
            st = cdp.video_state()
            if st and st.get("ready"):
                title = ""
                try:
                    t = cdp.tab(create=False)
                    title = (t.eval("document.title") or "") if t else ""
                except Exception:
                    pass
                for tail in (" - YouTube", " — VK Видео", " | VK Видео"):
                    title = title.split(tail)[0]
                return Media(title.strip() or "Видео", "", "video", not st.get("paused", True))
    except Exception as exc:
        logger.debug("Капсула, видео: %s", exc)
    try:
        from actions import spotify_premium as sp
        if sp.ready() and sp.is_premium():
            np = sp.now_playing()
            if np and np.get("title"):
                return Media(np["title"], np.get("artist", ""), "music", bool(np.get("playing")))
    except Exception as exc:
        logger.debug("Капсула, Spotify: %s", exc)
    try:
        from core import media_session
        np = media_session.now_playing()
        if np and np.title:
            return Media(np.title, np.artist, "music", np.playing)
    except Exception as exc:
        logger.debug("Капсула, медиа-сессия: %s", exc)
    return None


def poll_timer() -> tuple[str, float]:
    # Часы Джарвиса важнее всего: звенящий будильник, идущий таймер,
    # секундомер, ближайший будильник (core/clock.py).
    try:
        from core.clock import clock
        label, end = clock().nearest()
        if label:
            return label, end
    except Exception as exc:
        logger.debug("Капсула, часы: %s", exc)
    try:
        from actions.sleep_timer import sleep_timer_manager as m
        if m.is_active():
            return "Сон", time.time() + m.get_remaining_seconds()
    except Exception as exc:
        logger.debug("Капсула, таймер сна: %s", exc)
    try:
        from core import tg_call
        items = tg_call.schedule().items
        if items:
            nxt = min(items, key=lambda i: (i.get("date", ""), i["time"]))
            return f"Звонок в {nxt['time']}", 0.0
    except Exception as exc:
        logger.debug("Капсула, звонки: %s", exc)
    return "", 0.0


def poll_match() -> Score | None:
    """Идущий матч любимого клуба — если футбол вообще запущен (сеть здесь не трогаем)."""
    try:
        from core import football as F
        fb = F._fb
        m = fb.current if fb else None
        return score_from_match(m) if m else None
    except Exception as exc:
        logger.debug("Капсула, матч: %s", exc)
        return None


def media_command(media: Media | None, action: str):
    """play/pause/next/previous/volume_up/volume_down — туда, что играет."""
    try:
        if media and media.source == "video":
            from actions import video_player
            video_player.control({"toggle": "toggle", "next": "next", "previous": "seek_back"}.get(action, action))
        else:
            from actions.music_player import music_player
            music_player({"action": {"toggle": "resume" if media and not media.playing else "pause"}.get(action, action)})
    except Exception as exc:
        logger.warning("Капсула, команда %s: %s", action, exc)


def fullscreen_app_active() -> bool:
    """Впереди полноэкранная игра или фильм — капсула не мешает."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd or hwnd == u.GetShellWindow() or hwnd == u.GetDesktopWindow():
            return False
        # Развёрнутое окно — не игра. Со скрытой панелью задач оно покрывает
        # весь экран, и капсула пряталась за любым браузером на весь экран.
        if u.IsZoomed(hwnd):
            return False
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        import os
        if pid.value == os.getpid():               # окна самого Джарвиса
            return False
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        mon = u.MonitorFromWindow(hwnd, 2)

        class MI(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        mi = MI()
        mi.cbSize = ctypes.sizeof(MI)
        u.GetMonitorInfoW(mon, ctypes.byref(mi))
        m = mi.rcMonitor
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if cls.value in ("Progman", "WorkerW"):
            return False
        return (r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom)
    except Exception:
        return False


# ── окно ─────────────────────────────────────────────────────────────────────

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal  # noqa: E402
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QLinearGradient, QPainter,  # noqa: E402
                         QPainterPath, QPen)
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402


from ui_icons import draw_icon  # noqa: E402  (иконки — общие с окном)


class Island(QWidget):
    """Окно капсулы. Всё из других потоков — только через сигналы."""

    _state_sig = pyqtSignal(str)
    _level_sig = pyqtSignal(float)
    _reply_sig = pyqtSignal(str)
    _event_sig = pyqtSignal(str, str)
    _media_sig = pyqtSignal(object, str, float)
    _eyes_sig = pyqtSignal(bool)
    _match_sig = pyqtSignal(object)
    _football_sig = pyqtSignal(str, str)

    W, H = 460, 252                 # окно с запасом под самый большой вид
    TOP = 16                        # отступ от верхнего края экрана
    DROP_SPREAD = 0.97              # капля выросла — начинает растекаться

    def __init__(self, on_open=None, poll: bool = True):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setFixedSize(self.W, self.H)
        self.model = IslandModel()
        self.on_open = on_open or (lambda: None)
        self.hovered = False                       # наведение с задержкой (HOVER_IN/OUT_SEC)
        self._hover_raw, self._hover_t = False, 0.0
        self.wanted = False                        # окно Джарвиса свёрнуто
        self.dormant = False                       # ушла сама в покое — вернётся на событие
        self._quiet_since = 0.0
        self._view, self._ca = "compact", 0.0      # какой вид сейчас нарисован и его прозрачность
        self._w, self._h = SIZES["compact"]
        self._vw, self._vh = 0.0, 0.0             # скорость пружины
        # Появление жидкой каплей: сначала круг (_r: 0 → 1, диаметр — высота
        # капсулы), потом он растекается в капсулу (_s: 0 → 1) с перелётом,
        # как желе. Уход — в обратном порядке.
        self._r, self._vr = 0.0, 0.0
        self._s, self._vs = 0.0, 0.0
        self._leaving = False
        self._last = time.monotonic()
        self._clock = 0.0
        self._rgb = list(STATE_RGB["idle"])
        self._energy = 0.0
        self._buttons: dict[str, QRectF] = {}
        self._wave = [0.0] * 24                   # громкость голоса — для волны «Слушаю»
        self._wave_t = 0.0
        from orb import DotOrb
        self._orb = DotOrb(n=220, seed=3)

        self._state_sig.connect(self.model.set_state)
        self._level_sig.connect(self._feed_level)
        self._reply_sig.connect(lambda t: self.model.notify("ДЖАРВИС", t, "reply"))
        self._event_sig.connect(lambda title, text: self.model.notify(title, text, "event"))
        self._media_sig.connect(self._set_media)
        self._eyes_sig.connect(lambda on: setattr(self.model, "eyes", on))
        self._match_sig.connect(self.model.set_match)
        self._football_sig.connect(self.model.football)
        for sig in (self._state_sig, self._reply_sig, self._event_sig, self._media_sig, self._match_sig,
                    self._football_sig):
            sig.connect(self._maybe_wake)

        self._tmr = QTimer(self)
        self._tmr.setTimerType(Qt.TimerType.PreciseTimer)
        self._tmr.timeout.connect(self._step)
        self._poll_stop = threading.Event()
        if poll:
            threading.Thread(target=self._poll, daemon=True, name="island-poll").start()
            self._fs_tmr = QTimer(self)
            self._fs_tmr.timeout.connect(self._check_fullscreen)
            self._fs_tmr.start(1000)
        self._place()

    # ── вход из любого потока ───────────────────────────────────────────────
    def set_state(self, s: str):
        self._state_sig.emit(str(s))

    def feed_level(self, v: float):
        self._level_sig.emit(float(v))

    def reply(self, text: str):
        self._reply_sig.emit(str(text))

    def notify(self, title: str, text: str):
        self._event_sig.emit(str(title), str(text))

    def set_eyes(self, on: bool):
        self._eyes_sig.emit(bool(on))

    def football(self, title: str, text: str):
        """Событие матча: «ГОЛ», «ИТОГ», «МАТЧ», «МАТЧ НАЧАЛСЯ», «НОВОСТЬ КЛУБА»."""
        self._football_sig.emit(str(title), str(text))

    def set_match(self, score: Score | None):
        self._match_sig.emit(score)

    # ── показ ───────────────────────────────────────────────────────────────
    def _place(self):
        scr = QApplication.primaryScreen()
        g = scr.geometry() if scr else None
        if g:
            self.move(g.x() + (g.width() - self.W) // 2, g.y() + self.TOP)

    def set_wanted(self, on: bool):
        """Окно Джарвиса свёрнуто (on=True) или развёрнуто."""
        if on and not self.wanted:
            self.dormant, self._quiet_since = False, 0.0      # свернули — показаться хотя бы на миг
        self.wanted = on
        self._apply_visibility()

    def _maybe_wake(self, *_):
        """Событие (позвали, ответ, музыка, таймер) — вернуться из покоя."""
        if self.dormant and not self.model.quiet():
            self.dormant, self._quiet_since = False, 0.0
            self._apply_visibility()

    def _apply_visibility(self, fullscreen: bool = False):
        show = self.wanted and not fullscreen and not self.dormant
        if show:
            self._leaving = False
            if not self.isVisible():
                self._r = self._vr = self._s = self._vs = 0.0
                self._view, self._ca = self.model.mode(False), 0.0
                self._place()
                logger.info("Капсула: показ")
                self.show()
                self._last = time.monotonic()
                self._tmr.start(16)
        elif self.isVisible():
            if fullscreen:                 # игра на весь экран — сразу, без анимации
                logger.info("Капсула: спрятана — впереди полноэкранное окно")
                self._hide_now()
            else:                          # Джарвис развернулся — капсула втягивается
                self._leaving = True

    def _hide_now(self):
        self._leaving = False
        self._r = self._vr = self._s = self._vs = 0.0
        self.hide()
        self._tmr.stop()

    def _check_fullscreen(self):
        if self.wanted:
            self._apply_visibility(fullscreen_app_active())

    def _poll(self):
        while not self._poll_stop.wait(2.0):
            if not self.wanted:
                continue
            media = poll_media()
            label, end = poll_timer()
            self._media_sig.emit(media, label, end)
            match = poll_match()
            if match or self.model.match:
                self._match_sig.emit(match)

    def _set_media(self, media, label: str, end: float):
        was = self.model.media
        self.model.media = media
        self.model.timer_label, self.model.timer_end = label, end
        # Новый трек — коротко показать, что играет.
        if media and media.playing and (not was or was.title != media.title):
            self.model.notify("ИГРАЕТ" if media.source == "music" else "ВИДЕО",
                              media.title + (f" — {media.artist}" if media.artist else ""), "event", sec=3.5)

    def _feed_level(self, v: float):
        self.model.level = max(self.model.level, max(0.0, min(1.0, v)))

    # ── анимация ────────────────────────────────────────────────────────────
    def capsule_rect(self) -> QRectF:
        r, s = max(0.0, self._r), max(0.0, self._s)
        d = self._h * r                                   # диаметр капли
        w = d + (self._w - d) * s
        # Жидкость: растекаясь быстро, капсула становится тоньше, отскакивая —
        # толще; так же и при смене вида (ответ, «Слушаю»).
        squash = max(-0.16, min(0.16, -0.045 * self._vs - 0.0008 * self._vw))
        h = min(float(self.H), d * (1.0 + squash * min(1.0, r)))
        return QRectF((self.W - w) / 2, 0, max(0.0, w), max(0.0, h))

    def _radius(self, cap: QRectF) -> float:
        """Капля — круг; растёкшись, капсула берёт своё скругление."""
        full = min(cap.width(), cap.height()) / 2
        s = max(0.0, min(1.0, self._s))
        return min(full, full * (1 - s) + min(cap.height() / 2, 24) * s)

    def _step(self):
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        self._clock += dt
        m = self.model
        m.level *= math.exp(-dt * 8.0)
        # Наведение — с задержкой: пролёт мыши у края не дёргает капсулу туда-сюда.
        if self._hover_raw != self.hovered:
            self._hover_t += dt
            if self._hover_t >= (HOVER_IN_SEC if self._hover_raw else HOVER_OUT_SEC):
                self.hovered, self._hover_t = self._hover_raw, 0.0
        else:
            self._hover_t = 0.0
        # Покой: ничего не происходит — капсула уходит сама (и вернётся на событие).
        if not self._leaving and not self.hovered and m.quiet():
            self._quiet_since = self._quiet_since or now
            if now - self._quiet_since >= IDLE_HIDE_SEC:
                self.dormant, self._leaving = True, True
        else:
            self._quiet_since = 0.0
        target = m.mode(self.hovered)
        tw, th = SIZES[target]
        if target == "compact":
            tw = self._compact_width()
        elif target == "expanded" and m.match:
            th += EXPANDED_MATCH_EXTRA
        # Пружина с лёгким перелётом — капсула «пружинит», как на iPhone.
        k, c = 260.0, 24.0
        self._vw += (k * (tw - self._w) - c * self._vw) * dt
        self._vh += (k * (th - self._h) - c * self._vh) * dt
        self._w += self._vw * dt
        self._h += self._vh * dt
        if self._leaving:
            # Уход без перелёта: капсула стягивается в каплю, капля тает.
            ks = 300.0
            self._vs += (ks * (0.0 - self._s) - 2 * math.sqrt(ks) * self._vs) * dt
            self._s += self._vs * dt
            kr = 260.0
            rt = 0.0 if self._s < 0.12 else 1.0
            self._vr += (kr * (rt - self._r) - 2 * math.sqrt(kr) * self._vr) * dt
            self._r += self._vr * dt
            if self._s < 0.05 and self._r < 0.04:
                self._hide_now()
                return
        else:
            # Капля выпрыгивает с лёгким перелётом…
            kr, cr = 200.0, 16.0
            self._vr += (kr * (1.0 - self._r) - cr * self._vr) * dt
            self._r += self._vr * dt
            # …и, почти выросши, растекается в капсулу, покачиваясь, как желе.
            if self._r > self.DROP_SPREAD or self._s > 0:
                ks, cs = 150.0, 10.5
                self._vs += (ks * (1.0 - self._s) - cs * self._vs) * dt
                self._s += self._vs * dt
        self._wave_t += dt
        if self._wave_t >= 0.04:                   # волна сдвигается 25 раз в секунду
            self._wave_t = 0.0
            self._wave = self._wave[1:] + [m.level]
        tgt = STATE_RGB.get(m.state, STATE_RGB["idle"])
        for i in range(3):
            self._rgb[i] += (tgt[i] - self._rgb[i]) * (1 - math.exp(-dt * 6))
        self._orb.step(dt, m.level if m.state != "speaking" else max(m.level, 0.3 + 0.2 * math.sin(self._clock * 9)),
                       active=m.state in ("thinking", "speaking"))
        # Содержимое: другой вид — старое гаснет; тот же — проявляется, когда размер почти готов.
        if target != self._view:
            self._ca -= dt * 14.0
            if self._ca <= 0.0:
                self._ca, self._view = 0.0, target
        elif abs(self._w - tw) < 10 and abs(self._h - th) < 6:
            self._ca = min(1.0, self._ca + dt * 7.0)
        self.update()

    def _compact_label(self) -> str:
        m = self.model
        return m.timer_text() if (m.timer_label and m.state == "idle") else STATE_LABEL.get(m.state, "")

    def _compact_width(self) -> float:
        """Компактная капсула — по длине подписи («Будильник 07:00» не обрезается)."""
        from PyQt6.QtGui import QFontMetricsF
        f = QFont("Segoe UI", 1)
        f.setPointSizeF(7.5)
        f.setBold(True)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        text_w = QFontMetricsF(f).horizontalAdvance(self._compact_label())
        return max(float(SIZES["compact"][0]), min(300.0, 36 + text_w + 18 + (18 if self.model.eyes else 0)))

    # ── мышь ────────────────────────────────────────────────────────────────
    def enterEvent(self, _):
        self._hover_raw = True

    def leaveEvent(self, _):
        self._hover_raw = False

    def mouseReleaseEvent(self, ev):
        pos = ev.position()
        for name, rect in self._buttons.items():
            if rect.contains(pos):
                if name == "open":
                    self.on_open()
                else:
                    media = self.model.media
                    threading.Thread(target=media_command, args=(media, name), daemon=True).start()
                    if media and name == "toggle":
                        media.playing = not media.playing
                return
        if self.capsule_rect().contains(pos) and not self.hovered_expanded():
            self.on_open()

    def hovered_expanded(self) -> bool:
        return self.hovered and self._h > SIZES["banner"][1] + 20

    # ── рисование ───────────────────────────────────────────────────────────
    def _col(self, a: float, lift: float = 0.0) -> QColor:
        r, g, b = (int(c + (255 - c) * lift) for c in self._rgb)
        return QColor(r, g, b, max(0, min(255, int(a))))

    def _mini_orb(self, p: QPainter, cx: float, cy: float, r: float):
        xs, ys, zs = self._orb.project(cx, cy, r)
        for x, y, z in zip(xs, ys, zs):
            t = (z + 1) / 2
            p.setPen(QPen(self._col(60 + 195 * t, lift=0.4 * t ** 3), 1.0 + 1.2 * t))
            p.drawPoint(QPointF(x, y))

    def _eq(self, p: QPainter, x: float, cy: float, playing: bool):
        for i in range(4):
            h = 4 + (9 * (0.5 + 0.5 * math.sin(self._clock * (7 + i * 2.3) + i))) if playing else 3
            p.fillRect(QRectF(x + i * 5, cy - h / 2, 3, h), self._col(230))

    def _text(self, p: QPainter, rect: QRectF, text: str, size: float, color: QColor, bold=False,
              align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, spacing=0.0, wrap=False):
        f = QFont("Segoe UI", 1)
        f.setPointSizeF(size)
        f.setBold(bold)
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        p.setFont(f)
        p.setPen(QPen(color))
        if wrap:
            p.drawText(rect, int(align) | int(Qt.TextFlag.TextWordWrap), text)
        else:
            p.drawText(rect, int(align), p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width())))

    def paintEvent(self, _):
        p = QPainter(self)
        # Кадр — с чистого листа: прозрачное окно не должно помнить прошлые кадры.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cap = self.capsule_rect()
        radius = self._radius(cap)
        path = QPainterPath()
        path.addRoundedRect(cap, radius, radius)
        p.fillPath(path, QColor(0, 0, 0, 250))
        m = self.model
        if m.mode(self.hovered) == "listening":
            self._paint_listen_glow(p, path, cap)
        elif cap.width() > 2:
            # Тонкая ровная линия ВНУТРИ края (на целом пикселе): не «плывёт» по толщине.
            edge = cap.adjusted(0.5, 0.5, -0.5, -0.5)
            inner = QPainterPath()
            er = max(0.0, radius - 0.5)
            inner.addRoundedRect(edge, er, er)
            p.setPen(QPen(QColor(255, 255, 255, int(26 * min(1.0, max(0.0, self._r)))), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(inner)
        p.setClipPath(path)
        # Содержимое: проявляется, когда капля почти растеклась, и при смене вида
        # сначала гаснет старое (self._ca) — два вида друг на друга не ложатся.
        p.setOpacity(min(1.0, max(0.0, (self._s - 0.6) / 0.35)) * self._ca)
        if self._ca <= 0.01:
            return
        mode = self._view
        self._buttons = {}
        white, dim = QColor(238, 243, 246), QColor(138, 150, 161)
        x0, w, h = cap.x(), cap.width(), cap.height()
        if mode == "expanded" and h > 110:
            self._paint_expanded(p, cap, white, dim)
            return
        if mode == "listening" and h > 40:
            self._paint_listening(p, cap, white)
            return
        if mode == "goal" and h > 60:
            b = m.banner()
            if b and b.score:
                self._paint_goal(p, cap, white, dim, b)
            return
        if mode == "match" and m.match:
            self._paint_match(p, cap, white, dim)
            return
        if mode == "banner" and h > 46:
            b = m.banner()
            if b and b.kind == "football":
                self._ball(p, x0 + 30, cap.center().y(), 24, QColor(238, 243, 246), self._clock * 40)
            else:
                self._mini_orb(p, x0 + 30, cap.center().y(), 13)
            if b:
                self._text(p, QRectF(x0 + 56, 9, w - 70, 16), b.title, 7.5, self._col(235, 0.2), bold=True, spacing=1.5)
                self._text(p, QRectF(x0 + 56, 25, w - 70, h - 30), b.text, 9.5, white, wrap=True,
                           align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            return
        # компактный и «что играет»
        self._mini_orb(p, x0 + 20, h / 2, 9)
        media = m.media
        eye_w = 18 if m.eyes else 0
        if mode == "activity" and media and w > 200:
            self._text(p, QRectF(x0 + 38, 0, w - 80 - eye_w, h), media.title, 9, white)
            self._eq(p, x0 + w - 32, h / 2, media.playing)
            if m.eyes:
                self._eye(p, x0 + w - 46, h / 2)
        else:
            label = self._compact_label()
            self._text(p, QRectF(x0 + 36, 0, w - 50 - eye_w, h), label, 7.5, self._col(235, 0.25),
                       bold=True, spacing=1.6)
            if m.eyes:
                self._eye(p, x0 + w - 20, h / 2)

    # ── футбол ──────────────────────────────────────────────────────────────
    def _ball(self, p: QPainter, cx: float, cy: float, size: float, color: QColor, angle: float = 0.0):
        p.save()
        p.translate(cx, cy)
        p.rotate(angle)
        draw_icon(p, "ball", QPointF(0, 0), size, color)
        p.restore()

    def _live_dot(self, p: QPainter, cx: float, cy: float):
        """Красная точка «в эфире»: пульсирует, от неё расходится кольцо."""
        t = (self._clock * 1.1) % 1.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 70, 96, int(110 * (1 - t))))
        p.drawEllipse(QPointF(cx, cy), 3.2 + 6 * t, 3.2 + 6 * t)
        p.setBrush(QColor(255, 70, 96))
        p.drawEllipse(QPointF(cx, cy), 3.2, 3.2)

    def _pop(self, side: str, since: float) -> float:
        """Цифра счёта после гола: подпрыгивает и успокаивается (пружина)."""
        t = time.monotonic() - since
        if side == "" or t > 1.6:
            return 1.0
        return 1.0 + 0.7 * math.exp(-t * 4.5) * math.cos(t * 13)

    def _scoreline(self, p: QPainter, rect: QRectF, sc: Score, size: float, white: QColor,
                   pop_side: str = "", pop_since: float = -1e9):
        """«● RMA  1 : 0  BAR ●» по центру rect; забившая сторона — крупнее и цвета команды."""
        cx, cy, h = rect.center().x(), rect.center().y(), rect.height()
        dw = size * 1.25                                    # ширина цифры
        colon = QRectF(cx - size * 0.4, rect.y(), size * 0.8, h)
        self._text(p, colon, ":", size, QColor(170, 180, 190), bold=True, align=Qt.AlignmentFlag.AlignCenter)
        for side, num, sx in (("home", sc.home_score, cx - size * 0.4 - dw), ("away", sc.away_score, cx + size * 0.4)):
            k = self._pop(pop_side if pop_side == side else "", pop_since)
            fresh = k != 1.0
            p.save()
            p.translate(sx + dw / 2, cy)
            p.scale(k, k)
            r, g, b = sc.rgb(side)
            col = QColor(r, g, b) if fresh else white
            self._text(p, QRectF(-dw, -h / 2, dw * 2, h), num or "0", size * 1.05, col, bold=True,
                       align=Qt.AlignmentFlag.AlignCenter)
            p.restore()
        gap = size * 0.4 + dw + 8
        for side, x, align in (("home", cx - gap - 56, Qt.AlignmentFlag.AlignRight),
                               ("away", cx + gap, Qt.AlignmentFlag.AlignLeft)):
            self._text(p, QRectF(x, rect.y(), 56, h), sc.abbr(side), size * 0.78, QColor(215, 222, 228), bold=True,
                       align=align | Qt.AlignmentFlag.AlignVCenter, spacing=0.8)
            r, g, b = sc.rgb(side)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(r, g, b))
            fm_w = self._abbr_w(sc.abbr(side), size * 0.78)
            dot_x = (x + 56 - fm_w - 9) if side == "home" else (x + fm_w + 9)
            p.drawEllipse(QPointF(dot_x, cy), 3.6, 3.6)

    def _abbr_w(self, text: str, size: float) -> float:
        from PyQt6.QtGui import QFontMetricsF
        f = QFont("Segoe UI", 1)
        f.setPointSizeF(size)
        f.setBold(True)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        return QFontMetricsF(f).horizontalAdvance(text)

    def _paint_match(self, p: QPainter, cap: QRectF, white: QColor, dim: QColor):
        """Живой счёт в капсуле: ● RMA 1 : 0 BAR · 67'."""
        m = self.model
        x0, w, h = cap.x(), cap.width(), cap.height()
        self._live_dot(p, x0 + 18, h / 2)
        self._scoreline(p, QRectF(x0 + 30, 0, w - 90, h), m.match, 9.5, white, m.pop_side, m.pop_at)
        self._text(p, QRectF(x0 + w - 56, 0, 42, h), m.match.detail or "LIVE", 8, QColor(255, 130, 140),
                   bold=True, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _paint_goal(self, p: QPainter, cap: QRectF, white: QColor, dim: QColor, b: Banner):
        """«ГОЛ!»: вспышка цветом забившей команды, конфетти, мяч катится по капсуле,
        цифра счёта подпрыгивает. «ИТОГ» — то же табло, спокойно, без салюта."""
        m, sc = self.model, b.score
        x0, w, h = cap.x(), cap.width(), cap.height()
        goal = b.kind == "goal"
        t = time.monotonic() - m.goal_at if goal else 99.0
        side = m.goal_side if goal else ""
        r, g, bl = sc.rgb(side) if side else self._rgb_int()
        if goal:
            flash = max(0.0, 1.0 - t / 1.4)
            grad = QLinearGradient(QPointF(x0, 0), QPointF(x0 + w, 0))
            # Вспышка не выше 45 %: белая форма «Реала» не должна съедать белый «ГОЛ!».
            grad.setColorAt(0, QColor(r, g, bl, int(115 * flash * flash + 30)))
            grad.setColorAt(0.6, QColor(r, g, bl, int(45 * flash + 8)))
            grad.setColorAt(1, QColor(r, g, bl, 0))
            p.fillRect(cap, QBrush(grad))
            self._confetti(p, cap, t, sc)
            # «ГОЛ!» выпрыгивает и покачивается
            k = max(0.2, min(1.0, t / 0.18)) * (1.0 + 0.35 * math.exp(-t * 3.5) * math.cos(t * 11))
            p.save()
            p.translate(x0 + 84, h / 2 - 4)
            p.scale(k, k)
            self._text(p, QRectF(-69, -22.5, 140, 48), "ГОЛ!", 24, QColor(0, 0, 0, 150), bold=True,
                       align=Qt.AlignmentFlag.AlignCenter, spacing=1.0)            # тень
            self._text(p, QRectF(-70, -24, 140, 48), "ГОЛ!", 24, QColor(255, 255, 255), bold=True,
                       align=Qt.AlignmentFlag.AlignCenter, spacing=1.0)
            p.restore()
            # мяч прокатывается по низу капсулы, подскакивая
            u = min(1.0, t / 1.5)
            ease = 1 - (1 - u) ** 3
            bx = x0 + 18 + (w - 36) * ease
            by = h - 13 - abs(math.sin(t * 8)) * 12 * math.exp(-t * 2.2)
            alpha = int(255 * max(0.0, min(1.0, (2.3 - t) / 0.8)))
            if alpha > 0:
                self._ball(p, bx, by, 15, QColor(255, 255, 255, alpha), math.degrees((bx - x0) / 7.5))
        else:
            self._text(p, QRectF(x0 + 22, 14, 150, 18), "ФИНАЛ", 8, self._col(235, 0.25), bold=True, spacing=2.0)
            self._text(p, QRectF(x0 + 22, 34, 150, 30), "Матч окончен", 12, white, bold=True)
        board = QRectF(x0 + w - 236, 12, 220, 36)
        self._scoreline(p, board, sc, 14, white, side, m.goal_at)
        names = f"{sc.home} — {sc.away}" + (f"  ·  {sc.detail}" if sc.detail else "")
        self._text(p, QRectF(board.x(), board.bottom() + 2, board.width(), 16), names, 8, dim,
                   align=Qt.AlignmentFlag.AlignCenter)

    def _rgb_int(self) -> tuple[int, int, int]:
        return tuple(int(c) for c in self._rgb)

    def _confetti(self, p: QPainter, cap: QRectF, t: float, sc: Score):
        """Конфетти цветами обеих команд: вылетает из-под «ГОЛ!» и оседает за 2,5 с."""
        if t > 2.6:
            return
        cols = [sc.rgb("home"), sc.rgb("away"), (255, 255, 255)]
        fade = max(0.0, min(1.0, (2.6 - t) / 0.9))
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(34):
            ang = (i * 137.5) % 360
            speed = 70 + (i * 53) % 110
            vx = math.cos(math.radians(ang)) * speed * 1.6
            vy = math.sin(math.radians(ang)) * speed * 0.6 - 40
            x = cap.x() + 84 + vx * t
            y = cap.height() / 2 + vy * t + 90 * t * t
            if not (cap.x() - 6 < x < cap.right() + 6 and -6 < y < cap.height() + 6):
                continue
            r, g, b = cols[i % 3]
            p.save()
            p.translate(x, y)
            p.rotate(ang + t * (200 + i * 17))
            p.setBrush(QColor(r, g, b, int(230 * fade)))
            p.drawRect(QRectF(-2.2, -1.2, 4.4, 2.4))
            p.restore()

    def _eye(self, p: QPainter, cx: float, cy: float):
        """Глаза открыты — Джарвис видит экран или камеру. Мягко пульсирует."""
        from ui_icons import draw_icon
        a = 170 + 70 * math.sin(self._clock * 2.4)
        draw_icon(p, "eye", QPointF(cx, cy), 15, QColor(120, 200, 255, int(a)))

    def _paint_listen_glow(self, p: QPainter, path: QPainterPath, cap: QRectF):
        """«Позвали» — вспышка в момент имени и бегущий по краю свет, как у Siri."""
        r, g, b = LISTEN_RGB
        flash = max(0.0, 1.0 - (time.monotonic() - self.model.listen_since) / 0.9)
        if flash > 0:
            p.fillPath(path, QColor(r, g, b, int(70 * flash * flash)))
        grad = QConicalGradient(cap.center(), (-self._clock * 150) % 360)
        grad.setColorAt(0.00, QColor(r, g, b, 235))
        grad.setColorAt(0.18, QColor(63, 208, 189, 150))
        grad.setColorAt(0.45, QColor(r, g, b, 0))
        grad.setColorAt(0.55, QColor(r, g, b, 0))
        grad.setColorAt(0.82, QColor(63, 208, 189, 150))
        grad.setColorAt(1.00, QColor(r, g, b, 235))
        p.setPen(QPen(QBrush(grad), 2.0 + 1.5 * flash))
        p.drawPath(path)

    def _paint_listening(self, p: QPainter, cap: QRectF, white: QColor):
        x0, w, h = cap.x(), cap.width(), cap.height()
        cy = cap.center().y()
        self._mini_orb(p, x0 + 26, cy, 14)
        dots = "." * (int(self._clock * 2.5) % 4)
        self._text(p, QRectF(x0 + 50, 0, 120, h), "Слушаю" + dots, 10.5, white, bold=True)
        if self.model.eyes:
            self._eye(p, x0 + 157, cy)            # между «Слушаю…» и волной
        # Волна: живёт от голоса, а в тишине тихо «дышит» — видно, что микрофон открыт.
        n, bw, gap = len(self._wave), 3.0, 2.2
        right = x0 + w - 18
        r, g, b = LISTEN_RGB
        for i, v in enumerate(self._wave):
            breath = 0.07 + 0.05 * math.sin(self._clock * 3.2 + i * 0.55)
            amp = max(breath, min(1.0, v * 1.6))
            bh = 4 + (h - 20) * amp
            x = right - (n - i) * (bw + gap)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(r, g, b, int(120 + 135 * (i / n))))
            p.drawRoundedRect(QRectF(x, cy - bh / 2, bw, bh), 1.5, 1.5)

    def _paint_expanded(self, p: QPainter, cap: QRectF, white: QColor, dim: QColor):
        m = self.model
        x0, y0, w = cap.x() + 18, cap.y() + 14, cap.width() - 36
        self._mini_orb(p, x0 + 12, y0 + 12, 11)
        self._text(p, QRectF(x0 + 32, y0, w - 140, 24), STATE_LABEL.get(m.state, ""), 7.5,
                   self._col(235, 0.25), bold=True, spacing=1.6)
        # «Открыть» — круглая кнопка с «развернуть», как на iPhone.
        open_r = QRectF(x0 + w - 26, y0 - 1, 26, 26)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 34))
        p.drawEllipse(open_r)
        draw_icon(p, "expand", open_r.center(), 12, white)
        self._buttons["open"] = open_r
        y = y0 + 38
        if m.match:
            row = QRectF(x0, y, w, 32)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 14))
            p.drawRoundedRect(row, 10, 10)
            self._scoreline(p, row.adjusted(34, 0, -60, 0), m.match, 10.5, white)
            self._ball(p, row.x() + 17, row.center().y(), 16, QColor(238, 243, 246), self._clock * 30)
            self._live_dot(p, row.right() - 50, row.center().y())
            self._text(p, QRectF(row.right() - 42, row.y(), 34, row.height()), m.match.detail or "LIVE", 8.5,
                       QColor(255, 130, 140), bold=True,
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            y += EXPANDED_MATCH_EXTRA
        media = m.media
        if media:
            # «Обложка»: скруглённый квадрат цвета состояния с нотой / экраном.
            art = QRectF(x0, y, 48, 48)
            g = QLinearGradient(art.topLeft(), art.bottomRight())
            g.setColorAt(0, self._col(255, 0.15))
            g.setColorAt(1, self._col(255, -0.0).darker(260))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(art, 11, 11)
            draw_icon(p, "film" if media.source == "video" else "note", art.center(), 24, QColor(255, 255, 255, 235))
            tx = x0 + 60
            self._text(p, QRectF(tx, y + 4, w - 60 - 150, 20), media.title, 10.5, white, bold=True)
            self._text(p, QRectF(tx, y + 25, w - 60 - 150, 16),
                       media.artist or ("Видео" if media.source == "video" else "Музыка"), 8.5, dim)
            bx = x0 + w - 136
            for i, name in enumerate(("previous", "toggle", "next")):
                r = QRectF(bx + i * 46, y + 4, 40, 40)
                icon = {"previous": "backward", "next": "forward"}.get(name) or \
                    ("pause" if media.playing else "play")
                draw_icon(p, icon, r.center(), 26 if name == "toggle" else 20, white)
                self._buttons[name] = r
            y += 60
        else:
            self._text(p, QRectF(x0, y, w, 20), "Ничего не играет", 9, dim)
            y += 30
        timer = m.timer_text()
        if timer:
            draw_icon(p, "timer", QPointF(x0 + 8, y + 10), 15, self._col(235, 0.3))
            self._text(p, QRectF(x0 + 22, y, w - 22, 20), timer, 9.5, self._col(235, 0.3))
            y += 24
        if m.last_reply and y < cap.bottom() - 24:
            self._text(p, QRectF(x0, y, w, cap.bottom() - y - 10), "«" + m.last_reply + "»", 9, dim, wrap=True,
                       align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def closeEvent(self, ev):
        self._poll_stop.set()
        super().closeEvent(ev)
