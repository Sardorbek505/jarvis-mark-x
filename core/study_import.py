"""Расписание со скриншота: Gemini читает картинку — пары попадают в «Учёбу».

Как устроено:
  • картинка (файл или вставка из буфера) ужимается до разумного размера и
    уходит в Gemini с просьбой вернуть ТОЛЬКО JSON со списком пар;
  • ответ разбирается недоверчиво (normalize): день недели, время, тип пары,
    чётность — приводятся к тому, что понимает core/study.py; пустое и
    непонятное отбрасывается, а не выдумывается;
  • сохраняет окно «Учёба» после предпросмотра — заменить расписание или
    добавить к текущему (повторы не дублируются).
Окно — ui_study.py (кнопка «Из фото» и Ctrl+V).
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from dataclasses import dataclass, field

from core import study as S

logger = logging.getLogger(__name__)

MODEL = os.getenv("JARVIS_SCHEDULE_MODEL", "gemini-2.5-flash")
TIMEOUT_MS = 90_000            # большая таблица читается дольше обычного снимка экрана
MAX_SIDE = 2400

# Если на картинке только номера пар без времени — обычные звонки вуза (поправить можно в окне).
BELLS = [("08:30", "09:50"), ("10:00", "11:20"), ("11:30", "12:50"), ("13:30", "14:50"),
         ("15:00", "16:20"), ("16:30", "17:50"), ("18:00", "19:20"), ("19:30", "20:50")]

PROMPT = """Это скриншот или фото расписания занятий (вуз, колледж или школа).
Перепиши ВСЕ занятия из него. Верни ТОЛЬКО JSON без пояснений:
{"lessons": [{"subject": "...", "weekday": 0, "start": "08:30", "end": "09:50", "room": "...",
  "teacher": "...", "kind": "лекция", "weeks": "all", "pair": 1}], "note": "..."}

Правила:
- weekday: 0 — понедельник, 1 — вторник, … 5 — суббота, 6 — воскресенье.
- start/end — время как на картинке, формат ЧЧ:ММ. Если времени у пары нет, но есть номер
  пары — пиши номер в "pair", а start/end оставь пустыми. Если время пар указано отдельно
  (звонки, левая колонка) — подставь его каждой паре.
- subject — название предмета как написано (сокращения не расшифровывай, опечатки не исправляй).
- kind — одно из: "лекция", "практика", "семинар", "лабораторная" (лек/лк → лекция,
  пр/прак → практика, сем → семинар, лаб/лр → лабораторная); неясно — "лекция".
- weeks: "all" — каждую неделю; "odd" — только нечётные (числитель, верхняя, I неделя);
  "even" — только чётные (знаменатель, нижняя, II неделя). Клетка, разделённая на верх и
  низ, — это две пары: верх "odd", низ "even".
- room — аудитория/кабинет (только номер/название), teacher — преподаватель; нет — "".
- Пустые клетки, перемены, «окна» — не пиши. НИЧЕГО не выдумывай: не видно — не пиши.
- Если это вообще не расписание — {"lessons": [], "note": "не расписание"}.
- note — коротко, что было непонятно (или "")."""


@dataclass
class Result:
    lessons: list[S.Lesson] = field(default_factory=list)
    note: str = ""
    guessed_time: int = 0      # сколько пар получили время «по звонкам», а не с картинки


def prepare(image: bytes) -> bytes:
    """Любая картинка → JPEG не больше MAX_SIDE по длинной стороне (скриншоты телефона — высокие)."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(image)))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    im = im.convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


def _json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        d = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}|\[.*\]", text, re.S)            # JSON внутри лишнего текста
        if not m:
            return {}
        try:
            d = json.loads(m.group(0))
        except ValueError:
            return {}
    if isinstance(d, list):
        d = {"lessons": d}
    return d if isinstance(d, dict) else {}


def _weekday(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) or str(v).strip().isdigit():
        n = int(v)
        return n if 0 <= n <= 5 else None                     # воскресенье окно не показывает
    n = S.parse_weekday(str(v))
    return n if n is not None and n <= 5 else None


