"""История звонков Джарвиса — с расшифровкой, кто что сказал.

Раньше после звонка оставался только итог («Поговорили 2 мин»), а на «что
он сказал?» Джарвис отвечал, что расшифровки нет. Теперь каждый звонок
(вам или человеку из «Контактов») сохраняется: когда, кому, зачем, итог и
реплики обеих сторон. Живая расшифровка Gemini приходит кусочками по
несколько слов — подряд идущие кусочки одного говорящего склеиваются в
реплику.

Где видно: панель «Диалог» на ПК сразу после звонка; голосом — «что он
сказал?», «покажи расшифровку звонка» (phone_call action=transcript);
в Mini App — вкладка «Сводка» → «Звонки».
Хранится в calls.json в папке данных (последние MAX_CALLS).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_CALLS = 40
MAX_LINES = 200


def merge(transcript: list[str], other: str) -> list[dict]:
    """[«Вы: при», «Вы: вет», «Джарвис: Здравствуйте»] → реплики.
    «Вы» в расшифровке звонка — тот, кто в трубке (other)."""
    lines: list[dict] = []
    for raw in transcript or []:
        who, sep, text = raw.partition(":")
        if not sep:
            continue
        who = other if who.strip() == "Вы" else "Джарвис"
        if not text.strip():
            continue
        if lines and lines[-1]["who"] == who:
            prev = lines[-1]["text"]
            # Кусочек без пробела в начале — продолжение слова («при» + «вет»).
            glue = not text[:1].isspace() and prev[-1:].isalpha() and text[:1].isalpha()
            if glue or text.strip()[:1] in ",.!?:;…":            # знак препинания — без пробела перед ним
                lines[-1]["text"] = prev + (text if glue else text.strip())
            else:
                lines[-1]["text"] = f"{prev} {text.strip()}"
        else:
            lines.append({"who": who, "text": text.strip()})
    for ln in lines:
        ln["text"] = re.sub(r"\s+", " ", ln["text"]).strip()
    return [ln for ln in lines if ln["text"]][:MAX_LINES]


class CallLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.calls: list[dict] = []
        self.on_added = None                     # main.py: показать расшифровку в «Диалоге»
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            # Старый calls.json мог содержать расписание звонков (список) — это не история.
            calls = data.get("calls", []) if isinstance(data, dict) else []
            self.calls = [c for c in calls if isinstance(c, dict)]
        except FileNotFoundError:
            self.calls = []
        except Exception as exc:
            logger.warning("История звонков не прочиталась: %s", exc)
            self.calls = []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "calls": self.calls[-MAX_CALLS:]}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def add(self, who: str, topic: str, result: str, transcript: list[str], started: float,
            ended: float | None = None) -> dict:
        """who — кому звонил («вам» или имя контакта)."""
        ended = ended or time.time()
        entry = {"id": uuid.uuid4().hex[:8], "when": datetime.fromtimestamp(started).isoformat(timespec="seconds"),
                 "who": who, "topic": topic, "result": result, "sec": max(0, int(ended - started)),
                 "lines": merge(transcript, "Вы" if who == "вам" else who)}
        with self._lock:
            self.calls.append(entry)
            self.calls = self.calls[-MAX_CALLS:]
            try:
                self._save()
            except OSError as exc:
                logger.warning("История звонков не сохранилась: %s", exc)
        if self.on_added:
            try:
                self.on_added(entry)
            except Exception as exc:
                logger.debug("Показ расшифровки: %s", exc)
        return entry

    def recent(self, n: int = 10) -> list[dict]:
        return list(reversed(self.calls[-n:]))

    def find(self, name: str = "") -> dict | None:
        """Последний звонок; name — последний звонок этому человеку."""
        n = (name or "").strip().lower()
        for c in reversed(self.calls):
            if not n or n in c["who"].lower() or c["who"].lower()[:4] in n:
                return c
        return None


def title(c: dict) -> str:
    when = c["when"].replace("T", " ")[:16]
    mins = max(1, round(c.get("sec", 0) / 60))
    return f"Звонок {'вам' if c['who'] == 'вам' else c['who']} — {when}, {mins} мин"


def as_text(c: dict, limit: int = 3000) -> str:
    rows = [f"{ln['who']}: {ln['text']}" for ln in c["lines"]]
    body = "\n".join(rows) if rows else "(расшифровки нет — ничего не было сказано или не расслышано)"
    out = f"{title(c)}. {c.get('result', '')}\n{body}"
    return out if len(out) <= limit else out[:limit - 1] + "…"


_log: CallLog | None = None


def call_log() -> CallLog:
    global _log
    if _log is None:
        env = os.getenv("JARVIS_CALL_LOG", "").strip()
        if env:
            path = Path(env)
        else:
            from core.paths import get_data_root
            path = Path(get_data_root()) / "calls.json"
        _log = CallLog(path)
    return _log


def transcript_tool(p: dict) -> str:
    """phone_call action=transcript: «что он сказал», «покажи расшифровку», «с кем говорил»."""
    p = p or {}
    log = call_log()
    if str(p.get("which") or "").lower() == "list":
        calls = log.recent(8)
        if not calls:
            return "Звонков ещё не было."
        return "Последние звонки:\n" + "\n".join(f"— {title(c)}: {c.get('result', '')}" for c in calls)
    c = log.find(str(p.get("name") or ""))
    if not c:
        return "Такого звонка в истории нет." if p.get("name") else "Звонков ещё не было."
    return ("[Расшифровка звонка — перескажи коротко своими словами, что сказали; дословно — только если "
            "попросят. Полный текст пользователь видит в панели «Диалог».]\n" + as_text(c))
