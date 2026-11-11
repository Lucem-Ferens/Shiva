"""Encrypting to recipient keys and decrypting with a key file, through the real pipelines."""
import pytest

from shiva import agecli, creds, jobs
from shiva.steps import StepOptions, run_steps

pytestmark = pytest.mark.skipif(not (agecli.bin_dir() / "age-keygen.exe").exists(), reason="age binaries absent")


@pytest.fixture(params=[False, True], ids=["standard", "post-quantum"])
def pq(request):
    return request.param


def _keys(pq, with_identity=True):
    ident = agecli.generate_identity(pq)
    return creds.Keys(agecli.identity_recipients(ident), ident if with_identity else None), ident


def test_file_round_trip_with_key_verifies_and_reports_the_recipients(tmp_path, pq):
    k, ident = _keys(pq)
    f = tmp_path / "a.txt"; f.write_bytes(b"secret" * 100)
    r = jobs.run_jobs([f], k, False)[0]
    assert r.ok and r.created.name == "a.txt.age" and f.exists()
    assert r.verified_with_key and r.recipient_count == 1
    assert r.recipient_kinds == ("post-quantum" if pq else "standard") and r.post_quantum_confirmed is pq
    f.unlink()
    d = jobs.run_jobs([r.created], creds.Keys([], ident), False)[0]
    assert d.ok and (tmp_path / "a.txt").read_bytes() == b"secret" * 100


def test_without_a_key_file_nothing_is_verified_and_the_result_says_so(tmp_path):
    k, ident = _keys(False, with_identity=False)
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    r = jobs.run_jobs([f], k, False)[0]
    assert r.ok and not r.verified_with_key and "not opened to check" in r.message
    assert agecli.decrypt_hash_identity(r.created, ident)       # and it does decrypt


def test_shredding_without_a_key_file_is_refused_and_changes_nothing(tmp_path):
    k, _ = _keys(False, with_identity=False)
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    r = jobs.run_jobs([f], k, True)[0]
    assert not r.ok and r.error_code == "key_needed_to_verify" and f.read_bytes() == b"x"
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_shredding_with_a_key_file_removes_the_original_after_the_trial_decrypt(tmp_path, pq):
    k, ident = _keys(pq)
    f = tmp_path / "a.txt"; f.write_bytes(b"gone")
    r = jobs.run_jobs([f], k, True)[0]
    assert r.ok and r.verified_with_key and r.original_removed and not f.exists()
    out = tmp_path / "back.txt"
    agecli.decrypt_identity(r.created, out, ident)
    assert out.read_bytes() == b"gone"


def test_folder_round_trip_with_key(tmp_path, pq):
    k, ident = _keys(pq)
    d = tmp_path / "docs"; d.mkdir(); (d / "x.txt").write_bytes(b"1"); (d / "y.txt").write_bytes(b"2")
    r = jobs.run_jobs([d], k, False)[0]
    assert r.ok and r.created.name == "docs.tar.age" and r.verified_with_key
    out = tmp_path / "restored"; out.mkdir()
    dec = jobs.run_jobs([r.created], creds.Keys([], ident), False, out_dir=out)[0]
    assert dec.ok and (out / "docs" / "x.txt").read_bytes() == b"1"


def test_folder_shred_needs_the_key_file(tmp_path):
    k, _ = _keys(False, with_identity=False)
    d = tmp_path / "docs"; d.mkdir(); (d / "x.txt").write_bytes(b"1")
    r = jobs.run_jobs([d], k, True)[0]
    assert not r.ok and r.error_code == "key_needed_to_verify" and (d / "x.txt").read_bytes() == b"1"


