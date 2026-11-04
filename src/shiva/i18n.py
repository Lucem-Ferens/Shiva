"""Interface language. Basic Traditional Chinese (zh_TW) now; a full translation and German come later.

English is the source language. Two entry points, one engine:
  tr(text)   literal strings written in gui.py (labels, tooltips, dialog texts)
  dyn(text)  strings built at run time by ui_model, the cleaners and the pipeline (row notes, result texts, labels,
             warnings); the engine strings themselves stay English so logic and tests do not depend on wording
  trf(text, *args)  a literal with %1, %2 ... placeholders, formatted after translation

Lookup order for one sentence (all through the Qt translator, context "shiva"):
  1. the sentence itself        2. a template with %1 / %T1 / %N1 placeholders (%T1 = capture translated too, lists allowed; %N1 = a number)
  3. the sentence with counts ("3 files") replaced by %U, then %U filled with the translated count.
Multi-line and multi-sentence text is translated line by line and sentence by sentence; an unknown piece stays English.
Without an installed translator everything returns its input unchanged (English).

Stable message keys will replace this display-time matching later. PySide6 is imported lazily so the
Qt-free modules may import this one."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

CONTEXT = "shiva"
SUPPORTED = ("en", "zh_TW")
_active = False
_translators: list = []
_lang = "en"
_templates: list | None = None

_PH = re.compile(r"%([TNMR]?)(\d)")
_UNIT = re.compile(r"\b(\d+) (file|item|character|original)s?\b")
_SENT = re.compile(r"(?<=[.!?])\s+")
_END_CJK = re.compile(r"[。！？；：」）]$")


def N_(text: str) -> str:
    """Marks a string for the catalog without translating it now (like gettext's noop)."""
    return text


def active() -> bool:
    return _active


def language() -> str:
    return _lang


# ---------------------------------------------------------------- installation
def _qm_dirs() -> list[Path]:
    here = Path(__file__).resolve().parent
    dirs = [here / "translations"]
    for base in (Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else None,
                 Path(sys.executable).resolve().parent):
        if base is not None and (base / "translations") not in dirs:
            dirs.append(base / "translations")
    return dirs


def choose_language(requested: str | None = None) -> str:
    """SHIVA_LANG (en / zh_TW) overrides the OS interface language. Anything unknown means English."""
    raw = (requested or os.environ.get("SHIVA_LANG") or "").strip().replace("-", "_")
    if not raw:
        try:
            from PySide6.QtCore import QLocale
            langs = QLocale.system().uiLanguages() or [QLocale.system().name()]
            raw = langs[0].replace("-", "_")
        except Exception:
            raw = ""
    low = raw.lower()
    if low.startswith("zh") and not any(t in low for t in ("cn", "hans", "sg")):
        return "zh_TW"          # zh_TW, zh_HK, zh_MO, zh_Hant...: Traditional Chinese
    return "en"


def install(requested: str | None = None, search_dirs: list[Path] | None = None) -> str:
    """Install the translator for the chosen language. Returns the language that is really active: 'en' when
    the language is English or its compiled catalog (shiva_<lang>.qm) was not found."""
    global _active, _lang
    uninstall()
    lang = choose_language(requested)
    if lang == "en":
        return "en"
    from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator
    dirs = list(search_dirs) if search_dirs is not None else _qm_dirs()
    ours = next((d / f"shiva_{lang}.qm" for d in dirs if (d / f"shiva_{lang}.qm").is_file()), None)
    if ours is None:
        return "en"
    t = QTranslator()
    if not t.load(str(ours)):
        return "en"
    QCoreApplication.installTranslator(t); _translators.append(t)
    qt_dirs = dirs + [Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))]
    qtbase = next((d / f"qtbase_{lang}.qm" for d in qt_dirs if (d / f"qtbase_{lang}.qm").is_file()), None)
    if qtbase is not None:                      # Qt's own texts: file dialog buttons, context menus
        qt = QTranslator()
        if qt.load(str(qtbase)):
            QCoreApplication.installTranslator(qt); _translators.append(qt)
    _active, _lang = True, lang
    return lang


def uninstall() -> None:
    global _active, _lang, _templates
    _templates = None
    if _translators:
        from PySide6.QtCore import QCoreApplication
        for t in _translators:
            QCoreApplication.removeTranslator(t)
    _translators.clear()
    _active, _lang = False, "en"


# ---------------------------------------------------------------- lookup
def _exact(s: str) -> str | None:
    from PySide6.QtCore import QCoreApplication
    t = QCoreApplication.translate(CONTEXT, s)
    return t if t != s else None


