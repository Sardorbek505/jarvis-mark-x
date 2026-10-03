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
    """Иконка трея: шар из точек (assets/art/icon, scripts/build_art.py), на паузе — серый.
    Нет картинки — рисуется прежний значок."""
    try:
        from core.paths import get_base_dir
        p = get_base_dir() / "assets" / "art" / "icon" / ("tray_online.png" if online else "tray_offline.png")
        if p.is_file():
            icon = QIcon(str(p))
            if not icon.isNull():
                return icon
    except Exception:
        pass
    return _drawn_icon(online)


# Состояние Джарвиса → эмоция лица в трее.
_TRAY_EMOTION = {"IDLE": "calm", "LISTENING": "listen", "THINKING": "think", "PROCESSING": "think",
                 "SPEAKING": "talk", "RECONNECTING": "sad", "INITIALISING": "think", "MUTED": "sleep",
                 "TROUBLE": "dizzy"}
_TRAY_RGB = {"calm": (48, 208, 190), "listen": (70, 232, 128), "think": (182, 226, 64),
             "talk": (255, 138, 52), "sad": (255, 70, 96), "sleep": (105, 112, 124), "dizzy": (255, 92, 150)}


def face_icon(state: str = "IDLE") -> QIcon:
    """Значок в трее — само лицо Джарвиса с эмоцией состояния: ждёт, слушает,
    думает, говорит, спит (микрофон выключен), нет связи. Видно у часов."""
    try:
        from ui_face import Face
        emo = _TRAY_EMOTION.get((state or "").upper(), "calm")
        icon = QIcon()
        for size in (16, 24, 32, 48, 64):
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            f = Face(seed=5)
            f.set_emotion(emo)
            f.family = {"happy": "arc", "dizzy": "spiral", "sleep": "line"}.get(emo, "dot")
            f.step(1.0, 0.6 if emo == "talk" else 0.0)
            # Крупно и без ручек: в 16 точек у часов важны только глаза.
            f.paint(p, size / 2, size / 2, size * 0.80, _TRAY_RGB[emo], glow=0.0, hands=False)
            p.end()
            icon.addPixmap(pm)
        return icon
    except Exception as exc:
        logger.debug("Лицо для трея не нарисовалось: %s", exc)
        return create_reactor_icon(state.upper() != "MUTED")


