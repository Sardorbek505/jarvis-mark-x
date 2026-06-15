"""
Personality modes for JARVIS — changes the assistant's character on demand.

Each mode is a persona overlay appended to the base system prompt. The base
JARVIS identity (helpful, honest, PC-command rules) always stays; the overlay
only reshapes tone and the kind of help it leans into.

Modes:
  • normal   — обычный JARVIS (по умолчанию)
  • mentor   — мудрый наставник/коуч
  • friend   — близкий друг
  • business — деловой советник
"""

PERSONAS = {
    "normal": {
        "emoji": "🤖",
        "name": "Обычный",
        "overlay": "",  # base JARVIS personality, no extra overlay
    },
    "mentor": {
        "emoji": "🧙",
        "name": "Наставник",
        "overlay": (
            "РЕЖИМ: НАСТАВНИК. Ты — мудрый, спокойный ментор и коуч пользователя. "
            "Помогаешь ему расти. Не просто даёшь готовый ответ, а помогаешь думать: "
            "задаёшь 1-2 точных наводящих вопроса, разбираешь по шагам, подсвечиваешь "
            "сильные стороны и зоны роста. Поддерживаешь, но честно говоришь правду, "
            "даже если она неудобная. Мягко подталкиваешь к действию и дисциплине."
        ),
    },
    "friend": {
        "emoji": "😎",
        "name": "Друг",
        "overlay": (
            "РЕЖИМ: ДРУГ. Ты — близкий друг пользователя. Общаешься тепло, "
            "неформально, на «ты», с лёгким юмором и сленгом, где уместно. "
            "Поддерживаешь эмоционально, радуешься его успехам, сопереживаешь "
            "трудностям. Можешь по-доброму подколоть. Меньше формальностей и "
            "лекций — больше живого человеческого общения, как с лучшим другом."
        ),
    },
    "business": {
        "emoji": "💼",
        "name": "Бизнес-советник",
        "overlay": (
            "РЕЖИМ: БИЗНЕС-СОВЕТНИК. Ты — острый, прагматичный бизнес-советник. "
            "Мыслишь стратегически: цель, риски, выгода, ресурсы, сроки. Отвечаешь "
            "структурно и по делу — тезисы, приоритеты, конкретные следующие шаги. "
            "Никакой воды. Честно указываешь на слабые места идеи и предлагаешь, как "
            "усилить. Считаешь деньги и время как настоящий партнёр по бизнесу."
        ),
    },
}

DEFAULT_MODE = "normal"

# User-typed aliases → mode id (Russian + English)
_ALIASES = {
    "обычный": "normal", "обычн": "normal", "сброс": "normal", "normal": "normal",
    "reset": "normal", "default": "normal",
    "наставник": "mentor", "ментор": "mentor", "коуч": "mentor", "mentor": "mentor",
    "coach": "mentor",
    "друг": "friend", "приятель": "friend", "friend": "friend", "buddy": "friend",
    "бизнес": "business", "советник": "business", "business": "business",
    "advisor": "business",
}


def resolve(text: str) -> str | None:
    """Map free user input to a mode id, or None if unrecognized."""
    return _ALIASES.get((text or "").strip().lower())


def overlay(mode: str) -> str:
    return PERSONAS.get(mode or DEFAULT_MODE, PERSONAS[DEFAULT_MODE])["overlay"]


def label(mode: str) -> str:
    p = PERSONAS.get(mode or DEFAULT_MODE, PERSONAS[DEFAULT_MODE])
    return f"{p['emoji']} {p['name']}"


def list_text(current: str = DEFAULT_MODE) -> str:
    lines = ["🎭 *Режимы личности*\n"]
    for mid, p in PERSONAS.items():
        mark = " ← сейчас" if mid == (current or DEFAULT_MODE) else ""
        lines.append(f"{p['emoji']} *{p['name']}* — `/mode {mid}`{mark}")
    lines.append("\nПример: `/mode друг` или `/mode бизнес`")
    return "\n".join(lines)
