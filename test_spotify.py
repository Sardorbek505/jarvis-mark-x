#!/usr/bin/env python3
"""
Тесты для Spotify интеграции
"""

import sys
import os

# Добавляем текущую директорию в path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.spotify import SpotifyController, SpotifyAuth


def test_auth_init():
    """Тест инициализации auth."""
    print("=== Test: Auth Init ===")
    
    auth = SpotifyAuth(
        client_id="test_id",
        client_secret="test_secret",
        redirect_uri="http://127.0.0.1:8888/callback"
    )
    
    assert auth.client_id == "test_id"
    assert auth.client_secret == "test_secret"
    assert auth.redirect_uri == "http://127.0.0.1:8888/callback"
    
    print("[OK] Auth init successful")


def test_auth_url():
    """Тест генерации auth URL."""
    print("\n=== Test: Auth URL ===")
    
    auth = SpotifyAuth(
        client_id="test_id",
        client_secret="test_secret",
        redirect_uri="http://127.0.0.1:8888/callback"
    )
    
    url = auth.get_auth_url()
    
    assert "client_id=test_id" in url
    assert "redirect_uri=http://127.0.0.1:8888/callback" in url
    assert "response_type=code" in url
    
    print(f"[OK] Auth URL generated: {url[:50]}...")


def test_controller_init():
    """Тест инициализации controller."""
    print("\n=== Test: Controller Init ===")
    
    controller = SpotifyController(
        client_id="test_id",
        client_secret="test_secret",
        redirect_uri="http://127.0.0.1:8888/callback"
    )
    
    assert controller.auth.client_id == "test_id"
    assert controller.track_cache == {}
    assert controller.max_cache_size == 50
    
    print("[OK] Controller init successful")


def test_track_cache():
    """Тест кэша треков."""
    print("\n=== Test: Track Cache ===")
    
    controller = SpotifyController(
        client_id="test_id",
        client_secret="test_secret",
        redirect_uri="http://127.0.0.1:8888/callback"
    )
    
    # Добавляем треки в кэш
    controller._cache_track("Imagine Dragons", "spotify:track:123")
    controller._cache_track("Queen", "spotify:track:456")
    
    assert "Imagine Dragons" in controller.track_cache
    assert "Queen" in controller.track_cache
    assert controller.track_cache["Imagine Dragons"] == "spotify:track:123"
    
    print("[OK] Track cache working")


def test_cache_fifo():
    """Тест FIFO eviction в кэше."""
    print("\n=== Test: Cache FIFO ===")
    
    controller = SpotifyController(
        client_id="test_id",
        client_secret="test_secret",
        redirect_uri="http://127.0.0.1:8888/callback"
    )
    controller.max_cache_size = 3  # Small cache for testing
    
    # Добавляем 4 трека (превышает лимит)
    controller._cache_track("Track1", "uri1")
    controller._cache_track("Track2", "uri2")
    controller._cache_track("Track3", "uri3")
    controller._cache_track("Track4", "uri4")
    
    # Первый должен быть удален
    assert "Track1" not in controller.track_cache
    assert "Track4" in controller.track_cache
    
    print("[OK] Cache FIFO eviction working")


def test_mood_seeds():
    """Тест mood seed конфигураций."""
    print("\n=== Test: Mood Seeds ===")
    
    # Импортируем только если есть access token
    try:
        from tools.spotify.moods import SpotifyMoods
        
        # Создаём мок с фейковым токеном
        moods = SpotifyMoods("fake_token")
        
        # Проверяем что конфигурации существуют
        seeds = moods.get_mood_seeds("спокойное")
        assert "seed_genres" in seeds
        assert "min_energy" in seeds
        
        seeds = moods.get_mood_seeds("мотивационное")
        assert seeds["min_energy"] >= 0.7  # High energy
        
        print("[OK] Mood seeds configured correctly")
        
    except ImportError:
        print("[SKIP] Skipped (missing dependencies)")


def run_all_tests():
    """Запустить все тесты."""
    print("Running Spotify Integration Tests\n")
    print("=" * 50)
    
    try:
        test_auth_init()
        test_auth_url()
        test_controller_init()
        test_track_cache()
        test_cache_fifo()
        test_mood_seeds()
        
        print("\n" + "=" * 50)
        print("[SUCCESS] All tests passed!")
        
    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
