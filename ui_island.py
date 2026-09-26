"""«Капсула» Джарвиса — как Dynamic Island, когда окно свёрнуто.

Чёрная капсула вверху по центру экрана. Внутри — крошечный шар из точек
цвета состояния (ждёт / слушает / думает / говорит), он дышит голосом.
Капсула сама раскрывается на события и сворачивается обратно:
  • ответ Джарвиса — субтитром;
  • что играет (Spotify, медиа-сессии Windows, видео Джарвиса) — с эквалайзером;
  • таймер сна, ближайший звонок по расписанию.
Наведи мышь — откроется панель: плеер с кнопками, таймер, последний ответ.
Клик по капсуле возвращает окно Джарвиса. В полноэкранной игре или фильме
капсула прячется.

Модель (IslandModel) отдельно от рисования: что показать — решает она,
и это проверяется тестами без экрана.
"""
from __future__ import annotations

import logging
import math
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
SIZES = {"compact": (168, 34), "activity": (292, 34), "banner": (420, 66), "expanded": (440, 196)}
BANNER_SEC = 5.5


@dataclass
class Media:
    title: str
    artist: str = ""
    source: str = "music"          # "music" | "video"
    playing: bool = True


@dataclass
class Banner:
    kind: str                      # "reply" | "event"
    title: str
    text: str
    until: float


@dataclass
class IslandModel:
    state: str = "idle"
    level: float = 0.0
    media: Media | None = None
    timer_label: str = ""          # «Сон через 23:14»
    timer_end: float = 0.0         # time.time() окончания (для обратного отсчёта)
    last_reply: str = ""
    banners: list[Banner] = field(default_factory=list)

    def set_state(self, ui_state: str):
        self.state = _STATE_FROM_UI.get((ui_state or "").upper(), ui_state if ui_state in STATE_RGB else "idle")

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

    def mode(self, hovered: bool, now: float | None = None) -> str:
        if hovered:
            return "expanded"
        if self.banner(now):
            return "banner"
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
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRegion  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402


