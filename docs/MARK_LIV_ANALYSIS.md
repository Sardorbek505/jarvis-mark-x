# Разбор Mark-LIV (FatihMakes) против ДЖАРВИС Mark X

Дата разбора: 2026-09-18. Источник: `https://github.com/FatihMakes/Mark-LIV`,
коммит `476a9c0`. Разбор сделан по исходникам (48 файлов, ~24 800 строк), а не
по README: все параметры инструментов извлечены из AST, а не переписаны из
таблиц автора.

## 0. Главное за одну минуту

Проекты — родственники: общая структура (`main.py` + `ui.py` + `actions/` +
`core/` + `memory/` + `config/`), общий движок (Gemini Live), общий формат
объявления инструментов. Но разошлись они в разные стороны:

| | Mark-LIV | Наш Mark X |
|---|---|---|
| Строк Python | ~24 800 | ~31 100 |
| Инструментов модели | 24 (8 встроенных + 16 автозагружаемых) | 24 (все зашиты в `main.py`) |
| Модель Live | `gemini-3.1-flash-live-preview` | `gemini-2.5-flash-native-audio-latest` |
| Сильная сторона | глубина рабочего стола: браузер, файлы, код, игры, аватар | экосистема: Telegram-бот, Spotify, календарь, переводы, новости, деплой |
| Тесты | нет | 40+ файлов тестов, pytest, бенчмарки |
| Лицензия | **CC BY-NC 4.0** | MIT |

**Юридическое ограничение, которое определяет всё остальное:** Mark-LIV под
CC BY-NC 4.0 — некоммерческая лицензия с обязательным указанием авторства.
Мы под MIT. Копировать оттуда файлы дословно в наш репозиторий нельзя: это
сделает нашу MIT-лицензию недействительной для этих файлов и запретит
коммерческое использование всего проекта. **Брать можно идеи и архитектурные
решения, реализованные заново своим кодом** — идеи лицензией не защищены.
Всё, что ниже рекомендовано «взять», подразумевает именно переписывание.

---

## 1. Инструменты (function-calling), которых у нас нет

Полные схемы параметров, извлечённые из кода.

### 1.1 `undo` — отмена собственного действия  ⭐ приоритет 1
```
parameters:
  action: STRING — "undo" (по умолчанию) | "list"
required: []
```
Покрывает: move · rename · create · copy · write · delete · organize_desktop ·
volume · brightness · dark mode. Механика (`core/undo.py`, 121 строка): стек на
10 записей, каждая — `(label, callable)`; действие само регистрирует, как себя
откатить (`push_undo("громкость → 40%", lambda: volume_set(old))`). Запись
снимается со стека ДО выполнения, чтобы падающий откат нельзя было
зациклить. Файлы >1 МБ для undo записи не сохраняются осознанно.

**У нас этого не было.** **Сделано:** `core/undo.py` плюс регистрация в
`actions/file_controller.py` (move · rename · create_file · create_folder ·
copy · перезапись) и в `actions/computer_settings.py` (относительные изменения
громкости и яркости). Абсолютные значения в стек не кладутся сознательно:
Windows выставляет громкость и яркость от догадки, прежнего значения система
не отдаёт, а отмена, возвращающая выдумку, хуже её отсутствия. Удаление теперь
уходит в корзину ОС (`send2trash`), а не в `p.unlink()`.

### 1.2 Подтверждение необратимых действий  ⭐ приоритет 1 (это дефект безопасности у нас)
Не инструмент, а механизм (`core/confirm.py`, 161 строка). Для shutdown,
restart и toggle_wifi действие возвращает модели строку-инструкцию
`[CONFIRMATION_PENDING]`, а реально выполняется только когда **человек нажал
кнопку в HUD** (`resolve(True)`). Токен выдаёт интерфейс, не модель.

**У нас было** (уточнение к первой редакции этого разбора: гейт у нас есть,
и утверждение «подтверждения нет вовсе» было неверным — `grep` по словам
`confirm`/`undo` его не нашёл, потому что он называется `_DESTRUCTIVE`):
`main.py._execute_tool` требовал подтверждения для shutdown/restart/delete, но
подтверждением считался **повторный вызов того же инструмента в течение 90
секунд**. Модели было велено переспросить пользователя вслух — а проверки, что
человек ответил, не было: два вызова подряд модель делает сама. Ровно та
конструкция, которую автор Mark-LIV разбирает как «соглашение, а не гейт».

