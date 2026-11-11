"""Window-free rules for key-based encryption: reasons, option gating and result wording."""
from pathlib import Path

import pytest

from shiva import key_model as K, recipients as R, ui_model as m
from shiva.jobs import JobResult

STD = R.Recipient(R.STANDARD, "age1" + "q" * 58)
PQ = R.Recipient(R.POST_QUANTUM, "age1pq1" + "q" * 50)


def S(**kw):
    return K.CredState(mode=K.MODE_KEYS, **kw)


def test_password_mode_reason_is_the_password_blocks():
    assert K.cred_reason(K.CredState(password_reason="Enter a password."), False) == "Enter a password."
    assert K.cred_reason(K.CredState(), False) is None


def test_encrypt_reasons_come_in_the_designed_order():
    assert K.cred_reason(S(paste_text="age1x", recipients=[STD, PQ]), False) == "Press Add key to use the pasted key."
    assert K.cred_reason(S(), False) == "Add at least one recipient key."
    assert K.cred_reason(S(recipients=[STD, PQ]), False) == K.MIXED_REASON
    assert K.cred_reason(S(recipients=[STD], key_file=K.KF_LOCKED), False) == "Unlock the key file, or remove it."
    assert K.cred_reason(S(recipients=[STD], key_file=K.KF_WRONG_PASSWORD), False) == "The key file password is wrong."
    assert K.cred_reason(S(recipients=[STD, STD], key_file=K.KF_UNLOCKED), False) is None
    assert K.cred_reason(S(recipients=[PQ]), False) is None             # the key file is optional


def test_decrypt_reasons():
    assert K.cred_reason(S(), True) == "Choose a key file."
    assert K.cred_reason(S(key_file=K.KF_NOT_A_KEY), True) == "This isn't a key file."
    assert K.cred_reason(S(key_file=K.KF_LOCKED), True) == "Enter the key file password."
    assert K.cred_reason(S(key_file=K.KF_WRONG_PASSWORD), True) == "The key file password is wrong."
    assert K.cred_reason(S(key_file=K.KF_UNLOCKED), True) is None and K.cred_reason(S(key_file=K.KF_CHOSEN), True) is None


def test_shred_after_needs_the_key_file_in_key_mode_only():
    assert K.shred_after_reason(K.CredState()) is None
    assert K.shred_after_reason(S(recipients=[STD])) and K.shred_after_reason(S(key_file=K.KF_LOCKED))
    assert K.shred_after_reason(S(key_file=K.KF_UNLOCKED)) is None and K.shred_after_reason(S(key_file=K.KF_CHOSEN)) is None
    assert K.own_key_available(S(key_file=K.KF_UNLOCKED)) and not K.own_key_available(S(key_file=K.KF_LOCKED))
    assert not K.own_key_available(K.CredState(key_file=K.KF_UNLOCKED))


def test_summary_line():
    assert K.key_summary([]) == "" and K.key_summary([STD, PQ]) == ""
    assert K.key_summary([PQ, PQ]) == "2 keys, post-quantum." and K.key_summary([STD]) == "1 key, standard."


@pytest.mark.parametrize("tab,mode", [("encrypt", "encrypt"), ("encrypt", "decrypt"), ("clean", "encrypt"), ("shred", "encrypt")])
def test_option_view_gating_for_keys(tab, mode):
    ticks = {"shred_after": True, "own_key": True}
    for key_mode in (False, True):
        for ready in (False, True):
            v = m.option_view(tab, mode, ticks, m.OptionFacts(key_mode=key_mode, key_ready=ready))
            enc_page = tab == "encrypt" and mode == "encrypt"
            assert v.shown["own_key"] == (enc_page and key_mode)
            assert v.enabled["own_key"] == (enc_page and key_mode and ready) and v.live["own_key"] == v.enabled["own_key"]
            if tab == "encrypt" and mode == "encrypt":
                assert v.enabled["shred_after"] == (not key_mode or ready)
                assert v.live["shred_after"] == v.enabled["shred_after"]
            assert not (v.live["own_key"] and not v.shown["own_key"])


