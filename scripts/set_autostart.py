"""Configure Windows Registry autostart for JARVIS."""

import sys
import winreg
from pathlib import Path

exe_path = Path(r"C:\Users\User\Desktop\jarvis-mark-x\dist\JARVIS\JARVIS.exe")
if not exe_path.exists():
    cmd = f'"{sys.executable}" "{Path(__file__).resolve().parent.parent / "main.py"}"'
else:
    cmd = f'"{exe_path}"'

key = winreg.OpenKey(
    winreg.HKEY_CURRENT_USER,
    r"Software\Microsoft\Windows\CurrentVersion\Run",
    0,
    winreg.KEY_SET_VALUE,
)
winreg.SetValueEx(key, "JARVIS_Mark_X", 0, winreg.REG_SZ, cmd)
winreg.CloseKey(key)

# Verify
key = winreg.OpenKey(
    winreg.HKEY_CURRENT_USER,
    r"Software\Microsoft\Windows\CurrentVersion\Run",
    0,
    winreg.KEY_READ,
)
val, _ = winreg.QueryValueEx(key, "JARVIS_Mark_X")
winreg.CloseKey(key)

print("[OK] Windows Registry Autostart successfully configured!")
print(f"Target command: {val}")