Вдобавок поле `self._pending_destructive` нигде не инициализировалось, так что
первое же «выключи компьютер» падало с `AttributeError` прямо в приёмном
цикле: гейт не защищал, а ронял сессию.

**Сделано** (`core/confirm.py` + баннер в `ui.py`): токен выдаёт интерфейс.
Действие выполняется только после нажатия кнопки в окне; решение человека
уходит в сессию текстом, и модель вызывает инструмент повторно. В headless,
где нажимать негде, остаётся прежнее правило повторного вызова — слабее, но
лучше, чем выключать машину молча.

### 1.3 `recall_memory` — поиск по полной памяти  ⭐ приоритет 1
```
parameters:
  query: STRING — ключевое слово, имя, тема, категория; пусто = выдать всё
required: []
```
Смысл: в системный промпт кладётся не вся память, а «ядро» (identity целиком +
самые свежие факты, по бюджету ~971 символ на 62 фактах) плюс **индекс ключей**,
которые в бюджет не влезли, под заголовком `[ALSO REMEMBERED]`. Модель видит,
что факт существует, и дёргает `recall_memory` — локальный поиск по файлу,
без сети.

**У нас было:** `memory/memory_manager.py` — 3 функции,
`format_memory_for_prompt()` вываливала в промпт **всю** память целиком.
**Сделано:** бюджет (`PROMPT_BUDGET_CHARS`, раздел «Личность» всегда целиком),
индекс невлезших ключей `[ТАКЖЕ ПОМНЮ]` с перемешиванием категорий по кругу,
локальный поиск `search_memory()` за инструментом `recall_memory`, `forget()`
и предохранитель `over_limit()`, который не удаляет молча, а пишет в лог.

### 1.4 `system_status` — телеметрия железа
```
parameters: {} (без аргументов)
```
Отдаёт CPU %, RAM, GPU load, температуру CPU, uptime, число процессов.
Плюс фоновый `SystemMonitor.check()` с порогами и голосовыми алертами
(`actions/system_monitor.py`), NVML для NVIDIA.

**У нас было:** psutil только в `telegram_bot/pc_server.py` и в полосках HUD —
голосом спросить было нельзя. **Сделано:** `actions/system_monitor.py` с
инструментом `system_status`. Недоступное (температура на Windows, NVML без
NVIDIA) не заменяется нулём: такой строки просто нет в ответе. Числительные
согласованы с тем, как произносятся.

### 1.5 `manage_monitor` — фоновое слежение за темами
```
parameters:
  action: STRING — add | remove | list
  topic:  STRING — тема ("space exploration", "AI news")
required: [action]
```
Раз в сутки проверяет DDG по каждой теме, хранит хеши заголовков, чтобы не
повторяться, и сам заговаривает, когда есть новое. Крипта/трейдинг — в чёрном
списке (`_is_blocked`).

**У нас:** есть `core/news_manager.py` (RSS + фильтры + дайджест), но это
«принеси новости по запросу», а не «следи за темой и разбуди меня, когда
появится новое».

### 1.6 `web_search` — 5 режимов вместо одного  ⭐ приоритет 2
```
parameters:
  query:  STRING — запрос или тема
  mode:   STRING — search | news | research | price | compare
  items:  ARRAY[STRING] — что с чем сравнивать (режим compare)
  aspect: STRING — price | specs | reviews | features
required: [query]
```
Реализация: сначала Gemini Grounded Search, параллельно DDG, результат
рендерится в контент-панель HUD.

**У нас было:** 40 строк на DuckDuckGo Instant Answer API — справочник
определений, а не поиск. **Сделано:** Gemini с включённым Google Search первым
источником, затем разбор выдачи DuckDuckGo, затем Instant Answer, и только
потом браузер — причём ответ про браузер теперь прямо говорит, что вопрос
остался без ответа, а не выдаёт вкладку за результат. Режимы
`search|news|research|price|compare` (и их русские имена, которые модель
действительно подставляет) меняют форму ответа. Живьём не проверено: сетевая
политика контейнера не пускает ни к DuckDuckGo, ни к Google.

