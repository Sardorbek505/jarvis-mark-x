"""Тесты для модуля Vision Mode (анализ экрана и камеры)."""
from unittest.mock import MagicMock, patch

from actions import vision


def test_capture_screen_returns_jpeg_or_none():
    jpeg = vision.capture_screen_jpeg()
    if jpeg is not None:
        assert isinstance(jpeg, bytes)
        assert jpeg.startswith(b"\xff\xd8")  # JPEG magic bytes


def test_analyze_vision_mock():
    # Ключ подменяем явно. Без этого тест проходил только на машине владельца,
    # где config/api_keys.json заполнен: там, где ключа нет (CI, чужой клон),
    # analyze_vision выходит раньше вызова модели и возвращает «не найден
    # API-ключ» — мок при этом даже не трогается.
    with patch.object(vision, "_get_api_key", return_value="test-key"), \
         patch("google.genai.Client") as mock_client:
        instance = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "На экране открыт редактор кода VS Code с Python скриптом."
        instance.models.generate_content.return_value = mock_resp
        mock_client.return_value = instance

        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 200
        result = vision.analyze_vision(prompt="Что на экране?", image_bytes=fake_jpeg)
        assert "VS Code" in result


def test_vision_action_routing():
    with patch("actions.vision.analyze_vision", return_value="Экран чист"):
        res = vision.vision_action({"prompt": "посмотри на экран"})
        assert res == "Экран чист"


def test_fast_router_screen_vision(monkeypatch):
    """Проверка перехвата фраз голосового зрения Fast-Path роутером."""
    from core.fast_command_router import FastCommandRouter

    earcons = []
    monkeypatch.setattr("core.earcons.play_success_earcon", lambda: earcons.append("success"))
    monkeypatch.setattr("actions.vision.analyze_vision", lambda prompt, source: f"Вижу окно браузера, запрос: {prompt}")

    # 1. "Посмотри на экран"
    res = FastCommandRouter.match_and_execute("Джарвис, посмотри на экран")
    assert res.handled is True
    assert res.is_action is False
    assert "Вижу окно браузера" in res.text
    assert "success" in earcons

    # 2. "Что у меня на экране?"
    res2 = FastCommandRouter.match_and_execute("что у меня на экране?")
    assert res2.handled is True
    assert res2.is_action is False

    # 3. "Найди ошибку на экране"
    res3 = FastCommandRouter.match_and_execute("Джарвис, найди ошибку на экране")
    assert res3.handled is True
    assert res3.is_action is False

    # 4. "Посмотри в камеру"
    cam_sources = []
    monkeypatch.setattr("actions.vision.analyze_vision", lambda prompt, source: cam_sources.append(source) or "Вижу пользователя")
    res_cam = FastCommandRouter.match_and_execute("Джарвис, посмотри в камеру")
    assert res_cam.handled is True
    assert res_cam.is_action is False
    assert cam_sources == ["camera"]
