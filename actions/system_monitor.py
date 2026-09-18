"""
Действие: состояние железа — процессор, память, диск, батарея, температура.

ЗАЧЕМ ОТДЕЛЬНЫЙ ИНСТРУМЕНТ
    Метрики в проекте были — но только в двух местах, до которых не дотянуться
    голосом: полоски CPU/ОЗУ в углу HUD и ответ Telegram-бота на команду
    `/sys`. Спросить «что с компьютером» вслух было нельзя, и ассистент,
    сидящий на той самой машине, отвечал догадкой или предлагал посмотреть
    диспетчер задач.

ЧТО ЗДЕСЬ ЧЕСТНО, А ЧТО НЕТ
    Температуру и загрузку видеокарты отдаёт не всякая машина: на Windows
    `psutil.sensors_temperatures()` обычно пуст, NVML есть только у NVIDIA.
    Недоступное не выдумывается и не заменяется нулём — такой строки просто
    нет в ответе. Ноль градусов, произнесённый вслух, звучит как показание.
"""

import logging
import platform
import time

_logger = logging.getLogger(__name__)

_OS = platform.system()

# Пороги, за которыми стоит сказать вслух. Выше — уже не «работает», а «греется
# и тормозит», и человеку полезно узнать об этом раньше, чем по звуку кулера.
ALERT_CPU = 90.0
ALERT_MEM = 90.0
ALERT_DISK = 95.0
ALERT_TEMP = 85.0


def _disk_root() -> str:
    return "C:\\" if _OS == "Windows" else "/"


def _cpu_temp() -> float | None:
    """Температура процессора или None, если датчик недоступен.

    На Windows psutil почти всегда возвращает пустой словарь: там нужны либо
    OpenHardwareMonitor, либо WMI с правами администратора. Это нормально и
    не ошибка — просто нечего сказать."""
    try:
        import psutil
        датчики = psutil.sensors_temperatures()
    except (ImportError, AttributeError) as exc:
        _logger.debug("Датчики температуры недоступны: %s", exc)
        return None
    except Exception as exc:
        _logger.debug("Чтение температуры сорвалось: %s", exc)
        return None

    if not датчики:
        return None
    # Имена ключей зависят от железа: coretemp у Intel, k10temp у AMD,
    # cpu_thermal на одноплатниках. Берём первый датчик с внятным значением.
    for предпочтение in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
        for запись in датчики.get(предпочтение, []):
            if запись.current:
                return float(запись.current)
    for записи in датчики.values():
        for запись in записи:
            if запись.current:
                return float(запись.current)
    return None


def _gpu() -> tuple[float, float] | None:
    """Загрузка видеокарты и её температура. None — NVML нет или карта не NVIDIA."""
    try:
        import pynvml
        pynvml.nvmlInit()
        карта = pynvml.nvmlDeviceGetHandleByIndex(0)
        загрузка = pynvml.nvmlDeviceGetUtilizationRates(карта).gpu
        температура = pynvml.nvmlDeviceGetTemperature(карта, pynvml.NVML_TEMPERATURE_GPU)
        pynvml.nvmlShutdown()
        return float(загрузка), float(температура)
    except Exception as exc:
        _logger.debug("NVML недоступен: %s", exc)
        return None


def collect() -> dict:
    """Снимок состояния. Ключи, которые машина не отдаёт, отсутствуют."""
    try:
        import psutil
    except ImportError:
        return {"error": "psutil не установлен"}

    данные: dict = {}
    try:
        # interval=0.3 — это блокировка на треть секунды, поэтому инструмент
        # и выполняется в отдельном потоке. Без интервала psutil отдаёт
        # среднее с прошлого вызова, то есть при первом запросе — ноль.
        данные["cpu"] = psutil.cpu_percent(interval=0.3)
        данные["cpu_cores"] = psutil.cpu_count(logical=True)

        память = psutil.virtual_memory()
        данные["mem_percent"] = память.percent
        данные["mem_used_gb"] = память.used / 1024 ** 3
        данные["mem_total_gb"] = память.total / 1024 ** 3

        диск = psutil.disk_usage(_disk_root())
        данные["disk_percent"] = диск.percent
        данные["disk_free_gb"] = диск.free / 1024 ** 3

        данные["uptime_hours"] = (time.time() - psutil.boot_time()) / 3600
        данные["processes"] = len(psutil.pids())
    except Exception as exc:
        _logger.warning("Метрики не сняты: %s", exc)
        return {"error": str(exc)}

    try:
        батарея = psutil.sensors_battery()
        if батарея is not None:
            данные["battery_percent"] = батарея.percent
            данные["battery_plugged"] = bool(батарея.power_plugged)
    except Exception as exc:
        _logger.debug("Батарея недоступна: %s", exc)

    температура = _cpu_temp()
    if температура is not None:
        данные["cpu_temp"] = температура

    видеокарта = _gpu()
    if видеокарта is not None:
        данные["gpu_load"], данные["gpu_temp"] = видеокарта

    return данные


