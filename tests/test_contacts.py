"""Контакты: кого назвали (с падежами), правила (разрешения, ночь),
подтверждение перед сообщением и звонком (уходит ровно подтверждённый
текст), отправка и непрочитанные — на подменённом Telegram, окно."""
import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

import pytest

from core import contacts as CT


@pytest.fixture
def book(tmp_path):
    b = CT.Book(tmp_path / "contacts.json", now=lambda: datetime(2026, 9, 27, 14, 0))
    for c in (CT.Contact("Мама", "+79991234567", ["мама", "мамочка"], read_aloud=True, tg_id=11),
              CT.Contact("Азиз Каримов", "@aziz_k", ["брат", "азиз"], tg_id=22),
              CT.Contact("Шеф", "@boss_work", ["начальник"], can_call=False),
              CT.Contact("Дильноза", "@dilnoza", can_message=False)):
        b.upsert(c)
    return b


@pytest.mark.parametrize("spoken,name", [
    ("маме", "Мама"), ("Мамочке", "Мама"), ("брату", "Азиз Каримов"), ("Азизу", "Азиз Каримов"),
    ("@aziz_k", "Азиз Каримов"), ("t.me/aziz_k", "Азиз Каримов"), ("начальнику", "Шеф"), ("Дильнозе", "Дильноза"),
])
def test_finds_person_in_any_case(book, spoken, name):
    c, problem = book.one(spoken)
    assert c and c.name == name, problem


def test_unknown_and_ambiguous(book):
    c, problem = book.one("тёте Любе")
    assert c is None and "нет в контактах" in problem
    book.upsert(CT.Contact("Азиз Рахимов", "@aziz_r", ["азиз"]))
    c, problem = book.one("азизу")
    assert c is None and problem.startswith("Кого именно:")


@pytest.mark.parametrize("raw,out", [
    ("@Aziz_K", "@Aziz_K"), ("aziz_k", "@aziz_k"), ("https://t.me/aziz_k", "@aziz_k"),
    ("+7 999 123-45-67", "+79991234567"), ("8 999 123 45 67", "+89991234567"), ("ab", ""), ("@a b", ""),
])
def test_parse_telegram(raw, out):
    assert CT.parse_telegram(raw) == out


def test_rules_permissions_and_night(book):
    api = CT.Contacts(book, CT.Me("/nonexistent/jarvis_me"))
    assert api.precheck("message", "Дильнозе", "привет")[1].startswith("Писать «Дильноза» вы не разрешили")
    assert api.precheck("call", "шефу", "")[1].startswith("Звонить «Шеф» вы не разрешили")
    assert api.precheck("message", "маме", "")[1] == "Что написать?"
    assert api.precheck("call", "маме", "ужин")[0].name == "Мама"
    book.now = lambda: datetime(2026, 9, 27, 23, 30)
    assert "ночь" in api.precheck("call", "маме", "ужин")[1]
    assert api.precheck("call", "маме", "ужин", urgent=True)[0].name == "Мама"    # «срочно» — можно
    book.quiet_on = False
    assert api.precheck("call", "маме", "ужин")[0].name == "Мама"


# ── подменённый Telegram ─────────────────────────────────────────────────────

class FakeClient:
    def __init__(self):
        self.sent = []

    async def get_input_entity(self, peer):
        from telethon.tl.types import InputPeerUser
        return InputPeerUser(int(peer), 0)

    async def get_entity(self, target):
        return SimpleNamespace(id={"@aziz_k": 22, "@newguy": 77}.get(str(target), 99))

    async def send_message(self, entity, text):
        self.sent.append((entity.user_id, text))

    async def iter_dialogs(self, limit=40):
        for d in (SimpleNamespace(is_user=True, unread_count=2, id=11, name="Мама", entity=11),
                  SimpleNamespace(is_user=True, unread_count=1, id=500, name="Незнакомец", entity=500),
                  SimpleNamespace(is_user=True, unread_count=0, id=22, name="Азиз", entity=22)):
            yield d

    async def iter_messages(self, entity, limit=5):
        for m in (SimpleNamespace(out=False, raw_text="Ты где?"), SimpleNamespace(out=False, raw_text="Позвони")):
            yield m


class FakeMe:
    def __init__(self):
        self.client = FakeClient()
        self.watch_ids = set
        self.on_message = None

    def run(self, fn, timeout=40):
        return asyncio.run(fn(self.client))

    def linked(self):
        return True


