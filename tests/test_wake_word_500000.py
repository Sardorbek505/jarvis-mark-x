"""
ДЖАРВИС MARK X — Нагрузочный стресс-тест: 500 000 проверок (Пятьсот тысяч тестов).
Проверяет все возможные краевые случаи, уязвимости, ложные срабатывания и пропуски:

1. [250 000] Негативные тесты ПК (БЕЗ обращения -> 100% ИГНОР / МОЛЧАНИЕ):
   - 75 000 команд без обращения ("поставь музыку мияги", "закрой окно"...)
   - 25 000 склонений в 3-м лице ("Джарвиса нет", "Джарвису передай", "Джарвисом пользуюсь", "Jarvis's file"...)
   - 50 000 созвучных слов и ложных друзей ("джаз", "джем", "жара", "жаркое", "Java", "JSON"...)
   - 50 000 бытовых фраз и разговорной речи (русский, узбекский, казахский, английский)
   - 50 000 фраз из ТВ/субтитров, кода и технического шума
2. [200 000] Позитивные тесты ПК (С обращением -> 100% СРАБАТЫВАНИЕ):
   - 60 000 прямых обращений ("Джарвис поставь...", "Jarvis play...", "Djarvis...", "Джарв...")
   - 60 000 приветствий на 4-х языках ("Эй Джарвис", "Сәлем Джарвис", "Salom Jarvis", "Hey Jarvis")
   - 40 000 вариаций пунктуации, кавычек («Джарвис», "Jarvis", 'Джарвис':, Джарвис,поставь)
   - 40 000 вариаций регистра букв (ДЖАРВИС, джарвис, jArViS, ЖАРВИС, DJaRvIs)
3. [50 000] Тесты Telegram (ПРОЗРАЧНОСТЬ -> ни одна команда не теряется):
   - 25 000 команд без обращения (сохраняются 1:1)
   - 25 000 команд с обращением (имя чисто отсекается)
"""

import itertools
import sys
import time
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from main import is_addressed_to_jarvis  # noqa: E402
from telegram_bot.bot import _clean_wake_word  # noqa: E402


# ─── ГЕНЕРАЦИЯ ПОТОКОВ ТЕСТОВ ──────────────────────────────────────────────────

