"""Лицо Джарвиса в капсуле — маскот-шар, как GrokBot (образец владельца).

Объёмный матовый шар (свет сверху слева), два чёрных глаза-«таблетки».
Состояние читается по цвету снизу шара и по значку на «плече»:

  calm    — серо-белый, моргает, следит глазами за курсором;
  listen  — снизу наливается голубым, слегка покачивается;
  think   — голубой + синий значок «•••», точки бегут по очереди;
  talk    — голубой, значок «•••» прыгает в такт голосу, шар чуть «дышит»;
  happy   — готово: зелёная точка, голова наклонена, взгляд вверх;
  alert   — янтарный «!» — нужно разрешение;
  dizzy   — глаза-спирали, качается (лимит, сбой связи);
  sleep   — глаза-черточки, всплывают «z» (микрофон выключен);
  sad     — ошибка: розово-красный, глаза-черточки, красная точка;
  hungry  — тащат файл: шар становится коробкой с глазами — «сейчас приму».

Смена эмоции с другой формой глаз идёт через моргание: глаза закрываются
в одной форме и открываются в другой.

Живость: появление — выглядывает снизу, вокруг расходятся кольца пыли;
ручки-шарики машут при появлении и когда позвали; моргая, шар чуть
сплющивается; в покое иногда оглядывается; тык — подпрыгивает.

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
    "calm": Emo("dot", 0.16, 0.34),
    "listen": Emo("dot", 0.17, 0.37, -0.01),
    "think": Emo("dot", 0.15, 0.28, -0.01, (0.6, -0.8)),
    "talk": Emo("dot", 0.16, 0.31, -0.01),
    "happy": Emo("arc", 0.13, 0.22, -0.05, (0.7, -0.8)),
    "alert": Emo("dot", 0.19, 0.22, -0.01, (0.0, 0.0)),
    "dizzy": Emo("spiral", 0.2, 0.2, -0.01, (0.0, 0.0)),
    "sleep": Emo("line", 0.18, 0.06, 0.03, (0.0, 0.0)),
    "sad": Emo("line", 0.21, 0.09, 0.02, (0.0, 0.0)),
    "hungry": Emo("dot", 0.15, 0.26, 0.10, (0.0, 0.0)),
}
# Подкраска снизу шара по состоянию (как у GrokBot: голубой — работает, красный — ошибка)
TINTS: dict[str, tuple[int, int, int]] = {
    "listen": (104, 168, 232), "think": (104, 168, 232), "talk": (104, 168, 232),
    "sad": (232, 132, 146), "alert": (240, 186, 110),
}
BADGE_BLUE = (36, 140, 255)
# Какой значок у состояния: смена значка — он «выпрыгивает» заново.
_BADGES = {"think": "dots", "talk": "dots", "happy": "green", "sad": "red", "alert": "amber"}
BLINK_SEC = 0.16          # обычное моргание
SWITCH_SEC = 0.24         # моргание со сменой формы глаз — чуть медленнее
INTRO_SEC = 0.55         # проявление из темноты
DUST_SEC = 1.3           # сколько мерцает пыль
WAVE_SEC = 1.6           # сколько машет ручкой
EYE_RGB = (12, 13, 16)
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
        self.box = 0.0                        # 0 — шар, 1 — коробка (тащат файл); пружина с отскоком
        self._box_v = 0.0
        self.squish = 0.0                     # упругость: >0 — приплюснут, <0 — вытянут (пружина)
        self._squish_v = 0.0
        self._badge_at = -1e9                 # когда сменился значок на плече — он «выпрыгивает»
        self._badge_scale = 1.0
        self._prev_emotion = "calm"
        self.tint = 0.0                       # сила подкраски снизу
        self._tint_rgb = (104, 168, 232)
        self._dust_seed = [(self._rng.uniform(0, 2 * math.pi), self._rng.uniform(0.75, 1.55),
                            self._rng.uniform(0.6, 1.6), self._rng.uniform(0, 6.3)) for _ in range(28)]

    # ── вход ────────────────────────────────────────────────────────────────
    def set_emotion(self, name: str):
        if name not in EMOTIONS or name == self.emotion:
            return
        self._prev_emotion = self.emotion
        self.emotion, self.since = name, self.clock
        self._squish_v += 2.6                 # смена состояния — как желе: приплюснулся и выпрямился
        if _BADGES.get(name) != _BADGES.get(self._prev_emotion):
            self._badge_at = self.clock
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
        self._squish_v += 4.0

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
        m = 1 - math.exp(-dt * 8)
        # Пружины: шаг мелкий, чтобы при редких кадрах не «взрывались».
        n = max(1, int(dt / 0.008) + 1)
        h = dt / n
        target_box = 1.0 if self.emotion == "hungry" else 0.0
        for _ in range(n):
            self._box_v += (-140.0 * (self.box - target_box) - 11.0 * self._box_v) * h
            self.box += self._box_v * h
            self._squish_v += (-260.0 * self.squish - 9.0 * self._squish_v) * h
            self.squish += self._squish_v * h
        self.box = max(-0.15, min(1.15, self.box))
        self.squish = max(-0.25, min(0.25, self.squish))
        want = TINTS.get(self.emotion)
        if want:
            self._tint_rgb = want
        self.tint += ((1.0 if want else 0.0) - self.tint) * m
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
        """size — диаметр шара. Рисует вокруг (cx, cy)."""
        t_emo = self.clock - self.since
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dy, rot, squash = 0.0, 0.0, 0.0
        if self.emotion == "happy":                 # готово: подпрыгнул и наклонил голову
            hop = abs(math.sin(t_emo * 9.0)) * math.exp(-t_emo * 2.2)
            dy = -size * 0.14 * hop
            squash = 0.07 * (1 - hop) * math.exp(-t_emo * 2.2)
            rot = -16.0 * min(1.0, t_emo / 0.35)
        elif self.emotion == "listen":
            dy = math.sin(self.clock * 3.2) * size * 0.03
        elif self.emotion == "talk":
            squash = -0.05 * min(1.0, self.level * 1.5)
        elif self.emotion == "dizzy":
            rot = 8.0 * math.sin(self.clock * 5.0)
        t_hop = self.clock - self._hop_at
        if 0 <= t_hop < 0.6:                       # тык или прыжок от скуки
            hop = math.sin(math.pi * t_hop / 0.6)
            dy -= size * 0.14 * hop
            squash += 0.06 * (1 - hop) * (1 - t_hop / 0.6)
        if self._blink_t >= 0:                     # моргая, чуть сплющивается
            squash += 0.06 * (1 - self.open)
        squash += self.squish
        k = _ease(self.intro)
        # Появление: выглядывает снизу — тело поднимается из-под «края».
        rise = (1 - k) * size * 0.75
        p.translate(cx, cy + dy)
        if self.dust > 0:
            self._paint_dust(p, size)
        if rise > 0.5:
            p.setClipRect(QRectF(-size * 3, -size * 3, size * 6, size * 3 + size * 0.5))
        p.translate(0, rise)
        box = max(0.0, min(1.0, self.box))
        bw, bh = size * (1 + squash) * (1 + 0.12 * self.box), size * (1 - squash) * (1 - 0.08 * self.box)
        if glow > 0:                                # мягкая тень под шаром — он стоит, а не висит
            sh = QRadialGradient(QPointF(0, 0), 1.0)
            sh.setColorAt(0.0, QColor(0, 0, 0, int(90 * k)))
            sh.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.save()
            p.translate(0, size * 0.6 - dy * 0.5)
            p.scale(bw * 0.42 * (1 - dy / size * 0.8), size * 0.07)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sh)
            p.drawEllipse(QPointF(0, 0), 1.0, 1.0)
            p.restore()
        if rot:
            p.rotate(rot)
        body = QRectF(-bw / 2, -bh / 2, bw, bh)
        tint_rgb = self._tint_rgb
        # Свет вокруг — только когда есть о чём сказать: состояние (голубой, красный)
        # или особый случай капсулы (кружится голова, тащат файл). В покое — без ореола.
        special = 0.6 if self.emotion in ("dizzy", "hungry") else 0.0
        if glow > 0 and (self.tint > 0.02 or special):
            gr, gg, gb = tint_rgb if self.tint > 0.05 else rgb
            strength = glow * max(self.tint, special)
            rg = QRadialGradient(QPointF(0, 0), size * 0.95)
            rg.setColorAt(0.45, QColor(gr, gg, gb, int(120 * strength)))
            rg.setColorAt(1.0, QColor(gr, gg, gb, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rg)
            p.drawEllipse(QPointF(0, 0), size * 0.95, size * 0.95)
        hands = hands and self.hands_shown() > 0.01
        if hands:
            self._paint_hands(p, size, behind=True)
        path = QPainterPath()
        radius = (min(bw, bh) / 2) * (1 - 0.62 * box)               # шар → коробка
        path.addRoundedRect(body, radius, radius)
        self._paint_ball(p, path, body, size, k)
        if box > 0.05:                              # крышка коробки
            lid = QRectF(body.left() + bw * 0.06, body.top() + bh * 0.06, bw * 0.88, bh * 0.2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(18, 20, 24, int(235 * box)))
            p.drawRoundedRect(lid, bh * 0.08, bh * 0.08)
        p.save()
        p.setClipPath(path, Qt.ClipOperation.IntersectClip)   # глаза у края уходят за изгиб шара
        self._eyes(p, size)
        p.restore()
        self._extras(p, size, body)
        if hands:
            self._paint_hands(p, size, behind=False)
        p.restore()

    def _paint_ball(self, p: QPainter, path: QPainterPath, body: QRectF, s: float, k: float):
        """Матовый шар: свет сверху слева, тень снизу справа, подкраска состояния снизу."""
        dim = 1 - k                                 # проявление из темноты
        light = QRadialGradient(QPointF(body.center().x() - body.width() * 0.22,
                                        body.center().y() - body.height() * 0.28), s * 0.95)
        light.setColorAt(0.0, _mix((255, 255, 255), (70, 72, 78), dim))
        light.setColorAt(0.45, _mix((222, 224, 228), (60, 62, 68), dim))
        light.setColorAt(1.0, _mix((128, 132, 140), (40, 42, 48), dim))
        p.fillPath(path, light)
        if self.tint > 0.01:
            # Цвет состояния наливается снизу, как жидкость: уровень растёт с
            # tint, поверхность чуть колышется — шар живой, а не перекрашенный.
            r, g, b = self._tint_rgb
            level = body.bottom() - body.height() * (0.12 + 0.5 * self.tint)
            amp = body.height() * 0.035 * (0.4 + 0.6 * (1 - abs(2 * self.tint - 1)))
            wave = QPainterPath(QPointF(body.left() - 2, body.bottom() + 2))
            steps = 18
            for i in range(steps + 1):
                x = body.left() - 2 + (body.width() + 4) * i / steps
                y = level + amp * math.sin(i / steps * 2 * math.pi * 1.3 + self.clock * 3.2)
                wave.lineTo(QPointF(x, y))
            wave.lineTo(QPointF(body.right() + 2, body.bottom() + 2))
            wave.closeSubpath()
            soft = QLinearGradient(QPointF(0, level - body.height() * 0.25), QPointF(0, level + amp))
            soft.setColorAt(0.0, QColor(r, g, b, 0))                 # мягкий отсвет над поверхностью
            soft.setColorAt(1.0, QColor(r, g, b, int(90 * self.tint * k)))
            p.fillPath(path, soft)
            lg = QLinearGradient(QPointF(0, level - amp), body.bottomLeft())
            lg.setColorAt(0.0, QColor(r, g, b, int(95 * self.tint * k)))
            lg.setColorAt(0.5, QColor(r, g, b, int(185 * self.tint * k)))
            lg.setColorAt(1.0, QColor(r, g, b, int(235 * self.tint * k)))
            p.fillPath(path.intersected(wave), lg)
        # мягкая кромка снизу — шар стоит в объёме, а не нарисован
        rim = QRadialGradient(body.center(), max(body.width(), body.height()) * 0.55)
        rim.setColorAt(0.8, QColor(0, 0, 0, 0))
        rim.setColorAt(1.0, QColor(0, 0, 0, int(55 * k)))
        p.fillPath(path, rim)

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

    def hands_shown(self) -> float:
        """Ручки видны не всегда (как у GrokBot): когда машет, радуется или просит «да»."""
        if self.wave > 0:
            u = self.wave / WAVE_SEC
            return min(1.0, (1 - u) * 6, u * 4)
        return 1.0 if self.emotion in ("happy", "alert") else 0.0

    def _paint_hands(self, p: QPainter, s: float, behind: bool):
        """Ручки — маленькие шарики того же материала. Опущенные — за телом, поднятые — перед."""
        r = s * 0.11 * self.hands_shown()
        for (x, y) in self.hand_pose():
            if (y > 0.05) != behind:
                continue
            c = QPointF(x * s, y * s)
            g = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.4), r * 1.4)
            g.setColorAt(0.0, QColor(255, 255, 255))
            g.setColorAt(1.0, QColor(140, 144, 152))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, r * 1.1, r * 0.95)

    def _paint_dust(self, p: QPainter, s: float):
        """Пыль при появлении: наклонные кольца-орбиты крутятся вокруг шара и
        стягиваются к нему (как в интро GrokBot), потом гаснут."""
        u = self.dust / DUST_SEC                       # 1 → 0
        fade = min(1.0, (1 - u) * 4) * min(1.0, u * 2.2)
        p.setPen(Qt.PenStyle.NoPen)
        for ring in range(3):
            rr = s * (0.7 + 0.32 * ring) * (1.0 + 1.1 * u)          # стягиваются к шару
            tilt = (-0.35, 0.25, -0.1)[ring]
            for ang, rad, spd, ph in self._dust_seed:
                a = ang + ring * 0.9 + self.clock * (0.9 - 0.25 * ring) * (1 if ring % 2 else -1)
                jitter = 1 + 0.12 * (rad - 1.15)
                x0, y0 = math.cos(a) * rr * jitter, math.sin(a) * rr * jitter * 0.32
                x, y = x0 * math.cos(tilt) - y0 * math.sin(tilt), x0 * math.sin(tilt) + y0 * math.cos(tilt)
                front = math.sin(a) > 0                                # передняя половина орбиты ярче
                tw = 0.55 + 0.45 * math.sin(self.clock * 9 * spd + ph + ring)
                al = int(220 * fade * tw * (1.0 if front else 0.45) * (1 - 0.2 * ring))
                if al <= 4:
                    continue
                p.setBrush(QColor(255, 255, 255, al))
                d = max(0.7, s * 0.02 * (0.6 + tw) * (1.2 if front else 0.8))
                p.drawEllipse(QPointF(x, y), d, d)

    def eye_layout(self, s: float) -> list[tuple[float, float, float, float]]:
        """Глаза на сфере: (x, y, ширина, высота) левого и правого.

        Взгляд — это поворот головы: глаза едут по поверхности шара, у края
        сжимаются по ширине (cos долготы), дальний глаз — уже ближнего. На
        этом держится ощущение объёма, а не плоского кружка с точками."""
        yaw = self.look[0] * 0.62                       # до ~35° вбок
        pitch = -self.look[1] * 0.42                    # вверх — положительный
        R = s * 0.5
        out = []
        for side in (-1, 1):
            lon = side * 0.33 + yaw
            lat = pitch - self.eye_dy * 1.9
            cl, cb = math.cos(lon), math.cos(lat)
            x = R * math.sin(lon) * cb * 0.96
            y = -R * math.sin(lat) * 0.96
            w = self.eye_w * s * max(0.15, cl)
            h = max(self.eye_h * s * self.open * max(0.3, cb), s * 0.03)
            out.append((x, y, w, h))
        return out

    def _eyes(self, p: QPainter, s: float):
        ink = QColor(*EYE_RGB)
        for side, (ex, ey, w, h) in zip((-1, 1), self.eye_layout(s)):
            if self.family == "dot":                  # «таблетки», как у GrokBot
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ink)
                p.drawRoundedRect(QRectF(ex - w / 2, ey - h / 2, w, h), w / 2, min(w, h) / 2)
            elif self.family == "arc":                # готово: короткие таблетки наискось, смотрит вверх
                p.save()
                p.translate(ex, ey)
                p.rotate(28)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ink)
                p.drawRoundedRect(QRectF(-w / 2, -h / 2, w, h), w / 2, w / 2)
                p.restore()
            elif self.family == "line":               # черточки: спит / ошибка
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ink)
                p.drawRoundedRect(QRectF(ex - w / 2, ey - h / 2, w, h), h / 2, h / 2)
            elif self.family == "spiral":
                p.setPen(QPen(ink, max(0.9, s * 0.03), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(_spiral(ex, ey, self.eye_w * s * 0.6 * max(0.2, self.open), self.clock * 7 * side))

    def _badge(self, p: QPainter, s: float, body: QRectF, rgb, r: float) -> QPointF:
        """Значок на «плече» слева сверху — с тёмной обводкой, как бейдж приложения."""
        c = QPointF(body.left() + s * 0.12, body.top() + s * 0.1)
        self._badge_scale = _back_out(min(1.0, (self.clock - self._badge_at) / 0.35))
        r *= self._badge_scale
        if r <= 0.2:
            return c
        p.setPen(QPen(QColor(10, 11, 14), max(1.0, s * 0.035)))
        p.setBrush(QColor(*rgb))
        p.drawEllipse(c, r, r)
        return c

    def _extras(self, p: QPainter, s: float, body: QRectF):
        e = self.emotion
        if e in ("think", "talk"):
            # синий значок «•••»: думает — точки бегут, говорит — прыгают с голосом
            c = self._badge(p, s, body, BADGE_BLUE, s * 0.17)
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(3):
                if e == "think":
                    a = 0.45 + 0.55 * max(0.0, math.sin(self.clock * 6 - i * 0.9))
                    jump = 0.0
                else:
                    a = 1.0
                    jump = s * 0.03 * min(1.0, self.level * 1.6) * math.sin(self.clock * 14 + i * 1.3)
                k = max(0.0, self._badge_scale)
                p.setBrush(QColor(255, 255, 255, int(255 * a)))
                p.drawEllipse(QPointF(c.x() + (i - 1) * s * 0.075 * k, c.y() - jump), s * 0.028 * k, s * 0.028 * k)
        elif e == "happy":
            self._badge(p, s, body, (60, 222, 90), s * 0.075)
        elif e == "sad":
            self._badge(p, s, body, (236, 52, 64), s * 0.075)
        elif e == "alert":
            c = self._badge(p, s, body, AMBER, s * 0.16 * (1.0 + 0.08 * math.sin(self.clock * 7)))
            _glyph(p, c, "!", s * 0.24 * max(0.05, self._badge_scale), QColor(30, 20, 0))
        elif e == "sleep":
            for i in range(2):
                u = (self.clock * 0.45 + i * 0.5) % 1.0
                a = int(230 * math.sin(math.pi * u))
                c = QPointF(body.right() + s * (0.02 + 0.22 * u), body.top() + s * (0.12 - 0.4 * u))
                _glyph(p, c, "z", s * (0.22 + 0.1 * u), QColor(255, 255, 255, a))


def _back_out(u: float, k: float = 2.2) -> float:
    """0 → 1 с перелётом за 1 и возвратом — «выпрыгнул»."""
    u = max(0.0, min(1.0, u)) - 1
    return 1 + (k + 1) * u ** 3 + k * u ** 2


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
