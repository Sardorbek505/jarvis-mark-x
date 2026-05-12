"""
Spotify Search Module

Handles track/artist/album search with fuzzy matching and ranking.
"""

import requests
from typing import Optional, List, Dict, Any
from rapidfuzz import fuzz


class SpotifySearch:
    """
    Spotify search with intelligent ranking and fuzzy matching.
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
    
    def _fuzzy_match_score(self, query: str, track: Dict[str, Any]) -> float:
        """
        Calculate fuzzy match score for a track.
        
        Args:
            query: Search query
            track: Track data from API
            
        Returns:
            Match score (0-100)
        """
        query_lower = query.lower()
        
        # Title matching
        title = track.get('name', '').lower()
        title_score = fuzz.partial_ratio(query_lower, title)
        
        # Artist matching
        artists = track.get('artists', [])
        artist_scores = []
        for artist in artists:
            artist_name = artist.get('name', '').lower()
            artist_score = fuzz.partial_ratio(query_lower, artist_name)
            artist_scores.append(artist_score)
        
        artist_score = max(artist_scores) if artist_scores else 0
        
        # Combined score (60% title, 40% artist)
        combined_score = title_score * 0.6 + artist_score * 0.4
        
        # Boost for exact matches
        if query_lower == title:
            combined_score += 20
        if any(query_lower == artist.get('name', '').lower() for artist in artists):
            combined_score += 15
        
        # Prefer non-explicit if scores are equal
        if not track.get('explicit', True):
            combined_score += 5
        
        return min(combined_score, 100)
    
    def search_track(
        self,
        query: str,
        limit: int = 5,
        use_fuzzy: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Search for a track with intelligent ranking.
        
        Args:
            query: Search query
            limit: Number of results to return
            use_fuzzy: Whether to use fuzzy matching
            
        Returns:
            Best matching track or None
        """
        # Try multiple query variants for better matching
        query_variants = [
            query,
            query.replace('ё', 'е'),  # Cyrillic yo → e
            query.replace('й', 'i'),   # Cyrillic short i → i
            query.replace('ъ', ''),  # Remove hard sign
            query.replace('ь', ''),  # Remove soft sign
            query.replace('з', 'с'),   # Common typo z → s
            query.replace('с', 'з'),   # Common typo s → z
            query.replace('ш', 'щ'),   # Common typo sh → sch
            query.replace('щ', 'ш'),   # Common typo sch → sh
            query.replace('ч', 'ц'),   # Common typo ch → ts
            query.replace('ц', 'ч'),   # Common typo ts → ch
            query.replace('к', 'х'),   # Common typo k → kh
            query.replace('х', 'к'),   # Common typo kh → k
            query.replace('п', 'б'),   # Common typo p → b
            query.replace('б', 'п'),   # Common typo b → p
            query.replace('т', 'д'),   # Common typo t → d
            query.replace('д', 'т'),   # Common typo d → t
            query.replace('ф', 'в'),   # Common typo f → v
            query.replace('в', 'ф'),   # Common typo v → f
            query.split()[0] if ' ' in query else query,  # First word only
        ]
        
        all_tracks = []
        
        for variant in query_variants:
            params = {
                'type': 'track',
                'q': variant,
                'limit': limit
            }
            
            result = self._api_request('/search', params)
            if result and 'tracks' in result and 'items' in result['tracks']:
                for track in result['tracks']['items']:
                    # Avoid duplicates
                    if not any(t['id'] == track['id'] for t in all_tracks):
                        all_tracks.append(track)
        
        if not all_tracks:
            return None
        
        if use_fuzzy:
            # Rank by fuzzy matching
            ranked_tracks = []
            for track in all_tracks:
                score = self._fuzzy_match_score(query, track)
                ranked_tracks.append((score, track))
            
            # Sort by score descending
            ranked_tracks.sort(key=lambda x: x[0], reverse=True)
            
            # Return best match
            return ranked_tracks[0][1]
        else:
            # Return first result (API default ranking)
            return all_tracks[0]
    
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
