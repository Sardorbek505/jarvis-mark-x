# Jarvis Ducking Controller
import enum, logging, math, threading, time
from typing import Optional

logger = logging.getLogger("jarvis-ducking")

class DuckingState(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    RESTORING = "restoring"

class DuckingController:
    def __init__(self, duck_ratio=0.20, attack_ms=50.0, release_ms=350.0, step_hz=60.0):
        self.duck_ratio = max(0.05, min(1.0, duck_ratio))
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.step_sec = 1.0 / step_hz
        self.state = DuckingState.IDLE
        self._lock = threading.Lock()
        self._original_volume = None
        self._current_volume = None
        self._endpoint_volume = None
        self._init_endpoint()
        self._active = True
        self._fade_thread = None
        self._fade_target = None
        self._fade_duration = 0.0
        self._fade_start_time = 0.0
        self._fade_start_vol = 1.0
        self._start_worker()

    def _init_endpoint(self):
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            speakers = AudioUtilities.GetSpeakers()
            if speakers:
                interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                import ctypes
                self._endpoint_volume = ctypes.cast(interface, ctypes.POINTER(IAudioEndpointVolume))
                self._original_volume = self._get_master_volume()
                self._current_volume = self._original_volume
                logger.info('Audio Ducking: CoreAudio ready')
        except Exception as e:
            logger.debug('Audio Ducking init note: %s', e)

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
        if self._endpoint_volume is not None:
            try:
                import pythoncom
                pythoncom.CoInitialize()
                self._endpoint_volume.SetMasterVolumeLevelScalar(vol, None)
            except Exception as e:
                logger.debug('Set volume error: %s', e)

    def _start_worker(self):
        def _loop():
            while self._active:
                time.sleep(self.step_sec)
                with self._lock:
                    if self._fade_target is None:
                        continue
                    now = time.time()
                    elapsed = (now - self._fade_start_time) * 1000.0
                    progress = min(1.0, elapsed / max(1.0, self._fade_duration))
                    blend = 0.5 * (1.0 - math.cos(progress * math.pi))
                    new_vol = self._fade_start_vol + (self._fade_target - self._fade_start_vol) * blend
                    self._set_master_volume(new_vol)
                    if progress >= 1.0:
                        self._fade_target = None
                        if self.state == DuckingState.RESTORING:
                            self.state = DuckingState.IDLE
        self._fade_thread = threading.Thread(target=_loop, daemon=True, name='ducking-worker')
        self._fade_thread.start()

    def _begin_fade(self, target_vol: float, duration_ms: float):
        current = self._get_master_volume()
        self._fade_start_vol = current
        self._fade_target = target_vol
        self._fade_duration = duration_ms
        self._fade_start_time = time.time()

    def set_state(self, new_state: DuckingState):
        with self._lock:
            if self.state == new_state and new_state != DuckingState.LISTENING:
                return
            prev_state = self.state
            self.state = new_state
            if prev_state == DuckingState.IDLE and new_state == DuckingState.LISTENING:
                self._original_volume = self._get_master_volume()
                target_duck = max(0.05, (self._original_volume or 1.0) * self.duck_ratio)
                logger.info('Audio Ducking: [ATTACK] -> %s in %smss', target_duck, self.attack_ms)
                self._begin_fade(target_duck, self.attack_ms)
            elif new_state in (DuckingState.THINKING, DuckingState.SPEAKING):
                target_duck = max(0.05, (self._original_volume or 1.0) * self.duck_ratio)
                if abs_diff := abs(self._get_master_volume() - target_duck) > 0.05:
                    self._begin_fade(target_duck, 30.0)
            elif new_state == DuckingState.RESTORING:
                target_restore = self._original_volume or 1.0
                logger.info('Audio Ducking: [RELEASE] -> %s in %sms', target_restore, self.release_ms)
                self._begin_fade(target_restore, self.release_ms)
            elif new_state == DuckingState.IDLE:
                if self._original_volume is not None:
                    self._begin_fade(self._original_volume, 100.0)

    def duck(self):
        self.set_state(DuckingState.LISTENING)

    def restore(self):
        self.set_state(DuckingState.RESTORING)

    def close(self):
        self._active = False
        if self._original_volume is not None:
            try:
                self._set_master_volume(self._original_volume)
            except Exception:
                pass

ducking_controller = DuckingController()
