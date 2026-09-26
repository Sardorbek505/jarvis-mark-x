"""«Обо мне»: что Джарвису нужно знать о владельце — и знакомство голосом.

Вопросы — ниже (QUESTIONS): у каждого своё место в памяти (memory/
memory_manager — та же память, что у Telegram-бота), зачем он нужен и как
его задать вслух. При первом запуске Джарвис предлагает познакомиться и
задаёт их по одному (инструмент about_me); любой можно пропустить. Всё
видно и правится в окне «Обо мне» (ui_about.py) — вместе с тем, что
Джарвис запомнил сам из разговоров.

Некоторые ответы нужны и вне памяти: город — погоде и времени, имя —
звонкам, подъём — утреннему брифингу (sync()).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_AUTO_OFFERS = 3            # столько раз (не чаще раза в день) Джарвис сам предложит знакомство


@dataclass(frozen=True)
class Q:
    key: str
    category: str
    label: str
    ask: str                    # как спросить вслух
    why: str                    # зачем Джарвису
    group: str
    placeholder: str = ""
    sensitive: bool = False


GROUPS = ["Кто вы", "Распорядок дня", "Что вам нравится", "Люди и цели", "Здоровье"]

QUESTIONS: list[Q] = [
    Q("name", "identity", "Имя", "Как вас зовут?", "Чтобы обращаться лично", "Кто вы", "Сардор"),
    Q("address_as", "identity", "Как к вам обращаться", "Как мне к вам обращаться — «сэр», по имени или иначе?",
      "Так Джарвис и будет говорить", "Кто вы", "сэр"),
    Q("city", "identity", "Город", "В каком городе вы живёте?", "Погода, время, «где я»", "Кто вы", "Ташкент"),
    Q("birthday", "dates", "День рождения", "Когда у вас день рождения?", "Поздравит — и не забудет", "Кто вы",
      "12 марта"),
    Q("languages", "identity", "Языки", "На каких языках вы говорите?", "Поймёт и ответит на вашем", "Кто вы",
      "русский, узбекский"),
    Q("occupation", "work", "Чем занимаетесь", "Чем вы занимаетесь — учитесь, работаете?",
      "Советы и напоминания по делу", "Кто вы", "студент, программист"),
    Q("wake_time", "habits", "Подъём", "Во сколько вы обычно встаёте?", "Утренний брифинг — к подъёму",
      "Распорядок дня", "7:00"),
    Q("sleep_time", "habits", "Отбой", "А ложитесь во сколько?", "Напомнит, что пора спать", "Распорядок дня",
      "23:30"),
    Q("work_hours", "habits", "Работа / учёба", "Когда вы обычно работаете или учитесь?",
      "Не отвлекать в это время", "Распорядок дня", "будни 9–18"),
    Q("music", "preferences", "Музыка", "Какую музыку любите?", "«Включи что-нибудь» — угадает", "Что вам нравится",
      "рэп, lo-fi, Macan"),
    Q("news", "preferences", "Новости", "Какие новости вам интересны?", "Утренний брифинг — только нужное",
      "Что вам нравится", "технологии, футбол"),
    Q("talk_style", "preferences", "Как говорить", "Как мне с вами говорить — коротко или подробно, с юмором или "
      "по делу?", "Тон и длина ответов", "Что вам нравится", "коротко, с юмором"),
    Q("family", "relationships", "Близкие", "Кто ваши близкие — как их зовут?",
      "Поймёт «напиши маме»; контакты — в окне «Контакты»", "Люди и цели", "мама Гульнора, брат Азиз"),
    Q("goals", "wishes", "Цели", "Над чем сейчас работаете, какие цели?", "Поддержит и напомнит",
      "Люди и цели", "выучить английский, запустить Джарвиса"),
    Q("health", "health", "Здоровье", "Есть что-то о здоровье, что мне стоит знать — аллергии, режим? "
      "Можно пропустить.", "Только если сами хотите", "Здоровье", "необязательно", sensitive=True),
]
BY_KEY = {q.key: q for q in QUESTIONS}


def _mem():
    from memory import memory_manager
    return memory_manager


def _value(mem: dict, q: Q) -> str:
    v = (mem.get(q.category) or {}).get(q.key)
    return str(v.get("value", "") if isinstance(v, dict) else (v or "")).strip()


def answers() -> dict[str, str]:
    mem = _mem().load_memory()
    return {q.key: _value(mem, q) for q in QUESTIONS}


def other_facts() -> list[tuple[str, str, str]]:
    """Что Джарвис запомнил сам (не из анкеты): [(категория, ключ, значение)]."""
    own = {(q.category, q.key) for q in QUESTIONS}
    return [f for f in _mem().all_facts() if (f[0], f[1]) not in own]


def category_title(cat: str) -> str:
    return getattr(_mem(), "_CAT_RU", {}).get(cat, cat)


def answer(key: str, value: str) -> str:
    q = BY_KEY.get(key)
    value = " ".join(str(value or "").split())
    if not q:
        return f"Нет вопроса «{key}»."
    if not value:
        return forget(key)
    _mem().update_memory({q.category: {q.key: value}})
    st = state()
    if key in st["skipped"]:
        st["skipped"].remove(key)
        _save_state(st)
    sync(key, value)
    logger.info("Обо мне: %s сохранено", key)
    return "Запомнил."


def forget(key: str) -> str:
    q = BY_KEY.get(key)
    if q:
        _mem().forget(q.category, q.key)
    return "Забыл."


def skip(key: str) -> str:
    st = state()
    if key in BY_KEY and key not in st["skipped"]:
        st["skipped"].append(key)
        _save_state(st)
    return "Пропустили."


def sync(key: str, value: str) -> None:
    """Ответы, которые нужны не только памяти."""
    try:
        from core.paths import save_api_keys
        if key == "city":
            save_api_keys({"home_city": value})
            try:
                from core import location
                location._cache.update(at=0.0, place=None)
            except Exception:
                pass
        elif key in ("address_as", "name"):
            cur = answers()
            addr = cur.get("address_as") or ""
            save_api_keys({"user_name": addr if addr and addr.lower() != "по имени" else cur.get("name") or addr})
    except Exception as exc:
        logger.debug("Обо мне → ключи: %s", exc)


def next_question() -> Q | None:
    have, st = answers(), state()
    for q in QUESTIONS:
        if not have[q.key] and q.key not in st["skipped"]:
            return q
    return None


def progress() -> tuple[int, int]:
    have = answers()
    return sum(1 for v in have.values() if v), len(QUESTIONS)


# ── состояние знакомства ─────────────────────────────────────────────────────

def _state_path() -> Path:
    env = os.getenv("JARVIS_ABOUT_STATE", "").strip()
    if env:
        return Path(env)
    from core.paths import get_data_root
    return Path(get_data_root()) / "about_me_state.json"


def state() -> dict:
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        st = {}
    st.setdefault("skipped", [])
    st.setdefault("offers", 0)
    st.setdefault("last_offer", "")
    st.setdefault("intro", "")               # "" | "done" | "declined"
    return st


def _save_state(st: dict) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        logger.debug("Обо мне, состояние: %s", exc)


def should_offer(now: datetime | None = None) -> bool:
    """Предложить знакомство сейчас? Только пока мало известно, не чаще раза
    в день, не больше MAX_AUTO_OFFERS раз и никогда после «не надо»."""
    now = now or datetime.now()
    st = state()
    if st["intro"] or st["offers"] >= MAX_AUTO_OFFERS or st["last_offer"] == now.date().isoformat():
        return False
    known, total = progress()
    return known < total // 2


def mark_offered(now: datetime | None = None) -> None:
    st = state()
    st["offers"] += 1
    st["last_offer"] = (now or datetime.now()).date().isoformat()
    _save_state(st)


def intro_instruction(restart: bool = False) -> str:
    known, total = progress()
    return (
        "[СИСТЕМА: " + ("пользователь хочет познакомиться заново. " if restart else
                        "вы ещё мало знакомы (знаешь " f"{known} из {total}). ")
        + "Коротко, одной-двумя фразами, скажи, что хочешь узнать его получше, чтобы помогать лично, — это "
        "пара минут, любой вопрос можно пропустить. Спроси, удобно ли сейчас.\n"
        "Если да — вызови about_me action=\"next\" и задай вопрос СВОИМИ словами, тепло, по одному за раз. "
        "Ответ сохраняй about_me action=\"answer\" key=… value=… (кратко, как он сказал) — в ответе придёт "
        "следующий вопрос. «Пропусти», «не скажу» — action=\"skip\". «Хватит», «потом» — action=\"later\" и "
        "скажи, что продолжить можно в окне «Обо мне» или фразой «давай познакомимся».\n"
        "Если сейчас неудобно — action=\"later\". Не допрашивай: короткая живая реакция на ответ — и дальше.]"
    )


def about_tool(p: dict) -> str:
    p = p or {}
    a = str(p.get("action") or "status").lower()

    def ask_next(prefix: str = "") -> str:
        q = next_question()
        known, total = progress()
        if not q:
            st = state()
            st["intro"] = "done"
            _save_state(st)
            return (prefix + f"Вопросы закончились (знаю {known} из {total}). Поблагодари коротко и скажи, "
                    "что всё видно и правится в окне «Обо мне».")
        return (prefix + f"Следующий вопрос — key=\"{q.key}\": спроси своими словами «{q.ask}» "
                f"(зачем: {q.why.lower()}). Осталось {total - known}.")
    if a == "next":
        return ask_next()
    if a == "answer":
        key = str(p.get("key") or "")
        res = answer(key, str(p.get("value") or ""))
        return ask_next(res + " ") if key in BY_KEY else res
    if a == "skip":
        return ask_next(skip(str(p.get("key") or "")) + " ")
    if a == "later":
        st = state()
        if st["intro"] != "done":
            st["intro"] = "declined"
        _save_state(st)
        return "Хорошо. Больше сам не предложу — продолжить можно фразой «давай познакомимся» или в окне «Обо мне»."
    if a == "restart":
        st = state()
        st["intro"], st["skipped"] = "", []
        _save_state(st)
        return ask_next("Начинаем. ")
    if a == "status":
        have = answers()
        known, total = progress()
        rows = [f"{BY_KEY[k].label}: {v}" for k, v in have.items() if v]
        return f"Знаю {known} из {total}. " + ("; ".join(rows) if rows else "Пока почти ничего.")
    return f"Не понял действие «{a}»."
