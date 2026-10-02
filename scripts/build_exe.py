"""Скрипт автоматизированной сборки JARVIS Mark X в автономный .exe файл.

Использование:
    python scripts/build_exe.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Гарантируем корректный вывод UTF-8 в консолях Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_BASE_DIR = Path(__file__).resolve().parent.parent


def check_dependencies():
    print("[1/4] Проверка зависимостей сборщика...")
    try:
        import PyInstaller
        print(f"  [OK] PyInstaller версия: {PyInstaller.__version__}")
    except ImportError:
        print("  [WARN] PyInstaller не найден. Устанавливаю: pip install pyinstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def clean_previous_builds():
    print("[2/4] Очистка старых артефактов сборки...")
    for folder in ["build", "dist"]:
        p = _BASE_DIR / folder
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            print(f"  [OK] Очищена папка: {folder}/")


_VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"


_SPK_URL = "https://alphacephei.com/vosk/models/vosk-model-spk-0.4.zip"


def ensure_vosk_model():
    """Модели Vosk: словарь слова «Джарвис» (~45 МБ, models/vosk-small-ru) и
    отпечатки голоса владельца (~16 МБ, models/vosk-spk, core/voice_id.py).

    В репозитории их нет — они большие и не наши. Без них сборка всё равно
    работает (имя слушается через Gemini, голос не проверяется, модель
    докачивается из окна «Обо мне»), но в CI это ошибка."""
    _fetch_model("[2b] Модель слова «Джарвис» (Vosk)...", _VOSK_URL, "vosk-small-ru", "am")
    _fetch_model("[2c] Модель голоса владельца (Vosk spk)...", _SPK_URL, "vosk-spk", "final.ext.raw")
    _fetch_file("[2d] Модель голоса владельца (WeSpeaker)...", _WESPEAKER_URL, "voice-id",
                "wespeaker_en_voxceleb_resnet34.onnx")
    print("[2e] Слово «Джарвис» без ключей (sherpa-onnx KWS)...")
    try:
        if str(_BASE_DIR) not in sys.path:          # скрипт запускают из scripts/ — core рядом не виден
            sys.path.insert(0, str(_BASE_DIR))
        from core import wake_kws
        dst = _BASE_DIR / "models" / wake_kws.MODEL_DIRNAME
        if all((dst / f).is_file() for f in wake_kws.FILES.values()):
            print("  [OK] уже на месте")
        else:
            wake_kws.download(dst)
            print("  [OK] скачана")
    except Exception as exc:
        print(f"  [WARN] модель не скачалась: {exc}")
        if os.getenv("CI"):
            raise


_WESPEAKER_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
                  "wespeaker_en_voxceleb_resnet34.onnx")


def _fetch_file(title: str, url: str, dirname: str, name: str):
    """Модель одним файлом (не архивом)."""
    print(title)
    dst = _BASE_DIR / "models" / dirname / name
    if dst.exists():
        print("  [OK] уже на месте")
        return
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        print(f"  [OK] скачана ({len(data) / 1e6:.0f} МБ)")
    except Exception as exc:
        print(f"  [WARN] модель не скачалась: {exc}")
        if os.getenv("CI"):
            raise


def _fetch_model(title: str, url: str, dirname: str, marker: str):
    print(title)
    dst = _BASE_DIR / "models" / dirname
    if (dst / marker).exists():
        print("  [OK] уже на месте")
        return
    import io
    import urllib.request
    import zipfile
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = z.namelist()[0].split("/")[0]
            z.extractall(dst.parent)
        (dst.parent / root).rename(dst)
        print(f"  [OK] скачана ({len(data) / 1e6:.0f} МБ)")
    except Exception as exc:
        print(f"  [WARN] модель не скачалась: {exc}")
        if os.getenv("CI"):
            raise


def build_executable():
    print("[3/4] Запуск компиляции JARVIS.exe (это может занять 1-2 минуты)...")
    spec_path = _BASE_DIR / "jarvis.spec"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(spec_path),
    ]
    subprocess.check_call(cmd, cwd=str(_BASE_DIR))


def post_build():
    print("[4/5] Проверка собранного пакета...")
    dist_jarvis = _BASE_DIR / "dist" / "JARVIS"
    exe_file = dist_jarvis / "JARVIS.exe"

    if exe_file.exists():
        size_mb = exe_file.stat().st_size / (1024 * 1024)
        print("\n=======================================================")
        print("  [OK] СБОРКА УСПЕШНО ЗАВЕРШЕНА!")
        print(f"  Папка программы: {dist_jarvis}")
        print(f"  Исполняемый файл: {exe_file} ({size_mb:.1f} MB)")
        print("=======================================================\n")
    else:
        raise RuntimeError("Исполняемый файл JARVIS.exe не найден в dist/JARVIS.")


def build_installer():
    print("[5/5] Создание инсталлятора Inno Setup (JARVIS_Setup_v*.exe)...")
    iss_file = _BASE_DIR / "scripts" / "installer.iss"
    if not iss_file.exists():
        print(f"  [WARN] Файл {iss_file} не найден. Пропуск создания установщика.")
        return

    iscc_candidates = [
        shutil.which("iscc"),
        r"C:\Users\User\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    iscc_exe = next((p for p in iscc_candidates if p and Path(p).exists()), None)
    if not iscc_exe:
        print("  [WARN] Компилятор Inno Setup (ISCC.exe) не найден. Установщик не создан.")
        return

    try:
        print(f"  [OK] Запуск компилятора: {iscc_exe}")
        cmd = [str(iscc_exe), str(iss_file)]
        version = os.getenv("JARVIS_VERSION", "").strip().lstrip("v")
        if version:
            cmd.insert(1, f"/DMyAppVersion={version}")   # из тега релиза
        subprocess.check_call(cmd, cwd=str(_BASE_DIR / "scripts"))
        found = sorted((_BASE_DIR / "dist").glob("JARVIS_Setup_v*.exe"))
        setup_exe = found[-1] if found else None
        if setup_exe:
            size_mb = setup_exe.stat().st_size / (1024 * 1024)
            print("\n=======================================================")
            print("  [OK] ИНСТАЛЛЯТОР УСПЕШНО СОЗДАН!")
            print(f"  Файл инсталлятора: {setup_exe} ({size_mb:.1f} MB)")
            print("=======================================================\n")
    except Exception as exc:
        print(f"  [ERROR] Ошибка при компиляции Inno Setup: {exc}")
        if os.getenv("CI"):
            raise


if __name__ == "__main__":
    try:
        check_dependencies()
        clean_previous_builds()
        ensure_vosk_model()
        build_executable()
        post_build()
        build_installer()
    except Exception as e:
        print(f"\n[ERROR] Сборка прервана с ошибкой: {e}")
        sys.exit(1)
