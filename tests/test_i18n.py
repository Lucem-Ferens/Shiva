"""Basic Traditional Chinese interface: catalog coverage, the translation engine, and the window in zh_TW.
All other tests run in English; every test here that installs zh_TW removes it again."""
import ast
import os
import sys
from pathlib import Path

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from shiva import i18n, jobs, steps, storage  # noqa: E402
from shiva import ui_model as m  # noqa: E402
from shiva.gui import ConfirmDialog, MainWindow, PdfDialog  # noqa: E402
from shiva.metadata import office, pdf  # noqa: E402

QApplication.instance() or QApplication([])


def has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)


@pytest.fixture(scope="session")
def qm_dir(tmp_path_factory):
    import compile_translations
    d = tmp_path_factory.mktemp("translations")
    compile_translations.build(d)
    return d


@pytest.fixture
def zh(qm_dir):
    assert i18n.install("zh_TW", [qm_dir]) == "zh_TW"
    yield i18n
    i18n.uninstall()


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    w = MainWindow()
    yield w
    w._pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------- engine
def test_english_is_untouched_without_a_translator():
    assert not i18n.active()
    assert i18n.tr("Add files") == "Add files"
    assert i18n.dyn("Clean 3 files") == "Clean 3 files"
    assert i18n.trf("Working on %N1 of %N2: %3", 1, 2, "a.txt") == "Working on 1 of 2: a.txt"


def test_choose_language():
    for raw in ("zh_TW", "zh-TW", "zh-Hant-TW", "zh_HK", "zh"):
        assert i18n.choose_language(raw) == "zh_TW"
    for raw in ("en", "en_US", "de", "zh_CN", "zh-Hans-CN", "xx"):
        assert i18n.choose_language(raw) == "en"


def test_missing_catalog_means_english(tmp_path):
    assert i18n.install("zh_TW", [tmp_path]) == "en"
    assert not i18n.active() and i18n.tr("Add files") == "Add files"


def test_catalog_has_clean_keys():
    from shiva.translations.zh_TW import CATALOG
    for k, v in CATALOG.items():
        assert k == k.strip() and "\n" not in k, k
        assert v.strip() and "\n" not in v, k
        assert "安全刪除" not in v, "shredding is best-effort; never claim secure deletion"
    # a placeholder in the translation must exist in the source
    import re
    for k, v in CATALOG.items():
        src = set(re.findall(r"%[TNMR]?(\d)", k))
        assert set(re.findall(r"%(\d)", v)) <= src or k.startswith("unit:"), k


def test_shred_refusal_texts_are_translated(zh):
    from shiva import shred
    for text in shred.REFUSAL_TEXT.values():
        assert zh.dyn(text) != text and not any(c.isascii() and c.isalpha() for c in zh.dyn(text).replace("Shiva", ""))
        out = zh.dyn(f"Not shredded: {text} Nothing was changed.")
        assert out.startswith("未銷毀：") and out.endswith("沒有任何變更。")


def test_units_plurals_and_lists(zh):
    assert zh.dyn("Clean 1 file") == "清理 1 個檔案"
    assert zh.dyn("Clean 3 files") == "清理 3 個檔案"
    assert zh.dyn("Shred 2 items...") == "銷毀 2 個項目..."
    assert zh.dyn("Encrypt 3 items and decrypt 2") == "加密 3 個項目，並解密 2 個"
    assert zh.dyn("1 done, 2 failed.") == "完成 1 個、失敗 2 個。"
    # a list whose items contain commas is matched longest label first
    out = zh.dyn("Removed: Page labels, output intents and web-capture data, Document ID")
    assert "Page labels" not in out and "文件 ID" in out
    # the JPEG re-encode notes: one item holds commas and still translates as a whole
    assert zh.dyn("Removed: Exif camera/date, padding, compression tables and hidden bytes in the image data") \
        == "已移除：Exif 相機/日期資料、影像資料中的填充、壓縮表與隱藏的位元組"
    assert zh.dyn("Changed: JPEG re-encoded (lossy), image rotated to match its Exif orientation") \
        == "已變更：JPEG 已重新編碼（有損）、圖片已依 Exif 旋轉方向轉正"
    # file names are never translated, even when they look like a count
    assert zh.dyn("Saved as 3 files.txt. Original kept.") == "已存為 3 files.txt。已保留原始檔。"
    # unknown text stays English
    assert zh.dyn("Something unexpected") == "Something unexpected"


