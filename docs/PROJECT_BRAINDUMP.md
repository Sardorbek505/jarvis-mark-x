# JARVIS — Полный технический Brain Dump

> Документ-передача проекта senior-инженеру, который никогда не видел код.
> Состояние на 2026-06-16. Репозиторий: `Sardorbek505/jarvis-mark-x`.

---

## 0. Самое важное в одном абзаце

Это **две связанные системы под одним брендом JARVIS**, живущие в одном репозитории:

1. **Десктопный JARVIS** (`main.py` + `ui.py` + `core/` + `memory/`) — голосовой
   ассистент для ПК на **PyQt6** и **Gemini Live API** (двусторонний аудио-стрим,
   речь↔речь, function-calling). Управляет компьютером.
2. **Telegram JARVIS** (`telegram_bot/`) — мобильный/удалённый интерфейс: Telegram-бот
   (webhook) + **Mini App** (веб-приложение в Telegram) + **PC-bridge** (мост, через
   который телефон управляет домашним ПК поверх интернета). Имеет собственную
   **долгую память в Postgres (Neon)**.

Обе системы переиспользуют общий слой действий **`actions/`** (открыть приложение,
музыка, окна, камера, скриншот и т.д.) и **`tools/spotify/`** (Spotify Web API).

Объём: ~18 700 строк (Python + JS/CSS/HTML).

---

## 1. Главная цель проекта

**Что это.** Персональный ИИ-ассистент «как у Тони Старка»: понимает естественную
речь/текст (русский в первую очередь), отвечает голосом, помнит пользователя,
управляет компьютером и проактивно помогает (сам пишет утром/вечером, напоминает).

**Какую проблему решает.**
- Десктоп: руки-свободные управление ПК голосом + умный собеседник.
- Telegram: тот же ассистент **в кармане** и **удалённо** — можно из любой точки
  написать/сказать боту, и он выполнит команду на домашнем ПК, ответит голосом,
  поставит напоминание, покажет привычки/задачи в красивом Mini App.

**Vision.** Единый «JARVIS», который всегда с тобой (телефон), всегда помнит
контекст (вечная память), управляет твоим цифровым окружением (ПК, музыка,
календарь) и работает проактивно как личный секретарь.

---

## 2. Архитектура

### 2.1 Две подсистемы и их связь

```
┌──────────────────────────────────────────────────────────────────┐
│ ДЕСКТОП (на ПК пользователя)                                       │
│   main.py ── Gemini Live API (audio↔audio, tools)                  │
│     ├─ ui.py (PyQt6 лицо/окно)                                     │
│     ├─ core/* (мозг: проактивность, эмоции, новости, перевод…)     │
│     ├─ memory/memory_manager.py (JSON-память)                      │
│     └─ actions/* + tools/spotify/* (выполнение команд на ПК)       │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ ОБЛАКО (один web-сервис: Render/Koyeb)                             │
│   telegram_bot/render_app.py  =  FastAPI app, точка входа         │
│     ├─ Telegram bot (python-telegram-bot, webhook)                │
│     │     bot.py — команды, роутинг, хендлеры                     │
│     ├─ Mini App (miniapp_server.py)                               │
│     │     / , /app.js , /style.css  + WebSocket /ws               │
│     ├─ PC-bridge (/pc-link WebSocket)  ── pc_bridge.py            │
│     ├─ gemini_client.py (Gemini: chat/STT/TTS/vision/facts)       │
│     ├─ memory_store.py (Postgres/Neon  ИЛИ  SQLite fallback)      │
│     ├─ proactive.py (утренний/вечерний брифинг, планировщик)      │
│     ├─ reminders.py + directives.py + agenda.py (NL-парсинг)      │
│     └─ personas.py / onboarding.py / user_context / weather       │
└──────────────────────────────────────────────────────────────────┘
            ▲ WebSocket (/pc-link, исходящее соединение от ПК)
            │
┌───────────┴──────────────────────────────────────────────────────┐
│ ДОМАШНИЙ ПК: telegram_bot/pc_server.py (start_pc.bat)             │
│   Подключается ИЗ дома К облаку (обходит NAT), принимает команды, │
│   выполняет их через actions/*, шлёт результат обратно.          │
└──────────────────────────────────────────────────────────────────┘

Внешние сервисы: Gemini API, Neon Postgres, Spotify Web API,
Open-Meteo (погода), bigdatacloud (reverse-geocode в Mini App).
```

### 2.2 Ключевая идея PC-bridge (почему так)
Домашний ПК обычно за NAT/без белого IP — облако не может «постучаться» к нему.
Поэтому **ПК сам инициирует исходящее WebSocket-соединение** к облаку
(`pc_server.run_client` → `wss://<host>/pc-link?token=…`). Облако (`PCBridge`)
держит это соединение и проксирует в него команды от Telegram/Mini App. Это
классический паттерн **reverse tunnel / outbound persistent connection**.

### 2.3 Design patterns
- **Adapter / Facade**: `actions/*` — единый интерфейс «команда(dict)->str» поверх
  разнородных ОС-операций; и десктоп, и pc_server зовут одни и те же функции.
- **Strategy (fallback-цепочки)**: TTS (Gemini→браузер), музыка (Web API→media-keys→UI),
  память (Postgres→SQLite), модель Gemini (список с фолбэком).