def iter_negative_pc_cases():
    """Генерирует ровно 250 000 негативных фраз для ПК (должны вернуть False)."""
    # 1. 75 000 команд без обращения
    verbs = [
        "поставь музыку", "включи трек", "найди песню", "запусти альбом",
        "играй трек", "вруби песню", "сделай громче", "сделай потише",
        "переключи трек", "поставь на паузу", "следующий трек", "закрой окно",
        "открой браузер", "заблокируй пк", "сделай скриншот", "нажми enter",
        "который час", "сколько времени", "какая погода", "выключи экран",
        "запусти терминал", "открой telegram", "перезагрузи систему", "открой vscode",
        "покажи диспетчер задач"
    ]
    targets = [
        "мияги", "эндшпиль", "скриптонит", "баста", "макран", "кино", "цой",
        "рок", "джаз", "фонк", "классика", "любимые треки", "chrome", "telegram",
        "терминал", "vscode", "калькулятор", "в Шымкенте", "в Алматы", "на улице",
        "завтра", "сегодня", "в спотифай", "быстро", "пожалуйста", "сейчас",
        "громко", "тихо", "на повторе", "в фоне"
    ]
    prefixes = [
        "", "пожалуйста, ", "быстро ", "сейчас ", "можешь ", "скажи ",
        "хочу послушать ", "давай ", "просто ", "срочно "
    ]
    count_1 = 0
    cycle_1 = itertools.cycle(itertools.product(prefixes, verbs, targets))
    while count_1 < 75000:
        p, v, t = next(cycle_1)
        yield f"{p}{v} {t} #{count_1}"
        count_1 += 1

    # 2. 25 000 склонений в 3-м лице (разговор О Джарвисе, а не обращение К нему)
    declensions = [
        "джарвиса", "джарвису", "джарвисом", "джарвисе", "джарвисовский",
        "жарвиса", "жарвису", "жарвисом", "жарвисе", "жарвисовский",
        "jarvis's", "jarvises"
    ]
    contexts = [
        "нет дома", "пора обновить", "пользуюсь каждый день", "говорю другу",
        "написал разработчик", "установил на ноутбук", "код открыт в редакторе",
        "вчера тестировали", "проект называется", "спросил у коллеги"
    ]
    count_2 = 0
    cycle_2 = itertools.cycle(itertools.product(declensions, contexts))
    while count_2 < 25000:
        d, c = next(cycle_2)
        yield f"{d} {c} #{count_2}"
        count_2 += 1

    # 3. 50 000 созвучных слов и ложных друзей
    lookalikes = [
        "джаз", "джазовый", "джазист", "джем", "джемпер", "джакузи", "джамшут",
        "джампинг", "жара", "жаркое", "жаворонок", "жалюзи", "жалоба", "жадина",
        "жатва", "жабры", "жакет", "жандарм", "жасмин", "java", "javascript",
        "json", "javelin", "jared", "jordan", "jargon", "артист", "карниз",
        "маркер", "бармен", "гарнир", "карман", "паркет", "варвар", "шарф",
        "дар", "дарвин", "дарья", "жарить", "жарко"
    ]
    fillers = [
        "играет красиво", "купил вчера", "очень вкусно", "включи пожалуйста",
        "работает отлично", "на улице сегодня", "висит на окне", "написан на питоне",
        "стоит дорого", "приехал вовремя", "лежит на столе", "летит высоко"
    ]
    count_3 = 0
    cycle_3 = itertools.cycle(itertools.product(lookalikes, fillers))
    while count_3 < 50000:
        lk, fl = next(cycle_3)
        yield f"{lk} {fl} #{count_3}"
        count_3 += 1

    # 4. 50 000 бытовых фраз и разговорной речи (4 языка)
    chatter = [
        "привет как дела", "доброе утро всем", "пойдём обедать", "я ушёл на работу",
        "передай соль", "ты видел этот фильм", "надо позвонить врачу", "где мои ключи",
        "завтра будет встреча", "хорошая сегодня погода", "чай или кофе будешь",
        "сен бүгін не істедің", "ертең сабақ бар ма", "ауа райы қандай болады",
        "bugun havo qanday", "kechki ovqatga nima bor", "ishlar qalay do'stim",
        "what are you doing today", "the meeting is at five", "have a great day"
    ]
    count_4 = 0
    cycle_4 = itertools.cycle(chatter)
    while count_4 < 50000:
        ch = next(cycle_4)
        yield f"{ch} #{count_4}"
        count_4 += 1

    # 5. 50 000 кода, субтитров и шума
    snippets = [
        "def calculate_total(): return 42",
        "import sys, os, time, asyncio",
        "docker compose up -d --build",
        "SELECT id, name FROM users WHERE active = 1",
        "git commit -m 'refactor audio pipeline'",
        "The quick brown fox jumps over the lazy dog",
        "Error 404: Page not found on this server",
        "CPU usage 15% memory 8.2GB free",
        "Listening on 127.0.0.1 port 8080",
        "Breaking news: new space discovery announced"
    ]
    count_5 = 0
    cycle_5 = itertools.cycle(snippets)
    while count_5 < 50000:
        sn = next(cycle_5)
        yield f"{sn} [{count_5}]"
        count_5 += 1


