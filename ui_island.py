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
         "expanded": (440, 196), "match": (300, 34), "goal": (420, 84),
         "task": (380, 56), "confirm": (440, 106), "drop": (420, 92), "upload": (380, 56),
         "chat": (460, 196), "file": (420, 92)}
EXPANDED_MATCH_EXTRA = 40      # строка матча в развёрнутой панели
EXPANDED_STEP_H = 21           # строка шага задачи в развёрнутой панели
TASK_STEPS_SHOWN = 4
TASK_JOIN_SEC = 3.0            # инструмент сразу за предыдущим — та же задача
TASK_HOLD_SEC = 3.2            # «Готово» висит после последнего шага
FLASH_SEC = 2.4                # «рад» / «грустит» после задачи
CONFIRM_SEC = 90.0             # столько ждёт «да» (main._CONFIRM_WINDOW_SEC)
AMBER_RGB = (255, 176, 46)
DROP_RGB = (70, 232, 128)
TROUBLE_RGB = (255, 92, 150)
GOAL_SEC = 7.0                 # сколько висит «ГОЛ!»
LISTEN_RGB = (70, 232, 128)
BANNER_SEC = 5.5
IDLE_HIDE_SEC = 8.0            # в покое капсула уходит через столько секунд
HOVER_IN_SEC = 0.22            # раскрытие по наведению — не от случайного пролёта мыши
HOVER_OUT_SEC = 0.35
POKE_WINDOW_SEC = 1.5          # тыки по лицу в этом окне считаются «подряд»
POKES_DIZZY = 4                # столько тыков подряд — кружится голова
EVENT_RGB = (96, 156, 255)     # событие, звонок — синее свечение
DONE_RGB = (70, 232, 128)
CHAT_IDLE_SEC = 45.0           # чат в капсуле сам закрывается после тишины
FILE_CHOICE_SEC = 40.0         # «что сделать с файлом?» висит столько


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
    home_crest: str = ""           # эмблема на диске (core/football.crest) — вместо цветной точки
    away_crest: str = ""

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


def score_from_match(m, crests: bool = False) -> Score:
    """core.football.Match → Score. crests=True — ещё и скачать эмблемы (сеть: только из фонового потока)."""
    sc = Score(m.home, m.away, m.home_score or "0", m.away_score or "0", m.detail, m.home_abbr, m.away_abbr,
               m.home_color, m.away_color)
    if crests:
        from core.football import crest
        for side in ("home", "away"):
            path = crest(getattr(m, f"{side}_logo", ""))
            setattr(sc, f"{side}_crest", str(path) if path else "")
    return sc


_CREST_KEYS = ("home_abbr", "away_abbr", "home_color", "away_color", "home_crest", "away_crest")


@dataclass
class Banner:
    kind: str                      # "reply" | "event" | "football" | "goal" | "final"
    title: str
    text: str
    until: float
    score: Score | None = None


@dataclass
class Step:
    tool: str
    label: str
    status: str = "run"            # run | ok | fail | wait (ждёт «да» — не провал)


@dataclass
class Task:
    """Что Джарвис делает по просьбе: шаги-инструменты, как «3/4 › npm test» в Coucou."""
    steps: list[Step] = field(default_factory=list)
    last_end: float = 0.0          # когда закончился последний шаг

    @property
    def running(self) -> bool:
        return any(s.status == "run" for s in self.steps)

    @property
    def done(self) -> int:
        return sum(1 for s in self.steps if s.status != "run")

    @property
    def failed(self) -> bool:
        return any(s.status == "fail" for s in self.steps)

    def current(self) -> Step | None:
        run = [s for s in self.steps if s.status == "run"]
        return run[-1] if run else (self.steps[-1] if self.steps else None)


@dataclass
class Confirm:
    question: str
    since: float


@dataclass
class Upload:
    name: str
    frac: float                    # 0..1
    stage: str                     # «Читаю», «Отправляю», «Готово»
    until: float = 0.0             # 0 — ещё идёт


# Шаг задачи — по-человечески: «Открываю Telegram», а не open_app.
_TOOL_VERB = {
    "open_app": "Открываю", "web_search": "Ищу", "weather": "Смотрю погоду", "browser": "Браузер",
    "music_player": "Музыка", "youtube_player": "YouTube", "movie_player": "Фильм", "video_control": "Видео",
    "files": "Файлы", "computer_control": "Компьютер", "window_control": "Окна", "app_window": "Окно",
    "look_at_screen": "Смотрю на экран", "look_at_camera": "Смотрю в камеру", "vision_review": "Разглядываю",
    "remember_screen": "Запоминаю экран", "contacts": "Контакты", "phone_call": "Звоню",
    "send_to_telegram": "Пишу в Telegram", "obsidian": "Заметки", "calendar": "Календарь",
    "translation": "Перевожу", "save_to_memory": "Запоминаю", "recall_memory": "Вспоминаю",
    "forget_memory": "Забываю", "sleep_timer": "Таймер", "clock": "Время", "macro": "Команда",
    "football": "Футбол", "study": "Учёба", "morning_briefing": "Брифинг", "location": "Где я",
    "eyes": "Глаза", "about_me": "О владельце", "break_reminder": "Перерыв",
}
_TOOL_DETAIL_KEYS = ("app_name", "query", "city", "url", "title", "name", "phrase", "path", "text", "action")


def tool_label(name: str, args: dict | None = None) -> str:
    """«Открываю Telegram», «Ищу: курс доллара», «Файлы · delete»."""
    args = dict(args or {})
    verb = _TOOL_VERB.get(name, name.replace("_", " ").capitalize())
    detail = ""
    for k in _TOOL_DETAIL_KEYS:
        v = args.get(k)
        if v not in (None, "", [], {}):
            detail = " ".join(str(v).split())
            break
    if not detail:
        return verb
    if len(detail) > 48:
        detail = detail[:47].rstrip() + "…"
    if name == "contacts":
        who = " ".join(str(args.get("name", "")).split())
        act = str(args.get("action", "")).lower()
        if who and act in ("message", "call"):
            return ("Пишу " if act == "message" else "Звоню ") + who
    if name in ("open_app",):
        return f"{verb} {detail}"
    if name in ("web_search", "translation", "send_to_telegram"):
        return f"{verb}: {detail}"
    return f"{verb} · {detail}"


