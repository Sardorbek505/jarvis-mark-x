"""Переезд на другую машину: всё нажитое — одним файлом.

Зачем это есть. Джарвис накапливает то, что заново не наберёшь: факты о
человеке, записную книжку, темы слежения, профиль привычек и подобранные на
слух настройки разговора. Лежит всё это в четырёх разных местах — в папке
программы, в `~/.jarvis`, в `%APPDATA%` — и при переустановке системы или
переезде на новый компьютер пропадает целиком, молча.

    python scripts/transfer.py --выгрузить jarvis-backup.zip
    python scripts/transfer.py --загрузить jarvis-backup.zip
    python scripts/transfer.py --загрузить jarvis-backup.zip --поверх

ПРО КЛЮЧИ
    Ключи API в выгрузку НЕ попадают. Файл переезда человек кладёт в облако,
    отправляет себе в мессенджер и забывает в загрузках — а там лежал бы
    действующий ключ Gemini, по которому выставляют счета. Кому нужно —
    `--с-ключами`, и тогда об этом сказано вслух при выгрузке.

ПРО СЛИЯНИЕ
    По умолчанию загрузка ДОПОЛНЯЕТ, а не заменяет: факты и контакты с новой
    машины остаются, из файла добавляется то, чего нет. Это разумнее для
    самого частого случая — человек уже успел поговорить с Джарвисом на новом
    компьютере, прежде чем вспомнил про перенос. `--поверх` заменяет целиком.

ЧЕГО ЗДЕСЬ НЕТ
    Напоминаний: они живут не файлом, а задачами планировщика ОС, и перенести
    журнал без самих задач значило бы показать человеку список напоминаний,
    которые не сработают.

    Расшифровок разговоров: они и так живут две недели, и «вчера мы говорили»
    про вчера на другой машине — это не перенос, а путаница.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# Что переносим: имя внутри архива → как его взять и куда положить.
# Списком, а не «сгрести всё подряд»: в тех же папках лежат кэши, токены
# Google и временные файлы, которым на чужой машине делать нечего.
ВЕЩИ = ("memory", "contacts", "watch", "profile", "settings")

# Ключи, которые в выгрузку не попадают без явной просьбы.
СЕКРЕТЫ = ("gemini_api_key", "fish_api_key", "openai_key", "telegram_token",
           "spotify_client_id", "spotify_client_secret", "elevenlabs_api_key")


def _пути() -> dict[str, Path]:
    from core.paths import get_config_path

    личное = Path.home() / ".jarvis"
    return {
        "memory":   _BASE / "memory" / "data.json",
        "contacts": личное / "contacts.json",
        "watch":    личное / "watch" / "topics.json",
        "profile":  _BASE / "config" / "user_profile.json",
        "settings": get_config_path("api_keys.json", for_writing=True),
    }


def _прочитать(путь: Path):
    try:
        return json.loads(путь.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as беда:
        print(f"  [!] {путь.name} не прочитан: {беда}")
        return None


def _записать(путь: Path, данные) -> None:
    from core.storage import atomic_write_json

    путь.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(путь, данные)


def _без_секретов(настройки: dict) -> tuple[dict, list[str]]:
    убрано = [к for к in настройки if к in СЕКРЕТЫ]
    return {к: з for к, з in настройки.items() if к not in СЕКРЕТЫ}, убрано


def _размер(данные) -> str:
    if isinstance(данные, dict):
        return f"{sum(len(з) if isinstance(з, dict) else 1 for з in данные.values())} записей"
    if isinstance(данные, list):
        return f"{len(данные)} записей"
    return "есть"


# ─── Выгрузка ────────────────────────────────────────────────────────────────

def выгрузить(куда: Path, с_ключами: bool) -> int:
    пути = _пути()
    собрано: dict[str, object] = {}
    убранные: list[str] = []

    print(f"\nСобираю в {куда}\n")
    for имя in ВЕЩИ:
        данные = _прочитать(пути[имя])
        if данные is None:
            print(f"  [ ] {имя:<9} — нечего брать")
            continue

        if имя == "settings" and isinstance(данные, dict) and not с_ключами:
            данные, убранные = _без_секретов(данные)

        собрано[имя] = данные
        print(f"  [+] {имя:<9} — {_размер(данные)}")

    if not собрано:
        print("\nПереносить нечего, сэр.")
        return 1

    опись = {
        "версия": 1,
        "создано": datetime.now().isoformat(timespec="seconds"),
        "с_ключами": bool(с_ключами and "settings" in собрано),
    }

    try:
        куда.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(куда, "w", zipfile.ZIP_DEFLATED) as архив:
            архив.writestr("опись.json", json.dumps(опись, ensure_ascii=False, indent=2))
            for имя, данные in собрано.items():
                архив.writestr(f"{имя}.json",
                               json.dumps(данные, ensure_ascii=False, indent=2))
    except Exception as беда:
        print(f"\nНе записалось: {беда}")
        return 1

    print(f"\nГотово: {куда} ({куда.stat().st_size // 1024} КБ)")
    if убранные:
        print(f"Ключи в файл НЕ попали ({', '.join(убранные)}) — "
              f"их вводят на новой машине заново.")
    elif опись["с_ключами"]:
        # Про это нельзя написать мелким шрифтом: файл уедет в облако.
        print("ВНИМАНИЕ: в файле лежат действующие ключи API. Не выкладывайте "
              "его в облако и не пересылайте — по этим ключам выставляют счета.")
    return 0


# ─── Загрузка ────────────────────────────────────────────────────────────────

def _слить(старое, новое):
    """Дополняет старое новым, не затирая то, что уже есть на этой машине."""
    if isinstance(старое, dict) and isinstance(новое, dict):
        итог = dict(старое)
        for ключ, значение in новое.items():
            итог[ключ] = (_слить(итог[ключ], значение)
                          if ключ in итог else значение)
        return итог
    if isinstance(старое, list) and isinstance(новое, list):
        # Списки (темы слежения) — по одному разу каждый элемент.
        итог = list(старое)
        видели = {json.dumps(э, sort_keys=True, ensure_ascii=False) for э in итог}
        for элемент in новое:
            отпечаток = json.dumps(элемент, sort_keys=True, ensure_ascii=False)
            if отпечаток not in видели:
                видели.add(отпечаток)
                итог.append(элемент)
        return итог
    # Скалярам сливаться нечем: то, что уже стоит на этой машине, главнее.
    return старое


def загрузить(откуда: Path, поверх: bool) -> int:
    if not откуда.is_file():
        print(f"\nФайла нет: {откуда}")
        return 1

    try:
        with zipfile.ZipFile(откуда) as архив:
            внутри = set(архив.namelist())
            if "опись.json" not in внутри:
                print("\nЭто не файл переезда Джарвиса: описи внутри нет.")
                return 1
            опись = json.loads(архив.read("опись.json").decode("utf-8"))
            содержимое = {
                имя: json.loads(архив.read(f"{имя}.json").decode("utf-8"))
                for имя in ВЕЩИ if f"{имя}.json" in внутри
            }
    except Exception as беда:
        print(f"\nАрхив не читается: {беда}")
        return 1

    способ = "заменяю целиком" if поверх else "дополняю, не затирая своё"
    print(f"\nИз {откуда} (создан {опись.get('создано', 'когда-то')}), {способ}\n")

    пути = _пути()
    for имя, новое in содержимое.items():
        старое = _прочитать(пути[имя])

        if имя == "settings" and isinstance(новое, dict):
            # Ключи этой машины не трогаем НИКОГДА, даже с --поверх: иначе
            # перенос настроек тембра лишает человека доступа к Gemini.
            новое = {к: з for к, з in новое.items() if к not in СЕКРЕТЫ}
            if поверх and isinstance(старое, dict):
                свои = {к: з for к, з in старое.items() if к in СЕКРЕТЫ}
                новое = {**новое, **свои}

        итог = новое if (поверх or старое is None) else _слить(старое, новое)
        try:
            _записать(пути[имя], итог)
        except Exception as беда:
            print(f"  [!] {имя:<9} — не записалось: {беда}")
            continue
        print(f"  [+] {имя:<9} — {_размер(итог)}")

    print("\nГотово. Перезапустите Джарвиса, чтобы он перечитал накопленное.")
    return 0


def main() -> int:
    разбор = argparse.ArgumentParser(
        description="Перенос памяти, контактов и настроек Джарвиса на другую машину.")
    разбор.add_argument("--выгрузить", metavar="ФАЙЛ", type=Path)
    разбор.add_argument("--загрузить", metavar="ФАЙЛ", type=Path)
    разбор.add_argument("--с-ключами", action="store_true",
                        dest="с_ключами",
                        help="положить в файл и ключи API (по умолчанию нет)")
    разбор.add_argument("--поверх", action="store_true",
                        help="заменить целиком, а не дополнить")
    а = разбор.parse_args()

    if bool(а.выгрузить) == bool(а.загрузить):
        разбор.error("нужно ровно одно: --выгрузить или --загрузить")

    if а.выгрузить:
        return выгрузить(а.выгрузить, а.с_ключами)
    return загрузить(а.загрузить, а.поверх)


if __name__ == "__main__":
    sys.exit(main())
