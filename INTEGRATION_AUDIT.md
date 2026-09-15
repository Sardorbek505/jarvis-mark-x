# JARVIS Mark X — Комплексный интеграционный аудит (Phase 2)

**Дата проведения аудита:** 5 сентября 2026 г.  
**Репозиторий:** `Sardorbek505/jarvis-mark-x`  
**Статус тестовой базы:** 414 тестов пройдено (baseline).  
**Цель аудита:** Полная инспекция актуального кода, обнаружение скрытых конфликтов, дублирующихся подсистем, блокировок real-time аудиопотока, отсутствия реального AEC, рассинхронизации состояний диалога и доведение архитектуры до надёжности голосового ассистента уровня Alice/Siri.

---

## 1. Current Architecture (Текущая архитектура)

Текущий репозиторий содержит гибрид двух архитектурных поколений:
1. **Новые изолированные модули в `core/`:**
   - `core/audio_capture.py` (`AudioCaptureEngine`): двухпоточный синхронный захват микрофона и WASAPI Loopback reference на 16 кГц int16.
   - `core/aec_pipeline.py` (`AECPipeline`): GCC-PHAT оценка задержки, блочный адаптивный NLMS фильтр, спектральная винеровская маска подавления остаточного эха.
   - `core/voice_trigger_engine.py` (`VoiceTriggerEngine`): оркестратор, предназначенный для связывания `AudioCaptureEngine` + `AECPipeline` + `WakeWordDetector2Stage` + `DuckingController`.
   - `core/wake_detector.py` (`WakeWordDetector2Stage`): гибридный KWS (Vosk русская грамматика + openWakeWord ONNX `hey_jarvis`).
   - `core/fast_command_router.py` (`FastCommandRouter`): детерминированный роутер быстрых медиа/системных команд.
   - `core/ducking_controller.py` (`DuckingController`): контроллер плавного приглушения фонового звука per-app через Windows CoreAudio/pycaw.
   - `core/media_session_manager.py` (`MediaSessionManager`): чтение метаданных треков Windows GSMTC (WinRT).
   - `core/routines_engine.py` (`RoutinesEngine`): композитные сценарии автоматизации.
   - `core/episodic_memory.py` (`EpisodicMemory`): локальный RAG над SQLite и `data.json`.
   - `core/latency.py` (`LatencyTracker`): замер таймингов голосового цикла.
   - `core/headless_ui.py` (`HeadlessUI`): консольный запуск без GUI для тестов и серверов.

2. **Монолитный рантайм `main.py` (2865 строк, 163 КБ):**
   - Напрямую управляет сессией Google GenAI Multimodal Live API (`client.aio.live.connect`).
   - Содержит собственный аудиоцикл захвата через `sounddevice.InputStream` (`_listen_audio`).
   - Содержит цикл воспроизведения через `sounddevice.RawOutputStream` (`_play_audio`).
   - Содержит прямой вызов `WakeWordDetector2Stage.process_pcm` внутри аудиоколбэка.
   - Содержит синтез речи через Fish Audio (`_speak_fish`) с откатом на Edge-TTS.
   - Управляет GUI через PyQt-виджеты `ui.py` (Arc Reactor HUD).

---

## 2. Actual Call Graph (Фактический граф вызовов)

Фактический поток данных и управления в текущем production runtime:

```
[Пользователь / Окружение]
      │
      ▼ (Звук в комнате)
[Микрофон] ───> sounddevice.InputStream (callback в C-потоке PortAudio)
      │
      ├──> [БЛОКИРОВКА В АУДИОКОЛБЭКЕ]
      │      ├──> _check_barge_in() -> WakeWordDetector2Stage.process_pcm()
      │      │      ├──> Vosk KaldiRecognizer.AcceptWaveform() (тяжёлый C-декодер)
      │      │      └──> openWakeWord ONNX inference (CPU)
      │      ├──> (если не говорит): повторный process_pcm() на том же кадре!
      │      ├──> (если spotterless): FastCommandRouter.match_and_execute()
      │      │      └──> Windows API / COM / movie_player (time.sleep() до 5 сек!)
      │      ├──> ducking_controller.duck() (COM GetAllSessions в аудиопотоке!)
      │      └──> _push_level() (UI вызовы)
      │
      ▼ (Кадры PCM int16)
[main.out_queue] (asyncio.Queue)
      │
      ▼ (_send_realtime)
[Gemini Multimodal Live API (WebSocket)]
      │
      ▼ (_receive_audio)
[Gemini ServerContent]
      ├── response.data (Audio 24kHz)
      │     └── (ЕСЛИ JARVIS_VOICE=fish): ТИХО ВЫБРАСЫВАЕТСЯ В МУСОР!
      │     └── (ЕСЛИ JARVIS_VOICE=gemini): audio_in_queue -> sd.RawOutputStream
      │
      ├── response.server_content.model_turn (Text)
      │     └── turn_complete:
      │           ├── FastCommandRouter.match_and_execute() (ВТОРОЙ раз, постфактум!)
      │           ├── _wake_active_until = now + 4.0s (ТАЙМЕР СТАРТУЕТ ДО НАЧАЛА РЕЧИ!)
      │           └── (ЕСЛИ JARVIS_VOICE=fish): _start_speech()
      │                 ├── HTTP POST к api.fish.audio (сеть, 1.2-3.0 сек)
      │                 └── audio_in_queue -> sd.RawOutputStream
      │
      └── response.tool_call
            └── _execute_tool() -> Tool Dispatcher -> return types.FunctionResponse
```

---

## 3. Duplicate / Conflicting Pipelines (Таблица дублирующихся подсистем)

| Подсистема | Текущий владелец 1 | Текущий владелец 2 | В чём дублирование и конфликт | Риск | Предлагаемый единый владелец |
|---|---|---|---|---|---|
| **Audio Capture** | `AudioCaptureEngine` (`core/audio_capture.py`) | `sd.InputStream` (`main.py:2251`) | Два независимых захвата микрофона; `AudioCaptureEngine` умеет loopback reference, но `main.py` его не использует. | Двойное открытие аудиоустройств, потеря loopback PCM для эхоподавления. | `core/audio_capture.py` (интегрированный в единый аудио-пайплайн). |
| **Acoustic Echo Cancellation (AEC)** | `AECPipeline` (`core/aec_pipeline.py`) | `SpeakerMeter` (`speaker_meter.py`) | `AECPipeline` написан, но исключён из `main.py`. `main.py` пользуется только скалярным пик-детектором `SpeakerMeter`. | Полное отсутствие вычитания звука динамиков из микрофона; ложные перебивания и галлюцинации LLM от музыки. | Единый `AECPipeline`, получающий reference PCM из `AudioCaptureEngine`. |
| **Wake Word Detection** | `WakeWordDetector` (`core/wakeword.py`) | `WakeWordDetector2Stage` (`main.py:1245`) | Класс `WakeWordDetector` обёртывает `VoiceTriggerEngine`, а `main.py` напрямую создаёт `WakeWordDetector2Stage`. | Рассинхронизация настроек KWS, дублирование логики запуска. | `WakeWordDetector2Stage` через единый фоновый audio worker. |
| **Fast Path Execution** | `core/wake_detector.py` (через `on_quick_command`) | `main.py:2452` (после Gemini Live) | Быстрая команда исполняется локально из Vosk, но `out_queue` не чистится, и та же команда уходит в Gemini, где повторно матчится через 1.5 сек. | Двойное выполнение действия (например, двойной "next track" или двойное изменение громкости). | Единый перехват в аудио-роутере ДО отправки в облачную очередь `out_queue`. |
| **Audio Ducking** | `DuckingController` (`core/ducking_controller.py`) | Прямые вызовы `ducking_controller.duck()` в 6 местах `main.py` | Вызовы дакинга раскиданы по аудиоколбэку, хоткеям, приёму ответов, быстрым командам без единой state machine. | Застревание громкости на -18 dB, гонки фейдеров, COM-вызовы в realtime-потоке. | Централизованный вызов по переходам состояний `ConversationStateMachine`. |
| **TTS / Playback State** | `_is_speaking` + `_active_synth_tasks` + `_speaking_lock` в `main.py` | `DuckingState` в `DuckingController` | Независимые флаги занятости речи; окончание хода в `main.py` считается до завершения фактического воспроизведения Fish. | Преждевременный сброс Follow-up окна и восстановление громкости музыки поверх речи. | `ConversationStateMachine.set_state(SPEAKING / FOLLOW_UP / IDLE)`. |

---