### 1.7 `browser_control` — реальная автоматизация браузера  приоритет 2
```
parameters:
  action:      STRING — go_to | search | click | type | scroll | fill_form |
                        smart_click | smart_type | get_text | get_url | press |
                        new_tab | close_tab | screenshot | back | forward |
                        reload | switch | list_browsers | close | close_all
  browser:     STRING — chrome | edge | firefox | opera | operagx | brave |
                        vivaldi | safari
  url:         STRING
  query:       STRING
  engine:      STRING — google | bing | duckduckgo | yandex
  selector:    STRING — CSS-селектор
  text:        STRING
  description: STRING — описание элемента для smart_click/smart_type
  direction:   STRING — up | down
  amount:      INTEGER — пиксели прокрутки (500)
  key:         STRING — Enter, Escape, F5…
  path:        STRING — куда сохранить скриншот
  incognito:   BOOLEAN
  clear_first: BOOLEAN
required: [action]
```
На Playwright, 1132 строки. Ключевая идея: простой «открой сайт» запускает
**личный браузер пользователя** (его профиль, его залогиненные аккаунты), а
интерактивные действия подключают автоматизационный.

**У нас:** `browser` с 5 параметрами — только `go_to` и `search`. Selenium в
зависимостях есть, но используется он для `movie_player`, а не для общего
управления.

### 1.8 `computer_settings` — 56 действий против наших 9  ⭐ приоритет 2
```
parameters:
  action: STRING — volume_up | volume_down | volume_set | mute | brightness_up |
    brightness_down | sleep_display | pause_video | close_app | close_window |
    full_screen | minimize | maximize | snap_left | snap_right | switch_window |
    show_desktop | task_manager | focus_search | refresh_page | close_tab |
    new_tab | next_tab | prev_tab | go_back | go_forward | zoom_in | zoom_out |
    zoom_reset | find_on_page | scroll_up | scroll_down | scroll_top |
    scroll_bottom | page_up | page_down | copy | paste | cut | undo | redo |
    select_all | save | enter | escape | press_key | type_text | screenshot |
    lock_screen | open_settings | file_explorer | open_run | dark_mode |
    toggle_wifi | restart | shutdown
  description: STRING — запасной путь, если ни одно имя не подошло;
                        разрешается локально через difflib, без второго вызова модели
  value: STRING — уровень громкости 0-100, текст, имя клавиши
required: []
```
Отдельно ценен комментарий автора: раньше внутри инструмента делался **второй
вызов Gemini**, чтобы перевести фразу в имя действия; теперь все 56 имён
перечислены в самом объявлении, а опечатки чинит `difflib` за микросекунды.

**У нас:** `computer_control` — 9 действий (`volume_up | volume_down | mute |
brightness_up | brightness_down | screenshot | lock | shutdown | restart`) и
отдельный `window_control`. Вместе — примерно 25 из 56.

### 1.9 `computer_control` — мышь, клавиатура, поиск по экрану
```
parameters:
  action: STRING — type | smart_type | click | double_click | right_click |
    hotkey | press | scroll | move | copy | paste | screenshot | wait |
    clear_field | focus_window | screen_find | screen_click | random_data | user_data
  text, keys ("ctrl+c"), key, title, description,
  x: INTEGER, y: INTEGER, amount: INTEGER, seconds: NUMBER,
  direction: STRING, type: STRING, field: STRING (name|email|city),
  clear_first: BOOLEAN, path: STRING
required: [action]
```
`screen_find`/`screen_click` — найти элемент на экране по словесному описанию
и кликнуть по нему.

**У нас:** есть `actions/keyboard.py`, но инструмента с координатами мыши и
поиском по экрану нет.

