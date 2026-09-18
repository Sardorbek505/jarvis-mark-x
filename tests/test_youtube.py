"""YouTube: пересказать ролик, не открывая его.

Двадцать минут ради одной мысли — обычное дело, а субтитры есть почти у всех
роликов. Это единственное здесь, чего нельзя сделать руками за то же время:
включить видео человек и сам умеет.

Сеть не трогается: расшифровка и oEmbed подменяются, проверяется разбор ссылок
и то, что причина неудачи называется прямо.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from actions import youtube_video as yt


@pytest.fixture(autouse=True)
def без_сети(monkeypatch):
    monkeypatch.setattr(yt, "_open", lambda url: True)
    monkeypatch.setattr(yt, "_oembed", lambda vid: {})
    monkeypatch.setattr(yt, "_transcript", lambda vid: ("", "подменено"))
    monkeypatch.setattr(yt, "_summarise", lambda t, n: "")


# ─── Разбор ссылок ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ссылка", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?t=42",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "https://www.youtube.com/live/dQw4w9WgXcQ",
    "посмотри https://youtu.be/dQw4w9WgXcQ вот это",
    "dQw4w9WgXcQ",
])
def test_идентификатор_достаётся_из_любой_формы(ссылка):
    assert yt._video_id(ссылка) == "dQw4w9WgXcQ"


def test_короткие_ролики_тоже():
    assert yt._video_id("https://www.youtube.com/shorts/abcdefghijk") == "abcdefghijk"


@pytest.mark.parametrize("не_ссылка", ["просто текст", "", "https://example.com/video", "котики"])
def test_не_ссылка_не_превращается_в_ролик(не_ссылка):
    assert yt._video_id(не_ссылка) == ""


# ─── Воспроизведение ──────────────────────────────────────────────────────────

def test_ссылка_открывается_роликом(monkeypatch):
    открыто = []
    monkeypatch.setattr(yt, "_open", lambda url: открыто.append(url) or True)

    ответ = yt.youtube_video({"query": "https://youtu.be/dQw4w9WgXcQ"})

    assert открыто == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
    assert "Включил" in ответ


def test_название_открывает_поиск(monkeypatch):
    открыто = []
    monkeypatch.setattr(yt, "_open", lambda url: открыто.append(url) or True)

    ответ = yt.youtube_video({"query": "уроки питона"})

    assert "results?search_query=" in открыто[0]
    assert "выберите" in ответ, "поиск — это не «включил», и говорить надо иначе"


def test_пустой_запрос_переспрашивают():
    assert "Что включить" in yt.youtube_video({})


def test_несработавший_браузер_не_выдаётся_за_успех(monkeypatch):
    monkeypatch.setattr(yt, "_open", lambda url: False)
    assert "Не смог" in yt.youtube_video({"query": "что-нибудь"})


# ─── Пересказ ─────────────────────────────────────────────────────────────────

def test_пересказ_по_субтитрам(monkeypatch):
    monkeypatch.setattr(yt, "_oembed", lambda vid: {"title": "Как работает GPS"})
    monkeypatch.setattr(yt, "_transcript", lambda vid: ("длинная расшифровка", "ru"))
    monkeypatch.setattr(yt, "_summarise", lambda t, n: "Сэр, речь о спутниках.")

    ответ = yt.youtube_video({"action": "summarize", "url": "https://youtu.be/dQw4w9WgXcQ"})

    assert "Как работает GPS" in ответ
    assert "спутниках" in ответ


def test_без_ссылки_пересказывать_нечего():
    ответ = yt.youtube_video({"action": "summarize", "query": "какое-то видео"})
    assert "ссылку" in ответ


@pytest.mark.parametrize("причина", [
    "у этого ролика нет субтитров",
    "ролик недоступен",
    "YouTube не отдаёт субтитры с этого адреса",
])
def test_причина_неудачи_называется_прямо(monkeypatch, причина):
    """«Нет субтитров» и «ролик недоступен» требуют от человека разных действий."""
    monkeypatch.setattr(yt, "_transcript", lambda vid: ("", причина))

    ответ = yt.youtube_video({"action": "summarize", "url": "https://youtu.be/dQw4w9WgXcQ"})

    assert причина in ответ


def test_без_модели_отдаём_начало_расшифровки(monkeypatch):
    """Хуже пересказа, но честнее молчания: человек узнает, о чём там речь."""
    monkeypatch.setattr(yt, "_transcript",
                        lambda vid: ("Сегодня поговорим о спутниках " * 40, "ru"))
    monkeypatch.setattr(yt, "_summarise", lambda t, n: "")

    ответ = yt.youtube_video({"action": "summarize", "url": "https://youtu.be/dQw4w9WgXcQ"})

    assert "модель недоступна" in ответ
    assert "спутниках" in ответ


def test_расшифровка_обрезается_до_разумного(monkeypatch):
    """Часовой ролик — это сорок тысяч знаков; отдавать их модели незачем."""
    class _Отрывок:
        def __init__(self, text):
            self.text = text

    class _Api:
        def fetch(self, vid, languages=None):
            return [_Отрывок("слово " * 100) for _ in range(500)]

    monkeypatch.setitem(
        sys.modules, "youtube_transcript_api",
        type("m", (), {"YouTubeTranscriptApi": _Api}),
    )
    текст, _ = yt.__dict__["_transcript"]("dQw4w9WgXcQ")

    assert len(текст) <= yt._MAX_TRANSCRIPT


# ─── Справка о ролике ─────────────────────────────────────────────────────────

def test_название_и_канал(monkeypatch):
    monkeypatch.setattr(yt, "_oembed",
                        lambda vid: {"title": "Как работает GPS", "author_name": "Наука"})

    ответ = yt.youtube_video({"action": "get_info", "url": "https://youtu.be/dQw4w9WgXcQ"})

    assert "Как работает GPS" in ответ and "Наука" in ответ


def test_чего_не_знаем_о_том_не_врём(monkeypatch):
    """oEmbed не отдаёт ни длительность, ни просмотры — и выдумывать их нельзя."""
    monkeypatch.setattr(yt, "_oembed", lambda vid: {"title": "Ролик"})

    ответ = yt.youtube_video({"action": "get_info", "url": "https://youtu.be/dQw4w9WgXcQ"})

    assert "не могу" in ответ


def test_молчащий_oembed_не_придумывает_название():
    ответ = yt.youtube_video({"action": "get_info", "url": "https://youtu.be/dQw4w9WgXcQ"})
    assert "не ответил" in ответ


# ─── Границы ──────────────────────────────────────────────────────────────────

def test_тренды_честно_отправляют_к_поиску():
    """Выскребать главную страницу — значит ломаться молча при её обновлении."""
    ответ = yt.youtube_video({"action": "trending"})

    assert "не умею" in ответ
    assert "поиск" in ответ.lower()


def test_инструмент_подхвачен_реестром():
    import main

    assert main._ACTIONS.has("youtube_video")


def test_описание_разводит_с_плеерами():
    описание = yt.TOOL["description"]

    assert "movie_player" in описание and "music_player" in описание
