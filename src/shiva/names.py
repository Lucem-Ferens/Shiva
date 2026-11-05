"""Windows-safe archive names. One rule set for both sides: whatever Shiva archives, Shiva can restore,
and a hostile archive cannot make Shiva write outside its staging folder or over an earlier entry."""
from __future__ import annotations

_INVALID = set('<>:"|?*\\/')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
_RESERVED |= {f"COM{c}" for c in "123456789¹²³"}
_RESERVED |= {f"LPT{c}" for c in "123456789¹²³"}
MAX_COMPONENT = 255
MAX_PATH = 259  # classic MAX_PATH; long-path support of the frozen exe is not relied on
STAGING_OVERHEAD = 12  # ".shiva-part" plus a separator: a restore writes into a hidden folder this much longer than the result


class UnsafeName(ValueError):
    pass


class PathTooLong(UnsafeName):
    pass


def check_component(name: str) -> None:
    if name in ("", ".", ".."):
        raise UnsafeName(f"invalid name: {name!r}")
    if len(name) > MAX_COMPONENT:
        raise UnsafeName(f"name longer than {MAX_COMPONENT} characters: {name[:40]!r}...")
    bad = [c for c in name if c in _INVALID or ord(c) < 32]
    if bad:
        raise UnsafeName(f"name contains a character Windows cannot use ({bad[0]!r}): {name!r}")
    if name.endswith((".", " ")):
        raise UnsafeName(f"name ends with a dot or space, which Windows silently strips: {name!r}")
    if name.split(".")[0].rstrip(" ").upper() in _RESERVED:
        raise UnsafeName(f"reserved Windows device name: {name!r}")


def split_rel(rel: str) -> list[str]:
    """Validate an archive path ('/'-separated, relative) and return its components."""
    if rel.startswith("/") or "\\" in rel or (len(rel) > 1 and rel[1] == ":"):
        raise UnsafeName(f"absolute or backslash path: {rel!r}")
    parts = rel.rstrip("/").split("/")
    for p in parts:
        check_component(p)
    return parts


class CaseGuard:
    """Detects names that collide on a case-insensitive file system, with NTFS-style one-to-one
    upper-casing (not casefold, and not str.upper(): both would wrongly equate 'ß' and 'ss')."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    @staticmethod
    def _simple_upper(s: str) -> str:
        # NTFS maps characters one to one; str.upper() would turn 'ß' into 'SS' (verified), so only
        # accept single-character mappings.
        return "".join(u if len(u := c.upper()) == 1 else c for c in s)

    def add(self, rel: str) -> None:
        key = self._simple_upper(rel.rstrip("/"))
        prior = self._seen.get(key)
        if prior is not None:
            raise UnsafeName(f"names differ only by case or are duplicates: {prior!r} and {rel!r}")
        self._seen[key] = rel


def check_length(base_len: int, rel: str, overhead: int = STAGING_OVERHEAD) -> None:
    """`base_len` is the length of the folder that holds `rel`. When archiving, that is the source folder and the
    restore will need `overhead` more (its hidden staging folder). When restoring, pass the staging folder's own
    length with overhead=0: the overhead is already in it, and counting it twice refuses archives that were accepted."""
    if base_len + 1 + len(rel) + overhead > MAX_PATH:
        raise PathTooLong(f"path would be longer than {MAX_PATH} characters when restored: {rel[:60]!r}...")