## 4. Confirmed Bugs (Подтверждённые критические баги)

### Bug 1: Глухота при отключении/смене микрофона (`main.py:2263-2268`) — P0
- **Код:**
  ```python
  try:
      with sd.InputStream(...):
          while True: await asyncio.sleep(0.1)
  except Exception as e:
      logger.error(f"Microphone error: {e}")
      await asyncio.sleep(1)
  ```
- **Проблема:** Комментарий гласит `# Don't raise - let the task be recreated`, но корутина `_listen_audio` завершается и выходит из контекста. Таска `tg.create_task(self._listen_audio())` умирает навсегда. JARVIS становится перманентно глухим без перезапуска.

### Bug 2: Блокировка аудио-потока PortAudio тяжёлыми вычислениями (`main.py:2144-2220`) — P0
- **Код:** Прямо в `callback(indata, frames, time_info, status)` вызываются:
  - `_check_barge_in` -> `WakeWordDetector2Stage.process_pcm` (Vosk Kaldi Lattice + openWakeWord ONNX);
  - второй `process_pcm` на том же кадре;
  - `FastCommandRouter.match_and_execute` (системные Win32/COM вызовы и `movie_player` с `sleep(2.0)`);
  - `ducking_controller.duck()` (`GetAllSessions()` через Windows COM);
  - UI вызовы `_push_level`.
- **Проблема:** PortAudio требует возврата из callback за несколько миллисекунд (< 10-20 мс). Задержки вызывают buffer overflow, щелчки, пропуск кадров и зависание звукового стека.

### Bug 3: Двойной вызов `process_pcm` на одном и том же кадре (`main.py:1506` и `2179`) — P0
- **Код:** Если Джарвис говорит, кадр микрофона сначала попадает в `_check_barge_in`, где зовётся `self._wake_detector.process_pcm(pcm_bytes)`. Если детекция сработала, флаг `jarvis_speaking` сбрасывается в `False`, управление идёт дальше, и на строке 2179 вызывается `self._wake_detector.process_pcm(pcm_bytes)` вторично!
- **Проблема:** В `_ring_buffer` дважды дублируются одни и те же байты, Vosk получает двойной сигнал, счетчики и кулдауны сбиваются.

### Bug 4: Обрезание первого слова / потеря Pre-roll в Follow-up и Hotkey режимах (`main.py:2233`) — P0
- **Код:**
  ```python
  loud = self._is_loud_enough(indata)  # Если громко, сбрасывает self._quiet_frames = 0
  ...
  if wake_spotted or getattr(self, "_quiet_frames", MIC_HANGOVER_FRAMES + 1) > MIC_HANGOVER_FRAMES:
      while preroll:
          loop.call_soon_threadsafe(_put_nowait_safe, preroll.popleft())
  ```
- **Проблема:** На строке 2174 `_is_loud_enough` УЖЕ установил `_quiet_frames = 0`. Поэтому выражение `_quiet_frames > MIC_HANGOVER_FRAMES` гарантированно ЛОЖНО (`0 > 13 == False`). Если не было произнесено слово «Джарвис» (например, в Follow-up диалоге или при нажатии F8), `preroll` НИКОГДА НЕ СБРАСЫВАЕТСЯ в начале речи! Пользователь говорит «Сделай тише», но первые 640 мс фразы выбрасываются.

### Bug 5: Преждевременная смерть Follow-up окна при использовании Fish TTS (`main.py:2527`) — P0
- **Код:**
  ```python
  if get_voice_provider() == "fish":
      self._start_speech(full_out)  # Асинхронный синтез и воспроизведение
  self._wake_active_until = time.monotonic() + 4.0  # Таймер Follow-up стартует СРАЗУ
  ```
- **Проблема:** Синтез Fish Audio занимает ~1.5 секунды, само воспроизведение фразы занимает ~3-5 секунд. Таймер `_wake_active_until` на 4 секунды истекает В ТО ВРЕМЯ, ПОКА ДЖАРВИС ЕЩЁ ГОВОРИТ. Когда Джарвис замолкает, окно активности уже равно нулю, музыка мгновенно восстанавливается, и сказать «Следующий» без повторения «Джарвис» невозможно.