def test_mixed_or_empty_or_bad_recipients_are_rejected_before_anything_is_written(tmp_path):
    std, _ = _keys(False)
    pqk, _ = _keys(True)
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    for k in (creds.Keys(std.recipients + pqk.recipients), creds.Keys([]), creds.Keys(["age1" + "q" * 58])):
        r = jobs.run_jobs([f], k, False)[0]
        assert not r.ok and r.error_code == "recipient_rejected", r.message
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_wrong_key_cannot_decrypt_and_leaves_no_output(tmp_path):
    k, _ = _keys(False)
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    enc = jobs.run_jobs([f], k, False)[0].created
    f.unlink()
    other = agecli.generate_identity(False)
    r = jobs.run_jobs([enc], creds.Keys([], other), False)[0]
    assert not r.ok and not (tmp_path / "a.txt").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt.age"]


def test_password_results_carry_no_key_facts(tmp_path):
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    r = jobs.run_jobs([f], "pw", False)[0]
    assert r.ok and r.recipient_count == 0 and r.recipient_kinds == "" and not r.verified_with_key
    assert r.post_quantum_confirmed is None and "verified" in r.message


def test_steps_pass_keys_through_and_refuse_clean_with_keys(tmp_path):
    k, ident = _keys(False)
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    r = run_steps([f], k, StepOptions(clean=False, encrypt=True))[0]
    assert r.ok and r.verified_with_key
    r = run_steps([f], k, StepOptions(clean=True, encrypt=True))[0]
    assert not r.ok and r.error_kind == "unsupported"
    enc = tmp_path / "a.txt.age"
    f.unlink()
    r = run_steps([enc], creds.Keys([], ident), StepOptions(encrypt=False))[0]
    assert r.ok and f.read_bytes() == b"x"


@pytest.mark.parametrize("shred", [False, True])
def test_a_key_file_that_is_not_a_recipient_is_refused_before_any_work(tmp_path, shred):
    bob, _ = _keys(False)
    _, mine = _keys(False)
    k = creds.Keys(bob.recipients, mine)
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    d = tmp_path / "docs"; d.mkdir(); (d / "y.txt").write_bytes(b"y")
    for item in (f, d):
        r = jobs.run_jobs([item], k, shred)[0]
        assert not r.ok and r.error_code == "key_mismatch"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt", "docs"] and f.read_bytes() == b"x"


def test_key_file_matching_one_of_several_recipients_is_accepted(tmp_path):
    bob, _ = _keys(False)
    me, mine = _keys(False)
    k = creds.Keys(bob.recipients + me.recipients, mine)
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    r = jobs.run_jobs([f], k, False)[0]
    assert r.ok and r.verified_with_key and r.recipient_count == 2


def test_cross_mode_decrypt_is_a_clear_failure_without_output(tmp_path):
    k, ident = _keys(False)
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    d = tmp_path / "docs"; d.mkdir(); (d / "y.txt").write_bytes(b"y")
    pw_files = [jobs.run_jobs([i], "pw", False)[0].created for i in (f, d)]
    key_files = [jobs.run_jobs([i], k, False, out_dir=tmp_path / "k")[0].created for i in ()] or []
    f.unlink()
    for enc in pw_files:                               # password file opened with a key
        r = jobs.run_jobs([enc], creds.Keys([], ident), False)[0]
        assert not r.ok and r.error_code == "key_mismatch"
    kf = jobs.run_jobs([tmp_path / "docs"], k, False)[0].created
    r = jobs.run_jobs([kf], "pw", False)[0]            # key file opened with a password
    assert not r.ok
    assert not (tmp_path / "a.txt").exists() and key_files == []


def test_decrypting_without_a_key_file_has_its_own_code(tmp_path):
    k, _ = _keys(False)
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    enc = jobs.run_jobs([f], k, False)[0].created
    r = jobs.run_jobs([enc], creds.Keys([], None), False)[0]
    assert not r.ok and r.error_code == "key_needed"


def test_clean_pipeline_takes_a_password_only(tmp_path):
    from shiva import clean_pipeline
    k, _ = _keys(False)
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    with pytest.raises(TypeError):
        clean_pipeline.clean_encrypt_verify_shred(f, k)
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]