### 1.10 `file_processor` — работа с загруженными файлами  приоритет 2
```
parameters:
  file_path:   STRING — пусто = последний загруженный файл
  action:      STRING — зависит от типа:
      image: describe | ocr | resize | compress | convert | info
      pdf: summarize | extract_text | to_word | info
      docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet
      csv/excel: analyze | stats | filter | sort | convert | info
      json: validate | format | analyze | to_csv
      code: explain | review | fix | optimize | run | document | test
      audio: transcribe | trim | convert | info
      video: trim | extract_audio | extract_frame | compress | transcribe | info | convert
      archive: list | extract
      pptx: summarize | extract_text | analyze
  instruction: STRING — свободная инструкция, если action не подходит
  format:      STRING — целевой формат (mp3, pdf, csv, png)
  width/height: INTEGER, scale: NUMBER, quality: INTEGER (1-100)
  start/end:   STRING — секунды или HH:MM:SS (trim)
  timestamp:   STRING — HH:MM:SS (кадр из видео)
  column:      STRING, value: STRING, condition: STRING (equals|contains|gt|lt)
  ascending:   BOOLEAN, save: BOOLEAN, destination: STRING
required: []
```
923 строки. Работает в паре с drag-and-drop в окно HUD.

**У нас:** ни приёма файлов в окно, ни обработки документов. Есть
`actions/obsidian.py` (markdown-заметки) — это про другое.

### 1.11 `code_helper` — код в файлах
```
parameters:
  action:      STRING — write | edit | explain | run | build | auto
  description: STRING — что код должен делать / какую правку внести
  language:    STRING — по умолчанию python
  output_path: STRING
  file_path:   STRING
  code:        STRING — сырой код для explain
  args:        STRING — аргументы CLI для run/build
  timeout:     INTEGER — секунд (30)
required: [action]
```

### 1.12 `dev_agent` — сборка проекта целиком
```
parameters:
  description:  STRING — что должен делать проект  (required)
  language:     STRING — python по умолчанию
  project_name: STRING
  timeout:      INTEGER — 30
required: [description]
```
Планирует, пишет многофайловый проект, ставит зависимости, открывает VSCode,
запускает и чинит ошибки. 639 строк.

### 1.13 `file_controller` — шире нашего
Их `action`: `list | create_file | create_folder | delete | move | copy |
rename | read | write | find | largest | disk_usage | organize_desktop | info`,
плюс параметры `extension`, `count`, и все операции регистрируются в undo.

**У нас нет:** `write` (дописать в существующий файл), `largest` (крупнейшие
файлы), `organize_desktop` (разложить рабочий стол по типам/датам), `info`,
поиска по расширению, `send2trash` (мы удаляем безвозвратно).

### 1.14 `desktop_control`
```
parameters:
  action: STRING — wallpaper | wallpaper_url | organize | clean | list | stats | task
  path:   STRING — картинка для обоев
  url:    STRING — картинка по ссылке
  mode:   STRING — by_type | by_date (для organize)
  task:   STRING — задача на естественном языке
required: [action]
```

### 1.15 `youtube_video`
```
parameters:
  action: STRING — play | summarize | get_info | trending
  query:  STRING
  save:   BOOLEAN — сохранить пересказ в блокнот
  region: STRING — код страны для trending (TR, US)
  url:    STRING
required: []
```
`summarize` тянет субтитры через `youtube-transcript-api` и пересказывает
видео, не открывая его.

**У нас:** `movie_player` (VK Видео) и `music_player` (Spotify) — YouTube не
покрыт вообще.

### 1.16 `send_message` — мессенджеры
```
parameters:
  receiver:     STRING  (required)
  message_text: STRING  (required)
  platform:     STRING — WhatsApp, Telegram…  (required)
```
**У нас:** `send_to_telegram` умеет писать только **самому себе** (свой чат) —
адресата выбрать нельзя. WhatsApp не покрыт.

### 1.17 `game_updater` — Steam/Epic
```
parameters:
  action:   STRING — update | install | list | download_status | schedule |
                     cancel_schedule | schedule_status
  platform: STRING — steam | epic | both
  game_name: STRING — частичное совпадение
  app_id:   STRING — Steam AppID
  hour:     INTEGER 0-23 (по умолчанию 3)
  minute:   INTEGER 0-59
  shutdown_when_done: BOOLEAN — выключить ПК по окончании загрузки
required: []
```
1101 строка. Ночное обновление игр с автовыключением.

