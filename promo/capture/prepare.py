"""Раскладывает захват (capture.py) для ролика: promo/build/cap/<seq>/NNNN.jpg,
вырезает карточки результата крупно — promo/build/cards/<name>.png."""
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

SRC = Path(sys.argv[1]).resolve()
BUILD = Path(__file__).resolve().parents[1] / "build"
CARD_X0, CARD_Y0, CARD_X1 = 1234, 142, 1899      # правая колонка HUD при окне 1280×800 ×1.5


def card_bottom(im: Image.Image) -> int:
    last = 538
    for y in range(150, 1150):
        if max(im.getpixel((CARD_X0 + 4, y))) > 45:
            last = y
    return last + 5


def main():
    cap = BUILD / "cap"
    cap.mkdir(parents=True, exist_ok=True)
    old = BUILD / "assets.json"
    manifest = json.loads(old.read_text(encoding="utf-8"))["seq"] if old.exists() else {}
    for src in (SRC, *[Path(p) for p in sys.argv[2:]]):
        manifest.update(json.loads((src / "manifest.json").read_text(encoding="utf-8")))
        for item in src.iterdir():
            dst = cap / item.name
            if item.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(item, dst)
            elif item.suffix == ".png":
                shutil.copy2(item, dst)
    cards = {}
    (BUILD / "cards").mkdir(exist_ok=True)
    for name, n in manifest.items():
        if name.startswith(("page_", "island", "wake", "tour")):
            continue
        im = Image.open(cap / name / f"{n - 1:04d}.jpg").convert("RGB")
        box = (CARD_X0, CARD_Y0, CARD_X1, card_bottom(im))
        im.crop(box).save(BUILD / "cards" / f"{name}.png")
        cards[name] = [box[2] - box[0], box[3] - box[1]]
    (BUILD / "assets.json").write_text(json.dumps({"seq": manifest, "cards": cards}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    (BUILD / "assets.js").write_text("window.ASSETS=" + json.dumps({"seq": manifest, "cards": cards}), encoding="utf-8")
    print("ok", len(manifest), "sequences,", len(cards), "cards")


main()
