"""Автоматизируемый браузер: нажать, заполнить, прочитать.

Простое «открой сайт» запускает браузер пользователя — с его профилем и
залогиненными аккаунтами. Но нажать на кнопку и прочитать текст в чужом окне
нельзя: браузер не даёт собой управлять снаружи. Для этого поднимается
отдельное окно под Playwright.

Единственная часть проекта, которую в этом контейнере можно проверить
по-настоящему: Chromium здесь есть. Страницы поднимаются локальные — в сеть
тесты не ходят.
"""

import sys
import threading
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import browser_session as bs

pytest.importorskip("playwright", reason="Playwright не установлен")

_СТРАНИЦА = """
<html><body>
  <h1>Заголовок страницы</h1>
  <p id="текст">Здесь лежит содержимое, которое надо прочитать.</p>
  <input id="поле" aria-label="Поиск">
  <button id="кнопка" onclick="document.getElementById('текст').innerText='нажато'">
    Отправить
  </button>
</body></html>
"""


@pytest.fixture(scope="module")
def браузер():
    """Одна сессия на модуль: запуск Chromium — самая долгая часть."""
    try:
        s = bs.session(headless=True)
    except bs.BrowserUnavailable as exc:
        pytest.skip(f"браузер не поднялся: {exc}")
    yield s
    bs.close()


@pytest.fixture
def страница(браузер):
    """Чистая локальная страница перед каждым тестом.

    Сессию берём заново, а не через аргумент фикстуры: тесты закрытия ниже
    гасят её сознательно, и следующий тест должен получить живую, а не
    ссылку на закрытую."""
    живая = bs.session(headless=True)
    живая._call(lambda page: page.set_content(_СТРАНИЦА))
    return живая


# ─── Чтение ───────────────────────────────────────────────────────────────────

def test_читает_текст_со_страницы(страница):
    assert "Заголовок страницы" in страница.text("h1")


def test_читает_по_селектору(страница):
    assert "содержимое" in страница.text("#текст")


def test_текст_обрезается(страница):
    assert len(страница.text("body", предел=10)) <= 10


def test_отсутствующий_элемент_не_вешает_ход(страница):
    """Молча зависший голосовой ход хуже внятного отказа."""
    with pytest.raises(Exception) as беда:
        страница.text("#такого-нет")

    assert "Timeout" in str(беда.value) or "not" in str(беда.value).lower()


# ─── Ввод и нажатия ───────────────────────────────────────────────────────────

def test_заполняет_поле_по_селектору(страница):
    страница.fill("#поле", "запрос")
    assert страница._call(lambda page: page.input_value("#поле")) == "запрос"


def test_заполняет_поле_по_подписи(страница):
    """Человек голосом называет подпись, а не CSS-селектор."""
    страница.fill_by_label("Поиск", "по подписи")
    assert страница._call(lambda page: page.input_value("#поле")) == "по подписи"


def test_нажимает_по_селектору(страница):
    страница.click("#кнопка")
    assert страница.text("#текст") == "нажато"


def test_нажимает_по_видимой_надписи(страница):
    """«Нажми Отправить» — так это и звучит вслух."""
    страница.click_text("Отправить")
    assert страница.text("#текст") == "нажато"


def test_клавиша_доходит_до_страницы(страница):
    страница.fill("#поле", "текст")
    страница.press("Enter")   # не должно бросить исключение


# ─── Навигация ────────────────────────────────────────────────────────────────

def test_переход_возвращает_заголовок(браузер):
    # charset обязателен: без него браузер читает data:-адрес как latin-1 и
    # кириллица в заголовке превращается в кракозябры. У настоящих страниц
    # кодировка объявлена, так что это особенность именно такого адреса.
    заголовок = браузер.goto(
        "data:text/html;charset=utf-8,<title>Моя страница</title><h1>Тут</h1>")
    assert "Моя страница" in заголовок


def test_история_ходит_назад_и_вперёд(браузер):
    браузер.goto("data:text/html;charset=utf-8,<title>Первая</title>")
    браузер.goto("data:text/html;charset=utf-8,<title>Вторая</title>")

    браузер.back()
    assert "Первая" in браузер.title()

    браузер.forward()
    assert "Вторая" in браузер.title()


