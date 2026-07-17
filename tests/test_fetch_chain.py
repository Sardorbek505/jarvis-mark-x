"""Action-chain FETCH directive: safety filter + parsing (item #9, chains).

The full chain (run PC command -> re-prompt) needs a live PC + Gemini, so here
we lock down the parts that must never regress: the safe/unsafe classifier and
the directive regex. bot.py imports config at module load, so we test the pure
pieces by importing the compiled regex/helper indirectly.
"""
import re

# Re-declare the contract here so the test fails loudly if bot.py's values drift
# away from what's documented. Kept in sync intentionally.
_RE_FETCH = re.compile(r"\[\[FETCH\]\](.*?)\[\[/FETCH\]\]", re.S | re.I)
_FETCH_UNSAFE = ("выключ", "выруб", "shutdown", "перезагруз", "reboot",
                 "заблокир", "lock", "удал", "delete", "снеси",
                 "громкост", "volume", "заверши процесс", "kill")


def _fetch_is_safe(cmd: str) -> bool:
    low = cmd.lower()
    return not any(w in low for w in _FETCH_UNSAFE)


def test_safe_read_commands_allowed():
    for cmd in ("системная информация", "что в папке загрузки",
                "список окон", "скриншот"):
        assert _fetch_is_safe(cmd), cmd


def test_destructive_commands_blocked():
    for cmd in ("выключи компьютер", "заблокируй экран", "удали файл report.txt",
                "shutdown now", "поставь громкость на 100", "reboot"):
        assert not _fetch_is_safe(cmd), cmd


def test_fetch_block_extracts_command():
    reply = "Сейчас гляну.\n[[FETCH]]\nчто в папке загрузки\n[[/FETCH]]"
    m = _RE_FETCH.search(reply)
    assert m
    assert m.group(1).strip() == "что в папке загрузки"
    assert _RE_FETCH.sub("", reply).strip() == "Сейчас гляну."


def test_no_fetch_block_is_noop():
    assert _RE_FETCH.search("обычный ответ без действий") is None


def test_bot_module_matches_this_contract():
    """Guard: the real bot.py constants must equal the ones tested here."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    bot_path = Path(__file__).resolve().parent.parent / "telegram_bot" / "bot.py"
    src = bot_path.read_text(encoding="utf-8")
    # the unsafe list and regex literal must be present verbatim
    assert 'r"\\[\\[FETCH\\]\\](.*?)\\[\\[/FETCH\\]\\]"' in src
    for word in _FETCH_UNSAFE:
        assert word in src, f"unsafe word {word!r} missing from bot.py FETCH filter"
