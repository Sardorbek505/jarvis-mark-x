"""Лицо Джарвиса в капсуле — маленький «камешек» с глазами и эмоциями.

Светлое скруглённое тельце, два тёмных глаза-капсулы, за ним — мягкое
свечение цвета состояния (видно издалека: зелёное — слушает, оранжевое —
говорит). Эмоции:

  calm    — ждёт: моргает раз в несколько секунд, следит глазами за курсором;
  listen  — глаза шире, тельце покачивается;
  think   — смотрит вверх-вбок, рядом «…»;
  talk    — рот открывается от громкости голоса;
  happy   — ^ ^ и подпрыгивает (задача выполнена);
  alert   — круглые глаза и янтарный «!» (нужно разрешение);
  dizzy   — глаза-спирали, покачивается (лимит запросов, сбой связи);
  sleep   — — —, всплывают «z» (микрофон выключен);
  sad     — глаза ниже, брови домиком (не получилось, нет связи);
  hungry  — тащат файл: смотрит вниз, рот «О» — сейчас проглотит.

Смена эмоции с другой формой глаз (точки → дуги → спирали) идёт через
моргание: глаза закрываются в одной форме и открываются в другой — без
«перетекания» фигур, как у живого.

Живость (как у Coucou, только свой характер):
  • ручки — две маленькие капли по бокам: машет при появлении и когда
    позвали, обе вверх от радости, одна поднята при «!», у «подбородка»,
    когда думает, двигаются в такт голосу;
  • появление — тельце проявляется из темноты, вокруг мерцает пыль;
  • моргая, тельце чуть сплющивается; в покое иногда оглядывается
    или подпрыгивает;
  • тык по лицу — радуется; много тыков подряд — кружится голова.

Логика (Face.step) отдельно от рисования (Face.paint): что сейчас с глазами —
проверяется тестами без экрана.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient


@dataclass(frozen=True)
class Emo:
    family: str          # dot | arc | line | spiral
    w: float             # ширина глаза (доля высоты тельца)
    h: float             # высота глаза
    dy: float = 0.0      # сдвиг глаз вниз
    look: tuple[float, float] | None = None    # взгляд закреплён (иначе — за курсором)


EMOTIONS: dict[str, Emo] = {
    "calm": Emo("dot", 0.13, 0.26),
    "listen": Emo("dot", 0.15, 0.31, -0.02),
    "think": Emo("dot", 0.12, 0.20, -0.01, (0.65, -0.85)),
    "talk": Emo("dot", 0.13, 0.22, -0.03),
    "happy": Emo("arc", 0.17, 0.11, -0.02, (0.0, 0.0)),
    "alert": Emo("dot", 0.17, 0.18, -0.02, (0.0, 0.0)),
    "dizzy": Emo("spiral", 0.19, 0.19, -0.01, (0.0, 0.0)),
    "sleep": Emo("line", 0.16, 0.035, 0.03, (0.0, 0.0)),
    "sad": Emo("dot", 0.12, 0.17, 0.05, (0.0, 0.6)),
    "hungry": Emo("dot", 0.15, 0.27, -0.06, (0.0, 0.9)),
}
BLINK_SEC = 0.16          # обычное моргание
SWITCH_SEC = 0.24         # моргание со сменой формы глаз — чуть медленнее
INTRO_SEC = 0.55         # проявление из темноты
DUST_SEC = 1.3           # сколько мерцает пыль
WAVE_SEC = 1.6           # сколько машет ручкой
EYE_RGB = (20, 24, 31)
AMBER = (255, 176, 46)


class Face:
    def __init__(self, seed: int = 7):
        self._rng = random.Random(seed)
        self.emotion = "calm"
        self.family = "dot"
        self.since = 0.0                      # когда сменилась эмоция (для прыжка «рад»)
        self.clock = 0.0
        self.level = 0.0
        e = EMOTIONS["calm"]
        self.eye_w, self.eye_h, self.eye_dy = e.w, e.h, e.dy
        self.look = [0.0, 0.0]
        self._look_target = (0.0, 0.0)
        self.open = 1.0                       # 1 — глаза открыты, 0 — закрыты
        self._blink_t = -1.0                  # <0 — не моргает
        self._blink_len = BLINK_SEC
        self._switch_to: str | None = None
        self._next_blink = 2.0 + self._rng.uniform(0.0, 2.0)
        # Живость
        self.intro = 1.0                      # 0 → 1: проявление из темноты
        self.dust = 0.0                       # >0 — ещё мерцает пыль (секунд осталось)
        self.wave = 0.0                       # >0 — машет ручкой (секунд осталось)
        self._hop_at = -1e9                   # когда подпрыгнул от тыка
        self._idle_at = 12.0 + self._rng.uniform(0.0, 8.0)    # следующий «оглядеться/прыжок»
        self._peek: tuple[float, float, float] | None = None  # (x, y, до когда) — оглядывается
        self._dust_seed = [(self._rng.uniform(0, 2 * math.pi), self._rng.uniform(0.75, 1.55),
                            self._rng.uniform(0.6, 1.6), self._rng.uniform(0, 6.3)) for _ in range(28)]

    # ── вход ────────────────────────────────────────────────────────────────
    def set_emotion(self, name: str):
        if name not in EMOTIONS or name == self.emotion:
            return
        self.emotion, self.since = name, self.clock
        fam = EMOTIONS[name].family
        if fam != self.family or self._switch_to:
            self._switch_to = fam
            self._blink_t, self._blink_len = 0.0, SWITCH_SEC

    def greet(self):
        """Появление: проявиться из темноты, пыль вокруг, помахать ручкой."""
        self.intro, self.dust, self.wave = 0.0, DUST_SEC, WAVE_SEC

    def hello(self):
        """Позвали «Джарвис» — помахать (без проявления заново)."""
        self.wave = max(self.wave, WAVE_SEC * 0.7)
        self.dust = max(self.dust, DUST_SEC * 0.6)

    def poke(self):
        """Тык по лицу — подпрыгнуть."""
        self._hop_at = self.clock

    def look_at(self, x: float, y: float):
        """Куда смотреть: -1..1 по каждой оси (курсор слева/справа, выше/ниже)."""
        self._look_target = (max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y)))

    # ── время ───────────────────────────────────────────────────────────────
    def step(self, dt: float, level: float = 0.0):
        self.clock += dt
        # Громкость: вверх быстро, вниз медленно — рот не дребезжит.
        k = 18.0 if level > self.level else 6.0
        self.level += (level - self.level) * (1 - math.exp(-dt * k))
        # Моргание: само — только в форме «точки», и не у спящего/испуганного.
        if (self._blink_t < 0 and self.family == "dot" and self.emotion not in ("alert",)
                and self.clock >= self._next_blink):
            self._blink_t, self._blink_len = 0.0, BLINK_SEC
        if self._blink_t >= 0:
            self._blink_t += dt
            u = self._blink_t / self._blink_len
            self.open = min(1.0, abs(1.0 - 2.0 * min(1.0, u)))
            if u >= 0.5 and self._switch_to:      # закрылись — открываемся уже другими
                self.family, self._switch_to = self._switch_to, None
            if u >= 1.0:
                self._blink_t, self.open = -1.0, 1.0
                gap = self._rng.uniform(2.2, 5.0)
                if self._rng.random() < 0.15:     # иногда — двойное моргание
                    gap = 0.18
                self._next_blink = self.clock + gap
        self.intro = min(1.0, self.intro + dt / INTRO_SEC)
        self.dust = max(0.0, self.dust - dt)
        self.wave = max(0.0, self.wave - dt)
        # В покое иногда оглядывается или подпрыгивает — живой, а не картинка.
        if self.emotion == "calm" and self.clock >= self._idle_at:
            self._idle_at = self.clock + self._rng.uniform(10.0, 20.0)
            if self._rng.random() < 0.6:
                side = self._rng.choice((-1.0, 1.0))
                self._peek = (side * 0.9, self._rng.uniform(-0.3, 0.2), self.clock + 1.1)
            else:
                self._hop_at = self.clock
        if self._peek and (self.clock > self._peek[2] or self.emotion != "calm"):
            self._peek = None
        e = EMOTIONS[self.emotion]
        a = 1 - math.exp(-dt * 14)
        self.eye_w += (e.w - self.eye_w) * a
        self.eye_h += (e.h - self.eye_h) * a
        self.eye_dy += (e.dy - self.eye_dy) * a
        tx, ty = e.look if e.look is not None else (self._peek[:2] if self._peek else self._look_target)
        if self.emotion == "think":               # думает — взгляд медленно бродит
            tx += 0.25 * math.sin(self.clock * 1.3)
        b = 1 - math.exp(-dt * 9)
        self.look[0] += (tx - self.look[0]) * b
        self.look[1] += (ty - self.look[1]) * b

    # ── рисование ───────────────────────────────────────────────────────────
    def paint(self, p: QPainter, cx: float, cy: float, size: float, rgb=(48, 208, 190), glow: float = 1.0,
              hands: bool = True):
        """size — высота тельца. Рисует вокруг (cx, cy)."""
        t_emo = self.clock - self.since
        p.save()
        # Движение тельца: «рад» — подпрыгивает, «слушает» — покачивается,
        # «головокружение» — качается из стороны в сторону.
        dy, rot, squash = 0.0, 0.0, 0.0
        if self.emotion == "happy":
            hop = abs(math.sin(t_emo * 9.0)) * math.exp(-t_emo * 2.2)
            dy = -size * 0.16 * hop
            squash = 0.08 * (1 - hop) * math.exp(-t_emo * 2.2)
        elif self.emotion == "listen":
            dy = math.sin(self.clock * 3.2) * size * 0.035
        elif self.emotion == "dizzy":
            rot = 7.0 * math.sin(self.clock * 5.0)
        elif self.emotion == "hungry":
            squash = -0.05 * (0.5 + 0.5 * math.sin(self.clock * 8))
        t_hop = self.clock - self._hop_at
        if 0 <= t_hop < 0.6:                       # тык или прыжок от скуки
            hop = math.sin(math.pi * t_hop / 0.6)
            dy -= size * 0.14 * hop
            squash += 0.06 * (1 - hop) * (1 - t_hop / 0.6)
        if self._blink_t >= 0:                     # моргая, чуть сплющивается
            squash += 0.07 * (1 - self.open)
        bw, bh = size * 1.16 * (1 + squash), size * (1 - squash)
        p.translate(cx, cy + dy)
        if self.dust > 0:
            self._paint_dust(p, size)
        if rot:
            p.rotate(rot)
        r, g, b = rgb
        if glow > 0:
            rg = QRadialGradient(QPointF(0, 0), size * 1.05)
            rg.setColorAt(0.0, QColor(r, g, b, int(95 * glow)))
            rg.setColorAt(0.55, QColor(r, g, b, int(38 * glow)))
            rg.setColorAt(1.0, QColor(r, g, b, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rg)
            p.drawEllipse(QPointF(0, 0), size * 1.05, size * 1.05)
        body = QRectF(-bw / 2, -bh / 2, bw, bh)
        path = QPainterPath()
        path.addRoundedRect(body, size * 0.40, size * 0.40)
        # Проявление: из тёмно-серого — в белое (как включается экранчик).
        k = _ease(self.intro)
        top = _mix((72, 76, 84), _mix((252, 253, 255), rgb, 0.04).getRgb()[:3], k)
        bot = _mix((52, 56, 64), _mix((206, 213, 222), rgb, 0.12).getRgb()[:3], k)
        lg = QLinearGradient(body.topLeft(), body.bottomLeft())
        lg.setColorAt(0.0, top)
        lg.setColorAt(1.0, bot)
        if hands:
            self._paint_hands(p, size, bw, bh, top, bot, behind=True)
        p.fillPath(path, lg)
        # Блик сверху — объём, как у гладкого камешка.
        p.setPen(QPen(QColor(255, 255, 255, int(170 * k)), max(0.8, size * 0.03)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        hl = body.adjusted(size * 0.16, size * 0.07, -size * 0.16, 0)
        p.drawArc(hl, 30 * 16, 120 * 16)
        self._eyes(p, size)
        self._extras(p, size, body)
        if hands:
            self._paint_hands(p, size, bw, bh, top, bot, behind=False)
        p.restore()

    # ── ручки и пыль ────────────────────────────────────────────────────────
    def hand_pose(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """Где ручки: ((x, y) левой, (x, y) правой) в долях высоты тельца от
        центра; y < 0 — выше. Отдельно от рисования — проверяется тестами."""
        t, e = self.clock, self.emotion
        down = 0.20 + 0.015 * math.sin(t * 1.7)
        left, right = (-0.70, down), (0.70, down + 0.01 * math.sin(t * 1.9 + 1))
        if e == "happy":
            jig = 0.04 * math.sin(t * 16)
            left, right = (-0.70, -0.32 + jig), (0.70, -0.32 - jig)
        elif e == "alert":
            right = (0.66, -0.36 + 0.03 * math.sin(t * 9))
        elif e == "think":
            right = (0.32, 0.36)                       # у «подбородка»
        elif e == "talk":
            bob = 0.10 * min(1.0, self.level * 1.6)
            left, right = (-0.71, down - bob), (0.71, down - bob * 0.7)
        elif e == "dizzy":
            sw = 0.12 * math.sin(t * 5.0)
            left, right = (-0.72, 0.12 + sw), (0.72, 0.12 - sw)
        elif e == "sleep":
            left, right = (-0.62, 0.30), (0.62, 0.30)
        elif e == "hungry":
            left, right = (-0.66, 0.05), (0.66, 0.05)
        if self.wave > 0 and e not in ("dizzy", "sleep"):
            u = self.wave / WAVE_SEC                   # 1 → 0
            lift = min(1.0, (1 - u) * 6, u * 5)        # поднял — помахал — опустил
            wag = 0.10 * math.sin(t * 15) * lift
            right = (right[0] + (0.02 + wag) * lift, right[1] + (-0.52 - right[1]) * lift)
        return left, right

    def _paint_hands(self, p: QPainter, s: float, bw: float, bh: float, top: QColor, bot: QColor,
                     behind: bool):
        """Опущенные ручки — за тельцем (видны краешком), поднятые — перед."""
        r = s * 0.10
        for (x, y) in self.hand_pose():
            if (y > 0.05) != behind:
                continue
            c = QPointF(x * s, y * s)
            g = QLinearGradient(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
            g.setColorAt(0.0, top)
            g.setColorAt(1.0, bot)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, r * 1.05, r * 0.92)

    def _paint_dust(self, p: QPainter, s: float):
        """Мерцающая пыль вокруг — при появлении и когда позвали."""
        u = self.dust / DUST_SEC                       # 1 → 0
        fade = min(1.0, (1 - u) * 5) * min(1.0, u * 2.5)
        for ang, rad, spd, ph in self._dust_seed:
            a = ang + self.clock * 0.6 * spd
            rr = s * rad * (1.0 + 0.35 * (1 - u))      # кольцо медленно расходится
            x, y = math.cos(a) * rr * 1.35, math.sin(a) * rr * 0.72
            tw = 0.5 + 0.5 * math.sin(self.clock * 9 * spd + ph)
            al = int(220 * fade * tw)
            if al <= 4:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, al))
            d = max(0.8, s * 0.035 * (0.6 + tw))
            p.drawEllipse(QPointF(x, y), d, d)

    def _eyes(self, p: QPainter, s: float):
        ink = QColor(*EYE_RGB)
        gap = s * 0.21
        lx, ly = self.look[0] * s * 0.09, self.look[1] * s * 0.07
        ey = self.eye_dy * s + ly
        w, h = self.eye_w * s, max(self.eye_h * s * self.open, s * 0.025)
        for side in (-1, 1):
            ex = side * gap + lx
            if self.family == "dot":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ink)
                p.drawRoundedRect(QRectF(ex - w / 2, ey - h / 2, w, h), min(w, h) / 2, min(w, h) / 2)
                if h > s * 0.12:                      # блик в глазу — живой взгляд
                    p.setBrush(QColor(255, 255, 255, 200))
                    rr = w * 0.22
                    p.drawEllipse(QPointF(ex + w * 0.16, ey - h * 0.22), rr, rr)
            elif self.family == "arc":                # ^ ^
                p.setPen(QPen(ink, max(1.2, s * 0.075), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                hh = max(h, s * 0.03) * 2
                p.drawArc(QRectF(ex - w / 2, ey - hh / 2, w, hh * 1.4), 20 * 16, 140 * 16)
            elif self.family == "line":
                p.setPen(QPen(ink, max(1.2, s * 0.06), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(ex - w / 2, ey), QPointF(ex + w / 2, ey))
            elif self.family == "spiral":
                p.setPen(QPen(ink, max(0.9, s * 0.032), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(_spiral(ex, ey, w * 0.62 * max(0.2, self.open), self.clock * 7 * side))
        if self.emotion == "sad" and self.family == "dot":     # брови домиком
            p.setPen(QPen(ink, max(1.0, s * 0.05), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            by = ey - self.eye_h * s / 2 - s * 0.07
            for side in (-1, 1):
                ex = side * gap + lx
                # внутренний край выше внешнего — грусть, а не злость
                p.drawLine(QPointF(ex - side * w * 0.9, by - s * 0.04), QPointF(ex + side * w * 0.5, by + s * 0.03))

    def _extras(self, p: QPainter, s: float, body: QRectF):
        ink = QColor(*EYE_RGB)
        mouth_y = self.eye_dy * s + s * 0.23 + self.look[1] * s * 0.04
        if self.emotion == "talk" and self.level > 0.04:
            mh = s * (0.03 + 0.13 * min(1.0, self.level * 1.4))
            mw = s * 0.15
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            p.drawRoundedRect(QRectF(-mw / 2, mouth_y - mh / 2, mw, mh), mh / 2, mh / 2)
        elif self.emotion == "hungry":
            k = 0.8 + 0.2 * math.sin(self.clock * 8)
            mw, mh = s * 0.2 * k, s * 0.17 * k
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            p.drawEllipse(QPointF(0, mouth_y + s * 0.02), mw / 2, mh / 2)
        elif self.emotion == "alert":
            # Янтарный значок «!» на плече — как уведомление на иконке.
            c = QPointF(body.left() + s * 0.06, body.top() + s * 0.06)
            rr = s * 0.17 * (1.0 + 0.08 * math.sin(self.clock * 7))
            p.setPen(QPen(QColor(18, 18, 22), max(1.0, s * 0.04)))
            p.setBrush(QColor(*AMBER))
            p.drawEllipse(c, rr, rr)
            _glyph(p, c, "!", rr * 1.5, QColor(30, 20, 0))
        elif self.emotion == "think":
            # «…» — три точки по очереди, справа сверху.
            for i in range(3):
                a = 0.35 + 0.65 * max(0.0, math.sin(self.clock * 5 - i * 0.9))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, int(235 * a)))
                p.drawEllipse(QPointF(body.right() + s * (0.02 + i * 0.12), body.top() - s * (0.0 + i * 0.1)),
                              s * 0.05, s * 0.05)
        elif self.emotion == "sleep":
            for i in range(2):
                u = (self.clock * 0.45 + i * 0.5) % 1.0
                a = int(230 * math.sin(math.pi * u))
                c = QPointF(body.right() + s * (0.05 + 0.25 * u), body.top() + s * (0.1 - 0.45 * u))
                _glyph(p, c, "z", s * (0.26 + 0.12 * u), QColor(255, 255, 255, a))


def _ease(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def _mix(a, b, k: float) -> QColor:
    return QColor(*(int(x + (y - x) * k) for x, y in zip(a, b)))


def _spiral(cx: float, cy: float, r: float, phase: float) -> QPainterPath:
    path = QPainterPath()
    turns, n = 1.8, 48
    for i in range(n + 1):
        u = i / n
        ang = phase + u * turns * 2 * math.pi
        rad = r * u
        pt = QPointF(cx + math.cos(ang) * rad, cy + math.sin(ang) * rad)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    return path


def _glyph(p: QPainter, c: QPointF, ch: str, size: float, color: QColor):
    f = QFont("Segoe UI", 1)
    f.setPixelSize(max(1, int(size)))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QPen(color))
    p.drawText(QRectF(c.x() - size, c.y() - size, size * 2, size * 2), int(Qt.AlignmentFlag.AlignCenter), ch)