class Island(QWidget):
    """Окно капсулы. Всё из других потоков — только через сигналы."""

    _state_sig = pyqtSignal(str)
    _level_sig = pyqtSignal(float)
    _reply_sig = pyqtSignal(str)
    _event_sig = pyqtSignal(str, str)
    _media_sig = pyqtSignal(object, str, float)

    W, H = 460, 212                 # окно с запасом под самый большой вид
    TOP = 16                        # отступ от верхнего края экрана
    SEED_W, SEED_H = 38.0, 8.0      # из такой полоски капсула вырастает

    def __init__(self, on_open=None, poll: bool = True):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setFixedSize(self.W, self.H)
        self.model = IslandModel()
        self.on_open = on_open or (lambda: None)
        self.hovered = False
        self.wanted = False                        # окно Джарвиса свёрнуто
        self._w, self._h = SIZES["compact"]
        self._vw, self._vh = 0.0, 0.0             # скорость пружины
        # Появление и уход, как у MacBook: 0 — полоска у края, 1 — капсула.
        self._p, self._vp = 0.0, 0.0
        self._leaving = False
        self._last = time.monotonic()
        self._clock = 0.0
        self._rgb = list(STATE_RGB["idle"])
        self._energy = 0.0
        self._buttons: dict[str, QRectF] = {}
        from orb import DotOrb
        self._orb = DotOrb(n=220, seed=3)

        self._state_sig.connect(self.model.set_state)
        self._level_sig.connect(self._feed_level)
        self._reply_sig.connect(lambda t: self.model.notify("ДЖАРВИС", t, "reply"))
        self._event_sig.connect(lambda title, text: self.model.notify(title, text, "event"))
        self._media_sig.connect(self._set_media)

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

    # ── показ ───────────────────────────────────────────────────────────────
    def _place(self):
        scr = QApplication.primaryScreen()
        g = scr.geometry() if scr else None
        if g:
            self.move(g.x() + (g.width() - self.W) // 2, g.y() + self.TOP)

    def set_wanted(self, on: bool):
        """Окно Джарвиса свёрнуто (on=True) или развёрнуто."""
        self.wanted = on
        self._apply_visibility()

    def _apply_visibility(self, fullscreen: bool = False):
        show = self.wanted and not fullscreen
        if show:
            self._leaving = False
            if not self.isVisible():
                self._p, self._vp = 0.0, 0.0
                self._place()
                self.show()
                self._last = time.monotonic()
                self._tmr.start(16)
        elif self.isVisible():
            if fullscreen:                 # игра на весь экран — сразу, без анимации
                self._hide_now()
            else:                          # Джарвис развернулся — капсула втягивается
                self._leaving = True

    def _hide_now(self):
        self._leaving = False
        self._p, self._vp = 0.0, 0.0
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
        p = max(0.0, self._p)
        w = self.SEED_W + (self._w - self.SEED_W) * p
        h = max(self.SEED_H * 0.5, self.SEED_H + (self._h - self.SEED_H) * p)
        return QRectF((self.W - w) / 2, 0, w, h)

    def _step(self):
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        self._clock += dt
        m = self.model
        m.level *= math.exp(-dt * 8.0)
        tw, th = SIZES[m.mode(self.hovered)]
        # Пружина с лёгким перелётом — капсула «пружинит», как на iPhone.
        k, c = 260.0, 24.0
        self._vw += (k * (tw - self._w) - c * self._vw) * dt
        self._vh += (k * (th - self._h) - c * self._vh) * dt
        self._w += self._vw * dt
        self._h += self._vh * dt
        # Выход — быстрее и без перелёта (критическое затухание), вход —
        # с лёгким перелётом: капсула «выпрыгивает» и чуть пружинит.
        if self._leaving:
            k, c, target = 320.0, 2 * math.sqrt(320.0), 0.0
        else:
            k, c, target = 170.0, 19.0, 1.0
        self._vp += (k * (target - self._p) - c * self._vp) * dt
        self._p += self._vp * dt
        if self._leaving and self._p < 0.03:
            self._hide_now()
            return
        tgt = STATE_RGB.get(m.state, STATE_RGB["idle"])
        for i in range(3):
            self._rgb[i] += (tgt[i] - self._rgb[i]) * (1 - math.exp(-dt * 6))
        self._orb.step(dt, m.level if m.state != "speaking" else max(m.level, 0.3 + 0.2 * math.sin(self._clock * 9)),
                       active=m.state in ("thinking", "speaking"))
        r = self.capsule_rect().adjusted(-1, -1, 1, 1).toRect()
        self.setMask(QRegion(r, QRegion.RegionType.Rectangle))   # клики мимо капсулы — в окна под ней
        self.update()

    # ── мышь ────────────────────────────────────────────────────────────────
    def enterEvent(self, _):
        self.hovered = True

    def leaveEvent(self, _):
        self.hovered = False

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
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cap = self.capsule_rect()
        radius = min(cap.height() / 2, 24)
        path = QPainterPath()
        path.addRoundedRect(cap, radius, radius)
        p.fillPath(path, QColor(0, 0, 0, 250))
        p.setPen(QPen(self._col(70 * min(1.0, max(0.0, self._p))), 1))
        p.drawPath(path)
        p.setClipPath(path)
        # Содержимое проявляется, когда капсула почти выросла, и гаснет первым.
        p.setOpacity(min(1.0, max(0.0, (self._p - 0.55) / 0.4)))
        self._buttons = {}
        m = self.model
        mode = m.mode(self.hovered)
        white, dim = QColor(238, 243, 246), QColor(138, 150, 161)
        x0, w, h = cap.x(), cap.width(), cap.height()
        if mode == "expanded" and h > 110:
            self._paint_expanded(p, cap, white, dim)
            return
        if mode == "banner" and h > 46:
            b = m.banner()
            self._mini_orb(p, x0 + 30, cap.center().y(), 13)
            if b:
                self._text(p, QRectF(x0 + 56, 9, w - 70, 16), b.title, 7.5, self._col(235, 0.2), bold=True, spacing=1.5)
                self._text(p, QRectF(x0 + 56, 25, w - 70, h - 30), b.text, 9.5, white, wrap=True,
                           align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            return
        # компактный и «что играет»
        self._mini_orb(p, x0 + 20, h / 2, 9)
        media = m.media
        if mode == "activity" and media and w > 200:
            self._text(p, QRectF(x0 + 38, 0, w - 80, h), media.title, 9, white)
            self._eq(p, x0 + w - 32, h / 2, media.playing)
        else:
            label = m.timer_text() if (m.timer_label and m.state == "idle") else STATE_LABEL.get(m.state, "")
            self._text(p, QRectF(x0 + 36, 0, w - 50, h), label, 7.5, self._col(235, 0.25), bold=True, spacing=1.6)

    def _paint_expanded(self, p: QPainter, cap: QRectF, white: QColor, dim: QColor):
        m = self.model
        x0, y0, w = cap.x() + 18, cap.y() + 14, cap.width() - 36
        self._mini_orb(p, x0 + 12, y0 + 12, 11)
        self._text(p, QRectF(x0 + 32, y0, w - 140, 24), STATE_LABEL.get(m.state, ""), 7.5,
                   self._col(235, 0.25), bold=True, spacing=1.6)
        open_r = QRectF(x0 + w - 104, y0 + 1, 104, 22)
        p.setPen(QPen(self._col(110), 1))
        p.drawRoundedRect(open_r, 11, 11)
        self._text(p, open_r, "ОТКРЫТЬ", 7, self._col(235, 0.2), bold=True, spacing=1.4,
                   align=Qt.AlignmentFlag.AlignCenter)
        self._buttons["open"] = open_r
        y = y0 + 36
        media = m.media
        if media:
            self._text(p, QRectF(x0, y, w, 18), ("ВИДЕО" if media.source == "video" else "ИГРАЕТ"), 7, dim,
                       bold=True, spacing=1.4)
            self._text(p, QRectF(x0, y + 16, w - 150, 20), media.title, 10.5, white, bold=True)
            if media.artist:
                self._text(p, QRectF(x0, y + 35, w - 150, 16), media.artist, 8.5, dim)
            bx = x0 + w - 140
            for i, (name, glyph) in enumerate((("previous", "⏮"), ("toggle", "⏸" if media.playing else "▶"),
                                               ("next", "⏭"))):
                r = QRectF(bx + i * 46, y + 12, 40, 40)
                if name == "toggle":
                    p.setBrush(self._col(40))
                    p.setPen(QPen(self._col(120), 1))
                    p.drawEllipse(r)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                self._text(p, r, glyph, 13, white, align=Qt.AlignmentFlag.AlignCenter)
                self._buttons[name] = r
            y += 62
        else:
            self._text(p, QRectF(x0, y, w, 20), "Ничего не играет", 9, dim)
            y += 30
        timer = m.timer_text()
        if timer:
            self._text(p, QRectF(x0, y, w, 20), "⏱  " + timer, 9.5, self._col(235, 0.3))
            y += 24
        if m.last_reply and y < cap.bottom() - 24:
            self._text(p, QRectF(x0, y, w, cap.bottom() - y - 10), "«" + m.last_reply + "»", 9, dim, wrap=True,
                       align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def closeEvent(self, ev):
        self._poll_stop.set()
        super().closeEvent(ev)
