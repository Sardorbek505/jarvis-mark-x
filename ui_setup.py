"""JARVIS Mark X — Графический мастер настройки и первого запуска (Setup Wizard).

Позволяет пользователю настроить API ключи, протестировать микрофон,
выбрать голос и настроить автозапуск в Windows без редактирования файлов.
"""
import asyncio
import json
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("jarvis-setup")

_BASE_DIR = Path(__file__).resolve().parent
_CONFIG_FILE = _BASE_DIR / "config" / "api_keys.json"


# ── Работа с автозагрузкой Windows ────────────────────────────────────────────
def is_windows_autostart_enabled() -> bool:
    """Проверяет регистрацию в реестре автозапуска Windows."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        try:
            val, _ = winreg.QueryValueEx(key, "JARVIS_Mark_X")
            return bool(val)
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception as e:
        logger.debug("Проверка автозапуска: %s", e)
        return False


def set_windows_autostart(enable: bool) -> bool:
    """Включает или выключает автозапуск JARVIS при входе в Windows."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            if enable:
                if getattr(sys, "frozen", False):
                    exe_path = sys.executable
                else:
                    exe_path = f'"{sys.executable}" "{_BASE_DIR / "main.py"}"'
                winreg.SetValueEx(key, "JARVIS_Mark_X", 0, winreg.REG_SZ, str(exe_path))
            else:
                try:
                    winreg.DeleteValue(key, "JARVIS_Mark_X")
                except FileNotFoundError:
                    pass
            return True
        finally:
            winreg.CloseKey(key)
    except Exception as e:
        logger.warning("Ошибка изменения автозапуска: %s", e)
        return False


# ── Валидация Gemini API Key ──────────────────────────────────────────────────
def validate_gemini_key(key: str) -> tuple[bool, str]:
    """Проверяет работоспособность ключа Gemini отправкой минимального тестового запроса."""
    key = (key or "").strip()
    if not key:
        return False, "Ключ не может быть пустым"
    if len(key) < 20:
        return False, "Слишком короткий ключ. Проверьте правильность копирования."
    try:
        from google import genai
        client = genai.Client(api_key=key)
        # Быстрый легкий пинг модели
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="ping",
        )
        if response and (response.text or hasattr(response, "candidates")):
            return True, "Ключ активен и готов к работе!"
        return False, "Модель не вернула ответ. Проверьте статус ключа в Google AI Studio."
    except Exception as e:
        err_msg = str(e)
        if "API_KEY_INVALID" in err_msg or "400" in err_msg:
            return False, "Неверный API ключ (API_KEY_INVALID)"
        if "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            return False, "Превышена квота запросов (429)"
        return False, f"Ошибка проверки: {err_msg[:120]}"


_APPDATA_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "JARVIS"
_APPDATA_CONFIG = _APPDATA_DIR / "api_keys.json"


# ── Конфигурация ──────────────────────────────────────────────────────────────
def load_config_data() -> dict:
    for p in [_APPDATA_CONFIG, _CONFIG_FILE]:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if data:
                    return data
            except Exception:
                pass
    return {}


def save_config_data(data: dict) -> bool:
    saved = False
    for p in [_CONFIG_FILE, _APPDATA_CONFIG]:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            existing = load_config_data()
            existing.update(data)
            p.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
            saved = True
        except Exception as e:
            logger.debug("Save config to %s failed: %s", p, e)
    return saved


# ── Поток для проверки микрофона в реальном времени ───────────────────────────
class MicLevelWorker(QObject):
    level_signal = pyqtSignal(int)  # 0..100

    def __init__(self, device_index=None):
        super().__init__()
        self.device_index = device_index
        self._running = False

    def start_listening(self):
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._running = False

    def _run(self):
        try:
            import numpy as np
            import sounddevice as sd

            def callback(indata, frames, time_info, status):
                if not self._running:
                    raise sd.CallbackStop()
                rms = np.sqrt(np.mean(indata.astype(np.float32) ** 2))
                # Шкалируем 0..4000 RMS в проценты 0..100
                percent = int(min(100, (rms / 3000.0) * 100))
                self.level_signal.emit(percent)

            with sd.InputStream(
                channels=1,
                samplerate=16000,
                dtype="int16",
                device=self.device_index,
                callback=callback,
                blocksize=1024,
            ):
                while self._running:
                    time.sleep(0.05)
        except Exception as e:
            logger.debug("Mic level worker error: %s", e)


