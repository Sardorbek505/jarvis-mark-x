"""
Spotify Integration Module

Full official Spotify Web API + Spotify Connect control.
Replaces unreliable keyboard/UI automation with direct API control.
"""

from .controller import SpotifyController
from .auth import SpotifyAuth
from .search import SpotifySearch
from .devices import SpotifyDevices
from .moods import SpotifyMoods

__all__ = [
    "SpotifyController",
    "SpotifyAuth",
    "SpotifySearch",
    "SpotifyDevices",
    "SpotifyMoods",
]
