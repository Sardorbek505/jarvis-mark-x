"""Поиск обязан отвечать на вопрос, а не открывать браузер.

Прежняя версия умела ровно одно: DuckDuckGo Instant Answer API. Он знает
«что такое фотосинтез» и молчит про цены и вчерашние события — а на молчание
срабатывал запасной путь «открыл в браузере». Голосом заданный вопрос получал
ответ, который голосом не слушают.

Сеть здесь не трогается: разбор выдачи проверяется на сохранённой разметке,
переходы между источниками — на подменах. Живой поиск проверяется руками,
эти тесты стерегут логику вокруг него.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from actions import web_search as ws

# Снимается до фикстур: autouse-подмена ниже заменяет функцию в модуле, и
# «восстановить оригинал» через ws.__dict__ после её срабатывания уже нельзя.
_РЕАЛЬНЫЙ_РАЗБОР = ws._ddg_results

# Форма выдачи https://html.duckduckgo.com/html/ — сохранённый кусок,
# по которому писалась регулярка.
_ВЫДАЧА = """
<div class="results">
  <div class="result results_links">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=x">
        NVIDIA <b>RTX 5080</b> — цены
      </a>
    </h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">
      Средняя цена &mdash; <b>от 130&nbsp;000&#x20BD;</b> в магазинах Москвы.
    </a>
  </div>
  <div class="result results_links">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=y">Обзор карты</a>
    </h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=y">Тесты в играх.</a>
  </div>
