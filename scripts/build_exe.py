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


def ensure_vosk_model():
    """Словарь слова «Джарвис» (~45 МБ) в models/vosk-small-ru.

    В репозитории его нет — он большой и не наш. Без него сборка всё равно
    работает (имя слушается через Gemini), но в CI это ошибка: пользователь
    получил бы установщик без главного."""
    print("[2b] Модель слова «Джарвис» (Vosk)...")
    dst = _BASE_DIR / "models" / "vosk-small-ru"
    if (dst / "am").exists():
        print("  [OK] уже на месте")
        return
    import io
    import urllib.request
    import zipfile
    try:
        with urllib.request.urlopen(_VOSK_URL, timeout=120) as resp:
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
