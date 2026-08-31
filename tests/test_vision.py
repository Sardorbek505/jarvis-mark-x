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
