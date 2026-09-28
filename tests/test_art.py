"""Графика JARVIS (assets/art): всё из описи на месте, иконка — на плашке во
всех размерах (видна и в светлой теме), установщик ссылается на настоящие
файлы, экраны рисуют картинки (мастер, «Первые шаги», пустые списки)."""
import json
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "assets" / "art"


def test_manifest_files_exist_without_duplicates():
    items = json.loads((ART / "manifest.json").read_text(encoding="utf-8"))
    files = [e["file"] for e in items]
    assert len(files) == len(set(files))
    for f in files:
        assert (ART / f).is_file(), f
    assert not list((ART / "setup").glob("*.png"))                     # мастер — в JPEG
    assert sum(p.stat().st_size for p in ART.rglob("*") if p.is_file()) < 4_000_000


def test_app_icon_has_plate_in_every_size():
    ico = Image.open(ROOT / "app.ico")
    sizes = sorted(ico.info["sizes"])
    assert (16, 16) in sizes and (256, 256) in sizes
    for s in sizes:
        ico.size = s
        im = ico.convert("RGBA")
        w = s[0]
        # середина верхнего края — плашка (непрозрачная и тёмная), а не прозрачный фон
        r, g, b, a = im.getpixel((w // 2, max(1, w // 10)))
        assert a > 200 and r + g + b < 200, s
    for name in ("tray_online", "tray_offline"):
        assert Image.open(ART / "icon" / f"{name}.png").size == (64, 64)


def test_installer_uses_existing_art():
    iss = (ROOT / "scripts" / "installer.iss").read_text(encoding="utf-8")
    for line in re.findall(r"^Wizard\w*ImageFile=(.+)$", iss, re.M):
        for part in line.split(","):
            assert (ROOT / "scripts" / part.strip().replace("\\", "/")).resolve().is_file(), part
    big = Image.open(ART / "installer" / "wizard_large_2x.bmp")
    assert big.size == (328, 628) and big.mode == "RGB"


def test_installer_has_image_for_every_step_and_credit():
    """Картинки шагов меняет [Code]: каждая, что он достаёт, вшита (dontcopy) и есть в 1× и 2×;
    вшитые стоят раньше программы (иначе мастер распаковывал бы весь архив ради картинки)."""
    iss = (ROOT / "scripts" / "installer.iss").read_text(encoding="utf-8")
    shown = re.findall(r"ShowStepImage\(WizardForm\.\w+, '(\w+)', (\d+)\)", iss)
    assert {n for n, _ in shown} == {"wizard_small", "step_tasks", "step_ready", "step_installing", "wizard_finish"}
    files = re.findall(r'^Source: "([^"]+)"', iss, re.M)
    assert files[-1].startswith("..\\dist\\JARVIS")                           # программа — последней
    embedded = files[:-1]
    for name, base in shown:
        for sfx, k in (("", 1), ("_2x", 2)):
            f = ART / "installer" / f"{name}{sfx}.bmp"
            assert any(Path(f.name).match(Path(e.replace("\\", "/")).name) for e in embedded), f.name
            im = Image.open(f)
            assert im.mode == "RGB" and im.width == int(base) * k, f.name
    assert "https://t.me/atabekovch" in iss and "instagram.com/atabekovch" in iss
    # подпись в тёмном низу финальной картинки есть: светлые буквы ника над пустым краем
    fin = Image.open(ART / "installer" / "wizard_finish_2x.bmp").convert("L")
    assert max(fin.crop((40, 540, 288, 585)).getdata()) > 200
    assert max(fin.crop((0, 600, 328, 628)).getdata()) < 60


def test_screens_draw_art():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from core import tray
    import ui_kit as K
    assert K.art_pixmap("setup/setup_keys.jpg") is not None and K.art_pixmap("нет/такой.png") is None
    b = K.ArtBanner("setup/setup_welcome.jpg", "Привет", "текст")
    b.resize(700, 150)
    img = b.grab().toImage()
    assert img.pixelColor(640, 75).lightness() > img.pixelColor(20, 140).lightness()   # герой справа, текст слева
    e = K.EmptyArt("commands", "Пока пусто.")
    assert e.findChildren(type(e.text))[0].pixmap() is not None
    assert not tray.create_reactor_icon(True).isNull() and not tray.create_reactor_icon(False).isNull()
    from ui_welcome import WelcomeDialog
    w = WelcomeDialog(None)
    w.refresh()
    assert w._thumb("gemini", False) is not None and w._thumb("нет", False) is None