def test_message_goes_to_right_person_and_learns_id(book):
    me = FakeMe()
    api = CT.Contacts(book, me)
    logs = []
    api.log = logs.append
    assert api.message("брату", "Буду в 7") == "Отправил Азиз Каримов."
    book.upsert(CT.Contact("Новенький", "@newguy"))
    assert api.message("новенькому", "Привет") == "Отправил Новенький."
    assert me.client.sent == [(22, "Буду в 7"), (77, "Привет")]
    assert book.one("новенькому")[0].tg_id == 77                         # id запомнен — дальше без поиска
    assert logs[0] == "SYS: 💬 → Азиз Каримов: Буду в 7"
    assert api.message("Дильнозе", "x").startswith("Писать")            # запрещённому — не уходит
    assert len(me.client.sent) == 2


def test_unread_only_from_contacts(book):
    api = CT.Contacts(book, FakeMe())
    text = api.unread()
    assert text == "Непрочитанные: Мама (2): «Позвони» / «Ты где?»."     # по порядку; незнакомца — нет
    assert api.unread("брат") == "Новых сообщений от ваших контактов нет."


def test_incoming_goes_to_island_log(book):
    api = CT.Contacts(book, CT.Me("/nonexistent/jarvis_me"))
    logs = []
    api.log = logs.append
    assert api.me.watch_ids() == {11}                                   # «читать мне» — только мама
    api.me.on_message(11, "Mom", "Купи хлеб")
    assert logs == ["SYS: 💬 Мама: Купи хлеб"]


def test_call_contact_reports_back(book):
    api = CT.Contacts(book, FakeMe())
    calls, logs, done = [], [], []
    api.log = logs.append
    api.call_fn = lambda target, name, text, via=None: (calls.append((target, name, text, via)),
                                                        "Поговорили 1 мин, попрощались. Азиз ответил: «Иду».")[1]
    import threading
    finished = threading.Event()
    assert api.call("брату", "Ужин готов", done=lambda r: (done.append(r), finished.set())) == \
        "Звоню Азиз Каримов. Когда поговорю — перескажу."
    assert finished.wait(2)
    # ваш Telegram подключён — звоним с него, по id (его ваш аккаунт знает)
    assert calls == [("id:22", "Азиз Каримов", "Ужин готов", api.me)] and "Иду" in done[0]
    assert "с вашего Telegram" in api.confirm_text("call", "брату", "Ужин готов")


def test_call_without_your_telegram_goes_from_jarvis_account(book):
    me = FakeMe()
    me.linked = lambda: False
    api = CT.Contacts(book, me)
    calls = []
    api.call_fn = lambda target, name, text: calls.append((target, name)) or "ок"
    import threading
    finished = threading.Event()
    api.call("брату", "Ужин готов", done=lambda r: finished.set())
    assert finished.wait(2) and calls == [("@aziz_k", "Азиз Каримов")]     # аккаунту Джарвиса нужен @username
    assert "с аккаунта Джарвиса" in api.confirm_text("call", "брату", "x")


def test_resolve_peer_by_account_id_not_as_phone():
    """Контакт из вашего Telegram (только id): раньше id принимался за номер
    телефона («+123456789») — звонок не проходил."""
    from core import tg_call

    class Client:
        def __init__(self, known):
            self.known, self.asked = set(known), []

        async def get_input_entity(self, uid):
            if uid not in self.known:
                raise ValueError("нет в кэше")
            return uid

        async def __call__(self, req):
            self.asked.append(type(req).__name__)
            self.known.add(123456789)                     # контакты подтянулись в кэш

    c = Client([])
    assert asyncio.run(tg_call.resolve_peer(c, "id:123456789")) == 123456789
    assert c.asked == ["GetContactsRequest"]               # не ImportContactsRequest (телефон)

    class Nobody(Client):
        async def __call__(self, req):
            self.asked.append(type(req).__name__)
    with pytest.raises(RuntimeError, match="не знает этого человека"):
        asyncio.run(tg_call.resolve_peer(Nobody([]), "id:555555555"))


def test_what_they_said_from_transcript():
    from core import tg_call
    assert tg_call._what_they_said(["Джарвис: Здравствуйте", "Вы: Привет", "Вы: иду уже", "Джарвис: Хорошо"]) == \
        "Привет иду уже"
    prompt = tg_call.instruction_contact("Сардор", "Азиз", "ужин готов")
    assert "Азиз" in prompt and "ужин готов" in prompt and "не выдумывай" in prompt


def test_tool_add_find_delete(book, monkeypatch):
    monkeypatch.setattr(CT, "_contacts", CT.Contacts(book, FakeMe()))
    assert CT.contacts_tool({"action": "add", "name": "Бабушка", "telegram": "+998 90 123 45 67",
                             "aliases": "бабуля, бабушка"}) == "Контакт «Бабушка» сохранён (+998901234567)."
    assert "Бабушка" in CT.contacts_tool({"action": "find", "name": "бабуле"})
    assert "Нужен @username" in CT.contacts_tool({"action": "add", "name": "X", "telegram": "?"})
    assert CT.contacts_tool({"action": "delete", "name": "бабушку"}) == "Удалил контакт «Бабушка»."


