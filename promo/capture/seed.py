"""Демо-данные для съёмки — через собственные API Джарвиса, в КОПИИ репо (JV_ROOT)."""
import os, sys
JV = os.environ["JV_ROOT"]; sys.path.insert(0, JV); os.chdir(JV)
from core import about_me, help as H
from core.contacts import contacts, Contact
from core.macros import macros, Command
from core.study import study, Lesson
for k, v in {"name": "Сардор", "address_as": "сэр", "city": "Ташкент", "languages": "русский, узбекский",
             "occupation": "студент, программист", "wake_time": "07:00", "sleep_time": "00:30",
             "music": "Macan, The Weeknd", "club": "Реал Мадрид", "news": "технологии, футбол"}.items():
    about_me.answer(k, v, sync_now=False)
b = contacts().book
for n, tg, al in [("Мама", "+998 90 123-45-67", ["мама", "мамочка"]), ("Азиз Каримов", "@aziz", ["Азиз", "брат"]),
                  ("Дилноза", "@dilnoza", ["Дилноза"])]:
    b.upsert(Contact(name=n, telegram=tg, aliases=al, read_aloud=(n == "Мама")))
m = macros()
m.upsert(Command(name="Режим стрима", phrases=["режим стрима"], steps=[
    {"do": "open_app", "value": "OBS"}, {"do": "wait", "value": "2"}, {"do": "media", "value": "play"},
    {"do": "say", "value": "Эфир готов, сэр"}]))
m.upsert(Command(name="Учёба", phrases=["режим учёбы"], steps=[
    {"do": "open_app", "value": "Notion"}, {"do": "volume", "value": "20"}, {"do": "say", "value": "Фокус на 25 минут"}]))
s = study()
for subj, wd, st, en, room, kind in [("Матанализ", 0, "08:30", "09:50", "305", "лекция"), ("Программирование", 0, "10:00", "11:20", "Лаб 2", "практика"),
        ("Физика", 1, "08:30", "09:50", "214", "лекция"), ("Английский", 2, "11:30", "12:50", "117", "практика"),
        ("Матанализ", 3, "10:00", "11:20", "305", "семинар"), ("Алгоритмы", 4, "08:30", "09:50", "410", "лекция")]:
    s.lessons.append(Lesson(subject=subj, weekday=wd, start=st, end=en, room=room, kind=kind))
s.save()
s.add_task("Задачи 1–12 по рядам", "Матанализ", "пятница")
s.add_task("Лабораторная №3", "Программирование", "2026-10-07")
H.mark_seen()
print("seeded", about_me.progress())
