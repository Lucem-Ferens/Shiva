# Shiva

> **Warning: this is an early version, 0.3.0. Do not use it on important files.** Expect defects, and keep a
> separate copy of anything you process. See [Honest limits](#honest-limits).

**Clean metadata, encrypt, and shred files, from one small Windows window.**

Shiva is a portable Windows tool for privacy-minded people. It has three functions that each work on their own, and an
optional pipeline that chains them: **clean, then encrypt, then verify by trial decrypt, then shred the plaintext.**

![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey)
![Status: early, 0.3.0](https://img.shields.io/badge/status-early%200.3.0-orange)

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

**Settings** (language) and **About** are at the bottom of the sidebar.

## What it does

| Page | What you get |
|---|---|
| **Clean** | Removes metadata from copies of your files. The original is never changed. |
| **Encrypt** (with an Encrypt / Decrypt switch) | Encrypts files and folders with [age](https://github.com/FiloSottile/age), with a password or to recipients' public keys (standard or post-quantum). |
| **Shred** | Overwrites and deletes files and folders, with warnings about where that cannot be trusted. |

Every optional behaviour is a checkbox and is **off by default**. Only irreversible actions (shredding, or shredding
originals after a verified encryption) ask for confirmation, in a window that names exactly what will happen.

### Clean metadata

Cleaned copies are saved as `NAME.cleaned.EXT`, or into another folder you choose. A file that cannot be cleaned is
refused or reported, never copied through silently. When there is nothing to remove, no copy is saved and the result says
so. A taken name gets a number (`photo.cleaned (1).jpg`); an existing file is never overwritten.

| Type | Formats |
|---|---|
| Images | JPEG, PNG, GIF, WEBP |
| Audio | MP3, FLAC, OGG, Opus |
| PDF | You choose the categories; optional flattening of pages to images |
| Office and OpenDocument | DOCX, XLSX, PPTX, ODT, ODS, ODP (you choose the categories) |
| Archives | ZIP (contents are cleaned too) |

Details and methods: [`docs/formats.md`](docs/formats.md).

#### How this differs from MAT2

The Office, OpenDocument and ZIP cleaners are ported from [MAT2](https://github.com/jvoisin/mat2); the other formats are
Shiva's own code. MAT2 does not run on Windows (it needs GTK and Poppler). Where the result differs (MAT2 behaviour as
read in its source, November 2026):

| Format | MAT2 | Shiva |
|---|---|---|
| JPEG | Decodes and saves the image again at the encoder's default quality and applies the Exif rotation to the pixels. Its lightweight mode only removes tags with ExifTool. | The default is the same idea: decode, apply the rotation, re-encode at quality 90 (lossy, so the pixels are not identical to the original). Unseen edge blocks, compression tables and bytes inserted between or after the coded image data are gone; data hidden in the pixels is not reliably removed. Full colour detail is kept if the original had it (4:4:4 or CMYK), otherwise colour is stored at half detail. Under "Show more options", "JPEG: reduce colour detail" always stores less, and "JPEG and WebP: keep the image data as it is (no re-encoding)" leaves the pixels untouched but also keeps those unseen parts. |
| PNG, GIF | PNG is decoded and written again; GIF is cleaned with ExifTool only. | Metadata chunks and blocks are removed and the image data is copied unchanged. |
| WEBP | Decoded and saved again. | The same by default: lossless images are rebuilt pixel for pixel, lossy images are saved again at quality 90 (not identical to the original). Bytes hidden inside the image chunk and colour hidden under fully transparent pixels are gone; data hidden in the visible pixels is not reliably removed. "keep the image data as it is" copies the image chunk unchanged, so such bytes stay. |
| PDF | Draws every page as a picture by default. | You choose what to remove and the text stays searchable; "flatten pages to images" is an option. |
| Video, TIFF, HEIC, SVG, WAV | Supported. | Not supported yet. |

As the JPEG row says, neither JPEG mode reliably destroys data hidden inside the picture itself (steganography). When you
ask to shred the originals after encryption, the confirmation window says that JPEG images are saved again with a small
loss of quality and that no copy of the original images is kept; the originals are shredded only after you confirm.

### Encrypt and decrypt

Choose how the files are protected: **Password** (age, scrypt) or **Recipient keys** (age public keys). Any file type, any
size (streamed). Folders become `NAME.tar.age` and are restored into a new folder.

**Recipient keys.** Paste public keys (they start with `age1`) or add them from a text file; each recipient opens the files
with their own key file. Both kinds age supports are offered: **post-quantum** keys (`age1pq1...`, ML-KEM-768 with X25519) and
**standard** keys. One file can use only one kind. **Create key pair...** makes a key file and its public key in the
window: the key file can be protected with a password and is checked by reading it back. Shiva does not keep keys: you
choose the key file for each run, and nothing about keys is saved.

- Optional "Also encrypt to my own key", and an optional key file of your own: with it, each encrypted copy is opened and
  verified before anything is shredded.
- Decrypting uses the password, or a key file (a password-protected key file is unlocked first).
- Optional: shred the originals (best effort), but only after a trial decrypt matches the original's hash. In key mode this
  needs your key file; without it nothing is shredded. If verification fails, everything is kept.
- Existing output files are never overwritten; a taken name gets a number (`notes (1).txt.age`).
- Optional **Generate...** next to the password: random words (from the EFF large word list) or random characters, made with
  the operating system's random source. Shiva shows how many bits of randomness it picked, does not keep the password, and
  you confirm it in the repeat field (pasting there is allowed; Settings can turn it off for a generated password). Copying
  asks Windows not to keep it in clipboard history, and Shiva removes the password from the clipboard after 30 seconds, or when it closes
  normally, if it is still there; until then other programs can read it.
- A password file is not labelled post-quantum: how well it resists guessing, including by future quantum computers,
  depends on how strong the password is. Opening a post-quantum file needs age 1.3.0 or later.

### Shred

- Single overwrite pass over files and folders, then deleted.
- Refuses dangerous targets (drive roots, the Windows folder, your profile folder itself, hard-linked files, and others).
- Warns before running when the location is an SSD, a network share or a cloud-synced folder, and when a file is
  compressed, sparse or EFS-encrypted.
- Shred never claims "secure deletion". See [Honest limits](#honest-limits).

## Design choices

- **Portable.** An unzip-and-run folder; no installer and no temp folders left behind after a crash.
- **Official age.** Shiva bundles the unmodified official `age` binaries and calls them as a subprocess. The password
  reaches age only through the child process environment, never on a command line. A private key reaches age only through
  a pipe and is never written to disk by Shiva (except the key file you ask it to create).
- **Verify before destroy.** Plaintext is shredded only after a trial decrypt matches the original.
- **Clear failure messages.** Every failure says what happened, what to do next, and whether the original is untouched.
- **Languages.** English and Traditional Chinese, chosen by the Windows language (override with `SHIVA_LANG=en` or
  `SHIVA_LANG=zh_TW`). German is planned.

## Honest limits

What Shiva protects against and what it does not: [`docs/threat-model.md`](docs/threat-model.md).

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
- **The password is visible to your own programs while age runs.** It reaches age through the child process environment,
  so another program running as the same Windows user can read it during that time, as it could read Shiva's own memory.
  The same holds for a key file password, and an unlocked private key stays in Shiva's memory while the run is going and
  until the window closes or is rebuilt; Python cannot wipe it.
- **A lost key file cannot be recovered.** Files encrypted to a key can be opened only with its key file (and its password,
  if it has one). Keep a backup copy away from the computer. Windows gives a key file no extra protection: a password on the
  key file is the protection, and a key file in a cloud-synced folder is uploaded.
- **age files do not record who they were encrypted to.** Shiva shows the recipients you gave it in the results; it cannot
  list them later from the file.

## Not implemented yet

Decrypting with an SSH key, plugin and hardware keys, and armored (text) output for encryption; MP4 / MOV / WAV / TIFF / HEIC / RAW
cleaning; and German.

## Build and contribute

Build steps, tests and the dependency lock are in [`docs/BUILDING.md`](docs/BUILDING.md). Bugs and questions go to the
[issue tracker](../../issues). Report security problems privately, as described in [`SECURITY.md`](SECURITY.md).

## License

Shiva is licensed under **GPL-3.0-or-later** (see [`LICENSE`](LICENSE)). The Office/ODF and ZIP cleaners are ported from
[MAT2](https://github.com/jvoisin/mat2) and keep their LGPL-3.0-or-later headers. Bundled third-party components and their
licences are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
