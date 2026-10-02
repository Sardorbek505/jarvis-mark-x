"""Голос и чат без кодов: ссылки, пути, исключения, имена инструментов — не вслух."""
from core.speech_text import for_chat, for_speech, short_reason


def test_speech_drops_technical_bits():
    assert for_speech("Открыл https://youtube.com/watch?v=abc, сэр.") == "Открыл ссылка, сэр."
    assert for_speech(r"Файл C:\Users\Sardor\a.txt удалён.") == "Файл удалён."
    assert for_speech("Вызвал web_search и нашёл.") == "Вызвал и нашёл."
    assert "Error" not in for_speech("Не вышло: ConnectionResetError: [WinError 10054] хост")
    assert for_speech("<ctrl46>Привет, сэр. ") == "Привет, сэр. "
    assert for_speech("Код: ```python\nprint(1)\n``` готово") == "Код: готово"
    assert for_speech("Сделал **важное**: 3 задачи.") == "Сделал важное: 3 задачи."


def test_plain_speech_untouched_and_fragment_edges_kept():
    assert for_speech("Погода в Ташкенте +18, ясно.") == "Погода в Ташкенте +18, ясно."
    assert for_speech("Привет,") == "Привет,"                 # по запятой режется озвучка
    assert for_speech(" сэр.") == " сэр."
    assert for_speech("") == "" and for_speech("`` ") == ""


def test_chat_keeps_links_but_not_markup():
    assert for_chat("**Готово**: `pip install x` https://a.b") == "Готово: pip install x https://a.b"


def test_short_reason():
    assert short_reason(TimeoutError("timed out")) == "не ответило вовремя"
    assert short_reason("429 RESOURCE_EXHAUSTED") == "слишком много запросов, нужна пауза"
    assert short_reason(ValueError("x")) == "что-то пошло не так"


def test_quick_reply_hides_raw_error():
    from core import quick
    q = quick.Quick.__new__(quick.Quick) if hasattr(quick, "Quick") else None
    if q is None:
        return
    q.tool, q.reply = "open_app", "Открыл, сэр."
    text = quick.reply_for(q, "Ошибка: нет связи")
    assert text == "Не получилось, сэр: нет связи"
