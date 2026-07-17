"""
ДЖАРВИС — Интеграция с Obsidian (личный «мозг» на markdown-заметках).

Obsidian-vault — это обычная папка с `.md`-файлами, которую можно открыть
в приложении Obsidian. ДЖАРВИС пишет, ищет и читает заметки голосом.
Путь к vault и папкам берётся из `config/obsidian.json`.

Под-действия (`action`):
  write         — создать новую заметку (title + content)
  append_daily  — дописать строку в дневник за сегодня (content)
  search        — найти по всей базе (query)
  read          — прочитать заметку по заголовку (title)
  list          — список заметок (folder — опционально)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from core.storage import safe_read_json

_logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parent.parent
_CONFIG_FILE = _BASE / "config" / "obsidian.json"

_DEFAULT_CONFIG = {
    "vault_path": str(Path.home() / "Documents" / "JarvisVault"),
    "inbox_folder": "00-Inbox",
    "daily_folder": "Daily",
}

_MAX_SEARCH_RESULTS = 5
_SNIPPET_CHARS = 200
_FILENAME_MAX = 80
_READ_MAX_CHARS = 1500


# ── Конфигурация и пути ────────────────────────────────────────────────────
def _config() -> dict:
    """Читает конфиг vault, накладывая непустые значения поверх дефолтов."""
    cfg = dict(_DEFAULT_CONFIG)
    stored = safe_read_json(_CONFIG_FILE, default={})
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if v})
    return cfg


def _ensure_vault() -> tuple[Path, dict]:
    """Гарантирует, что vault и базовые папки существуют. Возвращает (vault, cfg)."""
    cfg = _config()
    vault = Path(cfg["vault_path"])
    for sub in ("", cfg["inbox_folder"], cfg["daily_folder"]):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    return vault, cfg


def _slugify(title: str) -> str:
    """Превращает заголовок в безопасное имя файла для Windows."""
    cleaned = re.sub(r'[<>:"/\\|?*\n\r\t]', " ", title or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = datetime.now().strftime("Заметка %Y-%m-%d %H-%M")
    return cleaned[:_FILENAME_MAX].strip()


def _unique_path(folder: Path, slug: str) -> Path:
    """Возвращает путь `<slug>.md`, при коллизии добавляет ` (2)`, ` (3)`…"""
    candidate = folder / f"{slug}.md"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{slug} ({counter}).md"
        counter += 1
    return candidate


def _write_md(path: Path, text: str) -> None:
    """Атомарная запись markdown: пишем в .tmp → replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _strip_frontmatter(text: str) -> str:
    """Убирает YAML-фронтматтер (--- … ---) из начала заметки."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip()
    return text


def _iter_notes(vault: Path):
    """Ленивый обход всех .md файлов vault (кроме служебного .obsidian)."""
    for path in vault.rglob("*.md"):
        if ".obsidian" in path.parts:
            continue
        yield path


# ── Под-действия ───────────────────────────────────────────────────────────
def _do_write(title: str, content: str, folder: str, cfg: dict, vault: Path) -> str:
    if not content and not title:
        return "Сэр, нечего записывать — не хватает текста заметки."

    target_folder = vault / (folder.strip() if folder else cfg["inbox_folder"])
    target_folder.mkdir(parents=True, exist_ok=True)

    display_title = (title or content or "").strip()
    slug = _slugify(title or content[:_FILENAME_MAX])
    path = _unique_path(target_folder, slug)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = content.strip() if content else ""
    note = (
        f"---\n"
        f"created: {stamp}\n"
        f"source: jarvis\n"
        f"---\n\n"
        f"# {display_title[:120]}\n\n"
        f"{body}\n"
    )
    _write_md(path, note)
    _logger.info("Obsidian: заметка сохранена → %s", path.name)
    return f"Записал в базу знаний: «{display_title[:80]}»."


def _do_append_daily(content: str, cfg: dict, vault: Path) -> str:
    if not content:
        return "Сэр, что именно добавить в дневник?"

    today = datetime.now().strftime("%Y-%m-%d")
    path = vault / cfg["daily_folder"] / f"{today}.md"

    line = f"- {datetime.now().strftime('%H:%M')} {content.strip()}\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        _write_md(path, existing.rstrip() + "\n" + line)
    else:
        header = f"# {today}\n\n## Журнал\n\n"
        _write_md(path, header + line)
    _logger.info("Obsidian: запись в дневник %s", today)
    return "Добавил в дневник за сегодня."


def _do_search(query: str, vault: Path) -> str:
    if not query:
        return "Сэр, уточните, что искать в базе знаний."

    needle = query.lower().strip()
    hits: list[tuple[str, str]] = []
    for path in _iter_notes(vault):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        haystack = (path.stem + "\n" + text).lower()
        if needle in haystack:
            body = _strip_frontmatter(text).replace("\n", " ").strip()
            idx = body.lower().find(needle)
            start = max(0, idx - 40)
            snippet = body[start : start + _SNIPPET_CHARS].strip()
            hits.append((path.stem, snippet))
        if len(hits) >= _MAX_SEARCH_RESULTS:
            break

    if not hits:
        return f"По запросу «{query}» в базе знаний ничего не нашёл."

    lines = [f"Нашёл {len(hits)} заметок по «{query}»:"]
    for title, snippet in hits:
        lines.append(f"• {title}: {snippet}")
    return "\n".join(lines)


def _do_read(title: str, vault: Path) -> str:
    if not title:
        return "Сэр, какую заметку прочитать?"

    needle = title.lower().strip()
    best: Path | None = None
    for path in _iter_notes(vault):
        stem = path.stem.lower()
        if needle in stem:
            best = path
            if stem == needle:
                break
    if best is None:
        return f"Заметку «{title}» не нашёл в базе знаний."

    try:
        text = _strip_frontmatter(best.read_text(encoding="utf-8")).strip()
    except OSError as e:
        _logger.warning("Obsidian: не удалось прочитать %s: %s", best.name, e)
        return f"Не удалось прочитать заметку «{best.stem}»."

    if len(text) > _READ_MAX_CHARS:
        text = text[:_READ_MAX_CHARS] + "…"
    return f"Заметка «{best.stem}»:\n{text}"


def _do_list(folder: str, vault: Path) -> str:
    root = vault / folder.strip() if folder else vault
    if not root.exists():
        return f"Папки «{folder}» в базе знаний нет."

    titles = [p.stem for p in _iter_notes(root)]
    if not titles:
        return "В базе знаний пока нет заметок."

    shown = titles[:15]
    suffix = f" и ещё {len(titles) - 15}" if len(titles) > 15 else ""
    return f"В базе знаний {len(titles)} заметок: " + ", ".join(shown) + suffix + "."


# ── Точка входа ────────────────────────────────────────────────────────────
def obsidian_action(parameters: dict, player=None) -> str:
    """Единый экшен для работы с Obsidian-vault. Возвращает фразу для озвучки."""
    action = (parameters.get("action") or "").strip().lower()
    title = (parameters.get("title") or "").strip()
    content = (parameters.get("content") or "").strip()
    query = (parameters.get("query") or "").strip()
    folder = (parameters.get("folder") or "").strip()

    try:
        vault, cfg = _ensure_vault()
    except OSError as e:
        _logger.error("Obsidian: не удалось создать vault: %s", e)
        return "Сэр, не удалось получить доступ к базе знаний Obsidian."

    if action == "write":
        return _do_write(title, content, folder, cfg, vault)
    if action == "append_daily":
        return _do_append_daily(content, cfg, vault)
    if action == "search":
        return _do_search(query, vault)
    if action == "read":
        return _do_read(title, vault)
    if action == "list":
        return _do_list(folder, vault)

    return (
        "Не понял действие для базы знаний. "
        "Доступно: записать, добавить в дневник, найти, прочитать, список."
    )