- **Provider/Callback injection**: `gemini_client.set_context_provider()` — клиент не
  знает про память/таймзоны, ему «вкалывают» контекст колбэком из bot.py.
- **State machine**: Mini App орб (idle/listening/processing/speaking), голосовой
  режим (push-to-talk / hands-free + VAD).
- **Command directives**: Gemini возвращает скрытые блоки `[[REMINDERS]]/[[HABITS]]/
  [[TASKS]]`, исполнитель (`directives.py`) превращает их в записи БД.
- **Keyword intent routing**: намерение определяется по ключевым словам (не ML-классификатор).

### 2.4 Почему такая архитектура
- Один Gemini-ключ и один стиль личности на обе системы.
- Мобильный доступ без открытия портов дома (reverse tunnel).
- Дешёвый хостинг (один контейнер FastAPI) + внешняя бесплатная БД (Neon).
- Mini App = богатый UI без публикации в сторах (живёт внутри Telegram).

---

## 3. Структура файлов (дерево + назначение)

```
jarvis-mark-x/
├── main.py                  # Десктоп: точка входа, класс Jarvis, Gemini Live loop
├── ui.py                    # Десктоп: PyQt6 окно/лицо (JarvisUI)  (импортируется main.py)
├── face.png                 # Картинка лица для UI
├── requirements.txt         # Зависимости ДЕСКТОПА (PyQt6, sounddevice, opencv…)
├── Dockerfile               # Образ облака: uvicorn render_app:app на $PORT
├── docker-compose.yml       # Локальный запуск облака
├── Caddyfile                # Реверс-прокси (если VPS)
├── render.yaml              # Деплой-конфиг Render (env, startCommand, healthCheck)
├── core/                    # ДЕСКТОПНЫЙ «мозг» (продвинутые модули)
│   ├── prompt.txt           #   системный промпт десктопного JARVIS
│   ├── storage.py           #   atomic_write_json / safe_read_json (надёжный I/O)
│   ├── user_profile.py      #   UserProfile — досье пользователя (десктоп)
│   ├── calendar_manager.py  #   календарь + NL-парсинг дат/длительностей
│   ├── smart_reminders.py   #   «умные» напоминания по паттернам использования
│   ├── proactive_engine.py  #   проактивные инициативы (десктоп)
│   ├── initiative_engine.py #   движок инициатив/триггеров
│   ├── emotion_analyzer.py  #   анализ тона/эмоций пользователя
│   ├── news_manager.py      #   агрегатор новостей (RSS/feedparser)
│   ├── translation_manager.py# режим переводчика (история, контекст, предпочтения)
│   ├── team_collaboration.py#   командные сценарии
│   └── integrations.py      #   SpotifyIntegration/MovieIntegration/IntegrationManager
├── memory/
│   └── memory_manager.py    #   Простая JSON-память десктопа: load/update/format
├── actions/                 # ОБЩИЙ слой действий (десктоп И pc_server)
│   ├── open_app.py          #   запуск приложений по алиасам (subprocess)
│   ├── browser_control.py   #   управление браузером (открыть URL, поиск)
│   ├── window_control.py    #   окна: свернуть/развернуть/alt-tab/рабочий стол
│   ├── computer_settings.py #   громкость/скриншот/блокировка/выключение/sysinfo
│   ├── camera.py            #   снимок с веб-камеры (opencv)
│   ├── music_player.py      #   музыка БЕЗ API: media-keys (Win32) + Spotify URI
│   ├── spotify_controller.py#   музыка ЧЕРЕЗ Spotify Web API (нужны креды)
│   ├── movie_player.py      #   запуск фильмов (selenium/UI-автоматизация)
│   ├── web_search.py        #   веб-поиск
│   ├── weather.py           #   погода (десктопная версия)
│   ├── calendar.py          #   действия календаря (десктоп)
│   ├── morning_briefing.py  #   утренний брифинг (десктоп)
│   ├── file_controller.py   #   операции с файлами
│   ├── vision_review.py     #   анализ экрана (vision)
│   └── modes.py             #   переключение режимов
├── tools/spotify/           # Spotify Web API клиент (OAuth refresh, поиск, девайсы)
│   ├── auth.py              #   токены/refresh (config/spotify_tokens.json)
│   ├── controller.py        #   play/pause/next/search/devices
│   ├── search.py, moods.py, devices.py
├── telegram_bot/            # ОБЛАЧНАЯ подсистема (бот + Mini App + bridge)
│   ├── render_app.py        #   ★ ТОЧКА ВХОДА облака: FastAPI app + lifespan
│   ├── bot.py               #   ★ Логика бота: команды, роутинг, хендлеры (1019 стр)
│   ├── config.py            #   Config (env→настройки), load()
│   ├── gemini_client.py     #   GeminiClient: chat/transcribe/TTS/vision/facts
│   ├── memory_store.py      #   ★ Durable память: Postgres/SQLite, схемы, методы
│   ├── miniapp_server.py    #   FastAPI-роуты Mini App + WebSocket + data-вкладки
│   ├── pc_bridge.py         #   PCBridge: реестр ПК-соединений, send_command
│   ├── pc_server.py         #   ★ Запускается на ПК: исполняет команды через actions/
│   ├── reminders.py         #   NL-парсер напоминаний + UTC-хелперы
│   ├── directives.py        #   Исполнитель [[REMINDERS]]/[[HABITS]]/[[TASKS]]
│   ├── agenda.py            #   NL-парсинг дат для задач/календаря
│   ├── proactive.py         #   Планировщик утро/вечер + генерация брифинга
│   ├── personas.py          #   Режимы личности (ментор/друг/бизнес)
│   ├── onboarding.py        #   Знакомство при первом /start (имя/город/цель)
│   ├── user_context.py      #   Таймзона/город/гео пользователя (in-RAM), describe()
│   ├── weather.py           #   Погода для брифинга (Open-Meteo)
│   ├── voice_util.py        #   PCM→OGG/Opus (ffmpeg) для голосовых Telegram
│   └── miniapp/             #   ФРОНТ Mini App
│       ├── index.html       #     вкладки (Чат/Сводка/Дела/Привычки/ПК) + орб
│       ├── app.js           #     WS-клиент, голос (PTT/hands-free+VAD), рендер вкладок
│       ├── style.css        #     тема (неон), орб-реактор, типографика, навбар
│       └── worklet.js       #     AudioWorklet: даунсэмпл микрофона в 16k PCM
├── config/                  # Конфиги/состояние (JSON), api_keys.example.json
│   ├── api_keys.example.json#   шаблон ключей (gemini, telegram, spotify, pc_link…)
│   ├── modes.json, news_preferences.json, translation_*.json, usage_patterns.json
├── scripts/                 # Запуск/установка
│   ├── start_pc.bat         #   запуск pc_server на ПК (с авто-рестартом)
│   ├── start_all.bat, install_autostart.ps1, setup_vps.sh
├── tasks/                   # Рабочие заметки агента
│   ├── todo.md, lessons.md, ux_rules.md, deploy_koyeb.md
├── docs/                    # Документация (этот файл, спеки)
└── (служебные: README.md, CLAUDE.md, LICENSE, тесты test_*.py в корне)
```

