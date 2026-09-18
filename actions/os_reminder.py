"""
Действие: напоминание средствами операционной системы.

ЧЕМ ОТЛИЧАЕТСЯ ОТ КАЛЕНДАРЯ
    `actions/calendar.py` держит напоминания в процессе: пока ДЖАРВИС открыт,
    он сверяется с расписанием и говорит вслух. Закрыли приложение, вышли из
    системы, перезагрузились — напоминания нет. Человек об этом не знает и
    узнаёт ровно в тот момент, когда напоминание не сработало.

    Здесь напоминание регистрируется в планировщике ОС — Task Scheduler на
    Windows, LaunchAgent на macOS, systemd-таймер на Linux — и срабатывает
    само, даже если ассистент закрыт.

ПОЧЕМУ УВЕДОМЛЕНИЕ, А НЕ ГОЛОС
    Сказать вслух может только запущенный ассистент, а вся суть в том, что он
    может быть не запущен. Поэтому срабатывает системное уведомление: окно или
    всплывающая подсказка, которую показывает сама ОС.

ЧТО ПРОВЕРЯЕТСЯ, А ЧТО НЕТ
    Команды планировщиков собираются чистыми функциями и проверены тестами.
    Само выполнение — вызов `schtasks`/`launchctl`/`systemd-run`, и его
    результат зависит от машины: на Windows Home часть возможностей урезана,
    на Linux без systemd таймеров нет вовсе. Отказ планировщика не
    проглатывается: ответ прямо говорит, что напоминание НЕ поставлено.
"""

import json
import logging
import platform
import re
import subprocess
from datetime import datetime
from pathlib import Path

_logger = logging.getLogger(__name__)

_OS = platform.system()

# Окно без консоли: иначе каждая постановка напоминания мигает чёрным окном.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if _OS == "Windows" else {}

_TIMEOUT = 15


def _dir() -> Path:
    """Куда складываем скрипты напоминаний. Рядом с настройками пользователя,
    а не в папке программы: программу могут переустановить."""
    d = Path.home() / ".jarvis" / "reminders"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _task_name(момент: datetime) -> str:
    """Имя задачи. По нему же её потом находят и отменяют, поэтому в нём
    только то, что переживёт любую локаль планировщика."""
    return f"JARVIS_{момент.strftime('%Y%m%d_%H%M%S')}"


def _safe(text: str, предел: int = 200) -> str:
    """Текст напоминания уходит в командную строку и в скрипт. Всё, чем можно
    оборвать строку или команду, вырезаем: сообщение сочиняет модель со слов
    человека, а не программист."""
    text = re.sub(r"[\r\n\t]+", " ", str(text))
    text = re.sub(r'["\'`$;&|<>\\]', "", text)
    return text.strip()[:предел]


# ─── Показ уведомления ────────────────────────────────────────────────────────

def _notify_command(сообщение: str) -> list[str]:
    """Команда, которая покажет уведомление. Одна на каждую ОС, без пакетов:
    напоминание не должно зависеть от того, что кто-то доустановил."""
    текст = _safe(сообщение)
    if _OS == "Windows":
        # MessageBox через WScript.Shell есть в любой Windows, в отличие от
        # тостов, которым нужен BurntToast или plyer.
        ps = (
            "$w = New-Object -ComObject WScript.Shell; "
            f"$w.Popup('{текст}', 0, 'ДЖАРВИС — напоминание', 64)"
        )
        return ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps]
    if _OS == "Darwin":
        return ["osascript", "-e",
                f'display notification "{текст}" with title "ДЖАРВИС" sound name "Glass"']
    return ["notify-send", "-u", "critical", "ДЖАРВИС — напоминание", текст]


# ─── Постановка в планировщик ─────────────────────────────────────────────────

def _schtasks_command(имя: str, момент: datetime, сообщение: str) -> list[str]:
    """Windows. `/sc once` плюс дата и время — задача снимается сама после
    срабатывания только в свежих сборках, поэтому чистим их и при постановке
    новой (см. `_подчистить`)."""
    команда = subprocess.list2cmdline(_notify_command(сообщение))
    return [
        "schtasks", "/create", "/tn", имя, "/tr", команда,
        "/sc", "once",
        "/sd", момент.strftime("%d/%m/%Y"),
        "/st", момент.strftime("%H:%M"),
        "/f",
    ]


