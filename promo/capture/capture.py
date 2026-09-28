"""Захват НАСТОЯЩЕГО интерфейса Джарвиса для промо-ролика.

Поднимает ui.JarvisUI без экрана (QT_QPA_PLATFORM=offscreen), подменяет часы
анимаций на детерминированные (1/30 с на кадр) и сохраняет PNG/JPEG:
  • последовательности кадров главного окна — шар по состояниям, фигуры
    инструментов (глобус, эквалайзер, экран, реактор), карточки результата;
  • капсулу (ui_island) — ответ, музыку, живой счёт матча;
  • страницы окна — Учёба, Команды, Контакты, Обо мне, Ключи, Что умею.

Запускать на КОПИИ проекта: демо-данные (расписание, контакты, команды)
пишутся в папку данных проекта.
    python promo/capture/capture.py <out_dir>
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time as _time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.5")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
FPS = 30


# ── детерминированные часы для анимаций ─────────────────────────────────────
class _Clock:
    t = 10_000.0
    wall0 = _time.time()


class _FakeTime:
    def __getattr__(self, name):
        return getattr(_time, name)

    def monotonic(self):
        return _Clock.t

    def time(self):
        return _Clock.wall0 + (_Clock.t - 10_000.0)


from PyQt6.QtCore import QEvent, QEventLoop  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import hud_cards  # noqa: E402
import ui  # noqa: E402
import ui_island  # noqa: E402

for mod in (ui, hud_cards, ui_island):
    mod.time = _FakeTime()


# ── демо-данные через настоящие модули ──────────────────────────────────────
def seed():
    from core.study import study
    s = study()
    s.semester_start = "2026-09-01"
    s.lessons = []
    s.tasks = []
    from core.study import Lesson, Task
    L = [("Математический анализ", 0, "08:30", "09:50", "305", "лекция"),
         ("Программирование", 0, "10:00", "11:20", "Лаб. 2", "лабораторная"),
         ("Английский язык", 1, "08:30", "09:50", "214", "практика"),
         ("Физика", 1, "10:00", "11:20", "101", "лекция"),
         ("Программирование", 2, "10:00", "11:20", "Лаб. 2", "лекция"),
         ("Математический анализ", 2, "11:30", "12:50", "305", "практика"),
         ("История", 3, "08:30", "09:50", "118", "семинар"),
         ("Физика", 3, "10:00", "11:20", "Лаб. 4", "лабораторная"),
         ("Английский язык", 4, "10:00", "11:20", "214", "практика"),
         ("Базы данных", 4, "11:30", "12:50", "Лаб. 2", "лекция"),
         ("Базы данных", 5, "10:00", "11:20", "Лаб. 2", "лабораторная")]
    s.lessons = [Lesson(subject=a, weekday=b, start=c, end=d, room=e, kind=f) for a, b, c, d, e, f in L]
    s.tasks = [Task("Лабораторная №3", "Программирование", "2026-10-02", "лабораторная"),
               Task("Эссе про Тимура", "История", "2026-10-01", "домашка"),
               Task("Контрольная по пределам", "Математический анализ", "2026-10-05", "контрольная"),
               Task("Презентация по SQL", "Базы данных", "2026-10-03", "проект"),
               Task("Выучить 30 слов", "Английский язык", "2026-09-29", "домашка"),
               Task("Отчёт по лабе №2", "Физика", "2026-09-27", "лабораторная", done=True)]
    s.save()

    from core.contacts import Contact, book
    b = book()
    b.contacts = []
    for c in [Contact("Мама", "@mama_tg", ["мама", "мамочка"], read_aloud=True),
              Contact("Азиз Каримов", "@aziz_k", ["Азиз", "брат"]),
              Contact("Дилноза", "@dilnoza", ["Диля"]),
              Contact("Папа", "@papa_tg", ["папа", "отец"]),
              Contact("Тимур — одногруппник", "@timur_dev", ["Тимур"]),
              Contact("Староста", "@starosta_group", ["староста"])]:
        b.upsert(c)

    from core.macros import Command, macros
    m = macros()
    for key in ("windows", "browser", "vscode"):
        try:
            m.install_pack(key)
        except Exception as exc:
            print("pack", key, exc)
    m.upsert(Command(name="Режим стрима", phrases=["включи режим стрима", "начинаем стрим"],
                     steps=[{"do": "open_app", "value": "obs"},
                            {"do": "wait", "value": "2"},
                            {"do": "open_url", "value": "twitch.tv/dashboard"},
                            {"do": "media", "value": "play"},
                            {"do": "say", "value": "Стрим готов, сэр"}]))
    m.upsert(Command(name="Рабочий режим", phrases=["рабочий режим", "садимся работать"],
                     steps=[{"do": "open_app", "value": "code"},
                            {"do": "open_url", "value": "github.com"},
                            {"do": "volume", "value": "30"},
                            {"do": "say", "value": "Рабочее место готово"}],
                     when=[{"on": "time", "at": "09:00", "days": "будни"}]))
    m.upsert(Command(name="Созвон", phrases=["созвон", "начинаем созвон"],
                     steps=[{"do": "open_app", "value": "telegram"},
                            {"do": "media", "value": "play"},
                            {"do": "volume", "value": "60"}]))
    m.upsert(Command(name="Спокойной ночи", phrases=["спокойной ночи"],
                     steps=[{"do": "media", "value": "play"},
                            {"do": "say", "value": "Спокойной ночи, сэр. Будильник на 7:30"},
                            {"do": "keys", "value": "win+d"}]))

    from core import about_me
    for k, v in (("name", "Сардор"), ("address_as", "сэр"), ("city", "Ташкент"), ("birthday", "12 марта"),
                 ("languages", "русский, узбекский, английский"), ("occupation", "студент, программист"),
                 ("wake_time", "7:30"), ("sleep_time", "23:30"), ("work_hours", "будни 9–18"),
                 ("music", "Imagine Dragons, lo-fi"), ("club", "Реал Мадрид"), ("news", "технологии, футбол"),
                 ("talk_style", "коротко и по делу"), ("family", "мама, папа, брат Азиз"),
                 ("goals", "сдать сессию, выучить английский"), ("health", "пить больше воды")):
        try:
            about_me.answer(k, v, sync_now=False)
        except Exception as exc:
            print("about", k, exc)

    from actions import weather
    weather.last_forecast["ташкент"] = {
        "city": "Ташкент", "temp": 24, "feels": 23, "desc": "Ясно", "humidity": 31, "wind": 9,
        "kind": "sun", "days": [
            {"name": "Сегодня", "kind": "sun", "max": 26, "min": 14, "rain": 0, "desc": "ясно"},
            {"name": "Завтра", "kind": "cloud", "max": 23, "min": 13, "rain": 10, "desc": "облачно"},
            {"name": "Среда", "kind": "rain", "max": 19, "min": 11, "rain": 60, "desc": "дождь"}]}


seed()


def mock_services():
    """«Ключи»: все сервисы заданы и проверены (демо — ключи не настоящие)."""
    from core import keys as K
    fake = {f.key: "demo-key-000000" + str(i) for i, f in enumerate(f for sv in K.SERVICES for f in sv.fields)}
    K.load_values = lambda: dict(fake)
    for sv in K.SERVICES:
        sv.check = lambda v, t=sv.title: ("ok", "Работает.")


def mock_football():
    """«Футбол»: любимый клуб, живой матч, результаты, новости (демо)."""
    from datetime import datetime, timedelta

    from core import football as F
    fb = F.football()
    fb.state["club"] = "Реал Мадрид"
    now = datetime.now().replace(second=0, microsecond=0)
    kw = dict(league="Ла Лига", home_color="febe10", away_color="a50044")

    def M(i, d, home, away, hs="", as_="", st="post", det="FT", ha="RMA", aa="BAR", **k):
        return F.Match(str(i), now + d, home, away, hs, as_, st, det, home_abbr=ha, away_abbr=aa, **{**kw, **k})
    live = M(1, -timedelta(minutes=67), "Реал Мадрид", "Барселона", "2", "1", "in", "67'")
    results = [M(2, -timedelta(days=4), "Реал Мадрид", "Севилья", "3", "0", aa="SEV", away_color="d6001c"),
               M(3, -timedelta(days=8), "Валенсия", "Реал Мадрид", "1", "2", ha="VAL", aa="RMA", home_color="ff8200", away_color="febe10"),
               M(4, -timedelta(days=12), "Реал Мадрид", "Атлетико", "1", "1", aa="ATM", away_color="cb3524"),
               M(5, -timedelta(days=16), "Бетис", "Реал Мадрид", "0", "2", ha="BET", aa="RMA", home_color="00954c", away_color="febe10")]
    upcoming = [M(6, timedelta(days=4, hours=3), "Вильярреал", "Реал Мадрид", st="pre", det="", ha="VIL", aa="RMA", home_color="ffe667", away_color="febe10"),
                M(7, timedelta(days=8, hours=2), "Реал Мадрид", "Жирона", st="pre", det="", aa="GIR", away_color="e21c23"),
                M(8, timedelta(days=12, hours=4), "Осасуна", "Реал Мадрид", st="pre", det="", ha="OSA", aa="RMA", home_color="0a346f", away_color="febe10")]
    news = [F.News(t, "https://example.com", "Новости клуба", now - timedelta(hours=h)) for t, h in (
        ("Эль Класико: где смотреть и во сколько начало", 2),
        ("Заявка «Реала» на матч с «Барселоной»", 5),
        ("Обзор тура Ла Лиги", 9),
        ("Расписание матчей «Реала» на октябрь", 20))]
    data = {"club": "Реал Мадрид", "name": "Реал Мадрид", "team": "Real Madrid", "next": live,
            "results": results, "upcoming": upcoming, "news": news, "goals": True, "news_on": True, "now": now}
    fb.overview = lambda news=6: data
    fb.fixtures = lambda force=False: []


mock_services()
mock_football()

win = ui.JarvisUI("face.png")
win.resize(1280, 800)
# шапка: сколько команд выполнено и сколько Джарвис в сети
win._tools_run = 128
win._started = _Clock.t - (3 * 3600 + 42 * 60)
for line in ("Вы: Джарвис, доброе утро",
             "Джарвис: Доброе утро, сэр. В Ташкенте +18°, в 10:00 Программирование, Лаб. 2.",
             "Вы: Включи что-нибудь для работы",
             "Джарвис: Включил lo-fi в Spotify.",
             "SYS: 📚 Учёба: Через 10 минут пара: Физика, ауд. 101",
             "Вы: Напиши маме, что задержусь",
             "Джарвис: Отправил маме в Telegram: «Задержусь».",
             "SYS: ⚽ Футбол: Реал Мадрид — Барселона, 2:1, 67'"):
    win.write_log(line)
hud = win._hud
hud._tmr.stop()
isl = getattr(win, "_island", None)
if isl is not None:
    isl._tmr.stop()


def pump():
    app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 5)
    # deleteLater() без настоящего exec() сам не срабатывает — старые виджеты висели бы в кадре
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def tick(level: float = 0.0):
    _Clock.t += 1 / FPS
    if level:
        hud.feed_level(level)
    hud._step()
    pump()


def voice(i: int, amp: float = 0.7) -> float:
    """Похожая на речь громкость: слоги и паузы между словами."""
    syll = abs(math.sin(i * 0.9)) * (0.6 + 0.4 * math.sin(i * 0.23))
    word = 1.0 if (i // 9) % 4 else 0.15
    return max(0.0, amp * syll * word + random.uniform(-0.05, 0.05))


def card(name, args, result):
    from core.result_card import build_card
    c = build_card(name, args, result)
    win._hud.show_card(c["title"], c["address"], c["body"], b"", c["extra"])


def save(img, path: Path, q=90):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "JPG" if path.suffix == ".jpg" else "PNG", q)


def shoot(name: str, seconds: float, script: dict[int, callable] | None = None, level=None):
    """Кадры главного окна: script — {кадр: действие}, level(i) — громкость."""
    n = int(seconds * FPS)
    for i in range(n):
        if script and i in script:
            script[i]()
        tick(level(i) if level else 0.0)
        save(win.grab(), OUT / name / f"{i:04d}.jpg", 90)
    manifest[name] = n
    print("seq", name, n, flush=True)


def reset(state="IDLE"):
    win._apply_state(state)
    hud._card, hud._card_vis = None, 0.0
    hud._sub_text, hud._sub_alpha = "", 0.0
    hud._orb.set_shape("sphere")
    hud._tool, hud._tool_lock = None, 0.0
    for _ in range(90):
        tick()


manifest: dict[str, int] = {}
ONLY = os.getenv("ONLY", "")
win.show_page("home")
random.seed(3)

if ONLY in ("island", "pages"):
    shoot = lambda *a, **k: None  # noqa: E731
    save_page = ONLY == "pages"
else:
    save_page = True
# 1. Пробуждение: ждёт → «Джарвис» → слушает (дышит голосом)
reset()
shoot("wake", 3.0, {30: lambda: win._apply_state("LISTENING")},
      level=lambda i: voice(i, 0.8) if 34 < i < 80 else 0.0)


def command(name, tool, args, result, reply, spoken=None):
    """Слушает фразу → думает (фигура инструмента) → карточка → говорит."""
    reset("LISTENING")
    script = {
        0: lambda: win._apply_state("LISTENING"),
        24: lambda: (win._apply_state("THINKING"), hud.lock_on(tool)),
        36: lambda: card(tool, args, result),
        44: lambda: (win._apply_state("SPEAKING"), hud.set_subtitle(reply)),
    }
    shoot(name, 3.2, script, level=lambda i: voice(i, 0.7) if i < 22 else (voice(i, 0.55) if i > 44 else 0.0))


command("weather", "weather", {"city": "Ташкент"}, "В Ташкенте +24°, ясно.",
        "В Ташкенте плюс двадцать четыре и ясно, сэр. В среду дождь — зонт пригодится.")
command("music", "music_player", {"action": "play", "query": "Imagine Dragons"},
        "Включил «Imagine Dragons» в Spotify.", "Включил Imagine Dragons, сэр.")
command("volume", "computer_control", {"action": "volume_set", "value": "30"},
        "Громкость 30%.", "Сделал потише — тридцать процентов.")
command("screen", "look_at_screen", {"prompt": "найди ошибку в коде", "source": "active_window"},
        "В строке 42 вызов без await.", "В сорок второй строке забыт await, сэр — функция не дождётся ответа.")
command("translate", "translation", {"action": "translate", "text": "Where is the nearest metro station?",
                                     "target_language": "русский"},
        "Где ближайшая станция метро?", "Где ближайшая станция метро?")
command("telegram", "send_to_telegram", {"text": "Скриншот экрана", "send_screenshot": True},
        "Отправил в Telegram.", "Отправил снимок экрана вам в Telegram.")
command("memory", "save_to_memory", {"category": "habits", "key": "кофе", "value": "пьёт кофе без сахара"},
        "Запомнил.", "Запомнил: кофе без сахара.")
command("timer", "sleep_timer", {"action": "set", "duration_minutes": 30},
        "Таймер сна на 30 минут.", "Через тридцать минут выключу компьютер, сэр.")
command("app", "open_app", {"app_name": "Telegram"}, "Открыл Telegram.", "Открыл Telegram.")
command("search", "web_search", {"query": "курс доллара"},
        "По запросу «курс доллара»: ЦБ Узбекистана — 12 650 сум | Kapitalbank — покупка 12 610 | "
        "Hamkorbank — продажа 12 700", "Курс доллара — около двенадцати тысяч шестисот пятидесяти сумов.")

# 2. Страницы окна — живые сценарии: клики, вкладки, прокрутка
from PyQt6.QtWidgets import QListWidget, QPushButton, QScrollArea  # noqa: E402


def page(key):
    win.show_page(key)
    return win._pages[key]


def button(widget, text):
    for b in widget.findChildren(QPushButton):
        if text in b.text():
            return b
    raise LookupError(text)


def scroll_area(widget):
    areas = [a for a in widget.findChildren(QScrollArea) if a.isVisible()]
    return max(areas, key=lambda a: a.verticalScrollBar().maximum(), default=None)


def smooth_scroll(widget, i, start, frames, to=1.0):
    """Плавная прокрутка самой длинной области страницы: кадры start..start+frames."""
    area = scroll_area(widget)
    if not area or not (start <= i <= start + frames):
        return
    p = (i - start) / frames
    p = p * p * (3 - 2 * p)
    bar = area.verticalScrollBar()
    bar.setValue(int(bar.maximum() * to * p))


def pshoot(name, seconds, per_frame):
    n = int(seconds * FPS)
    for i in range(n):
        per_frame(i)
        tick()
        save(win.grab(), OUT / name / f"{i:04d}.jpg", 90)
    manifest[name] = n
    print("seq", name, n, flush=True)


if save_page:
    # экскурсия по левой панели — те же экраны, что открываются голосом
    tour = ["home", "commands", "study", "football", "contacts", "about", "keys", "help"]
    win.show_page("home")
    pshoot("tour", 3.2, lambda i: win.show_page(tour[min(i // 12, len(tour) - 1)]) if i % 12 == 0 else None)

    # диалог: окно истории открыто, новый обмен репликами
    win.show_page("home")
    reset()                                    # без карточки от прошлой команды
    win._show_chat(True)
    dlg = {10: lambda: win.write_log("Вы: Что у меня завтра?"),
           18: lambda: win._apply_state("THINKING"),
           30: lambda: (win._apply_state("SPEAKING"),
                        win.write_log("Джарвис: Завтра две пары: Английский в 8:30 и Физика в 10:00. "
                                      "И дедлайн — эссе про Тимура."),
                        hud.set_subtitle("Завтра две пары: Английский в 8:30 и Физика в 10:00."))}
    pshoot("dialog", 3.0, lambda i: dlg[i]() if i in dlg else hud.feed_level(voice(i, .5) if i > 30 else 0))
    win._show_chat(False)
    win._apply_state("IDLE")

    fbp = page("football")
    fbp.refresh(force=True)
    pshoot("page_football", 3.0, lambda i: smooth_scroll(fbp, i, 45, 40, 0.5))

    pg = page("study")
    pg.show_page(0)
    pshoot("page_study", 3.0, lambda i: pg.show_page(1) if i == 48 else None)

    pg = page("commands")
    pg.show_page(0)
    lst = pg.findChildren(QListWidget)[0]

    def cmd_frame(i):
        if i == 14:
            lst.setCurrentRow(0)
        smooth_scroll(pg, i, 40, 44, 0.6)
    pshoot("page_commands", 3.0, cmd_frame)

    pg = page("contacts")
    lst = [w for w in pg.findChildren(QListWidget) if w.count()][0]
    pshoot("page_contacts", 3.0, lambda i: lst.setCurrentRow({0: 2, 30: 0, 60: 1}[i]) if i in (0, 30, 60) else None)

    pg = page("help")
    pshoot("page_help", 3.0, lambda i: smooth_scroll(pg, i, 10, 70, 0.55))

    pg = page("about")
    pshoot("page_about", 2.5, lambda i: smooth_scroll(pg, i, 8, 60, 0.4))

    pg = page("keys")
    pshoot("page_keys", 2.5, lambda i: pg.check_all() if i == 2 else smooth_scroll(pg, i, 20, 50, 0.35))
win.show_page("home")

# 3. Капсула (Dynamic Island на рабочем столе)
if isl is not None and ONLY != "pages":
    isl.set_wanted(True)
    isl.show()

    def ishoot(name, seconds, script):
        n = int(seconds * FPS)
        for i in range(n):
            if i in script:
                script[i]()
            _Clock.t += 1 / FPS
            isl.model.level = max(isl.model.level, voice(i, 0.6) if name == "island_listen" else 0.0)
            isl._step()
            pump()
            save(isl.grab(), OUT / name / f"{i:04d}.png")
        manifest[name] = n
        print("seq", name, n, flush=True)

    ishoot("island_listen", 2.5, {0: lambda: isl.model.set_state("LISTENING", _Clock.t)})
    ishoot("island_reply", 3.0, {0: lambda: (isl.model.set_state("SPEAKING", _Clock.t),
                                              isl.model.notify("ДЖАРВИС", "Через 10 минут пара: Физика, ауд. 101", "reply", _Clock.t))})
    ishoot("island_music", 3.0, {0: lambda: (isl.model.banners.clear(), isl.model.set_state("IDLE", _Clock.t),
                                              isl._set_media(ui_island.Media("Believer", "Imagine Dragons"), "", 0.0))})
    ishoot("island_match", 3.0, {
        0: lambda: (isl.model.banners.clear(), isl._set_media(None, "", 0.0)),
        1: lambda: isl.model.set_match(ui_island.Score("Реал Мадрид", "Барселона", "1", "1", "67'",
                                                      "RMA", "BAR", "febe10", "a50044"), _Clock.t),
        40: lambda: isl.model.set_match(ui_island.Score("Реал Мадрид", "Барселона", "2", "1", "71'",
                                                       "RMA", "BAR", "febe10", "a50044"), _Clock.t)})

(OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("done", OUT)
os._exit(0)
