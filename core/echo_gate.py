"""Голос поверх музыки: пропустить человека, не пропустить колонки.

Раньше было «колонки громче порога — микрофон не слушаем вовсе». Из-за этого:
  • под музыкой Джарвис был глух к речи без имени;
  • замкнутый круг: приглушили музыку → колонки тише порога → музыка из
    колонок пошла в микрофон как «речь» → снова приглушить… — ползунки в
    микшере прыгали сами, а в Gemini уезжала песня.

Теперь сравниваем микрофон с тем, что СЕЙЧАС играет. Сколько музыки из колонок
доходит до микрофона (связь g = RMS микрофона / уровень колонок), зависит от
комнаты и громкости и узнаётся на ходу: берём нижнюю часть недавних отношений
(человек говорит редко, музыка — всё время, так что низкие отношения — это
эхо, высокие — голос). Кадр — голос, если микрофон заметно громче ожидаемого
эха: rms > K · g · уровень. Приглушили музыку — уровень упал, ожидаемое эхо
упало вместе с ним: круг не замыкается.
"""
from __future__ import annotations

import collections
import os


class EchoGate:
    MIN_LEVEL = 0.02        # тише — колонки молчат, решает обычный порог
    # Во сколько раз голос должен быть громче эха. Было 3.0: в игре с колонками
    # обычная (тем более невнятная) речь не дотягивала до «втрое громче звука
    # игры» и выбрасывалась — Джарвис был глух. Эхо при этом гуляет в пределах
    # ±20 % от выученной связи, так что 2.0 его по-прежнему не пропускает.
    K = float(os.getenv("MIC_ECHO_K", "2.0"))
    WARMUP = 25             # замеров до первого решения (~1.6 с музыки)

    def __init__(self, window: int = 300, quantile: float = 0.35):
        self._ratios: collections.deque[float] = collections.deque(maxlen=window)
        self._q = quantile

    def observe(self, mic_rms: float, level: float) -> None:
        if level >= self.MIN_LEVEL:
            self._ratios.append(mic_rms / level)

    def coupling(self) -> float | None:
        """Сколько RMS микрофона даёт единица уровня колонок; None — ещё не знаем."""
        if len(self._ratios) < self.WARMUP:
            return None
        s = sorted(self._ratios)
        return s[int(len(s) * self._q)]

    def music(self, level: float) -> bool:
        return level >= self.MIN_LEVEL

    def is_voice(self, mic_rms: float, level: float, threshold: float) -> bool:
        """Голос ли этот кадр. Колонки молчат — как раньше, по порогу."""
        if level < self.MIN_LEVEL:
            return mic_rms >= threshold
        g = self.coupling()
        if g is None:
            return False                       # связь ещё не знаем — осторожно, как раньше
        return mic_rms >= max(threshold * 1.5, self.K * g * level)
