"""Generate the Qt translation source (.ts) from the catalog table and compile it to .qm with Qt's lrelease.

  python tools/compile_translations.py           -> src/shiva/translations/shiva_zh_TW.ts and shiva_zh_TW.qm

The authoring table is src/shiva/translations/zh_TW.py (English source -> Traditional Chinese). The .ts is committed
(a translator can open it in Qt Linguist); the .qm is a build product (ignored by git). The app runs without the .qm and
then simply shows English. build.py calls build() and ships the .qm."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = ROOT / "src" / "shiva" / "translations"
sys.path.insert(0, str(ROOT / "src"))


def write_ts(ts: Path, lang: str = "zh_TW") -> int:
    from shiva.translations import zh_TW
    rows = ['<?xml version="1.0" encoding="utf-8"?>', "<!DOCTYPE TS>",
            f'<TS version="2.1" language="{lang}" sourcelanguage="en">', "<context>", "    <name>shiva</name>"]
    for src, dst in zh_TW.CATALOG.items():
        if "\n" in src or src != src.strip():
            raise SystemExit(f"catalog key must be one trimmed line: {src!r}")
        rows.append(f"    <message><source>{escape(src)}</source><translation>{escape(dst)}</translation></message>")
    rows += ["</context>", "</TS>", ""]
    ts.write_text("\n".join(rows), encoding="utf-8", newline="\n")
    return len(zh_TW.CATALOG)


def find_lrelease() -> str:
    import PySide6
    cand = Path(PySide6.__file__).parent / "lrelease.exe"
    if cand.is_file():
        return str(cand)
    found = shutil.which("pyside6-lrelease") or shutil.which("lrelease")
    if not found:
        raise SystemExit("lrelease not found (it ships with PySide6)")
    return found


def build(dest: Path = TRANS_DIR, lang: str = "zh_TW") -> Path:
    """Write shiva_<lang>.ts and shiva_<lang>.qm into dest; returns the .qm path."""
    dest.mkdir(parents=True, exist_ok=True)
    ts, qm = dest / f"shiva_{lang}.ts", dest / f"shiva_{lang}.qm"
    write_ts(ts, lang)
    p = subprocess.run([find_lrelease(), str(ts), "-qm", str(qm)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0 or not qm.is_file():
        raise SystemExit(f"lrelease failed: {p.stdout} {p.stderr}")
    return qm


def qt_base_qm(lang: str = "zh_TW") -> Path | None:
    """Qt's own translation (file dialog buttons, context menus) from the PySide6 wheel."""
    import PySide6
    p = Path(PySide6.__file__).parent / "translations" / f"qtbase_{lang}.qm"
    return p if p.is_file() else None


if __name__ == "__main__":
    out = build()
    print(f"wrote {out.with_suffix('.ts').name} and {out.name} in {out.parent.relative_to(ROOT)}")
