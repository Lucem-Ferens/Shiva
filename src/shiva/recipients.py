"""Recipient public keys typed or pasted by the user (Qt-free).

Only the shape of a key is checked here: prefix and character set decide the
kind. The checksum and everything else is left to age, which rejects a
malformed recipient when the file is encrypted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

STANDARD = "standard"
POST_QUANTUM = "post-quantum"

# Characters age uses after the "age1" prefix (the Bech32 data alphabet plus the separator "1").
_BECH32 = frozenset("qpzry9x8gf2tvdw0s3jn54khce6mua7l")
_BOM = chr(0xFEFF)
_INVISIBLE = {0x200B: None, 0x200C: None, 0x200D: None, 0x2060: None, 0xFEFF: None}   # zero-width characters
_PLUGIN_NAME =frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_.")   # ASCII only
_SECRET_PREFIX ="AGE-SECRET-KEY-"
_SSH_PREFIXES = ("ssh-", "ecdsa-", "sk-ssh-", "sk-ecdsa-")
_PEM_SECRET = "PRIVATE KEY-----"     # the end of "-----BEGIN OPENSSH PRIVATE KEY-----" and its RSA / EC / encrypted variants


def _has_secret(text: str) -> bool:
    t = text.translate(_INVISIBLE).upper()
    return _SECRET_PREFIX in t or _PEM_SECRET in t


@dataclass(frozen=True)
class Recipient:
    kind: str           # STANDARD or POST_QUANTUM
    text: str
    source: str = "pasted"


@dataclass(frozen=True)
class Problem:
    line: int           # 1-based line in the parsed text
    code: str           # secret_key | ssh_key | plugin_key | unrecognised | duplicate


@dataclass
class ParseResult:
    keys: list[Recipient] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


def is_secret_key(text: str) -> bool:
    """True when any line looks like a private key, so the caller can refuse and clear it."""
    return _has_secret(text)


def classify(line: str) -> str:
    """Return STANDARD, POST_QUANTUM, or a problem code for one trimmed, non-comment line."""
    if _has_secret(line):       # anywhere in the line: quoted, labelled, in backticks
        return "secret_key"
    if line.startswith(_SSH_PREFIXES):
        return "ssh_key"
    if not line.startswith("age1") or len(line) <= 4:
        return "unrecognised"
    rest = line[4:]
    if "1" not in rest:
        return STANDARD if set(rest) <= _BECH32 else "unrecognised"
    # "age1<name>1<data>": a plugin name may use any lowercase letter or digit, the data part may not.
    name, _, data = rest.rpartition("1")
    if not (name and data and set(name) <= _PLUGIN_NAME and set(data) <= _BECH32):
        return "unrecognised"
    return POST_QUANTUM if name == "pq" else "plugin_key"


def kind_of(key: str) -> str | None:
    """STANDARD or POST_QUANTUM for a usable recipient, else None."""
    k = classify(key.strip())
    return k if k in (STANDARD, POST_QUANTUM) else None


def parse_recipients(text: str, source: str = "pasted", existing: tuple[str, ...] = ()) -> ParseResult:
    """Split text into recipients, one per line; blank lines and "#" comments are ignored.

    Keys in `existing`, and repeats inside the text, are reported as duplicates and not added.
    """
    out = ParseResult()
    seen = set(existing)
    for n, raw in enumerate(text.lstrip(_BOM).splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        k = classify(line)
        if k not in (STANDARD, POST_QUANTUM):
            out.problems.append(Problem(n, k))
        elif line in seen:
            out.problems.append(Problem(n, "duplicate"))
        else:
            seen.add(line)
            out.keys.append(Recipient(k, line, source))
    return out


# Every recipient goes on age's command line as "-r <key>"; Windows allows about 32,767 characters in all.
MAX_RECIPIENT_CHARS = 30000


def too_long(keys) -> bool:
    """True when the keys (Recipient objects or key text) cannot fit on one command line."""
    return sum(len(getattr(k, "text", k)) + 4 for k in keys) > MAX_RECIPIENT_CHARS


def mixed(keys: list[Recipient]) -> bool:
    """age cannot encrypt one file to both standard and post-quantum recipients."""
    return len({k.kind for k in keys}) > 1


def shorten(key: str) -> str:
    """Display form of a long key: the start and the end, never the whole text."""
    return key if len(key) <= 24 else key[:12] + "..." + key[-6:]