# ---------------------------------------------------------------- coverage of the sources
def _wrapped_literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("tr", "trf", "dyn"):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                out.append((node.lineno, node.args[0].value))
    return out


def _covered(text: str) -> bool:
    for line in text.split("\n"):
        if line.strip() and i18n._line(line) == line:
            return False
    return True


GUI_FILES = sorted((ROOT / "src" / "shiva" / "gui").glob("*.py"))
GUI_WRAPPED_LITERALS = 136  # total over the package; a split or a move must not lose a file or a literal


def test_every_literal_wrapped_in_gui_has_an_entry(zh):
    assert len(GUI_FILES) >= 5
    lits = [x for f in GUI_FILES for x in _wrapped_literals(f)]
    assert len(lits) == GUI_WRAPPED_LITERALS
    missing = [(n, t) for n, t in lits if t.strip() and not _covered(t)]
    assert not missing, missing
    from shiva import gui
    for head, sub in gui._EMPTY.values():
        assert _covered(head) and _covered(sub)
    for other in ("Use these choices for all other PDFs (%1)", "Use these choices for all other documents (%1)"):
        assert _covered(other)


_WIDGET_CALLS = {"QPushButton", "QCheckBox", "QLabel", "setText", "setToolTip", "setWindowTitle", "setPlaceholderText",
                 "setAccessibleName", "setAccessibleDescription"}
_ALLOWED_BARE = {"Shiva"}


def test_no_bare_english_literal_goes_to_a_widget():
    assert len(GUI_FILES) >= 5
    trees = [ast.parse(f.read_text(encoding="utf-8")) for f in GUI_FILES]
    bad = []
    for node in (n for tree in trees for n in ast.walk(tree)):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name in _WIDGET_CALLS and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                v = node.args[0].value
                if any(c.isalpha() for c in v) and v not in _ALLOWED_BARE:
                    bad.append((node.lineno, v))
    assert not bad, bad


