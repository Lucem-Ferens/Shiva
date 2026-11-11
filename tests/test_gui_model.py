"""Qt-free rules of the window (src/shiva/ui_model.py)."""
from pathlib import Path

import pytest

from shiva import ui_model as m
from shiva.jobs import JobResult


@pytest.mark.parametrize("pw,pw2,ok,reason,match,warn", [
    ("", "", False, "Enter a passphrase.", "", ""),                                     # A
    ("12345", "", False, "Repeat the passphrase.", "", m.SHORT_PW_WARNING),              # B: warns, no length block
    ("abcdefghi", "", False, "Repeat the passphrase.", "", ""),                          # C
    ("abcdefghijklmn", "abcdefghijkl", False, "",
     "\u2717 Passphrases do not match", ""),                                             # D
    ("abcdefghi", "abcdefghi", True, None, "\u2713 Passphrases match", ""),              # E
    ("correct horse battery", "correct horse battery", True, None, "\u2713 Passphrases match", ""),   # F
    ("short", "short", True, None, "\u2713 Passphrases match", m.SHORT_PW_WARNING),      # short + same: allowed
])
def test_pw_state_encrypting(pw, pw2, ok, reason, match, warn):
    s = m.pw_state(pw, pw2, encrypting=True)
    assert (s.ok, s.reason, s.match_text, s.warning) == (ok, reason, match, warn)
    assert s.show_confirm


def test_pw_state_decrypt_only_has_no_length_or_confirm_rule():
    s = m.pw_state("x", "", encrypting=False, decrypting=True)
    assert s.ok and not s.show_confirm and s.warning == "" and s.strength_text == ""
    assert not m.pw_state("", "", encrypting=False, decrypting=True).ok
    assert m.pw_state("", "", encrypting=False, decrypting=False).ok     # nothing to encrypt or decrypt
    assert not m.pw_state("x", "", encrypting=True, decrypting=True).ok   # mixed list uses the encrypt rule


def test_strength_text_is_length_only_and_never_a_strength_word():
    assert m.pw_state("", "", True).strength_text == ""
    assert m.pw_state("a", "", True).strength_text == "Length: 1 character"
    for pw in ("Password12345678", "aaaaaaaaaaaa", "correct horse battery staple"):
        t = m.pw_state(pw, "", True).strength_text
        assert t == f"Length: {len(pw)} characters"
        assert not any(w in t.lower() for w in ("weak", "strong", "fair", "safe", "guess"))


def _f(name="a.jpg", **kw):
    f = m.Facts(Path(name), probed=True, classified=True, fmt_status="supported", fmt="jpeg", size=10)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def test_encrypt_page_mode_switch():
    age, txt = _f("x.age", is_age=True), _f("x.txt")
    assert m.row_info(age, "encrypt", mode="decrypt").action == "decrypt"
    assert m.row_info(txt, "encrypt", mode="encrypt").action == "encrypt"
    wrong_dec = m.row_info(txt, "encrypt", mode="decrypt")
    assert not wrong_dec.run and "Not an .age file" in wrong_dec.note
    wrong_enc = m.row_info(age, "encrypt", mode="encrypt")
    assert not wrong_enc.run and "Switch to Decrypt" in wrong_enc.note


def test_row_notes_per_tab():
    assert m.row_info(_f(), "clean").note == ""
    assert m.row_info(_f("x.age", is_age=True), "clean").status == "Not supported"
    assert m.row_info(_f("d", is_dir=True), "clean").note == "Folders can't be cleaned yet."
    assert m.row_info(_f("x.age", is_age=True), "encrypt").note == ""
    assert m.row_info(_f("x.txt"), "encrypt").note == ""
    assert m.row_info(_f("x.age", is_age=True), "shred").run
    bad = _f("n.docx", fmt_status="unsupported", fmt="This file type cannot be cleaned yet")
    assert m.row_info(bad, "clean").note == "Not supported yet."
    cc = m.row_info(bad, "clean", frozenset({"encrypt"}))          # Clean page, "Encrypt the cleaned copies" ticked
    assert cc.status == "Can't be cleaned" and cc.can_unclean and not cc.run
    assert not m.row_info(_f("d", is_dir=True), "clean", frozenset({"encrypt"})).run
    assert m.row_info(_f("d", is_dir=True), "encrypt", frozenset({"shred"})).run   # Encrypt page + delete originals
    assert m.row_info(_f("p.jpg", placeholder=True), "clean").status == "Not supported"
    assert m.row_info(_f("x", class_error="PermissionError"), "clean").status == "Not readable"