### 1.18 `flight_finder` — авиабилеты
```
parameters:
  origin:      STRING  (required)
  destination: STRING  (required)
  date:        STRING — любой формат  (required)
  return_date: STRING
  passengers:  INTEGER — 1
  cabin:       STRING — economy | premium | business | first
  save:        BOOLEAN — сохранить в блокнот
```

### 1.19 `reminder` — OS-нативные напоминания
```
parameters:
  date:    STRING — YYYY-MM-DD  (required)
  time:    STRING — HH:MM 24ч   (required)
  message: STRING               (required)
```
Важно, чем это отличается от нашего: напоминание регистрируется в **планировщике
ОС** (Windows Task Scheduler / macOS LaunchAgent / Linux systemd) и сработает,
даже если ДЖАРВИС закрыт. Уведомление — plyer → win10toast → `msg` → beep,
каскадом.

**У нас:** `calendar.add_reminder` живёт в процессе; закрыли приложение —
напоминания нет.

### 1.20 `screen_process` / `close_camera` — детали, которых нет у нас
```
screen_process:
  angle: STRING — 'screen' | 'camera'
  text:  STRING — вопрос к изображению  (required)
close_camera: {} — закрыть живое окно камеры
```
Их находка: кадр прикрепляется к **тому же** обмену, что и результат
инструмента — один ответ вместо двух (раньше модель импровизировала до
прихода картинки, потом отвечала ещё раз). И каждое изображение помечается
источником, чтобы скриншот приложения не был принят за фото пользователя.

**У нас:** `look_at_screen` / `look_at_camera` с параметрами `prompt` и
`source` — отдельный конвейер `vision/`, живого окна камеры и её закрытия
голосом нет.

---

## 2. Подсистемы `core/`, которых у нас нет

| Модуль | Строк | Что делает | Есть ли аналог у нас |
|---|---|---|---|
| `action_loader.py` | 221 | Автообнаружение `actions/*.py` по `TOOL` dict + валидация + изоляция падений | ✅ Сделано: `core/action_loader.py`, 14 инструментов переехали, `main.py` короче на 430 строк |
| `plugin_loader.py` | 285 | То же для `plugins/` + UI-схемы настроек плагинов, вкл/выкл из HUD | Нет |
| `undo.py` | 121 | Общий стек отмены | Нет |
| `confirm.py` | 161 | Гейт необратимых действий через интерфейс | Нет |
| `audio_devices.py` | 421 | Список устройств: 41 запись → 8, один host API на направление, **измерение** работоспособности, хранение выбора **по имени** | ✅ Частично: `core/audio_devices.py` + окно выбора. Отбор, дедупликация, хранение по имени, откат с записью в лог — есть. **Измерения** устройств нет: оно требует стенда с железом |
| `echo.py` | 288 | Самокалибрующийся анти-эхо-гейт по полосным энергиям: вычитает собственный голос из микрофона, **не глуша** микрофон | У нас `core/aec_pipeline.py` (NLMS + подавление остатка) — по инженерии наш сильнее; их подход к «хвосту» динамика (окно по `device latency`) стоит подсмотреть |
| `hotkey.py` | 173 | Push-to-talk Ctrl+Space: опрос VK-кодов 30 раз/с на Windows, честный fallback в окно на mac/Linux | ✅ Сделано: `core/push_to_talk.py`. Опрос `GetAsyncKeyState` на Windows, события клавиш Qt как запасной путь, микрофонный гейт в `main.py`, переключатель в окне |
| `viseme.py` | 275 | Текст → виземы: Unicode-разложение к латинице + транслит кириллицы/греческого, ~20 правил артикуляции, fallback на аудио для арабского/CJK | Нет |
| `avatar.py` + `avatar_mesh.py` + `face_model.obj` | 1066 + 25 КБ | Голова из 468 вершин MediaPipe, череп/шея/риг генерируются на старте, рисуется QPainter'ом: без OpenGL, без GPU, без новых зависимостей. Лицо как индикатор состояния (отводит взгляд — думает, смотрит в глаза — слушает, веки опущены — спит) | Нет. У нас реактор-HUD |
| `wake_word.py` | 211 | openwakeword, установка в один клик из HUD, отдельный поток, оффлайн | У нас `wake_detector.py` (двухступенчатый) + `wakeword.py` — сопоставимо или лучше |
| `llm_client.py` | 586 | Абстракция над провайдером: Ollama/OpenAI-совместимый, прогрев модели, стриминг | У нас `tasks/provider-layer.md` — план есть, кода нет |
| `gemini.py` | 439 | Обёртка вызовов с тирами (FAST/…), таймаутами, cooldown-ом упавших моделей, `as_json()` | Разбросано по коду |
| `stt.py` + `tts.py` | 93 + 442 | Whisper/Vosk STT; Edge/Kokoro/ElevenLabs TTS с единым `TTSPlayer` и сжатием пауз | У нас Fish Audio + Gemini + edge-tts в боте — сопоставимо |
| `installer.py` | 138 | Доустановка пакетов под выбранные фичи из приложения | Нет |
| `dashboard/server.py` | 884 | Управление с телефона: FastAPI + HTTPS с самоподписанным сертификатом, QR-пейринг, AES-CBC на сессионном ключе, ретрансляция звука с телефона, загрузка файлов | У нас Telegram-бот + mini-app — функционально шире, но без «прямого» канала в обход интернета |

