"""Curiosity engine — JARVIS actively gets to know the user over time.

Onboarding asks 4 things once. This keeps the relationship going: a curated bank
of get-to-know-you questions across every area of life ("like your mom knows
you"). The proactive loop poses ONE per day (daytime, never spammy); the answer
is saved into the durable dossier as a fact.

State (per user, in `meta`):
  curio_asked   — JSON list of asked question ids (no repeats)
  curio_pending — id of the question awaiting an answer (the user's next message)
  curio_day     — date we last posed a question (one per day)
  curio_off     — "1" if the user paused proactive questions
"""
import json
import logging

logger = logging.getLogger("jarvis-curiosity")

# Each: {"id", "q" (warm question), "fact" (prefix used when saving the answer)}.
# Ordered roughly from light → deep so the relationship builds naturally.
BANK = [
    # basics & identity
    {"id": "age",        "q": "Сколько тебе лет, если не секрет? 🙂", "fact": "Возраст"},
    {"id": "from",       "q": "Откуда ты родом — где вырос?", "fact": "Родом из"},
    {"id": "live_now",   "q": "А сейчас где живёшь?", "fact": "Сейчас живёт в"},
    {"id": "languages",  "q": "На каких языках говоришь?", "fact": "Языки"},
    # family
    {"id": "family",     "q": "Расскажи про семью — кто у тебя есть?", "fact": "Семья"},
    {"id": "parents",    "q": "Как зовут твоих родителей? Чем они занимаются?", "fact": "Родители"},
    {"id": "siblings",   "q": "Братья, сёстры есть? Расскажи про них.", "fact": "Братья/сёстры"},
    # work / study
    {"id": "work",       "q": "Чем зарабатываешь на жизнь или где учишься?", "fact": "Работа/учёба"},
    {"id": "dream_job",  "q": "Кем бы хотел стать в идеале?", "fact": "Профессия мечты"},
    {"id": "skills",     "q": "В чём ты реально хорош? Чем гордишься?", "fact": "Силён в"},
    # tastes
    {"id": "food",       "q": "Какая твоя любимая еда? 🍽", "fact": "Любимая еда"},
    {"id": "drink",      "q": "Что любишь пить — кофе, чай, что-то ещё?", "fact": "Любимый напиток"},
    {"id": "music",      "q": "Какую музыку слушаешь? Любимые исполнители?", "fact": "Музыка"},
    {"id": "movies",     "q": "Любимый фильм или сериал?", "fact": "Любимое кино"},
    {"id": "books",      "q": "Читаешь? Если да — что нравится?", "fact": "Книги"},
    {"id": "games",      "q": "Во что играешь — игры, спорт?", "fact": "Игры/спорт"},
    # routine & lifestyle
    {"id": "morning",    "q": "Ты жаворонок или сова? Как обычно проходит твоё утро?", "fact": "Режим дня"},
    {"id": "weekend",    "q": "Как любишь проводить выходные?", "fact": "Выходные"},
    {"id": "hobby",      "q": "Чем увлекаешься в свободное время?", "fact": "Хобби"},
    {"id": "habits_good","q": "Какую полезную привычку хочешь у себя закрепить?", "fact": "Хочет привычку"},
    # relationships & people
    {"id": "friends",    "q": "Есть близкий друг? Расскажи, кто это.", "fact": "Близкий друг"},
    {"id": "partner",    "q": "Как у тебя на личном фронте? 🙂", "fact": "Личная жизнь"},
    {"id": "people",     "q": "Кто для тебя самый важный человек?", "fact": "Важный человек"},
    # values & inner world
    {"id": "values",     "q": "Что для тебя важнее всего в жизни?", "fact": "Ценности"},
    {"id": "faith",      "q": "Вера, духовность что-то значат для тебя?", "fact": "Вера"},
    {"id": "proud",      "q": "Какой свой поступок вспоминаешь с гордостью?", "fact": "Гордится"},
    {"id": "regret",     "q": "О чём-нибудь жалеешь? (можешь не отвечать)", "fact": "Сожаление"},
    # dreams & goals
    {"id": "goal_year",  "q": "Какая у тебя главная цель на этот год?", "fact": "Цель на год"},
    {"id": "dream_big",  "q": "А большая мечта на всю жизнь — какая?", "fact": "Большая мечта"},
    {"id": "travel",     "q": "Куда мечтаешь съездить?", "fact": "Хочет посетить"},
    # memories & childhood
    {"id": "childhood",  "q": "Какое самое тёплое воспоминание из детства?", "fact": "Воспоминание детства"},
    {"id": "hometown",   "q": "Что любишь в своём родном месте?", "fact": "Любит в родном городе"},
    # health & emotion
    {"id": "health",     "q": "Как со здоровьем? Есть что-то, о чём мне стоит помнить?", "fact": "Здоровье"},
    {"id": "stress",     "q": "Что тебя сейчас больше всего напрягает или тревожит?", "fact": "Стресс/тревога"},
    {"id": "happy",      "q": "Что делает тебя по-настоящему счастливым?", "fact": "Делает счастливым"},
    {"id": "recharge",   "q": "Как ты восстанавливаешься, когда вымотан?", "fact": "Восстанавливается"},
    # fun / random
    {"id": "superpower", "q": "Если бы дали суперсилу — какую выбрал бы? 🦸", "fact": "Суперсила-мечта"},
    {"id": "pet",        "q": "Животные — есть питомец или хотел бы?", "fact": "Питомцы"},
    {"id": "fear",       "q": "Чего боишься больше всего?", "fact": "Страх"},
    {"id": "perfect_day","q": "Опиши свой идеальный день от и до.", "fact": "Идеальный день"},
]

_BY_ID = {q["id"]: q for q in BANK}


async def _asked(memory, uid: int) -> set:
    raw = await memory.get_meta(uid, "curio_asked")
    try:
        return set(json.loads(raw)) if raw else set()
    except Exception:
        return set()


async def next_question(memory, uid: int):
    asked = await _asked(memory, uid)
    for q in BANK:
        if q["id"] not in asked:
            return q
    return None


async def pose(memory, uid: int):
    """Pick the next unasked question, mark it asked, set it pending.
    Returns the question text, or None if the bank is exhausted."""
    q = await next_question(memory, uid)
    if not q:
        return None
    asked = await _asked(memory, uid)
    asked.add(q["id"])
    await memory.set_meta(uid, "curio_asked", json.dumps(sorted(asked)))
    await memory.set_meta(uid, "curio_pending", q["id"])
    return q["q"]


async def save_answer(memory, uid: int, answer: str) -> bool:
    """If a question is pending, store the user's message as the answer (a durable
    fact) and clear the pending state. Returns True if an answer was captured."""
    qid = await memory.get_meta(uid, "curio_pending")
    if not qid:
        return False
    await memory.set_meta(uid, "curio_pending", "")
    q = _BY_ID.get(qid)
    answer = (answer or "").strip()
    if q and answer:
        await memory.add_fact(uid, f"{q['fact']}: {answer}")
        logger.info(f"curiosity answer saved for {uid}: {q['id']}")
    return True


async def progress(memory, uid: int) -> tuple:
    """(asked_count, total) for /memstats and friends."""
    return len(await _asked(memory, uid)), len(BANK)