def test_прокрутка_не_падает_на_короткой_странице(страница):
    страница.scroll(вниз=True)
    страница.scroll(вниз=False)


def test_снимок_страницы_сохраняется(страница, tmp_path):
    путь = str(tmp_path / "снимок.png")
    страница.screenshot(путь)

    assert Path(путь).exists() and Path(путь).stat().st_size > 0


# ─── Потоки ───────────────────────────────────────────────────────────────────

def test_работает_из_чужого_потока(страница):
    """Ровно то, ради чего команды идут очередью: синхронный API Playwright
    привязан к потоку, в котором создан, а действия выполняются в
    произвольных потоках executor'а — каждый раз в разных."""
    итог = []
    беда = []

    def из_другого_потока():
        try:
            итог.append(страница.text("h1"))
        except Exception as exc:
            беда.append(exc)

    поток = threading.Thread(target=из_другого_потока)
    поток.start()
    поток.join(timeout=30)

    assert not беда, f"из чужого потока не вышло: {беда}"
    assert "Заголовок страницы" in итог[0]


def test_несколько_потоков_подряд(страница):
    результаты = []
    потоки = [
        threading.Thread(target=lambda: результаты.append(страница.title()))
        for _ in range(4)
    ]
    for п in потоки:
        п.start()
    for п in потоки:
        п.join(timeout=30)

    assert len(результаты) == 4


# ─── Сессия ───────────────────────────────────────────────────────────────────

def test_сессия_одна_на_приложение(браузер):
    """Второе автоматизируемое окно почти всегда значит, что человек не
    заметил первое: команды уходят не туда."""
    assert bs.session(headless=True) is браузер


def test_закрытие_освобождает_сессию():
    bs.session(headless=True)
    assert bs.is_open()

    bs.close()
    assert not bs.is_open()


def test_обращение_к_закрытой_сессии_внятно_отказывает():
    s = bs.session(headless=True)
    bs.close()

    with pytest.raises(bs.BrowserUnavailable):
        s.text("h1")


def test_без_playwright_ошибка_объясняет_что_делать(monkeypatch):
    """Не «что-то пошло не так», а «поставьте пакет»."""
    настоящий = __import__

    def без_playwright(имя, *a, **kw):
        if имя.startswith("playwright"):
            raise ImportError("нет модуля")
        return настоящий(имя, *a, **kw)

    import builtins
    monkeypatch.setattr(builtins, "__import__", без_playwright)
    bs.close()

    with pytest.raises(bs.BrowserUnavailable) as беда:
        bs.session(headless=True)

    assert "playwright" in str(беда.value)


# ─── Инструмент целиком ───────────────────────────────────────────────────────

def test_простое_открытие_идёт_в_браузер_пользователя(monkeypatch):
    """Его профиль, его вкладки, его залогиненные аккаунты — так и надо."""
    from actions import browser_control as bc

    открыто = []
    monkeypatch.setattr(bc, "_open_url", lambda url, br=None: открыто.append(url))

    ответ = bc.browser_control({"action": "go_to", "url": "example.com"})

    assert открыто == ["https://example.com"]
    assert "Открываю" in ответ


def test_интерактивное_действие_не_трогает_браузер_пользователя(monkeypatch):
    """Нажимать кнопки в окне, где открыт банк, — не та цена за удобство."""
    from actions import browser_control as bc

    monkeypatch.setattr(bc, "_open_url",
                        lambda *a, **kw: pytest.fail("полезли в браузер пользователя"))
    bc.browser_control({"action": "close"})


def test_нажатие_по_надписи_в_настоящем_браузере(страница):
    from actions import browser_control as bc

    ответ = bc.browser_control({"action": "click", "description": "Отправить"})

    assert "Нажал" in ответ
    assert страница.text("#текст") == "нажато"


def test_заполнение_по_подписи_в_настоящем_браузере(страница):
    from actions import browser_control as bc

    ответ = bc.browser_control({"action": "type", "description": "Поиск", "text": "запрос"})

    assert "Заполнил" in ответ
    assert страница._call(lambda page: page.input_value("#поле")) == "запрос"


def test_чтение_страницы_через_инструмент(страница):
    from actions import browser_control as bc

    ответ = bc.browser_control({"action": "get_text", "selector": "h1"})

    assert "Заголовок страницы" in ответ


