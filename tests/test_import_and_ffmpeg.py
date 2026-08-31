"""Две поломки, которые видны только там, где нет секретов и нет ffmpeg.

Обе месяцами держали CI красным и молча выключали голосовой резерв на чужой
машине, а локально не воспроизводились: у владельца и конфиг на месте, и
системный ffmpeg в PATH.
"""
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _run(code: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """Гоняет сниппет отдельным процессом: импорт нужен ЧИСТЫЙ."""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=timeout,
    )


def test_bot_imports_without_secrets():
    """Импорт telegram_bot.bot не должен требовать токенов.

    load_config(require_bot=True) на нехватке токена делает sys.exit(1). Пока
    он стоял на уровне модуля, любой импортёр умирал вместе с ним — pytest
    падал целиком с INTERNALERROR: SystemExit, а не одним тестом.
    """
    proc = _run(
        "import os\n"
        "from pathlib import Path\n"
        "os.environ.pop('GEMINI_API_KEY', None)\n"
        "os.environ.pop('TELEGRAM_BOT_TOKEN', None)\n"
        "import telegram_bot.config as c\n"
        "c._CONFIG_FILE = Path('__no_such_config__.json')\n"
        "import telegram_bot.bot\n"
        "print('IMPORTED')\n"
    )
    assert proc.returncode == 0, f"импорт упал:\n{proc.stdout}\n{proc.stderr}"
    assert "IMPORTED" in proc.stdout


def test_bot_main_still_refuses_without_secrets():
    """Перенос проверки не должен её ослабить: запуск без секретов отказывает."""
    proc = _run(
        "import os\n"
        "from pathlib import Path\n"
        "os.environ.pop('GEMINI_API_KEY', None)\n"
        "os.environ.pop('TELEGRAM_BOT_TOKEN', None)\n"
        "import telegram_bot.config as c\n"
        "c._CONFIG_FILE = Path('__no_such_config__.json')\n"
        "import telegram_bot.bot as b\n"
        "b.main()\n"
    )
    assert proc.returncode == 1, f"ожидался отказ, получено {proc.returncode}"
    assert "not found" in (proc.stdout + proc.stderr)


def test_mp3_decoded_without_system_ffprobe():
    """Резервный голос обязан звучать там, где нет СИСТЕМНОГО ffmpeg.

    pydub для декодирования звал ещё и ffprobe (mediainfo_json), а портативный
    imageio-ffmpeg поставляет только ffmpeg — на чужой машине конвертация
    падала FileNotFoundError и Edge-TTS молча отдавал None.
    """
    from telegram_bot.tts_edge import _mp3_to_pcm
    from telegram_bot.voice_util import _ffmpeg_exe

    exe = _ffmpeg_exe()
    assert exe, "imageio-ffmpeg не поставил бинарь — проверять нечего"

    # Крохотный настоящий mp3 делаем тем же ffmpeg, чтобы тест не ходил в сеть.
    made = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
         "-f", "mp3", "pipe:1"],
        capture_output=True, timeout=60,
    )
    assert made.returncode == 0 and made.stdout, made.stderr[:200]

    saved_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = ""          # ни ffmpeg, ни ffprobe в системе
        pcm = _mp3_to_pcm(made.stdout, 24000)
    finally:
        os.environ["PATH"] = saved_path

    assert pcm, "без системного ffprobe декодирование снова молча вернуло None"
    # 0.3 с при 24 кГц / int16 — примерно 14400 байт; хватает грубой границы.
    assert len(pcm) > 8000, f"подозрительно мало звука: {len(pcm)} байт"