</div>
"""


class _Окно:
    def __init__(self):
        self.лог = []

    def write_log(self, text):
        self.лог.append(text)


@pytest.fixture(autouse=True)
def без_сети(monkeypatch):
    """Ни один тест не должен уйти в интернет — это оффлайн-сьют."""
    def запрещено(*a, **kw):
        raise AssertionError("тест полез в сеть")

    monkeypatch.setattr(ws.urllib.request, "urlopen", запрещено)
    monkeypatch.setattr(ws, "_gemini_search", lambda *a, **kw: "")
    monkeypatch.setattr(ws, "_ddg_results", lambda *a, **kw: [])
    monkeypatch.setattr(ws, "_instant_answer", lambda *a, **kw: "")


# ─── Режимы ───────────────────────────────────────────────────────────────────

def test_режим_по_умолчанию_поиск():
    assert ws._normalise_mode("") == "search"
    assert ws._normalise_mode(None) == "search"


def test_неизвестный_режим_не_ломает_запрос():
    """Модель может выдумать имя режима — это не повод отказывать в поиске."""
    assert ws._normalise_mode("супер-режим") == "search"


def test_русские_названия_режимов_понимаются():
    """Модель отвечает по-русски и вполне может так же заполнить параметр."""
    assert ws._normalise_mode("новости") == "news"
    assert ws._normalise_mode("Цена") == "price"
    assert ws._normalise_mode("СРАВНЕНИЕ") == "compare"


def test_режим_меняет_инструкцию_модели():
    цена = ws._build_query("RTX 5080", "price", [], "")
    новости = ws._build_query("RTX 5080", "news", [], "")

    assert "цену" in цена and "валютой" in цена
    assert "СВЕЖИЕ" in новости
    assert цена != новости


def test_сравнение_передаёт_предметы_и_аспект():
    запрос = ws._build_query("видеокарты", "compare", ["RTX 5080", "RX 9070"], "цена")

    assert "RTX 5080" in запрос and "RX 9070" in запрос
    assert "цена" in запрос


def test_модель_просят_не_выдумывать():
    """Поиск, отвечающий по памяти, — это не поиск."""
    assert "не выдумывай" in ws._build_query("что угодно", "search", [], "").lower()


# ─── Разбор выдачи ────────────────────────────────────────────────────────────

def test_разбор_выдачи_даёт_заголовок_и_выдержку(monkeypatch):
    class _Ответ:
        def read(self):
            return _ВЫДАЧА.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ws.urllib.request, "urlopen", lambda *a, **kw: _Ответ())
    результаты = _РЕАЛЬНЫЙ_РАЗБОР("цена RTX 5080", limit=2)

    assert len(результаты) == 2
    заголовок, выдержка = результаты[0]
    assert "RTX 5080" in заголовок, "теги <b> внутри заголовка должны сниматься"
    assert "130" in выдержка
    assert "<" not in заголовок and "&" not in выдержка, "мнемоники должны раскрыться"


def test_сломанная_разметка_не_роняет_поиск(monkeypatch):
    class _Мусор:
        def read(self):
            return "<html>ничего похожего</html>".encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ws.urllib.request, "urlopen", lambda *a, **kw: _Мусор())
    assert _РЕАЛЬНЫЙ_РАЗБОР("что угодно") == []


# ─── Порядок источников ───────────────────────────────────────────────────────

def test_ответ_gemini_имеет_приоритет(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda *a, **kw: "Ташкент, сэр.")
    monkeypatch.setattr(ws, "_ddg_results", lambda *a, **kw: [("не должно", "сюда дойти")])

    assert ws.web_search({"query": "столица Узбекистана"}) == "Ташкент, сэр."


def test_без_gemini_идём_в_выдачу(monkeypatch):
    monkeypatch.setattr(ws, "_ddg_results",
                        lambda *a, **kw: [("NVIDIA RTX 5080", "от 130 000 рублей")])

    ответ = ws.web_search({"query": "цена RTX 5080", "mode": "price"})

    assert "130 000" in ответ
    assert "браузер" not in ответ.lower()


def test_справочник_последний_источник_фактов(monkeypatch):
    monkeypatch.setattr(ws, "_instant_answer", lambda *a, **kw: "Процесс в растениях.")

    ответ = ws.web_search({"query": "фотосинтез"})

    assert "Процесс в растениях." in ответ


def test_браузер_не_выдаётся_за_ответ(monkeypatch):
    """Раньше «открыл поиск в браузере» приходило как успешный результат."""
    открыто = []
    monkeypatch.setattr(ws, "_open_in_browser",
                        lambda q: открыто.append(q) or "Сэр, сам ответа не нашёл — открыл поиск.")

    ответ = ws.web_search({"query": "что-то очень редкое"})

    assert открыто == ["что-то очень редкое"]
    assert "не нашёл" in ответ, "ответ обязан признавать, что вопрос остался без ответа"


def test_пустой_запрос_не_ходит_никуда():
    assert "Укажите" in ws.web_search({"query": "   "})


# ─── Пригодность для произнесения ─────────────────────────────────────────────

def test_длинный_ответ_режется_по_предложению():
    длинный = "Довольно длинное предложение о видеокартах. " * 40
    коротко = ws._shorten(длинный)

    assert len(коротко) <= ws._MAX_SPOKEN
    assert коротко.endswith("."), "оборванная на полуслове фраза вслух звучит как сбой"


def test_короткий_ответ_не_трогаем():
    assert ws._shorten("Ташкент, сэр.") == "Ташкент, сэр."


def test_перенос_строк_схлопывается():
    """Переводы строк в ответе модели вслух превращаются в паузы невпопад."""
    assert ws._shorten("Первое.\n\n  Второе.") == "Первое. Второе."


def test_строка_вместо_списка_в_items_не_ломает(monkeypatch):
    """Модель нередко кладёт в ARRAY одну строку."""
    monkeypatch.setattr(ws, "_gemini_search",
                        lambda q, m, items, a: f"получено {len(items)}")

    assert ws.web_search({"query": "x", "mode": "compare", "items": "RTX 5080"}) == "получено 1"


def test_запрос_попадает_в_лог_интерфейса(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda *a, **kw: "ответ")
    окно = _Окно()

    ws.web_search({"query": "погода", "mode": "news"}, player=окно)

    assert окно.лог and "погода" in окно.лог[0] and "news" in окно.лог[0]
