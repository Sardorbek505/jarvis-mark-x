"""Все экраны — в одном окне: панель слева, экран справа. Ни один экран не
открывается отдельным окном; «Готово» — назад к шару; голос и трей
(open_keys, open_study…) открывают экран в том же окне."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_every_screen_opens_inside_main_window(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import ui as jarvis_ui
    win = jarvis_ui.JarvisUI("face.png")
    try:
        win.show()
        app.processEvents()
        before = {w for w in app.topLevelWidgets() if w.isVisible()}
        assert win._stack.currentWidget() is win._hud and win._nav.buttons["home"].isChecked()
        for key, _title, _icon in jarvis_ui.PAGES[1:]:
            win.show_page(key)
            app.processEvents()
            page = win._pages[key]
            assert win._stack.currentWidget() is page, key
            assert not page.isWindow() and page.isVisible(), key          # встроен, не отдельное окно
            assert win._nav.buttons[key].isChecked() and not win._nav.buttons["home"].isChecked()
        after = {w for w in app.topLevelWidgets() if w.isVisible()}
        assert after - before == set()                                    # ни одного нового окна
        # «Готово»/«Понятно» у бывших окон — назад к шару, экран не пропадает
        win.show_page("about")
        win._pages["about"].close()                                       # кнопка «Готово»
        app.processEvents()
        assert win._page == "home" and win._stack.currentWidget() is win._hud
        win.show_page("about")
        assert win._pages["about"].isVisible()
        # из другого потока (голос, трей) — через сигнал
        win.open_keys()
        app.processEvents()
        assert win._page == "keys"
        win.open_study()
        app.processEvents()
        assert win._page == "study"
        # набор текста на экране — не улетает в поле ввода шара
        from PyQt6.QtTest import QTest
        QTest.keyClick(win, "a")
        assert win._input.text() == ""
        win.show_page("home")
        QTest.keyClick(win, "a")
        assert win._input.text() == "a"
    finally:
        win._island.close()
        win.hide()
        win.deleteLater()
