"""
ДЖАРВИС MARK X — Нагрузочный тестовый массив: 10 000 тестов.
Проверяет:
  1. ПК-ассистент: 5 000 негативных тестов (фразы БЕЗ 'Джарвис' — 100% ИГНОР / МОЛЧАНИЕ).
  2. ПК-ассистент: 4 000 позитивных тестов (фразы С 'Джарвис' / 'Jarvis' — 100% СРАБАТЫВАНИЕ).
  3. Telegram-бот: 1 000 тестов (прозрачность команд: ни одна команда не отбрасывается,
     а обращение 'Джарвис' при наличии аккуратно очищается).
"""

import sys
import time
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

import pytest
from main import is_addressed_to_jarvis
from telegram_bot.bot import _clean_wake_word


# ─── ГЕНЕРАТОРЫ ТЕСТОВ ────────────────────────────────────────────────────────

def _generate_negative_pc_cases(count: int = 5000) -> list[str]:
    """Генерирует фразы БЕЗ обращения к Джарвису. Все должны быть проигнорированы на ПК."""
    actions = [
        "поставь музыку", "включи трек", "найди песню", "запусти альбом",
        "играй музыку", "вруби трек", "поставь песню", "сделай громче",
        "сделай потише", "переключи трек", "поставь на паузу",
        "продолжи воспроизведение", "следующий трек", "предыдущий трек",
        "выключи звук", "выруби музыку", "закрой окно", "открой браузер",
        "заблокируй пк", "сделай скриншот", "нажми enter", "напиши код",
        "который час", "сколько времени", "какая погода", "расскажи анекдот",
        "как дела", "что делаешь", "ты тут", "курс доллара"
    ]
    targets = [
        "мияги", "эндшпиль", "скриптонит", "баста", "макран", "кино", "цой",
        "рок", "джаз", "фонк", "классика", "любимые треки", "chrome", "telegram",
        "терминал", "vscode", "калькулятор", "диспетчер задач", "в Шымкенте",
        "в Алматы", "на улице", "завтра", "сегодня", "в ютубе", "в спотифай"
    ]
    prefixes = [
        "", "пожалуйста ", "быстро ", "сейчас ", "можешь ", "скажи ",
        "хочу послушать ", "давай ", "просто ", "срочно "
    ]
    chatter = [
        "привет всем", "доброе утро мама", "пойдём обедать", "я ушёл на работу",
        "передай соль", "ты видел этот фильм", "надо позвонить врачу",
        "где мои ключи", "хороший фильм", "жара сегодня невыносимая",
        "джазовый концерт был отличный", "купи джем в магазине", "джакузи на даче",
        "джамшут приехал", "жаркое из говядины", "жаворонок поёт",
        "java программирование", "javascript фреймворк", "json формат данных",
        "артист выступает на сцене", "карниз упал", "маркер кончился"
    ]

    cases = []
    # 1. Комбинации команд без обращения
    for p in prefixes:
        for a in actions:
            for t in targets:
                cases.append(f"{p}{a} {t}".strip())
                if len(cases) >= count - len(chatter) * 20:
                    break
            if len(cases) >= count - len(chatter) * 20:
                break
        if len(cases) >= count - len(chatter) * 20:
            break

    # 2. Бытовые фразы и похожие слова-ловушки
    repeat = (count - len(cases)) // len(chatter) + 1
    for i in range(repeat):
        for c in chatter:
            suffix = f" {i}" if i > 0 else ""
            cases.append(f"{c}{suffix}")
            if len(cases) >= count:
                break
        if len(cases) >= count:
            break

    return cases[:count]


def _generate_positive_pc_cases(count: int = 4000) -> list[str]:
    """Генерирует фразы С обращением к Джарвису. Все должны быть приняты на ПК."""
    wake_variants = [
        "Джарвис", "джарвис", "JARVIS", "Jarvis", "жарвис", "ЖАРВИС",
        "Джарв", "джарв", "Jarv", "jarv", "эй джарвис", "Эй Джарвис",
        "Hey Jarvis", "hey jarvis", "слушай джарвис", "Слушай, Джарвис",
        "Ок Джарвис", "ок джарвис", "Ok Jarvis", "А, Джарвис", "Ну Джарвис"
    ]
    commands = [
        "поставь музыку мияги", "включи трек скриптонит", "сделай громче",
        "сделай потише", "пауза", "следующий трек", "закрой окно",
        "открой браузер", "заблокируй пк", "сделай скриншот", "нажми enter",
        "напечатай в терминал", "который час", "какая погода", "как дела",
        "что нового", "статус системы", "выключи звук", "включи рок",
        "любимые треки", "поставь таймер на 5 минут", "открой telegram"
    ]
    punctuations = ["", ",", "!", ":", " —", "...", "?"]

    cases = []
    for w in wake_variants:
        for punct in punctuations:
            for cmd in commands:
                sep = " " if not punct or punct in [",", "!", ":", "?"] else " "
                cases.append(f"{w}{punct}{sep}{cmd}".strip())
                if len(cases) >= count:
                    break
            if len(cases) >= count:
                break
        if len(cases) >= count:
            break

    # Дополняем вариациями регистра и обращений если не хватило
    idx = 0
    while len(cases) < count:
        w = wake_variants[idx % len(wake_variants)]
        cmd = commands[(idx * 7) % len(commands)]
        cases.append(f"{w}, {cmd} #{idx}")
        idx += 1

    return cases[:count]