def _ok(**kw):
    base = dict(path=Path("a.txt"), ok=True, message="Encrypted", created=Path("a.txt.age"))
    base.update(kw)
    return JobResult(**base)


def test_key_results_never_say_verified_without_the_key_file_check():
    r = _ok(key_mode=True, recipient_count=2, recipient_kinds="post-quantum", verified_with_key=False, post_quantum_confirmed=True)
    row = m.result_row(r, "encrypt")
    assert row.text.startswith("Encrypted for 2 keys (post-quantum). Not opened to check it.") and "verified" not in row.text
    assert "age-inspect confirms the file uses a post-quantum key." in row.details
    r2 = _ok(key_mode=True, recipient_count=1, recipient_kinds="standard", verified_with_key=True, post_quantum_confirmed=False)
    row = m.result_row(r2, "encrypt")
    assert row.text.startswith("Encrypted for 1 key and checked with your key file.") and "post-quantum" not in row.text
    assert not any("post-quantum key" in d for d in row.details)


def test_unconfirmed_post_quantum_is_worded_as_not_confirmed():
    r = _ok(key_mode=True, recipient_count=1, recipient_kinds="post-quantum", verified_with_key=True, post_quantum_confirmed=None)
    assert "Not confirmed: age-inspect gave no answer." in m.result_row(r, "encrypt").details


def test_password_results_are_unchanged_and_never_post_quantum():
    row = m.result_row(_ok(), "encrypt")
    assert row.text == "Encrypted and verified. Saved as a.txt.age. Original kept."
    assert not any("post-quantum" in d for d in row.details)


def test_key_results_with_shredding_use_the_key_head():
    r = _ok(key_mode=True, recipient_count=1, recipient_kinds="standard", verified_with_key=True, original_removed=True,
            shred_attempted=True)
    row = m.result_row(r, "encrypt", frozenset({"shred"}))
    assert row.text.startswith("Encrypted for 1 key and checked with your key file. Saved as a.txt.age. Original shredded")


def test_key_decrypt_row():
    r = JobResult(Path("a.txt.age"), True, "Decrypted", created=Path("a.txt"), key_mode=True)
    assert m.result_row(r, "encrypt").text == "Decrypted with your key file to a.txt. The .age file is kept."


@pytest.mark.parametrize("code", sorted(K.KEY_FAIL_CODES))
def test_every_key_error_has_its_own_text_and_says_what_happened_to_the_original(code):
    enc = m.result_row(JobResult(Path("a.txt"), False, "x", error_kind="error", error_code=code), "encrypt")
    dec = m.result_row(JobResult(Path("a.txt.age"), False, "x", error_kind="error", error_code=code), "encrypt")
    assert enc.status == dec.status == "Failed"
    assert enc.text.endswith("Original unchanged.") and dec.text.endswith("The .age file is unchanged.")
    assert "Shiva could not finish" not in enc.text + dec.text


def test_mismatch_text_depends_on_direction():
    e = m.result_row(JobResult(Path("a.txt"), False, "x", error_kind="error", error_code="key_mismatch"), "encrypt").text
    d = m.result_row(JobResult(Path("a.txt.age"), False, "x", error_kind="error", error_code="key_mismatch"), "encrypt").text
    assert "not one of the recipients" in e and "can't open this file" in d


def test_skipped_verify_stage_is_worded_skipped_and_a_single_step_hides_the_list():
    r = _ok(key_mode=True, recipient_count=1, recipient_kinds="standard",
            stages=(("encrypt", "done"), ("verify", "skipped")))
    assert m.recorded_stage_lines(r, frozenset({"encrypt"})) == []          # one step: no list
    lines = m.recorded_stage_lines(r, frozenset({"encrypt", "shred"}))
    assert any(l.word == "Skipped" for l in lines)
