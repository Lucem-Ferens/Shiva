"""The password generator: character rules, passphrases, exact bit counts, the word list."""
import hashlib
import itertools
import math

import pytest

from shiva import passgen, wordlist

LOOKALIKES = set("iloILO")


def test_wordlist_is_the_eff_list():
    w = wordlist.WORDS
    assert len(w) == 7776 and len(set(w)) == 7776
    assert w[0] == "abacus" and w[-1] == "zoom"
    assert all(x.isascii() and x == x.lower() and 3 <= len(x) <= 9 for x in w)
    assert sorted(x for x in w if "-" in x) == ["drop-down", "felt-tip", "t-shirt", "yo-yo"]
    # one word per line (the source file without its dice numbers); catches an accidental edit of the generated module
    digest = hashlib.sha256(("\n".join(w) + "\n").encode("ascii")).hexdigest()
    assert digest == passgen.WORDLIST_SHA256
    assert passgen.words_available() and passgen.load_words() is w


def test_a_changed_word_list_is_refused(monkeypatch):
    passgen.load_words.cache_clear()
    monkeypatch.setattr(wordlist, "WORDS", wordlist.WORDS[:-1] + ("zzzz",))
    try:
        with pytest.raises(passgen.WordListError):
            passgen.load_words()
        assert not passgen.words_available()
        with pytest.raises(passgen.WordListError):
            passgen.passphrase(6)
    finally:
        passgen.load_words.cache_clear()


@pytest.mark.parametrize("flags", list(itertools.product([False, True], repeat=3)))
@pytest.mark.parametrize("length", [8, 20, 48])
def test_random_password_rules(length, flags):
    up, num, sym = flags
    allowed = set(passgen._alphabet(up, num, sym))
    for _ in range(150):
        p = passgen.random_password(length, up, num, sym)
        assert len(p) == length and set(p) <= allowed and not set(p) & LOOKALIKES
        assert (not up or any(c in passgen.UPPER for c in p)) and (not num or any(c in passgen.DIGITS for c in p))
        assert not sym or any(c in passgen.SYMBOLS for c in p)   # every enabled class is present


def test_defaults_are_lower_case_only():
    p = passgen.random_password()
    assert len(p) == 20 and set(p) <= set(passgen.LOWER)


def test_random_password_length_limits():
    for bad in (0, 7, 49):
        with pytest.raises(ValueError):
            passgen.random_password(bad)


def test_two_passwords_differ():
    assert len({passgen.random_password() for _ in range(50)}) == 50


def test_passphrase_words_and_separators():
    words = set(wordlist.WORDS)
    p = passgen.passphrase(6, separator=" ")
    assert len(p.split(" ")) == 6 and set(p.split(" ")) <= words
    p = passgen.passphrase(4, separator=".", capitalise=True, numbers=True)
    parts = p.split(".")
    assert len(parts) == 4 and all(x[0].isupper() and x[-1].isdigit() and x[:-1].lower() in words for x in parts)
    assert passgen.passphrase().count("-") >= 5                  # default: six words, hyphen
    for bad in (dict(separator=","), dict(count=3), dict(count=11)):
        with pytest.raises(ValueError):
            passgen.passphrase(**bad)


def test_bits_of_a_passphrase():
    assert passgen.passphrase_bits(6) == pytest.approx(6 * math.log2(7776))
    assert 77 < passgen.passphrase_bits(6) < 78
    assert passgen.passphrase_bits(6, numbers=True) == pytest.approx(6 * math.log2(7776) + 6 * math.log2(10))


def _count_by_brute_force(length, alphabet, classes):
    return sum(all(any(c in k for c in s) for k in classes) for s in itertools.product(alphabet, repeat=length))


def test_character_bits_match_a_brute_force_count_on_a_tiny_alphabet(monkeypatch):
    # shrink the alphabet so every string can be listed: 3 lower-case letters, 2 digits, 2 symbols, length 4
    monkeypatch.setattr(passgen, "LOWER", "abc"); monkeypatch.setattr(passgen, "UPPER", "AB")
    monkeypatch.setattr(passgen, "DIGITS", "01"); monkeypatch.setattr(passgen, "SYMBOLS", "!@")
    for flags in itertools.product([False, True], repeat=3):
        alphabet = passgen._alphabet(*flags)
        classes = passgen._classes(*flags)
        want = _count_by_brute_force(4, alphabet, classes)
        got = 2 ** passgen.random_password_bits(4, *flags)
        assert got == pytest.approx(want), flags


def test_character_bits_for_the_real_alphabets():
    assert passgen.random_password_bits(20) == pytest.approx(20 * math.log2(23))       # about 90.5
    assert 90 < passgen.random_password_bits(20) < 91
    # forcing the classes removes a little, never adds
    full = passgen.random_password_bits(20, True, True, True)
    assert full < 20 * math.log2(len(passgen._alphabet(True, True, True)))
    assert full > 20 * math.log2(len(passgen._alphabet(True, True, True))) - 1
    assert passgen.random_password_bits(21) > passgen.random_password_bits(20)


def test_random_password_covers_the_whole_alphabet():
    seen = set()
    for _ in range(400):
        seen |= set(passgen.random_password(48, True, True, True))
    assert seen == set(passgen._alphabet(True, True, True))


def test_no_use_of_the_random_module():
    src = open(passgen.__file__, encoding="utf-8").read()
    assert "import random" not in src and "from random" not in src
