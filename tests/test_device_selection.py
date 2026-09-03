"""Tests for intelligent audio input device selection (headphones priority)."""

import pytest
import main as jarvis_main


def test_pick_input_device_prioritizes_headphones(monkeypatch):
    mock_devices = [
        {"name": "Realtek(R) Audio", "max_input_channels": 4, "hostapi": 0},
        {"name": "AI Noise-cancelling Input (ASUS)", "max_input_channels": 2, "hostapi": 0},
        {"name": "AirPods Pro Hands-Free AG Audio", "max_input_channels": 1, "hostapi": 0},
    ]

    monkeypatch.setattr(jarvis_main.sd, "query_devices", lambda: mock_devices)
    monkeypatch.setattr(jarvis_main, "_device_is_silent", lambda idx: False)
    monkeypatch.setenv("MIC_DEVICE", "")

    picked = jarvis_main._pick_input_device()
    # Should pick AirPods (index 2) over laptop AI Noise-cancelling (index 1)
    assert picked == 2


def test_pick_input_device_fallback_to_laptop_noise_cancelling(monkeypatch):
    mock_devices = [
        {"name": "Realtek(R) Audio", "max_input_channels": 4, "hostapi": 0},
        {"name": "AI Noise-cancelling Input (ASUS)", "max_input_channels": 2, "hostapi": 0},
    ]

    monkeypatch.setattr(jarvis_main.sd, "query_devices", lambda: mock_devices)
    monkeypatch.setattr(jarvis_main, "_device_is_silent", lambda idx: False)
    monkeypatch.setenv("MIC_DEVICE", "")

    picked = jarvis_main._pick_input_device()
    # Should pick AI Noise-cancelling (index 1) when no headphones are connected
    assert picked == 1


def test_pick_input_device_manual_override(monkeypatch):
    mock_devices = [
        {"name": "Realtek(R) Audio", "max_input_channels": 4, "hostapi": 0},
        {"name": "AI Noise-cancelling Input (ASUS)", "max_input_channels": 2, "hostapi": 0},
        {"name": "AirPods Pro Hands-Free AG Audio", "max_input_channels": 1, "hostapi": 0},
    ]

    monkeypatch.setattr(jarvis_main.sd, "query_devices", lambda: mock_devices)
    monkeypatch.setattr(jarvis_main, "_device_is_silent", lambda idx: False)
    monkeypatch.setenv("MIC_DEVICE", "realtek")

    picked = jarvis_main._pick_input_device()
    assert picked == 0
