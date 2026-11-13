"""Worker subprocess: parses untrusted files away from the process that holds the passphrase.

Protocol (binary, stdin -> stdout): 4-byte big-endian header length, JSON header, then raw payload bytes.
Request header: {"op": "clean"|"scan", "format": str, "options": dict}. Response header:
{"ok": bool, "error": str|None, "kind": "error"|"unsupported"|None, "removed": [...], "kept": [...],
 "content_changed": [...], "warnings": [...], "categories": [...]} followed by the cleaned bytes (clean only)."""
from __future__ import annotations

import json
import struct
import sys

from . import CleanError, Unsupported, dispatch


def _frame(header: dict, payload: bytes = b"") -> bytes:
    h = json.dumps(header).encode("utf-8")
    return struct.pack(">I", len(h)) + h + payload


MAX_HEADER = 4 * 1024 * 1024        # a header larger than this is not an answer of ours
MAX_TEXT = 2000                      # longest string kept from a helper answer
MAX_ITEMS = 500                      # most list items kept from a helper answer


def parse_frame(buf: bytes) -> tuple[dict, bytes]:
    if len(buf) < 4:
        raise ValueError("short frame")
    (n,) = struct.unpack(">I", buf[:4])
    if n > MAX_HEADER or 4 + n > len(buf):
        raise ValueError("bad header length")
    header = json.loads(buf[4:4 + n].decode("utf-8"))
    if not isinstance(header, dict):
        raise ValueError("header is not an object")
    return header, buf[4 + n:]


def _text(v, limit: int = MAX_TEXT) -> str:
    return v[:limit] if isinstance(v, str) else ""


def _texts(v) -> list[str]:
    return [_text(x) for x in v[:MAX_ITEMS] if isinstance(x, str)] if isinstance(v, list) else []


def sanitize_response(h: dict) -> dict:
    """What the window keeps of a helper answer: only the fields it uses, with the types it expects, and bounded sizes.
    The helper parses untrusted files, so its answer is untrusted too."""
    kind = h.get("kind") if h.get("kind") in ("error", "unsupported") else None
    out = {"ok": h.get("ok") is True, "kind": kind}
    if _text(h.get("error")):                      # an empty message is left out, so callers use their own default
        out["error"] = _text(h.get("error"))
    if _text(h.get("code"), 64):
        out["code"] = _text(h.get("code"), 64)
    for key in ("removed", "kept", "content_changed", "warnings", "kept_unclean"):
        out[key] = _texts(h.get(key))
    cats = []
    for c in (h.get("categories") if isinstance(h.get("categories"), list) else [])[:MAX_ITEMS]:
        if isinstance(c, dict):
            cat = {str(k)[:64]: (_text(v) if isinstance(v, str) else v) for k, v in c.items()
                   if isinstance(v, (str, bool, int, float)) or v is None}
            if all(isinstance(cat.get(k), str) for k in ("key", "label", "detail")) and isinstance(cat.get("content_changing"), bool):
                cats.append(cat)                  # the category window needs these four; anything else is not a category
    out["categories"] = cats
    return out


def handle(buf: bytes) -> bytes:
    try:
        req, data = parse_frame(buf)
        fmt = req["format"]
        if req.get("op") == "scan":
            return _frame({"ok": True, "categories": dispatch.scan(fmt, data)})
        out = dispatch.clean_and_check(fmt, data, req.get("options") or {})
        return _frame({"ok": True, "removed": out.removed, "kept": out.kept, "content_changed": out.content_changed,
                       "warnings": out.warnings, "kept_unclean": out.kept_unclean}, out.data)
    except Unsupported as e:
        return _frame({"ok": False, "kind": "unsupported", "error": str(e), "code": e.code})
    except CleanError as e:
        return _frame({"ok": False, "kind": "error", "error": str(e), "code": e.code})
    except MemoryError:
        return _frame({"ok": False, "kind": "error", "error": "file too large to process safely"})
    except Exception as e:  # untrusted input: never let a parser bug look like success
        return _frame({"ok": False, "kind": "error", "error": f"internal error while cleaning ({type(e).__name__})"})


def main() -> int:
    buf = sys.stdin.buffer.read()
    sys.stdout.buffer.write(handle(buf))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
