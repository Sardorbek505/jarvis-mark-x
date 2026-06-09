# Аудит проекта JARVIS — план и результаты

## Найденные проблемы

### 🔴 Критичные (безопасность)
- [x] `config/api_keys.json` с реальными секретами закоммичен в git (есть в .gitignore, но уже в истории)
- [x] Захардкоженные Spotify CLIENT_ID/CLIENT_SECRET в `get_spotify_token.py`
- [x] Захардкоженные CLIENT_ID/CLIENT_SECRET + утёкший ACCESS_TOKEN в `get_refresh_from_access.py`

### 🟡 Мусор
- [x] 5 битых submodule-gitlinks без `.gitmodules`: claude-mem-new, claude-plugins-official, context-mode, openclaw, superpowers
- [x] Одноразовый скрипт `fix_ui.py` (захардкожен путь D:/jarvis-ru/...)
- [x] Одноразовый скрипт `fix_ui2.py` (захардкожен путь D:/jarvis-ru/...)

## Выполнено
- [x] Убрал `config/api_keys.json` из отслеживания git (`git rm --cached`), файл остаётся локально
- [x] Добавил `config/api_keys.example.json` как шаблон
- [x] Переписал `get_spotify_token.py` — читает креды из config/env, без хардкода
- [x] Переписал `get_refresh_from_access.py` — без хардкода, удалён утёкший access token
- [x] Удалил битые submodule-ссылки
- [x] Удалил `fix_ui.py`, `fix_ui2.py`

## Ревью
Все изменения минимальны и точечны. Секреты больше не добавляются в новые коммиты.
ВАЖНО: утёкшие ключи уже в истории git — пользователю нужно их ОТОЗВАТЬ/перевыпустить.