def _scenario_texts() -> list[str]:
    F = m.Facts
    base = dict(probed=True, exists=True, classified=True, storage_done=True, counted=True)
    out: list[str] = [m.CLEAN_LIMITS, m.SHRED_LINE, m.LOST_PW, m.SHORT_PW_WARNING, m.RISK_SSD, m.RISK_REMOVABLE,
                      m.RISK_NETWORK, m.RISK_CLOUD, m.RISK_UNKNOWN, m.NOTE_CONTENT, m.NOTE_ARCHIVE,
                      m.WARN_REENCODE]
    out += [t for _k, t in m.ABOUT_SHREDDING] + [m.ABOUT_TITLE]
    out += list(m.shred.REFUSAL_TEXT.values()) + [f"Not shredded: {t} Nothing was changed." for t in m.shred.REFUSAL_TEXT.values()]
    for t in m.TITLES.values():
        out += list(t)
    facts = [("clean", F(Path("a.jpg"), fmt_status="supported", fmt="jpeg", **base)),
             ("clean", F(Path("x.age"), is_age=True, **base)), ("clean", F(Path("d"), is_dir=True, **base)),
             ("clean", F(Path("n.bin"), fmt_status="unsupported", fmt="This file type cannot be cleaned yet", **base)),
             ("clean", F(Path("n.bin"), placeholder=True, **base)),
             ("clean", F(Path("n.bin"), class_error="PermissionError", **base)),
             ("clean", F(Path("n"), exists=False, probed=True)), ("clean", F(Path("n"))),
             ("encrypt", F(Path("a.txt"), **base)), ("encrypt", F(Path("a.age"), is_age=True, **base)),
             ("shred", F(Path("a.txt"), drive_kind="ssd", cloud_provider="X", **base))]
    for tab, f in facts:
        for mode in ("auto", "encrypt", "decrypt"):
            ri = m.row_info(f, tab, overlap=True, mode=mode)
            out += [ri.status, ri.note]
    for tab, extra in (("clean", frozenset()), ("clean", frozenset({"encrypt"})), ("clean", frozenset({"encrypt", "shred"})),
                       ("encrypt", frozenset()), ("encrypt", frozenset({"shred"}))):
        ri = m.row_info(F(Path("a.jpg"), fmt_status="supported", fmt="jpeg", **base), tab, extra)
        out += [ri.note, m.options_note(m.tab_steps(tab, extra)), m.primary_label(tab, extra, [ri, ri]),
                m.primary_label(tab, extra, [ri])]
    for tab in ("clean", "encrypt", "shred"):
        act = {"encrypt": "decrypt"}.get(tab, tab)
        out += [m.primary_label(tab, frozenset(), []), m.primary_label(tab, frozenset(), [m.RowInfo("Ready", "", True, act)] * 3)]
    out.append(m.primary_label("encrypt", frozenset(), [m.RowInfo("Ready", "", True, "encrypt")] * 3
                               + [m.RowInfo("Ready", "", True, "decrypt")] * 2))
    for a in (("", "", True), ("abc", "", True), ("abc", "abc", True), ("abcdefghij", "x", True), ("x", "", False)):
        ps = m.pw_state(a[0], a[1], a[2], not a[2])
        out += [x for x in (ps.reason, ps.match_text, ps.strength_text, ps.warning) if x]
    for args in (("clean", frozenset(), 0, 0, None, True, False), ("clean", frozenset(), 0, 0, None),
                 ("encrypt", frozenset(), 0, 0, None), ("clean", frozenset(), 1, 1, None, False, True),
                 ("clean", frozenset({"encrypt"}), 1, 0, None), ("clean", frozenset(), 1, 0, None),
                 ("encrypt", frozenset(), 1, 0, None)):
        r = m.disable_reason(*args)
        if r:
            out.append(r)
    for a in ((True, True, 3, False), (True, False, 3, True), (False, True, 1, False), (False, False, 1, False),
              (True, False, 1, False)):
        out += list(m.shred_dialog_text(*a))
    for plain in (True, False):
        for kind in ("ssd", "network", "removable", "unknown"):
            out += m.out_dir_notes(storage.StorageInfo(Path("a"), drive_kind=kind, cloud_provider="OneDrive"), plain)
    for kind in ("ssd", "removable", "network", "unknown"):
        for fs in ("NTFS", "ReFS"):
            out += storage.StorageInfo(Path("a"), drive_kind=kind, filesystem=fs, cloud_provider="OneDrive").warnings()
    P = Path
    for tab, ticked in (("clean", frozenset()), ("encrypt", frozenset()), ("shred", frozenset()),
                        ("clean", frozenset({"encrypt", "shred"})), ("clean", frozenset({"encrypt"})),
                        ("encrypt", frozenset({"shred"}))):
        for kw in (dict(created=P("C:/x/a.cleaned.jpg"), removed=["Exif camera/date", "Exif GPS"],
                        warnings=["Images embedded in the PDF are not cleaned."]),
                   dict(created=P("C:/y/a.cleaned.jpg"), kept=["colour profile (ICC)"], original_removed=True,
                        shred_attempted=True),
                   dict(created=P("C:/x/a.cleaned.jpg"), shred_attempted=True),
                   dict(ok=False, error_kind="error"), dict(ok=False, error_kind="unsupported"),
                   dict(ok=False, cancelled=True), dict(ok=False, cancelled=True, shred_attempted=True),
                   dict(ok=False, partially_shredded=True),
                   dict(path=P("C:/x/a.txt.age"), created=P("C:/x/a.txt")),
                   dict(unchanged=True), dict(unchanged=True, kept=["colour profile (ICC)"]),
                   dict(unchanged=True, kept=[steps.KEPT_UNCLEAN], warnings=["a.txt was kept unchanged and may leak metadata"]),
                   dict(created=P("C:/x/a.cleaned (2).jpg"), renamed=True, usual=P("C:/x/a.cleaned.jpg")),
                   dict(created=P("C:/x/a.cleaned (2).jpg"), renamed=True),
                   dict(path=P("C:/x/a.txt.age"), created=P("C:/x/a (2).txt"), renamed=True, usual=P("C:/x/a.txt")),
                   dict(created=P("C:/x/a.cleaned.jpg.age"), unchanged=True),
                   dict(original_removed=True, shred_attempted=True)):
            kw.setdefault("path", P("C:/x/a.jpg")); kw.setdefault("ok", True); kw.setdefault("message", "msg")
            rr = m.result_row(jobs.JobResult(**kw), tab, ticked, 1000, 2000, uncleanable=(tab == "clean" and "encrypt" in ticked))
            out += [rr.text, rr.status_cell] + [d for d in rr.details if not d.startswith("Reason:")]
    out += list(m.RISK_CAUSE.values()) + [m.RISK_ROW_ONLINE, m.RISK_ROW_SERVER, m.RISK_SUMMARY, m.risk_row_text([m.RISK_SSD, m.RISK_CLOUD]),
                                          "All 3 items: " + m.RISK_SSD, "In a.txt, c.txt: " + m.RISK_SSD,
                                          "In a.txt, c.txt and 3 more: " + m.RISK_SSD, "1 item not found",
                                          "2 files not found and left out of the run.", "Previous results cleared",
                                          m.summary_text(["Done", "Failed"], "shred"),
                                          m.summary_text(["Done", "Not found"])]
    sk = m.skipped_row(P("a"), m.RowInfo("Not supported", "Folders can't be cleaned yet.", False), 10)
    out += [sk.text] + sk.details
    for sts in (["Done", "Failed"], ["Done"], ["Failed", "Failed"], ["Cancelled", "Done", "Warning"],
                ["Not fully shredded"], [], ["Skipped"]):
        out += [m.summary_text(sts), m.finish_text(sts, len(sts))]
    out += [m.finish_text(["Done", "Cancelled", "Cancelled"], 3), m.finish_text(["Cancelled"], 1)]
    # status-bar and dialog texts that gui.py builds with f-strings
    out += ["3 files added. 1 already in the list", "2 items added", "3 added to Clean metadata",
            "Scanning a.pdf ...", "a.pdf: cannot be scanned (OSError); it will be reported as failed.",
            "The output folder does not exist: X Nothing was changed.",
            "The output folder is inside the folder being processed (d). Choose a folder outside it. Nothing was changed.",
            "Working on 2 of 5: a.txt", "Results will be saved in X. Unlike the originals, these copies are not encrypted.",
            "Encrypt 2 files without cleaning?", "a.bin - not supported yet", "d (4 items)",
            "a.docx: will also remove comments, tracked changes (changes content).",
            "Cancelled. Nothing was changed.", "Cancelling after the current file...", "Closing after the current file...",
            "Files that can't be cleaned are encrypted but their originals are never shredded.", "12+ items"]
    return out


