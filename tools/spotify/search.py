"""
Spotify Search Module

Handles track/artist/album search. Порядок выдачи берём у Spotify: своя
пересортировка проверялась на живом API и проигрывала родной 8:0.
"""

import requests
from typing import Optional, List, Dict, Any


class SpotifySearch:
    """
    Spotify search. Ранжирование — на стороне Spotify.
    """
    
    API_BASE = "https://api.spotify.com/v1"
    
    def __init__(self, access_token: str, auth=None):
        self.access_token = access_token
        self.auth = auth  # SpotifyAuth instance for token refresh
        self.headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
    
    def _api_request(self, endpoint: str, params: Dict[str, Any], max_retries: int = 1) -> Optional[Dict]:
        """Make authenticated API request with automatic token refresh on 401."""
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                response = requests.get(
                    f"{self.API_BASE}{endpoint}",
                    headers=self.headers,
                    params=params
                )
                
                # If 401 Unauthorized, refresh token and retry
                if response.status_code == 401 and self.auth:
                    print(f"[SpotifySearch] 401 Unauthorized - refreshing token (attempt {retry_count + 1}/{max_retries})...")
                    if self.auth.refresh_access_token():
                        # Update headers with new token
                        self.access_token = self.auth.get_access_token()
                        self.headers['Authorization'] = f'Bearer {self.access_token}'
                        retry_count += 1
                        continue  # Retry with new token
                    else:
                        print("[SpotifySearch] Token refresh failed")
                        return None
                
                response.raise_for_status()
                return response.json()
                
            except Exception as e:
                print(f"[SpotifySearch] API request failed: {e}")
                if retry_count < max_retries:
                    retry_count += 1
                    continue
                return None
        
        return None
    
    def search_track(self, query: str, limit: int = 5) -> Optional[Dict[str, Any]]:
        """
        Найти трек по запросу. Доверяем порядку выдачи Spotify.

        Здесь была самодельная пересортировка: 20 «вариантов опечаток» (з→с,
        к→х, п→б), которые калечили запрос («Кино» → «Хино») и жгли 20 запросов
        к API на песню, плюс нечёткий балл поверх выдачи. Проверка на живом API
        по 8 запросам: позиция 0 от Spotify была верной 8 раз из 8, а
        пересортировка расходилась с ней дважды и оба раза ошибалась (Lil Wayne
        вместо MiyaGi, концертная версия вместо студийной). Она не помогала —
        только портила, поэтому её больше нет.

        Args:
            query: Поисковый запрос
            limit: Сколько результатов запросить у API

        Returns:
            Лучший трек или None
        """
        result = self._api_request('/search', {'type': 'track', 'q': query, 'limit': limit})
        if not result:
            return None

        items = result.get('tracks', {}).get('items', [])
        return items[0] if items else None
    
    def search_artist(self, query: str, limit: int = 5) -> Optional[Dict[str, Any]]:
        """
        Search for an artist.
        
        Args:
            query: Artist name
            limit: Number of results
            
        Returns:
            Best matching artist or None
        """
        params = {
            'type': 'artist',
            'q': query,
            'limit': limit
        }
        
        result = self._api_request('/search', params)
        if result and 'artists' in result and 'items' in result['artists']:
            artists = result['artists']['items']
            return artists[0] if artists else None
        
        return None
    
    def search_album(self, query: str, limit: int = 5) -> Optional[Dict[str, Any]]:
        """
        Search for an album.
        
        Args:
            query: Album name
            limit: Number of results
            
        Returns:
            Best matching album or None
        """
        params = {
            'type': 'album',
            'q': query,
            'limit': limit
        }
        
        result = self._api_request('/search', params)
        if result and 'albums' in result and 'items' in result['albums']:
            albums = result['albums']['items']
            return albums[0] if albums else None
        
        return None
    
    def get_track_uri(self, query: str) -> Optional[str]:
        """
        Get track URI from query.
        
        Args:
            query: Search query
            
        Returns:
            Spotify URI (spotify:track:xxxxx) or None
        """
        track = self.search_track(query)
        if track:
            return track.get('uri')
        return None
    
    def get_artist_top_tracks(self, artist_id: str) -> List[Dict[str, Any]]:
        """
        Get top tracks for an artist.
        
        Args:
            artist_id: Spotify artist ID
            
        Returns:
            List of tracks
        """
        params = {'market': 'RU'}
        result = self._api_request(f'/artists/{artist_id}/top-tracks', params)
        
        if result and 'tracks' in result:
            return result['tracks']
        
        return []
    
    def search_playlists(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for user playlists.
        
        Args:
            query: Search query
            limit: Number of results
            
        Returns:
            List of playlists
        """
        params = {
            'type': 'playlist',
            'q': query,
            'limit': limit
        }
        
        result = self._api_request('/search', params)
        if result and 'playlists' in result and 'items' in result['playlists']:
            return result['playlists']['items']
        
        return []
    
    def get_user_playlists(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get current user's playlists.
        
        Args:
            limit: Number of playlists to return
            
        Returns:
            List of playlists
        """
        params = {'limit': limit}
        result = self._api_request('/me/playlists', params)
        
        if result and 'items' in result:
            return result['items']
        
        return []
