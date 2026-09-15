"""JARVIS Mark X — Доменные модели и исключения медиасистемы.

Содержит структурированные объекты запросов, состояний сессии,
возможностей контроллеров (Capabilities) и иерархию ошибок.
"""

from dataclasses import dataclass, field
import enum
from typing import Any, Dict, List, Optional
import uuid


class MediaType(str, enum.Enum):
    MOVIE = "movie"
    SERIES = "series"
    VIDEO = "video"
    MUSIC = "music"
    PODCAST = "podcast"
    UNKNOWN = "unknown"


class MediaState(str, enum.Enum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


# ─── Исключения медиасистемы ──────────────────────────────────────────────────
class MediaException(Exception):
    """Базовое исключение медиасистемы JARVIS."""
    def __init__(self, message: str, error_code: str = "MEDIA_ERROR"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class MediaNotFound(MediaException):
    """Запрошенный контент не найден провайдерами."""
    def __init__(self, title: str):
        super().__init__(f"Медиа-контент «{title}» не найден, сэр.", "MEDIA_NOT_FOUND")


class ProviderUnavailable(MediaException):
    """Провайдер недоступен или не отвечает."""
    def __init__(self, provider_name: str):
        super().__init__(f"Провайдер {provider_name} недоступен, сэр.", "PROVIDER_UNAVAILABLE")


class PlaybackFailed(MediaException):
    """Сбой при попытке воспроизведения."""
    def __init__(self, reason: str):
        super().__init__(f"Ошибка воспроизведения: {reason}", "PLAYBACK_FAILED")


class UnsupportedMediaAction(MediaException):
    """Действие не поддерживается текущим контроллером или провайдером."""
    def __init__(self, action: str):
        super().__init__(f"Действие «{action}» не поддерживается для этого медиа, сэр.", "UNSUPPORTED_ACTION")


class NoActiveMediaSession(MediaException):
    """Отсутствует активная медиа-сессия."""
    def __init__(self):
        super().__init__("Нет активного медиа-воспроизведения, сэр.", "NO_ACTIVE_SESSION")


class BrowserNotFound(MediaException):
    """Браузер или окно с плеером не найдены."""
    def __init__(self):
        super().__init__("Не удалось найти окно браузера с плеером, сэр.", "BROWSER_NOT_FOUND")


# ─── Возможности контроллеров (Capabilities) ──────────────────────────────────
@dataclass
class MediaCapabilities:
    """Флаги поддерживаемых функций конкретного контроллера."""
    play_pause: bool = True
    seek_relative: bool = True
    seek_absolute: bool = False
    seek_percent: bool = True
    volume_control: bool = True
    set_player_volume: bool = True
    set_system_volume: bool = True
    mute_control: bool = True
    fullscreen: bool = True
    next_track: bool = False
    previous_track: bool = False
    state_readback: bool = False
    position_readback: bool = False
    duration_readback: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "play_pause": self.play_pause,
            "seek_relative": self.seek_relative,
            "seek_absolute": self.seek_absolute,
            "seek_percent": self.seek_percent,
            "volume_control": self.volume_control,
            "set_player_volume": self.set_player_volume,
            "set_system_volume": self.set_system_volume,
            "mute_control": self.mute_control,
            "fullscreen": self.fullscreen,
            "next_track": self.next_track,
            "previous_track": self.previous_track,
            "state_readback": self.state_readback,
            "position_readback": self.position_readback,
            "duration_readback": self.duration_readback,
        }


# ─── Структурированный запрос (MediaRequest) ─────────────────────────────────
@dataclass
class MediaRequest:
    """Структурированный запрос пользователя на воспроизведение контента."""
    media_type: MediaType = MediaType.UNKNOWN
    title: str = ""
    season: Optional[int] = None
    episode: Optional[int] = None
    provider: str = "auto"
    raw_query: str = ""
    url: Optional[str] = None
    action: str = "play"


@dataclass
class MediaIntentResult:
    """Результат семантического разбора интента с оценкой уверенности и уточнениями."""
    request: MediaRequest
    confidence: float = 1.0
    missing_fields: List[str] = field(default_factory=list)
    needs_clarification: bool = False


def parse_media_request(raw_query: str, parameters: Optional[Dict[str, Any]] = None) -> MediaRequest:
    """Парсит неструктурированную команду в структурированный MediaRequest через MediaIntentParser."""
    from core.media.parser import MediaIntentParser
    return MediaIntentParser.parse(raw_query, parameters)


MediaRequest.from_query = staticmethod(parse_media_request)


# ─── Активная медиа-сессия (MediaSession) ─────────────────────────────────────
@dataclass
class MediaSession:
    """Активное медиа-воспроизведение под управлением JARVIS."""
    media_type: MediaType = MediaType.UNKNOWN
    title: str = ""
    provider: str = "auto"
    status: MediaState = MediaState.IDLE
    position_seconds: float = 0.0
    duration_seconds: float = 0.0
    volume: int = 100
    muted: bool = False
    fullscreen: bool = False
    window_handle: Optional[Any] = None
    source_url: Optional[str] = None
    browser: str = "chrome"
    window_id: Optional[str] = None
    tab_id: Optional[str] = None
    controller: Optional[Any] = field(default=None, repr=False)
    capabilities: MediaCapabilities = field(default_factory=MediaCapabilities)
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancelled: bool = False

    @property
    def url(self) -> Optional[str]:
        return self.source_url

    @url.setter
    def url(self, value: Optional[str]):
        self.source_url = value

    def to_summary(self) -> str:
        s = f"«{self.title}» [{self.provider.upper()}] ({self.status.value})"
        if self.duration_seconds > 0:
            pos_m = int(self.position_seconds // 60)
            pos_s = int(self.position_seconds % 60)
            dur_m = int(self.duration_seconds // 60)
            dur_s = int(self.duration_seconds % 60)
            s += f" — {pos_m:02d}:{pos_s:02d} / {dur_m:02d}:{dur_s:02d}"
        return s