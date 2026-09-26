"""JARVIS Mark X — Контроллер плавного приглушения звука (Audio Ducking) и управления медиа.

Управляет системной громкостью, отдельными аудиосессиями приложений (Spotify, Chrome, VK Видео)
и воспроизведением через чистую машину состояний:
  IDLE        -> Нормальная громкость (100%)
  LISTENING   -> Плавное затухание (Attack 40-60мс) до ~15-20% (-18 dB) или автопауза
  THINKING    -> Удержание пониженной громкости (Hold)
  SPEAKING    -> Удержание пониженной громкости во время ответа Джарвиса
  RESTORING   -> Плавное восстановление (Release 300-400мс) до исходного уровня
"""

import ctypes
import enum
import logging
import math
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger("jarvis-ducking")

# Виртуальные клавиши Windows Media
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_STOP       = 0xB2
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1


class DuckingState(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    RESTORING = "restoring"


class DuckingController:
    """Потокобезопасный контроллер Audio Ducking и управления медиа."""

    # Состояния, в которых музыка уже приглушена, а исходный уровень сохранён.
    _DUCKED_STATES = (
        DuckingState.LISTENING,
        DuckingState.THINKING,
        DuckingState.SPEAKING,
    )

    def __init__(
        self,
        duck_ratio: float = 0.20,       # Уровень приглушения (20% от исходного или -18dB)
        attack_ms: float = 50.0,        # Длительность затухания (мс)
        release_ms: float = 350.0,      # Длительность восстановления (мс)
        step_hz: float = 60.0,          # Частота обновления интерполяции (Гц)
        auto_pause_media: bool = False, # Ставить ли плеер на паузу вместо затухания
        state_path: Optional[str] = None,  # куда сохранять громкость «до» (см. _persist)
    ):
        self.duck_ratio = max(0.05, min(1.0, duck_ratio))
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.step_sec = 1.0 / step_hz
        self.auto_pause_media = auto_pause_media

        self.state = DuckingState.IDLE
        self._lock = threading.Lock()

        # Громкости
        self._original_volume: Optional[float] = None
        self._current_volume: Optional[float] = None
        # Ключ — PID процесса (int), значение — громкость сессии до приглушения.
        self._saved_session_vols: Dict[int, float] = {}
        self._saved_names: Dict[int, str] = {}
        # Громкость, которую не успели вернуть (Джарвис закрыли или он упал,
        # пока музыка была приглушена): имя процесса -> громкость «до».
        # Windows помнит громкость приложений, и без этого CS2, Steam, браузер
        # оставались тихими навсегда, а следующее приглушение запоминало тихий
        # уровень как исходный и роняло его ещё ниже.
        self._state_path = state_path
        self._pending_heal: Dict[str, float] = self._load_pending()
        self._heal_checked = 0.0

        # Windows CoreAudio Endpoint
        self._endpoint_volume = None
        self._init_endpoint()

        # Поток интерполяции
        self._active = True
        self._fade_thread: Optional[threading.Thread] = None
        self._fade_target: Optional[float] = None
        self._fade_duration: float = 0.0
        self._fade_start_time: float = 0.0
        self._fade_start_vol: float = 1.0

        self._start_worker()

    # ── Громкость: виртуальный уровень + сессии других программ ──────────────
    #
    # Раньше приглушалась ОБЩАЯ громкость устройства. Джарвис играет через
    # это же устройство, поэтому его ответ звучал на 20%, а при запуске из
    # исходников сессия python.exe резалась ещё и отдельно — до ~4%, почти
    # неслышно. Теперь «громкость» здесь — виртуальный уровень 0..1, а
    # применяется он только к сессиям ДРУГИХ программ (Spotify, браузер,
    # плеер). Общую громкость Windows и свой процесс не трогаем никогда.
    #
    # Работа с CoreAudio (COM) — только в собственном потоке: раньше она шла
    # прямо из колбэка микрофона и срывала первые кадры речи.

    _HOLD_SEC = 6.0      # приглушили, а ответа так и нет — вернуть звук
    _SPEAK_HOLD_SEC = 90.0   # «говорит» дольше — ответ завис, звук вернуть

    # ── сохранение «до приглушения» на диск ──────────────────────────────────
    def _path(self) -> Optional[str]:
        if self._state_path is None:
            try:
                from core.paths import get_user_data_dir
                self._state_path = str(get_user_data_dir() / "ducked_sessions.json")
            except Exception:
                self._state_path = ""
        return self._state_path or None

    def _load_pending(self) -> Dict[str, float]:
        path = self._path()
        if not path:
            return {}
        try:
            import json
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return {str(k): float(v) for k, v in data.items() if 0.0 < float(v) <= 1.0}
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.debug("ducked_sessions.json: %s", e)
            return {}

    def _persist(self):
        """На диск — всё, что ещё предстоит вернуть: приглушённое сейчас и
        не возвращённое с прошлого раза."""
        path = self._path()
        if not path:
            return
        data = dict(self._pending_heal)
        for pid, vol in self._saved_session_vols.items():
            name = self._saved_names.get(pid)
            if name:
                data[name] = vol
        try:
            import json
            import os
            if data:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, path)
            elif os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logger.debug("ducked_sessions.json: %s", e)

    def _heal(self):
        """Вернуть громкость программам, которым её не вернули в прошлый раз
        (в том числе запущенным позже — проверяется раз в пару секунд)."""
        if not self._pending_heal:
            return
        healed = False
        for _pid, name, ctl in self._sessions():
            if name in self._pending_heal:
                try:
                    ctl.SetMasterVolume(self._pending_heal[name], None)
                    logger.info("Вернул громкость «%s»: %.0f%%", name, self._pending_heal[name] * 100)
                except Exception as e:
                    logger.debug("Heal %s: %s", name, e)
                    continue
                healed = True
        if healed:
            names = {n for _p, n, _c in self._sessions()}
            self._pending_heal = {n: v for n, v in self._pending_heal.items() if n not in names}
            self._persist()

    def _init_endpoint(self):
        self._endpoint_volume = None          # общую громкость не трогаем
        self._original_volume = 1.0
        self._current_volume = 1.0
        self._applied_level = 1.0
        self._ducked_at = 0.0
        self._pycaw_ok = True

    def _get_master_volume(self) -> float:
        return self._current_volume if self._current_volume is not None else 1.0

    def _set_master_volume(self, vol: float):
        self._current_volume = max(0.0, min(1.0, vol))

    def _sessions(self):
        """Аудиосессии чужих программ. Пусто, если pycaw недоступен."""
        if not self._pycaw_ok:
            return []
        try:
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
        except Exception:
            self._pycaw_ok = False
            logger.info("Audio Ducking: pycaw не установлен — музыка приглушаться не будет")
            return []
        import os
        own = os.getpid()
        out = []
        for session in AudioUtilities.GetAllSessions():
            pid = session.ProcessId
            if not pid or pid == own:
                continue
            try:
                name = (session.Process.name() if session.Process else "").lower()
            except Exception:
                name = ""
            if any(k in name for k in ("jarvis", "audiodg", "system")):
                continue
            out.append((pid, name, session._ctl.QueryInterface(ISimpleAudioVolume)))
        return out

    def _apply_sessions(self, level: float):
        """Выставляет чужим сессиям level от их громкости до приглушения."""
        if abs(level - self._applied_level) < 0.01:
            return
        try:
            if level >= 0.999:
                self._restore_active_sessions()
            else:
                grew = False
                for pid, name, ctl in self._sessions():
                    if pid not in self._saved_session_vols:
                        # Не вернули с прошлого раза — «до» берём оттуда, а
                        # не нынешний (уже заниженный) уровень.
                        self._saved_session_vols[pid] = self._pending_heal.pop(name, None) \
                            or float(ctl.GetMasterVolume())
                        self._saved_names[pid] = name
                        grew = True
                    ctl.SetMasterVolume(max(0.02, self._saved_session_vols[pid] * level), None)
                if grew:
                    self._persist()
            self._applied_level = level
        except Exception as e:
            logger.debug("Duck sessions note: %s", e)

    def _duck_active_sessions(self, target_ratio: float):
        self._apply_sessions(target_ratio)

    def _restore_active_sessions(self):
        """Возвращает чужим программам громкость до приглушения."""
        if self._saved_session_vols:
            restored = set()
            try:
                for pid, _name, ctl in self._sessions():
                    if pid in self._saved_session_vols:
                        ctl.SetMasterVolume(self._saved_session_vols[pid], None)
                        restored.add(pid)
            except Exception as e:
                logger.debug("Restore sessions note: %s", e)
            # Программу закрыли, пока она была приглушена, — Windows запомнил
            # её тихой. Вернём, когда она снова заиграет.
            for pid, vol in self._saved_session_vols.items():
                name = self._saved_names.get(pid)
                if pid not in restored and name:
                    self._pending_heal.setdefault(name, vol)
            self._saved_session_vols.clear()
            self._saved_names.clear()
            self._persist()
        self._applied_level = 1.0

    def _start_worker(self):
        def _loop():
            try:
                import comtypes
                comtypes.CoInitialize()
            except Exception:
                pass
            while self._active:
                time.sleep(self.step_sec)
                with self._lock:
                    if self._fade_target is not None:
                        now = time.time()
                        elapsed = (now - self._fade_start_time) * 1000.0
                        progress = min(1.0, elapsed / max(1.0, self._fade_duration))
                        # S-curve / Экспоненциальная плавная интерполяция (Cos)
                        blend = 0.5 * (1.0 - math.cos(progress * math.pi))
                        self._set_master_volume(
                            self._fade_start_vol + (self._fade_target - self._fade_start_vol) * blend)
                        if progress >= 1.0:
                            self._fade_target = None
                            if self.state == DuckingState.RESTORING:
                                self.state = DuckingState.IDLE
                    # Приглушили на звук, а Джарвис так и не заговорил (кашель,
                    # чужая речь, беззвучный инструмент) — отпускаем сами.
                    elif ((self.state in (DuckingState.LISTENING, DuckingState.THINKING)
                           and time.time() - self._ducked_at > self._HOLD_SEC)
                          or (self.state == DuckingState.SPEAKING
                              and time.time() - self._ducked_at > self._SPEAK_HOLD_SEC)):
                        self.state = DuckingState.RESTORING
                        self._begin_fade(self._original_volume or 1.0, self.release_ms)
                    level = self._get_master_volume()
                    idle = self.state == DuckingState.IDLE
                self._apply_sessions(level)      # COM — вне блокировки
                if idle and self._pending_heal and time.time() - self._heal_checked > 2.0:
                    self._heal_checked = time.time()
                    try:
                        self._heal()
                    except Exception as e:
                        logger.debug("Heal: %s", e)
            self._restore_active_sessions()

        self._fade_thread = threading.Thread(target=_loop, daemon=True, name="ducking-worker")
        self._fade_thread.start()

    def _begin_fade(self, target_vol: float, duration_ms: float):
        current = self._get_master_volume()
        self._fade_start_vol = current
        self._fade_target = target_vol
        self._fade_duration = duration_ms
        self._fade_start_time = time.time()

    def _capture_original_volume(self):
        """Запоминает громкость «до приглушения».

        Читать устройство можно только когда фейд не в полёте: иначе поймаем
        промежуточный уровень и запомним его как исходный, и каждый следующий
        цикл «приглушить — вернуть» занижал бы громкость всё сильнее.
        """
        if self._fade_target is not None and self._original_volume is not None:
            return
        self._original_volume = self._get_master_volume()

    def set_state(self, new_state: DuckingState):
        """Управление состоянием дакинга."""
        with self._lock:
            if self.state == new_state and new_state != DuckingState.LISTENING:
                return

            prev_state = self.state
            self.state = new_state

            if new_state == DuckingState.LISTENING:
                # Приглушаем из любого состояния, где музыка ещё громкая, —
                # включая RESTORING. Раньше условие было `prev == IDLE`, и
                # если пользователь заговаривал во время 350 мс возврата
                # громкости, duck() молча не делал ничего: release-фейд
                # доводил музыку до 100% прямо поверх речи.
                if prev_state in self._DUCKED_STATES:
                    return

                self._capture_original_volume()
                self._ducked_at = time.time()
                target_duck = max(0.05, (self._original_volume or 1.0) * self.duck_ratio)
                logger.debug("Audio Ducking: [ATTACK] -> %.0f%% за %.0f мс", target_duck * 100, self.attack_ms)
                self._begin_fade(target_duck, self.attack_ms)

                if self.auto_pause_media:
                    self.pause_media()

            elif new_state in (DuckingState.THINKING, DuckingState.SPEAKING):
                self._ducked_at = time.time()
                target_duck = max(0.05, (self._original_volume or 1.0) * self.duck_ratio)
                if abs(self._get_master_volume() - target_duck) > 0.05:
                    self._begin_fade(target_duck, 30.0)

            elif new_state == DuckingState.RESTORING:
                target_restore = self._original_volume or 1.0
                logger.debug("Audio Ducking: [RELEASE] -> %.0f%% за %.0f мс", target_restore * 100, self.release_ms)
                self._begin_fade(target_restore, self.release_ms)

            elif new_state == DuckingState.IDLE:
                if self._original_volume is not None:
                    self._begin_fade(self._original_volume, 100.0)

    def duck(self):
        """Шорткат для активации приглушения."""
        self.set_state(DuckingState.LISTENING)

    def restore(self):
        """Шорткат для плавного восстановления громкости."""
        self.set_state(DuckingState.RESTORING)

    def pause_media(self):
        """Отправка аппаратной команды Pause/Play на медиа-плееры Windows."""
        try:
            ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 2, 0)
            logger.info("DuckingController: VK_MEDIA_PLAY_PAUSE sent")
        except Exception as e:
            logger.debug("pause_media error: %s", e)

    def stop_media(self):
        """Остановка воспроизведения медиа."""
        try:
            ctypes.windll.user32.keybd_event(VK_MEDIA_STOP, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_STOP, 0, 2, 0)
            logger.info("DuckingController: VK_MEDIA_STOP sent")
        except Exception as e:
            logger.debug("stop_media error: %s", e)

    def close(self):
        """Остановить поток и вернуть громкость программам. Зовётся при выходе:
        Windows помнит громкость приложений, и приглушённый Spotify остался
        бы на 20% и после перезапуска."""
        self._active = False
        if self._fade_thread and self._fade_thread is not threading.current_thread():
            self._fade_thread.join(timeout=1.0)
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        self._restore_active_sessions()


# ─── Ленивый общий экземпляр ──────────────────────────────────────────────────
# Конструктор поднимает CoreAudio-эндпоинт и фоновый поток интерполяции, поэтому
# создавать его на импорте модуля нельзя: любой `import core.ducking_controller`
# — из теста, бенчмарка или утилиты — заводил поток на 60 Гц и брал в руки
# системную громкость, даже если дакинг в этом процессе никому не нужен.
_singleton: Optional[DuckingController] = None
_singleton_lock = threading.Lock()


def get_ducking_controller() -> DuckingController:
    """Общий контроллер дакинга; создаётся при первом обращении."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = DuckingController()
    return _singleton


def __getattr__(name: str):
    """Поддерживает привычное `from ... import ducking_controller` без импорт-эффекта."""
    if name == "ducking_controller":
        return get_ducking_controller()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
