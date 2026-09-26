"""Контакты: кому Джарвис может написать и позвонить.

«Напиши маме, что задержусь на 20 минут» — сообщение уходит от ВАШЕГО
Telegram (как будто вы написали сами). «Позвони брату и скажи, что ужин
готов» — звонит аккаунт Джарвиса, говорит сам и пересказывает ответ.

Правила — потому что это пишется и звонится живым людям от вашего имени:
- только людям из книжки, и только если у контакта включено «писать» /
  «звонить»; чужим — никогда, рассылок — нет;
- перед отправкой и звонком Джарвис переспрашивает, кому и что
  (подтверждение — в main.py, как для выключения ПК);
- ночью (по умолчанию 23:00–08:00) не звонит, кроме «срочно»;
- «читать мне» — сообщения этого человека Джарвис показывает в капсуле
  и читает на «что мне написали?».

Ваш Telegram для Джарвиса — своя сессия (jarvis_me.session), вход по QR из
окна «Контакты»: бот-сервер пользуется своей, и они друг другу не мешают.
Окно — ui_contacts.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_ENDINGS = ("ами", "ями", "ому", "ему", "ого", "его", "ими", "ыми", "ий", "ый", "ая", "яя", "ой", "ей", "ом",
            "ем", "ам", "ям", "ах", "ях", "ов", "ев", "ую", "юю", "а", "я", "у", "ю", "е", "и", "ы", "о")


@dataclass
class Contact:
    name: str
    telegram: str = ""                        # @username, t.me/…, номер телефона
    aliases: list[str] = field(default_factory=list)   # как вы его зовёте: «мама», «мамочка»
    can_message: bool = True
    can_call: bool = True
    read_aloud: bool = False                  # показывать и читать его сообщения
    note: str = ""
    tg_id: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @classmethod
    def from_dict(cls, d: dict) -> "Contact":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        c = cls(**known)
        c.name = str(c.name or "").strip() or "Без имени"
        c.aliases = [a.strip() for a in c.aliases or [] if str(a).strip()]
        c.telegram = str(c.telegram or "").strip()
        c.tg_id = int(c.tg_id or 0)
        return c

    def label(self) -> str:
        return self.name + (f" ({self.telegram})" if self.telegram else "")


def _norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _stem(word: str) -> str:
    """«маме» → «мам», «брату» → «брат», «Азизу» → «азиз»: падежи не мешают."""
    for e in _ENDINGS:
        if len(word) > len(e) + 2 and word.endswith(e):
            return word[:-len(e)]
    return word


def _stems(text: str) -> tuple[str, ...]:
    return tuple(_stem(w) for w in _norm(text).split())


def parse_telegram(value: str) -> str:
    """Любая запись → «@username» или «+79991234567»; пусто — если не похоже."""
    v = (value or "").strip()
    v = re.sub(r"^(https?://)?(www\.)?t\.me/", "@", v)
    digits = re.sub(r"[^\d+]", "", v)
    if digits.lstrip("+").isdigit() and len(digits.lstrip("+")) >= 9 and not v.startswith("@"):
        return digits if digits.startswith("+") else "+" + digits
    m = re.fullmatch(r"@?([A-Za-z][A-Za-z0-9_]{3,31})", v)
    return "@" + m.group(1) if m else ""


class Book:
    def __init__(self, path: Path, now: Callable[[], datetime] = datetime.now):
        self.path = Path(path)
        self.now = now
        self.contacts: list[Contact] = []
        self.quiet = (23, 8)                  # не звонить с 23 до 8
        self.quiet_on = True
        self._lock = threading.RLock()
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.contacts = [Contact.from_dict(d) for d in data.get("contacts", [])]
            q = data.get("quiet") or {}
            self.quiet = (int(q.get("from", 23)), int(q.get("to", 8)))
            self.quiet_on = q.get("on", True) is not False
        except FileNotFoundError:
            self.contacts = []
        except Exception as exc:
            logger.warning("Контакты не прочитались: %s", exc)
            self.contacts = []

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "quiet": {"from": self.quiet[0], "to": self.quiet[1],
                                                               "on": self.quiet_on},
                                       "contacts": [asdict(c) for c in self.contacts]},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def upsert(self, c: Contact) -> Contact:
        with self._lock:
            self.contacts = [x for x in self.contacts if x.id != c.id]
            self.contacts.append(c)
            self.save()
        return c

    def delete(self, cid: str) -> bool:
        with self._lock:
            n = len(self.contacts)
            self.contacts = [x for x in self.contacts if x.id != cid]
            if len(self.contacts) != n:
                self.save()
                return True
        return False

    def by_tg_id(self, tg_id: int) -> Contact | None:
        return next((c for c in self.contacts if tg_id and c.tg_id == tg_id), None)

    def find(self, spoken: str) -> list[Contact]:
        """Кого назвали: «маме», «брату Азизу», «@aziz». Точные совпадения —
        сразу; иначе по основе слова (падежи); иначе похожие. Несколько — список."""
        s = (spoken or "").strip()
        if not s:
            return []
        tg = parse_telegram(s)
        if tg:
            hit = [c for c in self.contacts if c.telegram.lower() == tg.lower()]
            if hit:
                return hit
        n = _norm(s)
        exact = [c for c in self.contacts if n in [_norm(x) for x in [c.name, *c.aliases]]]
        if exact:
            return exact
        st = _stems(s)
        by_stem = [c for c in self.contacts
                   if any(_stems(x) == st or (len(st) == 1 and st[0] in _stems(x)) for x in [c.name, *c.aliases])]
        if by_stem:
            return by_stem
        try:
            from rapidfuzz import fuzz
            scored = sorted(((max(fuzz.ratio(n, _norm(x)) for x in [c.name, *c.aliases]), c)
                             for c in self.contacts), key=lambda t: -t[0])
            return [c for score, c in scored if score >= 82][:3]
        except ImportError:
            return []

    def one(self, spoken: str) -> tuple[Contact | None, str]:
        """(контакт, «») или (None, что сказать пользователю)."""
        found = self.find(spoken)
        if not found:
            return None, (f"«{spoken}» нет в контактах. Добавьте в окне «Контакты» (или скажите "
                          f"«добавь контакт {spoken} — @username»).")
        if len(found) > 1:
            return None, "Кого именно: " + ", ".join(c.label() for c in found[:4]) + "?"
        return found[0], ""

    def quiet_now(self) -> bool:
        if not self.quiet_on:
            return False
        h = self.now().hour
        a, b = self.quiet
        return (h >= a or h < b) if a > b else (a <= h < b)


# ── ваш Telegram: своя сессия, один клиент в своём потоке ────────────────────

class Me:
    """Ваш аккаунт Telegram для Джарвиса: отправка, непрочитанные и новые
    сообщения. Один клиент в своём потоке и цикле — sqlite-сессию Telethon
    нельзя открывать из нескольких мест сразу."""

    def __init__(self, session: str):
        self.session = session
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self._lock = threading.Lock()
        self.on_message: Callable[[int, str, str], None] = lambda tg_id, name, text: None
        self.watch_ids: Callable[[], set[int]] = set

    def linked(self) -> bool:
        return os.path.isfile(self.session + ".session")

    def _ensure_loop(self):
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                threading.Thread(target=self._loop.run_forever, daemon=True, name="tg-me").start()

    def run(self, coro_fn, timeout: float = 40):
        """Выполнить coro_fn(client) в потоке клиента."""
        self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(self._with_client(coro_fn), self._loop)
        return fut.result(timeout=timeout)

    async def _with_client(self, coro_fn):
        if self._client is None or not self._client.is_connected():
            from telethon import TelegramClient, events

            from core.tg_call import _credentials
            api_id, api_hash = _credentials()
            client = TelegramClient(self.session, api_id, api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                raise RuntimeError("ваш Telegram не подключён к Джарвису — окно «Контакты» → «Подключить»")

            @client.on(events.NewMessage(incoming=True))
            async def _incoming(ev):
                try:
                    if ev.is_private and ev.sender_id in self.watch_ids():
                        sender = await ev.get_sender()
                        name = " ".join(x for x in (getattr(sender, "first_name", ""),
                                                    getattr(sender, "last_name", "")) if x)
                        self.on_message(ev.sender_id, name, ev.raw_text or "[вложение]")
                except Exception as exc:
                    logger.debug("Входящее: %s", exc)
            self._client = client
        return await coro_fn(self._client)

    def start_listening(self):
        """Подключиться заранее, чтобы новые сообщения приходили сами."""
        if not self.linked():
            return

        async def noop(_c):
            return True

        def go():
            try:
                self.run(noop, timeout=60)
                logger.info("Контакты: слушаю входящие сообщения")
            except Exception as exc:
                logger.info("Контакты: входящие не слушаю — %s", exc)
        threading.Thread(target=go, daemon=True, name="tg-me-start").start()


# ── действия ─────────────────────────────────────────────────────────────────

class Contacts:
    def __init__(self, book: Book, me: Me):
        self.book, self.me = book, me
        self.log: Callable[[str], None] = lambda text: None
        self.say: Callable[[str], None] = lambda text: None
        self.call_fn: Callable[..., str] | None = None          # tg_call.call_contact
        me.watch_ids = lambda: {c.tg_id for c in self.book.contacts if c.read_aloud and c.tg_id}
        me.on_message = self._incoming

    def _incoming(self, tg_id: int, name: str, text: str):
        c = self.book.by_tg_id(tg_id)
        who = c.name if c else name
        short = text if len(text) <= 160 else text[:157] + "…"
        self.log(f"SYS: 💬 {who}: {short}")

    # проверка до подтверждения: есть ли такой и можно ли
    def precheck(self, action: str, who: str, text: str = "", urgent: bool = False) -> tuple[Contact | None, str]:
        c, problem = self.book.one(who)
        if not c:
            return None, problem
        if not c.telegram and not c.tg_id:
            return None, f"У контакта «{c.name}» не указан Telegram — впишите @username или номер."
        if action == "message":
            if not c.can_message:
                return None, f"Писать «{c.name}» вы не разрешили — включите в окне «Контакты»."
            if not text.strip():
                return None, "Что написать?"
        if action == "call":
            if not c.can_call:
                return None, f"Звонить «{c.name}» вы не разрешили — включите в окне «Контакты»."
            if self.book.quiet_now() and not urgent:
                a, b = self.book.quiet
                return None, (f"Сейчас ночь ({a}:00–{b:02d}:00) — звонить «{c.name}» не буду. "
                              "Если срочно — скажите «срочно».")
        return c, ""

    def confirm_text(self, action: str, who: str, text: str = "") -> str:
        c, problem = self.precheck(action, who, text, urgent=True)
        if not c:
            return problem
        if action == "message":
            return f"Отправить {c.label()} от вашего имени: «{text}»?"
        return f"Позвонить {c.label()} с аккаунта Джарвиса и сказать: «{text or 'просто позвонить'}»?"

    def message(self, who: str, text: str, as_voice: bool = False) -> str:
        c, problem = self.precheck("message", who, text)
        if not c:
            return problem
        target = c.tg_id or c.telegram

        async def send(client):
            from telethon import utils
            entity = await _entity(client, target)
            if as_voice:
                try:
                    from telegram_bot import tts_fish
                    ogg = await tts_fish.speak_ogg(text)
                except Exception:
                    ogg = None
                if ogg:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                        tf.write(ogg)
                    try:
                        await client.send_file(entity, tf.name, voice_note=True)
                    finally:
                        os.unlink(tf.name)
                    return utils.get_peer_id(entity), True
            await client.send_message(entity, text)
            return utils.get_peer_id(entity), False
        try:
            tg_id, voiced = self.me.run(send)
        except Exception as exc:
            logger.warning("Сообщение %s: %s", c.name, exc)
            return f"Не отправил {c.name}: {exc}"
        if tg_id and c.tg_id != tg_id:
            c.tg_id = tg_id
            self.book.upsert(c)
        self.log(f"SYS: 💬 → {c.name}: {text}")
        return f"Отправил {c.name}" + (" голосовым" if voiced else "") + "."

    def call(self, who: str, text: str, urgent: bool = False, done: Callable[[str], None] | None = None) -> str:
        c, problem = self.precheck("call", who, text, urgent)
        if not c:
            return problem
        if not self.call_fn:
            return "Звонки не подключены."
        target = c.telegram or str(c.tg_id)

        def run():
            result = self.call_fn(target, c.name, text)
            self.log(f"SYS: 📞 {c.name}: {result}")
            if done:
                done(result)
        threading.Thread(target=run, daemon=True, name="tg-call-contact").start()
        return f"Звоню {c.name}. Когда поговорю — перескажу."

    def unread(self, who: str = "") -> str:
        only = None
        if who:
            c, problem = self.book.one(who)
            if not c:
                return problem
            only = c

        async def read(client):
            out = []
            async for d in client.iter_dialogs(limit=40):
                if not d.is_user or not d.unread_count:
                    continue
                c = self.book.by_tg_id(d.id)
                if only and (not c or c.id != only.id):
                    continue
                if not only and not c:
                    continue
                msgs = []
                async for m in client.iter_messages(d.entity, limit=min(d.unread_count, 5)):
                    if not m.out:
                        msgs.append(m.raw_text or "[вложение]")
                out.append((c.name if c else d.name, d.unread_count, list(reversed(msgs))))
            return out
        try:
            rows = self.me.run(read)
        except Exception as exc:
            return f"Не могу прочитать: {exc}"
        if not rows:
            return "Новых сообщений от ваших контактов нет."
        parts = []
        for name, n, msgs in rows:
            parts.append(f"{name} ({n}): " + " / ".join(f"«{m[:200]}»" for m in msgs))
        return "Непрочитанные: " + "; ".join(parts) + "."

    def import_from_telegram(self) -> str:
        async def fetch(client):
            from telethon.tl.functions.contacts import GetContactsRequest
            res = await client(GetContactsRequest(hash=0))
            return [(u.id, " ".join(x for x in (u.first_name, u.last_name) if x) or (u.username or ""),
                     ("@" + u.username) if u.username else ("+" + u.phone if u.phone else ""))
                    for u in res.users if not getattr(u, "bot", False) and not getattr(u, "deleted", False)]
        try:
            users = self.me.run(fetch, timeout=60)
        except Exception as exc:
            return f"Не получилось: {exc}"
        added = 0
        for tg_id, name, tg in users:
            if self.book.by_tg_id(tg_id) or (tg and any(c.telegram.lower() == tg.lower() for c in self.book.contacts)):
                continue
            self.book.contacts.append(Contact(name=name or tg, telegram=tg, tg_id=tg_id))
            added += 1
        self.book.save()
        return f"Добавил {added} из {len(users)} контактов Telegram." if users else "В Telegram нет контактов."

    def describe(self) -> str:
        if not self.book.contacts:
            return "Контактов пока нет. Откройте окно «Контакты» — можно подтянуть из Telegram одной кнопкой."
        rows = [c.name + (f" («{', '.join(c.aliases)}»)" if c.aliases else "") for c in self.book.contacts[:30]]
        return f"Контакты ({len(self.book.contacts)}): " + ", ".join(rows) + "."


async def _entity(client, target):
    if isinstance(target, int) or str(target).lstrip("-").isdigit():
        try:
            return await client.get_input_entity(int(target))
        except Exception:
            pass
    from core.tg_call import resolve_peer
    peer = await resolve_peer(client, str(target))
    return await client.get_input_entity(peer)


# ── вход в ваш Telegram по QR ────────────────────────────────────────────────

def login(ask: Callable[[str, bool], str], qr_view) -> str:
    """Подключить ваш Telegram к Джарвису (своя сессия) — по QR."""
    from core.tg_call import _credentials, _qr_sign_in

    async def go():
        from telethon import TelegramClient
        api_id, api_hash = _credentials()
        client = TelegramClient(me().session, api_id, api_hash)
        await client.connect()
        ok = False
        try:
            if not await client.is_user_authorized():
                if not await _qr_sign_in(client, qr_view, ask):
                    return "Вход отменён: QR-код не отсканировали."
            u = await client.get_me()
            ok = True
            return f"Готово: подключён ваш Telegram — {u.first_name or ''} (@{u.username or u.phone})."
        finally:
            await client.disconnect()
            if not ok:
                try:
                    os.remove(me().session + ".session")
                except OSError:
                    pass
    try:
        text = asyncio.run(go())
    except Exception as exc:
        return f"Вход не удался: {type(exc).__name__}: {exc}"
    if text.startswith("Готово"):
        me().start_listening()
    return text


# ── один на процесс ──────────────────────────────────────────────────────────

_book: Book | None = None
_me: Me | None = None
_contacts: Contacts | None = None


def _data_dir() -> Path:
    try:
        from core.paths import get_data_root
        return Path(get_data_root())
    except Exception:
        return Path(__file__).resolve().parent.parent


def book() -> Book:
    global _book
    if _book is None:
        _book = Book(Path(os.getenv("JARVIS_CONTACTS") or (_data_dir() / "contacts.json")))
    return _book


def me() -> Me:
    global _me
    if _me is None:
        _me = Me(str(_data_dir() / "jarvis_me"))
    return _me


def contacts() -> Contacts:
    global _contacts
    if _contacts is None:
        _contacts = Contacts(book(), me())
        try:
            from core import tg_call
            _contacts.call_fn = tg_call.call_contact
        except Exception as exc:
            logger.debug("Звонки контактам: %s", exc)
    return _contacts


def contacts_tool(p: dict, done: Callable[[str], None] | None = None) -> str:
    p = p or {}
    a = str(p.get("action") or "list").lower()
    c = contacts()
    who = str(p.get("name") or "").strip()
    text = str(p.get("text") or "").strip()
    if a == "message":
        return c.message(who, text, bool(p.get("as_voice")))
    if a == "call":
        return c.call(who, text, bool(p.get("urgent")), done)
    if a == "read":
        return c.unread(who)
    if a == "add":
        tg = parse_telegram(str(p.get("telegram") or ""))
        if not who:
            return "Как назвать контакт?"
        if not tg:
            return "Нужен @username или номер телефона в Telegram."
        aliases = [x.strip() for x in re.split(r"[,;]", str(p.get("aliases") or "")) if x.strip()]
        existing = c.book.find(who)
        con = existing[0] if len(existing) == 1 and _norm(existing[0].name) == _norm(who) else Contact(name=who)
        con.telegram, con.aliases = tg, sorted(set(con.aliases + aliases))
        c.book.upsert(con)
        return f"Контакт «{con.name}» сохранён ({tg})."
    if a == "delete":
        con, problem = c.book.one(who)
        if not con:
            return problem
        c.book.delete(con.id)
        return f"Удалил контакт «{con.name}»."
    if a == "find":
        found = c.book.find(who)
        return ("Нашёл: " + ", ".join(x.label() for x in found)) if found else f"«{who}» нет в контактах."
    if a == "list":
        return c.describe()
    return f"Не понял действие «{a}»."