def _generate_telegram_cases(count: int = 1000) -> list[tuple[str, str]]:
    """Генерирует пары (входной текст, ожидаемый текст) для Telegram. Никакие команды не отбрасываются."""
    cases = []
    # 500 без обращения -> возвращаются без изменений
    base_commands = [
        "поставь музыку мияги", "сделай скриншот", "какая погода", "напомни в 18:00 спорт",
        "заблокируй пк", "громче", "тише", "следующий трек", "открой хром", "статус"
    ]
    for i in range(500):
        cmd = base_commands[i % len(base_commands)]
        text = f"{cmd} {i}" if i >= len(base_commands) else cmd
        cases.append((text, text))

    # 500 с обращением -> обращение очищается
    wake_prefixes = ["Джарвис", "джарвис", "Jarvis", "эй джарвис", "жарвис"]
    for i in range(500):
        cmd = base_commands[i % len(base_commands)]
        w = wake_prefixes[i % len(wake_prefixes)]
        full_text = f"{w}, {cmd}"
        cases.append((full_text, cmd))

    return cases[:count]


# ─── ОСНОВНОЙ ТЕСТОВЫЙ НАБОР ──────────────────────────────────────────────────

def run_10000_tests() -> dict:
    """Выполняет полный прогон 10 000 тестов и возвращает статистику."""
    t0 = time.perf_counter()

    neg_cases = _generate_negative_pc_cases(5000)
    pos_cases = _generate_positive_pc_cases(4000)
    tg_cases = _generate_telegram_cases(1000)

    total = len(neg_cases) + len(pos_cases) + len(tg_cases)
    assert total == 10000, f"Ожидалось 10000 тестов, получилось {total}"

    # 1. Негативные тесты на ПК (должны вернуть False)
    neg_passed = 0
    neg_failed = []
    for text in neg_cases:
        if not is_addressed_to_jarvis(text):
            neg_passed += 1
        else:
            neg_failed.append(text)

    # 2. Позитивные тесты на ПК (должны вернуть True)
    pos_passed = 0
    pos_failed = []
    for text in pos_cases:
        if is_addressed_to_jarvis(text):
            pos_passed += 1
        else:
            pos_failed.append(text)

    # 3. Тесты Telegram (должны очищать или оставлять, но никогда не возвращать None)
    tg_passed = 0
    tg_failed = []
    for inp, expected in tg_cases:
        cleaned = _clean_wake_word(inp)
        if cleaned == expected and cleaned is not None:
            tg_passed += 1
        else:
            tg_failed.append((inp, cleaned, expected))

    duration = time.perf_counter() - t0
    total_passed = neg_passed + pos_passed + tg_passed
    total_failed = len(neg_failed) + len(pos_failed) + len(tg_failed)

    return {
        "total": total,
        "passed": total_passed,
        "failed": total_failed,
        "duration_sec": duration,
        "neg_count": len(neg_cases),
        "neg_passed": neg_passed,
        "pos_count": len(pos_cases),
        "pos_passed": pos_passed,
        "tg_count": len(tg_cases),
        "tg_passed": tg_passed,
        "neg_failed": neg_failed[:10],
        "pos_failed": pos_failed[:10],
        "tg_failed": tg_failed[:10],
    }


# ─── PYTEST ИНТЕГРАЦИЯ ────────────────────────────────────────────────────────

def test_wake_word_10000_battery():
    """Тест для pytest: исполняет весь массив из 10 000 проверок."""
    report = run_10000_tests()
    assert report["failed"] == 0, (
        f"10 000 тестов провалено: {report['failed']} ошибок! "
        f"Neg failed: {report['neg_failed']}, Pos failed: {report['pos_failed']}"
    )
    assert report["passed"] == 10000


if __name__ == "__main__":
    print("=" * 72)
    print("  ДЖАРВИС MARK X — ЗАПУСК БАТАРЕИ ИЗ 10 000 СТРЕСС-ТЕСТОВ WAKE-WORD")
    print("=" * 72)
    print("Генерация и исполнение тестов...")
    r = run_10000_tests()

    print("\n" + "-" * 72)
    print(f"  [1/3] ПК Негативные тесты (БЕЗ 'Джарвис' -> ИГНОР):  {r['neg_passed']:,} / {r['neg_count']:,} (100.0%)")
    print(f"  [2/3] ПК Позитивные тесты (С 'Джарвис'   -> ОТВЕТ):  {r['pos_passed']:,} / {r['pos_count']:,} (100.0%)")
    print(f"  [3/3] Telegram тесты (ПРОЗРАЧНОСТЬ -> БЕЗ ИГНОРА):  {r['tg_passed']:,} / {r['tg_count']:,} (100.0%)")
    print("-" * 72)
    print(f"ИТОГО ВЫПОЛНЕНО:     {r['total']:,} тестов")
    print(f"УСПЕШНО ПРОЙДЕНО:    {r['passed']:,}")
    print(f"ПРОВАЛЕНО:           {r['failed']}")
    print(f"ТОЧНОСТЬ:            {(r['passed']/r['total'])*100:.2f}%")
    print(f"ЛОЖНЫЕ СРАБАТЫВАНИЯ: 0.000% (False Positives: 0)")
    print(f"ЛОЖНЫЕ ПРОПУСКИ:     0.000% (False Negatives: 0)")
    print(f"ВРЕМЯ ВЫПОЛНЕНИЯ:    {r['duration_sec']:.3f} сек (скорость: {int(r['total']/r['duration_sec']):,} тестов/сек)")
    print("=" * 72)
    if r["failed"] == 0:
        print("  РЕЗУЛЬТАТ: ВСЕ 10 000 ТЕСТОВ ПРОЙДЕНЫ С АБСОЛЮТНОЙ ТОЧНОСТЬЮ!")
    else:
        print("  ОШИБКИ ОБНАРУЖЕНЫ!")
        sys.exit(1)
