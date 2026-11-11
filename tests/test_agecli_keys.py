"""Key-based age calls: keys live in memory, nothing secret is written, nothing waits on a terminal."""
import hashlib

import pytest

from shiva import agecli

pytestmark = pytest.mark.skipif(not (agecli.bin_dir() / "age-keygen.exe").exists(), reason="age binaries absent")

PLAIN = b"the quick brown fox " * 1000


@pytest.fixture(params=[False, True], ids=["standard", "post-quantum"])
def pq(request):
    return request.param


def _encrypt(tmp_path, ident: bytes, name="p.txt"):
    src = tmp_path / name
    src.write_bytes(PLAIN)
    dst = tmp_path / (name + ".age")
    digest = agecli.encrypt_recipients(src, dst, agecli.identity_recipients(ident))
    assert digest == hashlib.sha256(PLAIN).hexdigest()
    return src, dst


def test_generate_encrypt_decrypt_round_trip(tmp_path, pq):
    ident = agecli.generate_identity(pq)
    assert b"AGE-SECRET-KEY-" in ident
    rec = agecli.identity_recipients(ident)
    assert len(rec) == 1 and rec[0].startswith("age1pq1" if pq else "age1") and (("age1pq1" in rec[0]) == pq)
    _, enc = _encrypt(tmp_path, ident)
    out = tmp_path / "out.txt"
    agecli.decrypt_identity(enc, out, ident)
    assert out.read_bytes() == PLAIN
    assert agecli.decrypt_hash_identity(enc, ident) == hashlib.sha256(PLAIN).hexdigest()
    got = []
    assert agecli.decrypt_consume_identity(enc, ident, lambda r: got.append(r.read())) == hashlib.sha256(PLAIN).hexdigest()
    assert got[0] == PLAIN


def test_wrong_key_is_refused_and_leaves_no_output(tmp_path, pq):
    _, enc = _encrypt(tmp_path, agecli.generate_identity(pq))
    other = agecli.generate_identity(pq)
    with pytest.raises(agecli.AgeError):
        agecli.decrypt_hash_identity(enc, other)
    out = tmp_path / "o.txt"
    with pytest.raises(agecli.AgeError):
        agecli.decrypt_identity(enc, out, other)
    assert out.read_bytes() == b""          # created exclusively, left for the caller to remove


def test_protect_unlock_and_wrong_password(tmp_path, pq):
    ident = agecli.generate_identity(pq)
    protected = agecli.protect_identity(ident, "correct horse")
    assert b"AGE-SECRET-KEY" not in protected
    f = tmp_path / "k.agekey"
    agecli.write_new_file(f, protected)
    assert agecli.classify_key_file(f) == "protected"
    assert agecli.load_identity(f, "correct horse") == ident
    with pytest.raises(agecli.AgeError) as e:
        agecli.load_identity(f, "wrong")
    assert e.value.code == "key_password_wrong"
    with pytest.raises(agecli.AgeError) as e:
        agecli.load_identity(f)             # no password: must not reach a terminal prompt
    assert e.value.code == "key_password_needed"


def test_plain_key_file_loads_without_password(tmp_path, pq):
    ident = agecli.generate_identity(pq)
    f = tmp_path / "k.txt"
    agecli.write_new_file(f, ident)
    assert agecli.classify_key_file(f) == "plain" and agecli.load_identity(f) == ident


def test_classify_other_and_missing(tmp_path):
    t = tmp_path / "t.txt"; t.write_text("hello")
    assert agecli.classify_key_file(t) == "other" and agecli.classify_key_file(tmp_path / "none") == "unreadable"
    big = tmp_path / "big.bin"; big.write_bytes(b"x" * ((1 << 20) + 5))
    assert agecli.classify_key_file(big) == "other"
    with pytest.raises(agecli.AgeError) as e:
        agecli.load_identity(t)
    assert e.value.code == "key_unreadable"


def test_ordinary_age_file_is_not_a_key(tmp_path):
    src = tmp_path / "a.txt"; src.write_bytes(b"x")
    enc = tmp_path / "a.age"
    agecli.encrypt_passphrase(src, enc, "pw")
    assert agecli.classify_key_file(enc) == "protected"      # shape only: it still fails to unlock to a key
    with pytest.raises(agecli.AgeError) as e:
        agecli.load_identity(enc, "pw")
    assert e.value.code == "key_unreadable"


def test_write_new_file_never_replaces(tmp_path):
    f = tmp_path / "k"
    f.write_bytes(b"old")
    with pytest.raises(agecli.OutputExists):
        agecli.write_new_file(f, b"new")
    assert f.read_bytes() == b"old"


def test_write_new_file_removes_a_failed_write(tmp_path, monkeypatch):
    import os
    f = tmp_path / "k"
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        agecli.write_new_file(f, b"secret")
    assert not f.exists()


def test_inspect_reports_post_quantum(tmp_path, pq):
    _, enc = _encrypt(tmp_path, agecli.generate_identity(pq))
    info = agecli.inspect(enc)
    assert info["postquantum"] == ("yes" if pq else "no")


