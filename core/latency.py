"""Замер задержек голосового цикла.

Вопрос, ради которого это писалось: сколько проходит от момента, когда человек
замолчал, до момента, когда из динамиков пошёл голос. Ощущения тут врут —
нужны числа.

Цепочка одного хода:

    последний громкий кадр микрофона
        │  (сеть + распознавание)
        ├──> первый фрагмент расшифровки       "слышит"
        │  (обдумывание модели, вызовы инструментов)
        ├──> первый байт аудио от Gemini       "отвечает"  ← главная метрика
        │  (очередь + открытие устройства)
        └──> первый кадр в динамиках           "звучит"

Требования к коду в этом файле:
  * ничего не бросать наружу — замер не имеет права уронить ассистента;
  * не блокировать: mark_voice_frame зовётся из аудио-колбэка, где любая
    блокировка означает щелчки и потерю кадров. Поэтому только присваивание
    float, без локов;
  * выключаться одной переменной окружения JARVIS_LATENCY=0.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

_logger = logging.getLogger(__name__)

_ENABLED = os.getenv("JARVIS_LATENCY", "1") not in ("0", "false", "False", "")

# Пауза, после которой считаем, что человек договорил, а не просто взял вдох.
_TURN_GAP_SEC = float(os.getenv("JARVIS_LATENCY_GAP", "0.8"))


def _ms(start: float, end: float) -> int:
    return int((end - start) * 1000)


@dataclass
class _Stat:
    """Копилка по одной метрике: сколько раз, и какие значения."""

    name: str
    samples: list[int] = field(default_factory=list)

    def add(self, value_ms: int) -> None:
        self.samples.append(value_ms)

    @property
    def count(self) -> int:
        return len(self.samples)

    def percentile(self, p: float) -> int:
        if not self.samples:
            return 0
        ordered = sorted(self.samples)
        idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * p)))
        return ordered[idx]

    @property
    def median(self) -> int:
        return self.percentile(0.5)

    @property
    def worst(self) -> int:
        return max(self.samples) if self.samples else 0


class LatencyTracker:
    """Секундомер голосового хода.

    Один экземпляр на сессию. Все методы безопасно зовутся сколько угодно раз:
    лишние вызовы внутри уже начатого хода игнорируются, чтобы в статистику
    попадал именно первый отклик, а не каждый последующий кадр.
    """

    def __init__(self, enabled: bool = _ENABLED, sink=None):
        self.enabled = enabled
        self._sink = sink                  # куда дублировать строку (UI), опционально
        # Сентинел — None, а не 0.0: perf_counter() имеет право вернуть ноль,
        # и тогда «отметка есть» стало бы неотличимо от «отметки нет».
        self._last_frame: float | None = None   # пишется из аудио-колбэка
        self._heard: float | None = None
        self._answered: float | None = None
        self._played: float | None = None
        self._tool_ms = 0
        self._tool_names: list[str] = []
        self._stats = {
            "heard": _Stat("слышит"),
            "answered": _Stat("отвечает"),
            "played": _Stat("звучит"),
        }

    # ── отметки на горячем пути ───────────────────────────────────────────────

    def mark_voice_frame(self) -> None:
        """Громкий кадр ушёл в облако. Зовётся из потока sounddevice."""
        if not self.enabled:
            return
        now = time.perf_counter()
        # Пауза длиннее _TURN_GAP_SEC означает новую реплику: закрываем прошлый
        # ход, чтобы его хвост не приписался к следующему вопросу.
        if self._last_frame is not None and (now - self._last_frame) > _TURN_GAP_SEC:
            self._reset_turn()
        self._last_frame = now

    def mark_transcript(self) -> None:
        """Пришёл первый фрагмент расшифровки сказанного."""
        if not self.enabled or self._heard is not None or self._last_frame is None:
            return
        self._heard = time.perf_counter()

    def mark_answer_audio(self) -> None:
        """Первый байт ответного аудио от Gemini — главная метрика."""
        if not self.enabled or self._answered is not None or self._last_frame is None:
            return
        self._answered = time.perf_counter()

    def mark_playback(self) -> None:
        """Первый кадр реально отдан в звуковое устройство."""
        if not self.enabled or self._played is not None or self._last_frame is None:
            return
        self._played = time.perf_counter()

    def add_tool(self, name: str, elapsed_ms: int) -> None:
        """Инструмент отработал внутри хода — его время объясняет паузу."""
        if not self.enabled:
            return
        self._tool_ms += elapsed_ms
        self._tool_names.append(f"{name} {elapsed_ms}мс")

    # ── конец хода ────────────────────────────────────────────────────────────

    def mark_turn_complete(self) -> None:
        """Модель закончила ход: считаем и печатаем строку по нему."""
        if not self.enabled or self._last_frame is None:
            return
        try:
            self._report()
        except Exception as exc:                      # замер не роняет ассистента
            _logger.debug("Замер задержки не сложился: %s", exc)
        finally:
            self._reset_turn()
            # Сбрасываем и точку отсчёта: следующая реплика может быть
            # проактивной — Джарвис заговорит сам, без вопроса. Мерить её от
            # давнего кадра микрофона значит вписать в статистику минуты.
            self._last_frame = None

    def _report(self) -> None:
        base = self._last_frame
        parts = []
        for key, when in (
            ("heard", self._heard),
            ("answered", self._answered),
            ("played", self._played),
        ):
            if when is None:
                continue
            value = _ms(base, when)
            self._stats[key].add(value)
            parts.append(f"{self._stats[key].name} {value}мс")

        if not parts:
            return

        line = "⏱  " + " · ".join(parts)
        if self._tool_ms:
            line += f"  [инструменты: {', '.join(self._tool_names)}]"

        _logger.info(line)
        print(f"[ЗАМЕР] {line}")
        if self._sink:
            try:
                self._sink(f"SYS: {line}")
            except Exception:
                pass

    def _reset_turn(self) -> None:
        self._heard = None
        self._answered = None
        self._played = None
        self._tool_ms = 0
        self._tool_names = []

    # ── итог за сессию ────────────────────────────────────────────────────────

    def summary(self) -> str:
        """Сводка за всю сессию. Печатается при завершении."""
        if not self.enabled:
            return ""
        answered = self._stats["answered"]
        if not answered.count:
            return "⏱  Замеров не набралось — ни одного полного голосового хода."

        rows = [f"⏱  Задержки за сессию ({answered.count} ходов), от конца речи:"]
        for stat in self._stats.values():
            if stat.count:
                rows.append(
                    f"    {stat.name:<10} медиана {stat.median}мс · "
                    f"90-й процентиль {stat.percentile(0.9)}мс · худшая {stat.worst}мс"
                )
        return "\n".join(rows)
