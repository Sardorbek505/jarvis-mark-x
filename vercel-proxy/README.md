# Прокси Telegram API на Vercel

Замена Cloudflare-воркеру (`../cloudflare/telegram-proxy-worker.js`), который
Hugging Face перестал пускать.

## Почему понадобился

05.08.2026 из контейнера Space:

| Хост | Результат |
|------|-----------|
| `api.telegram.org` | ❌ ConnectTimeout (блок) |
| `jarvis-tg-proxy.*.workers.dev` | ❌ ConnectTimeout (блок) |
| `vercel.com` | ✅ HTTP 200 |
| `deno.com` | ✅ HTTP 200 |
| `api.render.com` | ✅ HTTP 405 |
| `pypi.org` | ✅ HTTP 200 |

Egress закрыт не целиком — заблокированы именно Telegram и `workers.dev`.
Значит прокси на Vercel из Space достижим.

Проверить это в любой момент заново: выставить у Space переменную
`DIAG_EGRESS=1` и посмотреть строки `[egress]` в логах при следующем старте.

## Развёрнут

**https://jarvis-tg-proxy.vercel.app** (проект Vercel `jarvis-tg-proxy`,
аккаунт sardorbek505, задеплоен 05.08.2026). Прописан в Space секретом
`TELEGRAM_API_BASE`.

Передеплой после правок:

```bash
cd vercel-proxy
npx vercel deploy --prod
```

## Подключение к Space (если поднимаешь заново)

Space → Settings → Variables and secrets → секрет `TELEGRAM_API_BASE` = URL
прокси (**без** завершающего слэша). Space перезапустится сам.

Проверка в логах Space:

```
Telegram API routed via proxy: https://jarvis-tg-proxy.vercel.app
JARVIS started ✅ (webhook mode, попытка №N)
```

Если Telegram не поднимется, бот не упадёт: ПК-линк и Mini App продолжат
работать, а подключение будет повторяться с нарастающей паузой (30→300 с).

## Проверка прокси вручную

```bash
curl -s "https://<твой-прокси>/bot<TOKEN>/getMe"
```

Должен вернуться JSON с `"ok":true`. Любой путь, кроме `/bot…` и `/file/bot…`,
отдаёт 404 — прокси нельзя использовать как открытый релей.
