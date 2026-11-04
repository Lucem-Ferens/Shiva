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


def parse_frame(buf: bytes) -> tuple[dict, bytes]:
    (n,) = struct.unpack(">I", buf[:4])
    return json.loads(buf[4:4 + n].decode("utf-8")), buf[4 + n:]


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
