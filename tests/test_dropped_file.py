"""Файл, брошенный на капсулу: что уходит в Gemini и когда честный отказ.

И хелперы main.py для капсулы: вопрос для кнопок и исход шага."""
import io
import zipfile

import pytest

from core import dropped_file as df


def test_text_and_code(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# План\nкупить хлеб", encoding="utf-8")
    p = df.prepare(f)
    assert p.kind == "text" and "купить хлеб" in p.text and not p.truncated
    code = tmp_path / "Makefile"                       # без расширения, но текст
    code.write_text("all:\n\techo hi\n", encoding="utf-8")
    assert df.prepare(code).kind == "text"


def test_cp1251_text_is_readable(tmp_path):
    f = tmp_path / "old.txt"
    f.write_bytes("Привет из Windows".encode("cp1251"))
    assert df.prepare(f).text == "Привет из Windows"


def test_long_text_is_cut(tmp_path):
    f = tmp_path / "big.log"
    f.write_text("строка\n" * 10_000, encoding="utf-8")
    p = df.prepare(f)
    assert p.truncated and len(p.text) == df.MAX_TEXT
    assert "начало" in df.instruction(p)


def test_docx_without_dependencies(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml",
                   '<w:document><w:body><w:p><w:r><w:t>Счёт №7</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>Итого &amp; НДС</w:t></w:r></w:p></w:body></w:document>')
    f = tmp_path / "invoice.docx"
    f.write_bytes(buf.getvalue())
    text = df.prepare(f).text
    assert "Счёт №7" in text and "Итого & НДС" in text and text.index("Счёт") < text.index("Итого")


def test_images_small_kept_big_shrunk_exotic_converted(tmp_path):
    from PIL import Image
    small = tmp_path / "a.png"
    Image.new("RGB", (40, 30), "red").save(small)
    p = df.prepare(small)
    assert p.kind == "image" and p.mime == "image/png" and p.data == small.read_bytes()
    big = tmp_path / "b.jpg"
    Image.new("RGB", (4000, 1000), "blue").save(big)
    p = df.prepare(big)
    assert p.mime == "image/jpeg" and max(Image.open(io.BytesIO(p.data)).size) == df.IMAGE_SIDE
    webp = tmp_path / "c.webp"
    Image.new("RGBA", (50, 50), (0, 255, 0, 128)).save(webp)
    assert df.prepare(webp).mime == "image/jpeg"
    assert "картинку" in df.instruction(df.prepare(small))


def test_pdf_text(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    w = pypdf.PdfWriter()
    w.add_blank_page(100, 100)
    f = tmp_path / "scan.pdf"
    with open(f, "wb") as fh:
        w.write(fh)
    with pytest.raises(df.Unsupported, match="скан"):  # страниц без текста — честно говорим
        df.prepare(f)


@pytest.mark.parametrize("make, why", [
    (lambda d: d / "nope.txt", "не найден"),
    (lambda d: d, "папка"),
])
def test_refusals(tmp_path, make, why):
    with pytest.raises(df.Unsupported, match=why):
        df.prepare(make(tmp_path))


def test_binary_and_empty_are_refused(tmp_path):
    exe = tmp_path / "game.exe"
    exe.write_bytes(b"MZ\x00\x00" * 100)
    with pytest.raises(df.Unsupported, match=r"\.exe"):
        df.prepare(exe)
    empty = tmp_path / "empty.txt"
    empty.write_text("   \n", encoding="utf-8")
    with pytest.raises(df.Unsupported, match="нет текста"):
        df.prepare(empty)


def test_too_big(tmp_path, monkeypatch):
    monkeypatch.setattr(df, "MAX_BYTES", 10)
    f = tmp_path / "a.txt"
    f.write_text("x" * 50, encoding="utf-8")
    with pytest.raises(df.Unsupported, match="МБ"):
        df.prepare(f)


# ── main.py: капсула ─────────────────────────────────────────────────────────

def test_confirm_label_and_tool_outcome():
    import main as m
    assert m._confirm_label("computer_control", {"action": "shutdown"}) == "Выключить компьютер?"
    assert m._confirm_label("computer_control", {"action": "restart"}) == "Перезагрузить компьютер?"
    assert m._confirm_label("files", {"action": "delete", "path": "a.txt"}) == "Удалить a.txt?"
    assert m._tool_outcome({"result": "Открыл."}) is True
    assert m._tool_outcome({"result": "Ошибка: нет сети"}) is False
    assert m._tool_outcome({"result": "Не успело выполниться за 25 секунд."}) is False
    assert m._tool_outcome({"result": "НЕ ВЫПОЛНЕНО — нужно подтверждение. Переспроси"}) is None
    assert m._tool_outcome(None) is True
