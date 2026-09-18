"""
Автоматизируемый браузер для голосовых команд.

ЗАЧЕМ ОТДЕЛЬНЫЙ БРАУЗЕР
    Простое «открой сайт» запускает БРАУЗЕР ПОЛЬЗОВАТЕЛЯ — с его профилем,
    закладками и залогиненными аккаунтами. Так и надо: человек просил открыть,
    а не «показать страницу в чистом окне без его почты».

    Но нажать на кнопку, заполнить форму и прочитать текст со страницы в чужом
    окне нельзя: браузер не даёт собой управлять снаружи. Для этого поднимается
    отдельное окно под управлением Playwright. Разделение сознательное: то, что
    человек сделает сам за секунду, идёт в его браузер; то, чего он голосом
    иначе не сделает, — в автоматизируемый.

ПОЧЕМУ ОТДЕЛЬНЫЙ ПОТОК
    Синхронный API Playwright привязан к потоку, в котором создан: обратиться
    к нему из другого — это `Error: It looks like you are using Playwright Sync
    API inside the asyncio loop` или молчаливая порча состояния. А наши
    действия выполняются в произвольных потоках executor'а, каждый раз в
    разных.

    Поэтому сессия живёт в одном своём потоке, а команды приходят к нему
    очередью. Вызывающий ждёт ответа — снаружи это обычный синхронный вызов.

ЧЕГО ЗДЕСЬ НЕТ
    Ни паролей, ни профиля пользователя. Автоматизируемое окно каждый раз
    чистое: доверять модели, разбирающей речь из комнаты, нажимать кнопки в
    сессии, где открыт банк, — не та цена за удобство.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("jarvis.browser")

# Сколько ждём ответа от браузера. Загрузка страницы бывает долгой, но
# бесконечное ожидание означает молча зависший голосовой ход.
_TIMEOUT_SEC = 45

# Сколько ждать появления элемента, прежде чем сказать «не нашёл».
_ELEMENT_TIMEOUT_MS = 8000

# Где лежит Chromium, если Playwright не нашёл его сам (так бывает в
# контейнерах, где браузеры ставят отдельно от пакета).
_CHROMIUM_HINTS = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
)


class BrowserUnavailable(RuntimeError):
    """Playwright не установлен или браузер не запускается."""


class _Session:
    """Браузер в собственном потоке. Наружу — обычные синхронные методы."""

    def __init__(self, headless: bool = False):
        self._headless = headless
        self._commands: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: str = ""
        self._page = None

    # ── Жизненный цикл ────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._error = ""
        self._thread = threading.Thread(target=self._loop, name="BrowserSession", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=_TIMEOUT_SEC):
            raise BrowserUnavailable("браузер не поднялся за отведённое время")
        if self._error:
            raise BrowserUnavailable(self._error)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._error)

    def stop(self) -> None:
        if not (self._thread and self._thread.is_alive()):
            return
        try:
            self._call(None)          # None — сигнал к завершению
        except Exception as exc:
            logger.debug("Браузер закрылся не по-хорошему: %s", exc)
        self._thread = None

    # ── Передача команд ───────────────────────────────────────────────────────

    def _call(self, работа: Callable[[Any], Any] | None) -> Any:
        """Отдаёт работу потоку браузера и ждёт результат."""
        if работа is not None and not self.alive:
            raise BrowserUnavailable("сессия браузера закрыта")

        ответ: queue.Queue = queue.Queue(maxsize=1)
        self._commands.put((работа, ответ))
        try:
            вид, значение = ответ.get(timeout=_TIMEOUT_SEC)
        except queue.Empty:
            raise BrowserUnavailable("браузер не ответил — страница слишком долго грузится")
        if вид == "error":
            raise значение
        return значение

    def _loop(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._error = "playwright не установлен (pip install playwright)"
            self._ready.set()
            return

        playwright = браузер = None
        try:
            playwright = sync_playwright().start()
            браузер = self._launch(playwright)
            self._page = браузер.new_page()
            self._ready.set()
        except Exception as exc:
            self._error = f"браузер не запустился: {str(exc)[:120]}"
            self._ready.set()
            if playwright:
                try:
                    playwright.stop()
                except Exception:
                    pass
            return

        while True:
            работа, ответ = self._commands.get()
            if работа is None:
                ответ.put(("ok", None))
                break
            try:
                ответ.put(("ok", работа(self._page)))
            except Exception as exc:
                ответ.put(("error", exc))

        for закрыть in (браузер.close, playwright.stop):
            try:
                закрыть()
            except Exception as exc:
                logger.debug("Закрытие браузера: %s", exc)
        self._page = None

    def _launch(self, playwright):
        """Запускает Chromium. Явный путь — для сред, где браузеры ставят
        отдельно от пакета и Playwright их не находит."""
        import os

        try:
            return playwright.chromium.launch(headless=self._headless)
        except Exception as первая:
            for путь in _CHROMIUM_HINTS:
                if os.path.exists(путь):
                    return playwright.chromium.launch(
                        headless=self._headless, executable_path=путь)
            raise первая

    # ── Действия ──────────────────────────────────────────────────────────────

    def goto(self, url: str) -> str:
        def работа(page):
            page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT_SEC * 1000)
            return page.title() or url
        return self._call(работа)

    def click(self, selector: str) -> None:
        self._call(lambda page: page.click(selector, timeout=_ELEMENT_TIMEOUT_MS))

    def click_text(self, текст: str) -> None:
        """Нажать по видимой надписи — так человек и описывает кнопку голосом,
        селекторов он не знает."""
        def работа(page):
            page.get_by_text(текст, exact=False).first.click(timeout=_ELEMENT_TIMEOUT_MS)
        self._call(работа)

    def fill(self, selector: str, текст: str) -> None:
        self._call(lambda page: page.fill(selector, текст, timeout=_ELEMENT_TIMEOUT_MS))

    def fill_by_label(self, подпись: str, текст: str) -> None:
        def работа(page):
            page.get_by_label(подпись, exact=False).first.fill(
                текст, timeout=_ELEMENT_TIMEOUT_MS)
        self._call(работа)

    def press(self, клавиша: str) -> None:
        self._call(lambda page: page.keyboard.press(клавиша))

    def text(self, selector: str = "body", предел: int = 2000) -> str:
        def работа(page):
            return (page.inner_text(selector, timeout=_ELEMENT_TIMEOUT_MS) or "")[:предел]
        return self._call(работа)

    def url(self) -> str:
        return self._call(lambda page: page.url)

    def title(self) -> str:
        return self._call(lambda page: page.title())

    def scroll(self, вниз: bool = True, пикселей: int = 600) -> None:
        шаг = пикселей if вниз else -пикселей
        self._call(lambda page: page.mouse.wheel(0, шаг))

    def back(self) -> None:
        self._call(lambda page: page.go_back(timeout=_TIMEOUT_SEC * 1000))

    def forward(self) -> None:
        self._call(lambda page: page.go_forward(timeout=_TIMEOUT_SEC * 1000))

    def reload(self) -> None:
        self._call(lambda page: page.reload(timeout=_TIMEOUT_SEC * 1000))

    def screenshot(self, путь: str) -> str:
        self._call(lambda page: page.screenshot(path=путь, full_page=False))
        return путь


# ─── Единственная сессия на приложение ────────────────────────────────────────
# Второе автоматизируемое окно почти всегда означает, что человек не заметил
# первое: команды уходят не туда, и понять это по ответу нельзя.

_session: _Session | None = None
_lock = threading.Lock()


def session(headless: bool = False) -> _Session:
    """Живая сессия. Поднимает браузер при первом обращении."""
    global _session
    with _lock:
        if _session is None or not _session.alive:
            _session = _Session(headless=headless)
            _session.start()
        return _session


def is_open() -> bool:
    with _lock:
        return _session is not None and _session.alive


def close() -> None:
    global _session
    with _lock:
        if _session is not None:
            _session.stop()
            _session = None
