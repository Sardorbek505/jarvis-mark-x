"""
Spotify Devices Module

Handles device detection, selection, and transfer of playback.
"""

import requests
import time
import subprocess
import platform
from typing import Optional, List, Dict, Any


class SpotifyDevices:
    """
    Spotify device management with automatic desktop app launching.
    """
    
    API_BASE = "https://api.spotify.com/v1"
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        self.device_cache = None
        self.cache_time = 0
    
    def _api_request(self, endpoint: str, method: str = 'GET', data: Dict = None) -> Optional[Dict]:
        """Make authenticated API request."""
        try:
            if method == 'GET':
                response = requests.get(
                    f"{self.API_BASE}{endpoint}",
                    headers=self.headers
                )
            elif method == 'PUT':
                response = requests.put(
                    f"{self.API_BASE}{endpoint}",
                    headers=self.headers,
                    json=data
                )
            elif method == 'POST':
                response = requests.post(
                    f"{self.API_BASE}{endpoint}",
                    headers=self.headers,
                    json=data
                )
            
            response.raise_for_status()
            # 204 No Content is a success response with no body
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()
        except Exception as e:
            print(f"[SpotifyDevices] API request failed: {e}")
            return None
    
    def list_devices(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Get list of available devices.
        
        Args:
            force_refresh: Force refresh of device list
            
        Returns:
            List of devices
        """
        # Cache for 30 seconds
        current_time = time.time()
        if not force_refresh and self.device_cache and (current_time - self.cache_time) < 30:
            return self.device_cache
        
        result = self._api_request('/me/player/devices')
        if result and 'devices' in result:
            self.device_cache = result['devices']
            self.cache_time = current_time
            return self.device_cache
        
        return []
    
    def get_active_device(self) -> Optional[Dict[str, Any]]:
        """
        Get currently active device.
        
        Returns:
            Active device or None
        """
        devices = self.list_devices()
        for device in devices:
            if device.get('is_active', False):
                return device
        return None
    
    def get_desktop_device(self) -> Optional[Dict[str, Any]]:
        """
        Get desktop Spotify client if available.
        
        Returns:
            Desktop device or None
        """
        devices = self.list_devices()
        for device in devices:
            name = device.get('name', '').lower()
            device_type = device.get('type', '').lower()
            if any(t in device_type for t in ('computer', 'desktop', 'pc')) or 'spotify' in name:
                return device
        if devices:
            return devices[0]
        return None
    
    def launch_spotify_desktop(self) -> bool:
        """
        Launch Spotify desktop application.

        Returns:
            True if successful, False otherwise
        """
        try:
            system = platform.system()

            if system == 'Windows':
                import os
                # 1. Standard installer location: %APPDATA%\Spotify\Spotify.exe
                appdata = os.environ.get('APPDATA', '')
                exe = os.path.join(appdata, 'Spotify', 'Spotify.exe')
                if appdata and os.path.exists(exe):
                    subprocess.Popen([exe], cwd=os.path.dirname(exe))
                    return True
                # 2. Local AppData location
                local_appdata = os.environ.get('LOCALAPPDATA', '')
                local_exe = os.path.join(local_appdata, 'Spotify', 'Spotify.exe')
                if local_appdata and os.path.exists(local_exe):
                    subprocess.Popen([local_exe], cwd=os.path.dirname(local_exe))
                    return True
                # 3. Fall back to the spotify: URI protocol handler.
                os.startfile('spotify:')  # type: ignore[attr-defined]
                return True
            elif system == 'Darwin':  # macOS
                subprocess.Popen(['open', '-a', 'Spotify'])
                return True
            elif system == 'Linux':
                subprocess.Popen(['spotify'])
                return True
            else:
                return False
        except Exception as e:
            print(f"[SpotifyDevices] Failed to launch Spotify: {e}")
            return False
    
    def ensure_active_device(self, max_wait: int = 5) -> Optional[Dict[str, Any]]:
        """
        Ensure there's an active device. If none is active, find available desktop device
        and transfer playback to it. If none is found, launch Spotify and wait up to max_wait sec.
        """
        # Check for existing active device
        active = self.get_active_device()
        if active:
            return active

        # Check for desktop device (not active but available in cluster)
        desktop = self.get_desktop_device()
        if desktop:
            transferred = self.transfer_playback(desktop['id'])
            if transferred:
                return transferred
            return desktop

        # No device available — launch desktop app then check with polling
        print("[SpotifyDevices] No device found, launching desktop app...")
        self.launch_spotify_desktop()

        for _ in range(max(1, int(max_wait))):
            time.sleep(1.0)
            devices = self.list_devices(force_refresh=True)
            if devices:
                desktop = self.get_desktop_device()
                if desktop:
                    transferred = self.transfer_playback(desktop['id'])
                    return transferred or desktop

        return None
    
    def transfer_playback(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Transfer playback to specific device.
        
        Args:
            device_id: Spotify device ID
            
        Returns:
            Device info or None
        """
        data = {
            'device_ids': [device_id],
            'play': False
        }
        
        result = self._api_request('/me/player', method='PUT', data=data)
        
        if result is not None:  # 204 No Content is success
            devices = self.list_devices(force_refresh=True)
            for device in devices:
                if device['id'] == device_id:
                    return device
        
        return None
    
    def get_device_info(self) -> Dict[str, Any]:
        """
        Get current device info.
        
        Returns:
            Device info dict
        """
        active = self.get_active_device()
        if active:
            return {
                'id': active.get('id'),
                'name': active.get('name'),
                'type': active.get('type'),
                'is_active': active.get('is_active', False),
                'volume': active.get('volume_percent', 0)
            }
        
        return {}