def _склонение(число: float, формы: tuple[str, str, str], знаков: int = 0) -> str:
    """«1 процент», «2 процента», «5 процентов».

    Ответ читают вслух, и «загружен на 2 процентов» выдаёт машину вернее любой
    интонации. Правило русского счётного падежа: 11–14 — всегда третья форма,
    дальше решает последняя цифра.

    `знаков` — сколько знаков после запятой ПОКАЗЫВАЕТСЯ. Согласовывать нужно с
    произнесённым числом, а не с исходным: 29,2 показывается как «29», и
    «29 гигабайта» звучит так же неправильно, как «2 процентов»."""
    число = round(число, знаков)
    if число != int(число):
        # «0,6 гигабайта», «2,5 часа» — дробь всегда просит вторую форму.
        return формы[1]
    целое = int(число)
    if 11 <= целое % 100 <= 14:
        return формы[2]
    остаток = целое % 10
    if остаток == 1:
        return формы[0]
    if 2 <= остаток <= 4:
        return формы[1]
    return формы[2]


_ПРОЦЕНТ = ("процент", "процента", "процентов")
_ГИГАБАЙТ = ("гигабайт", "гигабайта", "гигабайт")
_ГРАДУС = ("градус", "градуса", "градусов")
_ЧАС = ("час", "часа", "часов")
_СУТКИ = ("суток", "суток", "суток")


def alerts(данные: dict) -> list[str]:
    """Что стоит сказать вслух без отдельного вопроса."""
    поводы = []
    if данные.get("cpu", 0) >= ALERT_CPU:
        поводы.append(
            f"процессор загружен на {данные['cpu']:.0f} "
            f"{_склонение(данные['cpu'], _ПРОЦЕНТ)}")
    if данные.get("mem_percent", 0) >= ALERT_MEM:
        поводы.append(
            f"память занята на {данные['mem_percent']:.0f} "
            f"{_склонение(данные['mem_percent'], _ПРОЦЕНТ)}")
    if данные.get("disk_percent", 0) >= ALERT_DISK:
        осталось = данные.get("disk_free_gb", 0)
        поводы.append(
            f"на диске осталось {осталось:.1f} {_склонение(осталось, _ГИГАБАЙТ, 1)}")
    if данные.get("cpu_temp", 0) >= ALERT_TEMP:
        поводы.append(
            f"процессор нагрелся до {данные['cpu_temp']:.0f} "
            f"{_склонение(данные['cpu_temp'], _ГРАДУС)}")
    return поводы


def _фраза(данные: dict) -> str:
    """Ответ для произнесения вслух: без таблиц, знаков процента и мегабайт."""
    цпу = данные["cpu"]
    озу = данные["mem_percent"]
    занято = данные["mem_used_gb"]
    всего = данные["mem_total_gb"]
    части = [
        f"процессор загружен на {цпу:.0f} {_склонение(цпу, _ПРОЦЕНТ)}",
        f"память — на {озу:.0f}, это {занято:.1f} {_склонение(занято, _ГИГАБАЙТ, 1)} "
        f"из {всего:.0f}",
    ]

    if "gpu_load" in данные:
        части.append(f"видеокарта на {данные['gpu_load']:.0f} процентах")
    if "cpu_temp" in данные:
        t = данные["cpu_temp"]
        части.append(f"температура процессора {t:.0f} {_склонение(t, _ГРАДУС)}")

    свободно = данные["disk_free_gb"]
    части.append(f"на диске свободно {свободно:.0f} {_склонение(свободно, _ГИГАБАЙТ)}")

    if "battery_percent" in данные:
        заряд = данные["battery_percent"]
        состояние = "от сети" if данные.get("battery_plugged") else "от батареи"
        части.append(
            f"батарея {заряд:.0f} {_склонение(заряд, _ПРОЦЕНТ)}, {состояние}")

    часы = данные.get("uptime_hours", 0)
    if часы >= 48:
        суток = часы / 24
        части.append(f"машина работает без перезагрузки {суток:.0f} {_склонение(суток, _СУТКИ)}")
    elif часы >= 1:
        части.append(f"машина работает {часы:.0f} {_склонение(часы, _ЧАС)}")
    else:
        части.append("машина включена меньше часа назад")

    ответ = "Сэр, " + ", ".join(части) + "."

    поводы = alerts(данные)
    if поводы:
        ответ += " Обратил бы ваше внимание: " + ", ".join(поводы) + "."
    return ответ


def system_status(parameters: dict, player=None) -> str:
    данные = collect()
    if "error" in данные:
        return f"Не смог снять показания системы: {данные['error']}"

    if player:
        player.write_log(
            f"SYS: ЦПУ {данные['cpu']:.0f}% · ОЗУ {данные['mem_percent']:.0f}% · "
            f"диск {данные['disk_percent']:.0f}%"
        )
    return _фраза(данные)


# ─── Объявление для реестра действий ──────────────────────────────────────────
TOOL = {
    "name": "system_status",
    "description": (
        "Сообщает состояние этого компьютера: загрузку процессора и памяти, место "
        "на диске, температуру, видеокарту, батарею и время работы без перезагрузки. "
        "Вызывай, когда пользователь спрашивает: что с компьютером, как загружен "
        "процессор, сколько осталось памяти или места, не греется ли, сколько "
        "заряда, почему тормозит."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
        "required": [],
    },
    "handler": system_status,
}
