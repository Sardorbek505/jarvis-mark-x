# Деплой JARVIS на Hugging Face Spaces (24/7, бесплатно, БЕЗ карты)

Бот живёт на бесплатном Docker-Space, долгая память и напоминания — в бесплатном
Neon Postgres. Карта не нужна нигде. Засыпает только после 48 ч простоя.

```
Telegram ──► https://<user>-jarvis-mark-x.hf.space   (HF Space, Docker, render_app:app)
                          │
                          └──► Neon Postgres (DATABASE_URL)  ← durable память + напоминания
```

HF-конфиг уже в репозитории: front-matter в `README.md` (`sdk: docker`, `app_port: 8000`),
`Dockerfile` запускает `uvicorn telegram_bot.render_app:app` на `${PORT:-8000}`.

---

## Часть 1. Что делаешь ты в браузере (всё без карты)

1. **Hugging Face аккаунт** → https://huggingface.co/join
2. **Создать Space** → https://huggingface.co/new-space
   - Name: `jarvis-mark-x`
   - SDK: **Docker** → **Blank** (пустой)
   - Visibility: **Public** (приватный на free тоже можно, но public проще)
   - Публичный адрес будет: `https://<твой-username>-jarvis-mark-x.hf.space`
3. **Neon Postgres** → https://neon.tech → Sign up → **New Project**
   - После создания скопируй **Connection string** (вида
     `postgresql://user:pass@ep-xxx.neon.tech/dbname?sslmode=require`) — это `DATABASE_URL`.
4. **HF write-токен** → https://huggingface.co/settings/tokens → **New token**
   - Role: **Write**. Скопируй токен (вида `hf_...`).

## Часть 2. Что передаёшь мне

- HF **username** и имя Space (`jarvis-mark-x`)
- HF **write-токен** (`hf_...`)
- Neon **DATABASE_URL** (`postgresql://...`)
- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- твой Telegram **user_id** (узнать у @userinfobot)

## Часть 3. Что делаю я (терминал) — тебе команды вводить не нужно

1. Подключу HF Space как git-remote и **запушу код** в него (по твоему токену).
2. Пропишу в Space секреты через HF API:
   `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`,
   `MINIAPP_URL=https://<user>-jarvis-mark-x.hf.space`, `DATABASE_URL`,
   `DEFAULT_CITY`, `TIMEZONE`.
3. HF соберёт Docker-образ; при старте `render_app` сам зарегистрирует Telegram webhook.
4. Проверю: `https://<user>-jarvis-mark-x.hf.space/health` → `{"status":"ok"}`,
   затем `/start` боту в Telegram.

## Часть 4. Подключение домашнего ПК (управление компьютером, опционально)

На ПК в `config/api_keys.json`:
```json
"pc_link_url": "wss://<user>-jarvis-mark-x.hf.space",
"pc_link_token": "<любой_общий_секрет>"
```
Тот же `PC_LINK_TOKEN` я пропишу в секреты Space. Запусти PC-клиент — `/status` покажет ПК онлайн.

---

## Обновление после новых коммитов
Я просто пушу в HF-remote — Space пересоберётся сам. (Память в Neon переживает пересборку.)

## Если что-то не так
| Симптом | Причина / фикс |
|---|---|
| Space «Build failed» | смотрю логи сборки на странице Space → чиню |
| `/health` не отвечает | Space ещё собирается, либо спал (первый ответ ~1 мин) |
| Бот молчит | `MINIAPP_URL` ≠ реальный адрес Space, или неверный токен бота |
| Память теряется при рестарте | не задан `DATABASE_URL` (нужен Neon) |
| В логах `Telegram недоступен … ConnectError` | HF блокирует и `api.telegram.org`, и `*.workers.dev`. Нужен прокси на разрешённом хосте — см. `vercel-proxy/README.md`. Проверить, куда вообще есть доступ: выставить `DIAG_EGRESS=1` и посмотреть строки `[egress]` в логах |
| Neon: `exceeded the compute time quota` | пул держал соединение вечно и не давал базе уснуть. Должно быть `min_size=0` + `max_inactive_connection_lifetime` (уже так) |

## Что переживает недоступность чего

Приложение спроектировано так, чтобы падение одной внешней системы не гасило
остальные:

| Умерло | Что перестаёт работать | Что продолжает |
|---|---|---|
| Postgres (Neon) | долговременная память между рестартами | всё остальное; память на SQLite, авто-возврат на Postgres |
| Telegram API | бот в Telegram | ПК-линк, Mini App, память; переподключение 30→300 с |
| ПК выключен | управление компьютером | бот, память, напоминания |