def _launchd_plist(имя: str, момент: datetime, сообщение: str) -> str:
    """macOS. LaunchAgent с календарным интервалом — минимальный plist без
    лишних ключей: чем их меньше, тем меньше поводов для отказа launchctl."""
    аргументы = "".join(
        f"        <string>{a}</string>\n" for a in _notify_command(сообщение)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{имя}</string>
    <key>ProgramArguments</key>
    <array>
{аргументы}    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Month</key><integer>{момент.month}</integer>
        <key>Day</key><integer>{момент.day}</integer>
        <key>Hour</key><integer>{момент.hour}</integer>
        <key>Minute</key><integer>{момент.minute}</integer>
    </dict>
</dict>
</plist>
"""


def _systemd_command(имя: str, момент: datetime, сообщение: str) -> list[str]:
    """Linux. `systemd-run --user --on-calendar` создаёт разовый таймер, а
    `--unit` даёт ему имя, по которому его потом можно отменить."""
    return [
        "systemd-run", "--user",
        f"--on-calendar={момент.strftime('%Y-%m-%d %H:%M:%S')}",
        f"--unit={имя}",
        "--timer-property=AccuracySec=1s",
        *_notify_command(сообщение),
    ]


# ─── Учёт поставленного ───────────────────────────────────────────────────────

def _журнал() -> Path:
    return _dir() / "scheduled.json"


def _прочитать() -> list[dict]:
    try:
        return json.loads(_журнал().read_text(encoding="utf-8"))
    except Exception:
        return []


def _записать(записи: list[dict]) -> None:
    try:
        from core.storage import atomic_write_json
        atomic_write_json(_журнал(), записи)
    except Exception as exc:
        _logger.warning("Журнал напоминаний не сохранён: %s", exc)


def _подчистить(записи: list[dict]) -> list[dict]:
    """Убирает из журнала то, что уже сработало. Планировщик мог и не удалить
    свою задачу, но для человека прошедшее напоминание — не напоминание."""
    сейчас = datetime.now()
    живые = []
    for з in записи:
        try:
            if datetime.fromisoformat(з["when"]) > сейчас:
                живые.append(з)
        except Exception:
            continue
    return живые


# ─── Действия ─────────────────────────────────────────────────────────────────

def _поставить(сообщение: str, когда: str) -> str:
    from core.calendar_manager import _parse_datetime

    момент = _parse_datetime(когда) if когда else None
    if момент is None:
        return (f"Не разобрал время «{когда}», сэр. Скажите иначе: "
                f"«через сорок минут» или «завтра в девять утра».")
    if момент <= datetime.now():
        return "Это время уже прошло, сэр. Назовите будущее."

    имя = _task_name(момент)
    ошибка = _зарегистрировать(имя, момент, сообщение)
    if ошибка:
        # Молчание здесь было бы худшим исходом: человек ушёл бы уверенным,
        # что его разбудят.
        return f"Напоминание НЕ поставлено, сэр: {ошибка}"

    записи = _подчистить(_прочитать())
    записи.append({"name": имя, "when": момент.isoformat(), "text": _safe(сообщение)})
    _записать(записи)

    return f"Напомню {_когда_словами(момент)}: {_safe(сообщение)}."


def _зарегистрировать(имя: str, момент: datetime, сообщение: str) -> str:
    """Пустая строка — поставлено, иначе причина отказа."""
    try:
        if _OS == "Windows":
            готово = subprocess.run(
                _schtasks_command(имя, момент, сообщение),
                capture_output=True, text=True, timeout=_TIMEOUT, **_NO_WINDOW,
            )
        elif _OS == "Darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / f"{имя}.plist"
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(_launchd_plist(имя, момент, сообщение), encoding="utf-8")
            готово = subprocess.run(
                ["launchctl", "load", str(plist)],
                capture_output=True, text=True, timeout=_TIMEOUT,
            )
        else:
            готово = subprocess.run(
                _systemd_command(имя, момент, сообщение),
                capture_output=True, text=True, timeout=_TIMEOUT,
            )
    except FileNotFoundError:
        return "планировщик задач на этой машине недоступен"
    except subprocess.TimeoutExpired:
        return "планировщик не ответил"
    except Exception as exc:
        return str(exc)[:120]

    if готово.returncode != 0:
        причина = (готово.stderr or готово.stdout or "").strip()
        _logger.warning("Планировщик отказал: %s", причина)
        return причина[:120] or "планировщик отказал без объяснения"
    return ""


def _отменить(что: str) -> str:
    записи = _подчистить(_прочитать())
    if not записи:
        return "Активных напоминаний нет, сэр."

    искомое = _safe(что).lower()
    под_нож = [з for з in записи if not искомое or искомое in з["text"].lower()]
    if not под_нож:
        return f"Напоминания про «{что}» не нашёл, сэр."

    снято = 0
    for з in под_нож:
        if _снять(з["name"]):
            снято += 1
            записи.remove(з)
    _записать(записи)

    if снято == 0:
        return "Не смог снять напоминание — планировщик отказал."
    return f"Отменил {снято}." if снято > 1 else f"Отменил напоминание: {под_нож[0]['text']}."


def _снять(имя: str) -> bool:
    try:
        if _OS == "Windows":
            итог = subprocess.run(["schtasks", "/delete", "/tn", имя, "/f"],
                                  capture_output=True, timeout=_TIMEOUT, **_NO_WINDOW)
        elif _OS == "Darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / f"{имя}.plist"
            итог = subprocess.run(["launchctl", "unload", str(plist)],
                                  capture_output=True, timeout=_TIMEOUT)
            plist.unlink(missing_ok=True)
        else:
            итог = subprocess.run(["systemctl", "--user", "stop", f"{имя}.timer"],
                                  capture_output=True, timeout=_TIMEOUT)
        return итог.returncode == 0
    except Exception as exc:
        _logger.warning("Снятие %s не удалось: %s", имя, exc)
        return False


def _список() -> str:
    записи = _подчистить(_прочитать())
    _записать(записи)
    if not записи:
        return "Активных напоминаний нет, сэр."

    записи.sort(key=lambda з: з["when"])
    строки = [
        f"{_когда_словами(datetime.fromisoformat(з['when']))} — {з['text']}"
        for з in записи[:8]
    ]
    хвост = f" И ещё {len(записи) - 8}." if len(записи) > 8 else ""
    return "Напоминания: " + "; ".join(строки) + "." + хвост


_МЕСЯЦЫ = ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _когда_словами(момент: datetime) -> str:
    """«сегодня в 14:30», «завтра в 9:00», «3 марта в 8:00» — так это и
    произносят, в отличие от ISO-даты."""
    сегодня = datetime.now().date()
    дней = (момент.date() - сегодня).days
    время = момент.strftime("%H:%M")
    if дней == 0:
        return f"сегодня в {время}"
    if дней == 1:
        return f"завтра в {время}"
    if дней == 2:
        return f"послезавтра в {время}"
    return f"{момент.day} {_МЕСЯЦЫ[момент.month - 1]} в {время}"


def os_reminder(parameters: dict, player=None) -> str:
    действие = str((parameters or {}).get("action", "set")).strip().lower()
    сообщение = str((parameters or {}).get("message", "")).strip()
    когда = str((parameters or {}).get("when", "")).strip()

    if действие in ("list", "список"):
        ответ = _список()
    elif действие in ("cancel", "отмена", "отменить"):
        ответ = _отменить(сообщение)
    else:
        if not сообщение:
            return "О чём напомнить, сэр?"
        ответ = _поставить(сообщение, когда)

    if player:
        player.write_log(f"SYS: {ответ}")
    return ответ


# ─── Объявление для реестра действий ──────────────────────────────────────────
TOOL = {
    "name": "os_reminder",
    "description": (
        "Ставит напоминание средствами операционной системы — оно сработает, даже "
        "если ДЖАРВИС в этот момент закрыт или компьютер был перезагружен. "
        "Вызывай, когда пользователь просит напомнить о чём-то ко времени: "
        "«напомни через полчаса», «напомни завтра в девять позвонить», "
        "«не дай забыть про встречу». "
        "Для событий календаря и расписания на день используй инструмент calendar, "
        "а не этот: здесь только разовые напоминания."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "set (по умолчанию) — поставить | list — показать | cancel — отменить",
            },
            "message": {
                "type": "STRING",
                "description": "О чём напомнить. Для action=cancel — слово из текста напоминания.",
            },
            "when": {
                "type": "STRING",
                "description": (
                    "Когда, русским текстом: «через 30 минут», «завтра в 14:00», "
                    "«сегодня в 18:30»"
                ),
            },
        },
        "required": [],
    },
    "handler": os_reminder,
}
