"""
Действие: разобраться с файлом — прочитать, пересказать, распознать.

ЗАЧЕМ
    Файл на экране и ассистент рядом, но между ними не было ничего: чтобы
    спросить «о чём этот договор», человек должен был открыть PDF, выделить
    текст, скопировать и продиктовать. Проще прочитать самому — и ассистент
    оставался не при делах ровно там, где мог бы сэкономить полчаса.

    Теперь файл перетаскивают в окно и спрашивают вслух.

ЧТО ЧЕМ ЧИТАЕТСЯ
    Картинки уходят в Gemini Vision, который в проекте уже есть (vision/).
    Текст и код читаются как есть. PDF, Word и таблицы требуют по пакету на
    формат — каждый импортируется в момент использования, и отсутствие пакета
    отключает ровно один формат, а не инструмент целиком. Сообщение при этом
    называет, что именно поставить: «не могу прочитать PDF» без продолжения
    оставляет человека гадать.

ЧЕГО ЗДЕСЬ НЕТ
    Ни редактирования, ни конвертации, ни сжатия. Это не менеджер файлов —
    это «прочитай и расскажи». Перемещение, переименование и удаление живут в
    `file_controller`, вместе с отменой; складывать их сюда значило бы
    разделить работу с файлами по двум инструментам без всякой границы.
"""

import json
import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

# Сколько текста отдаём модели. Договор на сто страниц — это полмиллиона
# знаков; всё сразу и не нужно, и дорого.
_MAX_TEXT = 18000

# Сколько показываем, если модель недоступна и пересказывать нечем.
_PREVIEW = 600

_КАРТИНКИ = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
_ТЕКСТ = {".txt", ".md", ".log", ".csv", ".ini", ".cfg", ".yml", ".yaml", ".rst"}
_КОД = {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".rb",
        ".php", ".sh", ".ps1", ".sql", ".html", ".css", ".json", ".xml"}


def _тип(путь: Path) -> str:
    суффикс = путь.suffix.lower()
    if суффикс in _КАРТИНКИ:
        return "image"
    if суффикс == ".pdf":
        return "pdf"
    if суффикс in (".docx", ".doc"):
        return "docx"
    if суффикс in (".xlsx", ".xls"):
        return "excel"
    if суффикс == ".json":
        return "json"
    if суффикс in _КОД:
        return "code"
    if суффикс in _ТЕКСТ:
        return "text"
    return "unknown"


# ─── Чтение по форматам ───────────────────────────────────────────────────────

def _читать_текст(путь: Path) -> tuple[str, str]:
    try:
        return путь.read_text(encoding="utf-8", errors="replace")[:_MAX_TEXT], ""
    except Exception as exc:
        return "", f"файл не прочитался: {exc}"


def _читать_pdf(путь: Path) -> tuple[str, str]:
    try:
        import pdfplumber
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            return "", ("для PDF нужен пакет: pip install pdfplumber "
                        "(или PyPDF2)")
        try:
            куски = [(с.extract_text() or "") for с in PdfReader(str(путь)).pages[:40]]
            return "\n".join(куски)[:_MAX_TEXT], ""
        except Exception as exc:
            return "", f"PDF не разобрался: {exc}"

    try:
        with pdfplumber.open(str(путь)) as документ:
            куски = [(с.extract_text() or "") for с in документ.pages[:40]]
        текст = "\n".join(куски)[:_MAX_TEXT]
        if not текст.strip():
            # Скан без текстового слоя — частый случай, и человеку полезно
            # знать причину: это не «файл битый».
            return "", "в этом PDF нет текстового слоя — похоже, это скан"
        return текст, ""
    except Exception as exc:
        return "", f"PDF не разобрался: {exc}"


def _читать_docx(путь: Path) -> tuple[str, str]:
    try:
        import docx
    except ImportError:
        return "", "для Word нужен пакет: pip install python-docx"
    try:
        документ = docx.Document(str(путь))
        return "\n".join(а.text for а in документ.paragraphs)[:_MAX_TEXT], ""
    except Exception as exc:
        return "", f"документ не разобрался: {exc}"


def _читать_excel(путь: Path) -> tuple[str, str]:
    try:
        import openpyxl
    except ImportError:
        return "", "для таблиц нужен пакет: pip install openpyxl"
    try:
        книга = openpyxl.load_workbook(str(путь), read_only=True, data_only=True)
        строки = []
        for лист in книга.worksheets[:3]:
            строки.append(f"[Лист «{лист.title}»]")
            for номер, ряд in enumerate(лист.iter_rows(values_only=True)):
                if номер >= 80:
                    строки.append("…")
                    break
                строки.append("; ".join("" if я is None else str(я) for я in ряд))
        книга.close()
        return "\n".join(строки)[:_MAX_TEXT], ""
    except Exception as exc:
        return "", f"таблица не разобралась: {exc}"


def _читать_json(путь: Path) -> tuple[str, str]:
    сырое, беда = _читать_текст(путь)
    if беда:
        return "", беда
    try:
        разобранное = json.loads(сырое)
    except json.JSONDecodeError as exc:
        # Битый JSON — это ответ на вопрос, а не отказ: человек чаще всего
        # именно это и хочет узнать.
        return "", f"это не валидный JSON: строка {exc.lineno}, {exc.msg}"
    очертания = (f"{len(разобранное)} ключей верхнего уровня"
                 if isinstance(разобранное, dict) else
                 f"список из {len(разобранное)} элементов"
                 if isinstance(разобранное, list) else "одно значение")
    return f"[JSON: {очертания}]\n{сырое}", ""


