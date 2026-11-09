"""Qt-free rules of the Shiva window: row facts, row notes, passphrase states, button labels, reasons, results.

Everything here is plain Python so it can be tested without a display. Nothing here parses `JobResult.message`;
result rows are built only from the structured fields."""
from __future__ import annotations

import os
import re
import stat as _stat
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import shred, storage
from .jobs import OUTPUT_EXISTS, JobResult
from .metadata import ARCHIVE_MEMBER, runner

TABS = ("clean", "encrypt", "shred")
TITLES = {"clean": ("Clean metadata", "Images, PDF, Office, audio, ZIP"), "encrypt": ("Encrypt", "Files and folders"),
          "shred": ("Shred", "Overwrite and delete")}
MIN_PW = 8      # encrypting below this only WARNS; decrypting is never length-checked
SHORT_PW_WARNING = "⚠ Short passphrase: use 8 or more characters"
_OFFLINE_ATTRS = 0x1000 | 0x40000 | 0x400000   # OFFLINE, RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS (cloud placeholders)
_COUNT_CAP = 100_000

CLEAN_LIMITS = ("Cleaning removes known metadata only. Watermarks, visible content, the file name and its dates "
                "are not changed.")
SHRED_LINE = "Shredding is best effort: traces can remain. Full-disk encryption is the reliable protection."
ABOUT_LINK = "Learn about shredding"
WHY_LINK = "Learn why shredding can fail"
TRAILER = "Full-disk encryption is the reliable protection."
SHRED_TITLE = "Shred %1?"
SHRED_BODY = ("They will be overwritten and deleted as far as the drive allows. No copy is kept and you can't "
              "undo this.")
PIPE_BODY = ("After each encrypted copy passes a trial decrypt, its original is overwritten and deleted as far as "
             "the drive allows. If a check fails, the original is kept.")
NOTE_CONTENT = ("If cleaning changes content in another way (for example JPEG rotation), that original is kept and "
                "the result says so.")
NOTE_ARCHIVE = ("Archives with parts left out lose those parts. Their originals are kept unless you confirm that "
                "change here.")
# Per-item risks: only locations worse than the NTFS baseline (SSD, removable, network, cloud, unknown drive).
# The universal journal / shadow-copy fact lives on the "About shredding" page, not on every item.
RISK_SSD = "Warning: on an SSD, so old data may stay on the drive."
RISK_REMOVABLE = "Warning: on a removable drive, so old data may stay on it."
RISK_NETWORK = "Warning: on a network drive, so the server may keep copies."
RISK_CLOUD = "Warning: in a cloud folder, so copies may stay online."
RISK_UNKNOWN = "Warning: on a drive of unknown type, so old data may stay on it."
RISK_SPECIAL = {                       # shred.special_kind: how Windows stores the file
    "compressed": "Warning: compressed by Windows, so old data may stay on the drive.",
    "sparse": "Warning: a sparse file, so old data may stay on the drive.",
    "encrypted": "Warning: encrypted by Windows (EFS), so old data may stay on the drive.",
}
RISK_SPECIAL_IN_FOLDER = {
    "compressed": "Warning: contains a file compressed by Windows, so old data may stay on the drive.",
    "sparse": "Warning: contains a sparse file, so old data may stay on the drive.",
    "encrypted": "Warning: contains a file encrypted by Windows (EFS), so old data may stay on the drive.",
}
LOST_PW = ("If you lose this passphrase, your files cannot be opened. It cannot be recovered. "
           "Store it somewhere safe.")


def path_key(p) -> str:
    return os.path.normcase(os.path.abspath(p))


def human_size(n: int | None) -> str:
    if n is None:
        return "-"
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{int(f)} B" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{n} B"


