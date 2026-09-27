"""Подсказки: что умеет Джарвис, как с ним говорить и что настроить.

Для нового пользователя — окно «Добро пожаловать» (ui_welcome.py): при
первом запуске открывается само, потом — из трея («Что умеет Джарвис») и
голосом («что ты умеешь»). Три части:
  • Первые шаги — чек-лист настройки с живыми галочками (ключ Gemini,
    знакомство, слово «Джарвис», голос, Telegram, учёба, Spotify);
  • Как говорить — имя, капсула, цвета шара, горячие клавиши, чат;
  • Что умеет — разделы с примерами фраз; нажал — Джарвис выполнит.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class Step:
    id: str
    title: str
    why: str
    action: str                 # что открыть: keys | about | wake | voice | contacts | study | spotify
    check: Callable[[], tuple[bool, str]]
    optional: bool = False


def _gemini() -> tuple[bool, str]:
    from core.keys import load_values
    ok = bool(load_values().get("gemini_api_key"))
    return ok, "ключ вписан" if ok else "без него Джарвис не работает"


def _about() -> tuple[bool, str]:
    from core import about_me
    known, total = about_me.progress()
    return known >= total // 2, f"знает {known} из {total}"


def _wake() -> tuple[bool, str]:
    from core.wake_vosk import load_aliases
    ok = bool(load_aliases())
    return ok, "слышит ваше «Джарвис» прямо на ПК" if ok else "пока имя ищет облако — медленнее"


def _voice() -> tuple[bool, str]:
    from core.voice_id import voice_id
    ok = voice_id().enrolled()
    return ok, "опасное — только по вашему «да»" if ok else "не записан"


def _telegram() -> tuple[bool, str]:
    from core.contacts import contacts
    c = contacts()
    linked, n = c.me.linked(), len(c.book.contacts)
    return linked and n > 0, (f"подключён, людей: {n}" if linked else "не подключён")


def _study() -> tuple[bool, str]:
    from core.study import study
    s = study()
    return bool(s.lessons), f"пар в расписании: {len(s.lessons)}" if s.lessons else "расписания нет"


def _spotify() -> tuple[bool, str]:
    from core.keys import load_values
    v = load_values()
    ok = bool(v.get("spotify_client_id") and v.get("spotify_client_secret"))
    return ok, "ключи есть" if ok else "не подключён"


STEPS: list[Step] = [
    Step("gemini", "Ключ Gemini", "Мозг Джарвиса — бесплатный ключ Google AI Studio.", "keys", _gemini),
    Step("about", "Познакомиться", "Имя, город, подъём, музыка — чтобы помогать лично.", "about", _about),
    Step("wake", "Научить слову «Джарвис»", "8 раз сказать имя — будет откликаться быстрее и точнее.", "wake",
         _wake, optional=True),
    Step("voice", "Записать ваш голос", "Сообщения людям и выключение ПК — только по вашему «да».", "voice",
         _voice, optional=True),
    Step("telegram", "Подключить Telegram", "«Напиши маме…», «что мне написали?», звонки.", "contacts",
         _telegram, optional=True),
    Step("study", "Расписание пар", "Напомнит о паре, скажет, что завтра, следит за дедлайнами.", "study",
         _study, optional=True),
    Step("spotify", "Spotify", "Музыка голосом: «включи Macan», «следующий трек».", "keys", _spotify,
         optional=True),
]


def checklist() -> list[tuple[Step, bool, str]]:
    out = []
    for s in STEPS:
        try:
            done, detail = s.check()
        except Exception as exc:
            logger.debug("Подсказки, %s: %s", s.id, exc)
            done, detail = False, ""
        out.append((s, done, detail))
    return out


HOW_TO = [
    ("mic", "Позовите по имени", "«Джарвис, …» — и сразу просьба. 30 секунд после ответа можно говорить без имени."),
    ("expand", "Капсула сверху", "Когда окно свёрнуто, вверху экрана — капсула: «Слушаю», ответ, что играет. "
                                 "Нажмите на неё — вернётся окно."),
    ("spark", "Цвет шара", "Бирюзовый — ждёт имени, зелёный — слушает, жёлтый — думает, оранжевый — говорит, "
                           "красный — нет связи, серый — микрофон выключен."),
    ("keyboard", "Горячие клавиши", "F8 — позвать без слов, Ctrl+Shift+M — выключить/включить микрофон. "
                                    "Работают и в играх."),
    ("text", "Можно писать", "Напишите в чат окна — Джарвис ответит так же. Набранное «да» — всегда ваше."),
    ("lock", "Перед опасным переспросит", "Написать людям, позвонить, выключить ПК, удалить файлы — "
                                          "только после вашего «да»."),
]

# Разделы «Что умеет»: (заголовок, иконка, фразы).
ABILITIES = [
    ("Музыка", "note", ["включи Macan", "следующий трек", "поставь на паузу", "громкость музыки 40",
                        "что сейчас играет"]),
    ("Фильмы и видео", "film", ["поставь фильм Интерстеллар", "включи на ютубе как собрать ПК",
                                "на весь экран", "перемотай на 10 минут", "громкость фильма 70"]),
    ("Громкость и экран", "volume", ["громче", "громкость 50", "яркость 30", "сверни все окна",
                                     "смотри на экран"]),
    ("Окна и вкладки", "app", ["открой Telegram", "новая вкладка", "закрой вкладку", "вкладка три",
                               "переключи окно"]),
    ("Время", "timer", ["который час", "таймер на 10 минут", "разбуди в 7 по будням", "запусти секундомер",
                        "сколько осталось на таймере"]),
    ("Учёба", "book", ["какие пары завтра", "какая следующая пара", "задали задачи по матану до пятницы",
                       "какие дедлайны", "давай позанимаемся 25 минут"]),
    ("Люди", "person", ["напиши маме, что задержусь", "позвони брату и скажи, что ужин готов",
                        "что мне написали", "добавь контакт Азиз — @aziz"]),
    ("Свои команды", "bolt", ["создай команду режим стрима: открой OBS и включи музыку", "какие у меня команды",
                              "открой редактор команд"]),
    ("Память и заметки", "spark", ["запомни, что я люблю кофе без сахара", "что ты обо мне знаешь",
                                   "запиши заметку купить хлеб", "давай познакомимся"]),
    ("Погода и новости", "globe", ["какая погода завтра", "доброе утро", "что нового в мире технологий"]),
]


# ── показывали ли уже ──────────────────────────────────────────────────────

def _state_path() -> Path:
    env = os.getenv("JARVIS_WELCOME_STATE", "").strip()
    if env:
        return Path(env)
    from core.paths import get_data_root
    return Path(get_data_root()) / "welcome_state.json"


def seen() -> bool:
    try:
        return bool(json.loads(_state_path().read_text(encoding="utf-8")).get("seen"))
    except Exception:
        return False


def mark_seen() -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"seen": True}), encoding="utf-8")
    except OSError as exc:
        logger.debug("Подсказки, состояние: %s", exc)


def abilities_text() -> str:
    """Для «что ты умеешь» голосом — коротко, с примерами."""
    parts = [f"{title}: «{phrases[0]}», «{phrases[1]}»" for title, _i, phrases in ABILITIES]
    return ("Разделы и примеры — " + "; ".join(parts)
            + ". Всё с примерами — в окне «Что умеет Джарвис» (трей).")
