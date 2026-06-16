# Переезд JARVIS на Koyeb (бесплатно, always-on)

Зачем: Render-free делит 750ч/мес на все сервисы. У тебя их два → выгорает.
Koyeb free = 1 сервис, **always-on (не спит)** → напоминания/брифинги всегда
вовремя, keep-alive пинг не нужен. WebSocket (ПК + мини-апп) поддерживается.

ВАЖНО: база — внешняя Neon Postgres. Все данные (привычки, напоминания,
память) остаются. Переносим только «мотор».

## 1. Создать сервис на Koyeb
1. https://www.koyeb.com → Sign up (через GitHub).
2. Create Web Service → GitHub → репозиторий `Sardorbek505/jarvis-mark-x`,
   ветка `master`.
3. Builder: **Dockerfile** (определится сам).
4. Instance: **Free**.
5. Health check path: `/health`. Port: 8000 (или авто — приложение слушает $PORT).

## 2. Переменные окружения (Environment)
Скопируй значения из Render → Environment твоего jarvis-mark-x:
- `GEMINI_API_KEY`        — как на Render
- `GEMINI_MODEL`          = gemini-2.5-flash
- `TELEGRAM_BOT_TOKEN`    — как на Render
- `TELEGRAM_ALLOWED_USERS`— твой Telegram user_id
- `DATABASE_URL`          — та же строка Neon (та же база → данные на месте!)
- `PC_LINK_TOKEN`         — ТОТ ЖЕ, что на Render (чтобы ПК подключился без правок токена)
- `MINIAPP_URL`           — заполнить ПОСЛЕ первого деплоя (см. шаг 3)

## 3. Узнать URL и дописать MINIAPP_URL
1. Задеплой. Koyeb даст публичный адрес вида `https://jarvis-xxxx.koyeb.app`.
2. Впиши его в переменную `MINIAPP_URL` → пере-деплой.
3. В логах появится `Webhook registered: https://jarvis-xxxx.koyeb.app/telegram-webhook`
   и `Memory: Postgres connected (durable)`. Бот ожил.

## 4. Обновить мини-апп в боте (BotFather)
URL Mini App в BotFather (кнопка «Открыть JARVIS») указывает на старый Render.
Поменяй на новый Koyeb URL: @BotFather → твой бот → Bot Settings → Menu Button /
Web App URL → `https://jarvis-xxxx.koyeb.app`.
(MINIAPP_URL в env уже обновлён — этого хватает для вебхука; кнопку меню правим в BotFather.)

## 5. Переключить домашний ПК
На компьютере открой `config/api_keys.json` и поменяй адрес сервера:
```
"pc_link_url": "https://jarvis-xxxx.koyeb.app",
"pc_link_token": "<ТОТ ЖЕ PC_LINK_TOKEN>"
```
Перезапусти `scripts\start_pc.bat`. В логах Koyeb появится `PC linked`.

## 6. Render — оставить только smetafast-bot
jarvis на Render больше не нужен (Suspend/Delete его), чтобы 750ч доставались
одному smetafast-bot и он не выгорал.

Готово: jarvis на Koyeb (не спит, бесплатно), smetafast-bot один на Render free.