### Bug 6: Fast Path декларирует успех при фактических ошибках — P0
- **Код:** В `core/fast_command_router.py`:
  - `_send_media_key("playpause")` возвращает `bool`, но его результат игнорируется, и роутер возвращает `FastCommandResult(True, "Поставил на паузу, сэр.", is_action=True)`.
  - `computer_settings(...)` возвращает строку ошибки при сбое, но результат игнорируется.
  - `movie_player({"action": "fullscreen"})` при отсутствии видеоокна жмёт клавишу 'F' в случайное приложение (например, IDE) и рапортует «Развернул на полный экран, сэр».
- **Проблема:** Нарушение принципа достоверности: ассистент говорит «Готово», когда системное действие реально не выполнено.

### Bug 7: Ложные срабатывания Spotterless в Standby режиме — P1
- **Код:** В `core/wake_detector.py` словарь Vosk grammar содержит всего ~25 слов (`"пауза"`, `"стоп"`, `"громче"`, `"следующий"`...). Любая русская речь («Дальше поехали», «Останови машину», фильм в комнате) декодируется в эти слова, и при `enable_spotterless = True` мгновенно выполняет системную команду без обращения к Джарвису.

### Bug 8: Ошибка многопоточности / блокировка SQLite в `EpisodicMemory` (`core/episodic_memory.py:88`) — P1
- **Код:** `sqlite3.connect(str(_DB_PATH))` открывается без `timeout` и без `PRAGMA journal_mode=WAL;`.
- **Проблема:** При параллельном обращении фонового процесса Telegram-бота и основного рантайма возникает `sqlite3.OperationalError: database is locked`. Кроме того, `save_fact` не производит дедупликацию, забивая БД дубликатами.

### Bug 9: Логическая дыра в RoutinesEngine (`core/routines_engine.py:211`) — P1
- **Код:** При сбое шагов макроса в `_execute_custom` исключения ловятся в `logger.error`, но итоговая реплика всегда возвращает: `f"Макрос «{name}» успешно выполнен, сэр."`.

---

## 5. False Assumptions in Comments (Неподтверждённые утверждения в комментариях)

1. **`core/wake_detector.py:7-8`:**  
   *«Работает полностью локально на CPU (8 мс на кадр, <10% одного ядра), без задержек и с нулевой вероятностью пропуска.»*  
   **Факты:** Утверждение ложно. «Нулевой вероятности пропуска» в распознавании речи не существует математически. Vosk на тихой речи, фоновой музыке или быстром смазанном произношении пропускает триггер, а на похожих словах («Дарвис», «Барвиха») даёт ложные срабатывания.
2. **`core/wake_detector.py:95-97`:**  
   *«1. Точность 99%+ на «Джарвис» без ложных срабатываний. 3. Скорость отклика < 5 мс»*  
   **Факты:** Бенчмарк `scripts/benchmark_wake.py` тестировал только openWakeWord на синтетическом английском голосе pyttsx3. Vosk KWS grammar для русского языка на таких датасетах не замерялся.
3. **`main.py:2266`:**  
   *«# Don't raise - let the task be recreated»*  
   **Факты:** Таска никем не пересоздаётся. После `await asyncio.sleep(1)` корутина завершается, оставляя приложение без микрофона.
4. **`main.py:1236-1242`:**  
   *«AEC здесь СОЗНАТЕЛЬНО не поднимается... speaker_meter.py отдаёт только скалярный уровень, не PCM. См. core/audio_capture.py.»*  
   **Факты:** В то же время `core/audio_capture.py` уже умеет захватывать loopback PCM через WASAPI, но мост между ним и `main.py` не был построен.

---

## 6. Existing Components that can be Reused (Компоненты для повторного использования)

1. **`core/audio_capture.py` (`AudioCaptureEngine`):**
   - Отличная реализация кольцевого буфера опорного сигнала `_ref_window` со временными метками `time.perf_counter()`.
   - Готова для использования в качестве единого источника микрофона и колонок.
2. **`core/aec_pipeline.py` (`AECPipeline`):**
   - Матричный блочный NLMS + GCC-PHAT выравнивание задержки + спектральное подавление.
   - Полностью готов к боевому включению в тракт.
3. **`core/ducking_controller.py` (`DuckingController`):**
   - Корректно сохраняет и восстанавливает индивидуальные уровни сессий (`_saved_session_vols`, `_finish_restore_sessions`).
   - Изолированный рабочий поток интерполяции по косинусной S-кривой.
