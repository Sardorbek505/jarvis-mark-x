"""Проверка слова «Джарвис» на настоящем звуке (CI, job wake-word).

Голос Edge (два разных — мужской и женский) произносит фразы с именем и
без. Каждую прогоняем через тот же LocalWake, что работает в Джарвисе, с
настоящей моделью Vosk. Имя должно ловиться там, где оно есть, и молчать
там, где его нет. Печатаем, что Vosk услышал, — по этому подбирается
WAKE_RE, если модель пишет имя по-своему.

Запуск: python scripts/wake_check_vosk.py models/vosk-small-ru
Нужны: vosk, edge-tts, ffmpeg в PATH.
"""
import asyncio
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# Модуль грузим прямо из файла: `import core.wake_vosk` тянет core/__init__.py,
# а он — половину Джарвиса (новости, feedparser…), которой в этой проверке нет.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "wake_vosk", Path(__file__).resolve().parent.parent / "core" / "wake_vosk.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
LocalWake = _mod.LocalWake

VOICES = ["ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural"]
WITH_NAME = [
    "Джарвис",
    "Джарвис, открой ютуб",
    "Эй, Джарвис, какая погода завтра?",
    "Джарвис, включи музыку",
    "Слушай, Джарвис, сделай громче",
]
WITHOUT_NAME = [
    "Какая сегодня погода?",
    "Включи музыку погромче",
    "Дарвин написал книгу о видах",
    "Сегодня очень жарко",
    "Этот сервис опять не работает",
    "Я вчера смотрел фильм Марвел",
    "Червяк ползёт по дорожке",
]


async def _synth(text: str, voice: str, out: Path):
    """Сервис Edge иногда не отдаёт звук (NoAudioReceived) — это сбой
    синтеза, а не детектора: пробуем ещё раз с паузой."""
    import edge_tts
    for attempt in range(4):
        try:
            await edge_tts.Communicate(text, voice).save(str(out))
            return
        except Exception:
            if attempt == 3:
                raise
            await asyncio.sleep(2 * (attempt + 1))


def _ffmpeg() -> str:
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg                    # есть и в requirements Джарвиса
    return imageio_ffmpeg.get_ffmpeg_exe()


def _pcm(mp3: Path) -> bytes:
    return subprocess.run(
        [_ffmpeg(), "-loglevel", "error", "-i", str(mp3), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
        check=True, capture_output=True).stdout


def _detect(model_dir: Path, pcm: bytes) -> tuple[bool, str]:
    hit = threading.Event()
    w = LocalWake(lambda t: hit.set(), model_dir=model_dir)
    assert w.start(), "Vosk не запустился"
    pad = b"\0" * 16000                       # полсекунды тишины вокруг — как в жизни
    data = pad + pcm + pad * 3
    for i in range(0, len(data), 2048):       # кадры по 64 мс, как у микрофона
        w.feed(data[i:i + 2048])
        time.sleep(0.004)
    hit.wait(2.0)
    w.stop()
    return hit.is_set(), w.last_heard


def main():
    model_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "models/vosk-small-ru")
    misses, false_hits = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for voice in VOICES:
            for phrase, expect in [(p, True) for p in WITH_NAME] + [(p, False) for p in WITHOUT_NAME]:
                mp3 = Path(tmp) / "x.mp3"
                asyncio.run(_synth(phrase, voice, mp3))
                got, heard = _detect(model_dir, _pcm(mp3))
                mark = "OK " if got == expect else "ERR"
                print(f"{mark} [{voice.split('-')[2]}] «{phrase}» → услышал «{heard}» → имя: {got}")
                if expect and not got:
                    misses.append((voice, phrase))
                if got and not expect:
                    false_hits.append((voice, phrase))
    total = len(VOICES) * len(WITH_NAME)
    print(f"\nПоймано имён: {total - len(misses)}/{total}; ложных: {len(false_hits)}")
    # Синтезированный голос — не живой, поэтому допуск на один промах.
    if len(misses) > 1 or false_hits:
        sys.exit(1)


if __name__ == "__main__":
    main()
