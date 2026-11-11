"""What unlocks an encrypted file: a password, or recipient keys (encrypt) and a private key (decrypt). Qt-free.

The pipelines call one interface, so password mode keeps its exact behaviour and key mode differs only here.
`can_verify` is False when no private key was given: the trial decrypt cannot run, so nothing may be shredded.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import agecli, recipients as rcp


class Password:
    can_verify = True
    is_keys = False

    def __init__(self, password: str):
        self.password = password

    def preflight(self) -> None:
        pass

    def encrypt_file(self, src, dst, cancel=None) -> str:
        return agecli.encrypt_passphrase(src, dst, self.password, cancel=cancel)

    def encrypt_stream(self, feeder, dst, cancel=None) -> str:
        return agecli.encrypt_stream(feeder, dst, self.password, cancel)

    def verify_file(self, enc, cancel=None) -> str:
        return agecli.decrypt_hash_passphrase(enc, self.password, cancel=cancel)

    def verify_stream(self, enc, consumer, cancel=None) -> str:
        return agecli.decrypt_consume(enc, self.password, consumer, cancel)

    def decrypt_file(self, src, dst, cancel=None) -> None:
        agecli.decrypt_passphrase(src, dst, self.password, cancel=cancel)

    decrypt_stream = verify_stream


@dataclass
class Keys:
    """Recipient public keys for encrypting; `identity` (plain private-key text, memory only) for decrypting
    and for the trial decrypt."""
    recipients: list[str] = field(default_factory=list)
    identity: bytes | None = field(default=None, repr=False)
    _own: list | None = field(default=None, repr=False, compare=False)
    is_keys = True

    @property
    def can_verify(self) -> bool:
        return self.identity is not None

    @property
    def kind(self) -> str:
        """'post-quantum', 'standard', or '' when there are no recipients."""
        kinds = {rcp.kind_of(r) for r in self.recipients}
        return kinds.pop() if len(kinds) == 1 and None not in kinds else ""

    def check_recipients(self) -> None:
        if not self.recipients:
            raise agecli.AgeError("no recipient key was given", "recipient_rejected")
        kinds = {rcp.kind_of(r) for r in self.recipients}
        if None in kinds:
            raise agecli.AgeError("a recipient key is not usable", "recipient_rejected")
        if len(kinds) > 1:
            raise agecli.AgeError("post-quantum and standard keys cannot be mixed in one file", "recipient_rejected")
        if rcp.too_long(self.recipients):
            raise agecli.AgeError("too many recipient keys for one file", "recipients_too_many")

    def preflight(self) -> None:
        """Refuse before any work: unusable recipients, or a key file that opens none of the files this
        would produce (the trial decrypt would fail only after a full encryption)."""
        self.check_recipients()
        if self.identity is not None and not (set(self._own_keys()) & set(self.recipients)):
            raise agecli.AgeError("the key file is not one of the recipients", "key_mismatch")

    def _own_keys(self) -> list[str]:
        if self._own is None:
            self._own = agecli.identity_recipients(self.identity)
        return self._own

    def _opening(self, call):
        """Run a decrypt with the key; a failure of age itself means this key does not open the file
        (or the file is not a key file's): `key_mismatch`."""
        try:
            return call()
        except agecli.AgeError as e:
            if e.code == "age_failed":
                raise agecli.AgeError(str(e), "key_mismatch") from e
            raise

    def _encrypting(self, call):
        self.check_recipients()
        try:
            return call()
        except agecli.AgeError as e:
            if e.code == "age_failed" and "recipient" in str(e).lower():
                raise agecli.AgeError(str(e), "recipient_rejected") from e
            raise

    def encrypt_file(self, src, dst, cancel=None) -> str:
        return self._encrypting(lambda: agecli.encrypt_recipients(src, dst, self.recipients, cancel=cancel))

    def encrypt_stream(self, feeder, dst, cancel=None) -> str:
        return self._encrypting(lambda: agecli.encrypt_recipients_stream(feeder, dst, self.recipients, cancel=cancel))

    def _need_identity(self) -> bytes:
        if self.identity is None:
            raise agecli.AgeError("a key file is needed", "key_needed")
        return self.identity

    def verify_file(self, enc, cancel=None) -> str:
        return self._opening(lambda: agecli.decrypt_hash_identity(enc, self._need_identity(), cancel=cancel))

    def verify_stream(self, enc, consumer, cancel=None) -> str:
        return self._opening(lambda: agecli.decrypt_consume_identity(enc, self._need_identity(), consumer, cancel=cancel))

    def decrypt_file(self, src, dst, cancel=None) -> None:
        self._opening(lambda: agecli.decrypt_identity(src, dst, self._need_identity(), cancel=cancel))

    decrypt_stream = verify_stream


def as_cred(x):
    """A password string becomes Password; a credential object passes through."""
    return Password(x) if isinstance(x, str) else x