@pytest.mark.parametrize("tab,ticked,acts,label", [
    ("clean", (), ["clean"] * 2, "Clean 2 files"),
    ("clean", (), [], "Clean"),
    ("encrypt", (), ["encrypt"] * 2, "Encrypt 2 items"),
    ("encrypt", (), ["decrypt"], "Decrypt 1 file"),
    ("encrypt", (), ["encrypt"] * 2 + ["decrypt"], "Encrypt 2 items and decrypt 1"),
    ("encrypt", (), ["encrypt"] * 12 + ["decrypt"] * 3, "Encrypt and decrypt 15 items"),
    ("shred", (), ["shred"] * 3, "Shred 3 items..."),
    ("clean", ("encrypt",), ["clean"] * 3, "Clean and encrypt 3 files"),
    ("clean", ("encrypt", "shred"), ["clean"] * 3, "Clean, encrypt and shred 3 files..."),
    ("clean", ("encrypt",), [], "Clean"),
    ("encrypt", ("shred",), ["encrypt"] * 2, "Encrypt and shred 2 items..."),
    ("encrypt", ("shred",), ["decrypt"], "Decrypt 1 file"),
])
def test_primary_label(tab, ticked, acts, label):
    assert m.primary_label(tab, frozenset(ticked), [m.RowInfo("Ready", "", True, a) for a in acts]) == label


def test_disable_reason_order():
    d = m.disable_reason
    none = frozenset()
    assert d("clean", none, 0, 0, None, running=True) == "Wait for the current run to finish."
    assert d("clean", none, 0, 0, "x") == "Add files to clean."
    assert d("shred", none, 0, 0, None) == "Add files or folders."
    assert d("clean", none, 2, 0, "pw") == "Add at least one file that can be cleaned."
    assert d("encrypt", none, 2, 2, "Enter a passphrase.") == "Enter a passphrase."
    assert d("encrypt", none, 2, 2, None) is None
    assert d("encrypt", none, 2, 2, None, pending=True) == "Checking the added items..."


def test_shred_text_claims_trial_decrypt_only_when_encrypting():
    title, solo, do = m.shred_dialog_text(False, False, 3)
    assert title == "Shred 3 items?" and do == "Shred"
    assert "keeps no copy" in solo and "can't undo" in solo and "trial decrypt" not in solo
    _, cs, _ = m.shred_dialog_text(True, False, 3)
    assert "trial decrypt" not in cs and "not encrypted" in cs
    for clean in (True, False):
        _, enc, _ = m.shred_dialog_text(clean, True, 3)
        assert "trial decrypt" in enc
    assert "trial decrypt" not in m.options_note(frozenset({"clean"})) + m.options_note(frozenset({"encrypt"}))
    assert "trial decrypt" in m.options_note(frozenset({"encrypt", "shred"}))
    assert "trial decrypt" in m.options_note(frozenset({"clean", "encrypt", "shred"}))


def test_risk_lines_only_for_locations_worse_than_the_baseline():
    assert m.risk_lines("hdd", None) == [] and m.risk_lines("ramdisk", None) == []
    assert m.risk_lines("ssd", None) == [m.RISK_SSD]
    assert m.risk_lines("removable", None) == [m.RISK_REMOVABLE]
    assert m.risk_lines("network", None) == [m.RISK_NETWORK]
    assert m.risk_lines("unknown", None) == [m.RISK_UNKNOWN]
    assert m.risk_lines("hdd", "OneDrive") == [m.RISK_CLOUD]
    assert m.risk_lines("ssd", "OneDrive") == [m.RISK_SSD, m.RISK_CLOUD]      # several findings, fixed order


def test_shred_row_note_shows_risks_not_the_generic_block():
    ssd = _f("a.txt", probed=True, storage_done=True, drive_kind="ssd")
    hdd = _f("b.txt", probed=True, storage_done=True, drive_kind="hdd")
    assert m.row_info(ssd, "shred").note == m.RISK_SSD
    assert m.row_info(hdd, "shred").note == ""


def test_topic_one_strings_are_short_and_use_no_mechanism_words():
    texts = [m.SHRED_LINE, m.ABOUT_LINK, m.WHY_LINK, m.TRAILER, m.SHRED_BODY, m.PIPE_BODY, m.NOTE_CONTENT, m.NOTE_ARCHIVE,
             m.RISK_SSD, m.RISK_REMOVABLE, m.RISK_NETWORK, m.RISK_CLOUD, m.RISK_UNKNOWN]
    for t in texts:
        assert len(t.split()) <= 30, t
        for bad in ("wear levelling", "journal", "shadow cop", "securely delet", "guarantee"):
            assert bad not in t.lower(), (bad, t)
    assert "best effort" in m.SHRED_LINE
    # the help page may use the mechanism words; the phrase "securely deleted" appears nowhere
    about = " ".join(t for _, t in m.ABOUT_SHREDDING)
    assert "wear levelling" in about and "securely deleted" not in about


def test_pipeline_dialog_text_and_notes():
    title, body, do = m.shred_dialog_text(True, True, 2)
    assert title == "Shred 2 originals after encrypting?" and do == "Shred originals" and "trial decrypt" in body
    assert m.confirm_notes(False, False) == [] and m.confirm_notes(True, False) == [m.NOTE_CONTENT]
    assert m.confirm_notes(True, True) == [m.NOTE_CONTENT, m.NOTE_ARCHIVE]