4. **`core/media_session_manager.py` (`MediaSessionManager`):**
   - Надежное WinRT-чтение GSMTC для ответа на вопрос «Что сейчас играет?».
5. **`core/latency.py` (`LatencyTracker`):**
   - Готовая легковесная телеметрия (percentile 50/90, worst).
6. **`core/headless_ui.py` (`HeadlessUI`):**
   - Превосходный интерфейсный мок для автоматизированного сквозного тестирования.

---

## 7. Missing Runtime Pieces (Недостающие элементы рантайма)

1. **`core/audio_pipeline.py` (Аудио-воркер с очередью ограниченного размера):**
   - Архитектурная связка:
     `AudioCapture (PortAudio callback)` -> `bounded ring buffer / queue` -> `Worker Thread (AEC + VAD + KWS)` -> `Runtime Queue (Gemini Live)`.
   - Callback микрофона делает ТОЛЬКО `queue.put_nowait` с фиксацией `drop_count` при переполнении. Никаких замков, никакой обработки речи внутри прерывания звуковой карты.
2. **`core/conversation_state.py` (Детерминированная машина состояний диалога):**
   - Состояния: `STANDBY`, `LISTENING`, `THINKING`, `EXECUTING`, `SPEAKING`, `FOLLOW_UP`, `INTERRUPTED`, `RECONNECTING`, `ERROR`, `MUTED`.
   - Детерминированные переходы с событиями `on_state_changed`.
3. **Корректный Follow-Up контроллер:**
   - Таймер Follow-up стартует СТРОГО в момент `SPEAKING -> FOLLOW_UP` (когда звук физически прекратил звучать из динамиков).
   - Сброс таймера при обнаружении начала речи пользователя.
   - Безопасный переход `FOLLOW_UP -> STANDBY` по таймауту с плавным восстановлением музыки (`RESTORING -> IDLE`).
4. **Безопасный Spotterless Guard:**
   - Быстрые команды без wake-word разрешены ТОЛЬКО в состояниях `FOLLOW_UP` или во время воспроизведения речи/музыки для экстренной паузы (`"стоп"`, `"пауза"`). В состоянии `STANDBY` случайные фразы блокируются.
5. **Контекстный резолвер окружения ПК (`core/context_resolver.py`):**
   - Объединение `detect_active_window()`, заголовка активного окна, буфера обмена (clipboard) и текущего медиа-трека для разрешения фраз «Что здесь?» и «Открой это».

---

## 8. Priority Matrix (Матрица приоритетов)

### P0 (Критическая стабильность и ядро UX):
1. Устранить блокировки и вычисления внутри realtime аудиоколбэка (внедрить bounded worker thread).
2. Исправить баг с вечной глухотой при смене/ошибке аудиоустройства в `main.py`.
3. Устранить двойной вызов `process_pcm` на одном кадре.
4. Исправить логический баг сброса `preroll` (ликвидировать срезание начала фраз).
5. Разделить Fast Path команды на `LOCAL_SAFE`, `LOCAL_CONTEXT_DEPENDENT`, `LLM_REQUIRED` и гарантировать честный статус выполнения (без ложного «Готово»).
6. Исправить Follow-up таймер: привязать его к физическому окончанию речи Джарвиса.
7. Реализовать мгновенный Barge-In с реальной отменой воспроизведения и очисткой очередей.

### P1 (Надёжность диалога и системные связи):
1. Включить реальный AEC в production path (соединить loopback PCM из `AudioCaptureEngine` с `AECPipeline`).
2. Ввести централизованный `ConversationStateMachine` и синхронизировать с ним UI и `DuckingController`.
3. Ограничить Spotterless: запретить ложные срабатывания в режиме `STANDBY`.
4. Включить WAL-режим, таймауты и дедупликацию в `EpisodicMemory`.
5. Обеспечить честные статусы выполнения макросов в `RoutinesEngine` (`SUCCESS`, `PARTIAL_SUCCESS`, `FAILED`).
6. Ограничить Gemini reconnect: экспоненциальный backoff с jitter и лимитом попыток.

### P2 (Контекст и качество восприятия):
1. Задействовать `detect_active_window` и буфер обмена для понимания запросов «Что здесь?», «Что на экране?».
2. Разгрузить гигантский системный промпт: отделить постоянные инструкции от динамического контекста.
3. Добавить замер реальных метрик KWS на русском языке без неподтвержденных заявлений.