# ── подтверждение в main: переспросить дословно, отправить подтверждённое ────

@pytest.fixture
def jarvis(tmp_path, monkeypatch, book):
    import main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(CT, "_contacts", CT.Contacts(book, FakeMe()))
    ui = SimpleNamespace(logs=[], muted=False, on_text_command=None)
    ui.write_log = ui.logs.append
    ui.set_state = lambda s: None
    ui.lock_on = lambda t: None
    return main, main.Jarvis(ui)


def test_message_needs_yes_and_sends_exactly_what_was_confirmed(jarvis, monkeypatch):
    main, j = jarvis
    sent = []
    monkeypatch.setattr(CT, "contacts_tool", lambda p, done=None: sent.append(dict(p)) or "Отправил Мама.")
    assert main._is_destructive("contacts", {"action": "message", "name": "маме", "text": "Задержусь"})
    assert not main._is_destructive("contacts", {"action": "message", "name": "тёте Любе", "text": "x"})

    def fc(text):
        return SimpleNamespace(id="1", name="contacts", args={"action": "message", "name": "маме", "text": text})
    r = asyncio.run(j._execute_tool(fc("Задержусь на 20 минут")))
    assert "НЕ ВЫПОЛНЕНО" in r.response["result"]
    assert "дословно: «Отправить Мама (+79991234567) от вашего имени: «Задержусь на 20 минут»?»" \
        in r.response["result"]
    assert sent == []
    j._user_turn += 1
    j.last_user_text = "да, отправляй"
    r = asyncio.run(j._execute_tool(fc("Я задержусь минут на 20")))     # модель пересказала иначе
    assert sent and sent[0]["text"] == "Задержусь на 20 минут"          # ушло то, что подтвердили


def test_no_means_no(jarvis, monkeypatch):
    main, j = jarvis
    sent = []
    monkeypatch.setattr(CT, "contacts_tool", lambda p, done=None: sent.append(p) or "ok")
    call = SimpleNamespace(id="1", name="contacts", args={"action": "call", "name": "брату", "text": "ужин"})
    asyncio.run(j._execute_tool(call))
    j._user_turn += 1
    j.last_user_text = "нет, не надо"
    r = asyncio.run(j._execute_tool(call))
    assert sent == [] and "НЕ ВЫПОЛНЕНО" in r.response["result"]


# ── окно ─────────────────────────────────────────────────────────────────────

def test_contacts_window(book):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    import ui_contacts
    api = CT.Contacts(book, FakeMe())
    dlg = ui_contacts.ContactsDialog(api=api, caller_ready=lambda: "Аккаунт Джарвиса не подключён",
                                     open_keys=lambda: None)
    try:
        assert dlg.people.count() == 4 and "ЛЮДИ · 4" in dlg.count_cap.text()
        assert "Подключён" in dlg.me_state.text() and "Не настроен" in dlg.caller_state.text()
        dlg.new_contact()
        dlg.name.setText("Бабушка")
        dlg.telegram.setText("бабушка")                                  # не username и не номер
        assert not dlg.save_contact() and "Telegram" in dlg.status.text()
        dlg.telegram.setText("+998 90 123 45 67")
        dlg.alias_in.setText("бабуля")
        dlg.read_aloud.setChecked(True)
        assert dlg.save_contact()
        c, _ = book.one("бабуле")
        assert c.telegram == "+998901234567" and c.read_aloud and c.aliases == ["бабуля"]
        assert "напиши бабуля" in dlg.status.text()
        dlg.delete_contact()
        assert book.one("бабуле")[0] is None and dlg.people.count() == 4
        dlg.search.setText("брат")
        assert dlg.people.count() == 1
        dlg.repaint()
    finally:
        dlg.close()


def test_initials():
    from ui_contacts import initials
    assert initials("Азиз Каримов") == "АК" and initials("мама") == "М" and initials("") == "?"


def test_shutdown_asks_instead_of_crashing(jarvis, monkeypatch):
    """Старый баг: у Джарвиса не было _pending_destructive, и «выключи
    компьютер» падало AttributeError вместо вопроса «точно?»."""
    main, j = jarvis
    fc = SimpleNamespace(id="1", name="computer_control", args={"action": "shutdown"})
    r = asyncio.run(j._execute_tool(fc))
    assert "НЕ ВЫПОЛНЕНО" in r.response["result"] and "точно" in r.response["result"]