def test_ненайденный_элемент_объясняется_человечески(страница):
    """«Ошибка браузера» человеку ничего не даёт — он должен назвать иначе."""
    from actions import browser_control as bc

    ответ = bc.browser_control({"action": "click", "description": "Такой кнопки нет"})

    assert "Не нашёл" in ответ
    assert "назовите иначе" in ответ.lower()


def test_нажатие_без_указания_цели_переспрашивают(страница):
    from actions import browser_control as bc

    assert "На что нажать" in bc.browser_control({"action": "click"})


def test_опасный_адрес_не_открывается(страница):
    """file:// и javascript: остаются отклонёнными и в автоматизируемом окне."""
    from actions import browser_control as bc

    ответ = bc.browser_control({"action": "go_to", "url": "javascript:alert(1)"})

    assert "не стану" in ответ or "Открываю" not in ответ


# ─── Управляемое окно и адреса своей сети ────────────────────────────────────
#
# Разница с браузером человека здесь принципиальная. Когда он просит открыть
# роутер, страница появляется у него на экране, и дальше решает он. А
# управляемое окно умеет `get_text`: что бы там ни открылось, текст уедет в
# модель — а модель читает то, что пишут посторонние: расшифровки роликов,
# тексты сайтов, содержимое файлов.

from actions import browser_control as bc  # noqa: E402


@pytest.mark.parametrize("адрес", [
    "http://192.168.1.1/",
    "http://10.0.0.5/admin",
    "http://172.16.0.1/",
    "http://127.0.0.1:8787/",
    "http://[::1]/",
    "http://0.0.0.0/",
    "http://169.254.169.254/latest/meta-data/",   # метаданные облака
    "http://nas.local/",
    "http://localhost:3000",
    "http://printer.home.arpa/",
])
def test_адреса_своей_сети_узнаются(адрес):
    assert bc._адрес_своей_сети(адрес) is True, адрес


@pytest.mark.parametrize("адрес", [
    "https://github.com/",
    "https://ya.ru/поиск?q=1",
    "https://8.8.8.8/",
    "http://example.org:8080/path",
])
def test_внешние_адреса_не_трогаются(адрес):
    assert bc._адрес_своей_сети(адрес) is False, адрес


def test_неразобранный_адрес_считается_опасным():
    """Не понял, куда ведёт, — значит не открываю."""
    assert bc._адрес_своей_сети("не адрес вовсе") is True
    assert bc._адрес_своей_сети("") is True


@pytest.mark.parametrize("действие", ["click", "fill", "screenshot", "reload"])
def test_управляемое_окно_отказывается_и_объясняет(monkeypatch, действие):
    """Отказ должен называть путь, которым это всё-таки можно сделать."""
    monkeypatch.setattr(bc, "_is_safe_url", lambda url: True)

    ответ = bc.browser_control({"action": действие, "url": "http://192.168.1.1/",
                                "description": "Вход", "text": "что-то"})

    assert "вашем браузере" in ответ, ответ


def test_чтение_страницы_адрес_не_меняет(monkeypatch):
    """`get_text` и `get_url` читают ТО, что уже открыто, и на адрес в
    параметрах не смотрят вовсе — иначе «прочитай страницу» само бы и
    уводило окно куда попало."""
    переходы = []

    class _Страница:
        def goto(self, url):
            переходы.append(url)
            return "заголовок"

        def text(self):
            return "содержимое"

    monkeypatch.setattr(bc, "_is_safe_url", lambda url: True)
    monkeypatch.setattr("core.browser_session.session", lambda *a, **k: _Страница())

    bc.browser_control({"action": "get_text", "url": "https://example.org/"})

    assert переходы == []


def test_в_браузер_человека_свой_адрес_открывается(monkeypatch):
    """Ограничение не должно мешать «открой роутер»: там страницу видит
    человек, и в модель ничего не уезжает."""
    открытое = []
    monkeypatch.setattr(bc, "_open_url", lambda url, browser=None: открытое.append(url))

    bc.browser_control({"action": "go_to", "url": "http://192.168.1.1/"})

    assert открытое == ["http://192.168.1.1/"]
