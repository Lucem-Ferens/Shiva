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
    cases = {"ssh-ed25519 AAAAC3Nza": "unrecognised", "ssh-rsa AAAAB3": "unrecognised",
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


def test_ssh_private_keys_are_treated_as_secrets():
    for head in ("-----BEGIN OPENSSH PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN ENCRYPTED PRIVATE KEY-----"):
        assert R.is_secret_key(head + "\nb3BlbnNzaC1rZXk=\n") and R.classify(head) == "secret_key"
    assert not R.is_secret_key("-----BEGIN PUBLIC KEY-----")


ED = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICoDf7P0Oija95uvYLpv67z5IsJgR6Pr7JDj4tMTjBuP"
RSA2048 = ("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQChDCsCk7lwnKuRM/laegjGU7N10SOwf6T+3HQURuEx2oQIejo7TfSVc4XaACLitivhUvZIwcHy7VgSWoHNX1G412sUM5G2XyBA6mfCfHjWFyZDj9WhBNhUQmEKkR90dnf9hfuH/NXAjVvOXZuGX+MX6Uq27duoqPOma+w5wLfdSDqdua7Z1OgVVPAS2IL/QSsXzqruJqCOGhoWMG0nNNcAgrTDFEPHFVTKN9NGHPgNsUG9J9pLyVV4GNhep4LeNj1grJYX4ZKW9kcDJnsG7CDrCOV5SaE+kv5ufZ0dPgrL+w+Qez3t5KxMkCE43osbjAdytBh9MBJuGbhMLcxnQgFt")
RSA1024 = ("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAgQDXK/ochSJmDez6IUQ7bVFdH2o+9duZ3qn1HS/crjrm5GnnFWSiospyKJN99TnLtjhEZNziASG/CPYX5YUScOtNgi0ulzUOjq6tbo2fRBnQvolyBNsdBbbmhZXteEuF+/ZuhjrVGrL2gnTg0qezTxP3vP4yM6yxO+rXELGAUH5Avw==")
ECDSA = ("ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBB2YG406IaaRLpse6DlWac6cvSS104ZxaGn4TTeFfd45XHN122SINfPREfbI/T9UptH/Wj6H5nNQPpdLIY82/hw=")


def test_ssh_ed25519_and_rsa_keys_are_standard_recipients_without_their_comment():
    for key in (ED, RSA2048):
        res = R.parse_recipients(key + " alice@laptop")
        assert [(k.kind, k.text) for k in res.keys] == [(R.STANDARD, key)] and not res.problems
        assert R.is_ssh(key) and R.kind_of(key + " alice@laptop") == R.STANDARD
    assert R.shorten(ED) == "ssh-ed25519 ..." + ED[-6:]


def test_unusable_ssh_keys_are_named():
    assert R.parse_recipients(RSA1024).problems == [R.Problem(1, "ssh_rsa_small")]
    assert R.parse_recipients(ECDSA).problems == [R.Problem(1, "ssh_key")]
    assert R.parse_recipients(ED[:-8]).problems == [R.Problem(1, "unrecognised")]            # cut short
    assert R.parse_recipients(RSA2048.replace("ssh-rsa", "ssh-ed25519", 1)).problems == [R.Problem(1, "unrecognised")]   # type does not match the blob


def test_an_ssh_key_cannot_be_mixed_with_a_post_quantum_one():
    pq = R.Recipient(R.POST_QUANTUM, "age1pq1qpzry")
    assert R.mixed([pq, R.parse_recipients(ED).keys[0]])
    assert not R.mixed([R.Recipient(R.STANDARD, "age1qpzry"), R.parse_recipients(ED).keys[0]])


def test_an_ssh_key_pasted_twice_with_different_comments_is_a_duplicate():
    res = R.parse_recipients(ED + " alice@laptop\n" + ED + " bob@phone\n")
    assert [k.text for k in res.keys] == [ED] and res.problems == [R.Problem(2, "duplicate")]
    assert R.parse_recipients(ED + " x", existing=(ED,)).problems == [R.Problem(1, "duplicate")]
    assert R.parse_recipients('command="x" ' + ED).problems == [R.Problem(1, "unrecognised")]