_ЧИТАЛКИ = {
    "pdf": _читать_pdf, "docx": _читать_docx, "excel": _читать_excel,
    "json": _читать_json, "text": _читать_текст, "code": _читать_текст,
}


# ─── Картинки ─────────────────────────────────────────────────────────────────

def _разобрать_картинку(путь: Path, вопрос: str) -> str:
    try:
        from core.onboarding import ensure_gemini_key
        from vision.vision_analyzer import analyze_image
    except Exception as exc:
        return f"Модуль зрения недоступен, сэр: {exc}"

    ключ = ensure_gemini_key(interactive=False)
    if not ключ:
        return "Без ключа Gemini картинку я не разберу, сэр."

    try:
        данные = путь.read_bytes()
    except Exception as exc:
        return f"Не смог прочитать файл, сэр: {exc}"

    try:
        return analyze_image(данные, ключ, context="unknown", focus="general",
                             window_title=вопрос or путь.name)
    except Exception as exc:
        return f"Не разобрал картинку, сэр: {str(exc)[:100]}"


# ─── Пересказ текста ──────────────────────────────────────────────────────────

def _пересказать(текст: str, вопрос: str, имя: str) -> str:
    """Пересказ силами текстовой модели. Пустая строка — модели нет.

    Через `core.llm_client`, а не напрямую: когда у Gemini кончается квота,
    пересказ файла, пересказ ролика и резюме разговора замолкают
    одновременно — при том, что на этой же машине может стоять локальная
    модель, которой такая задача по силам."""
    from core import llm_client

    задача = (вопрос or "Расскажи, о чём этот файл и что в нём главное.")
    return llm_client.ask(
        f"Задача: {задача}\nФайл: {имя}\n\nСодержимое:\n{текст}",
        система=(
            "Ниже содержимое файла. Отвечай по-русски, обращайся «сэр», "
            "четыре-шесть предложений, без markdown и списков — это читают "
            "вслух. Не добавляй ничего, чего в файле нет; если ответа там нет, "
            "так и скажи."
        ),
        предел=700,
    )


def _начало(текст: str, предел: int = _PREVIEW) -> str:
    """Первые несколько строк для случая, когда пересказать нечем.

    Обрезка по последнему пробелу нужна, ТОЛЬКО если текст длиннее предела:
    иначе она отрезает последнее слово у файла, который целиком помещался, и
    «Здесь написано важное» превращается в «Здесь написано…»."""
    текст = " ".join(текст.split())
    if len(текст) <= предел:
        return текст
    return текст[:предел].rsplit(" ", 1)[0] + "…"


# ─── Точка входа ──────────────────────────────────────────────────────────────

def file_processor(parameters: dict, player=None) -> str:
    параметры = parameters or {}
    путь_строкой = str(параметры.get("file_path", "")).strip()
    вопрос = str(параметры.get("question", "")).strip()

    # Пусто — берём файл, который перетащили в окно. Ради этого сценария всё
    # и затевалось: человек бросает файл и спрашивает, не называя пути.
    if not путь_строкой and player is not None:
        путь_строкой = str(getattr(player, "current_file", "") or "")
    if not путь_строкой:
        return ("Какой файл разобрать, сэр? Перетащите его в окно "
                "или назовите путь.")

    путь = Path(путь_строкой).expanduser()
    if not путь.exists():
        return f"Файла нет по пути {путь}, сэр."
    if путь.is_dir():
        return "Это папка, сэр. Её содержимое покажет инструмент files."

    вид = _тип(путь)

    if вид == "image":
        ответ = _разобрать_картинку(путь, вопрос)
        if player:
            player.write_log(f"FILE: разобрал картинку {путь.name}")
        return ответ

    читалка = _ЧИТАЛКИ.get(вид)
    if читалка is None:
        return (f"Не знаю, как читать {путь.suffix or 'файл без расширения'}, сэр. "
                f"Умею: текст, код, PDF, Word, таблицы, JSON и картинки.")

    текст, беда = читалка(путь)
    if беда:
        return f"Не вышло, сэр: {беда}."
    if not текст.strip():
        return f"Файл {путь.name} пуст, сэр."

    if player:
        player.write_log(f"FILE: читаю {путь.name} ({len(текст)} знаков)")

    пересказ = _пересказать(текст, вопрос, путь.name)
    if пересказ:
        return пересказ

    # Модели нет — отдаём начало. Хуже пересказа, но честнее молчания.
    return (f"Пересказать не смог — модель недоступна. Начало файла "
            f"{путь.name}, сэр: {_начало(текст)}")


# ─── Объявление для реестра действий ──────────────────────────────────────────
TOOL = {
    "name": "file_processor",
    "description": (
        "Читает файл и отвечает по его содержимому: пересказывает документ, "
        "отвечает на вопрос по нему, описывает картинку. Понимает текст, код, "
        "PDF, Word, таблицы Excel, JSON и изображения. "
        "Вызывай, когда пользователь спрашивает «о чём этот файл», «что здесь "
        "написано», «найди в документе», «что на этой картинке» — особенно если "
        "он только что перетащил файл в окно: тогда file_path можно не указывать, "
        "возьмётся последний брошенный файл. "
        "Перемещение, переименование и удаление — это инструмент files, не этот."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": (
                    "Путь к файлу. Оставь пустым, чтобы взять файл, "
                    "перетащенный в окно."
                ),
            },
            "question": {
                "type": "STRING",
                "description": (
                    "Что именно спросили о файле: «есть ли тут про сроки», "
                    "«сколько строк», «что на фото». Пусто — общий пересказ."
                ),
            },
        },
        "required": [],
    },
    "handler": file_processor,
}
