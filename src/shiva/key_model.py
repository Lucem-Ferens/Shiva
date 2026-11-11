"""Texts and rules for key-based encryption that need no window (Qt-free, English source strings).

The window feeds in plain values (mode, the recipient list, the key file state) and gets back the reason a button is
disabled, the result wording and the failure wording. Nothing here sees a key's text beyond its public form.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
        if c.key_file == KF_NOT_A_KEY:
            return "This isn't a key file."
        if c.key_file == KF_WRONG_PASSWORD:
            return "The key file password is wrong."
        if c.key_file == KF_LOCKED:
            return "Enter the key file password."
        return None
    if c.paste_text.strip():
        return "Press Add key to use the pasted key."
    if not c.recipients:
        return "Add at least one recipient key."
    if rcp.mixed(c.recipients):
        return MIXED_REASON
    if c.key_file_public and c.key_file in (KF_CHOSEN, KF_UNLOCKED) \
            and not set(c.key_file_public) & {r.text for r in c.recipients}:
        return NOT_RECIPIENT
    if c.key_file in (KF_NOT_A_KEY, KF_WRONG_PASSWORD):
        return "This isn't a key file." if c.key_file == KF_NOT_A_KEY else "The key file password is wrong."
    if c.key_file == KF_LOCKED:
        return "Unlock the key file, or remove it."
    return None


MIXED_REASON = "A file can't use both post-quantum and standard keys. Remove one kind."
NOT_RECIPIENT = ("Your key file is not one of the recipients. Tick \"Also encrypt to my own key\", or remove the key "
                 "file.")


def shred_after_reason(c: CredState) -> str | None:
    """Why "Shred the originals after a verified encryption" is unavailable in key mode, or None when it is offered.
    The trial decrypt needs the private key."""
    if c.mode != MODE_KEYS:
        return None
    if c.key_file == KF_UNLOCKED or c.key_file == KF_CHOSEN:
        return None
    return "Choose your key file to enable this: Shiva opens each encrypted copy with it before shredding."


def own_key_available(c: CredState) -> bool:
    """"Also encrypt to my own key" needs an unlocked (or plain) key file."""
    return c.mode == MODE_KEYS and c.key_file in (KF_CHOSEN, KF_UNLOCKED)


def key_summary(recipients: list) -> str:
    """The line under the recipient table: "2 keys, post-quantum.", or "" when the list is empty or mixed."""
    if not recipients or rcp.mixed(recipients):
        return ""
    n = len(recipients)
    return f"{n} key{'s' if n != 1 else ''}, {recipients[0].kind}."


def encrypt_head(recipient_count: int, kind: str, verified_with_key: bool) -> str:
    """Start of the result row of a file encrypted to keys."""
    n = recipient_count
    what = f"Encrypted for {n} key{'s' if n != 1 else ''}" + (" (post-quantum)" if kind == rcp.POST_QUANTUM else "")
    return what + (" and checked with your key file." if verified_with_key else ". Not opened to check it.")


def pq_detail(kind: str, confirmed) -> str | None:
    """A details line about the post-quantum claim: only for post-quantum keys, and only what age-inspect said."""
    if kind != rcp.POST_QUANTUM:
        return None
    if confirmed is True:
        return "age-inspect confirms the file uses a post-quantum key."
    if confirmed is False:
        return "age-inspect says the file does not use a post-quantum key."
    return "Not confirmed: age-inspect gave no answer."


# Failure wording by error_code. The caller appends "Original unchanged." or "The .age file is unchanged."
def key_fail_text(code: str, decrypting: bool) -> str | None:
    if code == "key_mismatch":
        if decrypting:
            return ("This key file can't open this file. The file may be for another key, may use a password, or may "
                    "be damaged.")
        return NOT_RECIPIENT
    return _FAIL.get(code)


_FAIL = {
    "key_needed": "A key file is needed to open this file. Choose one and run again.",
    "key_needed_to_verify": "Shredding needs your key file, to open the encrypted copy first. Choose it, or turn off "
                            "shredding.",
    "key_password_needed": "Enter the key file password and run again.",
    "key_password_wrong": "The key file password is wrong, or the key file is damaged.",
    "key_damaged": "The key file is damaged. Use a backup copy of it.",
    "key_unreadable": "This isn't a usable key file. Choose another one.",
    "recipient_rejected": "A key in the list was rejected. Remove it and run again.",
}

KEY_FAIL_CODES = frozenset(_FAIL) | {"key_mismatch"}
