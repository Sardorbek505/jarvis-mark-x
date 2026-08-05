"""
First-meeting onboarding — JARVIS gets to know a new user by asking a few
basic questions and saving the answers into the long-term dossier.

Lightweight in-RAM state machine. A user is considered onboarded once the
'onboarded' meta flag is set (persisted), so it runs only on first contact.
Users can type «пропустить» to skip a question, or «стоп» to abort.
"""
import logging

logger = logging.getLogger("jarvis-onboarding")

# (key, question). Each answer is saved by _save() below.
_STEPS = [
    ("name", "Давай знакомиться 🙌 Как тебя зовут?"),
    ("city", "Приятно! А из какого ты города?"),
    ("about", "Чем занимаешься — учёба, работа, своё дело? Пары слов хватит."),
    ("goals", "И последнее: какая у тебя сейчас главная цель или мечта?"),
]

_SKIP = {"пропустить", "скип", "skip", "-", "позже", "потом"}
_STOP = {"стоп", "stop", "отмена", "хватит"}

# uid -> {"step": int, "answers": {}}
_active: dict = {}


def is_active(uid: int) -> bool:
    return uid in _active


def start(uid: int) -> str:
    _active[uid] = {"step": 0, "answers": {}}
    return _STEPS[0][1]


async def already_onboarded(memory, uid: int) -> bool:
    try:
        return (await memory.get_meta(uid, "onboarded")) == "1"
    except Exception as exc:
        # Новичок отличается от знакомого тем, что get_meta вернёт None — БЕЗ
        # исключения. Сюда попадаем только когда память сломалась, и тогда
        # честнее считать человека знакомым: иначе бот заново спрашивает имя,
        # и со стороны это выглядит как «он меня забыл».
        logger.warning("Не смог проверить онбординг для %s (%s) — считаю знакомым", uid, exc)
        return True


async def _save(memory, uid: int, key: str, value: str):
    if key == "name":
        await memory.set_profile_field(uid, "name", value)
    elif key == "city":
        await memory.add_fact(uid, f"Живёт в городе: {value}")
    elif key == "about":
        await memory.set_profile_field(uid, "about", value)
    elif key == "goals":
        await memory.set_profile_field(uid, "goals", value)


async def handle(memory, uid: int, text: str) -> str:
    """Process one onboarding answer; returns the next message to send."""
    state = _active.get(uid)
    if not state:
        return ""
    answer = (text or "").strip()
    low = answer.lower()

    if low in _STOP:
        _active.pop(uid, None)
        await memory.set_meta(uid, "onboarded", "1")
        return "Окей, не буду расспрашивать 🙂 Всё равно буду запоминать тебя по ходу общения."

    step = state["step"]
    key, _q = _STEPS[step]
    if low not in _SKIP and answer:
        await _save(memory, uid, key, answer)
        state["answers"][key] = answer

    state["step"] += 1
    if state["step"] < len(_STEPS):
        return _STEPS[state["step"]][1]

    # Finished
    _active.pop(uid, None)
    await memory.set_meta(uid, "onboarded", "1")
    name = state["answers"].get("name", "")
    hello = f"Рад знакомству, {name}! " if name else "Готово! "
    return (
        f"{hello}Я всё запомнил 🧠 Дальше буду узнавать тебя лучше сам.\n\n"
        "Можешь сменить мой характер: `/mode друг`, `/mode ментор`, `/mode бизнес`.\n"
        "Посмотреть, что я о тебе знаю: `/profile`.\n"
        "А ещё я сам напишу тебе утром и вечером ☀️🌙"
    )
