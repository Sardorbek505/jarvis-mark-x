# JARVIS Outbound — отправка сообщений контактам (дизайн)

Дата: 2026-06-18 · Статус: утверждён, в реализации

## Цель
JARVIS отправляет сообщения (текст и голос) контактам пользователя «от его имени»,
экономя время на формулировках и ручной отправке. Команда даётся с любого устройства
через бота; доставку исполняет userbot на домашнем ПК.

## Решения (из брейншторма)
- **Исполнитель:** Telethon-userbot на ПК пользователя (не в облаке) — обходит блок
  Telegram на HF и держит ключ-сессию локально (безопасно).
- **Голос:** синтез JARVIS (Gemini Charon) → OGG/Opus (`gemini.speak_ogg`, ffmpeg есть) → voice note.
- **Безопасность:** только белый список контактов; окно отмены 5 сек (отправка
  придерживается, тап «Отменить» = ничего не ушло); лимит частоты/анти-спам.
- **Триггер:** натуральный язык боту («отправь Айгуль голосом, что опоздаю»).

## Архитектура
```
iPhone → @Aimyjarvisbot (HF) → разбор намерения (Gemini)
      → проверка белого списка (Neon) → окно отмены 5 сек
      → PC-link: {type:command, action:send_telegram, target, text, as_voice}
      → ПК: Telethon отправляет (текст или speak_ogg→voice note)
      → {type:response, ok, error?} → бот: «✅ Отправлено Айгуль»
```

## Компоненты
1. **`telegram_bot/pc_userbot.py`** (новый, на ПК): Telethon-клиент.
   - CLI `login` — одноразовый интерактивный вход (номer+код, 2FA) → `config/userbot.session`.
   - API: `send(target, text, as_voice)`; резолвит target (username/phone/id), at
     `as_voice` синтезирует через GeminiClient.speak_ogg и шлёт voice note.
2. **`pc_server.py`**: в `run_client`, если `msg.action == "send_telegram"` → новый
   обработчик через pc_userbot; иначе прежний `_execute`.
3. **`pc_bridge.py`** (HF): метод `send_userbot(target, text, as_voice, user_id, timeout)`
   шлёт структурную команду и ждёт ответ.
4. **Белый список** (HF, durable в Neon через `memory_store`): алиас → telegram target.
   Команды бота: `/contacts`, `/addcontact <алиас> <@username|телефон>`, `/delcontact <алиас>`.
5. **Намерение + отмена** (HF, в обработчике чата): Gemini извлекает
   `{is_send, contact, text, as_voice}`; проверка списка; 5-сек pending с инлайн-кнопкой «Отменить».

## Конфиг (ПК, config/api_keys.json — не коммитим)
- `telethon_api_id`, `telethon_api_hash` (с my.telegram.org).
- Сессия: `config/userbot.session` (локальный файл, в .gitignore).

## Анти-бан
Белый список, окно отмены, малый объём, лёгкая задержка между отправками, только свой аккаунт.

## Обработка ошибок
- Не в белом списке → отказ + подсказка `/addcontact`.
- ПК офлайн → «не доставлено, ПК офлайн» (v1; очередь — позже).
- Telethon FloodWait/прочее → честный отчёт в чат.

## Вне scope v1 (следующие фазы)
Очередь при офлайн-ПК; управление контактами в Mini App; голос «мой» (форвард записи);
каналы кроме Telegram; направления «учёба/код/организация».