---

## 4. Точки входа и execution flow

### 4.1 Облако — `telegram_bot/render_app.py` (главная сейчас)
Запуск: `uvicorn telegram_bot.render_app:app --host 0.0.0.0 --port $PORT`
(Dockerfile/Render это и делает). Поток:
1. Импорт модулей; создаются singletons: `cfg` (config), `gemini` (GeminiClient),
   `bridge` (PCBridge), `memory` (MemoryStore).
2. `gemini.set_context_provider(_build_context)` — Gemini будет получать на каждый
   запрос строку: текущие дата/время+город (`user_context.describe`), блок памяти
   (`memory.cached_block`), оверлей персоны (`personas.overlay`).
3. `miniapp_server` получает ссылки на gemini/bridge/memory; статусы ПК
   броадкастятся в Mini App (`bridge.on_status_change`).
4. **FastAPI lifespan (startup)**:
   - `memory.init()` — подключение к Postgres (если есть `DATABASE_URL`), иначе SQLite;
     создаются таблицы (facts, profile, tasks, habits, habit_checks, reminders, meta).
   - Строится Telegram `Application` (webhook-режим, без Updater), регистрируются
     все CommandHandler/MessageHandler/CallbackQueryHandler.
   - `bot.setMyCommands`, `set_webhook(MINIAPP_URL/telegram-webhook)`;
     запускается `_webhook_keeper` (следит, что вебхук не сбросился).
   - Стартуют фоновые задачи: `proactive.loop` (утро/вечер), `_reminder_loop`
     (каждые 30с шлёт сработавшие напоминания).
5. **Runtime**: входящие апдейты Telegram приходят POST-ом на `/telegram-webhook`
   → отдаются в Application → хендлеры в `bot.py`. Mini App общается по `/ws`.
   Домашний ПК висит на `/pc-link`.

### 4.2 Обработка одного текстового сообщения в боте (`bot.py: handle_text`)
1. Авторизация (`_is_authorized` по `TELEGRAM_ALLOWED_USERS`).
2. **Онбординг?** если активен — ответ уходит в `onboarding.handle`.
3. **Напоминание?** если есть триггер («напомни/таймер…») → `_store_reminder`
   (parse в локальном времени → UTC → `memory.add_reminder`), иначе просит формат.
4. **PC-команда?** если `_looks_like_pc_command` и ПК онлайн → `bridge.send_command`.
5. **Иначе — Gemini**: `memory.ensure_loaded` → `gemini.chat` → `directives.apply`
   (создаёт привычки/задачи/напоминания из скрытых блоков, вырезает их) → ответ;
   фоновая `memory.observe` (извлечение фактов для долгой памяти).

### 4.3 Голосовое в боте (`handle_voice`)
download ogg → `gemini.transcribe` (STT) → если онбординг/PC-команда — туда; иначе
`gemini.chat_with_audio` → `directives.apply` → `gemini.speak_ogg` (TTS Charon →
PCM→OGG через `voice_util`) → `reply_voice` (голос-в-голос); индикатор «record_voice»
держится живым во время обработки (`_busy`).