### P3 (Косметика и код-стайл):
1. Устранить 30 предупреждений `ruff` (неиспользуемые импорты, переменные, пустые f-строки).
2. Удалить устаревшие комментарии, вводящие в заблуждение.

---

## 9. Exact Proposed Changes (Точный план изменений по файлам)

1. **`core/conversation_state.py` [NEW]:**
   - Класс `ConversationStateMachine` с состояниями: `STANDBY`, `LISTENING`, `THINKING`, `EXECUTING`, `SPEAKING`, `FOLLOW_UP`, `INTERRUPTED`, `RECONNECTING`, `ERROR`, `MUTED`.
   - Потокобезопасные переходы, подписка слушателей (`on_state_change`), управление таймаутом `FOLLOW_UP`.

2. **`core/audio_capture.py` [MODIFY]:**
   - Добавить отказоустойчивый перезапуск потоков при ошибках (`reopen_stream()`).
   - Убедиться, что loopback reference возвращает гарантированный PCM при любых условиях.

3. **`core/voice_trigger_engine.py` / `core/audio_pipeline.py` [MODIFY/ENHANCE]:**
   - Вынести обработку кадров из callback в выделенный рабочий поток (Worker Thread) через `queue.Queue(maxsize=100)`.
   - В callback оставить только `put_nowait` и инкремент `drop_count`.
   - В worker потоке: AEC -> VAD -> Wake Word -> Fast Path Spotterless Guard -> Gemini queue.

4. **`core/fast_command_router.py` [MODIFY]:**
   - Ввести статусы `CommandExecutionStatus` (`SUCCESS`, `FAILED`, `UNAVAILABLE`, `NOT_APPLICABLE`).
   - Разделить команды на `LOCAL_SAFE` (громкость, медиаклавиши), `LOCAL_CONTEXT_DEPENDENT` (fullscreen, seek, now playing) и `LLM_REQUIRED` (vision, composite routines).
   - Проверять фактический результат: если медиа-клавиша не отправилась или окно видео не найдено — возвращать `FAILED` / `UNAVAILABLE`.

5. **`core/wake_detector.py` [MODIFY]:**
   - Удалить неподтверждённые фразы («нулевая вероятность пропуска», «99%+ точность»).
   - Добавить проверку текущего состояния диалога перед срабатыванием `on_quick_command` (spotterless активен только в `FOLLOW_UP` или при активном воспроизведении для команд стоп/тише).

6. **`core/routines_engine.py` [MODIFY]:**
   - Сделать подсчёт статусов каждого шага в `_execute_custom` и предопределённых рутинах.
   - Если шаги упали — возвращать честный отчёт: «Частично выполнено» или «Ошибка выполнения».

7. **`core/episodic_memory.py` [MODIFY]:**
   - Добавить `PRAGMA journal_mode=WAL;` и `PRAGMA busy_timeout=5000;`.
   - Добавить проверку на дубликаты перед сохранением факта.

8. **`main.py` [MODIFY]:**
   - Обернуть `_listen_audio` в восстанавливающийся цикл с повторным открытием устройства при исключениях.
   - Подключить `ConversationStateMachine` как единый источник правды для UI, дакинга и шлюза.
   - Исправить логику сброса `preroll` (проверять факт смены состояния silence -> speech, а не обнулённый счетчик).
   - Привязать запуск таймера `FOLLOW_UP` к фактическому завершению воспроизведения речи.
   - Задействовать единый поток чистого аудио с AEC.
   - Очищать `out_queue` при выполнении быстрой команды, чтобы предотвратить двойное исполнение.

9. **`tests/test_integration_scenarios.py` [NEW]:**
   - Детерминированные сценарии A-J (Follow-up, Barge-In, Spotterless Guard, Recovery, Truthful Tools).

---

## 10. Test Plan (План верификации)

1. **Unit & Component Tests:**
   - Тесты переходов `ConversationStateMachine`.
   - Тесты `FastCommandRouter` на обработку сбоев (`FAILED`, `UNAVAILABLE`) и отсутствие ложных успехов.
   - Тесты отсутствия блокировок в аудиоколбэке (замер времени выполнения callback < 5 мс).
   - Тесты сохранения и дедупликации в `EpisodicMemory` при конкурентных запросах.
   - Тесты частичного выполнения сценариев в `RoutinesEngine`.

