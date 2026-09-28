# JARVIS — промо-ролик (v2)

Вертикальный ролик 1080×1920, 30 fps, 27,2 сек, со звуком: **`jarvis_promo.mp4`**.

От референса (Moolah) взят только **стиль**: палитра, жирный гротеск, жёсткие
смены фона, устройство в 3D с «выпрыгивающими» элементами, плашки. Идея и всё
содержимое — от настоящего продукта: **десктопный JARVIS для Windows**.

**Ничего не нарисовано вручную.** Всё, что на экране ноутбука, — живые кадры
настоящего `ui.JarvisUI` (шар, карточки, капсула, страницы), снятые скриптом
`capture/capture.py`. Крупные планы карточек и страниц — вырезки из тех же
живых кадров, поэтому они тоже двигаются.

## Сцены и откуда они в коде
| Время | Что в кадре | Источник |
|---|---|---|
| 0.0 | «Просто скажи «Джарвис»» | wake-word, `core/help.py`, README |
| 1.3 | Шар из точек просыпается: ждёт → слушает, капсула «Слушаю…» | `orb.py`, `ui.HudCanvas`, `ui_island.py` |
| 3.4 | «Какая погода в Ташкенте?» → шар-глобус + карточка погоды | инструмент `weather`, `hud_cards._weather` |
| 6.0 | «Говоришь — делает.»: музыка, громкость, экран, перевод, Telegram, память, таймер сна, открыть приложение, поиск | `music_player`, `computer_control`, `look_at_screen`, `translation`, `send_to_telegram`, `save_to_memory`, `sleep_timer`, `open_app`, `web_search` + `core/result_card.py` |
| 10.95 | «Всё в одном окне»: экскурсия по панели, Диалог, Учёба (вкладки), Свои команды (рецепт), Футбол (живой счёт), Контакты | `ui.NavRail`, `ui.LogWidget`, `ui_study.py`, `ui_macros.py`, `ui_football.py`, `ui_contacts.py` |
| 17.05 | «Свернул окно? Я рядом.»: капсула — слушает, отвечает, что играет, счёт матча | `ui_island.py` |
| 20.55 | 20 навыков плашками → «38 навыков» | 38 инструментов в `main.py` |
| 23.55 | Иконка, «Голосовой ИИ-ассистент для Windows», бесплатно / свой ключ Gemini / установка в 1 клик | `assets/art/icon`, README (BYOK, установщик) |

## Демо-данные (мок) для рекламы
Чтобы экраны не были пустыми, `capture.py` заполняет их через настоящие модули
(в копию проекта, не в репозиторий):
- **Учёба** — 11 пар на неделю, 6 задач с дедлайнами (`core/study.py`);
- **Контакты** — 6 человек с прозвищами (`core/contacts.py`);
- **Свои команды** — «Рабочий режим» (по будням в 9:00), «Режим стрима», «Созвон», «Спокойной ночи» (`core/macros.py`);
- **Обо мне** — все 16 ответов (`core/about_me.py`);
- **Ключи** — все 7 сервисов «Работает» (подменены `core.keys.load_values` и проверки, ключи не настоящие);
- **Футбол** — «Реал Мадрид — Барселона 2:1, 67'», 4 результата, 3 ближайших матча, новости
  (подменён `Football.overview`, ESPN недоступен);
- **Погода** — Ташкент +24° и прогноз на 3 дня (`actions.weather.last_forecast`);
- **Шапка и диалог** — 128 выполненных команд, 03:42 в сети, история разговора.

## Пересобрать
```bash
pip install PyQt6 numpy psutil Pillow feedparser requests rapidfuzz cryptography qrcode tzdata mss google-genai imageio-ffmpeg
npm i -g playwright
# 1) снять живой UI (на КОПИИ проекта: туда пишутся демо-данные)
cp -r . /tmp/jx && (cd /tmp/jx && python3 promo/capture/capture.py /tmp/cap)
# 2) разложить кадры и карточки в promo/build/ (не в git)
python3 promo/capture/prepare.py /tmp/cap
# 3) звук и видео
python3 promo/make_soundtrack.py
NODE_PATH=$(npm root -g) node promo/render.cjs            # → promo/jarvis_promo.mp4
NODE_PATH=$(npm root -g) node promo/render.cjs --stills 4,8  # отдельные кадры
```
На Linux для вида как в Windows шрифты Segoe UI / Consolas подменяются на
Noto Sans / Cascadia Mono через fontconfig. Музыка сгенерирована кодом.
