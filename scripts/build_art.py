"""Собрать из assets/art то, что нужно программе и установщику.

  • app.ico (корень) и assets/art/icon/jarvis.ico — шар из точек на тёмной
    скруглённой плашке во всех размерах 16…256: без плашки шар терялся в
    светлой теме Windows и на светлом Проводнике;
  • assets/art/icon/tray_online.png / tray_offline.png — трей (пауза — серый);
  • установщик: wizard_large / wizard_small в 1× и 2× (экраны 150–200 %);
  • картинки мастера и Mini App — в JPEG (в exe было ~10 МБ PNG).

Запуск: python scripts/build_art.py  (нужен Pillow; исходник — icon_1024.png)
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "assets" / "art"
BG, PANEL, BORDER = (3, 6, 9), (7, 12, 17), (36, 50, 64)
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def _sphere(size: int) -> Image.Image:
    """Шар из мастер-иконки, ярче (на плашке он должен читаться и в 16 px)."""
    src = Image.open(ART / "icon" / "icon_1024.png").convert("RGBA")
    box = src.getbbox() or (0, 0, *src.size)
    s = src.crop(box)
    s = ImageEnhance.Brightness(s).enhance(1.35 if size >= 48 else 1.7)
    if size < 48:                                     # мелко: точки сливаются — чуть контраста
        s = ImageEnhance.Contrast(s).enhance(1.25)
    return s.resize((size, size), Image.Resampling.LANCZOS)


def icon_layer(size: int) -> Image.Image:
    k = 4                                             # рисуем крупно, потом уменьшаем — ровные края
    big = size * k
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = round(big * (0.04 if size > 24 else 0.0))
    radius = round(big * 0.23)
    d.rounded_rectangle((pad, pad, big - pad - 1, big - pad - 1), radius, fill=PANEL + (255,),
                        outline=BORDER + (255,), width=max(k, round(big * 0.012)))
    sph = round(big * (0.74 if size > 24 else 0.86))
    s = _sphere(sph)
    im.alpha_composite(s, ((big - sph) // 2, (big - sph) // 2))
    return im.resize((size, size), Image.Resampling.LANCZOS)


def build_ico():
    layers = [icon_layer(s) for s in ICO_SIZES]
    for dst in (ART / "icon" / "jarvis.ico", ROOT / "app.ico"):
        layers[-1].save(dst, format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=layers[:-1])
    layers[-1].save(ART / "icon" / "icon_256.png")


def build_tray():
    for name, gray in (("tray_online", False), ("tray_offline", True)):
        im = _sphere(64)
        if gray:
            a = im.getchannel("A")
            im = ImageOps.grayscale(im.convert("RGB")).convert("RGBA")
            im.putalpha(a.point(lambda v: int(v * 0.8)))
        im.save(ART / "icon" / f"{name}.png")


def _wizard(w: int, h: int, small: bool) -> Image.Image:
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im, "RGBA")
    if not small:                                    # сетка HUD и дуги, как у мастера
        step = w // 5
        for x in range(step, w, step):
            d.line((x, 0, x, h), fill=(63, 208, 189, 18), width=1)
        for y in range(step, h, step):
            d.line((0, y, w, y), fill=(63, 208, 189, 18), width=1)
        for r, a in ((w * 0.46, 70), (w * 0.34, 40)):
            cx, cy = w / 2, h * 0.86
            d.arc((cx - r, cy - r, cx + r, cy + r), 200, 340, fill=(63, 208, 189, a), width=max(1, w // 160))
    sph = int(w * (0.78 if small else 0.62))
    s = _sphere(sph)
    y = (h - sph) // 2 if small else int(h * 0.2)
    im.paste(s, ((w - sph) // 2, y), s)
    return im


def build_installer():
    out = ART / "installer"
    for scale in (1, 2):
        suffix = "" if scale == 1 else "_2x"
        _wizard(164 * scale, 314 * scale, False).save(out / f"wizard_large{suffix}.bmp")
        _wizard(55 * scale, 58 * scale, True).save(out / f"wizard_small{suffix}.bmp")


def to_jpeg():
    for folder in ("setup", "miniapp"):
        for png in sorted((ART / folder).glob("*.png")):
            Image.open(png).convert("RGB").save(png.with_suffix(".jpg"), quality=88, optimize=True, progressive=True)
            png.unlink()


def main() -> int:
    build_ico()
    build_tray()
    build_installer()
    to_jpeg()
    print("Готово:", ", ".join(p.name for p in sorted((ART / "icon").iterdir())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