class _Template:
    def __init__(self, src: str):
        self.src = src
        rx, pos, self.kinds = "^", 0, []
        for mt in _PH.finditer(src):
            # %N1 = a number (digits, optional +); %M1 / %R1 = any text, several sentences allowed (M translated, R raw);
            # %1 / %T1 = text inside one sentence and one "; " part (so a template never swallows its neighbours)
            kind = mt.group(1)
            rx += re.escape(src[pos:mt.start()]) + (r"(\d+\+?)" if kind == "N" else r"(.+?)" if kind in ("M", "R")
                                                    else r"((?:(?!; |\. ).)+?)")
            self.kinds.append((kind in ("T", "M"), int(mt.group(2))))
            pos = mt.end()
        self.rx = re.compile(rx + re.escape(src[pos:]) + "$", re.S)
        self.weight = len(_PH.sub("", src))      # more literal text = more specific, tried first


def _load_templates() -> list:
    global _templates
    if _templates is None:
        if _lang == "zh_TW":
            from .translations.zh_TW import CATALOG
        else:
            CATALOG = {}
        _templates = sorted((_Template(k) for k in CATALOG if _PH.search(k)), key=lambda t: -t.weight)
    return _templates


def _fill(text: str, vals: dict[int, str]) -> str:
    return _PH.sub(lambda mt: vals.get(int(mt.group(2)), mt.group(0)), text)


def _via_templates(s: str) -> str | None:
    for tp in _load_templates():
        mt = tp.rx.match(s)
        if mt is None:
            continue
        t = _exact(tp.src)
        if t is None:
            continue
        vals = {}
        for (translate, n), cap in zip(tp.kinds, mt.groups()):
            vals[n] = _items(cap) if translate else cap
        return _fill(t, vals)
    return None


def _unit_text(n: str, noun: str) -> str:
    t = _exact(f"unit:{noun}")
    return t.replace("%1", n) if t else f"{n} {noun}{'' if n == '1' else 's'}"


def _one(s: str) -> str:
    """One sentence or phrase. Unknown text is returned unchanged."""
    if not s.strip():
        return s
    for cand, tail in ((s, ""), (s[:-1], "。") if s.endswith(".") else (None, "")):
        if cand is None:
            continue
        t = _exact(cand) or _via_templates(cand)
        if t is not None:
            return t + tail
    units = [(mt.group(1), mt.group(2)) for mt in _UNIT.finditer(s)]
    if units:
        norm = _UNIT.sub("%U", s)
        for cand, tail in ((norm, ""), (norm[:-1], "。") if norm.endswith(".") else (None, "")):
            if cand is None:
                continue
            t = _exact(cand) or _via_templates(cand)
            if t is not None:
                it = iter(units)
                t = re.sub(r"%U\d?", lambda _m: _unit_text(*next(it)), t)
                return t + tail
    if len(_SENT.split(s)) > 1:                 # several sentences: _line() translates them one by one
        return s
    if "; " in s:                               # "Cloud-synced folder: ...; SSD: ..." (several notes in one line)
        body, dot = (s[:-1], True) if s.endswith(".") else (s, False)
        parts = body.split("; ")
        out = [_one(p) for p in parts]
        if out != parts:
            return "；".join(out) + ("。" if dot else "")
    if ", " in s:                               # "2 done, 1 failed." (only when every part is known)
        body, dot = (s[:-1], True) if s.endswith(".") else (s, False)
        parts = body.split(", ")
        out = [_one(p) for p in parts]
        if all(o != p for o, p in zip(out, parts)):
            return "、".join(out) + ("。" if dot else "")
    return s


def _join(pieces: list[str]) -> str:
    out = ""
    for p in pieces:
        out += ("" if (not out or _END_CJK.search(out)) else " ") + p
    return out


def _line(line: str) -> str:
    t = _one(line)
    if t != line:
        return t
    pieces = _SENT.split(line)
    if len(pieces) > 1:
        return _join([_one(p) for p in pieces])
    return line


def _items(s: str) -> str:
    """A captured value that may be one phrase or a ', '-separated list (longest known label first)."""
    t = _line(s)
    if t != s or ", " not in s:
        return t
    parts, out, i = s.split(", "), [], 0
    while i < len(parts):
        for j in range(len(parts), i, -1):
            cand = ", ".join(parts[i:j])
            tc = _one(cand)
            if tc != cand or j == i + 1:
                out.append(tc); i = j
                break
    return "、".join(out)


def tr(text: str) -> str:
    """Translate a literal (line by line, sentence by sentence). English when no translator is installed."""
    if not _active or not text:
        return text
    return "\n".join(_line(ln) for ln in text.split("\n"))


dyn = tr        # same engine; the name says "this string was built at run time by the model or the engine"


def trf(text: str, *args) -> str:
    """tr() of a template with %1, %2 ..., then the arguments are put in (so they are never translated)."""
    return _fill(tr(text), {i: str(a) for i, a in enumerate(args, 1)})
