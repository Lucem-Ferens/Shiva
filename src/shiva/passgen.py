"""Password generator (Qt-free): random characters and passphrases of EFF words.

The character sets and the choice to leave out look-alike letters (i, l, o, I, L, O) come from the Proton Pass generator
(proton-pass-common, GPL-3.0-or-later, proton-pass-common/src/password/password_generator.rs, main at b1a9edc, read
2026-11-13); the code is our own Python. Unlike that generator, a password has no fixed positions and no "never the same
character twice" rule: both lower the number of possible passwords. Instead every character is drawn uniformly from the
enabled alphabet, and the whole password is drawn again until each enabled class (capitals, digits, symbols) appears, so
the result is uniform over all passwords that contain every enabled class. Every random choice comes from `secrets`, the
operating system's random source.

The bit figures count how many equally likely passwords this module can make (exactly for characters, by inclusion and
exclusion). They say how hard the value is to guess if an attacker knows how it was made; they say nothing about reuse, or
about a password that was edited, copied or written down.
"""
from __future__ import annotations

import hashlib
import math
import secrets
from functools import lru_cache
from itertools import combinations

LOWER = "abcdefghjkmnpqrstuvwxyz"
UPPER = "ABCDEFGHJKMNPQRSTUVWXYZ"
DIGITS = "0123456789"
SYMBOLS = "!@#$%^&*"
SEPARATORS = ("-", " ", ".")

MIN_WORDS, MAX_WORDS, DEFAULT_WORDS = 4, 10, 6
MIN_LENGTH, MAX_LENGTH, DEFAULT_LENGTH = 8, 48, 20

# SHA-256 of the 7776 words, one per line with a final newline: a changed word list is refused, not used
WORDLIST_SHA256 = "6d557f0693958fb5e650b68b5bee585eb82cf4da32965505c789e924743bc522"


class WordListError(Exception):
    """The packaged word list is missing or is not the list this program expects."""


@lru_cache(maxsize=1)
def load_words() -> tuple[str, ...]:
    try:
        from .wordlist import WORDS
    except ImportError as ex:                                    # pragma: no cover - the module ships with the program
        raise WordListError("word list missing") from ex
    digest = hashlib.sha256(("\n".join(WORDS) + "\n").encode("ascii", "replace")).hexdigest()
    if len(WORDS) != 7776 or digest != WORDLIST_SHA256:
        raise WordListError("word list damaged")
    return WORDS


def words_available() -> bool:
    try:
        load_words()
        return True
    except WordListError:
        return False


def _classes(uppercase: bool, numbers: bool, symbols: bool) -> list[str]:
    return [c for on, c in ((uppercase, UPPER), (numbers, DIGITS), (symbols, SYMBOLS)) if on]


def _alphabet(uppercase: bool, numbers: bool, symbols: bool) -> str:
    return LOWER + "".join(_classes(uppercase, numbers, symbols))


def random_password(length: int = DEFAULT_LENGTH, uppercase: bool = False, numbers: bool = False,
                    symbols: bool = False) -> str:
    if not MIN_LENGTH <= length <= MAX_LENGTH:
        raise ValueError(f"length must be {MIN_LENGTH} to {MAX_LENGTH}")
    alphabet = _alphabet(uppercase, numbers, symbols)
    required = _classes(uppercase, numbers, symbols)
    while True:
        pw = "".join(alphabet[secrets.randbelow(len(alphabet))] for _ in range(length))
        if all(any(c in kind for c in pw) for kind in required):
            return pw


def random_password_bits(length: int = DEFAULT_LENGTH, uppercase: bool = False, numbers: bool = False,
                         symbols: bool = False) -> float:
    """log2 of the number of passwords of this length that contain every enabled class (inclusion and exclusion)."""
    alphabet = _alphabet(uppercase, numbers, symbols)
    required = _classes(uppercase, numbers, symbols)
    total = 0
    for k in range(len(required) + 1):
        for left_out in combinations(required, k):
            total += (-1) ** k * (len(alphabet) - sum(len(c) for c in left_out)) ** length
    return math.log2(total)


def passphrase(count: int = DEFAULT_WORDS, separator: str = "-", capitalise: bool = False,
               numbers: bool = False) -> str:
    """`count` words from the EFF list, joined by `separator` (one of SEPARATORS)."""
    if not MIN_WORDS <= count <= MAX_WORDS:
        raise ValueError(f"count must be {MIN_WORDS} to {MAX_WORDS}")
    if separator not in SEPARATORS:
        raise ValueError("separator not allowed")
    words = load_words()
    out: list[str] = []
    for _ in range(count):
        word = words[secrets.randbelow(len(words))]
        if capitalise:
            word = word[0].upper() + word[1:]
        if numbers:
            word += DIGITS[secrets.randbelow(len(DIGITS))]
        out.append(word)
    return separator.join(out)


def passphrase_bits(count: int = DEFAULT_WORDS, numbers: bool = False) -> float:
    """Words and the optional digit after each word; the separator and the capital add nothing."""
    return count * (math.log2(7776) + (math.log2(len(DIGITS)) if numbers else 0))