### 4.4 Десктоп — `main.py`
`main()` → создаёт `JarvisUI` → `Jarvis(ui)` → подключается к Gemini Live
(`client.aio.live.connect(model=LIVE_MODEL, config=…)`, `response_modalities=["AUDIO"]`).
Микрофон стримится в сессию; ответы — аудио наружу. Когда модель вызывает
инструмент (`response.tool_call.function_calls`), `_execute_tool(fc)` маршрутизирует
в `actions/*` и возвращает `FunctionResponse`.

---

## 5. Модули — назначение, связи, ключевые функции

### Облако (`telegram_bot/`)

- **render_app.py** — сборка FastAPI-приложения, lifespan, регистрация хендлеров,
  вебхук, фоновые циклы (`_reminder_loop`, `proactive.loop`), `/health`.
  Связи: всё. Главное: `lifespan`, `_set_webhook`, `_webhook_keeper`, `build_app`.

- **bot.py** (★) — вся логика Telegram. Ключевое:
  - Роутинг: `_looks_like_pc_command`, `_looks_like_reminder`, `_PC_KEYWORDS`,
    `_REMINDER_TRIGGERS`.
  - Хендлеры: `handle_text`, `handle_voice`, `handle_photo`, `on_callback`
    (inline-кнопки), множество команд `cmd_*` (start/help/app/status/pc/screenshot/
    camera/vol/lock/sysinfo/briefing/remind/reminders/task/tasks/today/done/habit/
    habits/check/morning/evening/mode/profile/remember/forget/clear).
  - Helpers: `_store_reminder`, `_apply_reminder_directives` (обёртка над
    `directives.apply`), `_busy` (живой индикатор), клавиатуры
    `_mode_keyboard/_habits_keyboard/_tasks_keyboard`, рендер `_render_habits`.
  - Связи: gemini, memory, bridge, reminders/directives/agenda/personas/onboarding/
    proactive/user_context.

- **config.py** — `Config(NamedTuple)` + `load()`: читает env ИЛИ
  `config/api_keys.json`. Поля: gemini_api_key/model, telegram token/allowed_users,
  pc_ws_host/port, miniapp_url/port (или `RENDER_EXTERNAL_URL`), pc_link_url/token,
  default_city, timezone.

- **gemini_client.py** (★ «AI-мозг» Telegram) — класс `GeminiClient`:
  - `_SYSTEM_PROMPT` (личность + правила: честность, не врать про действия на ПК,
    протокол директив для напоминаний/привычек/задач).
  - `_system_for(uid)` — системный промпт + живой контекст от провайдера.
  - `_generate(...)` — единый вызов с фолбэком по списку моделей `_MODELS`.
  - `chat(uid,text)` — диалог с историей (`_history`, до `_MAX_HISTORY`).
  - `transcribe(bytes,mime)` — STT. `chat_with_audio` — голос→ответ.
  - `chat_with_image` — vision. `synthesize_speech(text, voice="Charon")` — TTS→PCM.
    `speak_ogg` — TTS+кодирование в OGG (через voice_util).
  - `extract_facts` — вытащить устойчивые факты; `clear_history`.

- **memory_store.py** (★ долгая память) — класс `MemoryStore`:
  - Бэкенд: Postgres (asyncpg, если `DATABASE_URL`) ИЛИ SQLite (aiosqlite, fallback,
    эфемерный на free-хостинге). Схемы `_SCHEMA_SQLITE/_SCHEMA_PG`.
  - Таблицы: `facts`, `profile`(+mode), `tasks`, `habits`, `habit_checks`,
    `reminders`, `meta`.
  - API: `ensure_loaded/get_profile/set_profile_field/add_fact/cached_block`,
    `get_mode/set_mode/cached_mode`, задачи `add_task/get_tasks/complete_task`,
    привычки `add_habit/get_habits/toggle_habit/delete_habit` (+`_streak`),
    напоминания `add_reminder/get_due_reminders/mark_reminder_sent/list_reminders`,
    `get_meta/set_meta`, `observe` (фоновое обучение фактам), `clear`.

- **miniapp_server.py** — статика Mini App (с `no-store`), `/ping`, `/health`,
  WS `/ws` (чат/голос/PC + data-вкладки), WS `/pc-link`. Ключевое:
  `_build_view`/`_send_view`/`_handle_action` (dashboard/habits/tasks),
  `_handle_text`/`_handle_voice` (Gemini + directives + TTS), `_pcm_to_wav`,
  `broadcast_pc_status`, `_looks_like_pc_command`.

- **pc_bridge.py** — `PCBridge`: `register/unregister`, `handle_message` (ответы от
  ПК по id запроса через Future), `send_command/send_command_full` (с таймаутом),
  `connected`, `on_status_change`.

- **pc_server.py** (★ на ПК) — `run_client(url,token)` держит WS к `/pc-link`,
  `_handle(msg)` → `_execute(text)` (роутинг по `_KW`) → `actions/*`. Категории:
  camera/system/music/weather/app/search/window/calendar/briefing. Музыка через
  `_parse_music`→`music_player` (media-keys, без API) либо `spotify_player`
  (shuffle/now_playing). Самодиагностика `ModuleNotFoundError`.

- **reminders.py** — `parse_reminder(text, now)` (ищет время В ЛЮБОМ месте фразы,
  относительные/абсолютные, «через минуту/час/полчаса», таймзона→UTC),
  `to_utc_iso/now_utc_iso/confirm_label/fmt_local`.