### Архитектурная разница, которая важнее любого отдельного инструмента

У них **инструмент = один файл**. Файл объявляет себя сам:
```python
TOOL = {"name": ..., "description": ..., "parameters": {...}, "handler": fn}
```
`action_loader.discover_actions()` находит его на старте, валидирует имя по
регулярке, ловит исключения обработчика, а `_describe_tools(declarations)`
генерирует из живого списка **строчку в системном промпте**: «вот что ты
умеешь». Плюс `_describe_limits(has_vision, has_mic)` — «вот чего ты НЕ
умеешь». Добавил файл — ассистент знает о новой способности; удалил — перестал
о ней заявлять.

У нас 24 инструмента объявлены списком `TOOLS` в `main.py` (строки 448-1050) и
диспетчеризуются цепочкой `if/elif` (строки 1349-1680). Каждый новый инструмент
— правка трёх мест в самом большом файле проекта.

---

## 3. Данные и конфигурация, которых у нас нет

| Что | Где у них | У нас |
|---|---|---|
| `{токены}` в промпте | `core/prompt.txt` подставляет имя ассистента, ОС, список инструментов, список ограничений на старте сессии | ✅ Сделано: `{tools}`, `{limits}`, `{os}`; блоки дописываются в конец, если места в шаблоне нет |
| Подхват `session_resumption` | Хендл читается из `response.session_resumption_update.new_handle` и возвращается при реконнекте; протухший хендл сбрасывается после одной неудачи | **У нас `session_resumption=SessionResumptionConfig()` включён, но хендл нигде не читается** — каждый реконнект начинает пустую сессию. Ровно тот баг, который они у себя нашли и починили |
| Память: бюджет + индекс | `format_memory_for_prompt` укладывается в бюджет, остальное — `[ALSO REMEMBERED]` + `search_memory(query, limit=8)` | Вся память в промпт целиком |
| `save_session_summary` / `pop_last_session` | Резюме беседы пишется при выходе и **потребляется** в утреннем брифинге («вчера мы говорили о…»), потом стирается | Нет |
| UI-панель памяти | Список всех фактов с датой и крестиком «забыть» | Нет |
| Настройки из `config_manager` | `input_device`, `output_device`, `hud_style`, `push_to_talk_enabled`, `thinking_enabled`, `turn_tuning`, `media_resolution`, `proactive_audio_enabled`, `brief_enabled`, `plugin_enabled/*`, `plugin_config/*` | У нас `api_keys.json` + `modes.json` + 3 файла предпочтений; выбора устройств и стиля HUD в конфиге нет |
| Пороги алертов железа | `SystemMonitor(thresholds)` | Нет |
| Чёрный список тем мониторинга | `_is_blocked` (крипта/трейдинг) | Нет |
| Зависимости с верхней границей | `PyQt6>=6.6,<7`, `numpy>=1.24,<3`, маркеры `sys_platform == "win32"` | У нас только нижние границы и никаких платформенных маркеров — мажорный релиз любой зависимости ломает свежий клон |

