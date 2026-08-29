"""JARVIS Mark X — Интеграция с системным треем Windows (System Tray).

Позволяет приложению работать в фоне, отображать статус в трее возле часов,
управлять автозапуском и открывать настройки без черных окон терминала.
"""
import logging
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

logger = logging.getLogger("jarvis-tray")


def create_reactor_icon(online: bool = True) -> QIcon:
    """Генерирует программную векторную иконку реактора Тони Старка в трей."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    center = size / 2

    # Цветовая гамма: бирюзовый/циан при онлайн, серый при паузе
    c_main = QColor(34, 211, 238) if online else QColor(148, 163, 184)
    c_glow = QColor(34, 211, 238, 70) if online else QColor(148, 163, 184, 40)
    c_core = QColor(255, 255, 255) if online else QColor(203, 213, 225)

    # 1. Внешнее свечение
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(c_glow)
    painter.drawEllipse(2, 2, size - 4, size - 4)

    # 2. Внешнее кольцо
    pen = QPen(c_main, 3)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(6, 6, size - 12, size - 12)

    # 3. Внутренний треугольник / ядро
    pen_core = QPen(c_main, 2)
    painter.setPen(pen_core)
    painter.setBrush(c_core)
    painter.drawEllipse(18, 18, size - 36, size - 36)

    # 4. Лучи реактора (3 сегмента)
    pen_ray = QPen(c_main, 2)
    painter.setPen(pen_ray)
    painter.drawLine(int(center), 8, int(center), 18)
    painter.drawLine(10, int(center + 12), 20, int(center + 6))
    painter.drawLine(size - 10, int(center + 12), size - 20, int(center + 6))

    painter.end()
    return QIcon(pixmap)


class JarvisTray(QSystemTrayIcon):
    """Иконка и контекстное меню в системном трее Windows."""

    def __init__(
        self,
        main_window: Optional[QWidget] = None,
        on_exit: Optional[Callable] = None,
        on_settings: Optional[Callable] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.main_window = main_window
        self.on_exit_cb = on_exit
        self.on_settings_cb = on_settings

        self.setIcon(create_reactor_icon(online=True))
        self.setToolTip("JARVIS Mark X — Активен")

        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self):
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #0c1021;
                color: #f1f5f9;
                border: 1px solid rgba(34, 211, 238, 0.3);
                border-radius: 6px;
                padding: 4px;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 13px;
            }
            QMenu::item {
                padding: 6px 24px 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: rgba(34, 211, 238, 0.2);
                color: #22d3ee;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(34, 211, 238, 0.15);
                margin: 4px 8px;
            }
        """)

        # Показать / скрыть интерфейс
        self.act_toggle_window = QAction("⚡ Показать HUD", menu)
        self.act_toggle_window.triggered.connect(self._toggle_window)
        menu.addAction(self.act_toggle_window)

        # Настройки
        self.act_settings = QAction("⚙️ Настройки и ключи", menu)
        self.act_settings.triggered.connect(self._open_settings)
        menu.addAction(self.act_settings)

        menu.addSeparator()

        # Автозапуск
        from ui_setup import is_windows_autostart_enabled, set_windows_autostart

        self.act_autostart = QAction("🔄 Автозапуск с Windows", menu)
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(is_windows_autostart_enabled())
        self.act_autostart.triggered.connect(lambda checked: set_windows_autostart(checked))
        menu.addAction(self.act_autostart)

        menu.addSeparator()

        # Выход
        self.act_exit = QAction("❌ Выход из JARVIS", menu)
        self.act_exit.triggered.connect(self._quit)
        menu.addAction(self.act_exit)

        self.setContextMenu(menu)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # Left click
            self._toggle_window()

    def _toggle_window(self):
        if not self.main_window:
            return
        if self.main_window.isVisible() and not self.main_window.isMinimized():
            self.main_window.hide()
            self.act_toggle_window.setText("⚡ Показать HUD")
        else:
            self.main_window.show()
            self.main_window.raise_()
            self.main_window.activateWindow()
            self.act_toggle_window.setText("⚡ Скрыть HUD")

    def _open_settings(self):
        if self.on_settings_cb:
            self.on_settings_cb()
        else:
            from ui_setup import SetupWizardDialog
            dialog = SetupWizardDialog(parent=self.main_window)
            dialog.exec()

    def _quit(self):
        self.hide()
        if self.on_exit_cb:
            self.on_exit_cb()
        else:
            from PyQt6.QtWidgets import QApplication
            QApplication.quit()
