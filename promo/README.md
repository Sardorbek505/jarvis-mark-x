# Промо-ролик Джарвиса (Reels 9:16)

Каждая функция из `core/help.py` показана по схеме «проблема → решение → настоящий экран».
Ничего не нарисовано «под Джарвиса»: все кадры — живой PyQt-интерфейс. Шар, карточки
результата, капсула, экраны и интро «два хлопка» сняты самим кодом программы.
Звуковые эффекты — родные функции `core/intro.py` и `core/sounds.py`.

## Конвейер

```
voice.py   → build/vo/*.wav, durations.json   реплики Джарвиса (Fish Audio, его reference_id)
capture/   → build/clips/*                     съёмка окна/капсулы/интро под длины реплик
build.py   → build/hf/index.html               HyperFrames-композиция 1080×1920, 30 fps
           --render → build/video_silent.mp4   рендер (npx hyperframes)
mix.py     → build/jarvis_reels.mp4            голос + интро + подложка + эффекты, −14 LUFS
```

## Финал с голосом Fish

Нужны ключ Fish (окно «Ключи» в Джарвисе или `FISH_API_KEY`), Node 22+, ffmpeg и, для
съёмки, Linux/WSL (Qt offscreen) с `pip install PyQt6 numpy pillow psutil`.

```bash
python promo/voice.py                                   # Fish: тот же голос, что в программе
cp -r . /tmp/jv && JV_ROOT=/tmp/jv python promo/capture/seed.py     # демо-данные — в КОПИИ
export JV_ROOT=/tmp/jv QT_QPA_PLATFORM=offscreen
python promo/capture/capture.py intro
QT_SCALE_FACTOR=2 python promo/capture/capture.py island
QT_SCALE_FACTOR=2 JARVIS_ISLAND=0 python promo/capture/capture.py hud
python promo/audio.py --seconds 200
python promo/build.py --render
```

Длины сцен считаются от реплик (`timeline.py`), поэтому после новой озвучки клипы
переснимаются — шар «дышит» огибающей именно этой реплики.

Черновой голос для проверки тайминга (Linux): `python promo/voice.py --engine rhvoice`.

## Где что менять

- Текст, порядок сцен, проблема/решение — `scenes.py`.
- Что Джарвис делает в сцене (инструмент, ответ, капсула) — `capture/capture.py` (`ORB`, `ISLAND_EVENTS`).
- Камера на экранах — `build.py` (`CAM`), стиль — `CSS` там же.
