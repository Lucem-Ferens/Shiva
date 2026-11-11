"""Recipient parsing: shape checks only; age stays the authority on validity."""
import subprocess

import pytest

from shiva import agecli, recipients as R

_KEYGEN = agecli.bin_dir() / "age-keygen.exe"
_AGE = agecli.bin_dir() / "age.exe"
needs_age = pytest.mark.skipif(not (_KEYGEN.exists() and _AGE.exists()), reason="age binaries absent")


def _pair(pq: bool, tmp_path):
    f = tmp_path / ("pq.key" if pq else "std.key")
    subprocess.run([str(_KEYGEN), *(["-pq"] if pq else []), "-o", str(f)], capture_output=True, check=True)
    pub = subprocess.run([str(_KEYGEN), "-y", str(f)], capture_output=True, check=True, text=True).stdout.strip()
    return f, pub


@needs_age
def test_real_keys_are_classified(tmp_path):
    _, std = _pair(False, tmp_path)
    _, pq = _pair(True, tmp_path)
    assert R.kind_of(std) == R.STANDARD and R.kind_of(pq) == R.POST_QUANTUM
    res = R.parse_recipients(f"# mine\n\n{std}\n  {pq}  \n")
    assert [k.kind for k in res.keys] == [R.STANDARD, R.POST_QUANTUM] and not res.problems


@needs_age
def test_secret_key_line_is_detected_and_refused(tmp_path):
    f, _ = _pair(False, tmp_path)
    secret = [ln for ln in f.read_text().splitlines() if ln.startswith("AGE-SECRET-KEY-")][0]
    assert R.is_secret_key("x\n" + secret) and R.is_secret_key(secret.lower()) and R.is_secret_key(chr(0xFEFF) + secret)
    res = R.parse_recipients(secret)
    assert not res.keys and res.problems == [R.Problem(1, "secret_key")]


def test_other_key_types_are_named_not_accepted():
    cases = {"ssh-ed25519 AAAAC3Nza": "ssh_key", "ssh-rsa AAAAB3": "ssh_key",
             "age1yubikey1qpzry": "plugin_key", "age1tag1qpzry": "plugin_key", "age1tagpq1qpzry": "plugin_key",
             "age1fido2-hmac1qpzry": "plugin_key", "sk-ssh-ed25519 AAAA": "ssh_key",
             "hello": "unrecognised", "age1": "unrecognised", "age1QQQ": "unrecognised", "age1bio": "unrecognised",
             "age1Tag1qpzry": "unrecognised", "age11qpzry": "unrecognised", "age1tag1QPZ": "unrecognised",
             "age1tag1qpzb": "unrecognised", "age1" + chr(0xE9) + "1qpzry": "unrecognised"}
    for text, code in cases.items():
        assert R.parse_recipients(text).problems == [R.Problem(1, code)], text


def test_duplicates_and_existing_keys_are_reported():
    k = "age1" + "q" * 58
    res = R.parse_recipients(f"{k}\n{k}\n")
    assert len(res.keys) == 1 and res.problems == [R.Problem(2, "duplicate")]
    res = R.parse_recipients(k, existing=(k,))
    assert not res.keys and res.problems == [R.Problem(1, "duplicate")]


def test_crlf_tabs_comments_and_bom():
    k = "age1" + "q" * 58
    res = R.parse_recipients(chr(0xFEFF) + "# c\r\n\t" + k + "\t\r\nbad\r\n")
    assert [x.text for x in res.keys] == [k] and res.problems == [R.Problem(3, "unrecognised")]


def test_one_bad_line_does_not_drop_the_others():
    k = "age1" + "q" * 58
    res = R.parse_recipients(f"nonsense\n{k}\n")
    assert [x.text for x in res.keys] == [k] and res.problems == [R.Problem(1, "unrecognised")]


def test_mixed_and_shorten():
    std, pq = R.Recipient(R.STANDARD, "a"), R.Recipient(R.POST_QUANTUM, "b")
    assert R.mixed([std, pq]) and not R.mixed([std, std]) and not R.mixed([])
    long = "age1pq1" + "q" * 1900
    assert len(R.shorten(long)) < 30 and R.shorten("age1short") == "age1short"


@needs_age
def test_age_rejects_what_the_local_check_lets_through(tmp_path):
    _, std = _pair(False, tmp_path)
    bad = std[:-3] + ("qqq" if not std.endswith("qqq") else "ppp")
    assert R.kind_of(bad) == R.STANDARD          # shape only
    src = tmp_path / "p.txt"; src.write_bytes(b"x")
    r = subprocess.run([str(_AGE), "-r", bad, "-o", str(tmp_path / "o.age"), str(src)], capture_output=True, text=True)
    assert r.returncode != 0 and "malformed recipient" in r.stderr


@needs_age
def test_age_refuses_to_mix_standard_and_post_quantum(tmp_path):
    _, std = _pair(False, tmp_path)
    _, pq = _pair(True, tmp_path)
    src = tmp_path / "p.txt"; src.write_bytes(b"x")
    r = subprocess.run([str(_AGE), "-r", std, "-r", pq, "-o", str(tmp_path / "m.age"), str(src)],
                       capture_output=True, text=True)
    assert r.returncode != 0
