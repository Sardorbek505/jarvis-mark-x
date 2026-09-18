"""Самопроверка: что на ЭТОЙ машине работает по-настоящему.

Зачем это есть. Больше половины того, что умеет Джарвис, проверить в тестах
можно только наполовину: что логика вокруг системного вызова верна — да, что
сам вызов сработает у вас — нет. Обои, мышь, буфер обмена, планировщик задач,
Steam, WhatsApp, живой поиск: в контейнере сборки нет ни рабочего стола, ни
мыши, ни буфера, и подменённый `subprocess.run` об этом ничего не скажет.

Раньше единственным способом узнать было сесть и пробовать голосом по одной
команде, каждый раз гадая, чего именно не хватило: ключа, программы, прав или
кода. Здесь всё это спрашивается подряд и отвечает словами.

    python scripts/selfcheck.py            # только чтение, ничего не меняет
    python scripts/selfcheck.py --полная   # + обратимые проверки действием
    python scripts/selfcheck.py --сеть     # + запросы в интернет (деньги/квота)

ПОЧЕМУ ПО УМОЛЧАНИЮ ТОЛЬКО ЧТЕНИЕ
    Самопроверка, которая сама что-то испортила, бесполезна вдвойне: человек
    запускает её как раз тогда, когда уже не уверен, что всё цело. Поэтому
    по умолчанию не трогается ничего, а всё, что включается флагом `--полная`,
    возвращает прежнее состояние: буфер получает обратно своё содержимое,
    курсор — прежнюю точку, задача планировщика удаляется.

ЧТО ЗНАЧАТ ОТМЕТКИ
    OK    — проверено, работает.
    НЕТ   — нечем работать, и это ожидаемо: не установлено, не настроено,
            не та система. Сказано, чего именно не хватает.
    СБОЙ  — всё на месте, но не сработало. Вот это и надо чинить.
    МИМО  — к этой системе не относится (проверка для Windows на Linux).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import logging
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

_OS = platform.system()


def _есть_графика() -> bool:
    """Есть ли вообще рабочий стол.

    Без этого половина отметок врала бы: на сервере в контейнере «снимок не
    сделался» — это не поломка, которую надо чинить, а отсутствие экрана.
    Отличать одно от другого — вся ценность самопроверки."""
    if _OS in ("Windows", "Darwin"):
        return True
    return bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))

OK, НЕТ, СБОЙ, МИМО = "OK", "НЕТ", "СБОЙ", "МИМО"

_ЦВЕТА = {OK: "\033[92m", НЕТ: "\033[93m", СБОЙ: "\033[91m", МИМО: "\033[90m"}
_СБРОС = "\033[0m"


@dataclass
class Итог:
    отметка: str
    подробность: str = ""
    совет: str = ""


@dataclass
class Проверка:
    раздел: str
    имя: str
    делать: callable
    меняет: bool = False          # нужен --полная
    сеть: bool = False            # нужен --сеть


_проверки: list[Проверка] = []


def проверка(раздел: str, имя: str, *, меняет: bool = False, сеть: bool = False):
    def обёртка(функция):
        _проверки.append(Проверка(раздел, имя, функция, меняет, сеть))
        return функция
    return обёртка


# ─── Модель и голос ──────────────────────────────────────────────────────────

@проверка("Модель", "Ключ Gemini")
def _ключ():
    from core.onboarding import ensure_gemini_key

    ключ = (ensure_gemini_key(interactive=False) or "").strip()
    if not ключ:
        return Итог(НЕТ, "ключа нет",
                    "получите его на aistudio.google.com и вставьте при запуске")
    return Итог(OK, f"есть, {ключ[:6]}…{ключ[-4:]}")


@проверка("Модель", "Текстовая модель")
def _текстовая():
    from core import llm_client

    описание = llm_client.describe()
    if not llm_client.available():
        return Итог(НЕТ, описание,
                    "пересказ файлов и роликов работать не будет")
    return Итог(OK, описание)


@проверка("Модель", "Ответ текстовой модели", сеть=True)
def _текстовая_живьём():
    from core import llm_client

    if not llm_client.available():
        return Итог(МИМО, "нечем спрашивать")
    ответ = llm_client.ask("Ответь одним словом: тест.", предел=20)
    if not ответ:
        return Итог(СБОЙ, "модель не ответила",
                    "проверьте квоту ключа и доступ в сеть")
    return Итог(OK, f"«{ответ[:40]}»")


@проверка("Голос", "Fish TTS")
def _fish():
    from telegram_bot import tts_fish

    if not tts_fish.is_configured():
        return Итог(НЕТ, "ключ Fish не задан",
                    "голосом будет Edge-TTS — бесплатный, но не тот тембр")
    return Итог(OK, "настроен")


@проверка("Голос", "Edge-TTS")
def _edge():
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return Итог(НЕТ, "пакет edge-tts не установлен",
                    "pip install edge-tts")
    return Итог(OK, "установлен")


# ─── Звук ────────────────────────────────────────────────────────────────────

@проверка("Звук", "Микрофон и динамики")
def _устройства():
    from core import audio_devices

    входы = audio_devices.list_devices("input")
    выходы = audio_devices.list_devices("output")
    if not входы and not выходы:
        # Ни входа, ни выхода — это обычно машина без звуковой карты вовсе
        # (сервер, контейнер), а не сломанный микрофон.
        return Итог(НЕТ, "звуковых устройств нет совсем",
                    "на этой машине Джарвис голосом работать не сможет")
    if not входы:
        return Итог(СБОЙ, "есть выход, но ни одного устройства ввода",
                    "Джарвис вас не услышит — проверьте микрофон в системе")
    if not выходы:
        return Итог(СБОЙ, "есть вход, но ни одного устройства вывода",
                    "Джарвис будет отвечать в тишину")

    выбран = audio_devices.saved_name("input") or "по умолчанию"
    return Итог(OK, f"вход: {len(входы)}, выход: {len(выходы)}; выбран «{выбран}»")


@проверка("Звук", "Зажми и говори")
def _ptt():
    from core.push_to_talk import PushToTalk
    from core import push_to_talk

    включён = "включён" if push_to_talk.enabled() else "выключен"
    if PushToTalk.global_capable():
        return Итог(OK, f"{включён}, работает в любом окне")
    return Итог(НЕТ, f"{включён}, {PushToTalk.scope_note().lower()}",
                "вне Windows удержание клавиши видно только своему окну")


# ─── Экран и мышь ────────────────────────────────────────────────────────────

@проверка("Экран", "Снимок экрана")
def _снимок():
    from vision.screen_capture import capture_full_screen

    кадр = capture_full_screen()
    if кадр is not None:
        return Итог(OK, f"{кадр.width}×{кадр.height}")
    if not _есть_графика():
        return Итог(НЕТ, "нет графического сеанса",
                    "на машине без рабочего стола зрение и мышь не нужны")
    return Итог(СБОЙ, "снимок не сделался",
                "без него не работают «посмотри на экран» и нажатия мышью")


@проверка("Экран", "Мышь")
def _мышь():
    try:
        import pyautogui
    except Exception as exc:
        return Итог(НЕТ, f"pyautogui недоступен: {str(exc)[:60]}",
                    "не будет «нажми Сохранить»")
    try:
        x, y = pyautogui.position()
    except Exception as exc:
        if not _есть_графика():
            return Итог(НЕТ, "нет графического сеанса")
        return Итог(СБОЙ, f"курсор не опрашивается: {str(exc)[:60]}")
    return Итог(OK, f"курсор в точке {x}, {y}")


@проверка("Экран", "Движение курсора", меняет=True)
def _мышь_живьём():
    try:
        import pyautogui
    except Exception:
        return Итог(МИМО, "мыши нет")

    было = pyautogui.position()
    try:
        pyautogui.moveTo(было[0] + 3, было[1], duration=0.1)
        стало = pyautogui.position()
    finally:
        pyautogui.moveTo(*было, duration=0.1)   # вернуть на место

    if стало == было:
        return Итог(СБОЙ, "курсор не сдвинулся",
                    "на macOS нужно разрешение «Универсальный доступ»")
    return Итог(OK, "двигается и возвращается")


@проверка("Экран", "Обои")
def _обои():
    from actions.desktop import _текущие_обои

    нынешние = _текущие_обои()
    if not нынешние:
        return Итог(НЕТ, "система не назвала нынешние обои",
                    "поставить смогу, а «отмени» после этого — нет")
    return Итог(OK, f"нынешние: {Path(нынешние).name}")


# ─── Буфер обмена ────────────────────────────────────────────────────────────

@проверка("Буфер", "Чтение")
def _буфер_чтение():
    from core import clipboard

    try:
        текст = clipboard.read()
    except clipboard.БуферНедоступен as беда:
        совет = ("установите wl-clipboard или xclip" if _OS not in ("Windows", "Darwin")
                 else "")
        return Итог(НЕТ, str(беда), совет)
    if not текст.strip():
        return Итог(OK, "доступен, сейчас пуст")
    return Итог(OK, f"доступен, {len(текст)} символов")


@проверка("Буфер", "Запись и возврат", меняет=True)
def _буфер_запись():
    from core import clipboard

    метка = "ДЖАРВИС-САМОПРОВЕРКА"
    try:
        было = clipboard.read()
        clipboard.write(метка)
        стало = clipboard.read()
    except clipboard.БуферНедоступен as беда:
        return Итог(МИМО, str(беда))
    finally:
        # Чужой буфер — не наша собственность: что человек копировал, то он
        # и должен вставить после самопроверки.
        try:
            clipboard.write(было)
        except Exception:
            pass

    if метка not in стало:
        return Итог(СБОЙ, "записанное не прочиталось обратно")
    return Итог(OK, "пишется и читается, прежнее возвращено")


# ─── Файлы и документы ───────────────────────────────────────────────────────

@проверка("Файлы", "Корзина")
def _корзина():
    try:
        import send2trash  # noqa: F401
    except ImportError:
        return Итог(НЕТ, "send2trash не установлен",
                    "удаление станет безвозвратным — Джарвис об этом предупредит")
    return Итог(OK, "удаление пойдёт в корзину")


@проверка("Файлы", "Чтение документов")
def _документы():
    есть, нет = [], []
    for пакет, формат in (("pdfplumber", "PDF"), ("docx", "Word"),
                          ("openpyxl", "Excel")):
        try:
            __import__(пакет)
            есть.append(формат)
        except ImportError:
            нет.append(формат)
    if not есть:
        return Итог(НЕТ, "ни одного пакета",
                    "pip install pdfplumber python-docx openpyxl")
    if нет:
        return Итог(НЕТ, f"читаю {', '.join(есть)}; не читаю {', '.join(нет)}",
                    "pip install -r requirements.txt")
    return Итог(OK, "PDF, Word, Excel")


@проверка("Файлы", "Субтитры YouTube")
def _субтитры():
    try:
        import youtube_transcript_api  # noqa: F401
    except ImportError:
        return Итог(НЕТ, "youtube-transcript-api не установлен",
                    "пересказ роликов работать не будет")
    return Итог(OK, "установлен")


# ─── Браузер ─────────────────────────────────────────────────────────────────

@проверка("Браузер", "Playwright и Chromium")
def _playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return Итог(НЕТ, "playwright не установлен",
                    "«открой сайт» будет работать, нажатия на страницах — нет")

    from core import browser_session

    найден = next((п for п in browser_session._CHROMIUM_HINTS if Path(п).exists()), None)
    if найден:
        return Итог(OK, f"Chromium: {найден}")
    return Итог(OK, "пакет на месте; Chromium найдёт сам playwright")


@проверка("Браузер", "Открыть и закрыть страницу", меняет=True)
def _браузер_живьём():
    from core import browser_session as bs

    try:
        # Невидимым окном: самопроверка не должна выбрасывать браузер человеку
        # в лицо. Проверяется то же самое — что Chromium запускается и слушает
        # команды.
        страница = bs.session(headless=True)
        страница.goto("data:text/html;charset=utf-8,<title>проверка</title>")
        заголовок = страница.title()
    except Exception as exc:
        return Итог(СБОЙ, str(exc)[:90],
                    "запустите: playwright install chromium")
    finally:
        try:
            bs.close()
        except Exception:
            pass
    return Итог(OK, f"страница открылась, заголовок «{заголовок}»")


# ─── Игры ────────────────────────────────────────────────────────────────────

@проверка("Игры", "Steam и Epic")
def _игры():
    from actions.game_launcher import _epic_manifests_dir, _steam_root, installed_games

    steam = _steam_root()
    epic = _epic_manifests_dir()
    if steam is None and epic is None:
        return Итог(НЕТ, "ни Steam, ни Epic не найдены",
                    "«включи игру» работать не будет")

    игры = installed_games()
    где = ", ".join(ф for ф, п in (("Steam", steam), ("Epic", epic)) if п)
    if not игры:
        return Итог(СБОЙ, f"{где} найден, но установленных игр не видно",
                    "проверьте, что игры стоят в библиотеке этого аккаунта")
    примеры = ", ".join(и["name"] for и in игры[:3])
    return Итог(OK, f"{где}: {len(игры)} — {примеры}…")


# ─── Планировщик ─────────────────────────────────────────────────────────────

@проверка("Напоминания", "Планировщик ОС")
def _планировщик():
    команда = {"Windows": "schtasks", "Darwin": "launchctl"}.get(_OS, "systemd-run")
    if shutil.which(команда) is None:
        return Итог(НЕТ, f"{команда} не найден",
                    "напоминания при закрытом Джарвисе ставиться не будут")

    # Планировщик есть, а показать напоминание нечем — Linux ставит задачу и
    # молча ничего не показывает. Это ровно тот случай, ради которого
    # самопроверка и написана: по отдельности всё на месте, вместе не работает.
    if _OS not in ("Windows", "Darwin") and shutil.which("notify-send") is None:
        return Итог(НЕТ, f"{команда} есть, а notify-send нет",
                    "sudo apt install libnotify-bin — иначе напоминание "
                    "сработает, но вы его не увидите")
    return Итог(OK, f"{команда} на месте")


@проверка("Напоминания", "Поставить и снять задачу", меняет=True)
def _планировщик_живьём():
    from actions.os_reminder import os_reminder

    метка = "самопроверка Джарвиса"
    ответ = os_reminder({"action": "set", "message": метка,
                         "when": "через 40 минут"})
    if "НЕ поставлено" in ответ or "не разобрал" in ответ.lower():
        return Итог(СБОЙ, ответ[:90])

    # Снимаем ИМЕННО свою задачу, по тексту: человек мог поставить настоящие
    # напоминания, и самопроверка не имеет права их стереть.
    снято = os_reminder({"action": "cancel", "message": метка})
    if "Отменил" not in снято:
        return Итог(СБОЙ, f"поставлено, но не снялось: {снято[:70]}",
                    f"удалите задачу с текстом «{метка}» вручную")
    return Итог(OK, "поставлено и снято")


# ─── Сеть ────────────────────────────────────────────────────────────────────

@проверка("Сеть", "Поиск", сеть=True)
def _поиск():
    from actions.web_search import web_search

    ответ = web_search({"query": "который сейчас год", "mode": "search"})
    if not ответ or "браузер" in ответ.lower()[:40]:
        return Итог(СБОЙ, (ответ or "пусто")[:90],
                    "поиск свалился в открытие браузера — это не голосовой ответ")
    return Итог(OK, ответ[:70].replace("\n", " ") + "…")


@проверка("Сеть", "Погода", сеть=True)
def _погода():
    from actions.weather import weather

    ответ = weather({"city": "Ташкент"})
    if not ответ or "недоступ" in ответ.lower():
        return Итог(СБОЙ, (ответ or "пусто")[:90])
    return Итог(OK, ответ[:70].replace("\n", " "))


# ─── Что накоплено ───────────────────────────────────────────────────────────

@проверка("Память", "Долгосрочная память")
def _память():
    from memory.memory_manager import all_entries

    записи = all_entries()
    if not записи:
        return Итог(OK, "пока пусто — заполнится в разговоре")
    категории = sorted({з["category"] for з in записи})
    return Итог(OK, f"{len(записи)} фактов в категориях: {', '.join(категории)}")


@проверка("Память", "Журнал разговоров")
def _журнал():
    from core import session_log

    дни = session_log.дни_с_разговорами()
    if not дни:
        return Итог(OK, "прошлых разговоров нет — «вчера мы говорили» промолчит")
    return Итог(OK, f"{len(дни)} дн., последний {дни[0].isoformat()}")


@проверка("Память", "Слежение за темами")
def _слежение():
    from core import watcher

    темы = watcher.topics()
    if not темы:
        return Итог(OK, "ни за чем не слежу")
    имена = ", ".join(f"«{т['тема']}»" for т in темы)
    return Итог(OK, f"{len(темы)}: {имена}")


@проверка("Память", "Записная книжка")
def _контакты():
    from actions.messenger import _контакты as книжка

    люди = книжка()
    if not люди:
        return Итог(OK, "пуста — скажите «запиши номер мамы»")
    return Итог(OK, f"{len(люди)}: {', '.join(sorted(люди))}")


@проверка("Настройки", "Разговор")
def _настройки():
    from core import settings

    значения = settings.all_settings()
    части = [f"{к}={з}" for к, з in значения.items()]
    return Итог(OK, "; ".join(части))


@проверка("Настройки", "Куда всё пишется")
def _пути():
    from core.paths import get_config_path, get_user_data_dir

    конфиг = get_config_path("api_keys.json", for_writing=True)
    return Итог(OK, f"настройки: {конфиг}; данные: {get_user_data_dir()}; "
                    f"личное: {Path.home() / '.jarvis'}")


# ─── Прогон ──────────────────────────────────────────────────────────────────

def _выполнить(п: Проверка) -> Итог:
    начало = time.perf_counter()
    # Проверяемый код печатает своё в stdout (мастер ключа, Spotify, драйверы
    # звука). В таблице это выглядит как сбой форматирования, а не как ответ,
    # поэтому его болтовня уходит в никуда.
    шум = io.StringIO()
    прежний = logging.getLogger().manager.disable
    logging.disable(logging.CRITICAL)      # и логи туда же: отчёт здесь свой
    try:
        with contextlib.redirect_stdout(шум), contextlib.redirect_stderr(шум):
            итог = п.делать()
    except Exception as exc:
        # Упавшая проверка — это тоже ответ, и притом самый важный: значит,
        # в этом месте Джарвис свалится молча.
        итог = Итог(СБОЙ, f"{type(exc).__name__}: {str(exc)[:80]}")
    finally:
        logging.disable(прежний)
    итог.длительность = time.perf_counter() - начало
    return итог


def _печатать(раздел: str, имя: str, итог: Итог, цветно: bool) -> None:
    метка = итог.отметка
    если_цвет = f"{_ЦВЕТА[метка]}{метка:<4}{_СБРОС}" if цветно else f"{метка:<4}"
    print(f"  [{если_цвет}] {имя:<26} {итог.подробность}")
    if итог.совет:
        print(f"         └─ {итог.совет}")


def main() -> int:
    разбор = argparse.ArgumentParser(
        description="Самопроверка Джарвиса: что на этой машине работает по-настоящему.")
    разбор.add_argument("--полная", action="store_true",
                        help="проверки действием: буфер, мышь, браузер, планировщик "
                             "(всё возвращается как было)")
    разбор.add_argument("--сеть", action="store_true",
                        help="запросы в интернет: поиск, погода, ответ модели "
                             "(тратит квоту ключа)")
    разбор.add_argument("--всё", action="store_true", help="то же, что --полная --сеть")
    разбор.add_argument("--без-цвета", action="store_true")
    аргументы = разбор.parse_args()

    полная = аргументы.полная or аргументы.всё
    сеть = аргументы.сеть or аргументы.всё
    цветно = not аргументы.без_цвета and sys.stdout.isatty()

    print(f"\nДЖАРВИС — самопроверка на {platform.system()} {platform.release()}, "
          f"Python {platform.python_version()}")
    if not полная:
        print("Только чтение. Проверки действием: --полная")
    if not сеть:
        print("Без сети. Поиск, погода и ответ модели: --сеть")
    print()

    счёт = {OK: 0, НЕТ: 0, СБОЙ: 0, МИМО: 0}
    сбои: list[tuple[str, Итог]] = []
    текущий_раздел = ""

    for п in _проверки:
        if п.раздел != текущий_раздел:
            текущий_раздел = п.раздел
            print(f"{текущий_раздел}")

        if п.меняет and not полная:
            итог = Итог(МИМО, "нужен --полная")
        elif п.сеть and not сеть:
            итог = Итог(МИМО, "нужен --сеть")
        else:
            итог = _выполнить(п)

        счёт[итог.отметка] += 1
        if итог.отметка == СБОЙ:
            сбои.append((f"{п.раздел} / {п.имя}", итог))
        _печатать(п.раздел, п.имя, итог, цветно)

    print(f"\nИтог: {счёт[OK]} работает, {счёт[НЕТ]} нечем, "
          f"{счёт[СБОЙ]} сбоев, {счёт[МИМО]} пропущено.")

    if сбои:
        print("\nЧинить здесь:")
        for имя, итог in сбои:
            print(f"  • {имя}: {итог.подробность}")
            if итог.совет:
                print(f"    {итог.совет}")
    elif счёт[НЕТ]:
        print("Сбоев нет. «НЕТ» — это не поломка, а то, чего на машине "
              "просто не стоит.")

    # Код возврата по СБОЯМ, а не по «НЕТ»: отсутствие Steam не должно ронять
    # чужой скрипт, который вызвал самопроверку.
    return 1 if сбои else 0


if __name__ == "__main__":
    sys.exit(main())
