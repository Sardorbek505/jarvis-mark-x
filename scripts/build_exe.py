"""Скрипт автоматизированной сборки JARVIS Mark X в автономный .exe файл.

Использование:
    python scripts/build_exe.py
"""
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
    print("[5/5] Создание инсталлятора Inno Setup (JARVIS_Setup_v1.0.exe)...")
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
        subprocess.check_call([str(iscc_exe), str(iss_file)], cwd=str(_BASE_DIR / "scripts"))
        setup_exe = _BASE_DIR / "dist" / "JARVIS_Setup_v1.0.exe"
        if setup_exe.exists():
            size_mb = setup_exe.stat().st_size / (1024 * 1024)
            print("\n=======================================================")
            print("  [OK] ИНСТАЛЛЯТОР УСПЕШНО СОЗДАН!")
            print(f"  Файл инсталлятора: {setup_exe} ({size_mb:.1f} MB)")
            print("=======================================================\n")
    except Exception as exc:
        print(f"  [ERROR] Ошибка при компиляции Inno Setup: {exc}")


if __name__ == "__main__":
    try:
        check_dependencies()
        clean_previous_builds()
        build_executable()
        post_build()
        build_installer()
    except Exception as e:
        print(f"\n[ERROR] Сборка прервана с ошибкой: {e}")
        sys.exit(1)