# ── Главное диалоговое окно Setup Wizard ───────────────────────────────────────
class SetupWizardDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("JARVIS Mark X — Мастер настройки")
        self.setMinimumSize(620, 560)
        self.cfg = load_config_data()
        self.mic_worker = None

        self._setup_style()
        self._init_ui()
        self._load_current_values()

    def _setup_style(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #070913;
                color: #e2e8f0;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QTabWidget::pane {
                border: 1px solid rgba(34, 211, 238, 0.25);
                border-radius: 8px;
                background-color: #0c1021;
                padding: 12px;
            }
            QTabBar::tab {
                background: #080c1a;
                color: #94a3b8;
                padding: 9px 18px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
                font-size: 13px;
            }
            QTabBar::tab:selected {
                background: #0c1021;
                color: #22d3ee;
                border: 1px solid rgba(34, 211, 238, 0.35);
                border-bottom: none;
            }
            QLabel {
                color: #cbd5e1;
                font-size: 13px;
            }
            QLineEdit {
                background: #080c1a;
                border: 1px solid rgba(34, 211, 238, 0.25);
                border-radius: 6px;
                padding: 8px 12px;
                color: #f8fafc;
                font-size: 13px;
                selection-background-color: #0284c7;
            }
            QLineEdit:focus {
                border: 1px solid #22d3ee;
                box-shadow: 0 0 8px rgba(34, 211, 238, 0.3);
            }
            QPushButton {
                background: linear-gradient(135deg, #0284c7, #0369a1);
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
                font-size: 13px;
                cursor: pointer;
            }
            QPushButton:hover {
                background: #0ea5e9;
            }
            QPushButton:pressed {
                background: #0369a1;
            }
            QPushButton.secondary {
                background: rgba(34, 211, 238, 0.12);
                border: 1px solid rgba(34, 211, 238, 0.35);
                color: #22d3ee;
            }
            QPushButton.secondary:hover {
                background: rgba(34, 211, 238, 0.22);
            }
            QComboBox {
                background: #080c1a;
                border: 1px solid rgba(34, 211, 238, 0.25);
                border-radius: 6px;
                padding: 6px 12px;
                color: #f8fafc;
                font-size: 13px;
            }
            QComboBox QAbstractItemView {
                background: #080c1a;
                color: #f8fafc;
                selection-background-color: #0284c7;
            }
            QProgressBar {
                border: 1px solid rgba(34, 211, 238, 0.25);
                border-radius: 5px;
                text-align: center;
                background-color: #080c1a;
                height: 16px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #22d3ee);
                border-radius: 4px;
            }
            QCheckBox, QRadioButton {
                color: #e2e8f0;
                font-size: 13px;
                spacing: 8px;
            }
        """)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("🤖 JARVIS MARK X — НАСТРОЙКА")
        title.setStyleSheet("font-size: 17px; font-weight: 800; color: #22d3ee; letter-spacing: 1px;")
        sub = QLabel("Персональный ИИ-ассистент с голосовым управлением и синхронизацией")
        sub.setStyleSheet("color: #94a3b8; font-size: 12px;")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        header.addLayout(title_box)
        header.addStretch()
        main_layout.addLayout(header)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_ai(), "🧠 Мозг (Gemini)")
        self.tabs.addTab(self._tab_audio(), "🎙️ Микрофон и Звук")
        self.tabs.addTab(self._tab_voice(), "🔊 Голос")
        self.tabs.addTab(self._tab_telegram(), "📱 Telegram (Опц.)")
        main_layout.addWidget(self.tabs)

        # Autostart checkbox
        self.chk_autostart = QCheckBox("Запускать JARVIS автоматически при включении Windows")
        self.chk_autostart.setChecked(is_windows_autostart_enabled())
        main_layout.addWidget(self.chk_autostart)

        # Bottom buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.setProperty("class", "secondary")
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Сохранить и запустить JARVIS")
        self.btn_save.clicked.connect(self._save_and_start)
        btn_box.addWidget(self.btn_save)

        main_layout.addLayout(btn_box)

    # ── Вкладка 1: Gemini AI ──────────────────────────────────────────────────
    def _tab_ai(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(12)

        lbl = QLabel("<b>Ключ Google Gemini API</b> (обязательно для работы ума):")
        l.addWidget(lbl)

        key_row = QHBoxLayout()
        self.edit_gemini = QLineEdit()
        self.edit_gemini.setPlaceholderText("Вставьте ключ AIzaSy...")
        self.edit_gemini.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        key_row.addWidget(self.edit_gemini, 1)

        self.btn_toggle_key = QPushButton("👁")
        self.btn_toggle_key.setFixedWidth(36)
        self.btn_toggle_key.setToolTip("Показать/скрыть ключ")
        self.btn_toggle_key.clicked.connect(self._toggle_key_visibility)
        key_row.addWidget(self.btn_toggle_key)
        l.addLayout(key_row)

        actions_row = QHBoxLayout()
        self.btn_get_key = QPushButton("🔗 Получить бесплатный ключ (Google AI Studio)")
        self.btn_get_key.setProperty("class", "secondary")
        self.btn_get_key.clicked.connect(lambda: webbrowser.open("https://aistudio.google.com/app/apikey"))
        actions_row.addWidget(self.btn_get_key)

        self.btn_test_key = QPushButton("Проверить ключ")
        self.btn_test_key.clicked.connect(self._check_gemini_key)
        actions_row.addWidget(self.btn_test_key)
        l.addLayout(actions_row)

        self.lbl_key_status = QLabel("⚪ Статус: не проверен")
        self.lbl_key_status.setStyleSheet("color: #94a3b8; font-size: 12px; margin-top: 4px;")
        l.addWidget(self.lbl_key_status)

        # Модель
        l.addWidget(QLabel("Модель Gemini:"))
        self.combo_model = QComboBox()
        self.combo_model.addItems(["gemini-2.5-flash", "gemini-2.0-flash-exp", "gemini-1.5-pro"])
        l.addWidget(self.combo_model)

        l.addStretch()
        return w

    def _toggle_key_visibility(self):
        if self.edit_gemini.echoMode() == QLineEdit.EchoMode.Password:
            self.edit_gemini.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.edit_gemini.setEchoMode(QLineEdit.EchoMode.Password)

    def _check_gemini_key(self):
        key = self.edit_gemini.text().strip()
        if not key:
            self.lbl_key_status.setText("❌ Введите ключ перед проверкой")
            self.lbl_key_status.setStyleSheet("color: #f87171;")
            return

        self.lbl_key_status.setText("🔄 Проверка связи с Gemini...")
        self.lbl_key_status.setStyleSheet("color: #38bdf8;")
        self.btn_test_key.setEnabled(False)

        def worker():
            ok, msg = validate_gemini_key(key)
            QTimer.singleShot(0, lambda: self._on_key_checked(ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_key_checked(self, ok: bool, msg: str):
        self.btn_test_key.setEnabled(True)
        if ok:
            self.lbl_key_status.setText(f"✅ {msg}")
            self.lbl_key_status.setStyleSheet("color: #4ade80; font-weight: 600;")
        else:
            self.lbl_key_status.setText(f"❌ {msg}")
            self.lbl_key_status.setStyleSheet("color: #f87171;")

    # ── Вкладка 2: Микрофон ───────────────────────────────────────────────────
    def _tab_audio(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(12)

        l.addWidget(QLabel("<b>Микрофон (входное устройство):</b>"))
        self.combo_mic = QComboBox()
        self._populate_audio_devices()
        l.addWidget(self.combo_mic)

        # Тест уровня звука
        l.addWidget(QLabel("Уровень входящего звука (говорите в микрофон):"))
        self.progress_mic = QProgressBar()
        self.progress_mic.setRange(0, 100)
        self.progress_mic.setValue(0)
        l.addWidget(self.progress_mic)

        mic_btn_row = QHBoxLayout()
        self.btn_toggle_mic_test = QPushButton("▶ Начать тест микрофона")
        self.btn_toggle_mic_test.setProperty("class", "secondary")
        self.btn_toggle_mic_test.clicked.connect(self._toggle_mic_test)
        mic_btn_row.addWidget(self.btn_toggle_mic_test)
        l.addLayout(mic_btn_row)

        l.addStretch()
        return w

    def _populate_audio_devices(self):
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            self.combo_mic.addItem("Автовыбор (Рекомендуется — физический микрофон)", None)
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0 and d.get("hostapi", 0) == 0:
                    name = f"[{i}] {d['name']}"
                    self.combo_mic.addItem(name, i)
        except Exception as e:
            self.combo_mic.addItem(f"Ошибка загрузки устройств: {e}", None)

    def _toggle_mic_test(self):
        if self.mic_worker and self.mic_worker._running:
            self.mic_worker.stop()
            self.mic_worker = None
            self.progress_mic.setValue(0)
            self.btn_toggle_mic_test.setText("▶ Начать тест микрофона")
        else:
            dev_idx = self.combo_mic.currentData()
            self.mic_worker = MicLevelWorker(device_index=dev_idx)
            self.mic_worker.level_signal.connect(self.progress_mic.setValue)
            self.mic_worker.start_listening()
            self.btn_toggle_mic_test.setText("⏹ Остановить тест")

    # ── Вкладка 3: Голос ──────────────────────────────────────────────────────
    def _tab_voice(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(12)

        l.addWidget(QLabel("<b>Основной голос Джарвиса:</b>"))

        self.rb_edge_dmitry = QRadioButton("Microsoft Edge — Дмитрий (100% Бесплатно, без задержек)")
        self.rb_edge_svetlana = QRadioButton("Microsoft Edge — Светлана (Женский, бесплатно)")
        self.rb_fish = QRadioButton("Fish Audio — Каноничный голос JARVIS из фильмов (Требует API ключ)")

        self.rb_edge_dmitry.setChecked(True)
        l.addWidget(self.rb_edge_dmitry)
        l.addWidget(self.rb_edge_svetlana)
        l.addWidget(self.rb_fish)

        # Fish audio key box
        self.fish_box = QWidget()
        fb_layout = QVBoxLayout(self.fish_box)
        fb_layout.setContentsMargins(16, 4, 0, 4)
        fb_layout.addWidget(QLabel("API ключ Fish Audio:"))
        self.edit_fish_key = QLineEdit()
        self.edit_fish_key.setPlaceholderText("Вставьте ключ Fish Audio...")
        fb_layout.addWidget(self.edit_fish_key)
        l.addWidget(self.fish_box)

        self.rb_fish.toggled.connect(self.fish_box.setVisible)
        self.fish_box.setVisible(False)

        # Тест голоса
        test_btn_row = QHBoxLayout()
        self.btn_test_voice = QPushButton("🔊 Прослушать образец голоса")
        self.btn_test_voice.clicked.connect(self._test_voice_sample)
        test_btn_row.addWidget(self.btn_test_voice)
        l.addLayout(test_btn_row)

        l.addStretch()
        return w

    def _test_voice_sample(self):
        self.btn_test_voice.setEnabled(False)
        self.btn_test_voice.setText("Генерация...")

        def play():
            try:
                phrase = "Здравствуйте, сэр. Все системы функционируют в штатном режиме."
                if self.rb_fish.isChecked() and self.edit_fish_key.text().strip():
                    from telegram_bot import tts_fish
                    pcm = asyncio.run(tts_fish.speak_pcm(phrase))
                elif self.rb_edge_svetlana.isChecked():
                    from telegram_bot import tts_edge
                    pcm = asyncio.run(tts_edge.speak_pcm(phrase, voice="ru-RU-SvetlanaNeural"))
                else:
                    from telegram_bot import tts_edge
                    pcm = asyncio.run(tts_edge.speak_pcm(phrase, voice="ru-RU-DmitryNeural"))

                if pcm:
                    import numpy as np
                    import sounddevice as sd
                    arr = np.frombuffer(pcm, dtype=np.int16)
                    sd.play(arr, 24000)
                    sd.wait()
            except Exception as e:
                logger.warning("Voice sample error: %s", e)
            finally:
                QTimer.singleShot(0, lambda: self._reset_voice_btn())

        threading.Thread(target=play, daemon=True).start()

    def _reset_voice_btn(self):
        self.btn_test_voice.setEnabled(True)
        self.btn_test_voice.setText("🔊 Прослушать образец голоса")

    # ── Вкладка 4: Telegram ───────────────────────────────────────────────────
    def _tab_telegram(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(12)

        l.addWidget(QLabel("<b>Связь с Telegram (для управления с телефона):</b>"))

        l.addWidget(QLabel("Токен бота (Telegram Bot Token):"))
        self.edit_tg_token = QLineEdit()
        self.edit_tg_token.setPlaceholderText("123456789:ABCdefGhIJKlmNoPQRstuVWXyz...")
        l.addWidget(self.edit_tg_token)

        tg_actions = QHBoxLayout()
        btn_botfather = QPushButton("🔗 Создать бота через @BotFather")
        btn_botfather.setProperty("class", "secondary")
        btn_botfather.clicked.connect(lambda: webbrowser.open("https://t.me/BotFather"))
        tg_actions.addWidget(btn_botfather)
        l.addLayout(tg_actions)

        l.addWidget(QLabel("Ваш Telegram User ID (узнать у @userinfobot):"))
        self.edit_tg_user = QLineEdit()
        self.edit_tg_user.setPlaceholderText("Например: 123456789")
        l.addWidget(self.edit_tg_user)

        l.addStretch()
        return w

    # ── Загрузка и Сохранение ─────────────────────────────────────────────────
    def _load_current_values(self):
        c = self.cfg
        self.edit_gemini.setText(c.get("gemini_api_key", os.getenv("GEMINI_API_KEY", "")))
        model = c.get("gemini_model", "gemini-2.5-flash")
        idx = self.combo_model.findText(model)
        if idx >= 0:
            self.combo_model.setCurrentIndex(idx)

        self.edit_fish_key.setText(c.get("fish_api_key", os.getenv("FISH_API_KEY", "")))
        if c.get("fish_api_key"):
            self.rb_fish.setChecked(True)
            self.fish_box.setVisible(True)

        self.edit_tg_token.setText(c.get("telegram_bot_token", os.getenv("TELEGRAM_BOT_TOKEN", "")))
        allowed = c.get("telegram_allowed_users", "")
        if isinstance(allowed, list):
            self.edit_tg_user.setText(", ".join(map(str, allowed)))
        else:
            self.edit_tg_user.setText(str(allowed))

    def _save_and_start(self):
        if self.mic_worker:
            self.mic_worker.stop()

        gemini_key = self.edit_gemini.text().strip()
        if not gemini_key:
            QMessageBox.warning(
                self,
                "Не указан API ключ",
                "Пожалуйста, введите Gemini API Key на первой вкладке.\nБез него ассистент не сможет отвечать.",
            )
            self.tabs.setCurrentIndex(0)
            return

        # Парсим Telegram allowed users
        raw_users = self.edit_tg_user.text().strip()
        allowed = []
        if raw_users:
            for u in raw_users.split(","):
                u = u.strip()
                if u.isdigit():
                    allowed.append(int(u))

        data = {
            "gemini_api_key": gemini_key,
            "gemini_model": self.combo_model.currentText(),
            "telegram_bot_token": self.edit_tg_token.text().strip(),
            "telegram_allowed_users": allowed,
        }

        if self.rb_fish.isChecked() and self.edit_fish_key.text().strip():
            data["fish_api_key"] = self.edit_fish_key.text().strip()

        # Сохраняем в файл
        if not save_config_data(data):
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить файл конфигурации.")
            return

        # Обновляем автозапуск Windows
        set_windows_autostart(self.chk_autostart.isChecked())

        # Устанавливаем выбранный микрофон в env
        mic_idx = self.combo_mic.currentData()
        if mic_idx is not None:
            os.environ["MIC_DEVICE"] = str(mic_idx)

        self.accept()


# ── Запуск диалога при необходимости ──────────────────────────────────────────
def ensure_setup(force: bool = False) -> bool:
    """Проверяет наличие конфигурации. Если ключа нет или force=True, показывает окно."""
    cfg = load_config_data()
    has_key = bool(cfg.get("gemini_api_key") or os.getenv("GEMINI_API_KEY"))

    if has_key and not force:
        return True

    app = QApplication.instance() or QApplication(sys.argv)
    wizard = SetupWizardDialog()
    res = wizard.exec()
    return res == QDialog.DialogCode.Accepted


if __name__ == "__main__":
    app = QApplication(sys.argv)
    wizard = SetupWizardDialog()
    wizard.show()
    sys.exit(app.exec())
