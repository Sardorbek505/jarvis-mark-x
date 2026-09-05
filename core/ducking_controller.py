"""JARVIS Mark X — Контроллер плавного приглушения звука (Audio Ducking) и управления медиа.

Управляет системной громкостью, отдельными аудиосессиями приложений (Spotify, Chrome, VK Видео, Яндекс Музыка)
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
import os
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
        duck_master: bool = False,      # Приглушать ли общий Master Volume (по умолч. False: глушатся только медиа-приложения, голос Джарвиса звучит на 100%)
    ):
        self.duck_ratio = max(0.05, min(1.0, duck_ratio))
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.step_sec = 1.0 / step_hz
        self.auto_pause_media = auto_pause_media
        self.duck_master = duck_master

        self.state = DuckingState.IDLE
        self._lock = threading.Lock()

        # Громкости
        self._original_volume: Optional[float] = None
        self._current_volume: Optional[float] = None
        # Ключ — PID процесса (int), значение — громкость сессии до приглушения.
        self._saved_session_vols: Dict[int, float] = {}

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

    def _init_endpoint(self):
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            speakers = AudioUtilities.GetSpeakers()
            if speakers:
                interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self._endpoint_volume = ctypes.cast(interface, ctypes.POINTER(IAudioEndpointVolume))
                self._original_volume = self._get_master_volume()
                self._current_volume = self._original_volume
                logger.info("Audio Ducking: CoreAudio endpoint ready")
        except Exception as e:
            logger.debug("Audio Ducking: CoreAudio init note: %s", e)

    def _get_master_volume(self) -> float:
        if self._endpoint_volume is not None:
            try:
                import pythoncom
                pythoncom.CoInitialize()
                return float(self._endpoint_volume.GetMasterVolumeLevelScalar())
            except Exception:
                pass
        return self._original_volume or 1.0

    def _set_master_volume(self, vol: float):
        vol = max(0.0, min(1.0, vol))
        self._current_volume = vol
        if self._endpoint_volume is not None and self.duck_master:
            try:
                import pythoncom
                pythoncom.CoInitialize()
                self._endpoint_volume.SetMasterVolumeLevelScalar(vol, None)
            except Exception as e:
                logger.debug("Set volume error: %s", e)

    def _is_jarvis_or_system_process(self, session) -> bool:
        """Проверяет, принадлежит ли сессия самому Джарвису или системным звуковым службам."""
        try:
            if session.ProcessId == os.getpid():
                return True
            proc_name = (session.Process.name() if session.Process else "").lower()
            if not proc_name:
                return True
            # Исключаем интерпретатор Python, процесс Джарвиса и системные драйверы
            if any(k in proc_name for k in ("python", "jarvis", "denoise", "audiodg", "system")):
                return True
        except Exception:
            return True
        return False

    def _discover_and_save_sessions(self):
        """Сканирует активные медиа-сессии Windows (Spotify, Chrome, Edge, VK) и фиксирует их громкость."""
        try:
            import pythoncom
            pythoncom.CoInitialize()
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

            sessions = AudioUtilities.GetAllSessions()
            for session in sessions:
                if self._is_jarvis_or_system_process(session):
                    continue
                pid = session.ProcessId
                if pid not in self._saved_session_vols:
                    try:
                        volume_ctl = session._ctl.QueryInterface(ISimpleAudioVolume)
                        vol = float(volume_ctl.GetMasterVolume())
                        if vol > 0.0:
                            self._saved_session_vols[pid] = vol
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Discover sessions error: %s", e)

    def _apply_session_ducking(self, ratio: float):
        """Интерполирует громкость всех сохранённых медиа-сессий."""
        if not self._saved_session_vols:
            return
        try:
            import pythoncom
            pythoncom.CoInitialize()
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

            sessions = AudioUtilities.GetAllSessions()
            for session in sessions:
                pid = session.ProcessId
                if pid in self._saved_session_vols:
                    orig = self._saved_session_vols[pid]
                    try:
                        volume_ctl = session._ctl.QueryInterface(ISimpleAudioVolume)
                        # Плавный уровень для данного приложения
                        new_vol = max(0.02, min(1.0, orig * ratio))
                        volume_ctl.SetMasterVolume(new_vol, None)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Apply session ducking error: %s", e)

    def _finish_restore_sessions(self):
        """Восстанавливает точную исходную громкость сессий и очищает кэш."""
        if not self._saved_session_vols:
            return
        try:
            import pythoncom
            pythoncom.CoInitialize()
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

            sessions = AudioUtilities.GetAllSessions()
            for session in sessions:
                pid = session.ProcessId
                if pid in self._saved_session_vols:
                    orig = self._saved_session_vols[pid]
                    try:
                        volume_ctl = session._ctl.QueryInterface(ISimpleAudioVolume)
                        volume_ctl.SetMasterVolume(orig, None)
                    except Exception:
                        pass
            self._saved_session_vols.clear()
        except Exception as e:
            logger.debug("Finish restore sessions error: %s", e)

    def _start_worker(self):
        def _loop():
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass

            while self._active:
                time.sleep(self.step_sec)
                with self._lock:
                    if self._fade_target is None:
                        continue

                    now = time.time()
                    elapsed = (now - self._fade_start_time) * 1000.0
                    progress = min(1.0, elapsed / max(1.0, self._fade_duration))

                    # S-curve / Экспоненциальная плавная интерполяция (Cos)
                    blend = 0.5 * (1.0 - math.cos(progress * math.pi))
                    current_ratio = self._fade_start_vol + (self._fade_target - self._fade_start_vol) * blend

                    # Применяем интерполированный уровень к медиа-сессиям
                    self._apply_session_ducking(current_ratio)

                    if self.duck_master:
                        self._set_master_volume(current_ratio)

                    if progress >= 1.0:
                        if self.state == DuckingState.RESTORING:
                            self._finish_restore_sessions()
                            self.state = DuckingState.IDLE
                        self._fade_target = None

        self._fade_thread = threading.Thread(target=_loop, daemon=True, name="ducking-worker")
        self._fade_thread.start()

    def _begin_fade(self, target_vol: float, duration_ms: float):
        current = self._fade_target if self._fade_target is not None else (self._current_volume or 1.0)
        self._fade_start_vol = current
        self._fade_target = target_vol
        self._fade_duration = duration_ms
        self._fade_start_time = time.time()

    def _capture_original_volume(self):
        """Запоминает громкость «до приглушения».
        
        Читать устройство можно только когда фейд не в полёте: иначе поймаем
        промежуточный уровень и запомним его как исходный.
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
                # Приглушаем из любого состояния, где музыка ещё громкая, включая RESTORING
                if prev_state in self._DUCKED_STATES:
                    return

                self._capture_original_volume()
                target_duck = self.duck_ratio
                logger.info("Audio Ducking: [ATTACK] -> %.0f%% за %.0f мс", target_duck * 100, self.attack_ms)
                self._discover_and_save_sessions()
                self._begin_fade(target_duck, self.attack_ms)

                if self.auto_pause_media:
                    self.pause_media()

            elif new_state in (DuckingState.THINKING, DuckingState.SPEAKING):
                target_duck = self.duck_ratio
                if self._fade_target is None or abs(self._fade_target - target_duck) > 0.05:
                    self._begin_fade(target_duck, 30.0)

            elif new_state == DuckingState.RESTORING:
                target_restore = 1.0
                logger.info("Audio Ducking: [RELEASE] -> 100%% за %.0f мс", self.release_ms)
                self._begin_fade(target_restore, self.release_ms)

            elif new_state == DuckingState.IDLE:
                if self._original_volume is not None and self.duck_master:
                    self._begin_fade(self._original_volume, 100.0)
                self._finish_restore_sessions()

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
        self._active = False
        if self._original_volume is not None:
            try:
                if self.duck_master:
                    self._set_master_volume(self._original_volume)
                self._finish_restore_sessions()
            except Exception:
                pass


# ─── Ленивый общий экземпляр ──────────────────────────────────────────────────
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
