"""Trinetra: metadata cleaning. Each format module exposes pure functions on bytes (no disk, no GUI):

    clean_xxx(data: bytes, **options) -> CleanOutcome   # raises CleanError / Unsupported
    check_equivalent_xxx(original: bytes, cleaned: bytes) -> None   # raises CleanError if content differs

The caller (job runner) is responsible for the worker subprocess, timeouts, snapshots, output files and shredding.
"""
from __future__ import annotations

from dataclasses import dataclass, field


ARCHIVE_MEMBER = "archive_member"   # error code: an archive holds a member whose format cannot be cleaned


class CleanError(Exception):
    """The file is damaged, hostile, or the cleaned result failed an equivalence/metadata check. Nothing downstream may run.

    `code` is an optional machine-readable reason (a constant above). The UI branches on it, never on the message."""

    def __init__(self, *args, code: str | None = None):
        super().__init__(*args)
        self.code = code


class CleanCancelled(CleanError):
    """The user cancelled while the cleaning helper ran; nothing was kept."""


class Unsupported(CleanError):
    """Format or variant not supported (refused, never copied through)."""


@dataclass
class CleanOutcome:
    data: bytes
    removed: list[str] = field(default_factory=list)    # metadata categories that were found and removed
    kept: list[str] = field(default_factory=list)       # found but kept (user choice or needed for display)
    content_changed: list[str] = field(default_factory=list)  # removals that alter content (blocks auto-shred unless confirmed)
    warnings: list[str] = field(default_factory=list)
    kept_unclean: list[str] = field(default_factory=list)   # archive members copied unchanged because they cannot be cleaned (policy "keep")

    @property
    def lossless(self) -> bool:
        return not self.content_changed
