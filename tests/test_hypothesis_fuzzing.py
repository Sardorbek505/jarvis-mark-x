"""
Property-based fuzzing suite using Hypothesis for JARVIS text parsing & wake-word gating.
Tests invariants, fuzzes random unicode, tests ReDoS (algorithmic complexity attacks),
and searches for unexpected crashes, hangs, or state inconsistencies.
"""

import time
from hypothesis import given, settings, strategies as st
from main import is_addressed_to_jarvis
from telegram_bot.bot import _clean_wake_word


# ─── INVARIANT 1: ReDoS & Complexity Resistance ─────────────────────────────

@given(st.text(alphabet=list("э -мнутакэй!? \t\n"), min_size=10, max_size=500))
@settings(max_examples=200, deadline=100)  # Each execution must finish within 100ms
def test_hypothesis_redos_resistance(repeated_prefix):
    """Проверка на ReDoS (катастрофический откат регулярки при повторах междометий)."""
    t0 = time.perf_counter()
    is_addressed_to_jarvis(repeated_prefix)
    is_addressed_to_jarvis(repeated_prefix + " джарвис поставь музыку")
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05, f"ReDoS detected! Parsing took {elapsed*1000:.1f}ms on input: {repeated_prefix!r}"


# ─── INVARIANT 2: Control Characters, Zero-Width & Unicode Bidi ──────────────

@given(
    st.text(alphabet=["\u200b", "\u200c", "\u200d", "\ufeff", "\u202e", "\x00", "\x1b", " "], min_size=1, max_size=20),
    st.sampled_from(["Джарвис", "Jarvis", "поставь музыку"])
)
@settings(max_examples=100)
def test_hypothesis_invisible_characters_and_bidi(invisibles, phrase):
    """Проверка устойчивости к невидимым символам, Zero-Width и RTL override."""
    dirty_text = f"{invisibles}{phrase}"
    # Функция не должна падать или выбрасывать необработанные исключения
    res = is_addressed_to_jarvis(dirty_text)
    assert isinstance(res, bool)


# ─── INVARIANT 3: Clean Wake Word Never Crashes or Returns None on Valid Text ──

@given(st.text(min_size=1, max_size=500))
@settings(max_examples=300)
def test_hypothesis_clean_wake_word_safety(arbitrary_text):
    """В Telegram очиститель wake-word обязан быть абсолютно безопасным для любого ввода."""
    result = _clean_wake_word(arbitrary_text)
    if arbitrary_text.strip():
        assert result is not None, f"Returned None on non-empty input: {arbitrary_text!r}"
        assert isinstance(result, str)


# ─── INVARIANT 4: Idempotency of Wake Word Cleaning ──────────────────────────

@given(st.text(min_size=1, max_size=200))
@settings(max_examples=200)
def test_hypothesis_idempotent_cleaning(text):
    """Повторная очистка текста обязана быть идемпотентной: clean(clean(t)) == clean(t)."""
    first_pass = _clean_wake_word(text)
    if first_pass:
        second_pass = _clean_wake_word(first_pass)
        assert first_pass == second_pass, (
            f"Non-idempotent cleaning!\nOriginal: {text!r}\nFirst: {first_pass!r}\nSecond: {second_pass!r}"
        )


# ─── INVARIANT 5: Sound Exclusion Invariant ──────────────────────────────────

@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="джарвисdjarvDJARVдЖаРвИс"), min_size=1, max_size=200))
@settings(max_examples=300)
def test_hypothesis_sound_exclusion(random_noise):
    """Если в строке нет букв из слова Джарвис, обращение ОБЯЗАНО вернуть False."""
    res = is_addressed_to_jarvis(random_noise)
    assert res is False, f"False positive on noise: {random_noise!r}"