@dataclass
class Chat:
    """Чат прямо в капсуле: последний вопрос и ответ Джарвиса."""
    file: str = ""                 # чип файла: первый вопрос уйдёт вместе с ним
    file_sent: bool = False
    question: str = ""
    answer: str = ""
    waiting: bool = False
    since: float = 0.0             # последняя активность (для автозакрытия)


@dataclass
class FileChoice:
    name: str
    since: float


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
    task: Task | None = None       # шаги того, что Джарвис сейчас делает
    confirm: Confirm | None = None  # опасное действие ждёт «да»
    drop_hover: bool = False       # над капсулой тащат файл
    upload: Upload | None = None   # брошенный файл читается / уходит Джарвису
    flash: tuple[str, float] = ("", 0.0)   # эмоция на миг: (имя, до какого времени)
    pokes: list[float] = field(default_factory=list)   # когда тыкали в лицо
    chat: Chat | None = None       # открыт чат в капсуле
    file_choice: FileChoice | None = None   # файл прочитан — что с ним сделать?

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
            if self.chat and (self.chat.waiting or self.chat.question):
                # Открыт чат — ответ печатается в нём, а не отдельным баннером.
                self.chat.answer, self.chat.waiting, self.chat.since = text, False, now
                return
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
            if self.match and sc.same_game(self.match):          # цвета, эмблемы — из живого матча
                for k in _CREST_KEYS:
                    setattr(sc, k, getattr(self.match, k))
            self.goal_side = sc.scorer(self.match)
            self.goal_at = now
            self.set_match(sc, now)
            self.banners.append(Banner("goal", title, text, now + GOAL_SEC, sc))
        elif title == "ИТОГ" and sc:
            if self.match and sc.same_game(self.match):
                for k in _CREST_KEYS:
                    setattr(sc, k, getattr(self.match, k))
            self.match = None
            self.banners.append(Banner("final", title, text, now + 8.0, sc))
        else:
            self.banners.append(Banner("football", title, text, now + BANNER_SEC))
        del self.banners[:-4]

    # ── задача: шаги инструментов ───────────────────────────────────────────
    def tool_start(self, name: str, args: dict | None = None, now: float | None = None):
        now = time.monotonic() if now is None else now
        t = self.task
        # Следующий инструмент сразу за прошлым — та же просьба («открой и включи»).
        if t is None or (not t.running and now - t.last_end > TASK_JOIN_SEC):
            t = self.task = Task()
        t.steps.append(Step(name, tool_label(name, args)))
        del t.steps[:-12]
        if self.flash[0] in ("happy", "sad"):
            self.flash = ("", 0.0)            # шаг закончился, но задача продолжается

    def tool_end(self, name: str, ok: bool | None = True, now: float | None = None):
        now = time.monotonic() if now is None else now
        t = self.task
        if t is None:
            return
        for s in reversed(t.steps):
            if s.tool == name and s.status == "run":
                s.status = "wait" if ok is None else ("ok" if ok else "fail")
                break
        else:
            return
        t.last_end = now
        if not t.running and not any(s.status == "wait" for s in t.steps):
            self.flash = ("sad" if t.failed else "happy", now + FLASH_SEC)

    def task_view(self, now: float | None = None) -> Task | None:
        """Задача на экране: идёт — или только что закончилась («Готово» ещё висит)."""
        now = time.monotonic() if now is None else now
        t = self.task
        if t is not None and not t.running and now - t.last_end > TASK_HOLD_SEC:
            self.task = t = None
        return t

    # ── подтверждение опасного ──────────────────────────────────────────────
    def ask_confirm(self, question: str, now: float | None = None):
        now = time.monotonic() if now is None else now
        self.confirm = Confirm(" ".join((question or "").split()) or "Выполнить опасное действие?", now)

    def confirm_view(self, now: float | None = None) -> Confirm | None:
        now = time.monotonic() if now is None else now
        if self.confirm and now - self.confirm.since > CONFIRM_SEC:
            self.confirm = None               # main тоже забыл вопрос через 90 с
        return self.confirm

    # ── файл, брошенный на капсулу ──────────────────────────────────────────
    def set_upload(self, name: str, frac: float, stage: str, now: float | None = None):
        now = time.monotonic() if now is None else now
        if frac < 0:                          # не вышло — головокружение и причина
            self.upload = None
            self.trouble("ФАЙЛ", f"{name}: {stage}", now)
            return
        frac = max(0.0, min(1.0, frac))
        self.upload = Upload(name, frac, stage, now + 0.9 if frac >= 1.0 else 0.0)

    def upload_view(self, now: float | None = None) -> Upload | None:
        now = time.monotonic() if now is None else now
        if self.upload and self.upload.until and now > self.upload.until:
            self.upload = None
        return self.upload

    def trouble(self, title: str, text: str, now: float | None = None, sec: float = BANNER_SEC):
        """Сбой, лимит запросов: розовый баннер, глаза-спирали."""
        now = time.monotonic() if now is None else now
        self.banners.insert(0, Banner("error", title, " ".join((text or "").split()), now + sec))
        del self.banners[4:]
        self.flash = ("dizzy", now + sec)

    # ── чат в капсуле ───────────────────────────────────────────────────────
    def open_chat(self, file: str = "", now: float | None = None):
        now = time.monotonic() if now is None else now
        self.chat = Chat(file=file, since=now)
        self.file_choice = None

    def chat_send(self, text: str, now: float | None = None) -> bool:
        """Вопрос ушёл. True — вместе с файлом (первый вопрос по нему)."""
        now = time.monotonic() if now is None else now
        c = self.chat or Chat()
        self.chat = c
        with_file = bool(c.file and not c.file_sent)
        c.file_sent = c.file_sent or with_file
        c.question, c.answer, c.waiting, c.since = " ".join(text.split()), "", True, now
        return with_file

    def close_chat(self):
        self.chat = None

    def chat_view(self, now: float | None = None) -> Chat | None:
        now = time.monotonic() if now is None else now
        c = self.chat
        if c and not c.waiting and now - c.since > CHAT_IDLE_SEC:
            self.chat = c = None
        return c

    def file_ready(self, name: str, now: float | None = None):
        """Файл прочитан — спросить, что с ним сделать."""
        now = time.monotonic() if now is None else now
        self.upload = None
        self.file_choice = FileChoice(name, now)

    def file_choice_view(self, now: float | None = None) -> FileChoice | None:
        now = time.monotonic() if now is None else now
        if self.file_choice and now - self.file_choice.since > FILE_CHOICE_SEC:
            self.file_choice = None
        return self.file_choice

    def poke(self, now: float | None = None) -> str:
        """Тык по лицу. Один — радуется; много подряд — кружится голова
        (пасхалка, как в Coucou, только по-джарвисовски)."""
        now = time.monotonic() if now is None else now
        self.pokes = [t for t in self.pokes if now - t <= POKE_WINDOW_SEC] + [now]
        if len(self.pokes) >= POKES_DIZZY:
            self.pokes = []
            self.trouble("ОЙ-ОЙ", "Голова кружится… Дайте секунду, сэр.", now, sec=3.0)
            return "dizzy"
        if not (self.flash[0] == "dizzy" and self.flash[1] > now):
            self.flash = ("happy", now + 0.9)
        return "happy"

    def glow(self, view: str, now: float | None = None) -> tuple[tuple[int, int, int], float]:
        """Свечение за капсулой: (цвет, сила 0..1) по тому, что сейчас показано."""
        now = time.monotonic() if now is None else now
        flash = self.flash[0] if self.flash[1] > now else ""
        b = self.banner(now)
        if view == "confirm":
            return AMBER_RGB, 1.0
        if flash == "dizzy" or (b and b.kind == "error" and view == "banner"):
            return TROUBLE_RGB, 1.0
        if view in ("drop", "file") or self.drop_hover:
            return DROP_RGB, 0.9 if view == "drop" else 0.6
        if flash == "happy":
            return DONE_RGB, 0.85
        if view == "listening":
            return LISTEN_RGB, 0.8
        if view in ("banner", "goal") and b and b.kind not in ("reply",):
            return EVENT_RGB, 0.7
        if self.state == "speaking":
            return STATE_RGB["speaking"], 0.35 + 0.55 * min(1.0, self.level * 1.5)
        if view in ("task", "upload", "chat", "expanded"):
            return STATE_RGB.get(self.state, STATE_RGB["idle"]), 0.45
        if view == "activity":
            return STATE_RGB.get(self.state, STATE_RGB["idle"]), 0.25
        return STATE_RGB.get(self.state, STATE_RGB["idle"]), 0.0

    def emotion(self, now: float | None = None) -> str:
        """Какое лицо у Джарвиса сейчас (ui_face.EMOTIONS)."""
        now = time.monotonic() if now is None else now
        if self.drop_hover:
            return "hungry"
        if self.confirm_view(now):
            return "alert"
        if self.flash[1] > now:
            return self.flash[0]
        if self.upload_view(now):
            return "think"
        c = self.chat_view(now)
        if c and c.waiting and self.state not in ("speaking", "listening"):
            return "think"
        return {"listening": "listen", "thinking": "think", "speaking": "talk",
                "offline": "sad", "muted": "sleep"}.get(self.state, "calm")

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
                and not live_timer and not self.match and not self.task_view(now)
                and not self.confirm_view(now) and not self.upload_view(now) and not self.drop_hover
                and not self.chat_view(now) and not self.file_choice_view(now))

    def mode(self, hovered: bool, now: float | None = None) -> str:
        # Вопрос «точно выключить?» и файл над капсулой важнее наведения:
        # иначе мышь, пришедшая нажать «Разрешить», сама бы закрыла кнопки.
        if self.confirm_view(now):
            return "confirm"
        if self.drop_hover:
            return "drop"
        if self.chat_view(now):
            return "chat"
        if self.file_choice_view(now):
            return "file"
        if hovered:
            return "expanded"
        if self.upload_view(now):
            return "upload"
        b = self.banner(now)
        t = self.task_view(now)
        # Пока шаги идут, их прогресс важнее реплики («Сейчас открою…») —
        # реплика и так видна в панели. События (звонок, гол) — важнее.
        if t and t.running and (not b or b.kind == "reply"):
            return "task"
        if b:
            return "goal" if b.kind in ("goal", "final") else "banner"
        # «Готово» уступает «Слушаю»: позвали — сразу видно, что слышит.
        if t and self.state != "listening":
            return "task"
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
        return score_from_match(m, crests=True) if m else None
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

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal  # noqa: E402
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QCursor, QFont, QLinearGradient, QPainter,  # noqa: E402
                         QPainterPath, QPen, QRadialGradient)