def display_path(path: Path | str, limit: int = 60) -> str:
    """The full path for a confirmation list, cut in the middle ("D:\\Data\\...\\old.txt") when longer than `limit`.
    Never empty: a drive root shows as the root itself. The untouched full path belongs in a tooltip."""
    s = str(path) or "?"
    if len(s) <= limit:
        return s
    head, tail = s[: (limit - 3) // 2], s[-((limit - 3) - (limit - 3) // 2):]
    return head + "..." + tail


def unit(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------- facts about one listed path
@dataclass
class Facts:
    path: Path
    probed: bool = False
    exists: bool = True
    is_dir: bool = False
    is_age: bool = False
    size: int | None = None
    count: int | None = None            # items in a folder
    count_capped: bool = False
    counted: bool = False
    placeholder: bool = False           # cloud "online-only" file: never opened on add (would download it)
    classified: bool = False
    fmt_status: str | None = None       # "supported" | "unsupported"
    fmt: str | None = None              # format name, or the reason when unsupported
    class_error: str | None = None
    storage_done: bool = False
    storage_warnings: list[str] = field(default_factory=list)
    drive_kind: str = ""                # "ssd" | "hdd" | "removable" | "network" | "ramdisk" | "unknown" (from storage.assess)
    cloud_provider: str | None = None
    special: str | None = None          # a file (or, for a folder, one of its files) stored compressed / sparse / EFS
    shred_refusal: str = ""             # shred.refusal_reason: "drive" | "user_folder" | "system_folder" | "program_folder" | ""


_SPECIAL_ORDER = ("encrypted", "compressed", "sparse")      # most serious first


def _worst(kinds: set) -> str | None:
    return next((k for k in _SPECIAL_ORDER if k in kinds), None)


def _count_items(root: Path) -> tuple[int, bool, str | None]:
    """(items below root, stopped at the cap, most serious compressed/sparse/EFS kind among the files seen)."""
    n, stack, kinds = 0, [str(root)], set()
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    n += 1
                    if n >= _COUNT_CAP:
                        return n, True, _worst(kinds)
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:       # attributes come with the directory listing: no extra disk access
                            k = shred.special_kind(getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0))
                            if k:
                                kinds.add(k)
                    except OSError:
                        pass
        except OSError:
            pass
    return n, False, _worst(kinds)


def risk_lines(drive_kind: str, cloud_provider: str | None, special: str | None = None,
               in_folder: bool = False) -> list[str]:
    """One complete sentence per finding, in a fixed order. HDD and RAM disks give none."""
    out = []
    if special:
        out.append((RISK_SPECIAL_IN_FOLDER if in_folder else RISK_SPECIAL)[special])
    if drive_kind == "ssd":
        out.append(RISK_SSD)
    elif drive_kind == "removable":
        out.append(RISK_REMOVABLE)
    elif drive_kind == "network":
        out.append(RISK_NETWORK)
    if cloud_provider:
        out.append(RISK_CLOUD)
    if drive_kind == "unknown":
        out.append(RISK_UNKNOWN)
    return out


def probe(base: Facts, need_class: bool, need_storage: bool, need_count: bool, cache: dict | None = None) -> Facts:
    """Runs off the GUI thread. Never raises. Folders are never listed as rows, only counted."""
    f = replace(base, storage_warnings=list(base.storage_warnings))
    p = f.path
    try:
        st = os.stat(p)
    except OSError:
        f.probed, f.exists = True, False
        return f
    f.probed, f.exists = True, True
    f.shred_refusal = shred.refusal_reason(p) or ""
    f.is_dir = _stat.S_ISDIR(st.st_mode)
    f.is_age = (not f.is_dir) and p.suffix.lower() == ".age"
    f.size = None if f.is_dir else st.st_size
    f.placeholder = bool(getattr(st, "st_file_attributes", 0) & _OFFLINE_ATTRS)
    if not f.is_dir:
        f.special = shred.special_kind(getattr(st, "st_file_attributes", 0))
    if need_count and f.is_dir and not f.counted:
        f.count, f.count_capped, f.special = _count_items(p)
        f.counted = True
    if need_class and not f.classified:
        f.classified = True
        if not (f.is_dir or f.is_age or f.placeholder):
            try:
                f.fmt_status, f.fmt = runner.classify(p)
            except Exception as e:          # locked file, vanished, hostile header: never crash the list
                f.class_error = "access denied" if isinstance(e, PermissionError) else "file error"
    if need_storage and not f.storage_done:
        key = path_key(p.parent)
        hit = cache.get(key) if cache is not None else None
        if hit is None:
            try:
                info = storage.assess(p)
            except Exception:
                info = storage.StorageInfo(p)
            hit = (info.warnings(), info.drive_kind, info.cloud_provider)
            if cache is not None:
                cache[key] = hit
        f.storage_warnings, f.drive_kind, f.cloud_provider = list(hit[0]), hit[1], hit[2]
        f.storage_done = True
    return f


# ---------------------------------------------------------------- one row: status, note, will it run
@dataclass
class RowInfo:
    status: str
    note: str
    run: bool
    action: str = ""            # "clean" | "encrypt" | "decrypt" | "shred" | ""
    can_unclean: bool = False   # Clean page with "encrypt the cleaned copies": can be encrypted only without cleaning (explicit tick)


def tab_steps(tab: str, ticked: frozenset = frozenset()) -> frozenset:
    """The page's own step plus the extra steps its ticked options add (Clean: encrypt, shred; Encrypt: shred).
    The order is fixed elsewhere: clean -> encrypt -> trial decrypt -> shred."""
    return frozenset({tab}) | frozenset(ticked)


def row_info(f: Facts, tab: str, ticked: frozenset = frozenset(), overlap: bool = False,
             mode: str = "auto") -> RowInfo:
    """mode (Encrypt page only): "encrypt" / "decrypt" from the page switch; "auto" decides by the .age extension."""
    steps = tab_steps(tab, ticked)
    extra = " Also inside a listed folder." if overlap else ""
    if not f.probed:
        return RowInfo("Checking...", "", False)
    if not f.exists:
        return RowInfo("Not found", "File or folder not found.", False)
    if "shred" in steps and f.shred_refusal:
        return RowInfo("Not supported", shred.REFUSAL_TEXT[f.shred_refusal] + extra, False)
    if tab == "encrypt":
        if mode == "decrypt" and not f.is_age:
            return RowInfo("Not supported", "Not an .age file. Switch to Encrypt to encrypt it.", False)
        if mode == "encrypt" and f.is_age:
            return RowInfo("Not supported", "Already encrypted. Switch to Decrypt to open it.", False)
        if f.is_age:
            return RowInfo("Ready", extra.strip(), True, "decrypt")
        return RowInfo("Ready", extra.strip(), True, "encrypt")
    if tab == "shred":
        return RowInfo("Ready", (" ".join(risk_lines(f.drive_kind, f.cloud_provider, f.special, f.is_dir)) + extra).strip(), True, "shred")
    if f.is_age:
        return RowInfo("Not supported", "Encrypted. Use the Decrypt switch on the Encrypt page.", False)
    if f.is_dir:
        return RowInfo("Not supported", "Folders can't be cleaned yet.", False)
    if f.placeholder:
        return RowInfo("Not supported", "Online-only cloud file. Make it available offline first.", False)
    if not f.classified:
        return RowInfo("Checking...", "", False)
    if f.class_error:
        return RowInfo("Not readable", f"Can't be read ({f.class_error}).", False)
    if f.fmt_status != "supported":
        if "encrypt" in steps:
            return RowInfo("Can't be cleaned", "Metadata stays if encrypted.", False, can_unclean=True)
        note = "Not supported yet." if (f.fmt or "").startswith("This file type") else (f.fmt or "Not supported.")
        return RowInfo("Not supported", note, False)
    return RowInfo("Ready", "", True, "clean")


def overlapping(keys: list[str], dir_keys: set[str]) -> set[str]:
    """Keys of listed items that sit inside another listed folder."""
    out = set()
    for k in keys:
        for d in dir_keys:
            if k != d and k.startswith(d.rstrip("\\/") + os.sep):
                out.add(k)
                break
    return out


# ---------------------------------------------------------------- passphrase
@dataclass
class PwState:
    ok: bool
    reason: str | None
    match_text: str         # "" until the repeat field has text
    strength_text: str      # plain length line ("Length: 12 characters"); never a strength word; "" when nothing typed
    show_confirm: bool
    warning: str = ""       # shown in red with an icon; never blocks the button


def pw_state(pw: str, pw2: str, encrypting: bool, decrypting: bool = False) -> PwState:
    """The length/strength/match rules apply ONLY when something is being encrypted.
    A decrypt-only list needs just a non-empty passphrase (old files may use short ones)."""
    if not (encrypting or decrypting):
        return PwState(True, None, "", "", False)
    if not encrypting:
        return PwState(bool(pw), None if pw else "Enter a passphrase.", "", "", False)
    stext = f"Length: {unit(len(pw), 'character')}" if pw else ""
    match = ""
    if pw2:
        match = "✓ Passphrases match" if pw == pw2 else "✗ Passphrases do not match"
    if not pw:
        reason = "Enter a passphrase."
    elif not pw2:
        reason = "Repeat the passphrase."
    elif pw != pw2:
        reason = ""     # the mismatch is said once, inline next to the repeat field (match_text); no second line
    else:
        reason = None
    warn = SHORT_PW_WARNING if 0 < len(pw) < MIN_PW else ""
    return PwState(reason is None, reason, match, stext, True, warn)


# ---------------------------------------------------------------- button label and reason
def primary_label(tab: str, ticked: frozenset, infos: list[RowInfo]) -> str:
    steps = tab_steps(tab, ticked)
    n = len(infos)
    if tab == "clean":
        if "encrypt" in steps and n:
            words = ("Clean, encrypt and shred" if "shred" in steps else "Clean and encrypt")
            return f"{words} {unit(n, 'file')}" + ("..." if "shred" in steps else "")
        return f"Clean {unit(n, 'file')}" if n else "Clean"
    if tab == "encrypt":
        e = sum(1 for i in infos if i.action == "encrypt")
        d = sum(1 for i in infos if i.action == "decrypt")
        if e and d:
            s = f"Encrypt {unit(e, 'item')} and decrypt {d}"
            return s if len(s) < 30 else f"Encrypt and decrypt {unit(e + d, 'item')}"
        if e and "shred" in steps:
            return f"Encrypt and shred {unit(e, 'item')}..."
        if e:
            return f"Encrypt {unit(e, 'item')}"
        if d:
            return f"Decrypt {unit(d, 'file')}"
        return "Encrypt"
    return f"Shred {unit(n, 'item')}..." if n else "Shred"


def disable_reason(tab: str, ticked: frozenset, n_rows: int, n_run: int, pw_reason: str | None,
                   running: bool = False, pending: bool = False) -> str | None:
    """First failing condition only, in fixed order: running, input, steps, passphrase, match."""
    if running:
        return "Wait for the current run to finish."
    if n_rows == 0:
        return "Add files to clean." if tab == "clean" else "Add files or folders."
    if pending:
        return "Checking the added items..."
    steps = tab_steps(tab, ticked)
    if n_run == 0:
        return "Add at least one file that can be cleaned." if "clean" in steps else "Add files or folders."
    return pw_reason


def out_dir_notes(info: storage.StorageInfo, plaintext: bool) -> list[str]:
    """Honest notes about WHERE results are written. Plaintext copies (cleaned files, decrypted output) can
    stay behind on SSDs, in cloud-sync folders and on network drives; encrypted output is less sensitive."""
    notes = []
    if info.cloud_provider:
        notes.append(f"The output folder is cloud-synced ({info.cloud_provider}): " + (
            "readable copies will be uploaded and old versions may stay online." if plaintext
            else "the encrypted files will be uploaded."))
    if info.drive_kind == "network":
        notes.append("The output folder is on a network drive: the server and its backups may keep copies.")
    elif plaintext and info.drive_kind == "ssd":
        notes.append("The output folder is on an SSD: readable copies you delete later cannot be reliably overwritten.")
    elif plaintext and info.drive_kind == "removable":
        notes.append("The output folder is on a removable drive: it may be lost or read by others.")
    elif info.drive_kind == "unknown":
        notes.append("Shiva could not tell what kind of drive the output folder is on.")
    return notes


def options_note(steps: frozenset) -> str:
    """The honest description of what the ticked options do (a trial decrypt exists only where encrypting).
    Shown under the file list on the Clean and Encrypt pages; steps = tab_steps(tab, ticked)."""
    parts = []
    if "encrypt" in steps and len(steps) > 1:
        parts.append("Each result is encrypted and verified by trial decrypt"
                     + (" before its original is shredded." if "shred" in steps else "."))
    if "clean" in steps:
        parts.append(CLEAN_LIMITS)
    if "shred" in steps:
        parts.append(SHRED_LINE)
    return " ".join(parts)


def shred_dialog_text(clean: bool, encrypt: bool, n: int, omit_parts: bool = False) -> tuple[str, str, str]:
    """(title, body, do-it button) for the destructive confirmation. Claims a trial decrypt only if one happens.
    Conditional one-sentence notes (NOTE_CONTENT, NOTE_ARCHIVE) are added by the caller as separate lines."""
    if not (clean or encrypt):
        return unit_title(SHRED_TITLE, n, "item"), SHRED_BODY, "Shred"
    if encrypt:
        return unit_title("Shred %1 after encrypting?", n, "original"), PIPE_BODY, "Shred originals"
    body = ("After each cleaned copy is saved and checked against the original, the original is overwritten "
            "and deleted. The cleaned copy is not encrypted.")
    return unit_title("Shred %1 after cleaning?", n, "original"), body, "Shred originals"


ABOUT_TITLE = "About shredding"
# (kind, text): "h" heading, "p" paragraph, "b" bullet. Mechanism words (wear levelling, journal, shadow copies) are
# allowed on this help page only; the main window stays in plain words.
ABOUT_SHREDDING = [
    ("h", "What Shred does"),
    ("p", "Shred overwrites the contents of an item, then deletes it. No copy is kept."),
    ("h", "Why it can fail"),
    ("p", "Overwriting changes the place the drive was told to write to. Some storage keeps older data elsewhere."),
    ("b", "SSDs and USB sticks: wear levelling (spreading writes over the chip) can leave old data in place."),
    ("b", "File system journal: Windows can keep a log of recent changes, and very small files can live in the file index."),
    ("b", "Shadow copies and backups: restore points or backups can hold earlier versions of a file."),
    ("b", "Cloud folders and network drives: the provider or server can keep copies and old versions."),
    ("h", "What helps"),
    ("p", "Turn on full-disk encryption before you store sensitive files. Recovered data is then unreadable without your passphrase."),
    ("p", "Keep sensitive files in encrypted form (use the Encrypt page) instead of leaving readable copies."),
    ("p", "Before you give away or sell a drive, overwrite the whole drive or destroy it."),
    ("h", "What Shiva checks"),
    ("p", "Shiva checks the drive type and cloud folders and shows a Warning on items that are worse than usual."),
    ("p", "It cannot see shadow copies or backups."),
    ("h", "What Shiva does not claim"),
    ("p", 'Shiva never promises that deleted data cannot be recovered. Results are labelled "best effort".'),
]


def unit_title(template: str, n: int, noun: str) -> str:
    return template.replace("%1", unit(n, noun))


def confirm_notes(clean: bool, omit_parts: bool) -> list[str]:
    """Conditional lines under the item list of a delete-the-originals confirmation."""
    out = []
    if clean:
        out.append(NOTE_CONTENT)
    if clean and omit_parts:
        out.append(NOTE_ARCHIVE)
    return out


# ---------------------------------------------------------------- results
GLYPH = {"Done": "✓", "Warning": "⚠", "Failed": "✗", "Skipped": "–", "Cancelled": "⊘",
         "Not fully shredded": "✗"}

ARCHIVE_PART_TIP = ("The archive holds a part whose format cannot be cleaned (named under Reason), so nothing was "
                    "written. Under \"Show more options\" you can leave such parts out (they are missing from the "
                    "copy) or keep them (they may leak metadata).")


@dataclass
class ResultRow:
    status: str
    text: str
    size_text: str
    details: list[str]
    technical: list[str] = field(default_factory=list)   # shown only under "Show technical details"
    tip: str = ""                                        # tooltip of the Result cell ("Learn more")

    @property
    def status_cell(self) -> str:
        return f"{GLYPH.get(self.status, '')} {self.status}".strip()


def skipped_row(path: Path, info: RowInfo, size: int | None) -> ResultRow:
    return ResultRow("Skipped", info.note or "Not supported.", human_size(size),
                     [f"File: {path}", f"Status: {info.status}", info.note])


RISK_ROW = "Old data may stay on the drive."


# Failure rows by error_code (errors.py): what happened + the next step. "Original unchanged." is appended by the caller.
FAIL_TEXT = {
    "disk_full": "Not enough free space for the output. Free some space or choose another output folder.",
    "age_failed": "The passphrase doesn't match this file, or the file is damaged. Check the passphrase and run again.",
    "age_failed_encrypt": "Shiva could not finish this item. Select the row for details.",
    "in_use": "Another program is using the file. Close that program and run again.",
    "permission": "Windows denied access to the file or the output folder. Check the permissions or choose another "
                  "output folder.",
    "path_too_long": "The output path is too long. Shorten the file name or choose an output folder with a shorter path.",
    "folder_path_too_long": "A path inside this folder is too long to restore later. Shorten the names inside the folder "
                            "and run again.",
    "restore_path_too_long": "A path inside this archive is too long for this output folder. Choose an output folder with "
                             "a shorter path.",
    "streams_unreadable": "Windows would not list this item's hidden data. Check the item's permissions and run again.",
    "hidden_streams": "This item has hidden data (NTFS data streams) that would not be encrypted, and shredding would "
                      "destroy it. Turn off shredding or remove the hidden data.",
    "volume_unsupported": "Shredding is not supported on this drive. Turn off shredding, or use a file on a "
                          "local NTFS, FAT or exFAT drive.",
    "changed_since_read": "The item changed while Shiva was working. Close any program that uses it and run again.",
    "not_contained": "Windows would not apply the safety limits to the encryption helper, so it was stopped before it "
                     "processed the file. Run again.",
    "changed_during_run": "The item changed while Shiva was working. Close any program that uses it and run again.",
    "verify_failed": "The trial decrypt did not match, so the encrypted copy was removed. Run again.",
    "changed_after_verify": "The output changed after it was verified. Delete it and run again.",
    "restore_incomplete": "The folder was decrypted but could not be renamed. The data is in the hidden folder named in "
                          "the details. Rename or move it.",
}


# Where the Shred page cannot offer the usual next step ("turn off shredding"): information only.
FAIL_TEXT_SHRED = {
    "volume_unsupported": "Shredding is not supported on this drive. Shiva shreds only files on local NTFS, FAT or "
                          "exFAT drives.",
}


def _add_cleanup_lines(rr: ResultRow, r: JobResult) -> None:
    """Temporary plaintext that could not be overwritten must show on the row, whatever else happened to the item."""
    if not (r.plaintext_left or r.plaintext_deleted or r.unrecognized):
        return
    if r.unrecognized:
        rr.text += (f" Shiva left alone an item in {Path(r.unrecognized[0]).parent} that is named like a leftover but is "
                    "not one it can recognise. Check it, and shred or delete it yourself if you do not need it.")
        rr.details += [f"Left alone: {p}" for p in r.unrecognized]
    if r.plaintext_left:
        rr.text += f" Readable temporary data may still be in {Path(r.plaintext_left[0]).parent}. Shred or delete it yourself."
        rr.details += [f"Still there: {p}" for p in r.plaintext_left]
    if r.plaintext_deleted:
        rr.text += " A temporary file with readable data was deleted without being overwritten."
        rr.details += [f"Deleted, not overwritten: {p}" for p in r.plaintext_deleted]
    if rr.status == "Done":
        rr.status = "Warning"


def result_row(r: JobResult, tab: str, ticked: frozenset = frozenset(), size_before: int | None = None,
               size_after: int | None = None, uncleanable: bool = False, risk_lines=()) -> ResultRow:
    """One result row. Storage jargon (the per-file notes of a shred) and the engine's own reason line of a successful
    item go to `technical`; the row says only what may remain. A successful shred at a risky location is a Warning."""
    rr = _result_row(r, tab, ticked, size_before, size_after, uncleanable)
    tech = []
    if tab == "shred":
        tech += [x for x in rr.details if x.startswith("Note: ")]
    if rr.status in ("Done", "Warning") and r.ok:
        tech += [x for x in rr.details if x.startswith("Reason: ")]
    rr.details = [x for x in rr.details if x not in tech]
    rr.technical = tech
    if tab == "shred" and r.ok and r.original_removed and risk_lines and rr.status == "Done":
        rr.status, rr.text = "Warning", "Shredded (best effort). " + RISK_ROW
    elif tab == "shred" and rr.status == "Done":
        rr.text = "Shredded (best effort)."
    elif tab != "shred" and r.ok and r.original_removed and risk_lines and rr.status == "Done":
        rr.status, rr.text = "Warning", rr.text + " " + RISK_ROW        # one event, one status (same rule as the Shred tab)
    _add_cleanup_lines(rr, r)     # last: it turns Done into Warning, which the risk rules above would then skip
    return rr


def _result_row(r: JobResult, tab: str, ticked: frozenset = frozenset(), size_before: int | None = None,
                size_after: int | None = None, uncleanable: bool = False) -> ResultRow:
    steps = tab_steps(tab, ticked)
    shred_req = "shred" in steps
    name = r.path.name
    created = r.created.name if r.created else None
    saved = created
    if r.created is not None and r.created.parent != r.path.parent:
        saved = f"{created} in folder {r.created.parent}"      # results went to another folder: name it in full
    size_text = human_size(size_before)
    if size_after is not None and tab != "shred":
        size_text += f" -> {human_size(size_after)}"
    d = [f"File: {r.path}"]
    if created:
        d.append(f"Output: {r.created}")
    if r.removed:
        d.append("Removed: " + ", ".join(r.removed))
    if r.kept:
        d.append("Not removed: " + ", ".join(r.kept))
    d += [f"Note: {w}" for w in r.warnings]
    if r.shred_attempted:
        d.append("Original shredded (best effort): " + ("yes" if r.original_removed else "not completely"))
    d.append("Reason: " + re.sub(r"^[A-Za-z_]+(?:Error|Exception|Unsupported): ", "", r.message))
    where = r.path.parent

    if r.partially_shredded or r.error_kind == "partial_shred":
        return ResultRow("Not fully shredded", "Not fully shredded. Some items were already overwritten or "
                         f"deleted. Check what is left in {where}.", size_text, d)
    if r.cancelled or r.error_kind == "cancelled":
        if r.shred_attempted:
            return ResultRow("Not fully shredded", f"Cancelled while shredding. Check what is left in {where}.",
                             size_text, d)
        return ResultRow("Cancelled", "Cancelled. Original unchanged.", size_text, d)
    if not r.ok:
        if r.error_kind == "unsupported":
            if r.error_code == ARCHIVE_MEMBER:
                return ResultRow("Failed", "Failed. Contains a part that cannot be cleaned. Original unchanged. "
                                 "Learn more", size_text, d + [ARCHIVE_PART_TIP], tip=ARCHIVE_PART_TIP)
            if r.error_code in FAIL_TEXT:
                what = FAIL_TEXT_SHRED.get(r.error_code, FAIL_TEXT[r.error_code]) if tab == "shred" else FAIL_TEXT[r.error_code]
                tail = "Nothing was changed." if tab == "shred" else "Original unchanged."
                return ResultRow("Failed", f"Failed. {what} {tail}", size_text, d)
            return ResultRow("Failed", "Failed. Not supported. Original unchanged.", size_text, d)
        if r.error_code == OUTPUT_EXISTS and r.conflict is not None:
            # the engine never overwrites: say what is in the way and what to do (the Reason line repeats it in full)
            kind = "folder" if r.conflict.is_dir() else "file"
            untouched = "The .age file is unchanged." if r.path.suffix.lower() == ".age" else "Original unchanged."
            d[-1] = f"Note: The output {kind} {r.conflict.name} already exists in {r.conflict.parent}."
            return ResultRow("Failed", f"Failed. The output {kind} {r.conflict.name} already exists. "
                             f"Rename or move it, or choose another output folder. {untouched}", size_text, d)
        if r.error_code in FAIL_TEXT and tab != "shred":
            untouched = "The .age file is unchanged." if r.path.suffix.lower() == ".age" else "Original unchanged."
            what = FAIL_TEXT[r.error_code]
            if r.error_code == "age_failed" and r.path.suffix.lower() != ".age":
                what = FAIL_TEXT["age_failed_encrypt"]
            return ResultRow("Failed", f"Failed. {what} {untouched}", size_text, d)
        if r.shred_attempted or r.original_removed or tab == "shred":
            return ResultRow("Failed", f"Failed. The original may be partly overwritten. Check what is left in {where}.",
                             size_text, d)
        return ResultRow("Failed", f"Failed. {FAIL_TEXT['age_failed_encrypt']} "
                         + ("The .age file is unchanged." if r.path.suffix.lower() == ".age" else "Original unchanged."),
                         size_text, d)

    warn =bool(r.kept) or uncleanable
    tail = ""
    if tab == "shred":
        if r.original_removed:
            text = "Shredded (best effort). See details for limits at this location."
        else:
            text, warn = "Part of the original may remain. See details.", True
        return ResultRow("Warning" if warn else "Done", text, size_text, d)
    if tab == "encrypt" and r.path.suffix.lower() == ".age":
        text = f"Decrypted to {saved}. The .age file is kept."
    elif tab == "encrypt" and not shred_req:
        text = f"Encrypted and verified. Saved as {saved}. Original kept."
    elif tab == "clean" and "encrypt" not in steps:
        text = f"Saved as {saved}. Original kept." + (" The usual name was taken, so a number was added." if r.renamed else "")
    else:       # the pipeline: clean (optional) -> encrypt -> trial decrypt -> shred (optional)
        if "clean" in steps:
            head = "Encrypted without cleaning (its metadata stays)." if uncleanable else "Cleaned, encrypted and verified."
        else:
            head = "Encrypted and verified."
        if r.original_removed:
            tail = " Original shredded (best effort)."
        elif r.shred_attempted and r.original_untouched:
            tail, warn = " Original kept (not shredded; see details).", True
        elif r.shred_attempted:
            tail, warn = " The original may be partly overwritten. Check it before use. See details.", True
        elif shred_req:
            tail, warn = " Original kept (not shredded; see details).", True
        else:
            tail = " Original kept."
        text = f"{head} Saved as {saved}.{tail}"
    if r.kept:
        text = "Not removed: " + ", ".join(r.kept) + ". " + text
    if not shred_req and not r.kept and r.warnings:
        specific, standing = split_warnings(r.warnings)
        if specific:        # about this file (possible covered content, rotation tag removed, unparsed page ...)
            text, warn = text + " See details.", True
        elif standing:      # the format's known limits, the same for every file of that kind
            text += " Limits apply: see details."
    return ResultRow("Warning" if warn else "Done", text, size_text, d)


def split_warnings(warnings) -> tuple[list[str], list[str]]:
    """(specific to this file, standing limits). "Nothing to remove" is neither: it stays in the details only."""
    from .metadata import pdf
    limits = {pdf.IMAGES_WARNING, pdf.NOT_CLEANED_WARNING}
    specific = [w for w in warnings if w not in limits and not w.startswith("Nothing to remove")]
    return specific, [w for w in warnings if w in limits]


def summary_text(statuses: list[str]) -> str:
    order = [("Done", "done"), ("Warning", "warnings"), ("Not fully shredded", "not fully shredded"),
             ("Failed", "failed"), ("Skipped", "skipped"), ("Cancelled", "cancelled")]
    parts = [f"{statuses.count(k)} {'warning' if k == 'Warning' and statuses.count(k) == 1 else w}"
             for k, w in order if statuses.count(k)]
    return ", ".join(parts) + "." if parts else "Nothing was processed."


RISK_SUMMARY = "Some items may leave traces."


def summary_level(statuses: list[str], location_risk: bool = False) -> str:
    """Worst severity of a run: "error" | "warning" | "success". A shred where every item succeeded but some sit on a
    drive or folder that may keep copies is still a warning (the green banner would read like a guarantee)."""
    if any(s in ("Failed", "Not fully shredded") for s in statuses):
        return "error"
    if location_risk or any(s in ("Warning", "Skipped", "Cancelled") for s in statuses):
        return "warning"
    return "success"


def first_problem_index(statuses: list[str]) -> int:
    """Row to select after a run: the first one that is not Done (or 0 when everything is Done)."""
    for i, s in enumerate(statuses):
        if s != "Done":
            return i
    return 0


def finish_text(statuses: list[str], n_run: int) -> str:
    """Status line after a run. 'Nothing was changed' only if nothing actually ran."""
    cancelled = statuses.count("Cancelled")
    if cancelled and cancelled == len(statuses):
        return "Cancelled. Nothing was changed."
    if cancelled:       # say what each finished item ended as: a cancel must not hide a Failed or half-shredded row
        s = "Cancelled. " + summary_text(statuses) + " Cancelled items were not touched."
        if "Not fully shredded" in statuses:
            s += " Check what is left."
        return s + (" Select a row to see why." if "Failed" in statuses else "")
    if "Not fully shredded" in statuses:
        return "Finished with a problem: not fully shredded. Check what is left. " + summary_text(statuses)
    s = summary_text(statuses)
    return s + (" Select a row to see why." if "Failed" in statuses else "")
