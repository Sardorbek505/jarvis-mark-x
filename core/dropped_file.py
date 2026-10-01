"""Файл, брошенный на капсулу, — в то, что можно отдать Gemini Live.

Фото — картинкой (большое — уменьшаем, экзотический формат — в JPEG).
Текст и код — текстом. PDF — текстом через pypdf. Word (.docx) — текстом
прямо из XML внутри архива, без лишних зависимостей. Остальное — честный
отказ с причиной: капсула покажет её владельцу.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 30_000                 # символов текста в разговор — дальше обрезаем
IMAGE_SIDE = 1600                 # длинная сторона картинки

IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
CONVERT_EXT = {".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic"}
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".toml", ".log",
            ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".java", ".kt", ".c", ".h", ".cpp", ".cs",
            ".go", ".rs", ".rb", ".php", ".sql", ".sh", ".bat", ".ps1", ".srt", ".rtf"}


class Unsupported(Exception):
    """Файл не прочитать — текст исключения показывается владельцу."""


@dataclass
class Prepared:
    name: str
    kind: str                     # image | text
    text: str = ""
    data: bytes = b""
    mime: str = ""
    truncated: bool = False


def prepare(path: str | Path) -> Prepared:
    p = Path(path)
    if p.is_dir():
        raise Unsupported("это папка, а не файл")
    if not p.is_file():
        raise Unsupported("файл не найден")
    size = p.stat().st_size
    if size > MAX_BYTES:
        raise Unsupported(f"больше {MAX_BYTES // (1024 * 1024)} МБ")
    ext = p.suffix.lower()
    raw = p.read_bytes()
    if ext in IMAGE_EXT or ext in CONVERT_EXT:
        data, mime = _image(raw, ext)
        return Prepared(p.name, "image", data=data, mime=mime)
    if ext == ".pdf":
        return _text(p.name, _pdf_text(raw))
    if ext == ".docx":
        return _text(p.name, _docx_text(raw))
    if ext in TEXT_EXT or _looks_like_text(raw):
        return _text(p.name, _decode(raw))
    raise Unsupported(f"не умею читать {ext or 'такие файлы'}")


def instruction(f: Prepared) -> str:
    """Что сказать модели вместе с файлом."""
    what = "картинку" if f.kind == "image" else "файл"
    cut = " Файл длинный — ниже только начало." if f.truncated else ""
    return (f"Владелец перетащил на тебя {what} «{f.name}».{cut} Посмотри и коротко, в одну-две фразы, "
            "скажи, что это, и спроси, что с ним сделать. Дальше отвечай на вопросы по нему.")


def _text(name: str, text: str) -> Prepared:
    text = text.strip()
    if not text:
        raise Unsupported("внутри нет текста")
    cut = len(text) > MAX_TEXT
    return Prepared(name, "text", text=text[:MAX_TEXT], truncated=cut)


def _image(raw: bytes, ext: str) -> tuple[bytes, str]:
    try:
        from PIL import Image
    except ImportError:
        if ext in IMAGE_EXT:
            return raw, IMAGE_EXT[ext]
        raise Unsupported("для этого формата картинок нужен Pillow") from None
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        raise Unsupported("картинка не открывается") from exc
    if ext in IMAGE_EXT and max(img.size) <= IMAGE_SIDE:
        return raw, IMAGE_EXT[ext]
    img.thumbnail((IMAGE_SIDE, IMAGE_SIDE))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


def _pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise Unsupported("для PDF нужен pypdf (pip install pypdf)") from None
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise Unsupported("PDF защищён паролем")
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
            if sum(map(len, parts)) > MAX_TEXT:
                break
    except Unsupported:
        raise
    except Exception as exc:
        raise Unsupported("PDF не открывается") from exc
    text = "\n".join(parts)
    if not text.strip():
        raise Unsupported("в PDF нет текста — похоже, это скан")
    return text


_W_PARA = re.compile(r"</w:p>")
_TAG = re.compile(r"<[^>]+>")


def _docx_text(raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except Exception as exc:
        raise Unsupported("документ Word не открывается") from exc
    import html
    return html.unescape(_TAG.sub("", _W_PARA.sub("\n", xml)))


def _looks_like_text(raw: bytes) -> bool:
    head = raw[:4096]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        return exc.start > len(head) - 4          # обрезали посреди символа — всё равно текст


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")
