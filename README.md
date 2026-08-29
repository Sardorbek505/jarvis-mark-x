---
title: JARVIS Mark X
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 8000
pinned: false
---

# 🤖 JARVIS Mark X — Русскоязычный персональный ИИ-ассистент

<div align="center">

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![GUI PyQt6](https://img.shields.io/badge/GUI-PyQt6-green.svg)](https://riverbankcomputing.com/software/pyqt/)
[![AI Engine](https://img.shields.io/badge/Engine-Gemini%202.5%20Flash%20Live-orange.svg)](https://aistudio.google.com/)
[![Voice Edge-TTS](https://img.shields.io/badge/Voice-Edge--TTS%20Neural-cyan.svg)](https://github.com/rany2/edge-tts)
[![Tests Passing](https://img.shields.io/badge/Tests-274%20passed-brightgreen.svg)](https://pytest.org/)
[![License MIT](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

**Высокотехнологичный голосовой ИИ-ассистент нового поколения с нативным двусторонним аудио-потоком, компьютерным зрением, памятью Obsidian и управлением ПК.**

</div>

---

## ✨ Ключевые возможности

| Категория | Возможности |
|---|---|
| 🎙️ **Голос и Диалог** | Нативный двусторонний аудио-стриминг через **Google Gemini Live API** с распознаванием перебиваний (Barge-In) и бесплатным резервным синтезом **Microsoft Edge-TTS** / **Fish Audio**. |
| 👁️ **Компьютерное зрение (Vision)** | Мгновенный захват экрана, анализ активных окон и веб-камеры: поиск багов в коде, ревью дизайна, распознавание текста на мониторе. |
| 🔔 **Wake-Word и Chime** | Голосовая активация по ключевым словам («Джарвис», «Слушай») с процедурным звуковым откликом в стиле Железного Человека. |
| 🧠 **Второй Мозг (Second Brain)** | Интеграция с базой знаний **Obsidian**, долгосрочная ассоциативная память SQLite и профилирование предпочтений пользователя. |
| 📱 **Telegram & Mini App** | Удаленный доступ и управление компьютером через Telegram-бота (`@atabekovch_jarvis_bot`) и Web Mini App с WebSocket. |
| 🖥️ **Управление Windows** | Запуск любых программ, регулировка звука и яркости, управление окнами, поиск файлов и медиаплеер (Spotify, Кино). |
| 🎛️ **Системный трей и автозапуск** | Неоновый анимированный HUD с возможностью сворачивания в область уведомлений Windows (System Tray). |

---

## 🚀 Установка и Быстрый старт

### Вариант 1. Установка в 1 клик для пользователей (Windows Installer)
1. Скачайте **`JARVIS_Setup_v1.0.exe`** из раздела [Releases](https://github.com/Sardorbek505/jarvis-mark-x/releases).
2. Запустите установщик — он создаст ярлыки на Рабочем столе и в меню «Пуск».
3. При первом запуске откроется **Мастер настройки**:
   - Вставьте бесплатный ключ **Google Gemini API**.
   - Проверьте микрофон встроенным индикатором громкости.
   - Выберите голос и нажмите **«Сохранить и запустить»**.

---

### Вариант 2. Запуск из исходного кода (Для разработчиков)

```bash
# 1. Клонирование репозитория
git clone https://github.com/Sardorbek505/jarvis-mark-x.git
cd jarvis-mark-x

# 2. Установка зависимостей
pip install -r requirements.txt

# 3. Запуск ассистента
python main.py
```

---

## 🔑 Как получить бесплатный API-ключ Gemini (BYOK)

Джарвис работает по модели **BYOK (Bring Your Own Key)** — каждый пользователь использует собственный бесплатный ключ:

1. Перейдите в [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Войдите через ваш Google-аккаунт.
3. Нажмите кнопку **«Create API Key»** и скопируйте созданный ключ вида `AIzaSy...`.
4. Вставьте ключ в окно настроек Джарвиса.

> [!TIP]
> Ключ сохраняется в защищенную пользовательскую директорию `%APPDATA%\JARVIS\api_keys.json` и никогда не передается третьим лицам.

---

## 🗣️ Примеры голосовых команд

- **Зрение**:
  - *«Джарвис, что сейчас открыто на экране?»*
  - *«Посмотри на код на мониторе и найди ошибку»*
  - *«Оцени визуальный дизайн страницы»*
- **Отправка в Telegram**:
  - *«Джарвис, отправь этот скриншот мне в телеграм»*
  - *«Скинь эту заметку в телеграм»*
- **База знаний Obsidian**:
  - *«Запиши в дневник: сегодня завершили релиз версии 1.0»*
  - *«Найди в базе знаний заметку о проекте Zapis»*
- **Управление ПК**:
  - *«Открой Telegram / VS Code / Калькулятор»*
  - *«Сделай звук потише на 20%»*
  - *«Сверни все окна»*
  - *«Какая сейчас погода в Алматы?»*

---

## 🏗️ Архитектура системы

```mermaid
flowchart TD
    User([Пользователь]) <-->|Голос / Микрофон| DesktopClient[JARVIS Desktop Client / HUD]
    DesktopClient <-->|Live Audio WebSocket| GeminiAPI[Google Gemini 2.5 Flash Live API]
    DesktopClient <-->|Аудио TTS| EdgeTTS[Microsoft Edge-TTS Engine]
    DesktopClient <-->|Screen Grab / DWM| VisionModule[Vision Module]
    DesktopClient <-->|Markdown API| ObsidianVault[(Obsidian Knowledge Base)]
    DesktopClient <-->|Port 47821 Local Bridge| PCServer[PC Server Daemon]
    PCServer <-->|Cloud WebSocket| HFServer[Hugging Face Cloud Server]
    HFServer <-->|Telegram Bot API| TelegramBot[Telegram Bot & Mini App]
    TelegramBot <-->|Мобильный телефон| UserPhone([Пользователь на телефоне])
```

---

## 🛠️ Сборка дистрибутива

Для сборки автономного `.exe` и установщика:

```bash
# 1. Компиляция автономного бинарника PyInstaller
python scripts/build_exe.py

# 2. Упаковка в ZIP и компиляция Inno Setup Installer
python scripts/package_dist.py
```

Готовые файлы появятся в папке `dist/`:
- `dist/JARVIS_Setup_v1.0.exe` — инсталлятор Windows.
- `dist/JARVIS_Mark_X_Portable_v1.0.zip` — портативная версия.

---

## 🧪 Тестирование

Проект покрыт автоматическими тестами:

```bash
# Запуск тестов
python -m pytest

# Проверка линтером
python -m ruff check
```

---

## 📄 Лицензия

Распространяется под лицензией MIT. Подробности в файле [LICENSE](LICENSE).
