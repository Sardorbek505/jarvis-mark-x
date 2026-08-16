"""Запуск Джарвиса без окна.

Зачем. `main()` создаёт PyQt-HUD и упирается в `ui.wait_for_api_key()`, который
ждёт человека. Из-за этого Джарвиса нельзя было ни запустить на сервере, ни
проверить автоматикой, ни просто померить задержку — сквозной прогон упирался
в графику. Здесь лежит замена окна: те же четыре точки, которыми пользуется
`Jarvis`, но вместо виджетов — лог в консоль.

Включается `JARVIS_HEADLESS=1` или флагом `--headless`.

Разница в поведении ровно одна, и она намеренная: настоящий HUD при отсутствии
ключа открывает мастер и ждёт. Здесь ждать некого, поэтому отсутствие ключа —
это громкая ошибка при старте, а не вечная тишина.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from core.onboarding import ensure_gemini_key

_logger = logging.getLogger("JARVIS")

_BASE = Path(__file__).resolve().parent.parent
_CONFIG = _BASE / "config" / "api_keys.json"


def headless_requested(argv: list[str] | None = None) -> bool:
    """Просили ли запуск без графики."""
    argv = sys.argv if argv is None else argv
    if "--headless" in argv:
        return True
    return os.getenv("JARVIS_HEADLESS", "") not in ("", "0", "false", "False")


class HeadlessUI:
    """Поверхность окна, которой пользуется Jarvis: четыре имени, не больше.

    `write_log`, `set_state`, `muted`, `on_text_command` — всё, что класс
    `Jarvis` дёргает у интерфейса. Сверено по main.py; если там появится новое
    обращение к `self.ui`, его нужно добавить и сюда.
    """

    def __init__(self, config_path: Path | None = None):
        self.muted = False
        self.on_text_command = None
        self.state = "INIT"
        self.logs: list[str] = []
        self._config_path = config_path or _CONFIG
        self._stdin_thread: threading.Thread | None = None

    # ── то, что зовёт Jarvis ──────────────────────────────────────────────────

    def write_log(self, text: str) -> None:
        self.logs.append(text)
        _logger.info("%s", text)

    def set_state(self, state: str) -> None:
        self.state = state
        _logger.debug("состояние: %s", state)

    # ── то, что зовёт main() ──────────────────────────────────────────────────

    def wait_for_api_key(self) -> str:
        """Проверяет ключ и НЕ блокирует.

        Мастер ввода намеренно не запускается: в headless некому отвечать, и
        вместо «висит непонятно почему» лучше внятная ошибка на старте.
        """
        key = ensure_gemini_key(self._config_path, interactive=False)
        if not key:
            raise RuntimeError(
                "Нет ключа Gemini. В headless мастер не запускается — задайте "
                f"переменную GEMINI_API_KEY или пропишите ключ в {self._config_path}"
            )
        _logger.info("Ключ Gemini на месте, окна не будет — идём в голосовой круг")
        return key

    def start_text_input(self) -> None:
        """Позволяет печатать команды вместо того, чтобы говорить.

        Только при живом терминале: под перенаправленным вводом поток сразу
        упёрся бы в EOF и молча умер, а в CI это лишний висящий тред.
        """
        if not sys.stdin or not sys.stdin.isatty():
            _logger.info("Ввод текста недоступен (нет терминала) — только голос")
            return

        def читать() -> None:
            for line in sys.stdin:
                text = line.strip()
                if not text:
                    continue
                if text in ("/quit", "/exit"):
                    _logger.info("Завершение по команде из терминала")
                    os._exit(0)
                handler = self.on_text_command
                if callable(handler):
                    handler(text)
                else:
                    _logger.warning("Команда пришла, но обработчик не готов: %s", text)
            # Проверке isatty верить нельзя: в Git Bash под Windows
            # `< /dev/null` рапортует себя терминалом, и поток встаёт здесь
            # сразу. Говорим правду по факту, а не по обещанию выше.
            _logger.info("Ввод с клавиатуры закончился — дальше только голос")

        self._stdin_thread = threading.Thread(target=читать, daemon=True,
                                              name="headless-stdin")
        self._stdin_thread.start()
        _logger.info("Можно печатать команды прямо здесь. /quit — выход")