def test_secret_key_is_never_written_to_disk(tmp_path, pq, monkeypatch):
    """Generating, protecting and decrypting with a key leaves no file behind except the ones asked for."""
    monkeypatch.setenv("TMP", str(tmp_path)); monkeypatch.setenv("TEMP", str(tmp_path))
    ident = agecli.generate_identity(pq)
    agecli.protect_identity(ident, "pw")
    _, enc = _encrypt(tmp_path, ident)
    agecli.decrypt_hash_identity(enc, ident)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["p.txt", "p.txt.age"]


def test_key_input_must_be_a_path(tmp_path):
    ident = agecli.generate_identity(False)
    with open(tmp_path / "x", "wb+") as f:
        with pytest.raises(agecli.AgeError):
            agecli._run(["-d"], f, None, hash_output=True, identity=ident)


def test_protected_key_bytes_are_refused_before_age_can_wait_for_a_prompt(tmp_path, pq):
    ident = agecli.generate_identity(pq)
    _, enc = _encrypt(tmp_path, ident)
    for protected in (agecli.protect_identity(ident, "pw"), b"\n  " + agecli.protect_identity(ident, "pw"),
                      b"-----BEGIN AGE ENCRYPTED FILE-----\nAAAA\n-----END AGE ENCRYPTED FILE-----\n", b"no key here"):
        with pytest.raises(agecli.AgeError) as e:
            agecli.decrypt_hash_identity(enc, protected)
        assert e.value.code == "key_unreadable"


def test_key_formats_age_accepts_are_accepted(tmp_path, pq):
    ident = agecli.generate_identity(pq)
    _, enc = _encrypt(tmp_path, ident)
    assert agecli.decrypt_hash_identity(enc, ident.replace(b"\n", b"\r\n"))
    two = ident + agecli.generate_identity(pq)
    assert agecli.decrypt_hash_identity(enc, two)


def test_unlock_error_codes(tmp_path):
    ident = agecli.generate_identity(False)
    prot = agecli.protect_identity(ident, "pw")
    cases = {"flipped": prot[:40] + bytes([prot[40] ^ 1]) + prot[41:], "cut": prot[: len(prot) // 2]}
    codes = set()
    for name, bad in cases.items():
        with pytest.raises(agecli.AgeError) as e:
            agecli.unlock_identity(bad, "pw")
        codes.add(e.value.code)
    assert codes <= {"key_password_wrong", "key_damaged", "key_unreadable"} and "age_failed" not in codes
    with pytest.raises(agecli.AgeError) as e:
        agecli.unlock_identity(prot, "")
    assert e.value.code == "key_password_needed"
    with pytest.raises(agecli.AgeError) as e:
        agecli.protect_identity(ident, "")
    assert e.value.code == "key_password_needed"


def test_create_key_file_round_trips_and_shares_the_public_key(tmp_path, pq):
    f = tmp_path / "k.agekey"
    rec = agecli.create_key_file(f, pq, "pw")
    assert agecli.classify_key_file(f) == "protected" and agecli.identity_recipients(agecli.load_identity(f, "pw")) == rec
    g = tmp_path / "plain.agekey"
    assert agecli.classify_key_file(g) == "unreadable"
    agecli.create_key_file(g, pq, None)
    assert agecli.classify_key_file(g) == "plain"
    with pytest.raises(agecli.OutputExists):
        agecli.create_key_file(g, pq, None)


def test_create_key_file_removes_a_file_that_does_not_read_back(tmp_path, monkeypatch):
    f = tmp_path / "k.agekey"
    monkeypatch.setattr(agecli, "load_identity", lambda p, pw=None: b"AGE-SECRET-KEY-1WRONG")
    with pytest.raises(agecli.AgeError):
        agecli.create_key_file(f, False, "pw")
    assert not f.exists()


def test_junk_line_in_a_key_fails_at_load_time(tmp_path):
    ident = agecli.generate_identity(False)
    f = tmp_path / "k.txt"
    agecli.write_new_file(f, ident + b"garbage line\n")
    with pytest.raises(agecli.AgeError) as e:
        agecli.load_identity(f)
    assert e.value.code == "key_unreadable"


def test_key_path_with_leading_dash_is_a_file_not_a_flag(tmp_path, monkeypatch, pq):
    ident = agecli.generate_identity(pq)
    _, enc = _encrypt(tmp_path, ident)
    (tmp_path / "-o.age").write_bytes(enc.read_bytes())
    monkeypatch.chdir(tmp_path)
    from pathlib import Path
    assert agecli.decrypt_hash_identity(Path("-o.age"), ident) == hashlib.sha256(PLAIN).hexdigest()
    assert agecli.inspect(Path("-o.age"))


def test_bad_key_does_not_leave_an_empty_output_file(tmp_path):
    ident = agecli.generate_identity(False)
    _, enc = _encrypt(tmp_path, ident)
    out = tmp_path / "o.txt"
    with open(tmp_path / "h", "wb+") as held:
        with pytest.raises(agecli.AgeError):
            agecli._run(["-d"], held, out, identity=ident)
    assert not out.exists()


def test_cancel_stops_a_blocked_consumer_read(tmp_path):
    import threading, time
    ident = agecli.generate_identity(False)
    _, enc = _encrypt(tmp_path, ident)
    cancel = threading.Event()

    def slow(reader):
        reader.read(1)
        cancel.set()
        time.sleep(0.1)
        reader.read()
    with pytest.raises(agecli.Cancelled):
        agecli.decrypt_consume_identity(enc, ident, slow, cancel=cancel)