---

## 4. Что есть у нас и чего нет у них (чтобы не потерять при заимствованиях)

- Telegram-экосистема: `bot.py` (1726), `memory_store.py` (1287), `pc_server.py`,
  `miniapp_server.py`, RAG по памяти, проактивность, онбординг, напоминания.
- Spotify Web API (537 строк) против их «нажми play в браузере».
- Obsidian, календарь с Google Calendar, менеджер переводов (606),
  новостной агрегатор (471), lifestyle-режимы, movie_player, sleep_timer,
  переключение голоса Fish/Gemini.
- Аудио-инженерия: `aec_pipeline.py` (NLMS с оценкой задержки и детектором
  double-talk), `ducking_controller.py` (приглушение чужого звука через
  pycaw), `voice_trigger_engine.py`, двухступенчатый wake-детектор.
- `core/latency.py` — перцентили задержек по этапам, бенчмарки в CSV/JSON.
- Тесты: 40+ файлов, pytest, `test_resilience`, `test_silent_failures`,
  `test_latency`. **У Mark-LIV тестов нет ни одного.**
- Дистрибуция: PyInstaller + Inno Setup, Docker, Oracle/Render/HF деплой,
  Caddy, автозапуск, watchdog, headless-режим, трей.

Вывод: мы не «отстаём» — мы построили другое. Заимствовать нужно точечно, не
пытаясь догнать их по всем 24 инструментам.

---

## 5. Рекомендация: что брать, что нет

### Уровень A — СДЕЛАНО (это закрывало наши дефекты, а не «добавляло фич»)

Реализовано в этой же ветке; подробности каждого пункта — в разделах 1.1–1.3
выше. Тесты: `tests/test_confirm_gate.py`, `test_undo_stack.py`,
`test_file_undo.py`, `test_memory_budget.py`, `test_session_resumption.py`.


1. ✅ **Гейт подтверждения для необратимых действий.** У нас голосовая команда
   выключает компьютер без единого вопроса. Даже без красивого баннера:
   shutdown/restart должны возвращать «подтвердите на экране» и выполняться
   только по нажатию. ~1 день.
2. ✅ **Дочитать `session_resumption`.** У нас конфиг включён, хендл не читается —
   то есть мы платим за фичу и не получаем её. Это правка на 10 строк в
   `_receive_audio`: сохранить `new_handle`, передать в `LiveConnectConfig`
   при реконнекте, сбросить после одного отказа. ~2 часа, эффект огромный.
3. ✅ **Бюджет памяти + `recall_memory` + индекс `[ТАКЖЕ ПОМНЮ]`.** Сейчас
   наша память масштабируется в худшую сторону: чем дольше пользуешься, тем
   дороже каждый реконнект. ~2 дня.
4. ✅ **Стек undo.** Наш `files` умеет `delete`, `move`, `rename` без отката и без
   корзины. Минимум — `send2trash` вместо `os.remove`, максимум — их схема со
   стеком замыканий. ~2 дня.
5. ✅ **Верхние границы зависимостей и платформенные маркеры** в
   `requirements.txt`. ~1 час.

### Уровень B — брать, это заметно усилит то, что уже есть

6. ✅ **Нормальный `web_search` с режимами** (`search|news|research|price|compare`)
   на Gemini Grounded + DDG-фоллбэк. Наш DuckDuckGo Instant Answer — заглушка,
   а поиск ассистент использует чаще всего. ~2 дня.
7. ✅ **`action_loader` — самоописывающиеся инструменты.** Перевести `actions/*.py`
   на `TOOL` dict с автообнаружением и выкинуть `if/elif` из `main.py`. Это не
   косметика: после этого добавление инструмента = один файл, а список
   способностей в промпте генерируется из живого реестра. ~3 дня, окупается на
   каждом следующем инструменте.