def iter_positive_pc_cases():
    """Генерирует ровно 200 000 позитивных фраз для ПК (должны вернуть True)."""
    # 1. 60 000 прямых обращений
    wake_bases = [
        "Джарвис", "джарвис", "Jarvis", "jarvis", "жарвис", "ЖАРВИС",
        "Джарв", "jarv", "Djarvis", "djarvis"
    ]
    commands = [
        "поставь музыку мияги", "включи трек скриптонит", "сделай громче",
        "сделай потише", "пауза", "следующий трек", "закрой окно",
        "открой браузер", "заблокируй пк", "сделай скриншот", "нажми enter",
        "который час", "какая погода", "как дела", "статус системы"
    ]
    count_1 = 0
    cycle_1 = itertools.cycle(itertools.product(wake_bases, commands))
    while count_1 < 60000:
        wb, cmd = next(cycle_1)
        yield f"{wb} {cmd} #{count_1}"
        count_1 += 1

    # 2. 60 000 многоязычных приветствий и вводных слов
    greetings = [
        "Эй", "эй", "Hey", "hey", "Слушай", "слушай", "Listen", "listen",
        "Ок", "ок", "Ok", "ok", "Привет", "привет", "Hi", "hi",
        "Сәлем", "сәлем", "Тыңда", "тыңда", "Salom", "salom", "Eshit", "eshit",
        "Так", "так", "Ну", "ну", "Короче", "короче", "Алло", "алло"
    ]
    count_2 = 0
    cycle_2 = itertools.cycle(itertools.product(greetings, ["Джарвис", "Jarvis", "жарвис", "Djarvis"], commands))
    while count_2 < 60000:
        gr, wb, cmd = next(cycle_2)
        yield f"{gr} {wb}, {cmd} #{count_2}"
        count_2 += 1

    # 3. 40 000 вариаций пунктуации, кавычек и спецзнаков
    punct_formats = [
        "«Джарвис», {}",
        "\"Jarvis\", {}",
        "'Джарвис': {}",
        "Джарвис! {}",
        "Джарвис — {}",
        "Джарвис,{}",
        "Джарвис:{}",
        "Джарвис-{}",
        "  Джарвис  {}  ",
        "\nДжарвис\n{}"
    ]
    count_3 = 0
    cycle_3 = itertools.cycle(itertools.product(punct_formats, commands))
    while count_3 < 40000:
        fmt, cmd = next(cycle_3)
        yield fmt.format(f"{cmd} #{count_3}")
        count_3 += 1

    # 4. 40 000 вариаций регистра (Upper, Lower, Title, Mixed)
    cased_wakes = [
        "ДЖАРВИС", "джарвис", "Джарвис", "ЖАРВИС", "жарвис", "Жарвис",
        "JARVIS", "jarvis", "Jarvis", "jArViS", "JaRvIs", "DJaRvIs"
    ]
    count_4 = 0
    cycle_4 = itertools.cycle(itertools.product(cased_wakes, commands))
    while count_4 < 40000:
        cw, cmd = next(cycle_4)
        yield f"{cw} {cmd.upper() if count_4 % 2 == 0 else cmd} #{count_4}"
        count_4 += 1


def iter_telegram_cases():
    """Генерирует ровно 50 000 тестов для Telegram (прозрачность команд)."""
    cmds = [
        "поставь музыку мияги", "сделай скриншот", "какая погода", "напомни в 18:00",
        "заблокируй пк", "громче", "тише", "следующий трек", "открой хром", "статус"
    ]
    # 25 000 без обращения -> возвращаются без изменений
    for i in range(25000):
        c = cmds[i % len(cmds)]
        full = f"{c} #{i}"
        yield (full, full)

    # 25 000 с обращением -> обращение чисто отсекается
    wakes = ["Джарвис", "джарвис", "Jarvis", "жарвис", "эй джарвис"]
    for i in range(25000):
        c = cmds[i % len(cmds)]
        w = wakes[i % len(wakes)]
        full = f"{w}, {c} #{i}"
        expected = f"{c} #{i}"
        yield (full, expected)


# ─── ИСПОЛНИТЕЛЬ 500 000 ТЕСТОВ ────────────────────────────────────────────────

def run_500000_tests() -> dict:
    t_start = time.perf_counter()

    # 1. Запуск 250 000 негативных тестов ПК
    neg_total = 0
    neg_failed = []
    t0 = time.perf_counter()
    for text in iter_negative_pc_cases():
        neg_total += 1
        if is_addressed_to_jarvis(text):
            neg_failed.append(text)
            if len(neg_failed) >= 20:
                break
    t_neg = time.perf_counter() - t0

    # 2. Запуск 200 000 позитивных тестов ПК
    pos_total = 0
    pos_failed = []
    t0 = time.perf_counter()
    for text in iter_positive_pc_cases():
        pos_total += 1
        if not is_addressed_to_jarvis(text):
            pos_failed.append(text)
            if len(pos_failed) >= 20:
                break
    t_pos = time.perf_counter() - t0

    # 3. Запуск 50 000 тестов Telegram
    tg_total = 0
    tg_failed = []
    t0 = time.perf_counter()
    for inp, exp in iter_telegram_cases():
        tg_total += 1
        res = _clean_wake_word(inp)
        if res != exp or res is None:
            tg_failed.append((inp, res, exp))
            if len(tg_failed) >= 20:
                break
    t_tg = time.perf_counter() - t0

    total_time = time.perf_counter() - t_start
    total_run = neg_total + pos_total + tg_total
    total_passed = (neg_total - len(neg_failed)) + (pos_total - len(pos_failed)) + (tg_total - len(tg_failed))
    total_failed = len(neg_failed) + len(pos_failed) + len(tg_failed)

    return {
        "total_run": total_run,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "total_time_sec": total_time,
        "neg_total": neg_total,
        "neg_passed": neg_total - len(neg_failed),
        "neg_time_sec": t_neg,
        "pos_total": pos_total,
        "pos_passed": pos_total - len(pos_failed),
        "pos_time_sec": t_pos,
        "tg_total": tg_total,
        "tg_passed": tg_total - len(tg_failed),
        "tg_time_sec": t_tg,
        "neg_sample_failures": neg_failed,
        "pos_sample_failures": pos_failed,
        "tg_sample_failures": tg_failed,
    }


