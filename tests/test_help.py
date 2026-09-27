"""Подсказки для нового пользователя: чек-лист с живыми галочками, кнопки
ведут в нужные окна, фразы-примеры отправляются Джарвису, окно при первом
запуске — один раз."""
import os

from core import help as H


def test_checklist_reflects_real_state(monkeypatch):
    import core.keys as K
    monkeypatch.setattr(K, "load_values", lambda: {"gemini_api_key": "AIza" + "x" * 35})
    items = {s.id: (done, detail) for s, done, detail in H.checklist()}
    assert items["gemini"] == (True, "ключ вписан")
    assert items["voice"] == (False, "не записан") and items["study"][0] is False
    assert [s.id for s in H.STEPS if not s.optional] == ["gemini", "about"]      # обязательного мало


def test_broken_check_does_not_break_window():
    step = H.Step("x", "X", "why", "keys", lambda: 1 / 0)
    H.STEPS.append(step)
    try:
        assert H.checklist()[-1][1:] == (False, "")
    finally:
        H.STEPS.remove(step)


def test_seen_once():
    assert not H.seen()
    H.mark_seen()
    assert H.seen()


def test_every_ability_has_examples_and_text():
    for title, icon, phrases in H.ABILITIES:
        assert title and icon and len(phrases) >= 3
    assert "Учёба" in H.abilities_text() and "(трей)" in H.abilities_text()


def test_welcome_window():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    import ui_welcome
    opened, tried = [], []
    dlg = ui_welcome.WelcomeDialog(actions={"keys": lambda: opened.append("keys")},
                                   try_phrase=tried.append, first_run=True)
    try:
        assert "из 7" in dlg.side_progress_label.text()
        dlg.run_action("keys")
        dlg.run_action("нет такого")                                           # не падает
        assert opened == ["keys"]
        dlg.show_page(2)
        dlg.try_it("таймер на 10 минут")
        assert tried == ["таймер на 10 минут"] and "Отправил" in dlg.try_hint.text()
        dlg.search.setText("маме")
        visible = [c for c, _h in dlg.ability_cards if c.isVisibleTo(dlg)]
        assert len(visible) == 1 and not dlg.nothing.isVisibleTo(dlg)
        dlg.search.setText("ничего такого нет")
        assert dlg.nothing.isVisibleTo(dlg)
        dlg.close()
        assert H.seen()                                                        # закрыли — больше само не откроется
    finally:
        dlg.close()
