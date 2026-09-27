"""Резервная копия Джарвиса: всё личное — в один зашифрованный файл.

Новый ПК, переустановка Windows, сломанный диск — и не надо заново вписывать
ключи, собирать команды, контакты, расписание и знакомиться. Копия:
  • ключи и настройки (api_keys — как их видит окно «Ключи»);
  • свои команды, контакты, «Обо мне», учёба, будильники и таймеры,
    выученное «Джарвис», отпечаток голоса, подсказки;
  • память (факты, итоги разговоров) и настройки из config/.
НЕ входит: сессии Telegram (*.session — это полный доступ к аккаунту;
после восстановления — войти по QR заново), модели, кэш голоса, заметки
Obsidian (это ваша папка — копируйте её как обычно).

Файл шифруется паролем: scrypt → ключ → Fernet (AES + HMAC). Без пароля
не открыть и не подменить незаметно. Пароль нигде не хранится.
Перед восстановлением текущее состояние сохраняется рядом с копией тем же
паролем — «передумал» всегда можно откатить.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import zipfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MAGIC = b"JARVISBAK1\n"
SUFFIX = ".jarvisbak"
MIN_PASSWORD = 6
FILES = ["macros.json", "contacts.json", "about_me_state.json", "briefing_state.json", "voice_id.json",
         "study.json", "clock.json", "wake_aliases.json", "welcome_state.json", "current_mode.json"]
DIRS = {"memory": (".json", ".jsonl", ".db"), "config": (".json", ".db")}
SKIP = {"api_keys.json", "api_keys.example.json"}       # ключи — отдельно, через load/save_api_keys


class BackupError(Exception):
    pass


def _root() -> Path:
    from core.paths import get_data_root
    return Path(get_data_root())


def _key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    raw = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def encrypt(data: bytes, password: str) -> bytes:
    from cryptography.fernet import Fernet
    salt = os.urandom(16)
    return MAGIC + salt + Fernet(_key(password, salt)).encrypt(data)


def decrypt(blob: bytes, password: str) -> bytes:
    from cryptography.fernet import Fernet, InvalidToken
    if not blob.startswith(MAGIC):
        raise BackupError("Это не резервная копия Джарвиса.")
    salt, token = blob[len(MAGIC):len(MAGIC) + 16], blob[len(MAGIC) + 16:]
    try:
        return Fernet(_key(password, salt)).decrypt(token)
    except InvalidToken:
        raise BackupError("Неверный пароль или файл повреждён.") from None


def collect(root: Path) -> list[tuple[str, Path]]:
    """Что положить в копию: [(путь в архиве, файл)]."""
    out = [(name, root / name) for name in FILES if (root / name).is_file()]
    for d, exts in DIRS.items():
        base = root / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_file() and f.suffix in exts and f.name not in SKIP and ".session" not in f.name:
                out.append((f.relative_to(root).as_posix(), f))
    return out


def create(dest: Path, password: str, root: Path | None = None, keys: dict | None = None) -> dict:
    """Собрать копию в dest. keys — ключи (по умолчанию — load_api_keys)."""
    if len(password or "") < MIN_PASSWORD:
        raise BackupError(f"Пароль — хотя бы {MIN_PASSWORD} символов.")
    root = Path(root or _root())
    if keys is None:
        from core.paths import load_api_keys
        keys = load_api_keys()
    items = collect(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = {"version": 1, "created": datetime.now().isoformat(timespec="seconds"),
                    "files": [name for name, _p in items]}
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        z.writestr("api_keys.json", json.dumps(keys or {}, ensure_ascii=False, indent=1))
        for name, path in items:
            z.write(path, "data/" + name)
    dest = Path(dest)
    if dest.suffix != SUFFIX:
        dest = dest.with_name(dest.name + SUFFIX)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(encrypt(buf.getvalue(), password))
    tmp.replace(dest)
    logger.info("Резервная копия: %d файлов → %s", len(items), dest.name)
    return {"path": str(dest), "files": len(items), "keys": sum(1 for v in (keys or {}).values() if v)}


def inspect(src: Path, password: str) -> dict:
    """Что внутри копии (без восстановления) — для окна: дата и состав."""
    with zipfile.ZipFile(io.BytesIO(decrypt(Path(src).read_bytes(), password))) as z:
        m = json.loads(z.read("manifest.json"))
        keys = json.loads(z.read("api_keys.json"))
    return {"created": m.get("created", ""), "files": m.get("files", []),
            "keys": sum(1 for v in keys.values() if v)}


def _safe(name: str) -> bool:
    p = Path(name)
    return not p.is_absolute() and ".." not in p.parts and ".session" not in name


def restore(src: Path, password: str, root: Path | None = None, save_keys=None,
            safety_dir: Path | None = None, keys_now: dict | None = None) -> dict:
    """Вернуть всё из копии. Сначала — снимок текущего (тем же паролем) рядом с
    копией; keys_now — текущие ключи для снимка (по умолчанию — load_api_keys)."""
    root = Path(root or _root())
    data = decrypt(Path(src).read_bytes(), password)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.startswith("data/") and _safe(n[5:])]
        keys = json.loads(z.read("api_keys.json"))
        safety = Path(safety_dir or Path(src).parent) / f"jarvis-before-restore-{datetime.now():%Y-%m-%d-%H%M%S}"
        before = create(safety, password, root=root, keys=keys_now)
        for n in names:
            target = root / n[5:]
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".restore")
            tmp.write_bytes(z.read(n))
            tmp.replace(target)
    if keys:
        if save_keys is None:
            from core.paths import save_api_keys as save_keys
        save_keys(keys)
    _reload()
    logger.info("Восстановлено из копии: %d файлов", len(names))
    return {"files": len(names), "keys": sum(1 for v in keys.values() if v), "before": before["path"]}


def _reload():
    """Уже созданные хранилища перечитывают файлы (подключённые к ним голос,
    журнал и расписание остаются). Остальное подхватится после перезапуска."""
    import sys
    for mod, attr in (("core.macros", "_macros"), ("core.contacts", "_book"), ("core.study", "_study"),
                      ("core.voice_id", "_vid")):
        obj = getattr(sys.modules.get(mod), attr, None)
        if obj is not None:
            try:
                obj.load()
            except Exception as exc:
                logger.debug("Копия: %s не перечитался: %s", mod, exc)


def default_name() -> str:
    return f"jarvis-backup-{datetime.now():%Y-%m-%d}{SUFFIX}"