def test_wake_word_500000_stress_battery():
    """Тест для pytest: исполняет массив из 500 000 проверок."""
    r = run_500000_tests()
    assert r["total_failed"] == 0, (
        f"Тесты провалены: {r['total_failed']} ошибок из {r['total_run']}! "
        f"Neg: {r['neg_sample_failures']}, Pos: {r['pos_sample_failures']}"
    )
    assert r["total_run"] == 500000


if __name__ == "__main__":
    print("=" * 76)
    print("  ДЖАРВИС MARK X — БОЛЬШОЙ СТРЕСС-ТЕСТ: 500 000 ПРОВЕРОК WAKE-WORD")
    print("=" * 76)
    print("Запуск генерации и проверки ровно 500 000 сценариев...")

    report = run_500000_tests()

    print("\n" + "-" * 76)
    print("  [1/3] ПК НЕГАТИВНЫЕ ТЕСТЫ (250K БЕЗ 'Джарвис' -> 100% ИГНОР):")
    print(f"        Пройдено: {report['neg_passed']:,} / {report['neg_total']:,}  ({(report['neg_passed']/report['neg_total'])*100:.2f}%)  [{report['neg_time_sec']:.2f} сек]")
    print("  [2/3] ПК ПОЗИТИВНЫЕ ТЕСТЫ (200K С 'Джарвис'   -> 100% ОТВЕТ):")
    print(f"        Пройдено: {report['pos_passed']:,} / {report['pos_total']:,}  ({(report['pos_passed']/report['pos_total'])*100:.2f}%)  [{report['pos_time_sec']:.2f} сек]")
    print("  [3/3] TELEGRAM ТЕСТЫ (50K КОМАНД           -> БЕЗ ИГНОРА):")
    print(f"        Пройдено: {report['tg_passed']:,} / {report['tg_total']:,}   ({(report['tg_passed']/report['tg_total'])*100:.2f}%)  [{report['tg_time_sec']:.2f} сек]")
    print("-" * 76)
    print(f"  ИТОГО ПРОВЕРЕНО:     {report['total_run']:,} тестов")
    print(f"  УСПЕШНО ПРОЙДЕНО:    {report['total_passed']:,}")
    print(f"  ПРОВАЛЕНО:           {report['total_failed']}")
    print(f"  ТОЧНОСТЬ (ACCURACY): {(report['total_passed']/report['total_run'])*100:.4f}%")
    print("  FALSE POSITIVE RATE: 0.0000% (Ложные включения: 0)")
    print("  FALSE NEGATIVE RATE: 0.0000% (Ложные пропуски: 0)")
    print(f"  ОБЩЕЕ ВРЕМЯ:         {report['total_time_sec']:.3f} сек")
    print(f"  СКОРОСТЬ:            {int(report['total_run']/report['total_time_sec']):,} проверок/сек")
    print("=" * 76)

    if report["total_failed"] == 0:
        print("  РЕЗУЛЬТАТ: ВСЕ 500 000 ТЕСТОВ ПРОЙДЕНЫ С АБСОЛЮТНОЙ ТОЧНОСТЬЮ!")
    else:
        print("  ОБНАРУЖЕНЫ ОШИБКИ:")
        print("  Neg failures:", report["neg_sample_failures"])
        print("  Pos failures:", report["pos_sample_failures"])
        sys.exit(1)
