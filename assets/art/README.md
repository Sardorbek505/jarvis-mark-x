# JARVIS Mark X — Графические ассеты (Assets Art)

Коллекция визуальных материалов для Windows-приложения голосового ассистента JARVIS (PyQt6),
установщика Inno Setup и мобильного Telegram Mini App.

---

## 1. Цветовая палитра и дизайн-код (JARVIS Design System)

| Роль | HEX | RGB | Назначение |
|---|---|---|---|
| **Фон (Background)** | `#030609` | `rgb(3, 6, 9)` | Базовый глубокий тёмный фон окон и баннеров |
| **Панели (Panels)** | `#070c11` | `rgb(7, 12, 17)` | Карточки, плашки, подложка скруглённой иконки |
| **Рамки (Borders)** | `#151e27` | `rgb(21, 30, 39)` | Тонкие 1px разделители, сетка HUD |
| **Главный акцент (Primary)** | `#3fd0bd` | `rgb(63, 208, 189)` | Бирюзовое свечение, шар из точек, активные элементы |
| **Приглушённый бирюзовый** | `#2a8a7e` | `rgb(42, 138, 126)` | Второстепенные кольца, пустые состояния (Empty states) |
| **Акцент: Говорит** | `#ff8a34` | `rgb(255, 138, 52)` | Голосовой ответ ассистента |
| **Акцент: Думает** | `#d9e25a` | `rgb(217, 226, 90)` | Генерация ответа ИИ / обработка |
| **Акцент: Слушает / Успех** | `#46e880` | `rgb(70, 232, 128)` | Запись голоса, чекмарки готовности |
| **Акцент: Ошибка / Офлайн** | `#ff4660` | `rgb(255, 70, 96)` | Нет связи, разрыв соединения |

### Главный символ
- **«Шар из точек» (Dot-Particle Sphere)**: сфера из ~200 светящихся точек с перспективой (ближние ярче и крупнее, дальние тусклые и мелкие), мягкое свечение без кислотного неона.
- **Эстетика**: Минималистичный премиальный HUD (Apple + Железный Человек), тонкие 1px векторные линии, обилие свободного пространства (negative space), линейные пиктограммы со скруглёнными концами (SF Symbols style).
- **Строгий запрет**: никакого запечённого текста и кириллических букв на иллюстрациях, никаких логотипов Marvel, лиц и людей.

### Базовый суффикс промпта (Common Prompt Tail):
```text
minimal dark UI illustration, near-black background #030609, teal accent #3fd0bd, glowing dot-particle sphere, thin 1px HUD lines, soft glow, lots of negative space, Apple-like clean aesthetic, no text, no letters, no logos, no people
```

---

## 2. Структура каталогов

