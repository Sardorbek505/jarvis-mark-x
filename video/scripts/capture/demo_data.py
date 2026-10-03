"""Demo data for product captures — a fictional user, nothing personal.

Everything is written through the app's own modules (core.study, core.about_me,
core.contacts, core.macros) into a throwaway %APPDATA%, so the screens show
exactly what the real app renders for such a user.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
# Captures run on a throwaway copy of the sources: from source the app keeps its
# data in the project root (core.paths.get_data_root), not in %APPDATA%.
SRC = Path(os.environ.get("JARVIS_SRC") or REPO)
# Screens show a fixed, friendly moment: Monday of this week, 9:40 local time
# (the app's default zone, Asia/Almaty) — mid-lecture, with the next class
# coming up — not whatever the capture machine's clock says.
_monday = date.today() - timedelta(days=date.today().weekday())
DEMO_NOW = datetime.combine(_monday, time(9, 40))
DEMO_TZ = "Asia/Almaty"


def source_copy(dest: Path) -> Path:
    """Copy the tracked app sources (without video/) to `dest`."""
    import subprocess
    files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    for f in files:
        if f.startswith("video/"):
            continue
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / f, dest / f)
    return dest


def env(appdata: Path, fonts_conf: Path | None = None, scale: float = 1.5) -> dict:
    """Environment for running the app headless with isolated data."""
    e = dict(os.environ)
    e.update({
        "APPDATA": str(appdata),
        "HOME": str(appdata),
        "PYTHONUSERBASE": os.environ.get("PYTHONUSERBASE", str(Path.home() / ".local")),
        "QT_QPA_PLATFORM": "offscreen",
        "QT_SCALE_FACTOR": str(scale),
        "JARVIS_NO_WELCOME": "1",
        "JARVIS_ISLAND": "0",
        "PYTHONPATH": str(SRC),
    })
    if fonts_conf:
        e["FONTCONFIG_FILE"] = str(fonts_conf)
    return e


def seed() -> None:
    """Call inside the app environment (APPDATA already pointing to a temp dir)."""
    sys.path.insert(0, str(SRC))
    from core import about_me
    from core import contacts as K
    from core import macros as M
    from core import study as S

    s = S.study()
    s.now = lambda: DEMO_NOW
    s.lessons = [
        S.Lesson("Математический анализ", 0, "08:30", "10:00", room="301", kind="лекция"),
        S.Lesson("Программирование на Python", 0, "10:10", "11:40", room="214", kind="практика"),
        S.Lesson("Английский язык", 1, "08:30", "10:00", room="105", kind="практика"),
        S.Lesson("Физика", 1, "10:10", "11:40", room="Б-2", kind="лекция"),
        S.Lesson("Базы данных", 2, "10:10", "11:40", room="214", kind="лабораторная"),
        S.Lesson("Алгоритмы и структуры данных", 3, "08:30", "10:00", room="301", kind="лекция"),
        S.Lesson("Математический анализ", 3, "10:10", "11:40", room="302", kind="практика"),
        S.Lesson("Веб-разработка", 4, "11:50", "13:20", room="214", kind="практика"),
    ]
    s.save()
    for title, subject, due in [
        ("Реферат: ряды Фурье", "Математический анализ", "в пятницу"),
        ("Лабораторная №3 — парсер", "Программирование на Python", "в среду"),
        ("Эссе «My future job»", "Английский язык", "в четверг"),
    ]:
        s.add_task(title, subject, due)

    for key, value in [
        ("name", "Алекс"), ("address_as", "сэр"), ("city", "Ташкент"),
        ("languages", "русский, английский"), ("occupation", "студент, программист"),
        ("wake_time", "7:00"), ("sleep_time", "23:30"), ("music", "lo-fi, синтвейв"),
        ("news", "технологии, космос"), ("talk_style", "коротко, с юмором"),
        ("goals", "выучить английский, выпустить своё приложение"),
    ]:
        about_me.answer(key, value, sync_now=False)

    b = K.book()
    b.contacts = [
        K.Contact("Мама", "@demo_mama", aliases=["мама", "мамочка"], read_aloud=True),
        K.Contact("Азиз", "@demo_aziz", aliases=["брат", "братишка"]),
        K.Contact("Команда проекта", "@demo_team", aliases=["команда"], can_call=False),
    ]
    b.save()

    m = M.macros()  # first load installs the default command pack
    for cmd in [
        {"name": "Режим стрима", "phrases": ["включи режим стрима"],
         "steps": [{"do": "open_app", "value": "OBS Studio"}, {"do": "wait", "value": "2"},
                   {"do": "keys", "value": "ctrl+shift+s"}, {"do": "volume", "value": "40"}]},
        {"name": "Режим учёбы", "phrases": ["режим учёбы"],
         "steps": [{"do": "open_app", "value": "VS Code"}, {"do": "open_url", "value": "https://classroom.google.com"},
                   {"do": "tool", "tool": "music_player", "args": {"query": "lo-fi"}}]},
        {"name": "Утренний старт", "phrases": ["доброе утро"],
         "steps": [{"do": "tool", "tool": "morning_briefing"}, {"do": "open_app", "value": "Telegram"}]},
        {"name": "Выключить компьютер", "phrases": ["выключи компьютер"], "confirm": True,
         "steps": [{"do": "keys", "value": "win+x u u"}]},
    ]:
        m.upsert(M.Command.from_dict(cmd))
