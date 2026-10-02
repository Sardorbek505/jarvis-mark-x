"""Трей: значок — лицо Джарвиса по состоянию, короткое меню с паузой и чатом."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])


def _tray():
    from core import tray as T
    calls = []
    win = SimpleNamespace(toggle_mute=lambda: calls.append("mute"), open_island_chat=lambda: calls.append("chat"),
                          isVisible=lambda: False, isMinimized=lambda: False, show=lambda: None,
                          raise_=lambda: None, activateWindow=lambda: None)
    return T.JarvisTray(main_window=win), calls, T


def test_face_icon_for_every_state():
    from core import tray as T
    for st in ("IDLE", "LISTENING", "THINKING", "SPEAKING", "RECONNECTING", "MUTED", "TROUBLE"):
        assert not T.face_icon(st).isNull(), st


def test_short_menu_with_pause_and_chat():
    t, calls, _T = _tray()
    top = [a.text() for a in t.contextMenu().actions() if a.text()]
    assert top[:3] == ["Открыть Джарвиса", "Написать Джарвису…", "Пауза — не слушать"]
    assert "Экраны" in top and top[-1] == "Выход"
    screens = next(a.menu() for a in t.contextMenu().actions() if a.text() == "Экраны")
    assert "Ключи и подключения" in [a.text() for a in screens.actions()]
    t.act_chat.trigger()
    t.act_pause.trigger()
    assert calls == ["chat", "mute"]


def test_icon_follows_state_and_pause():
    t, _calls, _T = _tray()
    t.update_state("LISTENING")
    assert t.toolTip() == "Джарвис — слушает"
    t.update_state(muted=True)
    assert t.act_pause.isChecked() and "пауза" in t.toolTip()
    t.update_state("SPEAKING")                     # на паузе лицо спит, что бы ни было
    assert "пауза" in t.toolTip()
    t.update_state(muted=False)
    assert t.toolTip() == "Джарвис — говорит"