_KIND = [("лаб", "лабораторная"), ("лр", "лабораторная"), ("сем", "семинар"), ("пр", "практика"),
         ("лек", "лекция"), ("лк", "лекция")]


def _kind(v) -> str:
    t = str(v or "").lower().strip()
    if t in S.KINDS:
        return t
    return next((k for pre, k in _KIND if t.startswith(pre)), "лекция")


def _weeks(v) -> str:
    t = str(v or "").lower().replace("ё", "е")
    if t in ("odd", "even", "all"):
        return t
    if any(w in t for w in ("нечет", "числ", "верх", "odd")) or t.strip() in ("1", "i"):
        return "odd"
    if any(w in t for w in ("чет", "знам", "ниж", "even")) or t.strip() in ("2", "ii"):
        return "even"
    return "all"


_ROOM_PREFIX = re.compile(r"^(?:ауд(?:итория)?|каб(?:инет)?|room|aud)\.?\s*", re.I)


def _room(v) -> str:
    return _ROOM_PREFIX.sub("", " ".join(str(v or "").split()))[:30]      # «ауд. 204» → «204»


def normalize(data: dict) -> Result:
    """Ответ модели → пары для core/study.py. Непонятное отбрасывается."""
    res = Result(note=str(data.get("note") or "").strip()[:200])
    seen = set()
    for x in data.get("lessons") or []:
        if not isinstance(x, dict):
            continue
        subject = " ".join(str(x.get("subject") or "").split())[:80]
        wd = _weekday(x.get("weekday"))
        if not subject or wd is None:
            continue
        start, end = S.hhmm(str(x.get("start") or "")), S.hhmm(str(x.get("end") or ""))
        if not start:
            try:
                pair = int(x.get("pair"))
            except (TypeError, ValueError):
                continue
            if not 1 <= pair <= len(BELLS):
                continue
            start, end = BELLS[pair - 1]
            res.guessed_time += 1
        les = S.Lesson(subject=subject, weekday=wd, start=start, end=end,
                       room=_room(x.get("room")),
                       teacher=" ".join(str(x.get("teacher") or "").split())[:60],
                       kind=_kind(x.get("kind")), weeks=_weeks(x.get("weeks")))
        k = key(les)
        if k not in seen:
            seen.add(k)
            res.lessons.append(les)
    res.lessons.sort(key=lambda les: (les.weekday, les.start, les.weeks))
    return res


def key(les: S.Lesson) -> tuple:
    return les.weekday, les.start, les.weeks, les.subject.lower()


def _ask(jpeg: bytes) -> str:
    from google import genai
    from google.genai import types

    from actions import vision
    api_key = vision._get_api_key()
    if not api_key:
        raise RuntimeError("нет ключа Gemini — добавьте его в «Ключи»")
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    resp = client.models.generate_content(
        model=MODEL, contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json", temperature=0.1, max_output_tokens=12000,
            thinking_config=types.ThinkingConfig(thinking_budget=1024),     # таблицу — вдумчиво
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH))
    return resp.text or ""


def read(image: bytes, ask=_ask) -> Result:
    """Картинка → найденные пары. Ошибки сети/ключа — исключением (окно покажет словами)."""
    text = ask(prepare(image))
    res = normalize(_json(text))
    logger.info("Расписание с картинки: пар %d, время по звонкам %d, заметка: %s",
                len(res.lessons), res.guessed_time, res.note or "—")
    return res


def apply(st: S.Study, lessons: list[S.Lesson], replace: bool) -> int:
    """Записать в «Учёбу»: заменить всё или добавить без повторов. → сколько добавлено."""
    if replace:
        st.lessons = list(lessons)
        added = len(lessons)
    else:
        have = {key(x) for x in st.lessons}
        new = [x for x in lessons if key(x) not in have]
        st.lessons.extend(new)
        added = len(new)
    st.save()
    return added
