"""Экран «Футбол» — любимый клуб в окне Джарвиса.

Сверху — большая карточка ближайшего матча: эмблемы-кружки цвета формы
въезжают с краёв, по центру время начала с обратным отсчётом, а когда матч
идёт — счёт с красной точкой «в эфире» и минутой; на смене счёта цифра
подпрыгивает. Кнопка «Смотреть на Кинопоиске» (core/football.watch_match).
Ниже — последние результаты с плашкой В / Н / П, ближайшие матчи, новости
клуба (клик — статья в браузере) и переключатели «голы» / «новости».

Данные — core/football.Football.overview() в фоне (там сеть), окно
обновляется раз в минуту, пока открыто.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                             QVBoxLayout, QWidget)

from ui import C
from ui_icons import draw_icon, qicon
from ui_island import Score, score_from_match
from ui_kit import STYLE, IconBadge, Toggle, _cap, _label, _line, art_pixmap

logger = logging.getLogger(__name__)
CREST = 64                                  # эмблема в карточке матча, точки

EXTRA = f"""
QFrame#row {{ background: transparent; border: none; border-radius: 10px; }}
QFrame#row:hover {{ background: {C.PANEL2}; }}
QLabel#date {{ color: {C.TEXT_DIM}; font-family: Consolas; font-size: 11px; }}
QLabel#teams {{ color: {C.WHITE}; font-weight: 600; }}
QLabel#res {{ color: {C.WHITE}; font-family: Consolas; font-size: 13px; font-weight: bold; }}
QLabel#league {{ color: {C.TEXT_DIM}; font-size: 11px; }}
QLabel#src {{ color: {C.PRI_DIM}; font-size: 11px; }}
"""
RESULT_COLORS = {"В": C.GREEN, "Н": C.TEXT_MED, "П": C.RED}


def result_letter(m, team: str) -> str:
    """В / Н / П с точки зрения клуба; счёта нет — пусто."""
    if not (m.home_score.isdigit() and m.away_score.isdigit()):
        return ""
    ours, theirs = int(m.home_score), int(m.away_score)
    if team and m.away == team:
        ours, theirs = theirs, ours
    return "В" if ours > theirs else "Н" if ours == theirs else "П"


def countdown(when: datetime, now: datetime) -> str:
    sec = int((when - now).total_seconds())
    if sec <= 0:
        return "вот-вот начнётся"
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"через {d} д {h} ч"
    if h:
        return f"через {h} ч {m:02d} мин"
    return f"через {m}:{s:02d}"


def day_text(when: datetime, now: datetime) -> str:
    d = (when.date() - now.date()).days
    return {0: "Сегодня", 1: "Завтра", -1: "Вчера"}.get(d, f"{when:%d.%m}")


def _crest_pixmap(url: str, size: int):
    """Эмблема, уже скачанная в фоне (FootballDialog.refresh), — без сети в потоке окна."""
    if not url:
        return None
    try:
        from core.football import crest
        path = crest(url, download=False)
    except Exception:
        return None
    if not path:
        return None
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(str(path))
    if pm.isNull():
        return None
    return pm.scaled(size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _crest_label(url: str) -> QLabel | None:
    pm = _crest_pixmap(url, 20)
    if pm is None:
        return None
    pm = pm.scaled(20, 20, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    lbl = QLabel()
    lbl.setPixmap(pm)
    lbl.setFixedSize(22, 22)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


class MatchHero(QWidget):
    """Большая карточка матча — рисуется сама, с анимацией."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(196)
        self.match = None                  # core.football.Match
        self.score: Score | None = None
        self.club = ""
        self._t0 = time.monotonic()        # появление: кружки въезжают с краёв
        self._pop_at, self._pop_side = -1e9, ""
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self.update)

    def set_match(self, m, club: str = ""):
        new = score_from_match(m) if m else None
        if new and self.score and new.same_game(self.score):
            side = new.scorer(self.score)
            if side:
                self._pop_at, self._pop_side = time.monotonic(), side
        elif (m and m.id) != (self.match and self.match.id):
            self._t0 = time.monotonic()
        self.match, self.score, self.club = m, new, club
        self._pix = {side: _crest_pixmap(getattr(m, f"{side}_logo", ""), CREST) for side in ("home", "away")} if m else {}
        self.update()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._tmr.start(33)

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._tmr.stop()

    def _text(self, p, rect, text, size, color, bold=False, align=Qt.AlignmentFlag.AlignCenter, spacing=0.0):
        f = QFont("Segoe UI", 1)
        f.setPointSizeF(size)
        f.setBold(bold)
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        p.setFont(f)
        p.setPen(QPen(color))
        p.drawText(rect, int(align), p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight,
                                                                int(rect.width())))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        p.fillPath(path, QColor(C.PANEL))
        m, sc = self.match, self.score
        field = art_pixmap("football/hero_bg.png")          # поле сверху, бирюзовая разметка
        if field is not None:
            p.save()
            p.setClipPath(path)
            p.setOpacity(0.42 if m else 0.25)
            k = max(r.width() / field.width(), r.height() / field.height())
            fw, fh = field.width() * k, field.height() * k
            p.drawPixmap(QRectF(r.center().x() - fw / 2, r.center().y() - fh / 2, fw, fh), field,
                         QRectF(field.rect()))
            p.restore()
        if not m or not sc:
            p.setPen(QPen(QColor(C.BORDER), 1))
            p.drawPath(path)
            empty = art_pixmap("empty/empty_football.png")
            if empty is not None:
                ew = 150
                eh = ew * empty.height() / empty.width()
                p.drawPixmap(QRectF(r.center().x() - ew / 2, r.center().y() - eh / 2 - 14, ew, eh), empty,
                             QRectF(empty.rect()))
            else:
                draw_icon(p, "ball", QPointF(r.center().x(), r.center().y() - 16), 34, QColor(C.TEXT_DIM))
            self._text(p, QRectF(r.x(), r.center().y() + 8, r.width(), 24),
                       "Ближайших матчей не нашёл" if self.club else "Укажите любимый клуб — сверху справа",
                       10.5, QColor(C.TEXT_MED))
            return
        live = m.state == "in"
        # Фон: свечение цветов обеих команд с краёв.
        hr, hg, hb = sc.rgb("home")
        ar, ag, ab = sc.rgb("away")
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0.0, QColor(hr, hg, hb, 46))
        g.setColorAt(0.42, QColor(hr, hg, hb, 0))
        g.setColorAt(0.58, QColor(ar, ag, ab, 0))
        g.setColorAt(1.0, QColor(ar, ag, ab, 46))
        p.fillPath(path, g)
        p.setPen(QPen(QColor(C.RED if live else C.BORDER_B), 1))
        p.drawPath(path)
        t = time.monotonic() - self._t0
        ease = 1 - (1 - min(1.0, t / 0.7)) ** 3                  # въезд с перелётом не нужен — плавно
        cx, cy = r.center().x(), r.y() + 84
        # лига сверху
        self._text(p, QRectF(r.x(), r.y() + 14, r.width(), 18), (m.league or "Матч").upper(), 8,
                   QColor(C.TEXT_DIM), bold=True, spacing=1.6)
        # кружки команд
        off = min(r.width() * 0.32, 230)
        for side, sign, name in (("home", -1, m.home), ("away", 1, m.away)):
            x = cx + sign * (off + (1 - ease) * 120)
            cr, cg, cb = sc.rgb(side)
            p.setOpacity(ease)
            pix = self._pix.get(side)
            if pix is not None:                                  # настоящая эмблема клуба
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(cr, cg, cb, 26))               # мягкое свечение цветом формы
                p.drawEllipse(QPointF(x, cy), 40, 40)
                p.drawPixmap(QRectF(x - 32, cy - 32, 64, 64).toRect(), pix)
            else:
                p.setPen(QPen(QColor(cr, cg, cb, 200), 2))
                p.setBrush(QColor(cr, cg, cb, 40))
                p.drawEllipse(QPointF(x, cy), 32, 32)
                self._text(p, QRectF(x - 32, cy - 32, 64, 64), sc.abbr(side), 12, QColor(C.WHITE), bold=True,
                           spacing=0.8)
            self._text(p, QRectF(x - 90, cy + 38, 180, 22), name, 10.5, QColor(C.TEXT), bold=True)
            p.setOpacity(1.0)
        now = datetime.now()
        if live or m.state == "post":
            for side, sign, num in (("home", -1, sc.home_score), ("away", 1, sc.away_score)):
                k = 1.0
                dt = time.monotonic() - self._pop_at
                if side == self._pop_side and dt < 1.6:
                    k = 1.0 + 0.6 * math.exp(-dt * 4.5) * math.cos(dt * 13)
                p.save()
                p.translate(cx + sign * 34, cy)
                p.scale(k, k)
                self._text(p, QRectF(-40, -30, 80, 60), num or "0", 34, QColor(C.WHITE), bold=True)
                p.restore()
            self._text(p, QRectF(cx - 10, cy - 30, 20, 56), ":", 28, QColor(C.TEXT_DIM), bold=True)
            if live:
                ph = (time.monotonic() * 1.1) % 1.0
                dot = QPointF(cx - 30, cy + 44)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 70, 96, int(110 * (1 - ph))))
                p.drawEllipse(dot, 4 + 7 * ph, 4 + 7 * ph)
                p.setBrush(QColor(C.RED))
                p.drawEllipse(dot, 4, 4)
                self._text(p, QRectF(cx - 22, cy + 34, 80, 20), m.detail or "LIVE", 10, QColor(C.RED), bold=True,
                           align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            else:
                self._text(p, QRectF(cx - 80, cy + 34, 160, 20), "ФИНАЛ", 8.5, QColor(C.TEXT_DIM), bold=True,
                           spacing=1.6)
        else:
            self._text(p, QRectF(cx - 90, cy - 34, 180, 44), f"{m.when:%H:%M}", 28, QColor(C.WHITE), bold=True)
            self._text(p, QRectF(cx - 90, cy + 8, 180, 18), day_text(m.when, now), 10, QColor(C.TEXT_MED))
            self._text(p, QRectF(cx - 110, cy + 30, 220, 20), countdown(m.when, now), 10, QColor(C.PRI), bold=True)


class FootballDialog(QDialog):
    _loaded = pyqtSignal(object)
    _status = pyqtSignal(str)
    _watch_done = pyqtSignal()

    def __init__(self, parent=None, fb=None, auto_refresh: bool = True):
        super().__init__(parent)
        from core import football as F
        self.F = F
        self.fb = fb or F.football()
        self.setWindowTitle("ДЖАРВИС — футбол")
        self.setStyleSheet(STYLE + EXTRA)
        self.resize(880, 760)
        self._loading = False
        self.data: dict = {}
        self._loaded.connect(self._apply)
        self._status.connect(lambda t: self.status.setText(t))
        self._watch_done.connect(lambda: self.watch_btn.setEnabled(True))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(_line())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        canvas = QWidget()
        canvas.setObjectName("canvas")
        outer = QHBoxLayout(canvas)
        outer.setContentsMargins(0, 0, 0, 0)
        col_w = QWidget()
        col_w.setMaximumWidth(860)
        self.col = QVBoxLayout(col_w)
        self.col.setContentsMargins(28, 22, 28, 28)
        self.col.setSpacing(12)
        self.hero = MatchHero()
        self.col.addWidget(self.hero)
        acts = QHBoxLayout()
        self.watch_btn = QPushButton("  Смотреть на Кинопоиске")
        self.watch_btn.setObjectName("primary")
        self.watch_btn.setIcon(qicon("play", 12, C.BG))
        self.watch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.watch_btn.clicked.connect(self._watch)
        self.refresh_btn = QPushButton("  Обновить")
        self.refresh_btn.setIcon(qicon("reload", 12, C.TEXT_MED))
        self.refresh_btn.clicked.connect(lambda: self.refresh(force=True))
        self.status = _label("", "status")
        acts.addWidget(self.watch_btn)
        acts.addWidget(self.refresh_btn)
        acts.addSpacing(8)
        acts.addWidget(self.status, 1)
        self.col.addLayout(acts)
        self.results = self._section("Последние матчи", "check")
        self.upcoming = self._section("Ближайшие матчи", "timer")
        self.news = self._section("Новости клуба", "globe")
        self.col.addWidget(self._settings())
        self.col.addStretch(1)
        outer.addStretch(1)
        outer.addWidget(col_w, 100)
        outer.addStretch(1)
        scroll.setWidget(canvas)
        root.addWidget(scroll, 1)
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self.refresh)
        self._auto = auto_refresh

    # ── вид ─────────────────────────────────────────────────────────────────
    def _header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(14)
        lay.addWidget(IconBadge("ball", 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(_label("ДЖАРВИС", "brand"))
        self.title = _label("Футбол", "h1", wrap=False)
        col.addWidget(self.title)
        lay.addLayout(col)
        lay.addStretch(1)
        self.club_edit = QLineEdit(self.fb.state.get("club", ""))
        self.club_edit.setPlaceholderText("Любимый клуб — «Реал», «Зенит»…")
        self.club_edit.setFixedWidth(240)
        self.club_edit.returnPressed.connect(self._save_club)
        save = QPushButton("Сохранить")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self._save_club)
        lay.addWidget(self.club_edit)
        lay.addWidget(save)
        return w

    def _section(self, title: str, icon: str) -> QVBoxLayout:
        self.col.addSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(qicon(icon, 13, C.PRI).pixmap(13, 13))
        head.addWidget(ic)
        head.addWidget(_cap(title))
        head.addStretch(1)
        self.col.addLayout(head)
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)
        self.col.addWidget(card)
        return lay

    def _settings(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 12, 18, 12)
        self.goals_t, self.news_t = Toggle(), Toggle()
        for text, hint, tg, key in (("Сообщать о голах", "Капсула с салютом и голос Джарвиса", self.goals_t, "goals"),
                                    ("Важные новости клуба", "Трансферы, травмы, тренер", self.news_t, "news")):
            row = QHBoxLayout()
            txt = QVBoxLayout()
            txt.setSpacing(0)
            txt.addWidget(_label(text, "teams"))
            txt.addWidget(_label(hint, "hint"))
            row.addLayout(txt, 1)
            tg.setChecked(bool(self.fb.state.get(key, True)))
            tg.toggled.connect(lambda on, k=key: self._set_flag(k, on))
            row.addWidget(tg)
            lay.addLayout(row)
        return card

    @staticmethod
    def _clear(lay: QVBoxLayout):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _row(self, date: str, teams: str, right: str = "", chip: str = "", sub: str = "", on_click=None,
             logos: tuple = ()) -> QFrame:
        row = QFrame()
        row.setObjectName("row")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(12)
        d = _label(date, "date", wrap=False)
        d.setFixedWidth(84)
        lay.addWidget(d)
        crests = [c for c in (_crest_label(u) for u in logos) if c]
        if len(crests) == 2:                                     # эмблемы обеих команд рядом
            lay.addWidget(crests[0])
            lay.addWidget(crests[1])
        mid = QVBoxLayout()
        mid.setSpacing(0)
        mid.addWidget(_label(teams, "teams"))
        if sub:
            mid.addWidget(_label(sub, "league"))
        lay.addLayout(mid, 1)
        if right:
            lay.addWidget(_label(right, "res", wrap=False))
        if chip:
            c = QLabel(chip)
            c.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c.setFixedSize(24, 24)
            col = RESULT_COLORS.get(chip, C.TEXT_MED)
            c.setStyleSheet(f"background: {col}; color: {C.BG}; border-radius: 12px; font-weight: 800;")
            lay.addWidget(c)
        if on_click:
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.mouseReleaseEvent = lambda _e: on_click()
        return row

    def _empty(self, lay: QVBoxLayout, text: str):
        w = _label(text, "hint")
        w.setContentsMargins(10, 8, 10, 8)
        lay.addWidget(w)

    # ── данные ──────────────────────────────────────────────────────────────
    def showEvent(self, ev):
        super().showEvent(ev)
        if self._auto:
            self.refresh()
            self._tmr.start(60_000)

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._tmr.stop()

    def refresh(self, force: bool = False):
        if self._loading:
            return
        self._loading = True
        self.status.setText("Обновляю…")

        def work():
            try:
                if force:
                    self.fb.fixtures(force=True)
                data = self.fb.overview()
                for m in [data.get("next"), *(data.get("results") or []), *(data.get("upcoming") or [])]:
                    for url in (m.home_logo, m.away_logo) if m else ():
                        self.F.crest(url)                        # эмблемы — здесь, в фоне
            except Exception as exc:
                logger.warning("Футбол, экран: %s", exc)
                data = {"club": self.fb.state.get("club", ""), "error": str(exc)}
            self._loaded.emit(data)
        threading.Thread(target=work, daemon=True, name="football-page").start()

    def _apply(self, data: dict):
        self._loading = False
        self.data = data or {}
        club = self.data.get("club", "")
        self.title.setText(f"Футбол · {self.data.get('name') or club}" if club else "Футбол")
        team = self.data.get("team", "")
        nxt = self.data.get("next")
        self.hero.set_match(nxt, club)
        now = self.data.get("now") or datetime.now()
        soon = bool(nxt) and (nxt.state == "in" or (nxt.when - now).total_seconds() < 3600)
        self.watch_btn.setEnabled(bool(club))
        self.watch_btn.setToolTip("" if soon else "Трансляция на Кинопоиске появляется ближе к матчу")
        self.status.setText("Нет связи с расписанием" if self.data.get("error") else
                            f"Обновлено в {datetime.now():%H:%M}" if club else "")
        for lay in (self.results, self.upcoming, self.news):
            self._clear(lay)
        if not club:
            for lay in (self.results, self.upcoming, self.news):
                self._empty(lay, "Появится, когда укажете клуб")
            return
        for m in self.data.get("results") or []:
            self.results.addWidget(self._row(f"{m.when:%d.%m}", m.title(), f"{m.home_score}:{m.away_score}",
                                             result_letter(m, team), m.league, logos=(m.home_logo, m.away_logo)))
        if not self.data.get("results"):
            self._empty(self.results, "Пока пусто")
        for m in self.data.get("upcoming") or []:
            self.upcoming.addWidget(self._row(f"{day_text(m.when, now)} {m.when:%H:%M}", m.title(), sub=m.league,
                                              logos=(m.home_logo, m.away_logo)))
        if not self.data.get("upcoming"):
            self._empty(self.upcoming, "Дальше в расписании пусто")
        for n in self.data.get("news") or []:
            when = f"{n.when:%H:%M}" if n.when else ""
            self.news.addWidget(self._row(when, n.title, sub=n.source,
                                          on_click=(lambda u=n.link: QDesktopServices.openUrl(QUrl(u))) if n.link
                                          else None))
        if not self.data.get("news"):
            self._empty(self.news, "Свежих новостей нет")

    # ── действия ────────────────────────────────────────────────────────────
    def _save_club(self):
        club = self.club_edit.text().strip()
        self.status.setText("Ищу клуб…")

        def work():
            try:
                text = self.fb.set_club(club)
                try:
                    from core import about_me
                    about_me.answer("club", club, sync_now=False)
                except Exception as exc:
                    logger.debug("Футбол → «Обо мне»: %s", exc)
            except Exception as exc:
                text = f"Не получилось: {exc}"
            self._status.emit(text)
        threading.Thread(target=work, daemon=True, name="football-club").start()
        QTimer.singleShot(1500, lambda: self.refresh(force=True))

    def _set_flag(self, key: str, on: bool):
        self.fb.state[key] = bool(on)
        self.fb.save()

    def _watch(self):
        nxt = self.data.get("next")
        if not nxt:
            self.status.setText("Нет матча, который можно включить.")
            return
        self.status.setText("Ищу трансляцию на Кинопоиске…")
        self.watch_btn.setEnabled(False)

        def work():
            try:
                text = self.F.watch_match(f"{nxt.home} против {nxt.away}")
            except Exception as exc:
                text = f"Не получилось открыть: {exc}"
            self._status.emit(text)
            self._watch_done.emit()
        threading.Thread(target=work, daemon=True, name="football-watch").start()
