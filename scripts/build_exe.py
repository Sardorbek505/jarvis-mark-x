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
    print("[4/4] Проверка собранного пакета...")
    dist_jarvis = _BASE_DIR / "dist" / "JARVIS"
    exe_file = dist_jarvis / "JARVIS.exe"

    if exe_file.exists():
        size_mb = exe_file.stat().st_size / (1024 * 1024)
        print("\n=======================================================")
        print("  [OK] СБОРКА УСПЕШНО ЗАВЕРШЕНА!")
        print(f"  Папка программы: {dist_jarvis}")
        print(f"  Исполняемый файл: {exe_file} ({size_mb:.1f} MB)")
        print("  Теперь можно запускать JARVIS.exe или упаковать инсталлятором Inno Setup.")
        print("=======================================================\n")
    else:
        print("\n[ERROR] Исполняемый файл JARVIS.exe не найден в dist/JARVIS.")


if __name__ == "__main__":
    try:
        check_dependencies()
        clean_previous_builds()
        build_executable()
        post_build()
    except Exception as e:
        print(f"\n[ERROR] Сборка прервана с ошибкой: {e}")
        sys.exit(1)
