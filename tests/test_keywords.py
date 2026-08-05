"""Маршрутизация команд ПК: ключ ловится по началу слова, не подстрокой.

Регрессия 20.06.2026: «чиСТОПлотный» содержал «стоп», личное сообщение ушло
на компьютер, и Джарвис ответил «🖥 Готово» вместо разговора.
"""
from telegram_bot import keywords

PC_WORDS = ["стоп", "включи", "камер", "скриншот", "открой", "погод"]


def test_keyword_inside_another_word_is_not_a_command():
    # Arrange / Act / Assert — тот самый случай из переписки
    text = "Высокий накаченный обеспеченный и чистоплотный и в машине мерседес"
    assert keywords.matches(text, PC_WORDS) is False


def test_real_commands_still_match():
    for text in ("стоп", "Стоп музыку", "включи музыку", "сделай скриншот",
                 "открой браузер", "какая погода"):
        assert keywords.matches(text, PC_WORDS) is True, text


def test_russian_endings_still_match():
    # морфология: ключ-префикс обязан ловить формы слова
    for text in ("покажи камеру", "фото с камерой", "погода на завтра"):
        assert keywords.matches(text, PC_WORDS) is True, text


def test_other_inner_matches_are_rejected():
    # слово содержит ключ, но не начинается с него
    for text in ("нескучный вечер", "прескриншотный", "заоткрой"):
        assert keywords.matches(text, PC_WORDS) is False, text


def test_empty_text_is_not_a_command():
    assert keywords.matches("", PC_WORDS) is False