def _drawn_icon(online: bool = True) -> QIcon:
    """Прежняя векторная иконка — запасная."""
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

        self._state, self._muted = "IDLE", False
        self.setIcon(face_icon("IDLE"))
        self.setToolTip("Джарвис — слушает")

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

        # Коротко, как у Coucou: открыть, написать, пауза — остальное в «Экраны».
        self.act_toggle_window = QAction("Открыть Джарвиса", menu)
        self.act_toggle_window.triggered.connect(self._toggle_window)
        menu.addAction(self.act_toggle_window)

        self.act_chat = QAction("Написать Джарвису…", menu)
        self.act_chat.triggered.connect(self._open_chat)
        menu.addAction(self.act_chat)

        self.act_pause = QAction("Пауза — не слушать", menu)
        self.act_pause.setCheckable(True)
        self.act_pause.triggered.connect(self._toggle_pause)
        menu.addAction(self.act_pause)

        menu.addSeparator()
        screens = menu.addMenu("Экраны")
        self.act_settings = QAction("Настройки", menu)
        self.act_settings.triggered.connect(self._open_settings)
        self.act_keys = QAction("Ключи и подключения", menu)
        self.act_keys.triggered.connect(self._open_keys)
        self.act_contacts = QAction("Контакты", menu)
        self.act_contacts.triggered.connect(self._open_contacts)
        self.act_macros = QAction("Свои команды", menu)
        self.act_macros.triggered.connect(self._open_macros)
        self.act_study = QAction("Учёба", menu)
        self.act_study.triggered.connect(self._open_study)
        self.act_about = QAction("Обо мне", menu)
        self.act_about.triggered.connect(self._open_about)
        self.act_backup = QAction("Резервная копия", menu)
        self.act_backup.triggered.connect(self._open_backup)
        self.act_help = QAction("Что умеет Джарвис", menu)
        self.act_help.triggered.connect(self._open_help)
        for a in (self.act_settings, self.act_keys, self.act_contacts, self.act_macros, self.act_study,
                  self.act_about, self.act_backup, self.act_help):
            screens.addAction(a)

        from ui_setup import is_windows_autostart_enabled, set_windows_autostart
        self.act_autostart = QAction("Запускать вместе с Windows", menu)
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(is_windows_autostart_enabled())
        self.act_autostart.triggered.connect(lambda checked: set_windows_autostart(checked))
        menu.addAction(self.act_autostart)

        menu.addSeparator()

        # Выход
        self.act_exit = QAction("Выход", menu)
        self.act_exit.triggered.connect(self._quit)
        menu.addAction(self.act_exit)

        self.setContextMenu(menu)

    # ── состояние в значке ───────────────────────────────────────────────────
    def update_state(self, state: str | None = None, muted: bool | None = None):
        """Лицо в трее — по состоянию; микрофон выключен — спит. Из потока Qt."""
        if state is not None:
            self._state = state.upper()
        if muted is not None:
            self._muted = bool(muted)
            self.act_pause.setChecked(self._muted)
        shown = "MUTED" if self._muted else self._state
        if shown == getattr(self, "_shown", None):
            return                         # то же лицо — не перерисовывать (голос меняется часто)
        self._shown = shown
        self.setIcon(face_icon(shown))
        self.setToolTip({"MUTED": "Джарвис — пауза, не слушает", "LISTENING": "Джарвис — слушает",
                         "THINKING": "Джарвис — думает", "SPEAKING": "Джарвис — говорит",
                         "RECONNECTING": "Джарвис — нет связи"}.get(shown, "Джарвис — ждёт «Джарвис»"))

    def _open_chat(self):
        opener = getattr(self.main_window, "open_island_chat", None)
        if opener:
            opener()

    def _toggle_pause(self):
        toggle = getattr(self.main_window, "toggle_mute", None)
        if toggle:
            toggle()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # Left click
            self._toggle_window()

    def _toggle_window(self):
        if not self.main_window:
            return
        if self.main_window.isVisible() and not self.main_window.isMinimized():
            self.main_window.hide()
            self.act_toggle_window.setText("Открыть Джарвиса")
        else:
            self.main_window.show()
            self.main_window.raise_()
            self.main_window.activateWindow()
            self.act_toggle_window.setText("Спрятать окно")

    def _open_settings(self):
        if self.on_settings_cb:
            self.on_settings_cb()
        else:
            from ui_setup import SetupWizardDialog
            dialog = SetupWizardDialog(parent=self.main_window)
            dialog.exec()

    def _open_help(self):
        opener = getattr(self.main_window, "open_welcome", None)
        if opener:
            opener()
        else:
            from ui_welcome import open_dialog
            self._help_dlg = open_dialog(None)

    def _open_study(self):
        opener = getattr(self.main_window, "open_study", None)
        if opener:
            opener()
        else:
            from ui_study import open_dialog
            self._study_dlg = open_dialog(None)

    def _open_about(self):
        opener = getattr(self.main_window, "open_about", None)
        if opener:
            opener()
        else:
            from ui_about import open_dialog
            self._about_dlg = open_dialog(None)

    def _open_contacts(self):
        opener = getattr(self.main_window, "open_contacts", None)
        if opener:
            opener()
        else:
            from ui_contacts import open_dialog
            self._contacts_dlg = open_dialog(None)

    def _open_keys(self):
        opener = getattr(self.main_window, "open_keys", None)
        if opener:
            opener()
        else:
            from ui_keys import open_dialog
            self._keys_dlg = open_dialog(None)

    def _open_macros(self):
        opener = getattr(self.main_window, "open_macros", None)
        if opener:
            opener()
        else:
            from ui_macros import open_dialog
            self._macros_dlg = open_dialog(None)

    def _open_backup(self):
        opener = getattr(self.main_window, "open_backup", None)
        if opener:
            opener()
        else:
            from ui_backup import open_dialog
            self._backup_dlg = open_dialog(None)

    def _quit(self):
        self.hide()
        if self.on_exit_cb:
            self.on_exit_cb()
        else:
            from PyQt6.QtWidgets import QApplication
            QApplication.quit()
