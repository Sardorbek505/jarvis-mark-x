"""Крестик окна выключает Джарвиса, а не прячет его в трей."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_x_quits_and_tray_button_only_hides(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import ui as jarvis_ui
    win = jarvis_ui.JarvisUI("face.png")
    quits = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))
    win.tray = SimpleNamespace(isVisible=lambda: True, hide=lambda: None, showMessage=lambda *a: None)
    win.show()
    app.processEvents()

    win.hide_to_tray()                       # «Свернуть в трей» — Джарвис живёт дальше
    assert not win.isVisible() and quits == []

    win.show()
    win.close()                              # крестик
    app.processEvents()
    assert quits == [1]
    assert not win.isVisible()


def test_mainloop_runs_quit_hooks_and_exits(monkeypatch):
    import ui as jarvis_ui
    calls = []
    fake = SimpleNamespace(_app=SimpleNamespace(exec=lambda: 0), on_quit=[lambda: calls.append("cleanup")])
    monkeypatch.setattr(jarvis_ui.os, "_exit", lambda code: calls.append(("exit", code)))
    jarvis_ui.JarvisUI.mainloop(fake)
    assert calls == ["cleanup", ("exit", 0)]
