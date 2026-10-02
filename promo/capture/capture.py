"""Съёмка всех клипов ролика с настоящего интерфейса Джарвиса — под длины реплик.

    JV_ROOT=/путь/к/копии python promo/capture/capture.py [hud|island|intro|<clip>...]

JV_ROOT — копия репозитория с демо-данными (seed.py): контакты, команды, пары,
«Обо мне». Сам репозиторий не трогаем. QT_SCALE_FACTOR=2 — кадры в 2x для крупных планов.
Шар «дышит» огибающей настоящей реплики (как в программе: set_level от голоса).
"""
from __future__ import annotations

import math
import os
import sys
import wave
from pathlib import Path

import numpy as np

PROMO = Path(__file__).resolve().parent.parent
os.environ.setdefault("REC_OUT", str(PROMO / "build" / "clips"))
sys.path.insert(0, str(PROMO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from timeline import BUILD, VO_AT, plan  # noqa: E402

ARGS = set(sys.argv[1:])


def want(*names: str) -> bool:
    return not ARGS or any(n in ARGS for n in names)


def envelope(key: str, fps: int = 30) -> np.ndarray:
    """Громкость реплики по кадрам 0..1 — так HUD получает уровень от голоса."""
    with wave.open(str(BUILD / "vo" / f"{key}.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768
        hop = w.getframerate() // fps
    rms = np.sqrt([np.mean(x[i:i + hop] ** 2) for i in range(0, len(x) - hop, hop)])
    return np.clip(rms / (np.percentile(rms, 95) or 1) * 0.9, 0, 1)


def word_time(text: str, word: str, vo_sec: float) -> float:
    """Когда в реплике звучит слово — пропорционально символам (точнее без транскрипции не надо)."""
    i = text.find(word)
    return VO_AT + vo_sec * (i / max(1, len(text))) if i >= 0 else VO_AT


# Что Джарвис делает в каждой сцене — настоящие инструменты и их ответы (core/result_card.build_card).
ORB = {
    "music": ("music_player", {"action": "play", "query": "Macan"}, "Включил Macan.", "Включаю Macan, сэр."),
    "movie": ("movie_player", {"action": "play", "title": "Интерстеллар"}, "Включаю «Интерстеллар».",
              "«Интерстеллар». Приятного просмотра."),
    "timer": ("sleep_timer", {"action": "set", "duration_minutes": 10}, "Таймер на 10 минут.", "Засёк десять минут."),
    "people": ("send_to_telegram", {"text": "Задержусь на 20 минут"}, "Отправил маме.", "Отправил маме, сэр."),
    "memory": ("save_to_memory", {"key": "кофе", "value": "Любит кофе без сахара", "category": "preferences"},
               "Запомнил.", "Запомнил: кофе без сахара."),
    "weather": ("weather", {"city": "Ташкент"}, "Погода в Ташкент: ясно. Температура 24°C.",
                "В Ташкенте ясно, двадцать четыре."),
    "translate": ("translation", {"text": "Good evening, sir. All systems are online.", "target_language": "русский"},
                  "Добрый вечер, сэр. Все системы в сети.", "Добрый вечер, сэр. Все системы в сети."),
    "calendar": ("calendar", {"action": "get_events"},
                 "• 08:30 Матанализ, ауд. 305\n• 10:00 Программирование, Лаб 2\n• 11:30 Английский, ауд. 117",
                 "Завтра три пары. Первая — в восемь тридцать."),
}
SYSTEM = [("computer_control", {"action": "volume_set", "value": 50}, "Громкость 50%.", "Громкость — пятьдесят."),
          ("computer_control", {"action": "brightness_set", "value": 30}, "Яркость 30%.", "Яркость — тридцать."),
          ("open_app", {"app_name": "Telegram"}, "Открыл Telegram.", "Telegram открыт.")]
FORECAST = {"city": "Ташкент", "temp": 24, "feels": 23, "desc": "Ясно", "humidity": 31, "wind": 9, "kind": "sun",
            "days": [{"name": "Сегодня", "max": "26", "min": "13", "desc": "Ясно", "rain": 0, "kind": "sun"},
                     {"name": "Завтра", "max": "24", "min": "12", "desc": "Переменная облачность", "rain": 10,
                      "kind": "cloud"},
                     {"name": "Послезавтра", "max": "21", "min": "11", "desc": "Небольшой дождь", "rain": 60,
                      "kind": "rain"}]}
PAGE = {"study": "study", "calls": "contacts", "commands": "commands", "safety": "keys", "setup": "help"}


def hud(scenes: list[dict]):
    from harness import vpump, vrecord
    import ui
    from actions import weather as W
    from core.result_card import build_card
    W.last_forecast["ташкент"] = FORECAST
    w = ui.JarvisUI("face.png")
    w.resize(1280, 800)
    vpump(2.0)
    w.show_page("home")
    vpump(1.0)

    def lvl(v):
        return lambda: w.set_level(float(v))

    def voice(key, t0, t1):
        env = envelope(key)
        return [(t0 + k / 30, lvl(env[k] if k < len(env) else 0.0)) for k in range(int((t1 - t0) * 30))]

    def mic(t0, t1):
        return [(t0 + k / 30, lvl(0.2 + 0.5 * abs(math.sin(k * 0.45)) * (0.6 + 0.4 * math.sin(k * 0.13))))
                for k in range(int((t1 - t0) * 30))]

    def card(name, args, result):
        c = build_card(name, args, result)
        return lambda: w.show_card(c["title"], c["address"], c["body"], b"", c["extra"])

    def cycle(t, tool, vo_key=None, vo_from=0.0, vo_to=0.0, listen=1.2):
        name, args, result, reply = tool
        ev = [(t, lambda: w.set_state("LISTENING"))] + mic(t, t + listen)
        ev += [(t + listen + 0.05, lambda: w.set_state("THINKING")), (t + listen + 0.2, lambda: w.lock_on(name)),
               (t + listen + 0.8, card(name, args, result)), (t + listen + 1.1, lambda: w.set_state("SPEAKING")),
               (t + listen + 1.15, lambda: w.set_subtitle(reply))]
        return ev

    def home():
        """Сброс между клипами: карточка, субтитр и подпись инструмента не должны
        переезжать в следующую сцену («Telegram открыт.» висел в сцене таймера)."""
        w.show_page("home")
        w.set_state("IDLE")
        hud_ = w._hud
        hud_._card, hud_._card_vis = None, 0.0
        hud_._sub_text, hud_._sub_alpha = "", 0.0
        hud_._tool, hud_._tool_lock = None, 0.0
        vpump(2.5)

    for s in scenes:
        sid, L, vo = s["id"], s["sec"] + 0.3, s["vo_sec"]
        if s["kind"] == "states" and want("hud", sid):
            ev = [(0.0, lambda: w.set_state("IDLE"))]
            tl = word_time(s["vo"], "слушаю", vo)
            td = word_time(s["vo"], "думаю", vo)
            ta = word_time(s["vo"], "отвечаю", vo)
            ev += [(tl, lambda: w.set_state("LISTENING"))] + mic(tl, td)
            ev += [(td, lambda: w.set_state("THINKING")), (ta, lambda: w.set_state("SPEAKING")),
                   (ta + 0.05, lambda: w.set_subtitle("Слушаю, сэр."))]
            env = envelope(sid)
            ev += [(ta + k / 30, lvl(env[min(len(env) - 1, int((ta - VO_AT) * 30) + k)]))
                   for k in range(int((L - ta) * 30))]
            vrecord("c_" + sid, w, L, ev)
            home()
        elif s["kind"] == "orb" and want("hud", sid):
            ev = cycle(0.0, ORB[sid])
            ev += [e for e in voice(sid, VO_AT, L) if e[0] >= 2.3]
            vrecord("c_" + sid, w, L, ev)
            home()
        elif s["kind"] == "multi" and want("hud", sid):
            ev, step = [], L / 3
            for i, tool in enumerate(SYSTEM):
                ev += cycle(i * step, tool, listen=0.5)
            ev += [e for e in voice(sid, VO_AT, L) if not any(i * step <= e[0] < i * step + 1.6 for i in range(3))]
            vrecord("c_" + sid, w, L, ev)
            home()
    if want("hud", "calendar"):
        vrecord("c_calendar", w, 6.0, cycle(0.0, ORB["calendar"]) + mic(2.3, 5.5))
        home()

    # экраны: настоящий переход (ui_anim) и выбранная запись в списке
    for s in scenes:
        key = PAGE.get(s["id"])
        if not key or not want("hud", s["id"]):
            continue
        w.show_page(key)
        vpump(0.6)
        pg = w._pages[key]
        lst = getattr(pg, "cmd_list", None) or getattr(pg, "people", None)
        if lst is not None and lst.count():
            lst.setCurrentRow(0)
        w.show_page("home")
        vpump(0.6)
        vrecord("c_" + s["id"], w, s["sec"] + 0.3, [(0.25, lambda k=key: w.show_page(k))])
        w.show_page("home")
        vpump(0.6)


ISLAND_EVENTS = {
    "music": lambda I, M, S: [(0.4, lambda: I.tool_started("music_player", {"action": "play", "query": "Macan"})),
                              (1.6, lambda: I.tool_finished("music_player")),
                              (1.8, lambda: I._set_media(M("Asphalt 8", "MACAN", "music"), "", 0.0))],
    "study": lambda I, M, S: [(0.3, lambda: I.file_progress("Расписание.png", 0.15, "Читаю")),
                              (1.4, lambda: I.file_progress("Расписание.png", 0.6, "Читаю")),
                              (2.6, lambda: I.file_progress("Расписание.png", 1.0, "Готово")),
                              (3.0, lambda: I.file_ready("Расписание.png"))],
    "people": lambda I, M, S: [(0.3, lambda: I.ask_confirm("Написать маме: «Задержусь на 20 минут»?"))],
    "calls": lambda I, M, S: [(2.5, lambda: I.notify("МАМА", "Хорошо, жду. Купи хлеб по дороге"))],
    "commands": lambda I, M, S: [(1.0, lambda: I.tool_started("open_app", {"app_name": "OBS"})),
                                 (2.6, lambda: I.tool_finished("open_app")),
                                 (2.8, lambda: I.tool_started("music_player", {"action": "play", "query": "Macan"})),
                                 (4.4, lambda: I.tool_finished("music_player")),
                                 (4.7, lambda: I.set_state("SPEAKING")), (4.8, lambda: I.reply("Эфир готов, сэр."))],
    "football": lambda I, M, S: [
        (0.2, lambda: I.set_match(S("Реал Мадрид", "Барселона", "1", "1", "66'", "RMA", "BAR", "febe10", "a50044"))),
        (2.2, lambda: I.set_match(S("Реал Мадрид", "Барселона", "2", "1", "67'", "RMA", "BAR", "febe10", "a50044"))),
        (2.25, lambda: I.football("ГОЛ", "Реал Мадрид 2:1 Барселона, 67'"))],
    "capsule": lambda I, M, S: [(0.3, lambda: I.set_state("LISTENING")), (2.4, lambda: I.set_state("THINKING")),
                                (3.0, lambda: I.tool_started("open_app", {"app_name": "Telegram"})),
                                (4.2, lambda: I.tool_finished("open_app")),
                                (4.4, lambda: I.set_state("SPEAKING")), (4.5, lambda: I.reply("Telegram открыт, сэр."))],
    "safety": lambda I, M, S: [(0.4, lambda: I.ask_confirm("Выключить компьютер?"))],
}


def island(scenes: list[dict]):
    from harness import vpump, vrecord
    from ui_island import Island, Media, Score
    for s in scenes:
        sid = s["id"]
        if sid not in ISLAND_EVENTS or not want("island", sid):
            continue
        isl = Island(poll=False)               # новая капсула на клип: состояние не переезжает между сценами
        isl.set_wanted(True)
        vpump(1.5)
        ev = ISLAND_EVENTS[sid](isl, Media, Score)
        if sid == "capsule":                                     # «Слушаю» дышит голосом пользователя
            ev += [(0.3 + k / 30, (lambda v: lambda: isl.feed_level(v))(0.6 * abs(math.sin(k * 0.5))))
                   for k in range(60)]
        vrecord("i_" + sid, isl, s["sec"] + 0.3, ev)
        isl.hide()
        isl.deleteLater()


def intro():
    """Интро «два хлопка» — IntroScene рисует кадр по времени; сразу в 1080×1920."""
    import harness  # noqa: F401  — QApplication и путь к JV_ROOT
    from PyQt6.QtGui import QColor, QImage, QPainter
    from core import intro as S
    from ui_intro import IntroScene
    checks = {k: True for k in S.CHECKS}
    sc = IntroScene(checks)
    # Субтитры интро рассчитаны на широкий экран — в 9:16 их рисует ролик.
    sc.set_voice([(at, 2.0, "") for at, _t in S.lines(checks, hour=20)], [])
    out = Path(os.environ["REC_OUT"]) / "intro"
    out.mkdir(parents=True, exist_ok=True)
    for k in range(int(S.T_END * 30)):
        img = QImage(1080, 1920, QImage.Format.Format_RGB32)
        img.fill(QColor(3, 6, 9))
        p = QPainter(img)
        sc.paint_at(p, 1080, 1920, k / 30)
        p.end()
        img.save(str(out / f"{k:05d}.png"))
    pcm = S.pcm16(0.6, 24000)
    with wave.open(str(BUILD / "audio" / "intro_sfx.wav"), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(pcm)
    print("intro:", k + 1, "frames", flush=True)


if __name__ == "__main__":
    scenes = plan()
    if want("intro"):
        intro()
    if want("island") or any(a in ISLAND_EVENTS for a in ARGS):
        island(scenes)
    if not ARGS or ARGS - {"intro", "island"}:
        hud(scenes)