from PyQt6.QtWidgets import QApplication, QLineEdit, QWidget  # noqa: E402


from ui_icons import draw_icon  # noqa: E402  (иконки — общие с окном)


class _ChatEdit(QLineEdit):
    """Поле чата в капсуле: Esc — закрыть чат."""

    def __init__(self, parent, on_escape):
        super().__init__(parent)
        self._on_escape = on_escape
        self.setPlaceholderText("Спросите что-нибудь…")
        self.setFrame(False)
        self.setStyleSheet("QLineEdit { background: transparent; color: #eef3f6; border: none;"
                           " font-family: 'Segoe UI'; font-size: 10pt; selection-background-color: #2f6f68; }")
        self.hide()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self._on_escape()
            return
        super().keyPressEvent(ev)


def _drop_glow(glow: QWidget):
    try:
        glow.hide()
        glow.deleteLater()
    except RuntimeError:                   # уже удалено Qt — нечего делать
        pass


class IslandGlow(QWidget):
    """Цветной ореол позади капсулы — по настроению: слушает — зелёный, нужно
    «да» — янтарный, сбой — розовый, событие — синий, говорит — пульсирует.

    Отдельное окно, прозрачное для мыши (WindowTransparentForInput): на
    Windows полупрозрачный пиксель ловит клики, и ореол в самом окне капсулы
    перехватывал бы вкладки браузера под ним."""

    MARGIN_X, MARGIN_Y = 150, 110

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.cap = QRectF()
        self.rgb = (48, 208, 190)
        self.strength = 0.0

    def follow(self, island: QWidget, cap: QRectF, rgb, strength: float):
        """cap — капсула в координатах окна капсулы."""
        w, h = island.width() + 2 * self.MARGIN_X, int(cap.height()) + 2 * self.MARGIN_Y
        x, y = island.x() - self.MARGIN_X, island.y() - self.MARGIN_Y
        if self.geometry().getRect() != (x, y, w, h):
            self.setGeometry(x, y, w, h)
        self.cap = cap.translated(self.MARGIN_X, self.MARGIN_Y)
        self.rgb, self.strength = tuple(int(c) for c in rgb), strength
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if self.strength <= 0.01 or self.cap.width() < 4:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.cap.center()
        rx = self.cap.width() / 2 + self.MARGIN_X * 0.75
        ry = self.cap.height() / 2 + self.MARGIN_Y * 0.7
        r, g, b = self.rgb
        a = self.strength
        grad = QRadialGradient(QPointF(0, 0), 1.0)
        grad.setColorAt(0.0, QColor(r, g, b, int(120 * a)))
        grad.setColorAt(0.45, QColor(r, g, b, int(55 * a)))
        grad.setColorAt(1.0, QColor(r, g, b, 0))
        p.translate(c.x(), c.y() + self.cap.height() * 0.15)
        p.scale(rx, ry)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(0, 0), 1.0, 1.0)


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
    _tool_sig = pyqtSignal(str, object)
    _tool_end_sig = pyqtSignal(str, object)
    _confirm_sig = pyqtSignal(str)
    _confirm_done_sig = pyqtSignal()
    _upload_sig = pyqtSignal(str, float, str)
    _trouble_sig = pyqtSignal(str, str)
    _file_ready_sig = pyqtSignal(str)
    _chat_sig = pyqtSignal(str)

    W, H = 480, 340                 # окно с запасом под самый большой вид
    TOP = 16                        # отступ от верхнего края экрана
    DROP_SPREAD = 0.97              # капля выросла — начинает растекаться

    def __init__(self, on_open=None, poll: bool = True, on_confirm=None, on_file=None, on_text=None,
                 on_file_action=None, on_mic=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setFixedSize(self.W, self.H)
        self.model = IslandModel()
        self.on_open = on_open or (lambda: None)
        self.on_confirm = on_confirm or (lambda ok: None)    # кнопки «Разрешить / Отклонить»
        self.on_file = on_file                             # файл брошен на капсулу (путь)
        self.on_text = on_text or (lambda text: None)      # вопрос из чата в капсуле
        # что сделать с прочитанным файлом: ("summary" | "ask" | "cancel", вопрос)
        self.on_file_action = on_file_action or (lambda action, question: None)
        self.on_mic = on_mic or (lambda: None)             # «сказать голосом» из чата
        self._edit = _ChatEdit(self, self.close_chat)
        self._edit.returnPressed.connect(self._chat_submit)
        self._focusable = False
        self.setAcceptDrops(on_file is not None)
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
        # Лицо вместо шара из точек (JARVIS_ISLAND_FACE=0 — вернуть шар).
        import os
        self._face = None
        if os.getenv("JARVIS_ISLAND_FACE", "1") != "0":
            from ui_face import Face
            self._face = Face()
        self._face_rect = QRectF()                 # где нарисовано лицо — по нему тыкают
        self._was_listening = False
        self._glow = None
        self._glow_rgb, self._glow_a = list(STATE_RGB["idle"]), 0.0
        if os.getenv("JARVIS_ANIMATIONS", "1").strip().lower() not in ("0", "false", "no", "off"):
            glow = self._glow = IslandGlow()
            # Свечение — отдельное окно без родителя: уходит вместе с капсулой,
            # а не живёт после неё.
            self.destroyed.connect(lambda *_: _drop_glow(glow))

        self._state_sig.connect(self.model.set_state)
        self._level_sig.connect(self._feed_level)
        self._reply_sig.connect(lambda t: self.model.notify("ДЖАРВИС", t, "reply"))
        self._event_sig.connect(lambda title, text: self.model.notify(title, text, "event"))
        self._media_sig.connect(self._set_media)
        self._eyes_sig.connect(lambda on: setattr(self.model, "eyes", on))
        self._match_sig.connect(self.model.set_match)
        self._football_sig.connect(self.model.football)
        self._tool_sig.connect(lambda name, args: self.model.tool_start(name, args))
        self._tool_end_sig.connect(lambda name, ok: self.model.tool_end(name, ok))
        self._confirm_sig.connect(lambda q: self.model.ask_confirm(q))
        self._confirm_done_sig.connect(lambda: setattr(self.model, "confirm", None))
        self._upload_sig.connect(lambda name, frac, stage: self.model.set_upload(name, frac, stage))
        self._trouble_sig.connect(lambda title, text: self.model.trouble(title, text))
        self._file_ready_sig.connect(lambda name: self.model.file_ready(name))
        self._chat_sig.connect(self._open_chat)
        for sig in (self._state_sig, self._reply_sig, self._event_sig, self._media_sig, self._match_sig,
                    self._football_sig, self._tool_sig, self._confirm_sig, self._upload_sig, self._trouble_sig,
                    self._file_ready_sig, self._chat_sig):
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

    def tool_started(self, name: str, args: dict | None = None):
        """Джарвис взялся за инструмент — шаг задачи «› Открываю Telegram»."""
        self._tool_sig.emit(str(name), dict(args or {}))

    def tool_finished(self, name: str, ok: bool | None = True):
        """ok: True — сделано, False — не вышло, None — ждёт «да»."""
        self._tool_end_sig.emit(str(name), None if ok is None else bool(ok))

    def ask_confirm(self, question: str):
        """Опасное действие ждёт «да»: янтарная капсула с кнопками."""
        self._confirm_sig.emit(str(question))

    def confirm_done(self):
        self._confirm_done_sig.emit()

    def file_progress(self, name: str, frac: float, stage: str):
        """Брошенный файл: 0..1 — прогресс, <0 — не вышло (stage — почему)."""
        self._upload_sig.emit(str(name), float(frac), str(stage))

    def trouble(self, title: str, text: str):
        """Сбой или лимит запросов — глаза-спирали и розовый баннер."""
        self._trouble_sig.emit(str(title), str(text))

    def file_ready(self, name: str):
        """Брошенный файл прочитан — спросить: кратко, спросить про него, отмена."""
        self._file_ready_sig.emit(str(name))

    def open_chat(self, file: str = ""):
        """Открыть чат в капсуле (из трея, из панели, после файла)."""
        self._chat_sig.emit(str(file))

    # ── чат ─────────────────────────────────────────────────────────────────
    def _open_chat(self, file: str = ""):
        self.model.open_chat(file)
        self.dormant, self._quiet_since = False, 0.0
        if not self.wanted:                # окно Джарвиса развёрнуто — капсула всё равно нужна
            self.wanted = True
        self._apply_visibility()
        self._set_focusable(True)
        self._edit.clear()

    def close_chat(self):
        self.model.close_chat()
        self._edit.hide()
        self._set_focusable(False)

    def _set_focusable(self, on: bool):
        """Поле чата должно принимать клавиатуру — на это время капсула берёт фокус."""
        if on == self._focusable:
            return
        self._focusable = on
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, not on)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not on)
        if visible:
            self.show()                    # смена флагов прячет окно — вернуть
        if on:
            self.activateWindow()
            self._edit.setFocus()

    def _chat_submit(self):
        text = self._edit.text().strip()
        if not text:
            return
        self._edit.clear()
        if self.model.chat_send(text):
            self.on_file_action("ask", text)
        else:
            self.on_text(text)

    # ── показ ───────────────────────────────────────────────────────────────
    def _place(self):
        scr = QApplication.primaryScreen()
        g = scr.geometry() if scr else None
        if g:
            self.move(g.x() + (g.width() - self.W) // 2, g.y() + self.TOP)

    def set_wanted(self, on: bool):
        """Окно Джарвиса свёрнуто (on=True) или развёрнуто."""
        if not on and self.model.chat_view():
            return                         # открыт чат — капсула остаётся, пока его не закроют
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
                if self._face is not None:
                    self._face.greet()             # проявиться из темноты и помахать
                if self._glow is not None:
                    self._glow_a = 0.0
                    self._glow.show()
                self.show()
                self.raise_()
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
        if self._glow is not None:
            self._glow.hide()
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
        elif target == "expanded":
            th += self._expanded_extra()
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
        if self._face is not None:
            listening = m.state == "listening"
            if listening and not self._was_listening:
                self._face.hello()                 # позвали «Джарвис» — машет
            self._was_listening = listening
            self._face.set_emotion(m.emotion())
            self._face_look()
            self._face.step(dt, m.level)
        if self._glow is not None:
            rgb, a = m.glow(target)
            a *= min(1.0, max(0.0, self._s)) if not self._leaving else max(0.0, self._s)
            k = 1 - math.exp(-dt * 5)
            for i in range(3):
                self._glow_rgb[i] += (rgb[i] - self._glow_rgb[i]) * k
            self._glow_a += (a - self._glow_a) * k
            self._glow.follow(self, self.capsule_rect(), self._glow_rgb, self._glow_a)
        self._place_edit()
        # Содержимое: другой вид — старое гаснет; тот же — проявляется, когда размер почти готов.
        if target != self._view:
            self._ca -= dt * 14.0
            if self._ca <= 0.0:
                self._ca, self._view = 0.0, target
        elif abs(self._w - tw) < 10 and abs(self._h - th) < 6:
            self._ca = min(1.0, self._ca + dt * 7.0)
        self.update()

    def _input_rect(self) -> QRectF:
        cap = self.capsule_rect()
        return QRectF(cap.x() + 62, cap.bottom() - 46, cap.width() - 62 - 92, 32)

    def _place_edit(self):
        show = self._view == "chat" and self._ca > 0.6 and self.model.chat is not None and self._s > 0.9
        if show:
            r = self._input_rect().adjusted(14, 2, -6, -2)
            self._edit.setGeometry(int(r.x()), int(r.y()), int(r.width()), int(r.height()))
            if not self._edit.isVisible():
                self._edit.show()
                if self._focusable:
                    self._edit.setFocus()
        elif self._edit.isVisible():
            self._edit.hide()
        if self.model.chat is None and self._focusable:
            self._set_focusable(False)     # чат закрылся сам (тишина)

    def _expanded_extra(self) -> int:
        m = self.model
        extra = EXPANDED_MATCH_EXTRA if m.match else 0
        t = m.task_view()
        if t:
            extra += 10 + EXPANDED_STEP_H * min(TASK_STEPS_SHOWN, len(t.steps))
        return extra

    def _face_look(self):
        """Глаза следят за курсором: он левее капсулы — смотрит влево, ниже — вниз."""
        try:
            cap = self.capsule_rect()
            c = self.mapToGlobal(QPoint(int(cap.x() + 24), int(cap.height() / 2)))
            cur = QCursor.pos()
            self._face.look_at((cur.x() - c.x()) / 260.0, (cur.y() - c.y()) / 160.0)
        except Exception:                      # без экрана (тесты) — смотрит прямо
            self._face.look_at(0.0, 0.0)

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

    def closeEvent(self, ev):
        """Капсулу закрыли совсем — остановить анимацию и убрать свечение."""
        self._tmr.stop()
        self._poll_stop.set()
        if self._glow is not None:
            self._glow.hide()
        super().closeEvent(ev)

    # ── мышь ────────────────────────────────────────────────────────────────
    def enterEvent(self, _):
        self._hover_raw = True

    def leaveEvent(self, _):
        self._hover_raw = False

    def mouseReleaseEvent(self, ev):
        pos = ev.position()
        # Тык по лицу — лицо реагирует, а не открывает окно (много тыков — кружится).
        if self._face_rect.contains(pos) and self._view != "confirm":
            self.poke_face()
            return
        for name, rect in self._buttons.items():
            if rect.contains(pos):
                if name == "open":
                    self.on_open()
                elif name == "chat":
                    self._open_chat()
                elif name == "chat_close":
                    self.close_chat()
                elif name == "chat_send":
                    self._chat_submit()
                elif name == "chat_mic":
                    self.on_mic()
                elif name in ("f_sum", "f_ask", "f_cancel"):
                    fc = self.model.file_choice
                    self.model.file_choice = None
                    if name == "f_ask" and fc:
                        self._open_chat(fc.name)
                    else:
                        self.on_file_action("summary" if name == "f_sum" else "cancel", "")
                elif name in ("allow", "deny"):
                    self.model.confirm = None
                    self.on_confirm(name == "allow")
                else:
                    media = self.model.media
                    threading.Thread(target=media_command, args=(media, name), daemon=True).start()
                    if media and name == "toggle":
                        media.playing = not media.playing
                return
        if self.capsule_rect().contains(pos) and not self.hovered_expanded():
            self.on_open()

    def poke_face(self) -> str:
        res = self.model.poke()
        if self._face is not None:
            self._face.poke()
        self._maybe_wake()
        return res

    # ── файл, брошенный на капсулу ──────────────────────────────────────────
    @staticmethod
    def _local_file(ev) -> str:
        md = ev.mimeData()
        for url in md.urls() if md.hasUrls() else []:
            if url.isLocalFile():
                return url.toLocalFile()
        return ""

    def dragEnterEvent(self, ev):
        if self.on_file is not None and self._local_file(ev):
            ev.acceptProposedAction()
            self.model.drop_hover = True
            self._maybe_wake()

    def dragMoveEvent(self, ev):
        if self.model.drop_hover:
            ev.acceptProposedAction()

    def dragLeaveEvent(self, _):
        self.model.drop_hover = False

    def dropEvent(self, ev):
        self.model.drop_hover = False
        path = self._local_file(ev)
        if not path or self.on_file is None:
            return
        ev.acceptProposedAction()
        import os
        self.model.set_upload(os.path.basename(path), 0.04, "Читаю")
        self.on_file(path)

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

    def _avatar(self, p: QPainter, cx: float, cy: float, r: float):
        """Лицо Джарвиса (или шар из точек, если лицо выключено)."""
        self._face_rect = QRectF(cx - r * 1.7, cy - r * 1.4, r * 3.4, r * 2.8)
        if self._face is None:
            self._mini_orb(p, cx, cy, r)
            return
        self._face.paint(p, cx, cy, r * 2.05, self._face_rgb())

    def _face_rgb(self) -> tuple[int, int, int]:
        emo = self._face.emotion if self._face is not None else ""
        return {"alert": AMBER_RGB, "dizzy": TROUBLE_RGB, "hungry": DROP_RGB}.get(emo) or self._rgb_int()

    def _tint(self, p: QPainter, cap: QRectF, rgb, strong: float = 1.0):
        """Цветная подсветка капсулы слева — как фон-настроение в Coucou."""
        r, g, b = rgb
        grad = QLinearGradient(QPointF(cap.x(), 0), QPointF(cap.right(), 0))
        grad.setColorAt(0.0, QColor(r, g, b, int(78 * strong)))
        grad.setColorAt(0.55, QColor(r, g, b, int(26 * strong)))
        grad.setColorAt(1.0, QColor(r, g, b, int(8 * strong)))
        p.fillRect(cap, QBrush(grad))

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
        if mode == "confirm" and h > 80:
            self._paint_confirm(p, cap, white, dim)
            return
        if mode == "drop" and h > 64:
            self._paint_drop(p, cap, white, dim)
            return
        if mode == "chat" and h > 120:
            self._paint_chat(p, cap, white, dim)
            return
        if mode == "file" and h > 64:
            self._paint_file(p, cap, white, dim)
            return
        if mode == "upload" and h > 44:
            self._paint_upload(p, cap, white, dim)
            return
        if mode == "task" and h > 44:
            self._paint_task(p, cap, white, dim)
            return
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
            if b and b.kind == "error":
                self._tint(p, cap, TROUBLE_RGB)
                self._avatar(p, x0 + 32, cap.center().y(), 14)
                self._text(p, QRectF(x0 + 60, 9, w - 74, 16), b.title, 7.5, QColor(*TROUBLE_RGB).lighter(125),
                           bold=True, spacing=1.5)
                self._text(p, QRectF(x0 + 60, 25, w - 74, h - 30), b.text, 9.5, white, wrap=True,
                           align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                return
            if b and b.kind == "football":
                self._ball(p, x0 + 30, cap.center().y(), 24, QColor(238, 243, 246), self._clock * 40)
            else:
                self._avatar(p, x0 + 30, cap.center().y(), 13)
            if b:
                self._text(p, QRectF(x0 + 56, 9, w - 70, 16), b.title, 7.5, self._col(235, 0.2), bold=True, spacing=1.5)
                self._text(p, QRectF(x0 + 56, 25, w - 70, h - 30), b.text, 9.5, white, wrap=True,
                           align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            return
        # компактный и «что играет»
        self._avatar(p, x0 + 20, h / 2, 9)
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

    # ── задача, разрешение, файл ────────────────────────────────────────────
    def _spinner(self, p: QPainter, c: QPointF, r: float, color: QColor):
        p.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), int(-self._clock * 360 * 16) % (360 * 16), 250 * 16)

    def _step_mark(self, p: QPainter, c: QPointF, status: str, white: QColor):
        """✓ сделано, ✕ не вышло, крутилка — идёт."""
        if status == "run":
            self._spinner(p, c, 5, self._col(255, 0.2))
            return
        if status == "wait":                   # ждёт разрешения — янтарная пауза
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*AMBER_RGB))
            p.drawRoundedRect(QRectF(c.x() - 3.5, c.y() - 3.5, 2.6, 7), 1.2, 1.2)
            p.drawRoundedRect(QRectF(c.x() + 0.9, c.y() - 3.5, 2.6, 7), 1.2, 1.2)
            return
        ok = status == "ok"
        col = QColor(70, 232, 128) if ok else QColor(*TROUBLE_RGB)
        p.setPen(QPen(col, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if ok:
            path = QPainterPath(QPointF(c.x() - 4, c.y()))
            path.lineTo(QPointF(c.x() - 1.2, c.y() + 3))
            path.lineTo(QPointF(c.x() + 4.2, c.y() - 3.4))
            p.drawPath(path)
        else:
            p.drawLine(QPointF(c.x() - 3.2, c.y() - 3.2), QPointF(c.x() + 3.2, c.y() + 3.2))
            p.drawLine(QPointF(c.x() - 3.2, c.y() + 3.2), QPointF(c.x() + 3.2, c.y() - 3.2))

    def _bar(self, p: QPainter, rect: QRectF, frac: float, rgb, shimmer: bool = False):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 28))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        fw = max(rect.height(), rect.width() * max(0.0, min(1.0, frac)))
        fill = QRectF(rect.x(), rect.y(), fw, rect.height())
        r, g, b = rgb
        grad = QLinearGradient(fill.topLeft(), fill.topRight())
        grad.setColorAt(0, QColor(r, g, b, 160))
        grad.setColorAt(1, QColor(r, g, b, 255))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(fill, rect.height() / 2, rect.height() / 2)
        if shimmer:                            # бегущий блик — видно, что идёт работа
            u = (self._clock * 0.8) % 1.0
            sx = fill.x() + (fill.width() + 40) * u - 40
            sg = QLinearGradient(QPointF(sx, 0), QPointF(sx + 40, 0))
            sg.setColorAt(0, QColor(255, 255, 255, 0))
            sg.setColorAt(0.5, QColor(255, 255, 255, 120))
            sg.setColorAt(1, QColor(255, 255, 255, 0))
            p.save()
            p.setClipRect(fill)
            p.fillRect(fill, QBrush(sg))
            p.restore()

    def _paint_task(self, p: QPainter, cap: QRectF, white: QColor, dim: QColor):
        """«ВЫПОЛНЯЮ 2/3 › Открываю Telegram» — шаги просьбы, как у агента в Coucou."""
        t = self.model.task_view()
        if not t:
            return
        x0, w, h = cap.x(), cap.width(), cap.height()
        cy = h / 2
        if not t.running and not any(s.status == "wait" for s in t.steps):
            self._tint(p, cap, TROUBLE_RGB if t.failed else (70, 232, 128), 0.7)
        self._avatar(p, x0 + 30, cy, 13)
        total, done = len(t.steps), t.done
        waiting = any(s.status == "wait" for s in t.steps)
        head = ("ВЫПОЛНЯЮ" if t.running else "НЕ ВСЁ ВЫШЛО" if t.failed
                else "ЖДУ РАЗРЕШЕНИЯ" if waiting else "ГОТОВО")
        head_col = self._col(240, 0.25) if t.running else (QColor(*TROUBLE_RGB) if t.failed else QColor(70, 232, 128))
        self._text(p, QRectF(x0 + 58, 9, w - 120, 16), head, 7.5, head_col, bold=True, spacing=1.6)
        self._text(p, QRectF(x0 + w - 62, 9, 46, 16), f"{done}/{total}", 8, dim, bold=True,
                   align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        cur = t.current()
        if cur:
            self._step_mark(p, QPointF(x0 + 64, 34), cur.status, white)
            self._text(p, QRectF(x0 + 76, 25, w - 92, 18), cur.label, 9.5, white)
        self._bar(p, QRectF(x0 + 58, h - 8, w - 74, 2.5), done / max(1, total),
                  self._rgb_int() if t.running else (70, 232, 128), shimmer=t.running)

    def _paint_confirm(self, p: QPainter, cap: QRectF, white: QColor, dim: QColor):
        """Опасное действие ждёт «да»: янтарь, вопрос, «Отклонить» и «Разрешить»."""
        c = self.model.confirm_view()
        if not c:
            return
        x0, w, h = cap.x(), cap.width(), cap.height()
        self._tint(p, cap, AMBER_RGB)
        self._avatar(p, x0 + 36, 40, 17)
        self._text(p, QRectF(x0 + 70, 12, w - 86, 16), "НУЖНО РАЗРЕШЕНИЕ", 7.5, QColor(*AMBER_RGB), bold=True,
                   spacing=1.6)
        self._text(p, QRectF(x0 + 70, 28, w - 86, 34), c.question, 10, white, wrap=True,
                   align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        by = h - 38
        deny = QRectF(x0 + w - 222, by, 100, 28)
        allow = QRectF(x0 + w - 114, by, 100, 28)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1.0))
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(deny, 14, 14)
        self._text(p, deny, "Отклонить", 9, white, align=Qt.AlignmentFlag.AlignCenter)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(245, 247, 250))
        p.drawRoundedRect(allow, 14, 14)
        self._text(p, allow, "Разрешить", 9, QColor(18, 20, 24), bold=True, align=Qt.AlignmentFlag.AlignCenter)
        self._buttons["deny"], self._buttons["allow"] = deny, allow
        # Сколько ещё ждёт ответа: полоска тает за 90 с, потом вопрос снимается.
        left = max(0.0, 1.0 - (time.monotonic() - c.since) / CONFIRM_SEC)
        self._text(p, QRectF(x0 + 70, by, w - 300, 28), "или скажите «да»", 8, dim)
        self._bar(p, QRectF(x0 + 18, h - 5, w - 36, 2), left, AMBER_RGB)

    def _chip(self, p: QPainter, rect: QRectF, text: str, white: QColor, primary: bool = False):
        p.setPen(Qt.PenStyle.NoPen if primary else QPen(QColor(255, 255, 255, 60), 1.0))
        p.setBrush(QColor(245, 247, 250) if primary else QColor(255, 255, 255, 18))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        self._text(p, rect, text, 9, QColor(18, 20, 24) if primary else white, bold=primary,
                   align=Qt.AlignmentFlag.AlignCenter)

    def _paint_file(self, p: QPainter, cap: QRectF, white: QColor, dim: QColor):
        """Файл прочитан: «quote.pdf — что сделать?» Кратко · Спросить · Отмена."""
        fc = self.model.file_choice_view()
        if not fc:
            return
        x0, w, h = cap.x(), cap.width(), cap.height()
        self._tint(p, cap, DROP_RGB, 0.6)
        self._avatar(p, x0 + 40, h / 2, 16)
        self._text(p, QRectF(x0 + 76, 12, w - 92, 20), fc.name, 10.5, white, bold=True)
        self._text(p, QRectF(x0 + 76, 31, w - 92, 16), "Что с ним сделать?", 8.5, dim)
        by = h - 36
        x = x0 + 76
        for key, label, bw, primary in (("f_ask", "Спросить про него", 150, True),
                                        ("f_sum", "Кратко", 74, False), ("f_cancel", "Отмена", 74, False)):
            r = QRectF(x, by, bw, 26)
            self._chip(p, r, label, white, primary)
            self._buttons[key] = r
            x += bw + 8

    def _paint_chat(self, p: QPainter, cap: QRectF, white: QColor, dim: QColor):
        """Чат в капсуле: файл-чип, вопрос справа, ответ слева, поле внизу."""
        c = self.model.chat_view()
        if not c:
            return
        x0, y0, w = cap.x(), cap.y(), cap.width()
        self._tint(p, cap, self._rgb_int(), 0.35)
        # Шапка: «ЧАТ», чип файла, закрыть.
        self._text(p, QRectF(x0 + 18, y0 + 10, 60, 18), "ЧАТ", 7.5, self._col(235, 0.25), bold=True, spacing=1.6)
        if c.file:
            chip = QRectF(x0 + 64, y0 + 9, min(220.0, 34 + len(c.file) * 6.2), 20)
            p.setPen(QPen(QColor(255, 255, 255, 50), 1.0))
            p.setBrush(QColor(255, 255, 255, 14))
            p.drawRoundedRect(chip, 10, 10)
            self._text(p, chip.adjusted(10, 0, -8, 0), "📄 " + c.file, 8, white)
        close = QRectF(x0 + w - 34, y0 + 8, 22, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 26))
        p.drawEllipse(close)
        draw_icon(p, "close", close.center(), 9, white)
        self._buttons["chat_close"] = close
        # Разговор: вопрос — пузырём справа, ответ — текстом слева.
        y = y0 + 38
        if c.question:
            f = QFont("Segoe UI", 1)
            f.setPointSizeF(9)
            p.setFont(f)
            qw = min(w * 0.62, p.fontMetrics().horizontalAdvance(c.question) + 24)
            bub = QRectF(x0 + w - 18 - qw, y, qw, 24)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawRoundedRect(bub, 12, 12)
            self._text(p, bub.adjusted(12, 0, -10, 0), c.question, 9, white)
            y += 32
        ans = c.answer or ("…" if c.waiting else "")
        if not c.question and not ans:
            ans = "Спросите что угодно — отвечу здесь и голосом."
        self._text(p, QRectF(x0 + 20, y, w - 40, cap.bottom() - 54 - y), ans, 9.5,
                   white if c.answer else dim, wrap=True,
                   align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # Низ: лицо, поле, микрофон, отправить.
        self._avatar(p, x0 + 34, cap.bottom() - 30, 13)
        ir = self._input_rect()
        p.setPen(QPen(QColor(255, 255, 255, 46), 1.0))
        p.setBrush(QColor(255, 255, 255, 12))
        p.drawRoundedRect(ir, 16, 16)
        mic = QRectF(ir.right() + 8, ir.y() + 2, 28, 28)
        send = QRectF(mic.right() + 8, ir.y() + 2, 28, 28)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 26))
        p.drawEllipse(mic)
        draw_icon(p, "mic", mic.center(), 12, white)
        p.setBrush(QColor(245, 247, 250))
        p.drawEllipse(send)
        draw_icon(p, "send", send.center(), 12, QColor(18, 20, 24))
        self._buttons["chat_mic"], self._buttons["chat_send"] = mic, send

    def _paint_drop(self, p: QPainter, cap: QRectF, white: QColor, dim: QColor):
        """Над капсулой тащат файл: пунктирная рамка, лицо «открыло рот»."""
        x0, w, h = cap.x(), cap.width(), cap.height()
        self._tint(p, cap, DROP_RGB, 0.9)
        frame = cap.adjusted(7, 7, -7, -7)
        pen = QPen(QColor(*DROP_RGB, 200), 1.4, Qt.PenStyle.DashLine)
        pen.setDashOffset(-self._clock * 8)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(frame, 18, 18)
        self._avatar(p, x0 + 46, h / 2, 18)
        self._text(p, QRectF(x0 + 84, 20, w - 100, 22), "Отпустите — я посмотрю", 11.5, white, bold=True)
        self._text(p, QRectF(x0 + 84, 46, w - 100, 18), "Фото · PDF · Word · текст и код", 8.5, dim)

    def _paint_upload(self, p: QPainter, cap: QRectF, white: QColor, dim: QColor):
        u = self.model.upload_view()
        if not u:
            return
        x0, w, h = cap.x(), cap.width(), cap.height()
        self._avatar(p, x0 + 30, h / 2, 13)
        self._text(p, QRectF(x0 + 58, 9, w - 130, 18), f"{u.stage} {u.name}", 9.5, white, bold=True)
        self._text(p, QRectF(x0 + w - 66, 9, 50, 18), f"{int(u.frac * 100)}%", 8.5, dim,
                   align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._bar(p, QRectF(x0 + 58, 35, w - 74, 4), u.frac, DROP_RGB, shimmer=u.frac < 1.0)

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
            fm_w = self._abbr_w(sc.abbr(side), size * 0.78)
            img = self._crest_img(getattr(sc, f"{side}_crest"))
            if img is not None:                                  # настоящая эмблема клуба
                cs = min(h - 6, size * 2.0)
                cx_ = (x + 56 - fm_w - 5 - cs / 2) if side == "home" else (x + fm_w + 5 + cs / 2)
                p.drawImage(QRectF(cx_ - cs / 2, cy - cs / 2, cs, cs), img)
            else:
                r, g, b = sc.rgb(side)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(r, g, b))
                dot_x = (x + 56 - fm_w - 9) if side == "home" else (x + fm_w + 9)
                p.drawEllipse(QPointF(dot_x, cy), 3.6, 3.6)

    def _crest_img(self, path: str):
        """Эмблема с диска — один раз, дальше из памяти."""
        if not path:
            return None
        cache = self.__dict__.setdefault("_crests", {})
        if path not in cache:
            from PyQt6.QtGui import QImage
            img = QImage(path)
            cache[path] = None if img.isNull() else img.scaled(
                96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return cache[path]

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
        self._avatar(p, x0 + 26, cy, 14)
        dots = "." * (int(self._clock * 2.5) % 4)
        self._text(p, QRectF(x0 + 56, 0, 120, h), "Слушаю" + dots, 10.5, white, bold=True)
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
        self._avatar(p, x0 + 12, y0 + 12, 11)
        self._text(p, QRectF(x0 + 32, y0, w - 140, 24), STATE_LABEL.get(m.state, ""), 7.5,
                   self._col(235, 0.25), bold=True, spacing=1.6)
        # «Открыть» — круглая кнопка с «развернуть», как на iPhone.
        open_r = QRectF(x0 + w - 26, y0 - 1, 26, 26)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 34))
        p.drawEllipse(open_r)
        draw_icon(p, "expand", open_r.center(), 12, white)
        self._buttons["open"] = open_r
        chat_r = QRectF(open_r.x() - 34, y0 - 1, 26, 26)     # «Написать» — чат прямо здесь
        p.setBrush(QColor(255, 255, 255, 34))
        p.drawEllipse(chat_r)
        draw_icon(p, "speak", chat_r.center(), 12, white)
        self._buttons["chat"] = chat_r
        y = y0 + 38
        t = m.task_view()
        if t:
            for st in t.steps[-TASK_STEPS_SHOWN:]:
                self._step_mark(p, QPointF(x0 + 8, y + EXPANDED_STEP_H / 2), st.status, white)
                self._text(p, QRectF(x0 + 22, y, w - 22, EXPANDED_STEP_H), st.label, 9,
                           white if st.status == "run" else dim)
                y += EXPANDED_STEP_H
            y += 10
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

