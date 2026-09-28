"""Собрать из assets/art то, что нужно программе и установщику.

  • app.ico (корень) и assets/art/icon/jarvis.ico — шар из точек на тёмной
    скруглённой плашке во всех размерах 16…256: без плашки шар терялся в
    светлой теме Windows и на светлом Проводнике;
  • assets/art/icon/tray_online.png / tray_offline.png — трей (пауза — серый);
  • установщик: картинка на каждый шаг (design/installer) и подпись автора
    на последнем, в 1× и 2× (экраны 150–200 %);
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


STEPS = ("folder", "tasks", "ready", "installing")    # маленькие картинки внутренних шагов
CREDIT = "@atabekovch"                                   # автор — на последнем экране


def _fit(src: Path, w: int, h: int, zoom: float = 1.0) -> Image.Image:
    """Вырезать из картинки середину с пропорцией w:h (zoom > 1 — ближе) и уменьшить."""
    im = Image.open(src).convert("RGB")
    sw, sh = im.size
    cw = min(sw, sh * w / h) / zoom
    ch = min(sh, cw * h / w)
    box = ((sw - cw) / 2, (sh - ch) / 2, (sw + cw) / 2, (sh + ch) / 2)
    return im.resize((w, h), Image.Resampling.LANCZOS, box=box)


def _font(size: int, medium: bool = True):
    from PIL import ImageFont
    name = "Tektur-Medium.ttf" if medium else "Tektur-Regular.ttf"
    return ImageFont.truetype(str(ROOT / "design" / "fonts" / name), size)


def _credit(im: Image.Image, scale: int) -> Image.Image:
    """Подпись автора в тёмном низу финальной картинки: черта, CREATED BY, ник со свечением.
    Текст рисуем сами, а не нейросетью — у неё буквы «плывут»."""
    from PIL import ImageFilter
    w, h = im.size
    teal = (63, 208, 189)
    small, big = _font(7 * scale, medium=False), _font(15 * scale)
    y_line = h - 62 * scale
    d = ImageDraw.Draw(im, "RGBA")
    for x in range(w):                                         # черта гаснет к краям
        a = int(150 * max(0.0, 1 - abs(x - w / 2) / (w * 0.36)))
        if a:
            d.point((x, y_line), fill=(*teal, a))
    label = "C R E A T E D   B Y"
    lw = d.textlength(label, font=small)
    d.text(((w - lw) / 2, y_line + 9 * scale), label, font=small, fill=(*teal, 170))
    nw = d.textlength(CREDIT, font=big)
    pos = ((w - nw) / 2, y_line + 21 * scale)
    glow = Image.new("RGBA", im.size, (0, 0, 0, 0))            # мягкое бирюзовое свечение под ником
    ImageDraw.Draw(glow).text(pos, CREDIT, font=big, fill=(*teal, 200))
    glow = glow.filter(ImageFilter.GaussianBlur(3 * scale))
    out = Image.alpha_composite(im.convert("RGBA"), glow)
    ImageDraw.Draw(out).text(pos, CREDIT, font=big, fill=(226, 252, 248, 255))
    return out.convert("RGB")


def build_installer():
    """Картинки установщика из design/installer (сгенерированы ИИ): у каждого шага своя.

    wizard_large — «Добро пожаловать», wizard_finish — «Готово» с подписью автора,
    wizard_small — «Папка установки» (по умолчанию), step_* — остальные шаги."""
    src, out = ROOT / "design" / "installer", ART / "installer"
    for scale in (1, 2):
        sfx = "" if scale == 1 else "_2x"
        W, H, w, h = 164 * scale, 314 * scale, 55 * scale, 58 * scale
        _fit(src / "01_welcome.png", W, H).save(out / f"wizard_large{sfx}.bmp")
        _credit(_fit(src / "06_finish.png", W, H), scale).save(out / f"wizard_finish{sfx}.bmp")
        for i, name in enumerate(STEPS, start=2):
            im = _fit(src / f"0{i}_{name}.png", w, h, zoom=1.45)     # значок крупнее, поля меньше
            im.save(out / (f"wizard_small{sfx}.bmp" if name == "folder" else f"step_{name}{sfx}.bmp"))


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