- **directives.py** — `apply(memory, uid, reply, tz)`: парсит блоки
  `[[REMINDERS]]/[[HABITS]]/[[TASKS]]`, пишет в БД, чистит текст, отдаёт сводку.

- **agenda.py** — NL-парсинг дат для задач/событий: `parse`, `fmt_due`,
  `is_today`, `is_overdue`, `render_list`.

- **proactive.py** — `loop/tick/_send_briefing`, `_morning_prompt/_evening_prompt`;
  утром добавляет погоду (`weather.for_city`), отправляет в нужный местный час.

- **personas.py** — `PERSONAS`, `overlay(mode)`, `label`, `list_text`,
  `DEFAULT_MODE` (ментор/друг/бизнес поверх базовой личности).

- **onboarding.py** — стейт-машина знакомства (`_STEPS`: name/city/about/goals),
  `start/handle/is_active/already_onboarded`, сохраняет в профиль/факты,
  ставит meta `onboarded`.

- **user_context.py** — in-RAM словарь по uid (tz/city/lat/lon), `update`,
  `local_now`, `get_city`, `describe` (строка контекста для Gemini).

- **weather.py** — `for_city(city)` через Open-Meteo (геокодер+прогноз), без ключа,
  мягкий фолбэк в None.

- **voice_util.py** — `pcm_to_wav`, `pcm_to_ogg` (через портативный
  `imageio-ffmpeg`, libopus) для голосовых Telegram.

### Mini App фронт (`telegram_bot/miniapp/`)
- **app.js** — WS-клиент; машина состояний орба; голос: **push-to-talk** (зажал=пишет,
  отпустил=отправил) и **hands-free** (тап → непрерывно, клиентский **VAD** по RMS
  ловит конец фразы, отвечает и слушает дальше; пауза захвата пока бот говорит);
  вкладки `switchTab/renderDashboard/renderHabits/renderTasks`; действия
  `habitToggle/taskDone/addHabit/addTaskOrReminder`; воспроизведение PCM 24k.
- **index.html** — шапка (заголовок экрана + статусы ПК/AI), 5 вкладок, орб, нижний
  «таблеточный» навбар (SVG-иконки + точка-индикатор).
- **style.css** — тема (неон cyan/purple), орб-арк-реактор (grid-центрирование,
  кольца-катушки, halo, HUD-sweep), типографическая система (по правилам DesignMe),
  премиальный навбар.
- **worklet.js** — AudioWorklet, даунсэмпл микрофона до 16 кГц int16 PCM, чанки ~100мс.

### Десктоп (`main.py`, `core/`, `memory/`)
- **main.py** — `Jarvis` (Gemini Live сессия, аудио-ввод/вывод, `_build_config`,
  `_execute_tool`, обработка `tool_call`), `_load_system_prompt` (core/prompt.txt).
