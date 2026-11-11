"""Texts and rules for key-based encryption that need no window (Qt-free, English source strings).

The window feeds in plain values (mode, the recipient list, the key file state) and gets back the reason a button is
disabled, the result wording and the failure wording. Nothing here sees a key's text beyond its public form.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from . import recipients as rcp

MODE_PASSWORD, MODE_KEYS = "password", "keys"

# Key file states (encrypt side: optional; decrypt side: required)
KF_NONE, KF_CHOSEN, KF_LOCKED, KF_UNLOCKED, KF_NOT_A_KEY, KF_WRONG_PASSWORD = (
    "none", "chosen", "locked", "unlocked", "not_a_key", "wrong_password")


@dataclass
class CredState:
    """What the user has set up for one run, as plain values."""
    mode: str = MODE_PASSWORD
    recipients: list = field(default_factory=list)       # list[rcp.Recipient]
    paste_text: str = ""                                  # text in the paste box that was not added yet
    key_file: str = KF_NONE
    key_file_public: list = field(default_factory=list)  # public keys (text) of the usable key file
    own_key: bool = False                                 # "Also encrypt to my own key"
    password_reason: str | None = None                    # reason from the password block (Password mode)


def cred_reason(c: CredState, decrypting: bool) -> str | None:
    """The first reason the primary button is disabled for the credentials, or None. Order as in the design."""
    if c.mode == MODE_PASSWORD:
        return c.password_reason
    if decrypting:
        if c.key_file == KF_NONE:
            return "Choose a key file."
        return _key_file_reason(c.key_file)
    if c.paste_text.strip():
        return "Press Add key to use the pasted key."
    if not c.recipients:
        return "Add at least one recipient key."
    if rcp.mixed(c.recipients):
        return MIXED_REASON
    if c.key_file_public and c.key_file in (KF_CHOSEN, KF_UNLOCKED) \
            and not set(c.key_file_public) & {r.text for r in c.recipients}:
        return NOT_RECIPIENT
    return _key_file_reason(c.key_file)


def _key_file_reason(state: str) -> str | None:
    """The reason for a key file that is chosen but not usable, with the one next action."""
    if state == KF_NOT_A_KEY:
        return "This isn't a key file. Choose another one."
    if state == KF_WRONG_PASSWORD:
        return "Enter the key file password again."
    if state == KF_LOCKED:
        return "Enter the key file password, then press Unlock."
    return None


MIXED_REASON = "A file can't use both post-quantum and standard keys. Remove one kind."
NOT_RECIPIENT = ("Your key file isn't one of the recipients. Tick \"Also encrypt to my own key\", or remove the key "
                 "file.")


def shred_after_reason(c: CredState) -> str | None:
    """Why "Shred the originals after a verified encryption" is unavailable in key mode, or None when it is offered.
    The trial decrypt needs the private key."""
    if c.mode != MODE_KEYS:
        return None
    if c.key_file == KF_UNLOCKED or c.key_file == KF_CHOSEN:
        return None
    first = "Unlock your key file first." if c.key_file in (KF_LOCKED, KF_WRONG_PASSWORD) else "Choose your key file first."
    return first + " Shredding needs it to verify each encrypted copy."


def own_key_available(c: CredState) -> bool:
    """"Also encrypt to my own key" needs an unlocked (or plain) key file."""
    return c.mode == MODE_KEYS and c.key_file in (KF_CHOSEN, KF_UNLOCKED)


def key_summary(recipients: list) -> str:
    """The line under the recipient table: "2 post-quantum keys.", or "" when the list is empty or mixed."""
    if not recipients or rcp.mixed(recipients):
        return ""
    n = len(recipients)
    return f"{n} {recipients[0].kind} key{'s' if n != 1 else ''}."


def pq_unconfirmed(kind: str, confirmed) -> bool:
    """True when the keys are post-quantum but age-inspect said the file does not use one: the row must not claim it."""
    return kind == rcp.POST_QUANTUM and confirmed is False


def encrypt_head(recipient_count: int, kind: str, verified_with_key: bool, pq_confirmed=None) -> str:
    """Start of the result row of a file encrypted to keys. "(post-quantum)" states the keys given, so it is left out
    when age-inspect said the file does not use a post-quantum key."""
    n = recipient_count
    what = f"Encrypted for {n} key{'s' if n != 1 else ''}"
    if kind == rcp.POST_QUANTUM and pq_confirmed is not False:
        what += " (post-quantum)"
    what += "."
    if pq_unconfirmed(kind, pq_confirmed):
        what += " Not confirmed as post-quantum: see details."
    return what + (" Verified with your key file." if verified_with_key else " Not verified (no key file).")


def pq_detail(kind: str, confirmed) -> str | None:
    """A details line about the post-quantum claim: only for post-quantum keys, and only what age-inspect said."""
    if kind != rcp.POST_QUANTUM:
        return None
    if confirmed is True:
        return "Post-quantum: yes (confirmed by age-inspect)."
    if confirmed is False:
        return "Post-quantum: no (age-inspect says the file does not use a post-quantum key)."
    return "Post-quantum: not confirmed (age-inspect gave no answer)."


# Failure wording by error_code. The caller appends "Original unchanged." or "The .age file is unchanged."
def key_fail_text(code: str, decrypting: bool) -> str | None:
    if code == "key_mismatch":
        if decrypting:
            return ("This key file can't open this file. It may be for another key, use a password, or be damaged. "
                    "Try another key file, or choose Password.")
        return NOT_RECIPIENT
    return _FAIL.get(code)


_FAIL = {
    "key_needed": "A key file is needed to open this file. Choose one and run again.",
    "key_needed_to_verify": "Shredding needs your key file to verify the encrypted copy first. Choose it, or turn off "
                            "shredding.",
    "key_password_needed": "Enter the key file password and run again.",
    "key_password_wrong": "The key file password is wrong, or the key file is damaged. Check the password and run "
                          "again.",
    "key_damaged": "The key file is damaged. Use a backup copy of it.",
    "key_unreadable": "This isn't a key file. Choose another one.",
    "recipient_rejected": "A key in the list was rejected. Remove it and run again.",
}

KEY_FAIL_CODES = frozenset(_FAIL) | {"key_mismatch"}


# ---- the Create key pair window
EXISTS_REASON = "A file with this name already exists. Choose another name."
LOSS_WARNING = ("Warning: if you lose this key file, or forget its password, files encrypted only to this key can't "
                "be opened. No one can recover them. Keep a backup copy somewhere safe, away from this computer.")
UNPROTECTED_WARNING = ("Warning: without a password, anyone who can read this file can open your files. Shiva can't "
                       "limit who can read it.")
SHORT_KEY_PASSWORD = "Warning: short password. Use 8 or more characters."
MIN_KEY_PASSWORD = 8


def path_problem(path: str) -> str | None:
    """Why a typed key file path is refused, or None. Only a full path is accepted: a relative one would put the key in
    whatever folder the program runs from, and a colon after the drive would hide it in an NTFS data stream."""
    p = path.strip()
    if not p:
        return "Choose where to save the key file."
    if not PureWindowsPath(p).is_absolute():
        return "Use Choose..., or type a full path starting with a drive letter."
    if ":" in p[2:]:
        return "The file name can't contain a colon."
    if not Path(os.path.abspath(p)).parent.is_dir():
        return "That folder doesn't exist."
    return None


def create_reason(path: str, protect: bool, pw: str, pw2: str, exists: bool) -> str | None:
    """The first reason "Create key pair" is disabled, or None. A short password never blocks."""
    problem = path_problem(path)
    if problem:
        return problem
    if protect:
        if not pw:
            return "Enter a password, or untick the password box."
        if not pw2:
            return "Repeat the password."
        if pw != pw2:
            return "Passwords do not match."
    if exists:
        return EXISTS_REASON
    return None
