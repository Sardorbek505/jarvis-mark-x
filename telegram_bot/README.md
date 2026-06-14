# JARVIS Telegram Bot + Mini App

Мобильный интерфейс JARVIS: чат, голосовые, фото — и полноценный
голосовой ассистент реального времени через Telegram Mini App.

---

## Что умеет

| Функция | Где работает | Нужен ПК? |
|---------|-------------|-----------|
| Текстовый чат с Gemini | Бот | Нет |
| Голосовые сообщения | Бот | Нет |
| Анализ фото | Бот | Нет |
| Голос в реальном времени | Mini App | Нет |
| Память ("второй мозг") | Mini App | Нет |
| Управление ПК (музыка, погода) | Бот + ПК | Да |
| Уведомления от JARVIS | Бот + ПК | Да |

---

## Архитектура

```
Телефон (Telegram)
   │
   ├─ Бот ──────────────► Gemini API        (чат, голос, фото)
   │                            │
   │                            └─► WebSocket ─► Desktop JARVIS (если ПК онлайн)
   │
   └─ Mini App ─────────► FastAPI сервер ─► Gemini Live  (голос реального времени)
                                │
                                └─► SQLite (память: разговоры, факты, профиль)
```

---

## Установка

```bash
pip install -r requirements.txt
```

Заполни `config/api_keys.json` (скопируй из `config/api_keys.example.json`):

```json
{
  "gemini_api_key": "...",
  "telegram_bot_token": "токен от @BotFather",
  "telegram_allowed_users": "твой_telegram_id",
  "pc_ws_host": "",
  "pc_ws_port": 8765,
  "miniapp_url": "",
  "miniapp_port": 8000
}
```

- **telegram_bot_token** — создай бота через `@BotFather` → `/newbot`
- **telegram_allowed_users** — узнай свой ID через `@userinfobot` (можно несколько через запятую)
- **miniapp_url** — заполни только когда настроишь Mini App (см. ниже)

---

## Запуск

### Шаг 1 — Только бот (минимум для старта)

```bash
python -m telegram_bot.bot
```

Иди в Telegram → твой бот → `/start`. Работает текст, голос, фото.

### Шаг 2 — Mini App (голос в реальном времени)

Telegram требует **HTTPS**. Для теста проще всего ngrok.

```bash
# Терминал 1 — сервер Mini App
python -m telegram_bot.server.app

# Терминал 2 — HTTPS туннель
ngrok http 8000
```

ngrok выдаст ссылку вида `https://abc123.ngrok.io`. 

1. Вставь её в `miniapp_url` в `config/api_keys.json`
2. Зарегистрируй у `@BotFather` → `/newapp` → выбери бота → вставь ссылку
3. Перезапусти бота, напиши `/app` → откроется интерфейс

### Шаг 3 — Управление ПК (опционально)

На компьютере, где запущен основной JARVIS:

```bash
python -m telegram_bot.pc_server
```

В `config/api_keys.json` на стороне **бота** укажи `pc_ws_host` —
публичный IP или DDNS-адрес твоего ПК. Порт 8765 должен быть открыт.

---

## Команды бота

| Команда | Действие |
|---------|----------|
| `/start` | Запуск и статус |
| `/app` | Открыть Mini App с голосом |
| `/status` | Статус подключения к ПК |
| `/pc <команда>` | Отправить команду напрямую на ПК |
| `/clear` | Очистить историю диалога |
| `/help` | Список команд |

---

## Файлы

```
telegram_bot/
├── bot.py             # Основной бот (текст/голос/фото/команды)
├── config.py          # Загрузка конфига из api_keys.json / env
├── gemini_client.py   # Обёртка Gemini (чат, аудио, картинки)
├── pc_bridge.py       # WebSocket-клиент: бот ↔ ПК
├── pc_server.py       # WebSocket-сервер на ПК (роутит в actions/)
├── miniapp/           # Веб-интерфейс Mini App
│   ├── index.html
│   ├── style.css      # Анимированный орб
│   ├── app.js         # WebSocket + запись/воспроизведение аудио
│   └── worklet.js     # Downsampling микрофона до 16kHz PCM
└── server/
    ├── app.py            # FastAPI: раздаёт Mini App + WebSocket
    ├── voice_session.py  # Проксирует браузер ↔ Gemini Live
    └── memory.py         # SQLite: память "второго мозга"
```

---

## 24/7 на сервере

Чтобы бот работал всегда — разверни на VPS (Railway / Oracle Cloud / Hetzner).
Переменные окружения вместо `api_keys.json`:
`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, `MINIAPP_URL`.
