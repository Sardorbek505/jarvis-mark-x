"""
Spotify Authentication Module

Handles OAuth 2.0 Authorization Code flow with refresh token support.
Never requires login every launch - uses stored refresh token.
"""

import requests
import time
from pathlib import Path
from typing import Optional
import json
import logging
import os

from core.storage import atomic_write_json

_logger = logging.getLogger(__name__)

# Обновляем токен заранее, чтобы он не истёк посреди запроса
_REFRESH_MARGIN_SEC = 30


class SpotifyAuth:
    """
    Spotify OAuth 2.0 authentication with automatic token refresh.
    """
    
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: Optional[float] = None
        
        # Load stored tokens if available
        self._load_tokens()
    
    def _load_tokens(self) -> None:
        """Load tokens from storage."""
        # Get absolute path to token file
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        token_file = os.path.join(script_dir, "config", "spotify_tokens.json")
        
        if os.path.exists(token_file):
            try:
                with open(token_file, 'r') as f:
                    data = json.load(f)
                    self.access_token = data.get('access_token')
                    self.refresh_token = data.get('refresh_token')
                    self.token_expiry = data.get('expiry')
            except Exception as e:
                print(f"[SpotifyAuth] Failed to load tokens: {e}")
    
    def _save_tokens(self) -> None:
        """Save tokens to storage."""
        # Get absolute path to token file
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        token_file = os.path.join(script_dir, "config", "spotify_tokens.json")
        
        try:
            # Атомарно: обрыв на середине записи оставлял бы обрезанный JSON,
            # а это потеря refresh-токена и повторный вход в Spotify руками.
            atomic_write_json(Path(token_file), {
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'expiry': self.token_expiry
            })
        except Exception as exc:
            _logger.error("Не сохранил токены Spotify в %s: %s", token_file, exc)
    
    def authenticate(self, auth_code: str) -> bool:
        """
        Exchange authorization code for access and refresh tokens.
        
        Args:
            auth_code: Authorization code from OAuth callback
            
        Returns:
            True if successful, False otherwise
        """
        data = {
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': self.redirect_uri,
            'client_id': self.client_id,
            'client_secret': self.client_secret
        }
        
        try:
            response = requests.post(self.TOKEN_URL, data=data)
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            self.refresh_token = token_data['refresh_token']
            expires_in = token_data.get('expires_in', 3600)
            self.token_expiry = time.time() + expires_in
            
            self._save_tokens()
            return True
            
        except Exception as e:
            print(f"[SpotifyAuth] Authentication failed: {e}")
            return False
    
    def set_refresh_token(self, refresh_token: str) -> None:
        """
        Set refresh token directly (for initial setup).
        """
        self.refresh_token = refresh_token
        self._save_tokens()
    
    def refresh_access_token(self) -> bool:
        """
        Refresh access token using refresh token.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.refresh_token:
            print("[SpotifyAuth] No refresh token available")
            return False
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
            'client_id': self.client_id,
            'client_secret': self.client_secret
        }
        
        try:
            response = requests.post(self.TOKEN_URL, data=data)
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            
            # Update refresh token if new one provided (Spotify sometimes rotates)
            if 'refresh_token' in token_data:
                self.refresh_token = token_data['refresh_token']
            
            expires_in = token_data.get('expires_in', 3600)
            self.token_expiry = time.time() + expires_in
            
            self._save_tokens()
            print("[SpotifyAuth] Token refreshed successfully")
            return True
            
        except Exception as e:
            print(f"[SpotifyAuth] Token refresh failed: {e}")
            return False
    
    def get_access_token(self) -> Optional[str]:
        """
        Get valid access token, refreshing if necessary.
        
        Returns:
            Access token or None if unavailable
        """
        if not self.refresh_token:
            return self.access_token

        # Неизвестный срок жизни считаем истёкшим. Раньше условие требовало
        # непустой token_expiry — а файл токенов приходит с expiry=null, и
        # обновление не срабатывало НИКОГДА: наружу вечно уходил мёртвый токен.
        expired = self.token_expiry is None or time.time() >= self.token_expiry - _REFRESH_MARGIN_SEC

        if not self.access_token or expired:
            if not self.refresh_access_token():
                return None

        return self.access_token
    
    def is_authenticated(self) -> bool:
        """Check if authentication is ready."""
        return self.get_access_token() is not None
    
    def get_auth_url(self) -> str:
        """
        Generate authorization URL for user to visit.
        
        Returns:
            Authorization URL
        """
        scopes = [
            'user-read-playback-state',
            'user-modify-playback-state',
            'user-read-currently-playing',
            'user-read-email',
            'user-read-private',
            'user-library-read',
            'user-library-modify',
            'user-top-read',
            'user-read-recently-played',
            'playlist-read-private',
            'playlist-read-collaborative',
            'playlist-modify-public',
            'playlist-modify-private',
            'ugc-image-upload',
        ]
        
        scope_str = ' '.join(scopes)
        
        return (
            f"https://accounts.spotify.com/authorize"
            f"?client_id={self.client_id}"
            f"&response_type=code"
            f"&redirect_uri={self.redirect_uri}"
            f"&scope={scope_str}"
        )
