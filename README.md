# Shiva

> **Warning: this is an early version (0.0.6). Do not use it on important files.** Expect defects, and keep a
> separate copy of anything you process. See [Honest limits](#honest-limits).

**Clean metadata, encrypt, and shred files, from one small Windows window.**

Shiva is a portable Windows tool for privacy-minded people. It has three functions that each work on their own, and an
optional pipeline that chains them: **clean, then encrypt, then verify by trial decrypt, then shred the plaintext.**

![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey)
![Status: early, 0.0.6](https://img.shields.io/badge/status-early%200.0.6-orange)

## Download and start

1. Download `Shiva-<version>-win64.zip` and `SHA256SUMS.txt` from the [Releases](../../releases) page.
2. Check the zip: in PowerShell run `Get-FileHash Shiva-<version>-win64.zip` and compare the hash with the line in
   `SHA256SUMS.txt`.
3. Unzip anywhere and run `Shiva.exe`. There is no installer.

Requires 64-bit Windows 10 or newer. The program is **not code-signed**, so Windows SmartScreen or an antivirus may warn
on first run; the hash check above is how you confirm the file is the one published.

## Quick start

1. Pick a page: **Clean**, **Encrypt** or **Shred**.
2. Drop files or folders into the list.
3. Tick only the options you want (all are off by default) and press the action button.
4. Read the result list. Each row says what happened and whether the original is untouched.

## What it does

| Page | What you get |
|---|---|
| **Clean** | Removes metadata from copies of your files. The original is never changed. |
| **Encrypt** (with an Encrypt / Decrypt switch) | Passphrase encryption of files and folders with [age](https://github.com/FiloSottile/age). |
| **Shred** | Overwrites and removes files and folders, with warnings about where that cannot be trusted. |

Every optional behaviour is a checkbox and is **off by default**. Only irreversible actions (shredding, or deleting
originals after a verified encryption) ask for confirmation, in a window that names exactly what will happen.

### Clean metadata

Cleaned copies are saved as `NAME.cleaned.EXT`, or into another folder you choose. A file that cannot be cleaned is
refused or reported, never copied through silently.

| Type | Formats |
|---|---|
| Images | JPEG, PNG, GIF, WEBP |
| Audio | MP3, FLAC, OGG, Opus |
| PDF | You choose the categories; optional flattening of pages to images |
| Office and OpenDocument | DOCX, XLSX, PPTX, ODT, ODS, ODP (you choose the categories) |
| Archives | ZIP (contents are cleaned too) |

Details and methods: [`docs/formats.md`](docs/formats.md).

### Encrypt and decrypt

- Passphrase mode (age, scrypt); any file type, any size (streamed).
- Folders become `NAME.tar.age` and are restored into a new folder.
- Optional: delete the originals, but only after a trial decrypt matches the original's hash. If verification fails,
  everything is kept.
- Existing output files are never overwritten.

### Shred

- Single overwrite pass over files and folders.
- Refuses dangerous targets (drive roots, the Windows folder, your profile folder itself, hard-linked files, and others).
- Warns before running when the location is an SSD, a network share or a cloud-synced folder, and when a file is
  compressed, sparse or EFS-encrypted.
- Shred never claims "secure deletion". See [Honest limits](#honest-limits).

## Design choices

- **Portable.** An unzip-and-run folder; no installer and no temp folders left behind after a crash.
- **Official age.** Shiva bundles the unmodified official `age` binaries and calls them as a subprocess. The passphrase
  reaches age only through the child process environment, never on a command line.
- **Verify before destroy.** Plaintext is shredded only after a trial decrypt matches the original.
- **Clear failure messages.** Every failure says what happened, what to do next, and whether the original is untouched.
- **Languages.** English and Traditional Chinese, chosen by the Windows language (override with `SHIVA_LANG=en` or
  `SHIVA_LANG=zh_TW`). German is planned.

## Honest limits

- **Shredding is best-effort.** On SSDs (wear levelling), NTFS (journal), volumes with shadow copies, and cloud-synced
  folders, overwritten data can survive elsewhere. Full-disk encryption is the reliable protection.
- **Cleaning removes what it knows about.** It does not remove visible content, watermarks, pixel-noise fingerprints,
  file names, sizes, or the new file's timestamps. The result says so.
- **File names stay visible** in the names of `.age` files.
- **Cleaning reads the whole file into memory.** Files larger than 256 MB are not cleaned; the list says so when you add them.
- **Files from sources you do not trust.** Cleaning parses the file with libraries such as Pillow and pikepdf, in a separate
  helper process that has a memory limit, is stopped on cancel or timeout, and cannot start other programs directly (in the packaged
  build). It is **not a sandbox**: the helper still has your Windows user's rights, so a flaw in a library that a crafted file
  triggers could read or change files you can. Keep Windows and Shiva up to date.
- **The passphrase is visible to your own programs while age runs.** It reaches age through the child process environment,
  so another program running as the same Windows user can read it during that time, as it could read Shiva's own memory.

## Not implemented yet

Key-based (recipient) encryption in the window, MP4 / MOV / WAV / TIFF / HEIC / RAW cleaning, and German.

## Build and contribute

Build steps, tests and the dependency lock are in [`docs/BUILDING.md`](docs/BUILDING.md). Bugs and questions go to the
[issue tracker](../../issues). Report security problems privately, as described in [`SECURITY.md`](SECURITY.md).

## License

Shiva is licensed under **GPL-3.0-or-later** (see [`LICENSE`](LICENSE)). The Office/ODF and ZIP cleaners are ported from
[MAT2](https://github.com/jvoisin/mat2) and keep their LGPL-3.0-or-later headers. Bundled third-party components and their
licences are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