def _translated(zh, s: str) -> bool:
    """Changed by the engine, or listed in the catalog (technical names such as 'XMP' stay as they are)."""
    from shiva.translations.zh_TW import CATALOG
    return zh.dyn(s) != s or s in CATALOG


def test_model_texts_are_translated(zh):
    missing = sorted({t for t in _scenario_texts() if t and t.strip() and not _translated(zh, t)
                      and t not in {"msg", "-"}})
    assert not missing, missing


def test_engine_labels_are_translated(zh):
    srcs = list(pdf._LABELS.values()) + list(pdf._LIMITS) + [pdf.IMAGES_WARNING, pdf.NOT_CLEANED_WARNING,
                                                              pdf.ANNOT_TEXT_WARNING, pdf.FLATTEN_SUPERSEDES,
                                                              steps.KEPT_UNCLEAN, office._BLOCKING_REASON]
    for lab, det, _cc in office.CATEGORIES.values():
        srcs += [lab, det, f"{lab} kept ({det})"] if _cc else [lab, det]
    for lab, det, why in office._ALWAYS_KEPT.values():
        srcs += [lab, det, why]
    srcs += [office.C_PROPS, office.C_APP, office.C_TRACK, office.C_COMMENTS, office.C_RSID, office.C_TEMPLATE,
             office.C_PATH, office.C_PRINTER, office.C_GLOSSARY]
    srcs += [cat for _rx, cat, *_r in office._OMIT] + [cat for _rx, cat, _k in office._ODF_OMIT]
    from shiva.metadata import png, webp, audio
    srcs += list(png._CATEGORY.values()) + list(webp._CATEGORY.values()) + list(audio._FLAC_CATEGORY.values())
    srcs += ["other APPn", "JFIF thumbnail", "ICC profile", "Exif GPS", "Exif camera/date", "XMP", "IPTC/Photoshop",
             "JPEG comment", "Exif orientation", "data after EOI", "other PNG chunks", "data after IEND",
             "other WebP chunks", "data after the RIFF end", "plain text extension", "GIF comments",
             "other application extensions", "data after the GIF trailer", "other FLAC blocks",
             "unknown data after the audio stream", "Opus comments", "encoder vendor string", "ID3v2 tag",
             "ID3v1 tag", "APEv2 tag", "Lyrics3 tag", "file times and attributes stored in the zip",
             "file times, permissions and comments stored in the zip", "empty folders", "embedded image metadata",
             "embedded audio metadata", "The Exif orientation tag (3) was removed; the image may display rotated.",
             "Warning: possible covered content on page 2 (a filled rectangle is drawn over text). "
             "Nothing was removed; covered text stays in the file.",
             "Warning: possible covered content on 3 more page(s).", "3 object(s) with /Metadata",
             "2 hidden layer(s): A, B; 4 content span(s)", "5 page(s) become images; possible covered content on 2 page(s); "
             "text becomes unselectable and unsearchable, links, bookmarks and forms are lost, the file is usually larger",
             "x.pdf was kept unchanged and may leak metadata", "omitted member with an unsupported format: x.bin",
             "NOT cleaned: This file type cannot be cleaned yet. Metadata, file name, size and times of this file are unchanged.",
             "The original was kept. Cleaning changed its content: comments.",
             "padding, compression tables and hidden bytes in the image data", "JPEG re-encoded (lossy)",
             "image rotated to match its Exif orientation"]
    missing = [s for s in srcs if not _translated(zh, s)]
    assert not missing, missing


