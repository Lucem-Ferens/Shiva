"""Stage list words: planned names while running, recorded words after the run (Qt-free)."""
from pathlib import Path

import pytest

from shiva import stages as st
from shiva import ui_model as m
from shiva.jobs import JobResult
from shiva.ui_model import shred_stage_word
from shiva.steps import StepOptions

TWO = frozenset({"encrypt", "shred"})
FULL = (("clean", "done"), ("encrypt", "done"), ("verify", "done"), ("shred", "attempted"))


def _r(stages=FULL, **kw):
    kw.setdefault("ok", True)
    return JobResult(Path("a.txt"), message="", stages=tuple(stages), **kw)


def _words(r, steps=TWO):
    return [(x.label, x.word, x.kind) for x in m.recorded_stage_lines(r, steps)]


# ---- running
def test_planned_names_only_with_two_or_more_steps():
    o = StepOptions(encrypt=True, shred=True)
    lines = m.planned_stage_lines(Path("a.txt"), o, TWO)
    assert [x.label for x in lines] == ["Encrypt", "Verify by trial decrypt", "Shred original"]
    assert all(x.word == "" and x.kind == "" for x in lines)
    assert m.planned_stage_lines(Path("a.txt"), StepOptions(encrypt=True), frozenset({"encrypt"})) == []
    assert m.planned_stage_lines(Path("a.txt"), o, frozenset({"shred"})) == []


def test_planned_hidden_for_decrypt_and_single_stage_paths():
    assert m.planned_stage_lines(Path("a.age"), StepOptions(decrypt_age=True, shred=True), TWO) == []
    assert m.planned_stage_lines(Path("a.age"), StepOptions(shred=True, encrypt=False), TWO) == []


def test_clean_encrypt_planned():
    o = StepOptions(clean=True, encrypt=True)
    labels = [x.label for x in m.planned_stage_lines(Path("a.jpg"), o, frozenset({"clean", "encrypt"}))]
    assert labels == ["Clean metadata", "Encrypt", "Verify by trial decrypt"]


# ---- shred word: row flags only, in order
@pytest.mark.parametrize("kw,word,kind", [
    (dict(ok=False, error_kind="unsupported"), "Not shredded", "warning"),
    (dict(ok=False, error_kind="unsupported", original_removed=True), "Not shredded", "warning"),
    (dict(original_removed=True), "Shredded (best effort)", "ok"),
    (dict(original_untouched=True), "Not shredded, original kept", "warning"),
    (dict(), "Not fully shredded", "error"),
    (dict(ok=False, error_kind="error", shred_attempted=False), "Not fully shredded", "error"),   # not keyed on shred_attempted
    (dict(ok=False, error_kind="partial_shred", partially_shredded=True), "Not fully shredded", "error"),
    (dict(ok=True, original_removed=True, original_untouched=True), "Shredded (best effort)", "ok"),
])
def test_shred_word_from_row_flags(kw, word, kind):
    got = _words(_r(**kw))[-1]
    assert got == ("Shred original", word, kind)


def test_shred_never_reads_done_or_a_bare_done():
    for state in ("done", "attempted", "failed", "cancelled"):
        r = _r((("encrypt", "done"), ("verify", "done"), ("shred", state)), original_removed=True)
        assert _words(r)[-1][1] == "Shredded (best effort)"
        for kw in (dict(), dict(original_untouched=True), dict(ok=False, error_kind="unsupported")):
            assert _words(_r((("encrypt", "done"), ("verify", "done"), ("shred", state)), **kw))[-1][1] != "Done"


# ---- other stage words
def test_success_row_words():
    assert _words(_r(original_removed=True)) == [
        ("Clean metadata", "Done", "ok"), ("Encrypt", "Done", "ok"), ("Verify by trial decrypt", "Done", "ok"),
        ("Shred original", "Shredded (best effort)", "ok")]


def test_clean_with_kept_parts_is_a_warning_not_a_claim():
    got = _words(_r(original_removed=True, kept=["XMP"]))[0]
    assert got == ("Clean metadata", "Done, some metadata not removed", "warning")


def test_skipped_only_on_success_rows():
    stages = (("encrypt", "done"), ("verify", "done"), ("shred", "skipped"))
    assert _words(_r(stages))[-1] == ("Shred original", "Skipped", "skipped")
    failed = _r((("encrypt", "done"), ("verify", "failed"), ("shred", "skipped")), ok=False, error_kind="error")
    assert _words(failed) == [("Encrypt", "Done", "ok"), ("Verify by trial decrypt", "Failed", "error"),
                              ("Shred original", "Not started", "pending")]


def test_not_started_and_cancelled():
    r = _r((("clean", "done"), ("encrypt", "cancelled"), ("verify", "not_started"), ("shred", "not_started")),
           ok=False, cancelled=True, error_kind="cancelled")
    assert [w for _l, w, _k in _words(r)] == ["Done", "Cancelled", "Not started", "Not started"]


# ---- hidden
def test_hidden_cases():
    assert m.recorded_stage_lines(None, TWO) == []
    assert m.recorded_stage_lines(_r(), frozenset({"encrypt"})) == []             # one step ticked
    assert m.recorded_stage_lines(_r(()), TWO) == []                              # nothing recorded
    assert m.recorded_stage_lines(_r((("shred", "attempted"),)), TWO) == []       # one stage
    refused = _r((("clean", "skipped"), ("encrypt", "not_started"), ("verify", "not_started"),
                  ("shred", "not_started")), ok=False, error_kind="error")
    assert m.recorded_stage_lines(refused, TWO) == []                             # no stage began
    assert m.recorded_stage_lines(_r((("bogus", "done"), ("shred", "attempted"))), TWO) == []


def test_every_state_of_every_stage_has_a_word():
    for name in m.STAGE_LABEL:
        for state in (st.DONE, st.FAILED, st.CANCELLED, st.SKIPPED, st.NOT_STARTED, st.ATTEMPTED):
            for ok in (True, False):
                r = _r(((name, state), ("encrypt" if name != "encrypt" else "clean", "done")), ok=ok,
                       error_kind=None if ok else "error")
                for line in m.recorded_stage_lines(r, TWO):
                    assert line.word and line.kind


def test_shred_word_icon_warns_when_removed_and_risky():
    r = JobResult(Path("a"), True, "", original_removed=True, shred_attempted=True)
    assert shred_stage_word(r, False) == ("Shredded (best effort)", "ok")
    assert shred_stage_word(r, True) == ("Shredded (best effort)", "warning")
    kept = JobResult(Path("a"), True, "", original_untouched=True, shred_attempted=True)
    assert shred_stage_word(kept, True) == ("Not shredded, original kept", "warning")
