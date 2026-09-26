"""Память разговора: журнал реплик, итоги разговоров и сбор фактов.

Зачем. Раньше разговор нигде не хранился: не удалось возобновить сессию
Gemini или программу перезапустили — Джарвис начинал с чистого листа. А в
долгосрочную память факт попадал, только если голосовая модель сама
вызовет save_to_memory, — она делает это редко.

Теперь:
  • dialog.jsonl — каждая реплика к Джарвису и его ответ. В новую сессию
    (без возобновления) уходит хвост разговора — он продолжается с места.
  • Collector — после разговора (тишина IDLE_SEC или BATCH реплик) один
    фоновый вызов текстовой Gemini: что нового узнали о человеке, что
    устарело, и итог разговора в одну-две фразы → episodes.jsonl.
  • В каждую сессию — факты и последние итоги: «о чём говорили раньше».
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from core.paths import get_data_root

logger = logging.getLogger(__name__)

_DIR = get_data_root() / "memory"
DIALOG_FILE = _DIR / "dialog.jsonl"
EPISODES_FILE = _DIR / "episodes.jsonl"
STATE_FILE = _DIR / "collector.json"

KEEP_LINES = 3000            # журнал: столько последних реплик (~ месяцы разговоров)
RECENT_TURNS = 24            # в новую сессию — столько последних реплик…
RECENT_CHARS = 3500          # …но не больше стольких знаков
RECENT_MAX_AGE = 12 * 3600   # и не старше 12 часов: дальше хватает итогов
EPISODES_IN_PROMPT = 10
EPISODES_CHARS = 1800
IDLE_SEC = 180
BATCH = 10
MODEL = os.getenv("JARVIS_MEMORY_MODEL", "gemini-2.5-flash")

_lock = threading.Lock()


# ── файлы ─────────────────────────────────────────────────────────────────────

def _read_jsonl(path: Path) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            out = []
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue                      # одна битая строка не губит журнал
            return out
    except OSError:
        return []


def _append_jsonl(path: Path, item: dict, keep: int | None = None):
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        if keep and path.stat().st_size > keep * 400:           # изредка подрезаем
            rows = _read_jsonl(path)
            if len(rows) > keep:
                tmp = path.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    for r in rows[-keep:]:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                os.replace(tmp, path)


# ── журнал реплик ─────────────────────────────────────────────────────────────

def log_turn(role: str, text: str, ts: float | None = None):
    """role: "user" | "jarvis". Системные указания ([СИСТЕМА: …]) не пишем."""
    text = (text or "").strip()
    if not text or text.startswith("["):
        return
    ts = ts or time.time()
    _append_jsonl(DIALOG_FILE, {"ts": ts, "role": role, "text": text[:2000]}, KEEP_LINES)
    _share(lambda sh: sh.queue_turn(role, text[:2000], ts))


def turns_since(ts: float) -> list[dict]:
    return [r for r in _read_jsonl(DIALOG_FILE) if r.get("ts", 0) > ts]


def _when(ts: float, now: float) -> str:
    d, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    if d.date() == n.date():
        return d.strftime("%H:%M")
    return d.strftime("%d.%m %H:%M")


def _line(r: dict) -> str:
    return f"{'Вы' if r['role'] == 'user' else 'Джарвис'}: {r['text']}"


def format_recent(now: float | None = None) -> str:
    """Хвост разговора для новой сессии: Джарвис продолжает с места."""
    now = now or time.time()
    rows = [r for r in _read_jsonl(DIALOG_FILE)[-RECENT_TURNS:] if now - r.get("ts", 0) <= RECENT_MAX_AGE]
    if not rows:
        return ""
    lines, size = [], 0
    for r in reversed(rows):
        line = f"({_when(r['ts'], now)}) {_line(r)}"
        if size + len(line) > RECENT_CHARS:
            break
        lines.append(line)
        size += len(line)
    lines.reverse()
    return ("[НЕДАВНИЙ РАЗГОВОР — это было до переподключения. Продолжай с этого места, "
            "не здоровайся заново и не пересказывай]\n" + "\n".join(lines) + "\n")


def search_dialog(query: str, limit: int = 8) -> list[str]:
    from rapidfuzz import fuzz
    q = (query or "").strip().lower()
    if not q:
        return []
    now = time.time()
    rows = _read_jsonl(DIALOG_FILE)
    scored = [(fuzz.partial_ratio(q, r["text"].lower()), r) for r in rows if len(r.get("text", "")) > 3]
    best = sorted((x for x in scored if x[0] >= 75), key=lambda x: (-x[0], -x[1]["ts"]))[:limit]
    return [f"({_when(r['ts'], now)}) {_line(r)}" for _, r in sorted(best, key=lambda x: x[1]["ts"])]


# ── итоги разговоров ──────────────────────────────────────────────────────────

def add_episode(summary: str, ts: float | None = None):
    summary = (summary or "").strip()
    if summary:
        ts = ts or time.time()
        _append_jsonl(EPISODES_FILE, {"ts": ts, "summary": summary[:600]}, 2000)
        _share(lambda sh: sh.queue_episode(summary[:600], ts))


def _share(fn):
    try:
        from memory import shared
        fn(shared)
    except Exception as exc:
        logger.debug("Общая память: %s", exc)


def format_episodes(now: float | None = None) -> str:
    now = now or time.time()
    rows = _read_jsonl(EPISODES_FILE)[-EPISODES_IN_PROMPT:]
    if not rows:
        return ""
    lines, size = [], 0
    for r in reversed(rows):
        line = f"  {_when(r['ts'], now)} — {r['summary']}"
        if size + len(line) > EPISODES_CHARS:
            break
        lines.append(line)
        size += len(line)
    lines.reverse()
    return "[О ЧЁМ ГОВОРИЛИ РАНЬШЕ]\n" + "\n".join(lines) + "\n"


def search_episodes(query: str, limit: int = 6) -> list[str]:
    from rapidfuzz import fuzz
    q = (query or "").strip().lower()
    rows = _read_jsonl(EPISODES_FILE)
    if not q:
        return [r["summary"] for r in rows[-limit:]]
    now = time.time()
    scored = [(max(fuzz.partial_ratio(q, r["summary"].lower()), fuzz.token_set_ratio(q, r["summary"].lower())), r)
              for r in rows]
    return [f"{_when(r['ts'], now)} — {r['summary']}"
            for s, r in sorted(scored, key=lambda x: (-x[0], -x[1]["ts"])) if s >= 60][:limit]


# ── сбор фактов ───────────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """Ты — модуль памяти личного ассистента ДЖАРВИС. Ниже — что ассистент уже знает о пользователе и новый кусок их разговора.