- **core/** — расширенный «мозг»: `user_profile.UserProfile`, `calendar_manager`
  (+парсинг дат/длительностей), `smart_reminders` (паттерны), `proactive_engine`,
  `initiative_engine`, `emotion_analyzer.EmotionAnalyzer`, `news_manager`
  (`NewsAggregator/NewsFilter/NewsArticle/NewsPreferences`), `translation_manager`
  (`TranslationManager/History/ContextMemory/Preferences`), `team_collaboration`,
  `integrations` (`SpotifyIntegration/MovieIntegration/IntegrationManager`),
  `storage` (atomic JSON).
- **memory/memory_manager.py** — простая JSON-память: `load_memory/update_memory/
  format_memory_for_prompt`.

---

## 6. AI Brain — как принимаются решения

Два разных «мозга»:

**Telegram (gemini_client + роутинг в bot.py/pc_server):**
- **Intent recognition — НЕ ML**, а keyword-matching: `_looks_like_pc_command`,
  `_looks_like_reminder`, `_KW` в pc_server. Это сознательно простой, предсказуемый
  роутер: сначала спец-ветки (онбординг/напоминание/PC), потом общий Gemini-чат.
- **Command parsing**: даты/время — регэкспами (`reminders.parse_reminder`,
  `agenda.parse`); музыка — `_parse_music`.
- **Reasoning layer**: сам Gemini (модель `gemini-2.5-flash` по умолчанию) на этапе
  чата. Ему даётся системный промпт + живой контекст (время/город/память/персона).
- **Действия из разговора**: т.к. у текстового Gemini нет нативного tool-calling в
  этом пути, используется **директивный протокол** — модель пишет скрытые блоки
  `[[REMINDERS]]/[[HABITS]]/[[TASKS]]`, а `directives.apply` их выполняет. Это решает
  проблему «бот обещал, но не сделал».

**Десктоп (Gemini Live):** настоящий **function-calling** — модель сама решает,
какой инструмент вызвать; `_execute_tool` исполняет `actions/*`. Reasoning —
полностью на стороне Live-модели, плюс core/-движки добавляют проактивность.

---

## 7. Memory system

**Telegram (главная, durable):** `memory_store.MemoryStore`.
- **Долгосрочная**: Postgres (Neon) — таблицы facts/profile/tasks/habits/reminders/
  meta. Переживает рестарты. Без `DATABASE_URL` — SQLite (эфемерно на free-хостинге).
- **Краткосрочная**: история диалога в RAM внутри `GeminiClient._history`
  (до `_MAX_HISTORY`), плюс `_cache` в MemoryStore (профиль/факты/режим на uid).
- **Запись**: явно (`/remember`, онбординг, директивы, задачи/привычки/напоминания)
  и **неявно** — `memory.observe(...)` после каждого обмена извлекает устойчивые
  факты через `gemini.extract_facts`.
- **Извлечение**: `cached_block(uid)` формирует блок «что я о тебе знаю», который
  через context_provider попадает в системный промпт каждого запроса.

**Десктоп:** `memory/memory_manager.py` — JSON-память (`load/update/format`) +
`core/user_profile.py` (досье) + `core/storage.py` (атомарная запись).

---

## 8. Voice system

**Telegram-бот:** STT и TTS — через **Gemini** (не отдельные библиотеки):
`transcribe` (ogg→текст), `synthesize_speech` (текст→PCM 24k, голос Charon),
кодирование в OGG/Opus портативным **imageio-ffmpeg** (`voice_util`). Голос-в-голос:
voice in → voice out.

**Mini App:** запись — Web Audio API + **AudioWorklet** (`worklet.js`, даунсэмпл в
16k PCM) → WS на сервер → Gemini STT. Воспроизведение — PCM 24k через AudioContext.
Фолбэк TTS — браузерный `speechSynthesis` (если серверный звук не пришёл, сигнал
`tts_failed`). Два режима ввода: **push-to-talk** (зажатие) и **hands-free**
(тап + клиентский **VAD** по RMS-энергии, авто-сегментация фраз). **Wake word** —
НЕ реализован (запуск по кнопке/жесту).

**Десктоп:** **Gemini Live** — непрерывный двусторонний аудио-стрим (микрофон↔динамик)
через `sounddevice`; по сути continuous listening на стороне Live-сессии.

---

## 9. Computer control (`actions/*`, выполняется на ПК)
- **Открытие приложений** — `open_app.py`: словарь алиасов («спотифай»→spotify),
  `subprocess.Popen`/`xdg-open`/`open`.
- **Браузер** — `browser_control.py`: открыть URL/поиск.
- **Окна** — `window_control.py`: свернуть/развернуть/alt-tab/рабочий стол
  (pyautogui/pygetwindow).
- **Системное** — `computer_settings.py`: громкость, скриншот, блокировка,
  выключение/перезагрузка, sysinfo (psutil).
- **Камера** — `camera.py`: снимок через opencv.
- **Музыка** — `music_player.py`: media-клавиши через Win32 `keybd_event` (без
  зависимостей) + запуск Spotify по `spotify:` URI + веб-фолбэк; `spotify_controller.py`
  + `tools/spotify/*`: полноценный Web API (play_query/pause/next/devices), нужны
  OAuth-креды (`config/spotify_tokens.json`).
- **Ввод текста/клавиши** — через pyautogui (в music/window/movie).
- Доставка результата (включая картинки скриншота/камеры base64) — обратно через
  PC-bridge в Telegram/Mini App.

---

## 10. API integrations
- **Gemini API** (google-genai): и Live (десктоп), и обычные generate (Telegram:
  chat/STT/TTS/vision/facts). Модель по умолчанию `gemini-2.5-flash`.
- **Telegram Bot API** (python-telegram-bot, webhook).
- **Neon Postgres** (asyncpg) — долгая память.
- **Spotify Web API** (requests + OAuth refresh) — `tools/spotify`, опционально.
- **Open-Meteo** — погода (без ключа), Telegram-брифинг.
- **bigdatacloud** reverse-geocode — Mini App определяет город по гео.
- **RSS/feedparser** — десктопные новости.
- **Selenium/webdriver-manager** — десктоп: выбор фильмов.
- НЕ используются: OpenAI, Claude, Discord, локальные модели, YouTube API.

---

## 11. Зависимости (что и зачем)

**Облако (`telegram_bot/requirements.txt`):** google-genai (Gemini),
python-telegram-bot (бот), fastapi + uvicorn (web/WS), websockets (PC-bridge/клиент),
aiosqlite (fallback-память), asyncpg (Neon Postgres), imageio-ffmpeg (PCM→OGG),
httpx (Open-Meteo).

**Десктоп (`requirements.txt`):** PyQt6 (UI), google-genai (Live), Pillow (картинки),
sounddevice (аудио), psutil (система), opencv-python (камера), mss + pygetwindow
(скрин/окна), pyautogui (клавиши), selenium + webdriver-manager (фильмы),
google-api-python-client + google-auth-* (Google Calendar, опц.), feedparser (новости),
requests + rapidfuzz (Spotify + fuzzy), python-telegram-bot/websockets/fastapi/
uvicorn/aiosqlite (если запускать облако локально).

---

## 12. Текущее состояние

**Готово и стабильно (Telegram):** бот в webhook-режиме; долгая память (Neon);
онбординг; режимы личности; задачи (NL-даты) и привычки (серии); напоминания
(durable, таймзона-корректные, исполнение из чата директивами); проактивные
утро/вечер брифинги (+погода); голос-в-голос; Mini App с 5 вкладками (Чат/Сводка/
Дела/Привычки/ПК), управление вкладок из чата; PC-bridge (reverse tunnel); музыка
без Spotify-кредов (media-keys).

**Работает частично:** Spotify Web API (нужны OAuth-креды; без них — media-keys);
действия на ПК зависят от установленных на ПК зависимостей (иначе самодиагностика
«поставь pip install X»); погода требует egress к Open-Meteo (на Render открыт);
hands-free VAD — эвристика по порогу, требует калибровки.

**Десктоп (main.py/core):** обширный код (Live, проактивность, новости, перевод,
календарь, эмоции, команды) — присутствует, но в этой сессии не тестировался;
зрелость отдельных core-модулей неизвестна без отдельного аудита.

**Не реализовано:** wake word; вкладка «Я» (память/смена режима) в Mini App;
аналитика/статистика привычек; интеграции календаря в Telegram-часть;
многопользовательскость (рассчитано на одного владельца).

---

## 13. Ограничения и узкие места
- **Хостинг free**: Render делит 750ч/мес на все сервисы; засыпание → cold start
  ~50с, напоминания во сне опаздывают. Решение — always-on хост (Koyeb) или
  дневной keep-alive (см. `tasks/deploy_koyeb.md`).
- **SQLite-fallback эфемерен** — без `DATABASE_URL` память стирается при рестарте.
- **Keyword-роутинг** хрупок к формулировкам (митигировано «срезанием до триггера»
  и поиском времени в любом месте фразы).
- **Директивный протокол** зависит от того, что модель корректно выдаёт блоки;
  при «забывчивости» модели действие не создастся (митигировано жёстким правилом
  в промпте + честным фолбэком).
- **PC-control** требует, чтобы дома работал `pc_server` и был онлайн.
- **user_context** — in-RAM: таймзона/город теряются при рестарте до следующего
  client_info из Mini App.
- **Масштабирование**: single-tenant; история диалога в RAM (не шарится между
  инстансами); один web-инстанс. Для多 пользователей нужна вынесенная история и
  очередь.

---

## 14. Качество кода (самокритично)
- **Дубли роутинга**: `_PC_KEYWORDS` есть и в `bot.py`, и в `miniapp_server.py` —
  стоит вынести в общий модуль (как уже сделали для `directives`).
- **Десктоп vs облако**: два «мозга» и две памяти (JSON vs Postgres) — концептуально
  разъезжаются; в идеале — общий слой памяти/персоны.
- **Историческое наследие в корне**: куча `test_*.py`, `fix_ui*.py`,
  `get_spotify_token.py`, `__pycache__` в гите — мусор/техдолг, просится чистка и
  `.gitignore`.
- **Spotify**: две реализации музыки (`music_player` vs `spotify_controller`/
  `tools/spotify`) с разной зрелостью — путаница; стоит явно задокументировать
  «media-keys по умолчанию, Web API опционально».
- **Кеш Telegram**: лечится `no-store`, но был источником долгой путаницы (исправлено).
- **Обработка ошибок**: местами широкие `except Exception` глушат причины (частично
  улучшено самодиагностикой в pc_server).
- **Тесты**: нет автоматического CI; проверки — ручные прогоны в сессии.

---

## 15. Будущее развитие
- **Ближайшее**: завершить переезд на always-on хост (Koyeb) → надёжные напоминания
  без keep-alive; вычистить корень репо (тесты/кэш/одноразовые скрипты) + `.gitignore`;
  вынести общий keyword-роутинг.
- **Среднее**: вкладка «Я» (память + смена режима) в Mini App; статистика привычек
  (графики); календарь в Telegram-часть; «напиши контакту» голосом (через юзербот);
  объединить память десктопа и облака.
- **Долгое**: настоящий tool-calling и в Telegram-пути (вместо директив, если перейти
  на модель с function-calling в этом канале); wake word на ПК; мультиязычность
  Mini App; многопользовательскость (вынести историю/сессии в БД, очередь задач).

---

## 16. Ключевые классы/функции/файлы (без пропусков, по подсистемам)
- **Облако точки входа**: `render_app.py` (lifespan, build_app, _set_webhook,
  _webhook_keeper, _reminder_loop), `Dockerfile`, `render.yaml`.
- **bot.py**: handle_text/handle_voice/handle_photo/on_callback; cmd_* (start, help,
  app, status, pc, screenshot, camera, vol, lock, sysinfo, briefing, remind,
  reminders, task, tasks, today, done, habit, habits, check, morning, evening, mode,
  profile, remember, forget, clear); _store_reminder, _apply_reminder_directives,
  _busy, _mode/_habits/_tasks_keyboard, _render_habits, _PC_KEYWORDS, _REMINDER_TRIGGERS.
- **gemini_client.py**: GeminiClient(_SYSTEM_PROMPT, _system_for, _generate, chat,
  transcribe, chat_with_audio, chat_with_image, synthesize_speech, speak_ogg,
  extract_facts, set_context_provider, clear_history).
- **memory_store.py**: MemoryStore(init/close, ensure_loaded, get_profile,
  set_profile_field, add_fact, cached_block, get_mode/set_mode/cached_mode,
  add_task/get_tasks/complete_task, add_habit/get_habits/toggle_habit/delete_habit,
  add_reminder/get_due_reminders/mark_reminder_sent/list_reminders, get_meta/set_meta,
  observe, clear) + _streak, _sort_tasks, схемы.
- **miniapp_server.py**: serve_index/app.js/style.css/worklet/ping/health,
  broadcast_pc_status, pc_link, ws_endpoint, _build_view/_send_view/_handle_action,
  _handle_text/_handle_voice/_send_text, _pcm_to_wav, _looks_like_pc_command.
- **pc_bridge.py**: PCBridge(register/unregister/handle_message/send_command/
  send_command_full/connected/on_status_change).
- **pc_server.py**: run_client, _handle, _execute, _do_camera/_do_screenshot,
  _parse_music/_parse_window/_parse_calendar, _get_sysinfo, _KW.
- **reminders.py**: parse_reminder, _find_time, now_utc_iso, to_utc_iso,
  confirm_label, fmt_local.
- **directives.py**: apply (+ регэкспы блоков).
- **agenda.py**: parse, fmt_due, is_today, is_overdue, render_list.
- **proactive.py**: loop, tick, _send_briefing, _morning_prompt, _evening_prompt.
- **personas.py**: PERSONAS, overlay, label, list_text, DEFAULT_MODE.
- **onboarding.py**: start, handle, is_active, already_onboarded, _STEPS, _save.
- **user_context.py**: update, local_now, get_city, describe, _resolve_tz.
- **weather.py**: for_city, _CODES.  **voice_util.py**: pcm_to_wav, pcm_to_ogg.
- **config.py**: Config(NamedTuple), load.
- **Mini App**: app.js (switchTab, renderDashboard/Habits/Tasks, onAudioChunk+VAD,
  openMic/closeMic/beginSegment/endSegment, playPCM, speak, awaitVoice), index.html,
  style.css, worklet.js (PCMProcessor).
- **Десктоп**: main.py (Jarvis, _build_config, _execute_tool, main), ui.py (JarvisUI),
  core/* (UserProfile, calendar_manager, smart_reminders, proactive_engine,
  initiative_engine, EmotionAnalyzer, news_manager*, translation_manager*,
  team_collaboration, integrations*, storage), memory/memory_manager.py.
- **actions/**: open_app, browser_control, window_control, computer_settings, camera,
  music_player, spotify_controller, movie_player, web_search, weather, calendar,
  morning_briefing, file_controller, vision_review, modes.
- **tools/spotify/**: auth, controller, search, moods, devices.

---

## 17. История развития (по коммитам этой и прошлой работы)
1. **Десктопная база**: PyQt6 + Gemini Live, actions/, core/, Spotify, камера, окна,
   vision, новости, перевод.
2. **Telegram-канал**: бот + Mini App + PC-bridge; авто-определение Render URL;
   webhook вместо polling (фикс «молчащего» бота: статический путь + secret header);
   Mini App заговорил настоящим голосом Charon (вместо браузерного робота).
3. **«Личность и память»** (эта сессия, слоями):
   - agenda (задачи/NL-даты) → proactive (утро/вечер) → onboarding (знакомство).
   - voice-everywhere (голос→голос, imageio-ffmpeg) → habits (привычки+серии) →
     inline-кнопки → weather в брифинге.
   - durable память: переход на Postgres/Neon.
4. **Напоминания**: durable + таймзона-корректные; парсер времени «в любом месте
   фразы»; директивный протокол (чат реально создаёт напоминания/привычки/задачи);
   фикс «Gemini обещал, но не сделал».
5. **Mini App как приложение**: вкладки (Чат/Сводка/Дела/Привычки/ПК) + WS-данные;
   плавающий навбар по референсу; орб-арк-реактор (несколько итераций центрирования
   и анимации); типографика по правилам DesignMe; `no-store` против кеша Telegram;
   голос как в WhatsApp (PTT + hands-free VAD).
6. **Инфра**: lightweight `/ping`, портативный Docker CMD, гайд переезда на Koyeb
   (после исчерпания free-часов Render).

---

## 18. Передача следующему инженеру — что знать в первую очередь
1. **Где что запускается**: облако = `render_app:app` (uvicorn, $PORT); ПК =
   `python -m telegram_bot.pc_server` (start_pc.bat); десктоп = `python main.py`.
2. **Конфиг**: всё через env ИЛИ `config/api_keys.json` (`config.load`). Критичные:
   `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, `DATABASE_URL`
   (Neon!), `PC_LINK_TOKEN`, `MINIAPP_URL` (с него строится вебхук).
3. **Данные живут в Neon** — менять хостинг можно свободно, БД не трогая.
4. **Поток сообщения** см. §4.2/4.3; роутинг — keyword-based (§6).
5. **Действия из чата** — через директивный протокол (§6, `directives.py`),
   а не tool-calling; промпт в `gemini_client._SYSTEM_PROMPT` это требует.
6. **PC-bridge** — reverse WebSocket (§2.2); ПК сам коннектится к облаку.
7. **Mini App** — ванильный фронт (без сборки), отдаётся FastAPI c `no-store`;
   правки видны после ре-деплоя и переоткрытия (Telegram кешировал — лечено).
8. **Грабли**: free-хостинг (часы/сон) → Koyeb; SQLite эфемерен → ставь DATABASE_URL;
   при правках Mini App помни про кеш; музыка по умолчанию media-keys (Spotify Web API
   опционален); таймзона напоминаний — всё хранится в UTC, парсится в локальном.
9. **Развитие** — §15; быстрый выигрыш: переезд на always-on + чистка корня репо.
