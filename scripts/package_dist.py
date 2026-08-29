"""Автоматическая упаковка JARVIS Mark X в Release ZIP и Inno Setup Installer.
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = BASE_DIR / "dist"
APP_DIR = DIST_DIR / "JARVIS"
ISS_FILE = BASE_DIR / "scripts" / "installer.iss"


def find_iscc() -> Path | None:
    """Ищет компилятор Inno Setup (iscc.exe)."""
    # 1. В PATH
    p = shutil.which("iscc") or shutil.which("ISCC.exe")
    if p:
        return Path(p)

    # 2. Стандартные пути установки
    standard_paths = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 7" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Inno Setup 7" / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]
    for sp in standard_paths:
        if sp.exists():
            return sp
    return None


def create_portable_zip() -> Path:
    """Создает портативный ZIP архив дистрибутива JARVIS."""
    print("\n[1/2] Создание портативного ZIP-архива...")
    zip_path = DIST_DIR / "JARVIS_Mark_X_Portable_v1.0.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
        for root, dirs, files in os.walk(APP_DIR):
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(DIST_DIR)
                zipf.write(full_path, rel_path)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"  [OK] Портативный архив создан: {zip_path.name} ({size_mb:.1f} MB)")
    return zip_path


def compile_inno_installer() -> Path | None:
    """Компилирует JARVIS_Setup_v1.0.exe с помощью Inno Setup."""
    print("\n[2/2] Компиляция Windows-инсталлятора (Inno Setup)...")
    iscc = find_iscc()
    if not iscc:
        print("  [WARN] Inno Setup compiler (ISCC.exe) не найден в системе.")
        print("  Установите Inno Setup (winget install JRSoftware.InnoSetup) для сборки Setup.exe")
        return None

    print(f"  Найден компилятор: {iscc}")
    cmd = [str(iscc), str(ISS_FILE)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        setup_exe = DIST_DIR / "JARVIS_Setup_v1.0.exe"
        if setup_exe.exists():
            size_mb = setup_exe.stat().st_size / (1024 * 1024)
            print(f"  [OK] Инсталлятор успешно собран: {setup_exe.name} ({size_mb:.1f} MB)")
            return setup_exe
    else:
        print(f"  [ERROR] Ошибка компиляции Inno Setup: {res.stderr}")
    return None


if __name__ == "__main__":
    print("==================================================")
    print("  УПАКОВКА РЕЛИЗНОГО ДИСТРИБУТИВА JARVIS MARK X   ")
    print("==================================================")
    if not APP_DIR.exists():
        print(f"[ERROR] Папка {APP_DIR} не найдена. Сначала выполните: python scripts/build_exe.py")
        sys.exit(1)

    zip_file = create_portable_zip()
    setup_file = compile_inno_installer()

    print("\n==================================================")
    print("  РЕЗУЛЬТАТЫ СБОРКИ РЕЛИЗА:")
    print("==================================================")
    print(f"  1. Портативная версия: {zip_file}")
    if setup_file:
        print(f"  2. Установщик Windows: {setup_file}")
    print("==================================================")