# ---------------------------------------------------------------- the window in Chinese
def test_window_is_chinese(zh, win):
    assert win.statusLine.text() == "就緒"
    assert [win.nav[t].accessibleName() for t in m.TABS][:2] == ["清理中繼資料, 圖片、PDF、Office、音訊、ZIP", "加密, 檔案與資料夾"]
    p = win.pages["encrypt"]
    assert p.addFilesBtn.text() == "加入檔案(&A)..." and p.modeDecBtn.text() == "解密"
    assert "加入要加密的檔案或資料夾" in p.emptyState.text()
    assert p.primaryBtn.text() == "加密" and p.clearBtn.text() == "清除清單"
    assert p.fileTable.horizontalHeaderItem(0).text() == "狀態"
    p.set_crypt_mode("decrypt")
    assert "加入要解密的 .age 檔案" in p.emptyState.text()


def test_window_lists_and_buttons_in_chinese(zh, win, tmp_path):
    files = []
    for n in ("a.txt", "b.txt", "c.txt"):
        f = tmp_path / n; f.write_bytes(b"x"); files.append(f)
    win.set_tab("encrypt")
    win.add_paths(files); win.wait_for_probes()
    p = win.pages["encrypt"]
    assert p.primaryBtn.text() == "加密 3 個項目"
    assert "已加入 3 個項目" in win.statusLine.text()
    assert p.fileTable.item(0, 0).text() == "就緒" and p.fileTable.item(0, 4).text() == ""
    assert p.reasonLabel.text() == "輸入密碼。"
    p.pwEdit.setText("abc")
    assert p.strengthLabel.text() == "長度：3 個字元"
    assert "密碼太短" in p.pwWarnLabel.text()
    win.set_tab("shred")
    s = win.pages["shred"]
    assert "全磁碟加密" in s.noteLabel.text() and "盡力而為" in s.noteLabel.text()
    assert "安全刪除" not in s.noteLabel.text()


def test_dialogs_in_chinese(zh, win):
    title, body, do = m.shred_dialog_text(True, True, 2)
    dlg = ConfirmDialog(win, title, body, ["a.txt"], ["x.txt: " + storage.StorageInfo(Path("a"), drive_kind="ssd").warnings()[0]], do)
    assert dlg.windowTitle() == "加密後要銷毀 2 個原始檔嗎？"
    assert dlg.cancelBtn.text() == "取消" and dlg.doBtn.text() == "銷毀原始檔"
    cats = [dict(key="info", label=pdf._LABELS["info"], detail="Author, Title", content_changing=False, default_checked=True),
            dict(key="links", label=pdf._LABELS["links"], detail="3 link(s)", content_changing=True, default_checked=False),
            dict(key="x", label="Theme files", detail="colours, fonts and effects of the design", content_changing=False,
                 selectable=False, reason=office._ALWAYS_KEPT["theme"][2])]
    pd = PdfDialog(win, "a.pdf", cats, remaining=2)
    texts = [cb.text() for _k, cb in pd.boxes]
    assert texts[0] == "文件資訊: Author, Title" and texts[1].endswith("（會改變內容）") and "連結" in texts[1]
    assert pd.windowTitle() == "選擇要從 a.pdf 移除的內容"
    assert pd.useOthersCheck.text() == "對所有其他 PDF 套用這些選擇（2 個）"
    assert pd.okBtn.text() == "使用這些選擇"