Задача:
1. Найди НОВЫЕ устойчивые факты о пользователе, которые пригодятся потом: имя, возраст, город, семья и близкие (имена!), работа/учёба, распорядок, здоровье, вкусы (музыка, фильмы, еда, игры), цели и планы, важные даты, проекты, как он любит, чтобы с ним общались.
   НЕ сохраняй: разовые команды («включи музыку»), погоду, то, что уже есть в памяти теми же словами, догадки.
2. Если новый разговор ОПРОВЕРГАЕТ старый факт — укажи его в remove и дай новый в facts с тем же key.
3. Напиши summary — о чём был разговор, одной-двумя фразами, по-русски, с конкретикой (имена, решения, договорённости). Если разговор пустой (только команды) — summary пустая строка.

Категории: {cats}.
key — короткий, по-русски, через подчёркивание (имя, город, любимая_музыка, брат, день_рождения_мамы). value — коротко, по сути.

Ответ — строго JSON:
{{"facts": [{{"category": "...", "key": "...", "value": "..."}}], "remove": [{{"category": "...", "key": "..."}}], "summary": "..."}}

УЖЕ ИЗВЕСТНО:
{known}

НОВЫЙ РАЗГОВОР:
{dialog}
"""


def build_extract_prompt(known: list[tuple[str, str, str]], turns: list[dict]) -> str:
    from memory.memory_manager import CATEGORIES
    known_s = "\n".join(f"- {c}/{k}: {v}" for c, k, v in known) or "(пока ничего)"
    return _EXTRACT_PROMPT.format(cats=", ".join(CATEGORIES), known=known_s[:6000],
                                  dialog="\n".join(_line(t) for t in turns)[-12000:])


def apply_extraction(data: dict) -> tuple[int, int]:
    """Применить ответ модели. (добавлено, удалено)."""
    from memory.memory_manager import CATEGORIES, forget, update_memory
    removed = 0
    for r in data.get("remove") or []:
        if isinstance(r, dict) and forget(str(r.get("category", "")), str(r.get("key", ""))):
            removed += 1
    patch: dict[str, dict] = {}
    for f in data.get("facts") or []:
        if not isinstance(f, dict):
            continue
        cat = str(f.get("category", "notes"))
        cat = cat if cat in CATEGORIES else "notes"
        key = str(f.get("key", "")).strip().replace(" ", "_")[:40]
        val = str(f.get("value", "")).strip()[:300]
        if key and val:
            patch.setdefault(cat, {})[key] = {"value": val}
    if patch:
        update_memory(patch)
    if data.get("summary"):
        add_episode(str(data["summary"]))
    return sum(len(v) for v in patch.values()), removed


def _gemini_json(prompt: str) -> dict:
    from core.onboarding import ensure_gemini_key
    from google import genai
    client = genai.Client(api_key=ensure_gemini_key(interactive=False))
    resp = client.models.generate_content(
        model=MODEL, contents=prompt,
        config={"response_mime_type": "application/json", "temperature": 0.2})
    return json.loads(resp.text or "{}")


_QUOTA_PAUSE_SEC = 60.0          # если Gemini не назвал свой срок


def _quota_pause(exc: Exception) -> float:
    """Сколько ждать после отказа по квоте. 0 — отказ не про квоту.

    Gemini присылает свой срок («Please retry in 24.57893355s»), его и берём:
    ждать меньше бессмысленно, больше — терять факты."""
    text = str(exc)
    if "RESOURCE_EXHAUSTED" not in text and "429" not in text:
        return 0.0
    match = re.search(r"retry in ([\d.]+)s", text, re.IGNORECASE)
    if match:
        try:
            return max(1.0, float(match.group(1)))
        except ValueError:
            pass
    return _QUOTA_PAUSE_SEC


class Collector:
    """Ждёт конца разговора и отдаёт его модели памяти. Место, до которого
    разобрано, хранится на диске: что не успели до выхода — разберём при
    следующем запуске."""

    def __init__(self, llm=_gemini_json, clock=time.time, state_file: Path | None = None):
        self.llm, self.clock, self.state_file = llm, clock, state_file or STATE_FILE
        self.done_ts = float(self._state().get("done_ts", 0.0))
        self._busy = threading.Lock()
        self._retry_at = 0.0        # до этого времени квота исчерпана, не дёргаем

    def _state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"done_ts": self.done_ts}), encoding="utf-8")
        except OSError as exc:
            logger.debug("Состояние памяти: %s", exc)

    def due(self) -> list[dict] | None:
        turns = turns_since(self.done_ts)
        if not turns:
            return None
        if len(turns) >= BATCH or self.clock() - turns[-1]["ts"] >= IDLE_SEC:
            return turns
        return None

    def run_once(self, force: bool = False) -> str:
        if not self._busy.acquire(blocking=False):
            return "занят"
        if self.clock() < self._retry_at:
            self._busy.release()
            return "ждём квоту"
        try:
            turns = turns_since(self.done_ts) if force else self.due()
            if not turns:
                return "нечего"
            if not any(t["role"] == "user" for t in turns):
                self.done_ts = turns[-1]["ts"]
                self._save()
                return "только ответы"
            from memory.memory_manager import all_facts
            data = self.llm(build_extract_prompt(all_facts(), turns))
            added, removed = apply_extraction(data if isinstance(data, dict) else {})
            self.done_ts = turns[-1]["ts"]
            self._save()
            logger.info("Память: +%d фактов, −%d, итог: %s", added, removed, (data or {}).get("summary", "")[:80])
            return f"+{added} −{removed}"
        except Exception as exc:
            pause = _quota_pause(exc)
            if pause:
                # Реплики остаются неразобранными, поэтому без паузы сборщик
                # каждый тик бил бы в ту же исчерпанную квоту: в живом логе
                # это дало 111 одинаковых предупреждений подряд.
                self._retry_at = self.clock() + pause
                logger.warning("Память: квота Gemini исчерпана, следующая попытка через %.0f с", pause)
            else:
                logger.warning("Память: разбор разговора не удался: %s", exc)
            return f"ошибка: {exc}"
        finally:
            self._busy.release()

    def start(self, tick: float = 20.0):
        def loop():
            while True:
                time.sleep(tick)
                self.run_once()
        threading.Thread(target=loop, daemon=True, name="memory-collector").start()


_collector: Collector | None = None


def collector() -> Collector:
    global _collector
    if _collector is None:
        _collector = Collector()
    return _collector


# ── для промпта и инструментов ────────────────────────────────────────────────

def prompt_context(resuming: bool) -> str:
    """Итоги прошлых разговоров, а в новую сессию — ещё и хвост разговора."""
    parts = [format_episodes()]
    try:
        from memory import shared
        from memory.memory_manager import all_facts
        parts.append(shared.prompt_block(all_facts()))
    except Exception as exc:
        logger.debug("Общая память: %s", exc)
    if not resuming:
        parts.append(format_recent())
    return "\n".join(p for p in parts if p)


def recall(query: str = "") -> str:
    """Ответ инструмента recall_memory: факты, итоги и реплики по теме."""
    from memory.memory_manager import _CAT_RU, all_facts, search
    q = (query or "").strip()
    facts = search(q, limit=15) if q else all_facts()
    out = []
    if facts:
        out.append("Факты: " + "; ".join(f"{_CAT_RU.get(c, c).lower()} — {k}: {v}" for c, k, v in facts[:40]))
    eps = search_episodes(q)
    if eps:
        out.append("Разговоры: " + " | ".join(eps))
    if q:
        lines = search_dialog(q)
        if lines:
            out.append("Реплики: " + " | ".join(lines))
    try:
        from memory import shared
        more = shared.search(q)
        if more:
            out.append("Общая память с Telegram: " + " | ".join(more))
    except Exception as exc:
        logger.debug("Общая память: %s", exc)
    return "\n".join(out) or (f"В памяти ничего про «{q}»." if q else "Память пока пуста.")


def forget_about(query: str) -> str:
    from memory.memory_manager import forget_matching
    gone = forget_matching(query)
    return (f"Забыл: {', '.join(k for _, k, _ in gone)}." if gone
            else f"В памяти нет фактов про «{query}».")
