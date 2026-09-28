"""Свои команды и контакты ПК — для пульта в Telegram (бот и Mini App).

Работает на ПК внутри pc_server.py (отдельный процесс от голосового
Джарвиса): те же macros.json и contacts.json из папки данных.

- list_items() — свои команды для кнопок пульта (паки — нет: им нужна
  программа впереди, с телефона это не работает).
- run(name, confirmed) — выполнить и дождаться; что команда «сказала»
  и на каком шаге споткнулась — в ответе текстом. Команда с «Спрашивать
  перед запуском» без confirmed не выполняется: пульт переспросит.
- match_text(text) — «режим стрима» текстом в бот → своя команда;
  «точно режим стрима» — подтверждение.
- resolve_contact(alias) — «мама» → @username из книжки «Контакты» на ПК,
  если человеку можно писать.
"""
from __future__ import annotations

import importlib
import logging
import re
import threading

logger = logging.getLogger(__name__)

_run_lock = threading.Lock()
_SAID = re.compile(r"«(.+)»\]\s*$", re.S)


def _store():
    from core.macros import macros
    m = macros()
    m.run_tool = _run_tool
    return m


def _run_tool(name: str, args: dict) -> str:
    """Шаг «Действие Джарвиса» без голосового Джарвиса: actions.<name>.<name>(parameters=…)."""
    try:
        mod = importlib.import_module(f"actions.{name}")
        fn = getattr(mod, name)
    except (ImportError, AttributeError):
        return f"Ошибка: действие «{name}» с телефона недоступно — только голосом на ПК"
    return str(fn(parameters=dict(args or {})) or "")


def list_items() -> list[dict]:
    from core.macro_triggers import describe_when
    out = []
    for c in sorted(_store().commands, key=lambda c: c.name.lower()):
        if c.pack or not c.enabled:
            continue
        out.append({"name": c.name, "phrase": c.phrases[0] if c.phrases else "",
                    "confirm": c.confirm, "steps": len(c.steps),
                    "when": ", ".join(describe_when(w) for w in c.when)})
    return out


def list_text() -> str:
    items = list_items()
    if not items:
        return "Своих команд пока нет — создайте их на ПК: «Джарвис, создай команду…» или в окне «Свои команды»."
    rows = [f"• {i['name']}" + (f" — «{i['phrase']}»" if i["phrase"] else "") + (" 🔒" if i["confirm"] else "")
            for i in items]
    return "🧩 Свои команды:\n" + "\n".join(rows) + "\n\nНапишите фразу команды — выполню на ПК."


def run(name: str, confirmed: bool = False) -> dict:
    m = _store()
    c = next((x for x in m.commands if x.name == name and not x.pack), None) or m.find(name)
    if not c or c.pack:
        return {"ok": False, "text": f"Нет своей команды «{name}»."}
    if not c.enabled:
        return {"ok": False, "text": f"Команда «{c.name}» выключена."}
    if c.confirm and not confirmed:
        return {"ok": False, "need_confirm": True, "name": c.name,
                "text": f"Выполнить «{c.name}»? Это команда с подтверждением."}
    return _run(m, c, {})


def _run(m, c, slots: dict) -> dict:
    said, problems = [], []
    with _run_lock:
        old = m.say, m.log
        m.say = lambda text: said.append(text)
        m.log = lambda text: problems.append(text) if "⚠" in text else None
        try:
            res = m.run(c, slots, wait=True)
        finally:
            m.say, m.log = old
    ok = res == "Готово."
    lines = [f"{'✅' if ok else '⚠️'} «{c.name}» — " + ("выполнено" if ok else "не до конца")]
    lines += [p.replace("SYS: ⚠ ", "") for p in problems]
    lines += ["🗣 " + mm.group(1) for s in said if (mm := _SAID.search(s)) and ok]
    return {"ok": ok, "text": "\n".join(lines)}


def match_text(text: str) -> dict | None:
    """Своя команда по фразе из бота. None — это не своя команда."""
    t = (text or "").strip()
    confirmed = t.lower().startswith("точно ")
    if confirmed:
        t = t[6:]
    try:
        m = _store()
        hit = m.match(t)
    except Exception as exc:
        logger.debug("Свои команды с телефона: %s", exc)
        return None
    if not hit or hit[0].pack:
        return None
    c, slots = hit
    if c.confirm and not confirmed:
        return {"ok": False, "text": f"«{c.name}» — команда с подтверждением. Напишите «точно {t}» "
                                     "или нажмите её в пульте."}
    return _run(m, c, slots)


def resolve_contact(alias: str) -> dict:
    """Человек из книжки ПК → адрес для userbot."""
    from core.contacts import contacts
    found = contacts().book.find(alias)
    if not found:
        return {"ok": False, "text": f"«{alias}» нет и в контактах ПК."}
    if len(found) > 1:
        return {"ok": False, "text": f"«{alias}» — несколько: " + ", ".join(c.name for c in found[:5]) + "."}
    c = found[0]
    if not c.can_message:
        return {"ok": False, "text": f"{c.name}: писать запрещено в окне «Контакты»."}
    target = c.telegram or (str(c.tg_id) if c.tg_id else "")
    if not target:
        return {"ok": False, "text": f"У {c.name} не указан Telegram."}
    return {"ok": True, "target": target, "name": c.name, "text": f"{c.name} → {target}"}


def call_contact(alias: str, message: str = "", confirmed: bool = False) -> dict:
    """«Позвони Ибрагиму и скажи …» из бота. Без confirmed — только вопрос «звонить?»
    (живому человеку от вашего имени — только после явного «да»); с confirmed — звонок
    в фоне тем же путём, что голосом на ПК (core/contacts: книжка, «звонить» разрешено,
    тихие часы, ваш Telegram или аккаунт Джарвиса)."""
    from core.contacts import contacts
    api = contacts()
    c, problem = api.precheck("call", alias, message, urgent=True)
    if not c:
        return {"ok": False, "text": problem}
    if not confirmed:
        return {"ok": True, "need_confirm": True, "name": c.name,
                "text": "📞 " + api.confirm_text("call", alias, message)}
    return {"ok": True, "name": c.name, "text": "📞 " + api.call(alias, message, urgent=True)}