2. **Integration & Resilience Tests:**
   - Эмуляция отключения микрофона в `_listen_audio` и проверка автоматического восстановления без падения процесса.
   - Проверка Pre-roll: проверка того, что первые 640 мс аудио попадают в очередь при старте фразы в Follow-up режиме.
   - Проверка Follow-up: эмуляция ответа Джарвиса длиной 5 сек и подтверждение, что Follow-up окно открывается ПОСЛЕ окончания звука.
   - Проверка Barge-In: прерывание 5-секундного воспроизведения через 2 секунды с замером задержки остановки воспроизведения.

3. **Code Quality & Regressions:**
   - `python -m ruff check` — 0 ошибок.
   - `python -m pytest` — 100% прохождение всех существующих (414+) и новых тестов.

---

## 11. Что сделано по этому аудиту (сентябрь 2026)

Закрыто:

* **P0 — блокировки в realtime-колбэке.** `push_frame` больше не делает переходов
  машины состояний и не трогает COM: снимает состояние, флаг шлюза и опорный кадр,
  кладёт в bounded-очередь. Вся обработка — в воркере (`core/audio_pipeline.py`).
* **P0 — вечная глухота при сбое микрофона.** `_listen_audio` переоткрывает поток
  в цикле с экспоненциальным backoff вместо выхода из корутины.
* **P0 — двойной `process_pcm` и потеря pre-roll.** Кадр обрабатывается один раз;
  предбуфер сливается по факту начала речи, а не по обнулённому счётчику тишины.
* **P0 — маршрутизация кадра по состоянию на момент ЗАХВАТА.** Речь, попавшая в
  очередь до начала ответа, больше не выбрасывается как эхо.
* **P0 — Follow-up.** Окно открывается по фактическому завершению воспроизведения;
  таймер ставится только после успешного перехода.
* **P0 — залипание в LISTENING.** Связь шлюза с машиной состояний сделана
  двусторонней: при закрытии окна активности машина возвращается в STANDBY.
  До этого она уходила в LISTENING навсегда — ключевое слово переставало
  требоваться, и весь микрофон непрерывно уезжал в облако.
* **P1 — реальный AEC.** Поднимается WASAPI loopback (`AudioCaptureEngine.start(reference_only=True)`),
  опорный кадр отдаётся конвейеру. Если loopback недоступен, это пишется в лог,
  а barge-in по RMS отключается — без опорного сигнала он ложно срабатывает
  на собственные динамики.
* **P1 — единая машина состояний** как источник правды для UI и дакинга.
* **P1 — Spotterless guard** по состоянию (в STANDBY заблокирован).
* **P1 — EpisodicMemory:** WAL, busy_timeout, дедупликация и закрытие соединения
  при любом исходе (раньше `conn.close()` пропускался на любой ошибке).
* **P1 — RoutinesEngine:** честные статусы SUCCESS / PARTIAL_SUCCESS / FAILED.
* **P1 — переподключение к Gemini:** экспоненциальная пауза с джиттером и
  потолком 60 с (`max_retries`/`max_retry_delay` до этого были объявлены и не
  использовались).
* **Дублирующие подсистемы сведены к одному владельцу.** `VoiceTriggerEngine`
  удалён, `WakeWordDetector` лишён собственного захвата микрофона: единственный
  владелец тракта — `core/audio_pipeline.py`.
* **Потокобезопасность.** `out_queue` и отмена воспроизведения больше не
  трогаются напрямую из аудиоворкера — работа переносится в цикл событий.

Осталось (не делалось намеренно):

* **P2 — разгрузка системного промпта.** `core/prompt.txt` — 48 КБ (~12k токенов)
  на каждое подключение. Разделение статической части и динамического контекста
  меняет поведение персоны, поэтому требует отдельного решения владельца.
* **P2 — контекстный резолвер окружения** (`detect_active_window` + буфер обмена)
  для фраз «что здесь?» / «открой это» — не реализован.
* **P2 — замер метрик KWS на русском.** Бенчмарк по-прежнему покрывает только
  openWakeWord на синтетическом английском; заявления о точности из комментариев
  убраны, но реальных цифр для русского нет.
