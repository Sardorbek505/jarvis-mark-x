"""
Spotify Moods Module

Maps mood commands to Spotify seed recommendations.
"""

import requests
from typing import Optional, List, Dict, Any


class SpotifyMoods:
    """
    Mood-based playlist generation using Spotify recommendations API.
    """
    
    API_BASE = "https://api.spotify.com/v1"
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
    
    def _api_request(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict]:
        """Make authenticated API request."""
        try:
            response = requests.get(
                f"{self.API_BASE}{endpoint}",
                headers=self.headers,
                params=params
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"[SpotifyMoods] API request failed: {e}")
            return None
    
    def get_mood_seeds(self, mood: str) -> Dict[str, Any]:
        """
        Get seed parameters for a mood.
        
        Args:
            mood: Mood description (спокойное, мотивационное, ночной вайб, etc.)
            
        Returns:
            Seed parameters for recommendations API
        """
        mood_lower = mood.lower()
        
        mood_configs = {
            'спокойное': {
                'seed_genres': 'ambient,classical,minimal,piano',
                'min_energy': 0.0,
                'max_energy': 0.4,
                'min_tempo': 60,
                'max_tempo': 100,
                'target_valence': 0.6  # Positive/relaxed
            },
            'мотивационное': {
                'seed_genres': 'hip-hop,rock,electronic',
                'min_energy': 0.7,
                'max_energy': 1.0,
                'min_tempo': 120,
                'max_tempo': 140,
                'target_valence': 0.8  # Very positive
            },
            'ночной вайб': {
                'seed_genres': 'electronic,chill,lo-fi,ambient',
                'min_energy': 0.3,
                'max_energy': 0.6,
                'min_tempo': 80,
                'max_tempo': 110,
                'target_valence': 0.4  # Somewhat relaxed
            },
            'работа': {
                'seed_genres': 'electronic,ambient,minimal',
                'min_energy': 0.4,
                'max_energy': 0.7,
                'min_tempo': 100,
                'max_tempo': 130,
                'target_valence': 0.5  # Neutral
            },
            'тренировка': {
                'seed_genres': 'hip-hop,rock,edm',
                'min_energy': 0.8,
                'max_energy': 1.0,
                'min_tempo': 130,
                'max_tempo': 160,
                'target_valence': 0.7  # Positive
            },
            'меланхолия': {
                'seed_genres': 'indie,alternative,blues',
                'min_energy': 0.2,
                'max_energy': 0.5,
                'min_tempo': 70,
                'max_tempo': 100,
                'target_valence': 0.2  # Sad
            },
            'веселье': {
                'seed_genres': 'pop,dance,disco',
                'min_energy': 0.7,
                'max_energy': 1.0,
                'min_tempo': 120,
                'max_tempo': 140,
                'target_valence': 0.9  # Very positive
            },
            'расслабление': {
                'seed_genres': 'jazz,blues,acoustic',
                'min_energy': 0.3,
                'max_energy': 0.5,
                'min_tempo': 60,
                'max_tempo': 90,
                'target_valence': 0.6  # Relaxed
            }
        }
        
        # Find best match (partial matching)
        for key, config in mood_configs.items():
            if key in mood_lower or mood_lower in key:
                return config
        
        # Default fallback
        return mood_configs['спокойное']
    
    def get_mood_recommendations(
        self,
        mood: str,
        limit: int = 10
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get track recommendations based on mood.
        
        Args:
            mood: Mood description
            limit: Number of tracks to return
            
        Returns:
            List of recommended tracks
        """
        seeds = self.get_mood_seeds(mood)
        
        params = {
            'limit': limit,
            'seed_genres': seeds['seed_genres'],
            'min_energy': seeds['min_energy'],
            'max_energy': seeds['max_energy'],
            'min_tempo': seeds['min_tempo'],
            'max_tempo': seeds['max_tempo'],
            'target_valence': seeds['target_valence'],
            'market': 'RU'
        }
        
        result = self._api_request('/recommendations', params)
        
        if result and 'tracks' in result:
            return result['tracks']
        
        return None
    
    def get_mood_playlist_uri(self, mood: str) -> Optional[str]:
        """
        Get first track URI from mood recommendations.
        
        Args:
            mood: Mood description
            
        Returns:
            Spotify URI or None
        """
        tracks = self.get_mood_recommendations(mood, limit=1)
        if tracks and len(tracks) > 0:
            return tracks[0].get('uri')
        return None
    
    def create_mood_playlist(
        self,
        mood: str,
        user_id: str,
        limit: int = 20
    ) -> Optional[str]:
        """
        Create a playlist based on mood.
        
        Args:
            mood: Mood description
            user_id: Spotify user ID
            limit: Number of tracks
            
        Returns:
            Playlist URI or None
        """
        # Get recommendations
        tracks = self.get_mood_recommendations(mood, limit)
        if not tracks:
            return None
        
        # Create playlist
        playlist_data = {
            'name': f'JARVIS {mood.capitalize()}',
            'description': f'Автоматически созданный плейлист для настроения: {mood}',
            'public': False
        }
        
        try:
            response = requests.post(
                f"{self.API_BASE}/users/{user_id}/playlists",
                headers=self.headers,
                json=playlist_data
            )
            response.raise_for_status()
            playlist = response.json()
            
            # Add tracks to playlist
            track_uris = [track['uri'] for track in tracks]
            requests.post(
                f"{self.API_BASE}/playlists/{playlist['id']}/tracks",
                headers=self.headers,
                json={'uris': track_uris}
            )
            
            return playlist.get('uri')
            
        except Exception as e:
            print(f"[SpotifyMoods] Failed to create playlist: {e}")
            return None