def test_result_rows_from_structured_fields():
    p = Path("C:/x/a.jpg"); out = Path("C:/x/a.cleaned.jpg")
    ok = m.result_row(JobResult(p, True, "whatever", created=out, removed=["Document Info"]), "clean", size_before=3000,
                      size_after=2000)
    assert ok.status == "Done" and "Saved as a.cleaned.jpg. Original kept." in ok.text and "->" in ok.size_text
    kept = m.result_row(JobResult(p, True, "m", created=out, kept=["Thumbnails"]), "clean")
    assert kept.status == "Warning" and "Not removed: Thumbnails" in kept.text
    bad = m.result_row(JobResult(p, False, "m", error_kind="error"), "clean")
    assert bad.status == "Failed" and "Original unchanged" in bad.text
    canc = m.result_row(JobResult(p, False, "m", cancelled=True, error_kind="cancelled"), "clean")
    assert canc.status == "Cancelled"
    part = m.result_row(JobResult(p, False, "m", shred_attempted=True, partially_shredded=True,
                                  error_kind="partial_shred"), "shred")
    assert part.status == "Not fully shredded" and "Original unchanged" not in part.text
    assert "Check what is left" in part.text
    sh = m.result_row(JobResult(p, True, "m", original_removed=True, shred_attempted=True), "shred")
    assert sh.status == "Done" and "best effort" in sh.text and "securely" not in sh.text.lower()
    # encrypt + delete originals (Encrypt page option): checked, original shredded or kept with a warning
    cs = m.result_row(JobResult(p, True, "m", created=Path("C:/x/a.jpg.age"), original_removed=True,
                                shred_attempted=True), "encrypt", frozenset({"shred"}))
    assert "Encrypted and verified" in cs.text and "Original shredded (best effort)" in cs.text
    kept_orig = m.result_row(JobResult(p, True, "m", created=Path("C:/x/a.jpg.age")), "encrypt", frozenset({"shred"}))
    assert kept_orig.status == "Warning" and "not shredded" in kept_orig.text
    cleaned = m.result_row(JobResult(p, True, "m", created=Path("C:/x/a.jpg.age")), "clean", frozenset({"encrypt"}))
    assert cleaned.status == "Done" and "Cleaned, encrypted and verified" in cleaned.text
    unc = m.result_row(JobResult(p, True, "m", created=Path("C:/x/a.docx.age")), "clean",
                       frozenset({"encrypt"}), uncleanable=True)
    assert unc.status == "Warning" and "without cleaning" in unc.text
    dec = m.result_row(JobResult(Path("C:/x/o.zip.age"), True, "m", created=Path("C:/x/o.zip")), "encrypt")
    assert dec.text.startswith("Decrypted to o.zip")
    for r in (ok, kept, bad, canc, part, sh, cs):
        assert r.status_cell == r.status and r.status in m.STATUS_KIND   # word only; the icon is drawn


def test_finish_text_cancel_wording():
    assert m.finish_text(["Cancelled", "Cancelled"], 2) == "Cancelled. Nothing was changed."
    t = m.finish_text(["Done", "Done", "Cancelled"], 3)
    assert t.startswith("Cancelled. 2 done, 1 cancelled.") and "Nothing was changed" not in t
    t = m.finish_text(["Done", "Not fully shredded", "Failed", "Cancelled"], 4)     # a cancel must not hide problems
    assert "1 not fully shredded" in t and "1 failed" in t and "Check what is left" in t and "Select a row" in t
    assert m.finish_text(["Done", "Failed"], 2).startswith("1 done, 1 failed")
    assert "Select a row" in m.finish_text(["Failed"], 1)
    assert "not fully shredded" in m.finish_text(["Not fully shredded"], 1)


def test_summary_level_is_the_worst_severity():
    assert m.summary_level(["Done", "Done"]) == "success"
    assert m.summary_level(["Done", "Warning"]) == "warning"
    assert m.summary_level(["Done", "Skipped"]) == "warning"
    assert m.summary_level(["Done", "Failed", "Warning"]) == "error"
    assert m.summary_level(["Not fully shredded"]) == "error"
    assert m.summary_level(["Done", "Done"], location_risk=True) == "warning"   # shred on a risky location


def test_first_problem_row_is_selected():
    assert m.first_problem_index(["Done", "Done", "Failed", "Warning"]) == 2
    assert m.first_problem_index(["Done", "Done"]) == 0


def test_shred_row_at_risky_location_is_a_warning_and_jargon_is_technical():
    from pathlib import Path
    from shiva.jobs import JobResult
    r = JobResult(Path("a.bin"), True, "Overwritten and removed", original_removed=True, shred_attempted=True,
                  warnings=["The file system keeps a journal."])
    ok = m.result_row(r, "shred")
    assert ok.status == "Done" and ok.text == "Shredded (best effort)."
    assert any(x.startswith("Note: ") for x in ok.technical) and not any(x.startswith("Note: ") for x in ok.details)
    risky = m.result_row(r, "shred", risk_lines=[m.RISK_SSD])
    assert risky.status == "Warning" and risky.text == "Shredded (best effort). " + m.RISK_ROW
    failed = m.result_row(JobResult(Path("a.bin"), False, "PermissionError: denied", error_kind="error"), "shred")
    assert any(x.startswith("Reason: ") for x in failed.details)        # a failure keeps its cause visible