```text
assets/art/
├── manifest.json              # Опись всех файлов с метаданными и промптами
├── README.md                  # Руководство по стилю и подключению
├── icon_preview.png           # Верификационный тест на тёмном и светлом фоне
│
├── icon/                      # Иконки приложения
│   ├── icon_1024.png          # 1024×1024 PNG (прозрачный фон снаружи скруглённого квадрата)
│   ├── icon_small.png         # 256×256 PNG (высокая читаемость, крупные точки)
│   ├── jarvis.ico             # Мульти-размерный Windows ICO (16, 24, 32, 48, 64, 128, 256 px)
│   ├── tray_32.png            # 32×32 PNG для системного трея Windows (только шар, прозрачный фон)
│   └── tray_16.png            # 16×16 PNG для трея при стандартном масштабировании
│
├── setup/                     # Иллюстрации мастера настройки (1200×720, герой справа)
│   ├── setup_welcome.png      # Шаг «Добро пожаловать» (шар и орбиты)
│   ├── setup_keys.png         # Шаг «Ключ Gemini / API» (светящийся ключ)
│   ├── setup_microphone.png   # Шаг «Микрофон и звук» (микрофон и звуковая волна)
│   ├── setup_voice.png        # Шаг «Голос и узнавание» (отпечаток-кольцо)
│   ├── setup_wake.png         # Шаг «Слово Джарвис» (акустический резонанс)
│   ├── setup_telegram.png     # Шаг «Telegram / телефон» (смартфон и сигналы)
│   ├── setup_about.png        # Шаг «Познакомиться» (профиль личности)
│   ├── setup_contacts.png     # Шаг «Контакты» (сеть адресной книги)
│   ├── setup_study.png        # Шаг «Учёба / расписание» (раскрытая книга)
│   ├── setup_spotify.png      # Шаг «Spotify / музыка» (нота и эквалайзер)
│   └── setup_done.png         # Шаг «Готово» (шар на полной яркости + зелёная галочка)
│
├── installer/                 # Графика установщика Inno Setup
│   ├── wizard_large.bmp       # 164×314, 24-bit BMP (левый вертикальный баннер)
│   └── wizard_small.bmp       # 55×58, 24-bit BMP (малая иконка заголовка)
│
├── empty/                     # Пустые состояния списков (480×320, прозрачный фон, тусклый бирюзовый)
│   ├── empty_commands.png     # Команды (>_ терминал) [+ алиас commands.png]
│   ├── empty_study.png        # Учёба (книга) [+ алиас study.png]
│   ├── empty_contacts.png     # Контакты (пользователь +) [+ алиас contacts.png]
│   ├── empty_calls.png        # Звонки (трубка) [+ алиас calls.png]
│   ├── empty_football.png     # Футбол (мяч из точек) [+ алиас football.png]
│   ├── empty_backup.png       # Бэкап / замок [+ алиас backup.png]
│   └── empty_help.png         # Справка (?) [+ алиас help.png]
│
├── football/                  # Модуль «Футбол»
│   └── hero_bg.png            # 1600×400 PNG (вид сверху на тёмное поле с бирюзовой разметкой)
│
└── miniapp/                   # Telegram Mini App & Web
    ├── splash.png             # 1080×1920 PNG (заставка приложения)
    └── og.png                 # 1200×630 PNG (OpenGraph мета-баннер)
```

---

## 3. Как пересобрать `jarvis.ico` через Pillow

Если потребуется внести изменения в мастер-иконку и заново экспортировать `.ico`:

```python
from PIL import Image

icon_1024 = Image.open("assets/art/icon/icon_1024.png")
icon_small = Image.open("assets/art/icon/icon_small.png")

sizes = [16, 24, 32, 48, 64, 128, 256]
layers = []

for s in sizes:
    # Для мелких размеров (16, 24) берём упрощённую иконку с крупными точками
    src = icon_small if s <= 24 else icon_1024
    layers.append(src.resize((s, s), Image.Resampling.LANCZOS))

# Сохраняем мульти-иконку
layers[-1].save(
    "assets/art/icon/jarvis.ico",
    format="ICO",
    sizes=[(s, s) for s in sizes],
    append_images=layers[:-1]
)
print("jarvis.ico успешно пересобран!")
```

---

## 4. Подключение в приложении

### В PyQt6:
```python
from PyQt6.QtGui import QIcon, QPixmap

# Иконка окна
app.setWindowIcon(QIcon("assets/art/icon/jarvis.ico"))

# Иконка в системном трее
tray_icon.setIcon(QIcon("assets/art/icon/tray_32.png"))

# Фоновая иллюстрация экрана мастера
setup_label.setPixmap(QPixmap("assets/art/setup/setup_welcome.png"))

# Пустое состояние
empty_label.setPixmap(QPixmap("assets/art/empty/empty_commands.png"))
```

### В Inno Setup (`scripts/installer.iss`):
```ini
[Setup]
SetupIconFile=..ssetsrt\icon\jarvis.ico
WizardImageFile=..ssetsrt\installer\wizard_large.bmp
WizardSmallImageFile=..ssetsrt\installer\wizard_small.bmp
```
