"""Новый контент глушит играющий ДО своего поиска, а не после.

Пользователь: «если играет музыка и я говорю поставить фильм — сначала
выключи музыку, потом ставь фильм, и очень быстро». Раньше музыка молчала
только после того, как фильм уже открылся (закрытие предыдущей сессии в
tracker.set_active_session), то есть несколько секунд играли оба.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.media.controllers.base import BaseMediaController
from core.media.controllers.spotify import SpotifyMediaController
from core.media.models import MediaCapabilities, MediaSession, MediaState, MediaType
from core.media.providers.base import ProviderResult
from core.media.orchestrator import MediaOrchestrator
from core.media.state import get_media_tracker


class _Recorder(BaseMediaController):
    def __init__(self, log, name):
        super().__init__(capabilities=MediaCapabilities(play_pause=True))
        self.log, self.name = log, name

    def play(self):
        self.log.append(f"{self.name}:play"); return "ok"

    def pause(self):
        self.log.append(f"{self.name}:pause"); return "ok"

    def toggle_playback(self):
        return "ok"

    def stop(self):
        self.log.append(f"{self.name}:stop"); return "ok"

    def close(self):
        self.log.append(f"{self.name}:close"); return "ok"

    def get_state(self):
        return MediaState.PLAYING


@pytest.fixture
def orchestrator():
    tracker = get_media_tracker()
    tracker.clear_active_session()
    orch = MediaOrchestrator()
    yield orch
    tracker.clear_active_session()


def _session(controller, media_type=MediaType.MUSIC):
    return MediaSession(session_id="s1", media_type=media_type, title="t", provider="spotify",
                        status=MediaState.PLAYING, controller=controller,
                        capabilities=controller.capabilities)


def test_active_music_is_paused_before_new_content_is_searched(orchestrator):
    log = []
    music = _Recorder(log, "music")
    orchestrator.tracker.set_active_session(_session(music))

    def fake_open(req):
        log.append("router:open")
        return ProviderResult(success=True, url="u", message="Включаю фильм, сэр.",
                              controller=_Recorder(log, "movie"), provider="vkvideo")

    with patch.object(orchestrator.router, "resolve_and_open", side_effect=fake_open):
        orchestrator.play_media("Гладиатор", {"media_type": "movie"})

    assert log.index("music:pause") < log.index("router:open"), log


def test_preempt_does_not_touch_already_stopped_session(orchestrator):
    log = []
    music = _Recorder(log, "music")
    s = _session(music)
    s.status = MediaState.STOPPED
    orchestrator.tracker.set_active_session(s)

    with patch.object(orchestrator.router, "resolve_and_open",
                      return_value=ProviderResult(success=True, url="u", message="ok",
                                                  controller=_Recorder(log, "movie"), provider="vk")):
        orchestrator.play_media("Гладиатор", {"media_type": "movie"})

    assert "music:pause" not in log


def test_preempt_failure_does_not_block_new_content(orchestrator):
    log = []
    music = _Recorder(log, "music")
    music.pause = MagicMock(side_effect=RuntimeError("Spotify упал"))
    orchestrator.tracker.set_active_session(_session(music))

    with patch.object(orchestrator.router, "resolve_and_open",
                      return_value=ProviderResult(success=True, url="u", message="Включаю фильм, сэр.",
                                                  controller=_Recorder(log, "movie"), provider="vk")):
        assert orchestrator.play_media("Гладиатор", {"media_type": "movie"}) == "Включаю фильм, сэр."


# ── Spotify: пауза и продолжение детерминированно, а не toggle-клавишей ──────

def test_spotify_pause_uses_web_api_before_media_key():
    ctrl = SpotifyMediaController()
    api = MagicMock()
    api.controller.is_ready.return_value = True
    api.controller.pause.return_value = "Пауза, сэр."
    with patch("core.media.controllers.spotify._spotify_api", return_value=api), \
         patch("core.media.controllers.spotify._send_media_key") as key:
        ctrl.pause()
    api.controller.pause.assert_called_once()
    key.assert_not_called()
    assert ctrl.get_state() == MediaState.PAUSED


def test_spotify_pause_falls_back_to_media_key_without_api():
    ctrl = SpotifyMediaController()
    with patch("core.media.controllers.spotify._spotify_api", return_value=None), \
         patch("core.media.controllers.spotify._send_media_key") as key:
        ctrl.pause()
    key.assert_called_once_with("playpause")


def test_spotify_play_uses_web_api_resume():
    ctrl = SpotifyMediaController()
    ctrl._current_state = MediaState.PAUSED
    api = MagicMock()
    api.controller.is_ready.return_value = True
    api.controller.resume.return_value = "Продолжаю, сэр."
    with patch("core.media.controllers.spotify._spotify_api", return_value=api), \
         patch("core.media.controllers.spotify._send_media_key") as key:
        ctrl.play()
    api.controller.resume.assert_called_once()
    key.assert_not_called()
    assert ctrl.get_state() == MediaState.PLAYING


# ── Смена сессии не убивает Spotify ──────────────────────────────────────────

def test_superseded_spotify_is_paused_not_killed(orchestrator):
    ctrl = SpotifyMediaController()
    orchestrator.tracker.set_active_session(_session(ctrl))
    with patch("core.media.controllers.spotify._spotify_api", return_value=None), \
         patch("core.media.controllers.spotify._send_media_key") as key, \
         patch("core.media.controllers.spotify.subprocess.run") as run:
        orchestrator.tracker.set_active_session(_session(MagicMock(), MediaType.MOVIE))
    run.assert_not_called()
    assert key.call_count <= 1  # максимум одна пауза, никакого taskkill


def test_superseded_browser_tab_is_closed(orchestrator):
    log = []
    video = _Recorder(log, "video")
    orchestrator.tracker.set_active_session(_session(video, MediaType.MOVIE))
    orchestrator.tracker.set_active_session(_session(_Recorder(log, "other"), MediaType.MUSIC))
    assert "video:close" in log


# ── Музыка через Web API тоже участвует в переключении ───────────────────────

def test_play_music_intent_registers_session_and_preempts_video(orchestrator):
    from core.command_orchestrator import CommandOrchestrator
    log = []
    video = _Recorder(log, "video")
    orchestrator.tracker.set_active_session(_session(video, MediaType.MOVIE))

    def fake_spotify(params, player=None):
        log.append("spotify:play")
        return "Включаю «numb», сэр."

    with patch("actions.spotify_controller.spotify_player", side_effect=fake_spotify):
        res = CommandOrchestrator().process_user_text("джарвис, включи музыку linkin park numb", None)

    assert res.decision.name == "CONSUMED_ACTION"
    assert log.index("video:pause") < log.index("spotify:play"), log
    active = orchestrator.tracker.get_active_session()
    assert active is not None and active.media_type == MediaType.MUSIC
    assert isinstance(active.controller, SpotifyMediaController)


def test_failed_music_does_not_register_session(orchestrator):
    from core.command_orchestrator import CommandOrchestrator
    with patch("actions.spotify_controller.spotify_player", return_value="Spotify недоступен, сэр."):
        CommandOrchestrator().process_user_text("джарвис, включи музыку numb", None)
    assert orchestrator.tracker.get_active_session() is None


# ── Закрытие — быстрым путём, без модели ─────────────────────────────────────

@pytest.mark.parametrize("text", ["закрой фильм", "Джарвис, закрой видео", "выключи плеер", "закрой кино", "выключи сериал"])
def test_close_video_is_a_fast_command(text):
    from core.fast_command_router import FastCommandRouter
    with patch("core.media.orchestrator.MediaOrchestrator.close", return_value="Вкладка плеера закрыта, сэр.") as m:
        res = FastCommandRouter.match_and_execute(text)
    assert res[0] is True and m.called
    assert res.is_action is True


@pytest.mark.parametrize("text", ["выключи музыку", "останови музыку", "выруби музыку"])
def test_stop_music_is_a_fast_command(text):
    from core.fast_command_router import FastCommandRouter
    with patch("core.media.orchestrator.MediaOrchestrator.stop", return_value="Музыка остановлена, сэр.") as m:
        res = FastCommandRouter.match_and_execute(text)
    assert res[0] is True and m.called