8. ✅ **Токены в `core/prompt.txt`** (`{tools}`, `{limits}`, `{os}`, `{name}`) —
   после п.7 делается почти бесплатно и убирает расхождение «промпт обещает то,
   чего в коде нет».
9. ✅ **`system_status` как голосовой инструмент** + пороговые алерты. Код уже
   почти есть в `telegram_bot/pc_server.py`. ~1 день.
10. ✅ **OS-нативные напоминания** (Task Scheduler) в дополнение к нашему
    календарю — напоминание должно срабатывать при закрытом приложении. ~2 дня.
11. ✅ **Выбор микрофона и динамиков по имени в UI** с сохранением в конфиг.
    Наша эвристика по подстрокам («headset», «realtek») отлично работает, пока
    угадывает, и никак не лечится пользователем, когда не угадала. ~2 дня.
12. ✅ **Push-to-talk (Ctrl+Space)** как режим: микрофон физически закрыт, пока
    клавиша не зажата. Для шумной комнаты и созвонов это единственный рабочий
    режим. У нас уже есть `hotkey_manager` — доделать. ~1 день.

### Уровень C — брать по желанию, если есть спрос у пользователя

13. `file_processor` + drag-and-drop файлов в окно — крупная, полезная,
    самостоятельная фича (~1 неделя).
14. `browser_control` на Playwright (click/type/fill_form/smart_click). Мощно,
    но Selenium у нас уже есть — сначала стоит понять, будет ли это
    использоваться голосом (~1 неделя).
15. `youtube_video` с `summarize` по субтитрам — дёшево и эффектно (~2 дня).
16. Расширить `computer_settings` с 9 до ~40 действий + `difflib` на опечатки
    (~2 дня).
17. Плагинная система (`plugins/*.py` + UI вкл/выкл) — имеет смысл **после**
    п.7, как его продолжение.

### Уровень D — не брать

18. **Аватар с виземами** (`avatar.py` + `avatar_mesh.py` + `viseme.py` +
    `face_model.obj`, ~1340 строк). Инженерно это самое красивое, что есть в
    Mark-LIV, но: (а) это чужая визуальная подпись, копировать её — значит
    делать клон; (б) у нас своя идентичность HUD-реактора и своя персона Пола
    Беттани; (в) виземы рассчитаны на латиницу/кириллицу, узбекский и казахский
    придётся отлаживать самому; (г) ~1340 строк рендера на поддержке ради
    косметики. Если очень хочется «лица» — дешевле сделать выразительный
    реактор, чем чужую голову.
19. **`game_updater`** (Steam/Epic, 1101 строка) — нишевая фича под конкретного
    пользователя-геймера.
20. **`flight_finder`** — парсинг Google Flights ломается при каждом редизайне;
    поддержка дороже пользы. Наш `web_search` в режиме `price` закроет 80%
    запроса.
21. **`dev_agent`** (пишет проекты целиком) — дублирует то, для чего у
    пользователя уже есть Claude Code/Cursor. Голосом такое не управляют.
22. **`dashboard/server.py`** (телефон через QR + самоподписанный TLS) — у нас
    Telegram-бот и mini-app делают то же самое лучше и без возни с
    сертификатами.
23. **Копирование кода как есть.** CC BY-NC 4.0 против нашего MIT — любая
    дословно скопированная функция делает наш репозиторий смешанно
    лицензированным и некоммерческим.

### Порядок, если делать

```
Неделя 1:  A2 (resumption) → A1 (confirm) → A5 (requirements)   ✅ сделано
Неделя 2:  A3 (память: бюджет + recall) → A4 (undo + корзина)   ✅ сделано
Неделя 3:  B7 (action_loader) → B8 (токены промпта)             ✅ сделано
Неделя 4:  B6 (web_search) → B9 (system_status) → B12 (PTT)     ✅ сделано
Далее:     B10 ✅, B11 ✅ — уровень B закрыт целиком.
           Осталось C (по запросу): file_processor, browser_control,
           youtube_video, расширение computer_settings, плагины.
```

Уровень A — это не «взять фичи у Mark-LIV», это починить своё. Их код лишь
показал, где смотреть.
